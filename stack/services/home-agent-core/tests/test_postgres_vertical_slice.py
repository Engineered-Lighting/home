from __future__ import annotations

import base64
import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.range import Range
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app import schema
from app.config import Settings
from app.crypto import canonical_json
from app.db import Database
from app.erasure import LineageTraversalError, governed_descendants
from app.errors import ConflictError, NotFoundError
from app.ledger import EncryptedErasureLedger, ErasureLedgerRecord
from app.models import (
    ConfirmMemoryRequest,
    DescriptorCorrectionPreviewRequest,
    DescriptorLifecycleConfirm,
    DescriptorPreviewRequest,
    ForgetConfirm,
    IngestBatch,
    IngestEnvelope,
    InitiativeClaim,
    LegacyRoleImport,
    ParentConfirmation,
    PersonCreate,
    PlaceCreate,
    PlaceLocatorInput,
    PreferenceUpdate,
    ReviewedPersonVerify,
    SourceEntityBindingCreate,
)
from app.restore import RestoreQuarantineGate, RestoreReplay, outbox_health
from app.spool import DisabledRuntimeSpool, EncryptedRuntimeSpool
from app.store import CoreStore
from app.worker import DurableWorker
from tests.binding_helpers import complete_staged_principal_binding
from tests.maintenance_helpers import seed_current_worker_maintenance


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)


async def _reset_test_authority(database: Database) -> None:
    """Give each vertical replay a clean semantic and ledger authority."""

    tables = [
        table
        for table in schema.metadata.sorted_tables
        if table.schema
        in {"ingest", "identity", "knowledge", "engagement", "privacy", "operations"}
    ]
    assert tables
    qualified = ", ".join(f'"{table.schema}"."{table.name}"' for table in tables)
    async with database.transaction() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {qualified} CASCADE"))
    await seed_current_worker_maintenance(database)


@pytest.mark.asyncio
async def test_complete_itaipava_commit_and_scoped_forgetting(tmp_path) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    knowledge_key = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    ledger_key = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        runtime_spool_path=tmp_path / "runtime.sqlite",
        runtime_spool_key=SecretStr(key),
        knowledge_encryption_key=SecretStr(knowledge_key),
        erasure_ledger_path=tmp_path / "erasure-ledger.jsonl",
        erasure_ledger_head_path=tmp_path / "erasure-ledger.head.json",
        erasure_ledger_key=SecretStr(ledger_key),
        edge_token=SecretStr("edge-token-with-at-least-32-characters"),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        bootstrap_token=SecretStr("bootstrap-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="all",
        rollout_mode="canary",
    )
    database = Database(settings.async_database_url())
    await _reset_test_authority(database)
    spool = EncryptedRuntimeSpool(tmp_path / "runtime.sqlite", b"k" * 32)
    store = CoreStore(database, spool, settings)
    return_spool = None
    ambiguous_spool = None
    try:
        edge_epoch = uuid.uuid4()
        gap_envelope = IngestEnvelope(
            edge_instance_id="edge-test",
            source_name="home_assistant",
            epoch=edge_epoch,
            sequence=1,
            event_type="coverage_gap",
            source_observed_at=datetime.now(UTC),
            edge_received_at=datetime.now(UTC),
            payload={
                "reason_code": "retention_expired",
                "sequence_start": 1,
                "sequence_end": 1,
            },
            dependency_domain="home_assistant:event",
            coverage="gap",
        )
        gap_result = await store.ingest(IngestBatch(envelopes=[gap_envelope]))
        duplicate_gap = await store.ingest(IngestBatch(envelopes=[gap_envelope]))
        conflicting_gap = await store.ingest(
            IngestBatch(
                envelopes=[
                    gap_envelope.model_copy(
                        update={
                            "payload": {
                                "reason_code": "spool_overflow",
                                "sequence_start": 1,
                                "sequence_end": 1,
                            }
                        }
                    )
                ]
            )
        )
        assert gap_result.accepted == 1
        assert gap_result.acknowledgements["edge-test:home_assistant"] == 1
        assert len(gap_result.opened_gaps) == 1
        assert duplicate_gap.duplicates == 1
        assert duplicate_gap.opened_gaps == []
        assert conflicting_gap.quarantined == 1
        assert conflicting_gap.opened_gaps == []

        malformed_raw = {
            "edge_instance_id": "edge-test",
            "source_name": "home_assistant",
            "epoch": str(edge_epoch),
            "sequence": 2,
            "event_type": "conversation_turn",
            "payload": {"text": "short private canary"},
        }
        malformed = await store.quarantine_invalid(
            malformed_raw,
            [{"loc": ("payload",), "type": "missing"}],
        )
        assert malformed.quarantined == 1
        assert malformed.acknowledgements["edge-test:home_assistant"] == 2
        async with database.transaction() as connection:
            malformed_digest = (
                await connection.execute(
                    select(schema.quarantine.c.payload_sha256).where(
                        schema.quarantine.c.sequence == 2
                    )
                )
            ).scalar_one()
        assert (
            malformed_digest
            != hashlib.sha256(canonical_json(malformed_raw)).hexdigest()
        )

        marcelo = await store.create_person(PersonCreate(display_name="Marcelo"))
        amelia = await store.create_person(PersonCreate(display_name="Amelia"))
        marcelo_sr = await store.create_person(PersonCreate(display_name="Marcelo Sr."))
        binding = await complete_staged_principal_binding(
            store,
            ha_user_id="ha-user-marcelo",
            person_id=marcelo.person_id,
        )
        principal = await store.resolve_principal("ha-user-marcelo")
        assert principal["principal_id"] == binding.principal_id
        await store.bind_source_entity(
            principal,
            SourceEntityBindingCreate(
                entity_id="device_tracker.iphone",
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )

        pre_opt_in_observed = datetime.now(UTC) - timedelta(minutes=1)
        pre_opt_in_root = uuid.uuid4()
        pre_opt_in_payload = {
            "observation_kind": "transition",
            "new_state": {
                "state": "not_home",
                "attributes": {
                    "latitude": -22.412345,
                    "longitude": -43.154321,
                    "gps_accuracy": 20,
                },
            },
        }
        records_before = spool.stats()["records"]
        pre_opt_in = await store.ingest(
            IngestBatch(
                envelopes=[
                    IngestEnvelope(
                        edge_instance_id="edge-test",
                        source_name="home_assistant",
                        epoch=edge_epoch,
                        sequence=3,
                        event_type="location_fix",
                        entity_id="device_tracker.iphone",
                        source_observed_at=pre_opt_in_observed,
                        edge_received_at=pre_opt_in_observed,
                        payload=pre_opt_in_payload,
                        root_observation_id=pre_opt_in_root,
                        evidence_family_id=pre_opt_in_root,
                        dependency_domain=(
                            "home_assistant.location:device_tracker.iphone"
                        ),
                        coverage="continuous",
                        metadata={
                            "projection_type": "device_tracker",
                            "source_platform": "home_assistant",
                        },
                    )
                ]
            )
        )
        assert pre_opt_in.acknowledgements["edge-test:home_assistant"] == 3
        assert spool.stats()["records"] == records_before
        assert spool.get("edge-test:home_assistant", str(edge_epoch), 3) is None
        assert b"-22.412345" not in spool.path.read_bytes()
        async with database.transaction() as connection:
            suppressed_header = (
                (
                    await connection.execute(
                        select(
                            schema.envelopes.c.entity_id,
                            schema.envelopes.c.dependency_domain,
                            schema.envelopes.c.ha_context,
                            schema.envelopes.c.metadata,
                        ).where(schema.envelopes.c.sequence == 3)
                    )
                )
                .mappings()
                .one()
            )
            assert suppressed_header["entity_id"] is None
            assert suppressed_header["dependency_domain"] == (
                "suppressed.precise_location"
            )
            assert suppressed_header["ha_context"] == {}
            assert suppressed_header["metadata"]["raw_payload_status"] == (
                "suppressed_location_memory_disabled"
            )

        for parent_id in (amelia.person_id, marcelo_sr.person_id):
            result = await store.confirm_parent(
                principal,
                ParentConfirmation(
                    parent_person_id=parent_id,
                    child_person_id=marcelo.person_id,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )
            assert result.state == "committed"

        with pytest.raises(ConflictError, match="require location memory"):
            await store.set_preference(
                principal,
                PreferenceUpdate(
                    key="travel_greetings",
                    enabled=True,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )

        for key_name in ("location_memory", "travel_greetings"):
            await store.set_preference(
                principal,
                PreferenceUpdate(
                    key=key_name,
                    enabled=True,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )

        directive_id = uuid.uuid4()
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.privacy_directives).values(
                    directive_id=directive_id,
                    person_id=marcelo.person_id,
                    directive="do_not_track",
                    enabled=True,
                    source_artifact_id=uuid.uuid4(),
                )
            )
        do_not_track_observed = datetime.now(UTC) - timedelta(minutes=1)
        do_not_track_root = uuid.uuid4()
        do_not_track_result = await store.ingest(
            IngestBatch(
                envelopes=[
                    IngestEnvelope(
                        edge_instance_id="edge-test",
                        source_name="home_assistant",
                        epoch=edge_epoch,
                        sequence=4,
                        event_type="location_fix",
                        entity_id="device_tracker.iphone",
                        source_observed_at=do_not_track_observed,
                        edge_received_at=do_not_track_observed,
                        payload=pre_opt_in_payload,
                        root_observation_id=do_not_track_root,
                        evidence_family_id=do_not_track_root,
                        dependency_domain=(
                            "home_assistant.location:device_tracker.iphone"
                        ),
                        coverage="continuous",
                        metadata={
                            "projection_type": "device_tracker",
                            "source_platform": "home_assistant",
                        },
                    )
                ]
            )
        )
        assert do_not_track_result.acknowledgements["edge-test:home_assistant"] == 4
        assert spool.get("edge-test:home_assistant", str(edge_epoch), 4) is None
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            blocked_status = (
                await connection.execute(
                    select(schema.envelopes.c.metadata).where(
                        schema.envelopes.c.sequence == 4
                    )
                )
            ).scalar_one()
            assert blocked_status["raw_payload_status"] == "suppressed_do_not_track"
            await connection.execute(
                update(schema.privacy_directives)
                .where(schema.privacy_directives.c.directive_id == directive_id)
                .values(enabled=False)
            )

        itaipava_id = await store.create_place(
            principal,
            PlaceCreate(
                canonical_name="Itaipava",
                place_type="locality",
                travel_greeting_eligible=True,
                locator=PlaceLocatorInput(
                    latitude=-22.4,
                    longitude=-43.14,
                    radius_m=1_000,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            ),
        )
        start = datetime.now(UTC) - timedelta(minutes=11)
        location_envelopes = []
        for sequence, observed_at, latitude in (
            (5, start, -22.4),
            (6, start + timedelta(minutes=10), -22.40003),
        ):
            root_id = uuid.uuid4()
            location_envelopes.append(
                IngestEnvelope(
                    edge_instance_id="edge-test",
                    source_name="home_assistant",
                    epoch=edge_epoch,
                    sequence=sequence,
                    event_type="location_fix",
                    entity_id="device_tracker.iphone",
                    source_observed_at=observed_at,
                    edge_received_at=observed_at,
                    payload={
                        "observation_kind": "transition",
                        "new_state": {
                            "state": "not_home",
                            "attributes": {
                                "latitude": latitude,
                                "longitude": -43.14,
                                "gps_accuracy": 20,
                            },
                        },
                    },
                    root_observation_id=root_id,
                    evidence_family_id=root_id,
                    dependency_domain="home_assistant.location:device_tracker.iphone",
                    coverage="continuous",
                    metadata={
                        "projection_type": "device_tracker",
                        "source_platform": "home_assistant",
                    },
                ),
            )
        location_result = await store.ingest(IngestBatch(envelopes=location_envelopes))
        assert location_result.accepted == 2
        assert location_result.acknowledgements["edge-test:home_assistant"] == 6
        snapshot = await store.snapshot(principal)
        assert snapshot.latest_visit is not None
        assert snapshot.latest_visit["place_id"] == itaipava_id
        assert snapshot.latest_visit["state"] == "visiting"
        assert snapshot.pending_initiatives == []
        assert snapshot.coverage_gaps == []
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.state == "pending",
                    )
                )
            ).scalar_one() == 0
        visit_id = snapshot.latest_visit["visit_id"]

        hidden_parent_directives = [uuid.uuid4(), uuid.uuid4()]
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.privacy_directives),
                [
                    {
                        "directive_id": hidden_parent_directives[0],
                        "person_id": amelia.person_id,
                        "directive": "silent",
                        "enabled": True,
                        "source_artifact_id": uuid.uuid4(),
                    },
                    {
                        "directive_id": hidden_parent_directives[1],
                        "person_id": marcelo_sr.person_id,
                        "directive": "private",
                        "enabled": True,
                        "source_artifact_id": uuid.uuid4(),
                    },
                ],
            )
        hidden_preview = await store.preview_descriptor(
            principal,
            DescriptorPreviewRequest(
                visit_id=visit_id,
                exact_text="This is my parents' mountain house.",
            ),
        )
        assert hidden_preview.resolved_parent_person_ids == []
        assert hidden_preview.resolved_parents == []
        assert hidden_preview.unresolved_role_expression is True
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                delete(schema.memory_transactions).where(
                    schema.memory_transactions.c.transaction_id
                    == hidden_preview.transaction_id
                )
            )
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.directive_id.in_(
                        hidden_parent_directives
                    )
                )
            )

        preview = await store.preview_descriptor(
            principal,
            DescriptorPreviewRequest(
                visit_id=visit_id,
                exact_text="This is my parents' mountain house.",
            ),
        )
        assert preview.state == "needs_confirmation"
        assert set(preview.resolved_parent_person_ids) == {
            amelia.person_id,
            marcelo_sr.person_id,
        }
        assert {person.display_name for person in preview.resolved_parents} == {
            "Amelia",
            "Marcelo Sr.",
        }
        assert preview.locator == {
            "coordinates": "encrypted",
            "resolution": "specific",
            "radius_m": 40,
            "source_visit_id": str(visit_id),
            "current_place_id": str(itaipava_id),
            "specific_locator_retention": "will_retain_on_confirmation",
        }
        assert "ownership" in preview.not_asserted

        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            persisted_transaction = (
                (
                    await connection.execute(
                        select(
                            schema.memory_transactions.c.preview,
                            schema.memory_transactions.c.exact_text_sha256,
                        ).where(
                            schema.memory_transactions.c.transaction_id
                            == preview.transaction_id
                        )
                    )
                )
                .mappings()
                .one()
            )
        persisted_preview = persisted_transaction["preview"]
        assert persisted_preview["exact_descriptor"] == {
            "storage": "encrypted",
            "sha256": persisted_preview["exact_descriptor"]["sha256"],
        }
        assert "mountain house" not in str(persisted_preview).lower()
        exact_quote = "This is my parents' mountain house."
        assert (
            persisted_transaction["exact_text_sha256"]
            != hashlib.sha256(exact_quote.encode()).hexdigest()
        )
        restored_public_preview = dict(persisted_preview)
        restored_public_preview["exact_descriptor"] = exact_quote
        unkeyed_public_digest = hashlib.sha256(
            canonical_json(restored_public_preview)
        ).hexdigest()
        assert preview.preview_digest != unkeyed_public_digest

        client_confirmation_nonce = uuid.uuid4()
        committed = await store.confirm_descriptor(
            principal,
            preview.transaction_id,
            ConfirmMemoryRequest(
                preview_digest=preview.preview_digest,
                confirmation_artifact_id=client_confirmation_nonce,
            ),
        )
        assert committed.state == "committed"
        assert committed.fact_id is not None
        assert committed.place_id is not None
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            server_confirmation = (
                (
                    await connection.execute(
                        select(schema.confirmation_artifacts).where(
                            schema.confirmation_artifacts.c.purpose
                            == "place_social_descriptor.confirm",
                            schema.confirmation_artifacts.c.proposal_digest
                            == preview.preview_digest,
                        )
                    )
                )
                .mappings()
                .one()
            )
            supported_by = (
                await connection.execute(
                    select(schema.fact_support.c.artifact_id)
                    .select_from(
                        schema.fact_support.join(
                            schema.fact_versions,
                            schema.fact_support.c.fact_version_id
                            == schema.fact_versions.c.fact_version_id,
                        )
                    )
                    .where(schema.fact_versions.c.fact_id == committed.fact_id)
                )
            ).scalar_one()
            durable_confirmation_digest = (
                await connection.execute(
                    select(schema.memory_transactions.c.confirmation_digest).where(
                        schema.memory_transactions.c.transaction_id
                        == preview.transaction_id
                    )
                )
            ).scalar_one()
        assert server_confirmation["artifact_id"] == supported_by
        assert server_confirmation["artifact_id"] != client_confirmation_nonce
        assert server_confirmation["consumed_at"] <= server_confirmation["expires_at"]
        assert durable_confirmation_digest == preview.preview_digest
        assert durable_confirmation_digest != unkeyed_public_digest

        # Simulate a later return after the raw 24-hour spool has rolled over:
        # the previous visit is historical, while the new spool contains only
        # the new arrival's two distinct observation roots.
        historical_start = datetime.now(UTC) - timedelta(days=2)
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                update(schema.visits)
                .where(schema.visits.c.visit_id == visit_id)
                .values(
                    state="departed",
                    observed_range=Range(
                        historical_start,
                        historical_start + timedelta(minutes=10),
                        bounds="[)",
                    ),
                )
            )

        return_spool = EncryptedRuntimeSpool(
            tmp_path / "return-runtime.sqlite", b"k" * 32
        )
        return_store = CoreStore(database, return_spool, settings)
        return_start = datetime.now(UTC) - timedelta(minutes=11)
        return_envelopes = []
        for sequence, observed_at, latitude in (
            (7, return_start, -22.4),
            (8, return_start + timedelta(minutes=10), -22.40003),
        ):
            root_id = uuid.uuid4()
            return_envelopes.append(
                IngestEnvelope(
                    edge_instance_id="edge-test",
                    source_name="home_assistant",
                    epoch=edge_epoch,
                    sequence=sequence,
                    event_type="location_fix",
                    entity_id="device_tracker.iphone",
                    source_observed_at=observed_at,
                    edge_received_at=observed_at,
                    payload={
                        "observation_kind": "transition",
                        "new_state": {
                            "state": "not_home",
                            "attributes": {
                                "latitude": latitude,
                                "longitude": -43.14,
                                "gps_accuracy": 20,
                            },
                        },
                    },
                    root_observation_id=root_id,
                    evidence_family_id=root_id,
                    dependency_domain="home_assistant.location:device_tracker.iphone",
                    coverage="continuous",
                    metadata={
                        "projection_type": "device_tracker",
                        "source_platform": "home_assistant",
                    },
                )
            )
        return_batch = IngestBatch(envelopes=return_envelopes)
        return_result = await return_store.ingest(return_batch)
        replay_result = await return_store.ingest(return_batch)
        assert return_result.accepted == 2
        assert return_result.acknowledgements["edge-test:home_assistant"] == 8
        assert replay_result.duplicates == 2

        return_snapshot = await return_store.snapshot(principal)
        assert return_snapshot.latest_visit is not None
        assert return_snapshot.latest_visit["place_id"] == committed.place_id
        return_visit_id = return_snapshot.latest_visit["visit_id"]
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.visits)
                    .where(
                        schema.visits.c.principal_id == principal["principal_id"],
                        schema.visits.c.place_id == committed.place_id,
                    )
                )
            ).scalar_one() == 2
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.state == "pending",
                    )
                )
            ).scalar_one() == 1

        explanation_directives = [uuid.uuid4(), uuid.uuid4()]
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.privacy_directives),
                [
                    {
                        "directive_id": explanation_directives[0],
                        "person_id": amelia.person_id,
                        "directive": "silent",
                        "enabled": True,
                        "source_artifact_id": uuid.uuid4(),
                    },
                    {
                        "directive_id": explanation_directives[1],
                        "person_id": marcelo_sr.person_id,
                        "directive": "private",
                        "enabled": True,
                        "source_artifact_id": uuid.uuid4(),
                    },
                ],
            )
        hidden_explanation = await return_store.explain_descriptor_relationship(
            principal, committed.place_id
        )
        assert hidden_explanation.resolved_parents == []
        assert hidden_explanation.role_resolution == "unresolved"
        assert "Amelia" not in hidden_explanation.explanation
        assert "Marcelo Sr." not in hidden_explanation.explanation
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.directive_id.in_(
                        explanation_directives
                    )
                )
            )

        explanation = await return_store.explain_descriptor_relationship(
            principal, committed.place_id
        )
        assert explanation.descriptor_state == "active_confirmed"
        assert explanation.role_resolution == "complete"
        assert {person.display_name for person in explanation.resolved_parents} == {
            "Amelia",
            "Marcelo Sr.",
        }
        assert "does not assert ownership" in explanation.explanation
        presence = await return_store.query_parent_presence(
            principal, committed.place_id
        )
        assert presence.resolution == "unknown"
        assert all(person.resolution == "unknown" for person in presence.people)
        assert "place social descriptor" in presence.not_evidence
        presence_transaction_id = uuid.uuid4()
        presence_fact_id = uuid.uuid4()
        presence_fact_version_id = uuid.uuid4()
        presence_now = datetime.now(UTC)
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.memory_transactions).values(
                    transaction_id=presence_transaction_id,
                    principal_id=principal["principal_id"],
                    kind="sensor_presence_evidence",
                    state="committed",
                    candidate={"typed": "current_presence_at_place"},
                    preview={"sensor_only": True},
                    verifier_results=[],
                    policy_version=settings.policy_version,
                    policy_digest=settings.policy_digest,
                    confirmed_at=presence_now,
                )
            )
            await connection.execute(
                insert(schema.fact_versions).values(
                    fact_version_id=presence_fact_version_id,
                    fact_id=presence_fact_id,
                    version=1,
                    subject_type="person",
                    subject_id=amelia.person_id,
                    predicate="current_presence_at_place",
                    object={"place_id": str(committed.place_id)},
                    perspective_principal_id=principal["principal_id"],
                    valid_range=Range(
                        presence_now - timedelta(minutes=5),
                        presence_now + timedelta(minutes=5),
                        bounds="[)",
                    ),
                    system_range=Range(presence_now, None, bounds="[)"),
                    authority="sensor",
                    support="multiple_independent_roots",
                    contradiction="none",
                    freshness="fresh",
                    coverage="sufficient",
                    resolution="accepted",
                    privacy_scope="private",
                    memory_transaction_id=presence_transaction_id,
                )
            )
            await connection.execute(
                insert(schema.fact_support),
                [
                    {
                        "support_id": uuid.uuid4(),
                        "fact_version_id": presence_fact_version_id,
                        "artifact_id": uuid.uuid4(),
                        "root_observation_id": uuid.uuid4(),
                        "dependency_domain": "frigate.camera.front",
                        "support_role": "presence_observation",
                    },
                    {
                        "support_id": uuid.uuid4(),
                        "fact_version_id": presence_fact_version_id,
                        "artifact_id": uuid.uuid4(),
                        "root_observation_id": uuid.uuid4(),
                        "dependency_domain": "presence.sensor.entry",
                        "support_role": "presence_observation",
                    },
                ],
            )
        evidenced_presence = await return_store.query_parent_presence(
            principal, committed.place_id
        )
        amelia_presence = next(
            item for item in evidenced_presence.people if item.person_id == amelia.person_id
        )
        assert amelia_presence.resolution == "present"
        assert len(amelia_presence.independent_evidence_domains) == 2
        hidden_presence_parent = uuid.uuid4()
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.privacy_directives).values(
                    directive_id=hidden_presence_parent,
                    person_id=marcelo_sr.person_id,
                    directive="private",
                    enabled=True,
                    source_artifact_id=uuid.uuid4(),
                )
            )
        partially_hidden_presence = await return_store.query_parent_presence(
            principal, committed.place_id
        )
        assert partially_hidden_presence.resolution == "unknown"
        assert partially_hidden_presence.reason_code == "role_visibility_restricted"
        assert [
            item.person_id for item in partially_hidden_presence.people
        ] == [amelia.person_id]
        assert partially_hidden_presence.people[0].resolution == "present"
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.directive_id
                    == hidden_presence_parent
                )
            )
        amelia_do_not_track = uuid.uuid4()
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.privacy_directives).values(
                    directive_id=amelia_do_not_track,
                    person_id=amelia.person_id,
                    directive="do_not_track",
                    enabled=True,
                    source_artifact_id=uuid.uuid4(),
                )
            )
        blocked_presence = await return_store.query_parent_presence(
            principal, committed.place_id
        )
        blocked_amelia = next(
            item for item in blocked_presence.people if item.person_id == amelia.person_id
        )
        assert blocked_amelia.resolution == "unknown"
        assert blocked_amelia.reason_code == "privacy_directive_blocks_presence"
        assert blocked_amelia.independent_evidence_domains == []
        first_client, second_client = await asyncio.gather(
            return_store.list_initiatives(principal),
            return_store.list_initiatives(principal),
        )
        assert len(first_client) == len(second_client) == 1
        assert first_client[0].initiative_id == second_client[0].initiative_id
        assert "message" not in first_client[0].model_dump()

        stale_end = datetime.now(UTC) - timedelta(minutes=16)
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                update(schema.visits)
                .where(schema.visits.c.visit_id == return_visit_id)
                .values(
                    observed_range=Range(
                        stale_end - timedelta(minutes=10),
                        stale_end,
                        bounds="[)",
                    ),
                    freshness="fresh",
                )
            )
        with pytest.raises(ConflictError, match="visit_stale"):
            await return_store.claim_initiative(
                principal,
                first_client[0].initiative_id,
                InitiativeClaim(
                    session_id=f"stale-tauri-{uuid.uuid4()}",
                    surface="private_tauri",
                ),
            )
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            stale_state = (
                (
                    await connection.execute(
                        select(
                            schema.initiatives.c.state,
                            schema.initiatives.c.suppression_reason,
                        ).where(
                            schema.initiatives.c.initiative_id
                            == first_client[0].initiative_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert stale_state == {
                "state": "suppressed",
                "suppression_reason": "visit_stale",
            }
            await connection.execute(
                update(schema.visits)
                .where(schema.visits.c.visit_id == return_visit_id)
                .values(
                    observed_range=Range(
                        return_start,
                        return_start + timedelta(minutes=10),
                        bounds="[)",
                    ),
                    freshness="fresh",
                )
            )
            # Test-only restoration lets the same stable visit exercise the
            # subsequent two-client atomic claim without creating another
            # presentation key.
            await connection.execute(
                update(schema.initiatives)
                .where(
                    schema.initiatives.c.initiative_id
                    == first_client[0].initiative_id
                )
                .values(state="pending", suppression_reason=None)
            )

        async def claim_from_client(label: str):
            return await database.run_serializable(
                lambda: return_store.claim_initiative(
                    principal,
                    first_client[0].initiative_id,
                    InitiativeClaim(
                        session_id=f"{label}-{uuid.uuid4()}",
                        surface="private_tauri",
                    ),
                )
            )

        claim_results = await asyncio.gather(
            claim_from_client("tauri-a"),
            claim_from_client("tauri-b"),
            return_exceptions=True,
        )
        claims = [item for item in claim_results if not isinstance(item, Exception)]
        conflicts = [item for item in claim_results if isinstance(item, Exception)]
        assert len(claims) == len(conflicts) == 1
        assert isinstance(conflicts[0], ConflictError)
        claimed = claims[0]
        assert claimed.state == "claimed"
        assert claimed.visit_id == return_visit_id
        assert claimed.message == (
            "Looks like you\u2019re back at your parents' mountain house."
        )
        with pytest.raises(ConflictError, match="initiative_unavailable"):
            await return_store.claim_initiative(
                principal,
                first_client[0].initiative_id,
                InitiativeClaim(
                    session_id=f"tauri-{uuid.uuid4()}",
                    surface="private_tauri",
                ),
            )
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.presentation_attempts)
                    .where(
                        schema.presentation_attempts.c.initiative_id
                        == first_client[0].initiative_id
                    )
                )
            ).scalar_one() == 1

        # An overlapping second property makes specific resolution ambiguous.
        # The next stable visit must remain at the reviewed locality and must
        # not guess either child property.
        ambiguous_property_id = uuid.uuid4()
        ambiguous_locator_id = uuid.uuid4()
        ambiguous_locator = store.knowledge_cipher.seal(
            canonical_json(
                {
                    "latitude": -22.400015,
                    "longitude": -43.14,
                    "radius_m": 40,
                }
            ),
            purpose="place-locator",
            artifact_id=str(ambiguous_locator_id),
        )
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.places).values(
                    place_id=ambiguous_property_id,
                    canonical_name="Ambiguous test property",
                    place_type="property",
                    parent_place_id=itaipava_id,
                    owner_principal_id=principal["principal_id"],
                    privacy_scope="private",
                    status="active",
                )
            )
            await connection.execute(
                insert(schema.place_locators).values(
                    locator_id=ambiguous_locator_id,
                    place_id=ambiguous_property_id,
                    ciphertext=ambiguous_locator.ciphertext,
                    nonce=ambiguous_locator.nonce,
                    key_id=store.knowledge_cipher.key_id,
                    locator_sha256=ambiguous_locator.sha256,
                    radius_m=40,
                    valid_range=Range(datetime.now(UTC), None, bounds="[)"),
                )
            )
        older_return_start = datetime.now(UTC) - timedelta(days=1)
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                update(schema.visits)
                .where(schema.visits.c.visit_id == return_visit_id)
                .values(
                    state="departed",
                    observed_range=Range(
                        older_return_start,
                        older_return_start + timedelta(minutes=10),
                        bounds="[)",
                    ),
                )
            )

        ambiguous_spool = EncryptedRuntimeSpool(
            tmp_path / "ambiguous-runtime.sqlite", b"k" * 32
        )
        ambiguous_store = CoreStore(database, ambiguous_spool, settings)
        ambiguous_start = datetime.now(UTC) - timedelta(minutes=11)
        ambiguous_envelopes = []
        for sequence, observed_at, latitude in (
            (9, ambiguous_start, -22.4),
            (10, ambiguous_start + timedelta(minutes=10), -22.40003),
        ):
            root_id = uuid.uuid4()
            ambiguous_envelopes.append(
                return_envelopes[0].model_copy(
                    update={
                        "sequence": sequence,
                        "source_observed_at": observed_at,
                        "edge_received_at": observed_at,
                        "payload": {
                            "observation_kind": "transition",
                            "new_state": {
                                "state": "not_home",
                                "attributes": {
                                    "latitude": latitude,
                                    "longitude": -43.14,
                                    "gps_accuracy": 20,
                                },
                            },
                        },
                        "root_observation_id": root_id,
                        "evidence_family_id": root_id,
                    }
                )
            )
        ambiguous_result = await ambiguous_store.ingest(
            IngestBatch(envelopes=ambiguous_envelopes)
        )
        assert ambiguous_result.acknowledgements["edge-test:home_assistant"] == 10
        ambiguous_snapshot = await ambiguous_store.snapshot(principal)
        assert ambiguous_snapshot.latest_visit is not None
        assert ambiguous_snapshot.latest_visit["place_id"] == itaipava_id

        # Typed descriptor correction is a preview-bound bitemporal version,
        # not an in-place edit. Only explicitly version-linked dependents,
        # including the deterministic greeting, are invalidated.
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            original_version = (
                (
                    await connection.execute(
                        select(schema.fact_versions).where(
                            schema.fact_versions.c.fact_id == committed.fact_id,
                            schema.fact_versions.c.resolution == "accepted",
                            func.upper_inf(schema.fact_versions.c.system_range),
                        )
                    )
                )
                .mappings()
                .one()
            )
            descriptor_initiative_id = uuid.uuid4()
            await connection.execute(
                insert(schema.initiatives).values(
                    initiative_id=descriptor_initiative_id,
                    principal_id=principal["principal_id"],
                    visit_id=return_visit_id,
                    source_fact_version_id=original_version["fact_version_id"],
                    purpose="descriptor_explanation",
                    dedupe_key=f"descriptor:{committed.fact_id}:v1",
                    sensitivity="private_location",
                    allowed_surfaces=["private_tauri"],
                    template_key="descriptor_explanation_v1",
                    state="pending",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            retained_place_count = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.places)
                    .where(schema.places.c.place_id == committed.place_id)
                )
            ).scalar_one()
            retained_locator_count = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.place_locators)
                    .where(schema.place_locators.c.place_id == committed.place_id)
                )
            ).scalar_one()
            retained_parent_count = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.fact_versions)
                    .where(
                        schema.fact_versions.c.predicate == "parent_of",
                        schema.fact_versions.c.resolution == "accepted",
                        func.upper_inf(schema.fact_versions.c.system_range),
                    )
                )
            ).scalar_one()
            independent_pending_count = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.source_fact_version_id.is_(None),
                        schema.initiatives.c.state == "pending",
                    )
                )
            ).scalar_one()

        with pytest.raises(ConflictError, match="outside the typed"):
            await store.preview_descriptor_correction(
                principal,
                committed.fact_id,
                DescriptorCorrectionPreviewRequest(
                    exact_text="I own my parents' mountain house."
                ),
            )
        correction = await store.preview_descriptor_correction(
            principal,
            committed.fact_id,
            DescriptorCorrectionPreviewRequest(
                exact_text="This is my parents' mountain retreat."
            ),
        )
        assert correction.operation == "correction"
        assert correction.base_version == 1
        assert correction.new_version == 2
        assert correction.place_id == committed.place_id
        assert correction.proposed_exact_descriptor.endswith("mountain retreat.")
        assert "place and encrypted locator" in correction.retained
        correction_artifact = uuid.uuid4()
        with pytest.raises(ConflictError, match="preview digest mismatch"):
            await store.confirm_descriptor_correction(
                principal,
                correction.transaction_id,
                DescriptorLifecycleConfirm(
                    preview_digest="0" * 64,
                    confirmation_artifact_id=correction_artifact,
                ),
            )
        corrected = await store.confirm_descriptor_correction(
            principal,
            correction.transaction_id,
            DescriptorLifecycleConfirm(
                preview_digest=correction.preview_digest,
                confirmation_artifact_id=correction_artifact,
            ),
        )
        assert corrected.fact_id == committed.fact_id
        assert corrected.place_id == committed.place_id

        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            first_two_versions = (
                (
                    await connection.execute(
                        select(schema.fact_versions)
                        .where(schema.fact_versions.c.fact_id == committed.fact_id)
                        .order_by(schema.fact_versions.c.version)
                    )
                )
                .mappings()
                .all()
            )
            assert [row["version"] for row in first_two_versions] == [1, 2]
            assert first_two_versions[0]["system_range"].upper is not None
            assert first_two_versions[1]["system_range"].upper is None
            assert all(row["resolution"] == "accepted" for row in first_two_versions)
            dependent = (
                (
                    await connection.execute(
                        select(
                            schema.initiatives.c.state,
                            schema.initiatives.c.suppression_reason,
                        ).where(
                            schema.initiatives.c.initiative_id
                            == descriptor_initiative_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert dependent == {
                "state": "suppressed",
                "suppression_reason": "descriptor_corrected",
            }
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.source_fact_version_id.is_(None),
                        schema.initiatives.c.state == "pending",
                    )
                )
            ).scalar_one() == independent_pending_count
            stored_preview = (
                await connection.execute(
                    select(schema.memory_transactions.c.preview).where(
                        schema.memory_transactions.c.transaction_id
                        == correction.transaction_id
                    )
                )
            ).scalar_one()
            assert stored_preview["current_exact_descriptor"]["encrypted"] is True
            assert stored_preview["proposed_exact_descriptor"]["encrypted"] is True

        # Two confirmations based on the same active version cannot both win.
        competing = [
            await store.preview_descriptor_correction(
                principal,
                committed.fact_id,
                DescriptorCorrectionPreviewRequest(exact_text=text),
            )
            for text in (
                "This is my parents' mountain place.",
                "This is my parents' mountain home.",
            )
        ]

        async def confirm_competing(preview):
            return await database.run_serializable(
                lambda: store.confirm_descriptor_correction(
                    principal,
                    preview.transaction_id,
                    DescriptorLifecycleConfirm(
                        preview_digest=preview.preview_digest,
                        confirmation_artifact_id=uuid.uuid4(),
                    ),
                )
            )

        competing_results = await asyncio.gather(
            *(confirm_competing(preview) for preview in competing),
            return_exceptions=True,
        )
        assert (
            sum(not isinstance(result, Exception) for result in competing_results) == 1
        )
        conflicts = [
            result for result in competing_results if isinstance(result, Exception)
        ]
        assert len(conflicts) == 1
        assert isinstance(conflicts[0], ConflictError)

        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            active_corrected_version = (
                (
                    await connection.execute(
                        select(schema.fact_versions).where(
                            schema.fact_versions.c.fact_id == committed.fact_id,
                            schema.fact_versions.c.resolution == "accepted",
                            func.upper_inf(schema.fact_versions.c.system_range),
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert active_corrected_version["version"] == 3
            retraction_initiative_id = uuid.uuid4()
            await connection.execute(
                insert(schema.initiatives).values(
                    initiative_id=retraction_initiative_id,
                    principal_id=principal["principal_id"],
                    visit_id=return_visit_id,
                    source_fact_version_id=active_corrected_version["fact_version_id"],
                    purpose="descriptor_explanation",
                    dedupe_key=f"descriptor:{committed.fact_id}:v3",
                    sensitivity="private_location",
                    allowed_surfaces=["private_tauri"],
                    template_key="descriptor_explanation_v1",
                    state="pending",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )

        retraction = await store.preview_descriptor_retraction(
            principal, committed.fact_id
        )
        assert retraction.operation == "retraction"
        assert retraction.base_version == 3
        assert retraction.new_version == 4
        assert retraction.proposed_exact_descriptor is None
        retracted = await store.confirm_descriptor_retraction(
            principal,
            retraction.transaction_id,
            DescriptorLifecycleConfirm(
                preview_digest=retraction.preview_digest,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        assert retracted.state == "committed"
        with pytest.raises(ConflictError, match="transaction is committed"):
            await store.confirm_descriptor_retraction(
                principal,
                retraction.transaction_id,
                DescriptorLifecycleConfirm(
                    preview_digest=retraction.preview_digest,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )

        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            lifecycle_versions = (
                (
                    await connection.execute(
                        select(schema.fact_versions)
                        .where(schema.fact_versions.c.fact_id == committed.fact_id)
                        .order_by(schema.fact_versions.c.version)
                    )
                )
                .mappings()
                .all()
            )
            assert [row["version"] for row in lifecycle_versions] == [1, 2, 3, 4]
            assert all(
                row["object"].get("erased") is not True for row in lifecycle_versions
            )
            assert lifecycle_versions[-2]["system_range"].upper is not None
            assert lifecycle_versions[-2]["resolution"] == "accepted"
            assert lifecycle_versions[-1]["system_range"].upper is None
            assert lifecycle_versions[-1]["resolution"] == "suppressed"
            retraction_dependent = (
                (
                    await connection.execute(
                        select(
                            schema.initiatives.c.state,
                            schema.initiatives.c.suppression_reason,
                        ).where(
                            schema.initiatives.c.initiative_id
                            == retraction_initiative_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert retraction_dependent == {
                "state": "suppressed",
                "suppression_reason": "descriptor_retracted",
            }
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.places)
                    .where(schema.places.c.place_id == committed.place_id)
                )
            ).scalar_one() == retained_place_count
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.place_locators)
                    .where(schema.place_locators.c.place_id == committed.place_id)
                )
            ).scalar_one() == retained_locator_count
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.fact_versions)
                    .where(
                        schema.fact_versions.c.predicate == "parent_of",
                        schema.fact_versions.c.resolution == "accepted",
                        func.upper_inf(schema.fact_versions.c.system_range),
                    )
                )
            ).scalar_one() == retained_parent_count

        with pytest.raises(NotFoundError, match="active descriptor"):
            await store.preview_descriptor_correction(
                principal,
                committed.fact_id,
                DescriptorCorrectionPreviewRequest(
                    exact_text="This is my parents' mountain house."
                ),
            )

        lineage_artifacts = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            await connection.execute(
                insert(schema.artifact_registry),
                [
                    {
                        "artifact_id": artifact_id,
                        "artifact_kind": artifact_kind,
                        "store": "postgresql",
                        "owner_principal_id": principal["principal_id"],
                        "retention_class": "until_erased",
                    }
                    for artifact_id, artifact_kind in zip(
                        lineage_artifacts,
                        ("generated_summary", "copied_export", "retrieval_index"),
                        strict=True,
                    )
                ],
            )
            await connection.execute(
                insert(schema.artifact_links),
                [
                    {
                        "link_id": uuid.uuid4(),
                        "parent_artifact_id": committed.fact_id,
                        "child_artifact_id": lineage_artifacts[0],
                        "relation": "summarizes",
                    },
                    {
                        "link_id": uuid.uuid4(),
                        "parent_artifact_id": lineage_artifacts[0],
                        "child_artifact_id": lineage_artifacts[1],
                        "relation": "copy_of",
                    },
                    {
                        "link_id": uuid.uuid4(),
                        "parent_artifact_id": lineage_artifacts[1],
                        "child_artifact_id": lineage_artifacts[2],
                        "relation": "indexed_as",
                    },
                ],
            )
            with pytest.raises(LineageTraversalError, match="traversal limit"):
                await governed_descendants(connection, committed.fact_id, max_depth=2)

        with pytest.raises(IntegrityError, match="artifact lineage cycle"):
            async with database.transaction(
                principal_id=principal["principal_id"]
            ) as connection:
                await connection.execute(
                    insert(schema.artifact_links).values(
                        link_id=uuid.uuid4(),
                        parent_artifact_id=lineage_artifacts[2],
                        child_artifact_id=committed.fact_id,
                        relation="derived_from",
                    )
                )

        forget = await store.preview_forget_descriptor(principal, committed.fact_id)
        blocked_fact_id, already_complete = await store._block_forget_retrieval(
            principal,
            forget.erasure_request_id,
            ForgetConfirm(preview_digest=forget.preview_digest),
        )
        assert blocked_fact_id == committed.fact_id
        assert already_complete is False
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(schema.erasure_requests.c.state).where(
                        schema.erasure_requests.c.erasure_request_id
                        == forget.erasure_request_id
                    )
                )
            ).scalar_one() == "blocked"
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.retrieval_blocks)
                    .where(schema.retrieval_blocks.c.artifact_id == committed.fact_id)
                )
            ).scalar_one() == 1
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.fact_versions)
                    .where(
                        schema.fact_versions.c.fact_id == committed.fact_id,
                        schema.fact_versions.c.object != {"erased": True},
                    )
                )
            ).scalar_one() == 4
        erased = await store.confirm_forget(
            principal,
            forget.erasure_request_id,
            ForgetConfirm(preview_digest=forget.preview_digest),
        )
        assert erased.state == "ledger_pending"
        assert erased.external_pending == ["erasure_ledger_receipt"]
        assert "place and encrypted locator" in erased.preserved

        await store.set_preference(
            principal,
            PreferenceUpdate(
                key="location_memory",
                enabled=False,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )

        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            descriptors = (
                (
                    await connection.execute(
                        select(
                            schema.fact_versions.c.object,
                            schema.fact_versions.c.resolution,
                            schema.fact_versions.c.system_range,
                        )
                        .where(schema.fact_versions.c.fact_id == committed.fact_id)
                        .order_by(schema.fact_versions.c.version)
                    )
                )
                .mappings()
                .all()
            )
            assert len(descriptors) == 4
            assert all(row["object"] == {"erased": True} for row in descriptors)
            assert all(row["resolution"] == "suppressed" for row in descriptors)
            assert all(row["system_range"].upper is not None for row in descriptors)
            descriptor_transaction_ids = [
                preview.transaction_id,
                correction.transaction_id,
                *(item.transaction_id for item in competing),
                retraction.transaction_id,
            ]
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.memory_transactions)
                    .where(
                        schema.memory_transactions.c.transaction_id.in_(
                            descriptor_transaction_ids
                        ),
                        schema.memory_transactions.c.exact_text_ciphertext.is_not(None),
                    )
                )
            ).scalar_one() == 0
            transaction_rows = (
                (
                    await connection.execute(
                        select(
                            schema.memory_transactions.c.transaction_id,
                            schema.memory_transactions.c.state,
                            schema.memory_transactions.c.candidate,
                            schema.memory_transactions.c.preview,
                        ).where(
                            schema.memory_transactions.c.transaction_id.in_(
                                descriptor_transaction_ids
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
            assert all(row["candidate"] == {"erased": True} for row in transaction_rows)
            assert all(row["preview"] == {"erased": True} for row in transaction_rows)
            assert sum(row["state"] == "cancelled" for row in transaction_rows) == 1
            assert (
                (
                    await connection.execute(
                        select(schema.artifact_registry.c.status)
                        .where(
                            schema.artifact_registry.c.artifact_id.in_(
                                lineage_artifacts
                            )
                        )
                        .order_by(schema.artifact_registry.c.artifact_id)
                    )
                )
                .scalars()
                .all()
            ) == ["erased", "erased", "erased"]
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.retrieval_blocks)
                    .where(
                        schema.retrieval_blocks.c.artifact_id.in_(
                            [committed.fact_id, *lineage_artifacts]
                        )
                    )
                )
            ).scalar_one() == 4
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.initiatives)
                    .where(
                        schema.initiatives.c.source_fact_version_id.is_not(None),
                        schema.initiatives.c.state == "suppressed",
                        schema.initiatives.c.suppression_reason == "descriptor_erased",
                    )
                )
            ).scalar_one() == 3
            assert (
                await connection.execute(
                    select(schema.preferences.c.enabled).where(
                        schema.preferences.c.principal_id == principal["principal_id"],
                        schema.preferences.c.key == "travel_greetings",
                    )
                )
            ).scalar_one() is False
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.state == "suppressed",
                        schema.initiatives.c.suppression_reason == "principal_opt_out",
                    )
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.coverage_intervals)
                    .where(
                        schema.coverage_intervals.c.reason_code
                        == "retention_expired"
                    )
                )
            ).scalar_one() == 1
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.place_locators)
                    .where(schema.place_locators.c.place_id == committed.place_id)
                )
            ).scalar_one() == 1
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.visits)
                    .where(schema.visits.c.visit_id == visit_id)
                )
            ).scalar_one() == 1

        ledger = EncryptedErasureLedger(
            settings.erasure_ledger_path,
            settings.decoded_erasure_ledger_key(),
            head_path=settings.erasure_ledger_head_path,
            initialize=True,
        )
        worker = DurableWorker(database, ledger, DisabledRuntimeSpool())
        for _ in range(20):
            if not await worker.run_once():
                break
        assert ledger.head().epoch == 1
        ledger_entry = ledger.entries_after(0)[0]
        assert ledger_entry.record.erasure_request_id == forget.erasure_request_id
        gate = RestoreQuarantineGate(
            database, settings.erasure_ledger_head_path, cache_seconds=0
        )
        assert (await gate.status(force=True)).current is True
        assert (
            await store.inspect_erasure(principal, forget.erasure_request_id)
        ).state == "complete"
        async with database.transaction() as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.outbox)
                    .where(schema.outbox.c.state != "complete")
                )
            ).scalar_one() == 0

        # Simulate a database backup from before this erasure while preserving
        # the independently stored current ledger/head. Replay must recreate a
        # minimized request receipt, block retrieval, and scrub transitively.
        async with database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            await connection.execute(delete(schema.erasure_replay_receipts))
            await connection.execute(delete(schema.erasure_ledger_state))
            await connection.execute(
                delete(schema.erasure_tasks).where(
                    schema.erasure_tasks.c.erasure_request_id
                    == forget.erasure_request_id
                )
            )
            await connection.execute(
                delete(schema.erasure_requests).where(
                    schema.erasure_requests.c.erasure_request_id
                    == forget.erasure_request_id
                )
            )
            await connection.execute(
                delete(schema.retrieval_blocks).where(
                    schema.retrieval_blocks.c.artifact_id.in_(
                        [committed.fact_id, *lineage_artifacts]
                    )
                )
            )
            await connection.execute(
                delete(schema.outbox).where(
                    schema.outbox.c.outbox_id == ledger_entry.record.outbox_id
                )
            )
            latest_version_id = (
                await connection.execute(
                    select(schema.fact_versions.c.fact_version_id)
                    .where(schema.fact_versions.c.fact_id == committed.fact_id)
                    .order_by(schema.fact_versions.c.version.desc())
                    .limit(1)
                )
            ).scalar_one()
            await connection.execute(
                update(schema.fact_versions)
                .where(schema.fact_versions.c.fact_version_id == latest_version_id)
                .values(
                    object={"restored_canary": True},
                    resolution="accepted",
                    system_range=Range(datetime.now(UTC), None, bounds="[)"),
                )
            )
            await connection.execute(
                update(schema.artifact_registry)
                .where(schema.artifact_registry.c.artifact_id.in_(lineage_artifacts))
                .values(status="active")
            )

        quarantined = await gate.status(force=True)
        assert quarantined.current is False
        assert quarantined.code == "erasure_replay_required"
        replay = RestoreReplay(database, ledger)
        assert await replay.replay() == 1
        assert await replay.replay() == 0
        assert (await gate.status(force=True)).current is True
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            replayed_fact = (
                await connection.execute(
                    select(schema.fact_versions.c.object).where(
                        schema.fact_versions.c.fact_version_id == latest_version_id
                    )
                )
            ).scalar_one()
            assert replayed_fact == {"erased": True}
            replayed_request = (
                (
                    await connection.execute(
                        select(schema.erasure_requests).where(
                            schema.erasure_requests.c.erasure_request_id
                            == forget.erasure_request_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert replayed_request["state"] == "complete"
            assert replayed_request["scope"]["replayed_from_ledger"] is True
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.retrieval_blocks)
                    .where(
                        schema.retrieval_blocks.c.artifact_id.in_(
                            [committed.fact_id, *lineage_artifacts]
                        )
                    )
                )
            ).scalar_one() == 4
    finally:
        if ambiguous_spool is not None:
            ambiguous_spool.close()
        if return_spool is not None:
            return_spool.close()
        spool.close()
        await database.close()


@pytest.mark.asyncio
async def test_location_quality_switch_and_coverage_gates(tmp_path) -> None:
    key = base64.urlsafe_b64encode(b"q" * 32).decode().rstrip("=")
    knowledge_key = base64.urlsafe_b64encode(b"w" * 32).decode().rstrip("=")
    ledger_key = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        runtime_spool_path=tmp_path / "quality-runtime.sqlite",
        runtime_spool_key=SecretStr(key),
        knowledge_encryption_key=SecretStr(knowledge_key),
        erasure_ledger_path=tmp_path / "quality-erasure-ledger.jsonl",
        erasure_ledger_head_path=tmp_path / "quality-erasure-ledger.head.json",
        erasure_ledger_key=SecretStr(ledger_key),
        edge_token=SecretStr("quality-edge-token-with-at-least-32-chars"),
        service_token=SecretStr("quality-service-token-at-least-32-chars"),
        bootstrap_token=SecretStr("quality-bootstrap-token-at-least-32-chars"),
        policy_digest="a" * 64,
        role="all",
        rollout_mode="canary",
    )
    database = Database(settings.async_database_url())
    await _reset_test_authority(database)
    spool = EncryptedRuntimeSpool(tmp_path / "quality-runtime.sqlite", b"q" * 32)
    store = CoreStore(database, spool, settings)

    async def bootstrap(label: str, *entities: str):
        person = await store.create_person(PersonCreate(display_name=label))
        ha_user_id = f"ha-{label.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}"
        await complete_staged_principal_binding(
            store,
            ha_user_id=ha_user_id,
            person_id=person.person_id,
        )
        principal = await store.resolve_principal(ha_user_id)
        for entity in entities:
            await store.bind_source_entity(
                principal,
                SourceEntityBindingCreate(
                    entity_id=entity,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )
        await store.set_preference(
            principal,
            PreferenceUpdate(
                key="location_memory",
                enabled=True,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        locality_id = await store.create_place(
            principal,
            PlaceCreate(
                canonical_name=f"Reviewed {label} locality",
                place_type="locality",
                locator=PlaceLocatorInput(
                    latitude=-22.4,
                    longitude=-43.14,
                    radius_m=1_000,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            ),
        )
        return principal, locality_id

    def location_envelope(
        *,
        epoch: uuid.UUID,
        sequence: int,
        entity_id: str,
        observed_at: datetime,
        accuracy: float = 20,
        latitude: float = -22.4,
        coverage: str = "continuous",
    ) -> IngestEnvelope:
        root_id = uuid.uuid4()
        return IngestEnvelope(
            edge_instance_id="edge-quality",
            source_name="home_assistant",
            epoch=epoch,
            sequence=sequence,
            event_type="location_fix",
            entity_id=entity_id,
            source_observed_at=observed_at,
            edge_received_at=observed_at,
            payload={
                "observation_kind": "transition",
                "new_state": {
                    "state": "not_home",
                    "attributes": {
                        "latitude": latitude,
                        "longitude": -43.14,
                        "gps_accuracy": accuracy,
                    },
                },
            },
            root_observation_id=root_id,
            evidence_family_id=root_id,
            dependency_domain=f"home_assistant.location:{entity_id}",
            coverage=coverage,
            metadata={
                "projection_type": "device_tracker",
                "source_platform": "home_assistant",
            },
        )

    try:
        # 51 m is valid locality evidence, but it is not a property anchor. It
        # remains reviewable as location_unresolved and confirmation still
        # cannot guess or create a property.
        coarse_entity = f"device_tracker.coarse_{uuid.uuid4().hex[:8]}"
        coarse_principal, coarse_locality = await bootstrap(
            "Coarse Quality", coarse_entity
        )
        coarse_epoch = uuid.uuid4()
        coarse_start = datetime.now(UTC) - timedelta(minutes=11)
        await store.ingest(
            IngestBatch(
                envelopes=[
                    location_envelope(
                        epoch=coarse_epoch,
                        sequence=1,
                        entity_id=coarse_entity,
                        observed_at=coarse_start,
                        accuracy=51,
                    ),
                    location_envelope(
                        epoch=coarse_epoch,
                        sequence=2,
                        entity_id=coarse_entity,
                        observed_at=coarse_start + timedelta(minutes=10),
                        accuracy=51,
                    ),
                ]
            )
        )
        async with database.transaction(
            principal_id=coarse_principal["principal_id"]
        ) as connection:
            coarse_visit = (
                (
                    await connection.execute(
                        select(schema.visits).where(
                            schema.visits.c.principal_id
                            == coarse_principal["principal_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert coarse_visit["place_id"] == coarse_locality
        assert coarse_visit["anchor_kind"] == "locality"
        preview = await store.preview_descriptor(
            coarse_principal,
            DescriptorPreviewRequest(
                visit_id=coarse_visit["visit_id"],
                exact_text="This is my parents' mountain house.",
            ),
        )
        assert preview.state == "needs_confirmation"
        assert any(
            result["rule"] == "location_anchor"
            and result["reason_code"] == "location_unresolved"
            for result in preview.verifier_results
        )
        with pytest.raises(ConflictError, match="no longer safe to resolve"):
            await store.confirm_descriptor(
                coarse_principal,
                preview.transaction_id,
                ConfirmMemoryRequest(
                    preview_digest=preview.preview_digest,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )
        async with database.transaction(
            principal_id=coarse_principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.places)
                    .where(schema.places.c.place_type == "property")
                )
            ).scalar_one() == 0

        # An explicit edge gap between otherwise compatible fixes blocks the
        # arrival, even though both raw fixes remain available in the spool.
        gap_entity = f"device_tracker.gap_{uuid.uuid4().hex[:8]}"
        gap_principal, _ = await bootstrap("Gap Evidence", gap_entity)
        gap_epoch = uuid.uuid4()
        gap_start = datetime.now(UTC) - timedelta(minutes=11)
        gap_marker = IngestEnvelope(
            edge_instance_id="edge-quality",
            source_name="home_assistant",
            epoch=gap_epoch,
            sequence=2,
            event_type="coverage_gap",
            entity_id=gap_entity,
            source_observed_at=gap_start + timedelta(minutes=5),
            edge_received_at=gap_start + timedelta(minutes=5),
            payload={
                "reason_code": "retention_expired",
                "sequence_start": 2,
                "sequence_end": 2,
            },
            dependency_domain=f"home_assistant.location:{gap_entity}",
            coverage="gap",
        )
        await store.ingest(
            IngestBatch(
                envelopes=[
                    location_envelope(
                        epoch=gap_epoch,
                        sequence=1,
                        entity_id=gap_entity,
                        observed_at=gap_start,
                    ),
                    gap_marker,
                    location_envelope(
                        epoch=gap_epoch,
                        sequence=3,
                        entity_id=gap_entity,
                        observed_at=gap_start + timedelta(minutes=10),
                    ),
                ]
            )
        )
        async with database.transaction(
            principal_id=gap_principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.visits)
                    .where(
                        schema.visits.c.principal_id == gap_principal["principal_id"]
                    )
                )
            ).scalar_one() == 0

        # Snapshot reconciliation between two fixes is also a material gap;
        # it may establish current locality but cannot prove arrival.
        snapshot_entity = f"device_tracker.snapshot_{uuid.uuid4().hex[:8]}"
        snapshot_principal, _ = await bootstrap("Snapshot Evidence", snapshot_entity)
        snapshot_epoch = uuid.uuid4()
        snapshot_start = datetime.now(UTC) - timedelta(minutes=11)
        await store.ingest(
            IngestBatch(
                envelopes=[
                    location_envelope(
                        epoch=snapshot_epoch,
                        sequence=1,
                        entity_id=snapshot_entity,
                        observed_at=snapshot_start,
                    ),
                    location_envelope(
                        epoch=snapshot_epoch,
                        sequence=2,
                        entity_id=snapshot_entity,
                        observed_at=snapshot_start + timedelta(minutes=5),
                        coverage="snapshot_only",
                    ),
                    location_envelope(
                        epoch=snapshot_epoch,
                        sequence=3,
                        entity_id=snapshot_entity,
                        observed_at=snapshot_start + timedelta(minutes=10),
                    ),
                ]
            )
        )
        async with database.transaction(
            principal_id=snapshot_principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.visits)
                    .where(
                        schema.visits.c.principal_id
                        == snapshot_principal["principal_id"]
                    )
                )
            ).scalar_one() == 0

        # Principal-wide tracker comparison makes a source switch explicit
        # instead of silently constructing a single-sensor dwell.
        first_tracker = f"device_tracker.first_{uuid.uuid4().hex[:8]}"
        second_tracker = f"device_tracker.second_{uuid.uuid4().hex[:8]}"
        switched_principal, _ = await bootstrap(
            "Switched Tracker", first_tracker, second_tracker
        )
        switched_epoch = uuid.uuid4()
        switched_start = datetime.now(UTC) - timedelta(minutes=11)
        await store.ingest(
            IngestBatch(
                envelopes=[
                    location_envelope(
                        epoch=switched_epoch,
                        sequence=1,
                        entity_id=first_tracker,
                        observed_at=switched_start,
                    ),
                    location_envelope(
                        epoch=switched_epoch,
                        sequence=2,
                        entity_id=second_tracker,
                        observed_at=switched_start + timedelta(minutes=10),
                    ),
                ]
            )
        )
        async with database.transaction(
            principal_id=switched_principal["principal_id"]
        ) as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.visits)
                    .where(
                        schema.visits.c.principal_id
                        == switched_principal["principal_id"]
                    )
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.coverage_intervals)
                    .where(
                        schema.coverage_intervals.c.reason_code
                        == "tracker_source_switch"
                    )
                )
            ).scalar_one() >= 1

        # A later teaching transaction at an already recognized property
        # reuses its owned locator. An active descriptor must use correction;
        # after retraction, re-teaching cannot create a nested property.
        property_entity = f"device_tracker.property_{uuid.uuid4().hex[:8]}"
        property_principal, _ = await bootstrap("Property Reuse", property_entity)
        property_epoch = uuid.uuid4()
        property_start = datetime.now(UTC) - timedelta(minutes=11)
        await store.ingest(
            IngestBatch(
                envelopes=[
                    location_envelope(
                        epoch=property_epoch,
                        sequence=1,
                        entity_id=property_entity,
                        observed_at=property_start,
                    ),
                    location_envelope(
                        epoch=property_epoch,
                        sequence=2,
                        entity_id=property_entity,
                        observed_at=property_start + timedelta(minutes=10),
                    ),
                ]
            )
        )
        property_snapshot = await store.snapshot(property_principal)
        property_visit_id = property_snapshot.latest_visit["visit_id"]
        first_preview = await store.preview_descriptor(
            property_principal,
            DescriptorPreviewRequest(
                visit_id=property_visit_id,
                exact_text="This is my parents' mountain house.",
            ),
        )
        newly_confirmed_parent = await store.create_person(
            PersonCreate(display_name="Later Confirmed Parent")
        )
        await store.confirm_parent(
            property_principal,
            ParentConfirmation(
                parent_person_id=newly_confirmed_parent.person_id,
                child_person_id=property_principal["person_id"],
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        with pytest.raises(ConflictError, match="parent role resolution changed"):
            await store.confirm_descriptor(
                property_principal,
                first_preview.transaction_id,
                ConfirmMemoryRequest(
                    preview_digest=first_preview.preview_digest,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            )
        first_preview = await store.preview_descriptor(
            property_principal,
            DescriptorPreviewRequest(
                visit_id=property_visit_id,
                exact_text="This is my parents' mountain house.",
            ),
        )
        first_commit = await store.confirm_descriptor(
            property_principal,
            first_preview.transaction_id,
            ConfirmMemoryRequest(
                preview_digest=first_preview.preview_digest,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        async with database.transaction(
            principal_id=property_principal["principal_id"]
        ) as connection:
            verifier_receipts = (
                await connection.execute(
                    select(schema.memory_transactions.c.verifier_results).where(
                        schema.memory_transactions.c.transaction_id
                        == first_preview.transaction_id
                    )
                )
            ).scalar_one()
        assert [item["phase"] for item in verifier_receipts[:8]] == ["preview"] * 8
        assert [item["phase"] for item in verifier_receipts[8:]] == ["commit"] * 8
        assert [int(item["order"]) for item in verifier_receipts[8:]] == list(
            range(1, 9)
        )
        with pytest.raises(ConflictError, match="use correction"):
            await store.preview_descriptor(
                property_principal,
                DescriptorPreviewRequest(
                    visit_id=property_visit_id,
                    exact_text="This is my parents' mountain house.",
                ),
            )
        retraction_preview = await store.preview_descriptor_retraction(
            property_principal, first_commit.fact_id
        )
        await store.confirm_descriptor_retraction(
            property_principal,
            retraction_preview.transaction_id,
            DescriptorLifecycleConfirm(
                preview_digest=retraction_preview.preview_digest,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        async with database.transaction(
            principal_id=property_principal["principal_id"]
        ) as connection:
            before_counts = (
                (
                    await connection.execute(
                        select(
                            func.count(func.distinct(schema.places.c.place_id)).label(
                                "places"
                            ),
                            func.count(
                                func.distinct(schema.place_locators.c.locator_id)
                            ).label("locators"),
                        )
                        .select_from(
                            schema.places.join(
                                schema.place_locators,
                                schema.places.c.place_id
                                == schema.place_locators.c.place_id,
                            )
                        )
                        .where(
                            schema.places.c.owner_principal_id
                            == property_principal["principal_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
        second_preview = await store.preview_descriptor(
            property_principal,
            DescriptorPreviewRequest(
                visit_id=property_visit_id,
                exact_text="This is my parents' mountain house.",
            ),
        )
        second_commit = await store.confirm_descriptor(
            property_principal,
            second_preview.transaction_id,
            ConfirmMemoryRequest(
                preview_digest=second_preview.preview_digest,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        assert second_commit.place_id == first_commit.place_id
        async with database.transaction(
            principal_id=property_principal["principal_id"]
        ) as connection:
            after_counts = (
                (
                    await connection.execute(
                        select(
                            func.count(func.distinct(schema.places.c.place_id)).label(
                                "places"
                            ),
                            func.count(
                                func.distinct(schema.place_locators.c.locator_id)
                            ).label("locators"),
                        )
                        .select_from(
                            schema.places.join(
                                schema.place_locators,
                                schema.places.c.place_id
                                == schema.place_locators.c.place_id,
                            )
                        )
                        .where(
                            schema.places.c.owner_principal_id
                            == property_principal["principal_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert after_counts == before_counts
    finally:
        spool.close()
        await database.close()


@pytest.mark.asyncio
async def test_worker_skip_locked_and_ledger_retry_idempotency(tmp_path) -> None:
    database = Database(os.environ["TEST_DATABASE_URL"])
    await _reset_test_authority(database)
    ledger_path = tmp_path / "worker-ledger.jsonl"
    first_ledger = EncryptedErasureLedger(ledger_path, b"e" * 32, initialize=True)
    second_ledger = EncryptedErasureLedger(ledger_path, b"e" * 32)
    first = DurableWorker(database, first_ledger, DisabledRuntimeSpool())
    second = DurableWorker(database, second_ledger, DisabledRuntimeSpool())

    def make_record(outbox_id: uuid.UUID) -> ErasureLedgerRecord:
        return ErasureLedgerRecord(
            outbox_id=outbox_id,
            erasure_request_id=uuid.uuid4(),
            principal_id=uuid.uuid4(),
            fact_id=uuid.uuid4(),
            operation_codes=["scrub_descriptor_fact"],
            policy_digest="a" * 64,
            completed_at=datetime.now(UTC),
            source_created_at=datetime.now(UTC),
            checkpoint_affected=True,
            exact_residual_codes=["backup_expiry_unverified"],
        )

    async def insert_record(record: ErasureLedgerRecord) -> None:
        payload = record.model_dump(mode="json")
        payload.pop("outbox_id")
        payload.pop("source_created_at")
        async with database.transaction() as connection:
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=record.outbox_id,
                    topic="privacy.erasure.completed",
                    aggregate_id=record.fact_id,
                    payload=payload,
                    created_at=record.source_created_at,
                )
            )

    try:
        async with database.transaction() as connection:
            await connection.execute(delete(schema.erasure_replay_receipts))
            await connection.execute(delete(schema.erasure_ledger_state))
            await connection.execute(delete(schema.outbox))

        records = [make_record(uuid.uuid4()), make_record(uuid.uuid4())]
        for value in records:
            await insert_record(value)
        results = await asyncio.gather(first.run_once(), second.run_once())
        assert results == [True, True]
        assert first_ledger.head().epoch == 2
        async with database.transaction() as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.outbox)
                    .where(schema.outbox.c.state == "complete")
                )
            ).scalar_one() == 2

        crash_record = make_record(uuid.uuid4())
        await insert_record(crash_record)
        claim = await first._claim_one(datetime.now(UTC))
        assert claim is not None
        reconstructed = ErasureLedgerRecord.model_validate(
            {
                "outbox_id": claim.outbox_id,
                "source_created_at": claim.created_at,
                **claim.payload,
            }
        )
        crash_entry = first_ledger.append_once(reconstructed)
        assert crash_entry.epoch == 3
        await first._retry_claim(
            claim, datetime.now(UTC), "injected_after_ledger_fsync"
        )
        async with database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == crash_record.outbox_id)
                .values(available_at=datetime.now(UTC))
            )
        assert await second.run_once() is True
        assert first_ledger.head().epoch == 3
        assert len(first_ledger.entries_after(0)) == 3

        invalid_id = uuid.uuid4()
        async with database.transaction() as connection:
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=invalid_id,
                    topic="privacy.erasure.completed",
                    aggregate_id=uuid.uuid4(),
                    payload={"version": 1},
                )
            )
        assert await first.run_once() is True
        health = await outbox_health(database)
        assert health.ready is False
        assert health.code == "erasure_receipt_failed"
        async with database.transaction() as connection:
            invalid = (
                (
                    await connection.execute(
                        select(
                            schema.outbox.c.state,
                            schema.outbox.c.last_error_code,
                        ).where(schema.outbox.c.outbox_id == invalid_id)
                    )
                )
                .mappings()
                .one()
            )
            assert invalid == {
                "state": "failed",
                "last_error_code": "invalid_erasure_receipt",
            }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_legacy_imports_are_exactly_idempotent_and_drift_safe() -> None:
    database = Database(os.environ["TEST_DATABASE_URL"])
    await _reset_test_authority(database)
    knowledge_key = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode="shadow",
    )
    store = CoreStore(database, DisabledRuntimeSpool(), settings)
    person_id = uuid.uuid4()
    person_value = PersonCreate(
        person_id=person_id,
        display_name="Reviewed Legacy Person",
        pronouns="they/them",
        privacy_scope="private",
        legacy_source_ref="legacy:person:reviewed",
        legacy_source_version=7,
        legacy_source_sha256="b" * 64,
    )
    try:
        created = await store.create_person(person_value)
        duplicate = await store.create_person(person_value)
        assert duplicate.person_id == created.person_id == person_id
        for update_value in (
            {"display_name": "Drifted Name"},
            {"pronouns": "she/her"},
            {"privacy_scope": "household"},
            {"legacy_source_ref": "legacy:person:other"},
            {"legacy_source_version": 8},
            {"legacy_source_sha256": "c" * 64},
        ):
            with pytest.raises(ConflictError, match="projection drifted"):
                await store.create_person(person_value.model_copy(update=update_value))

        verified = await store.verify_reviewed_person(
            ReviewedPersonVerify(
                person_id=person_id,
                display_name=person_value.display_name,
                legacy_source_sha256=person_value.legacy_source_sha256,
            )
        )
        assert verified.person_id == person_id
        with pytest.raises(ConflictError, match="projection drifted"):
            await store.verify_reviewed_person(
                ReviewedPersonVerify(
                    person_id=person_id,
                    display_name="Wrong",
                    legacy_source_sha256=person_value.legacy_source_sha256,
                )
            )
        with pytest.raises(NotFoundError):
            await store.verify_reviewed_person(
                ReviewedPersonVerify(
                    person_id=uuid.uuid4(),
                    display_name="Absent",
                    legacy_source_sha256="d" * 64,
                )
            )

        role = LegacyRoleImport(
            person_id=person_id,
            role_label="parent",
            source_ref="legacy:role:reviewed",
            source_version=3,
            source_snapshot_sha256="e" * 64,
        )
        label_id = await store.import_legacy_role(role)
        assert await store.import_legacy_role(role) == label_id
        for update_value in (
            {"role_label": "sibling"},
            {"source_version": 4},
            {"source_snapshot_sha256": "f" * 64},
        ):
            with pytest.raises(ConflictError, match="projection drifted"):
                await store.import_legacy_role(role.model_copy(update=update_value))

        async with database.transaction() as connection:
            await connection.execute(
                update(schema.people)
                .where(schema.people.c.person_id == person_id)
                .values(status="archived")
            )
        with pytest.raises(ConflictError, match="projection drifted"):
            await store.create_person(person_value)
    finally:
        await database.close()

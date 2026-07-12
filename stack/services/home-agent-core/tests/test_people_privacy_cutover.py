from __future__ import annotations

import base64
import os
import uuid
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from psycopg.types.range import Range

from app import schema
from app.api import semantic_router
from app.config import Settings
from app.crypto import sha256_json
from app.errors import CapabilityDisabledError, ForbiddenError
from app.ledger import ErasureLedgerRecord, LedgerEntry, LedgerHead
from app.models import (
    HaContext,
    IngestBatch,
    IngestEnvelope,
    PersonCreate,
    ReviewedPrivacyDirectiveImport,
    SourceEntityBindingCreate,
)
from app.spool import DisabledRuntimeSpool, EncryptedRuntimeSpool
from app.store import CoreStore
from app.db import Database
from app.worker import DurableWorker, OutboxClaim
from tests.binding_helpers import complete_staged_principal_binding
from tests.maintenance_helpers import seed_current_worker_maintenance


SERVICE_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    SERVICE_ROOT / "alembic" / "versions" / "0002_people_privacy_cutover.py"
).read_text(encoding="utf-8")
GRANTS = (SERVICE_ROOT.parents[1] / "home-agent-deploy" / "apply-grants.sh").read_text(
    encoding="utf-8"
)
WORKER = (SERVICE_ROOT / "app" / "worker.py").read_text(encoding="utf-8")


def _settings(mode: str = "record_only") -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    return Settings(
        database_url=SecretStr("postgresql+psycopg://unused:unused@127.0.0.1:1/unused"),
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode=mode,
    )


def test_rollout_default_blocks_people_and_memory_without_mutable_api() -> None:
    store = CoreStore(object(), DisabledRuntimeSpool(), _settings())  # type: ignore[arg-type]
    with pytest.raises(CapabilityDisabledError, match="rollout mode shadow"):
        store._require_rollout("shadow", "semantic_people_write")
    with pytest.raises(CapabilityDisabledError, match="rollout mode canary"):
        store._require_rollout("canary", "persistent_memory")

    shadow = CoreStore(
        object(),
        DisabledRuntimeSpool(),
        _settings("shadow"),  # type: ignore[arg-type]
    )
    shadow._require_rollout("shadow", "semantic_people_write")
    with pytest.raises(CapabilityDisabledError):
        shadow._require_rollout("canary", "persistent_memory")


def test_cutover_routes_are_typed_and_relationship_candidates_are_not_facts() -> None:
    routes = {
        (method, route.path)
        for route in semantic_router().routes
        for method in route.methods or set()
    }
    assert {
        ("POST", "/v1/people/{person_id}/aliases"),
        ("POST", "/v1/people/{person_id}/recognition-bindings"),
        ("POST", "/v1/people/{person_id}/privacy-directives"),
        ("POST", "/v1/people/{person_id}/status-import"),
        ("POST", "/v1/people/legacy-relationship-candidates"),
    } <= routes
    assert schema.external_recognition_bindings is not schema.source_entity_bindings
    assert "global_normalized_alias" in {
        constraint.name for constraint in schema.aliases.constraints
    }
    assert (
        schema.legacy_relationship_candidates.c.authoritative.server_default is not None
    )


def test_auto_expiry_kernel_has_narrow_authority_and_restore_path() -> None:
    assert "SET search_path = pg_catalog, pg_temp" in MIGRATION
    assert "target_schedule_id uuid" in MIGRATION
    assert "s.state = 'scheduled'" in MIGRATION
    assert "d.expires_at <= operation_time" in MIGRATION
    assert "privacy.replay_person_auto_expiry" in MIGRATION
    assert "metadata = jsonb_build_object(" in MIGRATION
    assert "DELETE FROM ingest.artifact_links" in MIGRATION
    assert "WITH RECURSIVE affected_artifacts" in MIGRATION
    assert "link.child_artifact_id" in MIGRATION
    assert "DELETE FROM knowledge.place_locators" in MIGRATION
    assert "DELETE FROM knowledge.places" in MIGRATION
    assert "ha_context->>'user_id' = ANY(bound_user_ids)" in MIGRATION
    assert "root_observation_id = erased.replacement_root_id" in MIGRATION
    assert "evidence_family_id = erased.replacement_root_id" in MIGRATION
    assert "resolution_at_commit" in MIGRATION
    assert "GRANT UPDATE (state, completed_at)" in GRANTS
    assert "GRANT UPDATE ON TABLE privacy.auto_expiry_schedules" not in GRANTS
    assert "transaction(serializable=True)" in WORKER
    assert "run_serializable(apply_auto_expiry)" in WORKER


class _SynchronizedMemoryLedger:
    """Worker test ledger synchronized to any pre-existing shared DB epoch."""

    def __init__(self, epoch: int, head_hash: str) -> None:
        self._head = LedgerHead(epoch, head_hash)
        self._entries: dict[uuid.UUID, LedgerEntry] = {}

    def append_once(self, record: ErasureLedgerRecord) -> LedgerEntry:
        existing = self._entries.get(record.outbox_id)
        if existing is not None:
            return existing
        digest = sha256_json(record.model_dump(mode="json"))
        epoch = self._head.epoch + 1
        record_hash = hashlib.sha256(
            f"{self._head.head_hash}:{epoch}:{digest}".encode()
        ).hexdigest()
        entry = LedgerEntry(epoch, record_hash, digest, record)
        self._entries[record.outbox_id] = entry
        self._head = LedgerHead(epoch, record_hash)
        return entry

    def head(self) -> LedgerHead:
        return self._head


def test_person_erasure_ledger_subject_is_exact() -> None:
    record = ErasureLedgerRecord(
        outbox_id=uuid.uuid4(),
        erasure_request_id=uuid.uuid4(),
        subject_kind="person",
        person_id=uuid.uuid4(),
        operation_codes=["scrub_person_content"],
        policy_digest="a" * 64,
        completed_at=datetime.now(UTC),
        source_created_at=datetime.now(UTC),
        checkpoint_affected=True,
    )
    assert record.principal_id is None and record.fact_id is None
    invalid = record.model_dump()
    invalid["principal_id"] = uuid.uuid4()
    with pytest.raises(ValidationError):
        ErasureLedgerRecord(**invalid)


def test_auto_expiry_directive_requires_exact_timestamp_shape() -> None:
    now = datetime.now(UTC)
    value = ReviewedPrivacyDirectiveImport(
        directive="auto_expire",
        expires_at=now,
        source_ref="legacy:test",
        source_version=1,
        source_snapshot_sha256="a" * 64,
    )
    assert value.expires_at == now


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
async def test_postgres_reviewed_people_cutover_is_exact_and_collision_safe() -> None:
    from app.models import (
        ReviewedAliasImport,
        ReviewedRecognitionBindingImport,
        ReviewedPersonStatusImport,
    )

    knowledge_key = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode="shadow",
    )
    database = Database(settings.async_database_url())
    store = CoreStore(database, DisabledRuntimeSpool(), settings)
    first_id, second_id, ignored_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        for person_id, name in (
            (first_id, "Reviewed One"),
            (second_id, "Reviewed Two"),
            (ignored_id, "Reviewed Ignored"),
        ):
            await store.create_person(
                PersonCreate(person_id=person_id, display_name=name)
            )
        alias_text = f"Mãe {first_id.hex}"
        alias = ReviewedAliasImport(
            alias=alias_text,
            alias_kind="nickname",
            source_ref=f"legacy:alias:{first_id}",
            source_version=1,
            source_snapshot_sha256="b" * 64,
        )
        first = await store.import_reviewed_alias(first_id, alias)
        assert (
            await store.import_reviewed_alias(first_id, alias)
        ).record_id == first.record_id
        with pytest.raises(Exception, match="collides"):
            await store.import_reviewed_alias(
                second_id,
                alias.model_copy(
                    update={
                        "alias": alias_text.upper(),
                        "source_ref": f"legacy:alias:{second_id}",
                        "source_snapshot_sha256": "c" * 64,
                    }
                ),
            )
        binding = ReviewedRecognitionBindingImport(
            external_system="frigate",
            external_id=f"reviewed_cluster_{first_id.hex}",
            status="active",
            source_ref=f"legacy:enrollment:{first_id}",
            source_version=1,
            source_snapshot_sha256="d" * 64,
        )
        receipt = await store.import_reviewed_recognition_binding(first_id, binding)
        assert receipt.state == "active"
        status = await store.import_reviewed_person_status(
            second_id,
            ReviewedPersonStatusImport(
                status="archived",
                source_ref=f"legacy:status:{second_id}",
                source_version=1,
                source_snapshot_sha256="e" * 64,
            ),
        )
        assert status.state == "archived"
        await store.import_reviewed_privacy_directive(
            ignored_id,
            ReviewedPrivacyDirectiveImport(
                directive="ignored",
                source_ref=f"legacy:privacy:{ignored_id}",
                source_version=1,
                source_snapshot_sha256="f" * 64,
            ),
        )
        with pytest.raises(ForbiddenError, match="unavailable"):
            await complete_staged_principal_binding(
                store,
                ha_user_id="ignored-ha-user",
                person_id=ignored_id,
            )
        async with database.transaction() as connection:
            assert (
                await connection.execute(
                    select(schema.external_recognition_bindings.c.binding_id).where(
                        schema.external_recognition_bindings.c.binding_id
                        == receipt.record_id
                    )
                )
            ).scalar_one() == receipt.record_id
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principals)
                    .where(schema.principals.c.person_id == ignored_id)
                )
            ).scalar_one() == 0
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
async def test_postgres_auto_expiry_blocks_ingress_scrubs_places_and_replays_pre_schedule(
    tmp_path,
) -> None:
    knowledge_key = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        runtime_spool_path=tmp_path / "runtime.sqlite",
        runtime_spool_key=SecretStr(
            base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("=")
        ),
        knowledge_encryption_key=SecretStr(knowledge_key),
        erasure_ledger_key=SecretStr(
            base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
        ),
        edge_token=SecretStr("edge-token-with-at-least-thirty-two-chars"),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="all",
        rollout_mode="canary",
    )
    database = Database(settings.async_database_url())
    await seed_current_worker_maintenance(database)
    spool = EncryptedRuntimeSpool(tmp_path / "runtime.sqlite", b"r" * 32)
    store = CoreStore(database, spool, settings)
    now = datetime.now(UTC)

    async def claim_exact(outbox_id: uuid.UUID) -> OutboxClaim:
        claim_token = uuid.uuid4()
        async with database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(schema.outbox)
                        .where(schema.outbox.c.outbox_id == outbox_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            attempts = row["attempts"] + 1
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == outbox_id)
                .values(
                    state="running",
                    attempts=attempts,
                    claimed_at=now,
                    claim_token=claim_token,
                    last_error_code=None,
                )
            )
        return OutboxClaim(
            outbox_id=outbox_id,
            claim_token=claim_token,
            attempts=attempts,
            payload=dict(row["payload"]),
            created_at=row["created_at"],
        )

    try:
        person = await store.create_person(
            PersonCreate(display_name="Auto Expiry Integration Subject")
        )
        ha_user_id = f"ha-auto-expiry-{uuid.uuid4()}"
        principal_view = await complete_staged_principal_binding(
            store,
            ha_user_id=ha_user_id,
            person_id=person.person_id,
        )
        principal = await store.resolve_principal(ha_user_id)
        entity_id = f"device_tracker.auto_expiry_{uuid.uuid4().hex}"
        await store.bind_source_entity(
            principal,
            SourceEntityBindingCreate(
                entity_id=entity_id,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        epoch = uuid.uuid4()
        await store.ingest(
            IngestBatch(
                envelopes=[
                    IngestEnvelope(
                        edge_instance_id=f"edge-auto-{uuid.uuid4()}",
                        source_name="home_assistant",
                        epoch=epoch,
                        sequence=1,
                        event_type="conversation_turn",
                        source_observed_at=now,
                        edge_received_at=now,
                        payload={"content_discarded": True},
                        dependency_domain="home_assistant:conversation",
                        coverage="continuous",
                        ha_context=HaContext(user_id=ha_user_id),
                    )
                ]
            )
        )
        assert spool.stats()["records"] == 1

        place_id = uuid.uuid4()
        locator_id = uuid.uuid4()
        owned_artifact_id = uuid.uuid4()
        derived_artifact_id = uuid.uuid4()
        async with database.transaction(
            principal_id=principal_view.principal_id
        ) as connection:
            await connection.execute(
                insert(schema.places).values(
                    place_id=place_id,
                    place_type="property",
                    canonical_name="ERASURE PLACE CANARY",
                    owner_principal_id=principal_view.principal_id,
                    privacy_scope="private",
                    status="active",
                )
            )
            await connection.execute(
                insert(schema.place_locators).values(
                    locator_id=locator_id,
                    place_id=place_id,
                    ciphertext=b"encrypted-locator-canary",
                    nonce=b"n" * 12,
                    key_id="test-key",
                    locator_sha256="b" * 64,
                    radius_m=40,
                    valid_range=Range(now, None, bounds="[)"),
                )
            )
            await connection.execute(
                insert(schema.artifact_registry),
                [
                    {
                        "artifact_id": owned_artifact_id,
                        "artifact_kind": "memory_source",
                        "store": "postgresql",
                        "owner_principal_id": principal_view.principal_id,
                        "retention_class": "person_scoped",
                        "status": "active",
                    },
                    {
                        "artifact_id": derived_artifact_id,
                        "artifact_kind": "generated_summary",
                        "store": "postgresql",
                        "owner_principal_id": None,
                        "retention_class": "derived",
                        "status": "active",
                    },
                ],
            )
            await connection.execute(
                insert(schema.artifact_links).values(
                    link_id=uuid.uuid4(),
                    parent_artifact_id=owned_artifact_id,
                    child_artifact_id=derived_artifact_id,
                    relation="derived_from",
                )
            )

        directive = await store.import_reviewed_privacy_directive(
            person.person_id,
            ReviewedPrivacyDirectiveImport(
                directive="auto_expire",
                expires_at=now - timedelta(seconds=1),
                source_ref=f"legacy:auto-expire:{person.person_id}",
                source_version=1,
                source_snapshot_sha256="c" * 64,
            ),
        )
        with pytest.raises(ForbiddenError, match="no available"):
            await store.resolve_principal(ha_user_id)
        policy = await store.edge_privacy_policy()
        assert ha_user_id in policy.blocked_user_ids
        assert entity_id in policy.blocked_entity_ids

        blocked_edge = f"edge-blocked-{uuid.uuid4()}"
        blocked_epoch = uuid.uuid4()
        result = await store.ingest(
            IngestBatch(
                envelopes=[
                    IngestEnvelope(
                        edge_instance_id=blocked_edge,
                        source_name="home_assistant",
                        epoch=blocked_epoch,
                        sequence=1,
                        event_type="conversation_turn",
                        source_observed_at=now,
                        edge_received_at=now,
                        payload={"content_discarded": True},
                        dependency_domain="home_assistant:conversation",
                        coverage="continuous",
                        ha_context=HaContext(user_id=ha_user_id),
                    ),
                    IngestEnvelope(
                        edge_instance_id=blocked_edge,
                        source_name="home_assistant",
                        epoch=blocked_epoch,
                        sequence=2,
                        event_type="conversation_turn",
                        source_observed_at=now,
                        edge_received_at=now,
                        payload={"content_discarded": True},
                        dependency_domain="home_assistant:conversation",
                        coverage="continuous",
                        ha_context=HaContext(
                            user_id=f"unknown-restored-{uuid.uuid4()}"
                        ),
                    ),
                ]
            )
        )
        assert result.accepted == 2
        assert spool.stats()["records"] == 1
        async with database.transaction() as connection:
            blocked_headers = (
                (
                    await connection.execute(
                        select(
                            schema.envelopes.c.ha_context,
                            schema.envelopes.c.metadata,
                        )
                        .select_from(
                            schema.envelopes.join(
                                schema.source_streams,
                                schema.envelopes.c.stream_id
                                == schema.source_streams.c.stream_id,
                            )
                        )
                        .where(schema.source_streams.c.edge_instance_id == blocked_edge)
                        .order_by(schema.envelopes.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
        assert [row["ha_context"] for row in blocked_headers] == [{}, {}]
        assert [row["metadata"]["raw_payload_status"] for row in blocked_headers] == [
            "suppressed_auto_expire",
            "suppressed_unbound_user",
        ]

        async with database.transaction() as connection:
            schedule = (
                (
                    await connection.execute(
                        select(schema.auto_expiry_schedules).where(
                            schema.auto_expiry_schedules.c.directive_id
                            == directive.record_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            ledger_state = (
                (
                    await connection.execute(
                        select(schema.erasure_ledger_state).where(
                            schema.erasure_ledger_state.c.state_key == "current"
                        )
                    )
                )
                .mappings()
                .first()
            )
        ledger = _SynchronizedMemoryLedger(
            ledger_state["recorded_epoch"] if ledger_state else 0,
            ledger_state["recorded_head_hash"] if ledger_state else "0" * 64,
        )
        worker = DurableWorker(database, ledger, spool)  # type: ignore[arg-type]
        source_claim = await claim_exact(schedule["outbox_id"])
        await worker._complete_auto_expiry(source_claim, now)
        async with database.transaction() as connection:
            receipt = (
                (
                    await connection.execute(
                        select(schema.auto_expiry_receipts).where(
                            schema.auto_expiry_receipts.c.schedule_id
                            == schedule["schedule_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
        ledger_claim = await claim_exact(receipt["ledger_outbox_id"])
        record = ErasureLedgerRecord.model_validate(
            {
                "outbox_id": ledger_claim.outbox_id,
                "source_created_at": ledger_claim.created_at,
                **ledger_claim.payload,
            }
        )
        entry = ledger.append_once(record)
        await worker._complete_claim(ledger_claim, entry, ledger.head(), now)

        assert spool.stats()["records"] == 0
        assert receipt["cascade_counts"]["places"] == 1
        assert receipt["cascade_counts"]["place_locators"] == 1
        assert "delete_private_place_locators" in receipt["operation_codes"]
        assert "delete_principal_binding_requests" in receipt["operation_codes"]
        assert "delete_principal_binding_proposals" in receipt["operation_codes"]
        assert "delete_ha_user_bindings" in receipt["operation_codes"]
        assert ha_user_id not in str(receipt["cascade_counts"])
        async with database.transaction() as connection:
            assert (
                await connection.execute(
                    select(schema.people.c.status).where(
                        schema.people.c.person_id == person.person_id
                    )
                )
            ).scalar_one() == "erased"
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.places)
                    .where(schema.places.c.place_id == place_id)
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.artifact_registry)
                    .where(
                        schema.artifact_registry.c.artifact_id.in_(
                            [owned_artifact_id, derived_artifact_id]
                        )
                    )
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.artifact_links)
                    .where(
                        schema.artifact_links.c.child_artifact_id == derived_artifact_id
                    )
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.place_locators)
                    .where(schema.place_locators.c.locator_id == locator_id)
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(schema.auto_expiry_schedules.c.state).where(
                        schema.auto_expiry_schedules.c.schedule_id
                        == schedule["schedule_id"]
                    )
                )
            ).scalar_one() == "complete"
            first_header = (
                (
                    await connection.execute(
                        select(schema.envelopes).where(
                            schema.envelopes.c.stream_id
                            == (
                                select(schema.source_streams.c.stream_id)
                                .where(
                                    schema.source_streams.c.edge_instance_id.like(
                                        "edge-auto-%"
                                    )
                                )
                                .order_by(schema.source_streams.c.created_at.desc())
                                .limit(1)
                                .scalar_subquery()
                            ),
                            schema.envelopes.c.sequence == 1,
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert first_header["ha_context"] == {}
            assert first_header["metadata"] == {
                "raw_payload_status": "erased_auto_expiry"
            }

        # Restore-kernel regression: a backup may contain the person and exact
        # place but predate both the later directive and schedule transaction.
        restored = await store.create_person(
            PersonCreate(display_name="Pre-schedule Restored Subject")
        )
        restored_user = f"ha-restored-{uuid.uuid4()}"
        restored_principal = await complete_staged_principal_binding(
            store,
            ha_user_id=restored_user,
            person_id=restored.person_id,
        )
        restored_place = uuid.uuid4()
        restored_locator = uuid.uuid4()
        replay_schedule = uuid.uuid4()
        replay_ledger_outbox = uuid.uuid4()
        async with database.transaction(
            principal_id=restored_principal.principal_id, serializable=True
        ) as connection:
            await connection.execute(
                insert(schema.places).values(
                    place_id=restored_place,
                    place_type="property",
                    canonical_name="RESTORED LOCATOR CANARY",
                    owner_principal_id=restored_principal.principal_id,
                    privacy_scope="private",
                    status="active",
                )
            )
            await connection.execute(
                insert(schema.place_locators).values(
                    locator_id=restored_locator,
                    place_id=restored_place,
                    ciphertext=b"restored-encrypted-locator",
                    nonce=b"z" * 12,
                    key_id="test-key",
                    locator_sha256="d" * 64,
                    radius_m=40,
                    valid_range=Range(now, None, bounds="[)"),
                )
            )
            replay_counts = (
                await connection.execute(
                    select(
                        func.privacy.replay_person_auto_expiry(
                            replay_schedule,
                            restored.person_id,
                            replay_ledger_outbox,
                        )
                    )
                )
            ).scalar_one()
        assert replay_counts["places"] == 1
        assert replay_counts["place_locators"] == 1
        async with database.transaction() as connection:
            assert (
                await connection.execute(
                    select(schema.people.c.status).where(
                        schema.people.c.person_id == restored.person_id
                    )
                )
            ).scalar_one() == "erased"
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.place_locators)
                    .where(schema.place_locators.c.locator_id == restored_locator)
                )
            ).scalar_one() == 0
    finally:
        spool.close()
        await database.close()

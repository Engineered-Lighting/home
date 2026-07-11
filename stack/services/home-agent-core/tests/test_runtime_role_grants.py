from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.range import Range
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, update

from app import schema
from app.config import Settings
from app.db import Database
from app.ledger import EncryptedErasureLedger, ErasureLedgerRecord
from app.restore import RestoreQuarantineGate, RestoreReplay, outbox_health
from app.spool import DisabledRuntimeSpool
from app.store import CoreStore
from app.worker import DurableWorker


pytestmark = pytest.mark.skipif(
    not all(
        os.getenv(name)
        for name in (
            "TEST_DATABASE_URL",
            "TEST_INGEST_DATABASE_URL",
            "TEST_ERASURE_DATABASE_URL",
            "TEST_WORKER_DATABASE_URL",
        )
    ),
    reason="runtime-role database URLs are required",
)


@pytest.mark.asyncio
async def test_ingest_readiness_conflict_depart_and_erasure_restore_grants(
    tmp_path,
) -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    ingest = Database(os.environ["TEST_INGEST_DATABASE_URL"])
    erasure = Database(os.environ["TEST_ERASURE_DATABASE_URL"])
    worker_database = Database(os.environ["TEST_WORKER_DATABASE_URL"])
    principal_id = uuid.uuid4()
    person_id = uuid.uuid4()
    visit_id = uuid.uuid4()
    initiative_id = uuid.uuid4()
    privacy_directive_id = uuid.uuid4()
    ha_binding_id = uuid.uuid4()
    ha_user_id = f"role-grant-user-{uuid.uuid4()}"
    worker_request_id = uuid.uuid4()
    worker_fact_id = uuid.uuid4()
    worker_outbox_id = uuid.uuid4()
    now = datetime.now(UTC)
    key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_INGEST_DATABASE_URL"]),
        runtime_spool_key=SecretStr(
            base64.urlsafe_b64encode(b"s" * 32).decode().rstrip("=")
        ),
        knowledge_encryption_key=SecretStr(key),
        edge_token=SecretStr("ingest-role-edge-token-at-least-32-chars"),
        policy_digest="a" * 64,
        role="ingest",
    )
    store = CoreStore(ingest, DisabledRuntimeSpool(), settings)
    try:
        async with owner.transaction() as connection:
            await connection.execute(
                insert(schema.people).values(
                    person_id=person_id,
                    display_name="Role Grant Person",
                    privacy_scope="private",
                    status="active",
                )
            )
            await connection.execute(
                insert(schema.principals).values(
                    principal_id=principal_id,
                    person_id=person_id,
                    kind="ha_user",
                    display_label="Role Grant Principal",
                    status="active",
                )
            )
            await connection.execute(
                insert(schema.ha_user_bindings).values(
                    binding_id=ha_binding_id,
                    ha_user_id=ha_user_id,
                    principal_id=principal_id,
                    person_id=person_id,
                    confirmed_by_principal_id=principal_id,
                    confirmed_at=now,
                )
            )
            await connection.execute(
                insert(schema.privacy_directives).values(
                    directive_id=privacy_directive_id,
                    person_id=person_id,
                    directive="auto_expire",
                    enabled=True,
                    expires_at=now - timedelta(seconds=1),
                    source_ref="role-grant-test",
                    source_version=1,
                    source_snapshot_sha256="b" * 64,
                )
            )
            await connection.execute(
                insert(schema.visits).values(
                    visit_id=visit_id,
                    principal_id=principal_id,
                    first_root_observation_id=uuid.uuid4(),
                    observed_range=Range(now - timedelta(minutes=10), now, bounds="[)"),
                    state="visiting",
                    freshness="fresh",
                    coverage="sufficient",
                    anchor_ciphertext=b"ciphertext",
                    anchor_nonce=b"nonce",
                    anchor_sha256="a" * 64,
                    anchor_kind="specific",
                    anchor_radius_m=40,
                )
            )
            await connection.execute(
                insert(schema.initiatives).values(
                    initiative_id=initiative_id,
                    principal_id=principal_id,
                    visit_id=visit_id,
                    purpose="travel_arrival",
                    dedupe_key=f"role-grant:{initiative_id}",
                    sensitivity="private_location",
                    allowed_surfaces=["private_tauri"],
                    template_key="travel_arrival_v1",
                    state="pending",
                    expires_at=now + timedelta(days=1),
                )
            )

        async with ingest.transaction(principal_id=principal_id) as connection:
            await store._mark_location_conflicted(connection, principal_id=principal_id)
        async with owner.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(
                            schema.visits.c.state,
                            schema.initiatives.c.state.label("initiative_state"),
                        )
                        .select_from(
                            schema.visits.join(
                                schema.initiatives,
                                schema.visits.c.visit_id
                                == schema.initiatives.c.visit_id,
                            )
                        )
                        .where(schema.visits.c.visit_id == visit_id)
                    )
                )
                .mappings()
                .one()
            )
            assert row == {"state": "conflicted", "initiative_state": "suppressed"}
            await connection.execute(
                update(schema.visits)
                .where(schema.visits.c.visit_id == visit_id)
                .values(state="visiting", coverage="sufficient")
            )
            await connection.execute(
                update(schema.initiatives)
                .where(schema.initiatives.c.initiative_id == initiative_id)
                .values(state="pending", suppression_reason=None)
            )
        async with ingest.transaction(principal_id=principal_id) as connection:
            await store._depart_active_visits(connection, principal_id=principal_id)
        assert (await outbox_health(ingest)).code != "unavailable"
        privacy_policy = await store.edge_privacy_policy()
        assert ha_user_id in privacy_policy.blocked_user_ids

        async with owner.transaction() as connection:
            await connection.execute(delete(schema.erasure_replay_receipts))
            await connection.execute(delete(schema.erasure_ledger_state))
            await connection.execute(
                insert(schema.erasure_requests).values(
                    erasure_request_id=worker_request_id,
                    principal_id=principal_id,
                    scope={"descriptor_fact_id": str(worker_fact_id)},
                    state="ledger_pending",
                    policy_digest="a" * 64,
                    completed_at=now,
                )
            )
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=worker_outbox_id,
                    topic="privacy.erasure.completed",
                    aggregate_id=worker_fact_id,
                    payload={
                        "version": 1,
                        "erasure_request_id": str(worker_request_id),
                        "principal_id": str(principal_id),
                        "fact_id": str(worker_fact_id),
                        "operation_codes": ["scrub_descriptor_fact"],
                        "policy_digest": "a" * 64,
                        "completed_at": now.isoformat(),
                        "external_pending_codes": [],
                        "legacy_untracked_codes": [],
                        "checkpoint_affected": True,
                        "backup_expiry_at": None,
                        "exact_residual_codes": ["backup_expiry_unverified"],
                    },
                )
            )
        worker_ledger = EncryptedErasureLedger(
            tmp_path / "worker-ledger.jsonl",
            b"w" * 32,
            head_path=tmp_path / "worker-ledger.head.json",
            initialize=True,
        )
        worker = DurableWorker(worker_database, worker_ledger, DisabledRuntimeSpool())
        assert await worker.run_once(now=now + timedelta(seconds=1)) is True
        async with owner.transaction() as connection:
            assert (
                await connection.execute(
                    select(schema.erasure_requests.c.state).where(
                        schema.erasure_requests.c.erasure_request_id
                        == worker_request_id
                    )
                )
            ).scalar_one() == "complete"

        head_path = tmp_path / "grant-ledger.head.json"
        ledger = EncryptedErasureLedger(
            tmp_path / "grant-ledger.jsonl",
            b"e" * 32,
            head_path=head_path,
            initialize=True,
        )
        record = ErasureLedgerRecord(
            outbox_id=uuid.uuid4(),
            erasure_request_id=uuid.uuid4(),
            principal_id=uuid.uuid4(),
            fact_id=uuid.uuid4(),
            operation_codes=["scrub_descriptor_fact"],
            policy_digest="a" * 64,
            completed_at=now,
            source_created_at=now,
            checkpoint_affected=True,
            exact_residual_codes=["backup_expiry_unverified"],
        )
        ledger.append_once(record)
        async with owner.transaction() as connection:
            await connection.execute(delete(schema.erasure_replay_receipts))
            await connection.execute(delete(schema.erasure_ledger_state))
        assert await RestoreReplay(erasure, ledger).replay() == 1
        status = await RestoreQuarantineGate(
            erasure, head_path, cache_seconds=0
        ).status(force=True)
        assert status.current is True
    finally:
        async with owner.transaction() as connection:
            await connection.execute(
                delete(schema.outbox).where(
                    schema.outbox.c.outbox_id == worker_outbox_id
                )
            )
            await connection.execute(
                delete(schema.erasure_requests).where(
                    schema.erasure_requests.c.erasure_request_id == worker_request_id
                )
            )
            await connection.execute(
                delete(schema.initiatives).where(
                    schema.initiatives.c.initiative_id == initiative_id
                )
            )
            await connection.execute(
                delete(schema.visits).where(schema.visits.c.visit_id == visit_id)
            )
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.directive_id == privacy_directive_id
                )
            )
            await connection.execute(
                delete(schema.ha_user_bindings).where(
                    schema.ha_user_bindings.c.binding_id == ha_binding_id
                )
            )
            await connection.execute(
                delete(schema.principals).where(
                    schema.principals.c.principal_id == principal_id
                )
            )
            await connection.execute(
                delete(schema.people).where(schema.people.c.person_id == person_id)
            )
        await ingest.close()
        await erasure.close()
        await worker_database.close()
        await owner.close()

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import DBAPIError

from app import schema
from app.config import Settings
from app.db import Database
from app.models import RolloutAuthorizationRequest
from app.phase2 import Phase2GateInspector
from app.rollout import (
    RECORD_ONLY_TO_SHADOW,
    RolloutAuthorizationGate,
    RolloutAuthorizationService,
)


POLICY_DIGEST = "a" * 64
REQUIRED_URLS = (
    "TEST_DATABASE_URL",
    "TEST_API_DATABASE_URL",
    "TEST_INGEST_DATABASE_URL",
    "TEST_WORKER_DATABASE_URL",
    "TEST_ROLLOUT_DATABASE_URL",
)

pytestmark = pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED_URLS),
    reason="owner, API, ingest, worker, and rollout database URLs are required",
)


def rollout_settings(mode: str = "record_only") -> Settings:
    return Settings(
        database_url=SecretStr(
            "postgresql+psycopg://home_agent_rollout:"
            + "f" * 64
            + "@postgres:5432/home_agent"
        ),
        policy_digest=POLICY_DIGEST,
        policy_version="home-agent-mvp-v1",
        role="rollout",
        rollout_mode=mode,
    )


async def seed_ready_evidence(owner: Database) -> None:
    started_at = datetime.now(UTC) - timedelta(days=8)
    stream_id = uuid.uuid4()
    epoch = uuid.uuid4()
    async with owner.transaction() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE operations.rollout_authorizations, "
                "ingest.envelopes, ingest.source_streams CASCADE"
            )
        )
        await connection.execute(
            insert(schema.source_streams).values(
                stream_id=stream_id,
                edge_instance_id="rollout-role-test-edge",
                source_name="home_assistant",
                epoch=epoch,
                last_acked_sequence=500,
                created_at=started_at,
                updated_at=started_at,
            )
        )
        await connection.execute(
            insert(schema.envelopes),
            [
                {
                    "envelope_id": uuid.uuid4(),
                    "stream_id": stream_id,
                    "sequence": sequence,
                    "event_type": "location_fix",
                    "payload_bytes": 0,
                    "entity_id": None,
                    "source_event_id": None,
                    "source_observed_at": started_at + timedelta(seconds=sequence),
                    "edge_received_at": started_at + timedelta(seconds=sequence),
                    "ingested_at": started_at + timedelta(seconds=sequence),
                    "payload_sha256": hashlib.sha256(
                        str(sequence).encode()
                    ).hexdigest(),
                    "root_observation_id": uuid.uuid4(),
                    "evidence_family_id": uuid.uuid4(),
                    "dependency_domain": "suppressed.precise_location",
                    "freshness": "fresh",
                    "coverage": "continuous",
                    "clock_state": "synchronized",
                    "ha_context": {},
                    "metadata": {
                        "raw_payload_status": "suppressed_unbound_location"
                    },
                }
                for sequence in range(1, 501)
            ],
        )


def receipt_values(*, from_mode: str, to_mode: str) -> dict:
    now = datetime.now(UTC)
    return {
        "authorization_id": uuid.uuid4(),
        "operator_request_id": uuid.uuid4(),
        "from_mode": from_mode,
        "to_mode": to_mode,
        "rule_version": "forged-transition-test",
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": POLICY_DIGEST,
        "input_digest": "d" * 64,
        "readiness_evaluated_at": now,
        "authorized_at": now,
    }


async def assert_denied(operation, expected_sqlstate: str = "42501") -> None:
    with pytest.raises(DBAPIError) as denied:
        await operation()
    assert getattr(denied.value.orig, "sqlstate", None) == expected_sqlstate


@pytest.mark.asyncio
async def test_one_shot_role_is_the_only_writer_and_every_gate_reads_receipt() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    api = Database(os.environ["TEST_API_DATABASE_URL"])
    ingest = Database(os.environ["TEST_INGEST_DATABASE_URL"])
    worker = Database(os.environ["TEST_WORKER_DATABASE_URL"])
    rollout = Database(os.environ["TEST_ROLLOUT_DATABASE_URL"])
    databases = (owner, api, ingest, worker, rollout)
    try:
        await seed_ready_evidence(owner)
        async with owner.transaction() as connection:
            updatable = (
                await connection.execute(
                    text(
                        "SELECT is_updatable FROM information_schema.views "
                        "WHERE table_schema = 'operations' "
                        "AND table_name = 'phase2_rollout_evidence'"
                    )
                )
            ).scalar_one()
            assert updatable == "NO"
            view_privileges = (
                await connection.execute(
                    text(
                        "SELECT "
                        "has_table_privilege('home_agent_rollout', "
                        "'operations.phase2_rollout_evidence', 'SELECT') "
                        "AS can_select, "
                        "has_table_privilege('home_agent_rollout', "
                        "'operations.phase2_rollout_evidence', 'INSERT') "
                        "AS can_insert, "
                        "has_table_privilege('home_agent_rollout', "
                        "'operations.phase2_rollout_evidence', 'UPDATE') "
                        "AS can_update, "
                        "has_table_privilege('home_agent_rollout', "
                        "'operations.phase2_rollout_evidence', 'DELETE') "
                        "AS can_delete"
                    )
                )
            ).mappings().one()
            assert dict(view_privileges) == {
                "can_select": True,
                "can_insert": False,
                "can_update": False,
                "can_delete": False,
            }
        readiness = await Phase2GateInspector(
            api,
            rollout_mode="record_only",
            policy_version="home-agent-mvp-v1",
            policy_digest=POLICY_DIGEST,
        ).inspect()
        assert readiness.ready_to_advance
        request = RolloutAuthorizationRequest(
            operator_request_id=uuid.uuid4(),
            expected_rule_version=RECORD_ONLY_TO_SHADOW.rule_version,
            expected_policy_version="home-agent-mvp-v1",
            expected_policy_digest=POLICY_DIGEST,
            expected_input_digest=readiness.input_digest,
        )
        receipt = await RolloutAuthorizationService(
            rollout, rollout_settings()
        ).authorize_shadow(request)
        assert receipt.from_mode == "record_only"
        assert receipt.to_mode == "shadow"

        shadow_settings = rollout_settings("shadow")
        for database in (api, ingest, worker, rollout):
            status = await RolloutAuthorizationGate(
                database, shadow_settings
            ).status(force=True)
            assert status.authorized, status.code

        async def api_insert():
            async with api.transaction() as connection:
                await connection.execute(
                    insert(schema.rollout_authorizations).values(
                        **receipt_values(from_mode="record_only", to_mode="shadow")
                    )
                )

        async def rollout_update():
            async with rollout.transaction() as connection:
                await connection.execute(
                    update(schema.rollout_authorizations).values(
                        policy_digest="e" * 64
                    )
                )

        async def rollout_delete():
            async with rollout.transaction() as connection:
                await connection.execute(delete(schema.rollout_authorizations))

        async def rollout_canary_insert():
            async with rollout.transaction() as connection:
                await connection.execute(
                    insert(schema.rollout_authorizations).values(
                        **receipt_values(from_mode="shadow", to_mode="canary")
                    )
                )

        async def rollout_base_read():
            async with rollout.transaction() as connection:
                await connection.execute(
                    text("SELECT envelope_id FROM ingest.envelopes LIMIT 1")
                )

        async def rollout_view_insert():
            async with rollout.transaction() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO operations.phase2_rollout_evidence "
                        "(envelope_id) VALUES (:value)"
                    ),
                    {"value": str(uuid.uuid4())},
                )

        await assert_denied(api_insert)
        await assert_denied(rollout_update)
        await assert_denied(rollout_delete)
        await assert_denied(rollout_canary_insert, "23514")
        await assert_denied(rollout_base_read)
        # PostgreSQL reports the security-barrier view's intentional
        # non-updatability before its absent INSERT ACL. Both are asserted.
        await assert_denied(rollout_view_insert, "55000")
    finally:
        try:
            async with owner.transaction() as connection:
                await connection.execute(
                    text(
                        "TRUNCATE TABLE operations.rollout_authorizations, "
                        "ingest.envelopes, ingest.source_streams CASCADE"
                    )
                )
        finally:
            for database in databases:
                await database.close()

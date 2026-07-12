from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from app import schema
from app.config import Settings
from app.db import Database
from app.errors import ConflictError
from app.main import create_app
from app.models import RolloutAuthorizationRequest, RolloutAuthorizationView
from app.rollout import (
    RECORD_ONLY_TO_SHADOW,
    RolloutAuthorizationGate,
    RolloutAuthorizationService,
    RolloutAuthorizationStatus,
    evaluate_rollout_receipt,
)
from app.phase2 import Phase2GateInspector


POLICY_DIGEST = "a" * 64


def settings_for(
    tmp_path, mode: str, *, policy_digest: str = POLICY_DIGEST
) -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    operator_url = (
        "postgresql+psycopg://home_agent_binding_operator:"
        f"{'d' * 64}@postgres:5432/home_agent"
    )
    return Settings(
        database_url=SecretStr(
            os.getenv(
                "TEST_DATABASE_URL",
                "postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
            )
        ),
        operator_database_url=SecretStr(operator_url),
        runtime_spool_path=tmp_path / "runtime.sqlite",
        storage_monitor_path=tmp_path,
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        operator_token=SecretStr("operator-token-with-at-least-32-chars"),
        bootstrap_token=SecretStr("bootstrap-token-with-at-least-32-chars"),
        policy_digest=policy_digest,
        role="api",
        rollout_mode=mode,
    )


def settings_for_role(tmp_path, role: str, mode: str) -> Settings:
    spool_key = base64.urlsafe_b64encode(b"s" * 32).decode().rstrip("=")
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    ledger_key = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    common = {
        "database_url": SecretStr(
            "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
        ),
        "runtime_spool_path": tmp_path / f"{role}-runtime.sqlite",
        "storage_monitor_path": tmp_path,
        "policy_digest": POLICY_DIGEST,
        "role": role,
        "rollout_mode": mode,
    }
    if role in {"worker", "all"}:
        ledger_path = tmp_path / f"{role}-ledger.jsonl"
        ledger_head = tmp_path / f"{role}-ledger.head.json"
        common.update(
            {
                "erasure_ledger_path": ledger_path,
                "erasure_ledger_head_path": ledger_head,
                "erasure_ledger_key": SecretStr(ledger_key),
            }
        )
    if role in {"ingest", "worker", "all"}:
        common["runtime_spool_key"] = SecretStr(spool_key)
    if role in {"api", "ingest", "all"}:
        common["knowledge_encryption_key"] = SecretStr(knowledge_key)
    if role in {"ingest", "all"}:
        common["edge_token"] = SecretStr(
            "edge-token-with-at-least-32-characters"
        )
    if role in {"api", "all"}:
        common.update(
            {
                "service_token": SecretStr(
                    "service-token-with-at-least-32-chars"
                ),
                "operator_token": SecretStr(
                    "operator-token-with-at-least-32-chars"
                ),
                "bootstrap_token": SecretStr(
                    "bootstrap-token-with-at-least-32-chars"
                ),
            }
        )
    if role == "api":
        common["operator_database_url"] = SecretStr(
            "postgresql+psycopg://home_agent_binding_operator:"
            f"{'d' * 64}@postgres:5432/home_agent"
        )
    return Settings(**common)


def receipt(**changes):
    value = {
        "authorization_id": uuid.uuid4(),
        "operator_request_id": uuid.uuid4(),
        "from_mode": "record_only",
        "to_mode": "shadow",
        "rule_version": RECORD_ONLY_TO_SHADOW.rule_version,
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": POLICY_DIGEST,
        "input_digest": "b" * 64,
        "readiness_evaluated_at": datetime.now(UTC),
        "authorized_at": datetime.now(UTC),
    }
    value.update(changes)
    return value


def test_receipt_rejects_stale_rule_wrong_policy_and_stale_evidence() -> None:
    common = {
        "transition": RECORD_ONLY_TO_SHADOW,
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": POLICY_DIGEST,
        "input_digest": "b" * 64,
    }

    assert evaluate_rollout_receipt(receipt(), **common).authorized
    assert (
        evaluate_rollout_receipt(receipt(rule_version="obsolete-v1"), **common).code
        == "authorization_rule_stale"
    )
    assert (
        evaluate_rollout_receipt(receipt(policy_digest="c" * 64), **common).code
        == "authorization_policy_mismatch"
    )
    assert (
        evaluate_rollout_receipt(receipt(input_digest="d" * 64), **common).code
        == "authorization_evidence_stale"
    )


class RejectedRolloutGate:
    async def status(self, *, force: bool = False) -> RolloutAuthorizationStatus:
        return RolloutAuthorizationStatus(False, "authorization_missing")


class FakeLedger:
    def __init__(self, *_args, **_kwargs):
        pass


@pytest.mark.parametrize(
    ("role", "mode"),
    [
        ("api", "shadow"),
        ("ingest", "shadow"),
        ("worker", "shadow"),
        ("all", "shadow"),
        ("api", "canary"),
    ],
)
def test_environment_jump_fails_every_role_startup(
    tmp_path, role: str, mode: str
) -> None:
    with mock.patch("app.main.EncryptedErasureLedger", FakeLedger):
        app = create_app(settings_for_role(tmp_path, role, mode))
    app.state.rollout_gate = RejectedRolloutGate()

    with pytest.raises(
        RuntimeError,
        match="rollout authorization rejected: authorization_missing",
    ):
        with TestClient(app):
            pass



class NeverDatabase:
    def transaction(self):
        raise AssertionError("record-only authorization must not query PostgreSQL")


@pytest.mark.asyncio
async def test_record_only_gate_does_not_require_a_receipt(tmp_path) -> None:
    gate = RolloutAuthorizationGate(
        NeverDatabase(), settings_for(tmp_path, "record_only")
    )

    status = await gate.status(force=True)

    assert status.authorized
    assert status.code == "record_only"


@pytest.mark.asyncio
async def test_authorization_rejects_policy_version_drift_before_database(
    tmp_path,
) -> None:
    service = RolloutAuthorizationService(
        NeverDatabase(), settings_for(tmp_path, "record_only")
    )
    with pytest.raises(ConflictError, match="policy version changed"):
        await service.authorize_shadow(
            RolloutAuthorizationRequest(
                operator_request_id=uuid.uuid4(),
                expected_rule_version=RECORD_ONLY_TO_SHADOW.rule_version,
                expected_policy_version="reviewed-but-now-stale-v1",
                expected_policy_digest=POLICY_DIGEST,
                expected_input_digest="b" * 64,
            )
        )


async def _seed_ready_evidence(database: Database) -> None:
    started_at = datetime.now(UTC) - timedelta(days=8)
    stream_id = uuid.uuid4()
    epoch = uuid.uuid4()
    async with database.transaction() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE operations.rollout_authorizations, "
                "ingest.envelopes, ingest.source_streams CASCADE"
            )
        )
        await connection.execute(
            insert(schema.source_streams).values(
                stream_id=stream_id,
                edge_instance_id="phase2-test-edge",
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
                    "metadata": {"raw_payload_status": "suppressed_unbound_location"},
                }
                for sequence in range(1, 501)
            ],
        )


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_old_nonqualifying_header_cannot_age_the_gate(tmp_path) -> None:
    settings = settings_for(tmp_path, "record_only")
    database = Database(settings.async_database_url())
    try:
        await _seed_ready_evidence(database)
        recent = datetime.now(UTC) - timedelta(minutes=1)
        async with database.transaction() as connection:
            await connection.execute(
                update(schema.envelopes).values(ingested_at=recent)
            )
            stream_id = (
                await connection.execute(select(schema.source_streams.c.stream_id))
            ).scalar_one()
            await connection.execute(
                insert(schema.envelopes).values(
                    envelope_id=uuid.uuid4(),
                    stream_id=stream_id,
                    sequence=501,
                    event_type="conversation_turn",
                    payload_bytes=0,
                    source_observed_at=recent - timedelta(days=30),
                    edge_received_at=recent - timedelta(days=30),
                    ingested_at=recent - timedelta(days=30),
                    payload_sha256="e" * 64,
                    root_observation_id=uuid.uuid4(),
                    evidence_family_id=uuid.uuid4(),
                    dependency_domain="suppressed.identity_linked",
                    freshness="stale",
                    coverage="continuous",
                    clock_state="synchronized",
                    ha_context={},
                    metadata={"raw_payload_status": "suppressed_unbound_user"},
                )
            )

        readiness = await Phase2GateInspector(
            database,
            rollout_mode="record_only",
            policy_version="home-agent-mvp-v1",
            policy_digest=POLICY_DIGEST,
        ).inspect()

        assert readiness.relevant_envelopes == 500
        assert readiness.started_at == recent
        assert not readiness.time_requirement_met
        assert not readiness.ready_to_advance
    finally:
        await database.close()


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_authorization_is_idempotent_and_conflicts_on_drift(tmp_path) -> None:
    settings = settings_for(tmp_path, "record_only")
    database = Database(settings.async_database_url())
    try:
        await _seed_ready_evidence(database)
        readiness = await Phase2GateInspector(
            database,
            rollout_mode="record_only",
            policy_version="home-agent-mvp-v1",
            policy_digest=POLICY_DIGEST,
        ).inspect()
        assert readiness.ready_to_advance
        service = RolloutAuthorizationService(database, settings)
        request = RolloutAuthorizationRequest(
            operator_request_id=uuid.uuid4(),
            expected_rule_version=RECORD_ONLY_TO_SHADOW.rule_version,
            expected_policy_version="home-agent-mvp-v1",
            expected_policy_digest=POLICY_DIGEST,
            expected_input_digest=readiness.input_digest,
        )

        first = await service.authorize_shadow(request)
        duplicate = await service.authorize_shadow(request)

        assert duplicate.authorization_id == first.authorization_id
        with pytest.raises(ConflictError):
            await RolloutAuthorizationService(
                database,
                settings.model_copy(update={"policy_digest": "c" * 64}),
            ).authorize_shadow(request)
    finally:
        await database.close()


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_concurrent_authorizations_create_one_receipt(tmp_path) -> None:
    settings = settings_for(tmp_path, "record_only")
    database = Database(settings.async_database_url())
    try:
        await _seed_ready_evidence(database)
        readiness = await Phase2GateInspector(
            database,
            rollout_mode="record_only",
            policy_version="home-agent-mvp-v1",
            policy_digest=POLICY_DIGEST,
        ).inspect()
        service = RolloutAuthorizationService(database, settings)
        requests = [
            RolloutAuthorizationRequest(
                operator_request_id=uuid.uuid4(),
                expected_rule_version=RECORD_ONLY_TO_SHADOW.rule_version,
                expected_policy_version="home-agent-mvp-v1",
                expected_policy_digest=POLICY_DIGEST,
                expected_input_digest=readiness.input_digest,
            )
            for _ in range(2)
        ]

        results = await asyncio.gather(
            *(service.authorize_shadow(value) for value in requests),
            return_exceptions=True,
        )

        assert (
            sum(isinstance(value, RolloutAuthorizationView) for value in results) == 1
        )
        assert sum(isinstance(value, ConflictError) for value in results) == 1
        async with database.transaction() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM operations.rollout_authorizations")
                )
            ).scalar_one()
        assert count == 1
    finally:
        await database.close()


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_shadow_matches_evidence_and_canary_requires_future_receipt(
    tmp_path,
) -> None:
    record_settings = settings_for(tmp_path, "record_only")
    database = Database(record_settings.async_database_url())
    try:
        await _seed_ready_evidence(database)
        readiness = await Phase2GateInspector(
            database,
            rollout_mode="record_only",
            policy_version="home-agent-mvp-v1",
            policy_digest=POLICY_DIGEST,
        ).inspect()
        await RolloutAuthorizationService(database, record_settings).authorize_shadow(
            RolloutAuthorizationRequest(
                operator_request_id=uuid.uuid4(),
                expected_rule_version=RECORD_ONLY_TO_SHADOW.rule_version,
                expected_policy_version="home-agent-mvp-v1",
                expected_policy_digest=POLICY_DIGEST,
                expected_input_digest=readiness.input_digest,
            )
        )

        shadow = await RolloutAuthorizationGate(
            database,
            record_settings.model_copy(update={"rollout_mode": "shadow"}),
        ).status(force=True)
        canary = await RolloutAuthorizationGate(
            database,
            record_settings.model_copy(update={"rollout_mode": "canary"}),
        ).status(force=True)

        assert shadow.authorized
        assert shadow.code == "authorized"
        assert not canary.authorized
        assert canary.code == "authorization_missing"

        # The online API writer cannot manufacture the reserved future
        # transition directly: opening canary requires a reviewed migration.
        with pytest.raises(IntegrityError):
            async with database.transaction() as connection:
                await connection.execute(
                    insert(schema.rollout_authorizations).values(
                        authorization_id=uuid.uuid4(),
                        operator_request_id=uuid.uuid4(),
                        from_mode="shadow",
                        to_mode="canary",
                        rule_version="shadow-to-canary-forged",
                        policy_version="home-agent-mvp-v1",
                        policy_digest=POLICY_DIGEST,
                        input_digest="d" * 64,
                        readiness_evaluated_at=datetime.now(UTC),
                        authorized_at=datetime.now(UTC),
                    )
                )

        async with database.transaction() as connection:
            await connection.execute(
                delete(schema.envelopes).where(schema.envelopes.c.sequence == 1)
            )
        stale = await RolloutAuthorizationGate(
            database,
            record_settings.model_copy(update={"rollout_mode": "shadow"}),
        ).status(force=True)
        assert not stale.authorized
        assert stale.code == "authorization_evidence_stale"
    finally:
        await database.close()

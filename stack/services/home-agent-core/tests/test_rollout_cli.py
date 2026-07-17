from __future__ import annotations

import base64
import io
import json
import sys
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from app import cli
from app.config import Settings
from app.models import RolloutAuthorizationView
from app.restore import RestoreGateStatus


POLICY_DIGEST = "a" * 64


def settings_for(tmp_path) -> Settings:
    return Settings(
        database_url=SecretStr(
            "postgresql+psycopg://home_agent_rollout:"
            + "f" * 64
            + "@postgres:5432/home_agent"
        ),
        storage_monitor_path=tmp_path,
        erasure_ledger_head_path=tmp_path / "ledger.head.json",
        policy_digest=POLICY_DIGEST,
        role="rollout",
        rollout_mode="record_only",
    )


def request_payload() -> dict[str, str]:
    return {
        "operator_request_id": str(uuid.uuid4()),
        "expected_rule_version": "record-only-envelope-worker-gate-v3",
        "expected_policy_version": "home-agent-mvp-v1",
        "expected_policy_digest": POLICY_DIGEST,
        "expected_input_digest": "b" * 64,
    }


def set_stdin(monkeypatch, raw: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))


def test_rollout_request_stdin_is_strict_bounded_and_duplicate_rejecting(
    monkeypatch,
) -> None:
    payload = request_payload()
    set_stdin(monkeypatch, json.dumps(payload).encode())
    value = cli.rollout_request_from_stdin()
    assert str(value.operator_request_id) == payload["operator_request_id"]

    duplicate = (
        '{"operator_request_id":"%s","operator_request_id":"%s"}'
        % (uuid.uuid4(), uuid.uuid4())
    ).encode()
    set_stdin(monkeypatch, duplicate)
    with pytest.raises(SystemExit, match="request is invalid"):
        cli.rollout_request_from_stdin()

    set_stdin(monkeypatch, b"x" * (cli.MAX_ROLLOUT_REQUEST_BYTES + 1))
    with pytest.raises(SystemExit, match="missing or oversized"):
        cli.rollout_request_from_stdin()


def test_rollout_role_rejects_every_unrelated_secret(tmp_path) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    with pytest.raises(ValueError, match="only its database credential"):
        settings_for(tmp_path).model_copy(
            update={"knowledge_encryption_key": SecretStr(key)}
        ).validate_secret_separation()


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://home_agent_api:"
        + "f" * 64
        + "@postgres:5432/home_agent",
        "postgresql+psycopg://home_agent_rollout:"
        + "f" * 64
        + "@db.example:5432/home_agent",
        "postgresql+psycopg://home_agent_rollout:short@postgres:5432/home_agent",
        "postgresql+psycopg://home_agent_rollout:"
        + "f" * 64
        + "@postgres:5432/home_agent?sslmode=disable",
    ],
)
def test_rollout_role_rejects_wrong_database_authority(
    tmp_path, database_url: str
) -> None:
    with pytest.raises(ValidationError, match="isolated rollout role"):
        Settings(
            database_url=SecretStr(database_url),
            storage_monitor_path=tmp_path,
            policy_digest=POLICY_DIGEST,
            role="rollout",
        )


@pytest.mark.asyncio
async def test_one_shot_checks_migration_restore_and_disk_before_receipt(
    tmp_path, monkeypatch, capsys
) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(UTC)
    request = cli.RolloutAuthorizationRequest.model_validate(request_payload())
    events: list[str] = []

    class FakeDatabase:
        def __init__(self, _url):
            events.append("database")

        async def migration_revision(self):
            events.append("migration")
            return "0006a_worker_lease_arbitration"

        async def close(self):
            events.append("close")

    class FakeRestoreGate:
        def __init__(self, *_args, **_kwargs):
            pass

        async def status(self, *, force=False):
            assert force
            events.append("restore")
            return RestoreGateStatus(True, "current", 0, 0)

    class FakeDisk:
        optional_work_allowed = True

    class FakeService:
        def __init__(self, _database, _settings):
            pass

        async def authorize_shadow(self, value):
            events.append("authorize")
            return RolloutAuthorizationView(
                authorization_id=uuid.uuid4(),
                operator_request_id=value.operator_request_id,
                from_mode="record_only",
                to_mode="shadow",
                rule_version=value.expected_rule_version,
                policy_version=value.expected_policy_version,
                policy_digest=value.expected_policy_digest,
                input_digest=value.expected_input_digest,
                worker_kernel_version="worker-maintenance-cycle-v1",
                worker_success_sequence=1,
                worker_proof_digest="c" * 64,
                readiness_evaluated_at=now,
                authorized_at=now,
            )

    monkeypatch.setattr(cli, "Database", FakeDatabase)
    monkeypatch.setattr(cli, "RestoreQuarantineGate", FakeRestoreGate)
    monkeypatch.setattr(cli, "inspect_disk_budget", lambda _path: FakeDisk())
    monkeypatch.setattr(cli, "RolloutAuthorizationService", FakeService)

    assert await cli.authorize_shadow(settings, request) == 0
    assert events == ["database", "migration", "restore", "authorize", "close"]
    output = json.loads(capsys.readouterr().out)
    assert output["from_mode"] == "record_only"
    assert output["to_mode"] == "shadow"
    assert set(output) == {
        "authorization_id",
        "authorized_at",
        "contract",
        "from_mode",
        "input_digest",
        "operator_request_id",
        "policy_digest",
        "policy_version",
        "readiness_evaluated_at",
        "rule_version",
        "to_mode",
        "worker_kernel_version",
        "worker_proof_digest",
        "worker_success_sequence",
    }

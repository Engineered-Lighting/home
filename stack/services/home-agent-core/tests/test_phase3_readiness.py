from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api import phase3_readiness_diagnostic
from app.config import Settings
from app.main import create_app
from app.models import PHASE3_FIXED_READINESS_BLOCKERS, Phase3ReadinessView
from app.restore import RestoreGateStatus
from app.rollout import RolloutAuthorizationStatus


OPERATOR_TOKEN = "operator-token-with-at-least-32-chars"
BOOTSTRAP_TOKEN = "bootstrap-token-with-at-least-32-chars"


class StubRolloutGate:
    def __init__(self, status: RolloutAuthorizationStatus) -> None:
        self.result = status
        self.calls = 0

    async def status(self, *, force: bool = False) -> RolloutAuthorizationStatus:
        assert force is True
        self.calls += 1
        return self.result


class CurrentRestoreGate:
    async def status(self, *, force: bool = False) -> RestoreGateStatus:
        return RestoreGateStatus(True, "current", 0, 0)


def _settings(tmp_path, *, rollout_mode: str = "shadow") -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    operator_url = (
        "postgresql+psycopg://home_agent_binding_operator:"
        f"{'d' * 64}@postgres:5432/home_agent"
    )
    return Settings(
        database_url=SecretStr(
            "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
        ),
        operator_database_url=SecretStr(operator_url),
        runtime_spool_path=tmp_path / "runtime.sqlite",
        storage_monitor_path=tmp_path,
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        operator_token=SecretStr(OPERATOR_TOKEN),
        bootstrap_token=SecretStr(BOOTSTRAP_TOKEN),
        policy_digest="a" * 64,
        role="api",
        rollout_mode=rollout_mode,
    )


def _operator_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPERATOR_TOKEN}",
        "X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN,
    }


def test_phase3_diagnostic_is_fixed_non_authoritative_and_content_free() -> None:
    result = phase3_readiness_diagnostic(
        rollout_mode="shadow",
        authorization_authorized=True,
        authorization_code="authorized",
    )

    assert result.model_dump(mode="json") == {
        "contract": "phase3-readiness-diagnostic-v0",
        "schema_revision": "0006_worker_maintenance_health",
        "rollout_mode": "shadow",
        "shadow_predecessor_status": "authorized",
        "semantic_people_migration_manifest_status": "unproven",
        "semantic_people_migration_completion_status": "unproven",
        "privacy_cutover_status": "unproven",
        "legacy_semantic_write_freeze_status": "unproven",
        "subject_binding_status": "not_evaluated",
        "parent_confirmation_protocol_status": "capability_disabled",
        "evidence_scope": "local_database_only",
        "counts_exposed": False,
        "identity_or_fact_state_evaluated": False,
        "authoritative": False,
        "enables_writes": False,
        "ready_to_advance": False,
        "blockers": list(PHASE3_FIXED_READINESS_BLOCKERS),
    }


@pytest.mark.parametrize(
    ("mode", "authorized", "code", "expected"),
    [
        ("record_only", True, "record_only", "mode_not_shadow"),
        ("shadow", True, "record_only", "invalid"),
        ("shadow", False, "authorization_missing", "missing"),
        ("shadow", False, "authorization_unavailable", "unavailable"),
        ("shadow", False, "authorization_policy_mismatch", "invalid"),
    ],
)
def test_phase3_shadow_predecessor_requires_exact_authorized_status(
    mode: str,
    authorized: bool,
    code: str,
    expected: str,
) -> None:
    result = phase3_readiness_diagnostic(
        rollout_mode=mode,
        authorization_authorized=authorized,
        authorization_code=code,
    )

    assert result.shadow_predecessor_status == expected
    assert result.ready_to_advance is False
    assert "shadow_predecessor_not_authorized" in result.blockers
    if mode != "shadow":
        assert result.blockers[:2] == [
            "rollout_mode_not_shadow",
            "shadow_predecessor_not_authorized",
        ]


def test_phase3_model_cannot_claim_authority_or_accept_extra_content() -> None:
    with pytest.raises(ValidationError):
        Phase3ReadinessView(
            rollout_mode="shadow",
            shadow_predecessor_status="authorized",
            authoritative=True,
            blockers=list(PHASE3_FIXED_READINESS_BLOCKERS),
        )
    with pytest.raises(ValidationError):
        Phase3ReadinessView(
            rollout_mode="shadow",
            shadow_predecessor_status="authorized",
            blockers=list(PHASE3_FIXED_READINESS_BLOCKERS),
            person_id="forbidden",
        )


def test_phase3_endpoint_is_operator_bootstrap_only_and_rejects_input(tmp_path) -> None:
    application = create_app(_settings(tmp_path))
    gate = StubRolloutGate(RolloutAuthorizationStatus(True, "authorized"))
    application.state.rollout_gate = gate
    application.state.restore_gate = CurrentRestoreGate()
    path = "/v1/operator-rollout/phase3-readiness"

    with TestClient(application) as client:
        startup_gate_calls = gate.calls
        anonymous = client.get(path)
        service = client.get(
            path,
            headers={
                "Authorization": "Bearer service-token-with-at-least-32-chars",
                "X-Authenticated-HA-User": "forged-user",
                "X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN,
            },
        )
        no_bootstrap = client.get(
            path, headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"}
        )
        query = client.get(path, headers=_operator_headers(), params={"person": "x"})
        body = client.request(
            "GET", path, headers=_operator_headers(), content=b"{}"
        )
        accepted = client.get(path, headers=_operator_headers())

    assert anonymous.status_code == 401
    assert service.status_code == 401
    assert no_bootstrap.status_code == 401
    assert query.status_code == 422
    assert query.json()["error"]["message"] == (
        "Phase 3 readiness does not accept query parameters"
    )
    assert body.status_code == 422
    assert body.json()["error"]["message"] == (
        "Phase 3 readiness does not accept a body"
    )
    assert accepted.status_code == 200
    assert accepted.json()["authoritative"] is False
    assert accepted.json()["ready_to_advance"] is False
    assert accepted.json()["shadow_predecessor_status"] == "authorized"
    assert gate.calls == startup_gate_calls + 1
    assert all(
        forbidden not in accepted.text
        for forbidden in (
            "person_id",
            "principal_id",
            "ha_user_id",
            "timestamp",
            "evaluated_at",
            "digest",
        )
    )


def test_phase3_endpoint_rechecks_actual_schema_and_fails_closed(tmp_path) -> None:
    application = create_app(_settings(tmp_path))
    gate = StubRolloutGate(RolloutAuthorizationStatus(True, "authorized"))
    application.state.rollout_gate = gate
    application.state.restore_gate = CurrentRestoreGate()

    async def drifted_revision() -> str:
        return "0005_principal_binding_proposals"

    with TestClient(application) as client:
        startup_gate_calls = gate.calls
        application.state.database.migration_revision = drifted_revision
        response = client.get(
            "/v1/operator-rollout/phase3-readiness",
            headers=_operator_headers(),
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "capability_disabled",
            "message": "Phase 3 revision-0006 diagnostic is unavailable",
            "details": {},
        }
    }
    assert gate.calls == startup_gate_calls

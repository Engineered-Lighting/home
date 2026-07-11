from __future__ import annotations

import base64
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.models import PrincipalBindingCreate
from app.restore import RestoreGateStatus


class CurrentRestoreGate:
    async def status(self, *, force: bool = False) -> RestoreGateStatus:
        return RestoreGateStatus(True, "current", 0, 0)


def settings_for(tmp_path) -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    return Settings(
        database_url=SecretStr("postgresql+psycopg://unused:unused@127.0.0.1:1/unused"),
        runtime_spool_path=tmp_path / "runtime.sqlite",
        storage_monitor_path=tmp_path,
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        operator_token=SecretStr("operator-token-with-at-least-32-chars"),
        bootstrap_token=SecretStr("bootstrap-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode="shadow",
    )


def test_bff_service_credential_cannot_bootstrap_people(tmp_path) -> None:
    app = create_app(
        settings_for(tmp_path).model_copy(update={"rollout_mode": "record_only"})
    )
    app.state.restore_gate = CurrentRestoreGate()
    headers = {
        "Authorization": "Bearer service-token-with-at-least-32-chars",
        "X-Authenticated-HA-User": "attacker-ha-user",
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/people", headers=headers, json={"display_name": "Victim"}
        )
        wrong = client.post(
            "/v1/people",
            headers={**headers, "X-Home-Agent-Bootstrap": "wrong-token"},
            json={"display_name": "Victim"},
        )
        valid_bootstrap_wrong_audience = client.post(
            "/v1/people",
            headers={
                **headers,
                "X-Home-Agent-Bootstrap": "bootstrap-token-with-at-least-32-chars",
            },
            json={"display_name": "Victim"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert wrong.status_code == 401
    assert valid_bootstrap_wrong_audience.status_code == 401


def test_principal_binding_requires_confirmation_artifact() -> None:
    with pytest.raises(ValidationError):
        PrincipalBindingCreate(
            ha_user_id="ha-user",
            person_id=uuid.uuid4(),
            display_label="Marcelo",
        )


def test_private_initiatives_require_native_channel_attestation(tmp_path) -> None:
    app = create_app(settings_for(tmp_path).model_copy(update={"rollout_mode": "canary"}))
    app.state.restore_gate = CurrentRestoreGate()
    with TestClient(app) as client:
        response = client.get(
            "/v1/initiatives",
            headers={
                "Authorization": "Bearer service-token-with-at-least-32-chars",
                "X-Authenticated-HA-User": "marcelo-ha-user",
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "private native channel is required"


def test_fixed_operator_capability_contract_requires_bootstrap(tmp_path) -> None:
    app = create_app(settings_for(tmp_path))
    app.state.restore_gate = CurrentRestoreGate()
    operator_headers = {
        "Authorization": "Bearer operator-token-with-at-least-32-chars",
    }
    with TestClient(app) as client:
        denied = client.get("/v1/operator-capabilities", headers=operator_headers)
        accepted = client.get(
            "/v1/operator-capabilities",
            headers={
                **operator_headers,
                "X-Home-Agent-Bootstrap": "bootstrap-token-with-at-least-32-chars",
            },
        )
        openapi = client.get("/openapi.json")

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {
        "contract": "legacy-identity-migration-v1",
        "audience": "operator-bootstrap",
        "person_import": {
            "method": "POST",
            "path": "/v1/people",
            "schema": "PersonCreate.v2",
            "source_digest_field": "legacy_source_sha256",
            "idempotency": "exact-projection-v1",
        },
        "role_import": {
            "method": "POST",
            "path": "/v1/people/legacy-role-labels",
            "schema": "LegacyRoleImport.v1",
            "source_digest_field": "source_snapshot_sha256",
            "idempotency": "exact-projection-v1",
        },
        "person_verify": {
            "method": "POST",
            "path": "/v1/people/verify-reviewed",
            "schema": "ReviewedPersonVerify.v1",
        },
        "alias_import": {
            "method": "POST",
            "path": "/v1/people/{person_id}/aliases",
            "schema": "ReviewedAliasImport.v1",
            "source_digest_field": "source_snapshot_sha256",
            "idempotency": "exact-projection-v1",
        },
        "recognition_binding_import": {
            "method": "POST",
            "path": "/v1/people/{person_id}/recognition-bindings",
            "schema": "ReviewedRecognitionBindingImport.v1",
            "source_digest_field": "source_snapshot_sha256",
            "idempotency": "exact-projection-v1",
        },
        "privacy_directive_import": {
            "method": "POST",
            "path": "/v1/people/{person_id}/privacy-directives",
            "schema": "ReviewedPrivacyDirectiveImport.v1",
            "source_digest_field": "source_snapshot_sha256",
            "idempotency": "exact-projection-v1",
        },
        "person_status_import": {
            "method": "POST",
            "path": "/v1/people/{person_id}/status-import",
            "schema": "ReviewedPersonStatusImport.v1",
            "source_digest_field": "source_snapshot_sha256",
            "idempotency": "exact-projection-v1",
        },
        "relationship_candidate_import": {
            "method": "POST",
            "path": "/v1/people/legacy-relationship-candidates",
            "schema": "LegacyRelationshipCandidateImport.v1",
            "source_digest_field": "source_snapshot_sha256",
            "idempotency": "exact-projection-v1",
        },
    }
    assert openapi.status_code == 404


def test_rollout_policy_defaults_record_only_and_has_no_mutation_route(tmp_path) -> None:
    app = create_app(
        settings_for(tmp_path).model_copy(update={"rollout_mode": "record_only"})
    )
    app.state.restore_gate = CurrentRestoreGate()
    headers = {
        "Authorization": "Bearer operator-token-with-at-least-32-chars",
        "X-Home-Agent-Bootstrap": "bootstrap-token-with-at-least-32-chars",
    }
    with TestClient(app) as client:
        status_response = client.get("/v1/operator-rollout", headers=headers)
        mutation_attempt = client.post(
            "/v1/operator-rollout", headers=headers, json={"mode": "canary"}
        )

    assert status_response.status_code == 200
    assert status_response.json() == {
        "mode": "record_only",
        "source": "deployment_policy",
        "semantic_people_writes": False,
        "persistent_memory_writes": False,
        "ingest_projection": True,
    }
    assert mutation_attempt.status_code == 405


def test_credentials_are_role_scoped_and_bootstrap_is_optional(tmp_path) -> None:
    spool_key = base64.urlsafe_b64encode(b"s" * 32).decode().rstrip("=")
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    ledger_key = base64.urlsafe_b64encode(b"e" * 32).decode().rstrip("=")
    common = {
        "database_url": SecretStr(
            "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
        ),
        "runtime_spool_path": tmp_path / "worker.sqlite",
        "runtime_spool_key": SecretStr(spool_key),
        "knowledge_encryption_key": SecretStr(knowledge_key),
        "erasure_ledger_key": SecretStr(ledger_key),
        "policy_digest": "a" * 64,
    }
    worker_common = {
        key: value for key, value in common.items() if key != "knowledge_encryption_key"
    }
    worker = Settings(**worker_common, role="worker")
    api_common = {
        key: value
        for key, value in common.items()
        if key not in {"runtime_spool_key", "erasure_ledger_key"}
    }
    api = Settings(
        **api_common,
        role="api",
        service_token=SecretStr("service-token-with-at-least-32-chars"),
    )

    assert (
        worker.edge_token is None
        and worker.service_token is None
        and worker.knowledge_encryption_key is None
    )
    assert api.bootstrap_token is None and api.runtime_spool_key is None
    with pytest.raises(ValidationError, match="service credential"):
        Settings(**api_common, role="api")

    with pytest.raises(ValidationError, match="every configured secret"):
        Settings(
            **common,
            role="all",
            edge_token=SecretStr("edge-token-with-at-least-32-characters"),
            service_token=common["erasure_ledger_key"],
            bootstrap_token=SecretStr("bootstrap-token-with-at-least-32-chars"),
        )

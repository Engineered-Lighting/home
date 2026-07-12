from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.auth import ATTESTED_NATIVE_CHANNEL, require_native_service_identity
from app.errors import AuthenticationError
from app.main import create_app
from app.restore import RestoreGateStatus
from app.rollout import RolloutAuthorizationStatus


class CurrentRestoreGate:
    async def status(self, *, force: bool = False) -> RestoreGateStatus:
        return RestoreGateStatus(True, "current", 0, 0)


class CurrentRolloutGate:
    async def status(self, *, force: bool = False) -> RolloutAuthorizationStatus:
        return RolloutAuthorizationStatus(True, "authorized")


def settings_for(tmp_path) -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    operator_url = (
        "postgresql+psycopg://home_agent_binding_operator:"
        f"{'d' * 64}@postgres:5432/home_agent"
    )
    return Settings(
        database_url=SecretStr("postgresql+psycopg://unused:unused@127.0.0.1:1/unused"),
        operator_database_url=SecretStr(operator_url),
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
    app.state.rollout_gate = CurrentRolloutGate()
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


def test_direct_principal_binding_route_and_store_method_are_absent(tmp_path) -> None:
    app = create_app(settings_for(tmp_path))
    assert not hasattr(app.state.store, "bind_principal")
    assert "/v1/principal-bindings" not in {
        getattr(route, "path", None) for route in app.routes
    }


def test_private_initiatives_require_native_channel_attestation(tmp_path) -> None:
    app = create_app(settings_for(tmp_path).model_copy(update={"rollout_mode": "canary"}))
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
    with TestClient(app) as client:
        response = client.get(
            "/v1/initiatives",
            headers={
                "Authorization": "Bearer service-token-with-at-least-32-chars",
                "X-Authenticated-HA-User": "marcelo-ha-user",
            },
        )
        contained = client.get(
            "/v1/initiatives",
            headers={
                "Authorization": "Bearer service-token-with-at-least-32-chars",
                "X-Authenticated-HA-User": "marcelo-ha-user",
                "X-Home-Agent-Channel": ATTESTED_NATIVE_CHANNEL,
                "X-Home-Agent-Installation": (
                    "018f6f42-3a8b-4c11-8123-123456789abc"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "private native channel is required"
    assert contained.status_code == 409
    assert contained.json()["error"]["code"] == "capability_disabled"


def test_core_accepts_only_versioned_attested_native_channel() -> None:
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    settings=SimpleNamespace(
                        service_token=SecretStr(
                            "service-token-with-at-least-32-chars"
                        )
                    )
                )
            ),
        }
    )

    with pytest.raises(AuthenticationError, match="private native channel"):
        asyncio.run(
            require_native_service_identity(
                request,
                authorization="Bearer service-token-with-at-least-32-chars",
                x_authenticated_ha_user="marcelo-ha-user",
                x_home_agent_channel="private_tauri",
                x_home_agent_installation=(
                    "018f6f42-3a8b-4c11-8123-123456789abc"
                ),
            )
        )

    with pytest.raises(AuthenticationError, match="attested native installation"):
        asyncio.run(
            require_native_service_identity(
                request,
                authorization="Bearer service-token-with-at-least-32-chars",
                x_authenticated_ha_user="marcelo-ha-user",
                x_home_agent_channel=ATTESTED_NATIVE_CHANNEL,
            )
        )

    identity = asyncio.run(
        require_native_service_identity(
            request,
            authorization="Bearer service-token-with-at-least-32-chars",
            x_authenticated_ha_user="marcelo-ha-user",
            x_home_agent_channel=ATTESTED_NATIVE_CHANNEL,
            x_home_agent_installation=(
                "018f6f42-3a8b-4c11-8123-123456789abc"
            ),
        )
    )
    assert identity.ha_user_id == "marcelo-ha-user"


def test_fixed_operator_capability_contract_requires_bootstrap(tmp_path) -> None:
    app = create_app(settings_for(tmp_path))
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
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


def test_binding_operator_routes_fail_closed_without_database_role(tmp_path) -> None:
    app = create_app(
        settings_for(tmp_path).model_copy(
            update={"rollout_mode": "shadow", "operator_database_url": None}
        )
    )
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
    with TestClient(app) as client:
        response = client.get(
            "/v1/operator/principal-binding-requests",
            headers={
                "Authorization": "Bearer operator-token-with-at-least-32-chars",
                "X-Home-Agent-Bootstrap": (
                    "bootstrap-token-with-at-least-32-chars"
                ),
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "capability_disabled"


def test_stale_binding_operator_database_blocks_api_startup(tmp_path) -> None:
    operator_url = (
        "postgresql+psycopg://home_agent_binding_operator:"
        f"{'f' * 64}@postgres:5432/home_agent"
    )
    settings = settings_for(tmp_path).model_copy(
        update={"operator_database_url": SecretStr(operator_url)}
    )
    app = create_app(settings)
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()

    async def stale_revision() -> str:
        return "0004_rollout_authorizations"

    app.state.operator_database.migration_revision = stale_revision
    with pytest.raises(RuntimeError, match="operator database migration mismatch"):
        with TestClient(app):
            pass


def test_api_accepts_only_isolated_binding_operator_database_role(tmp_path) -> None:
    values = settings_for(tmp_path).model_dump()
    values["operator_database_url"] = SecretStr(
        "postgresql+psycopg://home_agent_binding_operator:"
        f"{'e' * 64}@postgres:5432/home_agent"
    )
    settings = Settings(**values)
    assert "home_agent_binding_operator" in settings.async_operator_database_url()

    values["operator_database_url"] = SecretStr(
        "postgresql+psycopg://home_agent_api:"
        f"{'e' * 64}@postgres:5432/home_agent"
    )
    with pytest.raises(ValidationError, match="isolated binding operator role"):
        Settings(**values)

    values["operator_database_url"] = None
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(**values)


def test_rollout_policy_defaults_record_only_and_has_no_mutation_route(tmp_path) -> None:
    app = create_app(
        settings_for(tmp_path).model_copy(update={"rollout_mode": "record_only"})
    )
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
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


def test_phase2_readiness_is_operator_only_and_requires_paired_ids(tmp_path) -> None:
    app = create_app(
        settings_for(tmp_path).model_copy(update={"rollout_mode": "record_only"})
    )
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
    operator_headers = {
        "Authorization": "Bearer operator-token-with-at-least-32-chars",
    }
    with TestClient(app) as client:
        denied = client.get(
            "/v1/operator-rollout/phase2-readiness", headers=operator_headers
        )
        unpaired = client.get(
            "/v1/operator-rollout/phase2-readiness",
            headers={
                **operator_headers,
                "X-Home-Agent-Bootstrap": "bootstrap-token-with-at-least-32-chars",
            },
            params={"controlled_journey_id": str(uuid.uuid4())},
        )

    assert denied.status_code == 401
    assert unpaired.status_code == 422
    assert unpaired.json()["error"]["message"] == (
        "controlled principal and journey IDs must be paired"
    )


def test_shadow_authorization_has_no_online_api_route(tmp_path) -> None:
    app = create_app(
        settings_for(tmp_path).model_copy(update={"rollout_mode": "record_only"})
    )
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
    with TestClient(app) as client:
        response = client.post(
            "/v1/operator-rollout/authorizations/shadow",
            headers={
                "Authorization": "Bearer operator-token-with-at-least-32-chars",
                "X-Home-Agent-Bootstrap": (
                    "bootstrap-token-with-at-least-32-chars"
                ),
            },
            json={},
        )

    assert response.status_code == 404


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

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime

from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from app import main as main_module
from app.api import semantic_router
from app.auth import (
    require_bootstrap,
    require_native_service_identity,
    require_operator_bearer,
    require_service_identity,
)
from app.config import Settings
from app.maintenance import WorkerMaintenanceStatus
from app.models import PreferenceUpdate
from app.resources import DiskBudget
from app.restore import OutboxHealth, RestoreGateStatus
from app.rollout import RolloutAuthorizationStatus


NOW = datetime(2026, 7, 12, 23, 0, tzinfo=UTC)
SERVICE_TOKEN = "service-token-with-at-least-32-chars"
OPERATOR_TOKEN = "operator-token-with-at-least-32-chars"
BOOTSTRAP_TOKEN = "bootstrap-token-with-at-least-32-chars"
EDGE_TOKEN = "edge-token-with-at-least-32-characters"
HA_USER = "authenticated-ha-user"


class CurrentRestoreGate:
    async def status(self, *, force: bool = False) -> RestoreGateStatus:
        del force
        return RestoreGateStatus(True, "current", 0, 0)


class CurrentRolloutGate:
    async def status(self, *, force: bool = False) -> RolloutAuthorizationStatus:
        del force
        return RolloutAuthorizationStatus(True, "authorized")


class Maintenance:
    def __init__(self, code: str = "missing") -> None:
        self.status = WorkerMaintenanceStatus(code)  # type: ignore[arg-type]
        self.calls = []

    async def inspect(self, **kwargs) -> WorkerMaintenanceStatus:
        self.calls.append(kwargs)
        return self.status


def _key(byte: bytes) -> str:
    return base64.urlsafe_b64encode(byte * 32).decode().rstrip("=")


def _api_settings(tmp_path, *, rollout_mode: str, bootstrap: bool = False) -> Settings:
    values = {
        "database_url": SecretStr(
            "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
        ),
        "runtime_spool_path": tmp_path / "api-runtime.sqlite",
        "storage_monitor_path": tmp_path,
        "knowledge_encryption_key": SecretStr(_key(b"k")),
        "service_token": SecretStr(SERVICE_TOKEN),
        "policy_digest": "a" * 64,
        "role": "api",
        "rollout_mode": rollout_mode,
    }
    if bootstrap:
        values.update(
            {
                "operator_database_url": SecretStr(
                    "postgresql+psycopg://home_agent_binding_operator:"
                    + "d" * 64
                    + "@postgres:5432/home_agent"
                ),
                "operator_token": SecretStr(OPERATOR_TOKEN),
                "bootstrap_token": SecretStr(BOOTSTRAP_TOKEN),
            }
        )
    return Settings(**values)


def _ingest_settings(tmp_path) -> Settings:
    return Settings(
        database_url=SecretStr(
            "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
        ),
        runtime_spool_path=tmp_path / "ingest-runtime.sqlite",
        storage_monitor_path=tmp_path,
        runtime_spool_key=SecretStr(_key(b"r")),
        knowledge_encryption_key=SecretStr(_key(b"k")),
        edge_token=SecretStr(EDGE_TOKEN),
        policy_digest="a" * 64,
        role="ingest",
        rollout_mode="shadow",
    )


def _configure_db_less_app(app, monkeypatch, *, maintenance: Maintenance) -> None:
    async def current_time():
        return NOW

    async def ping():
        return True

    async def revision():
        return app.state.settings.readiness_migration

    async def current_outbox(_database):
        return OutboxHealth(True, "current", 0, 0, 0)

    async def current_resources(*_args, **_kwargs):
        return {"status": "ok", "ready": True}

    app.state.database.current_time = current_time
    app.state.database.ping = ping
    app.state.database.migration_revision = revision
    if app.state.operator_database is not None:
        app.state.operator_database.ping = ping
        app.state.operator_database.migration_revision = revision
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()
    app.state.maintenance_inspector = maintenance
    monkeypatch.setattr(main_module, "outbox_health", current_outbox)
    monkeypatch.setattr(main_module, "resource_budget_snapshot", current_resources)
    monkeypatch.setattr(
        main_module,
        "inspect_disk_budget",
        lambda _path: DiskBudget("normal", 90.0, 900, 1000),
    )


def _service_headers(**extra: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Authenticated-HA-User": HA_USER,
        **extra,
    }


def _replace_post_route(app, path: str, endpoint, dependencies) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", None) or set())
        )
    ]
    app.add_api_route(
        path,
        endpoint,
        methods=["POST"],
        dependencies=[Depends(dependency) for dependency in dependencies],
    )


def test_record_only_health_is_categorical_but_missing_worker_does_not_block_ready(
    tmp_path, monkeypatch
) -> None:
    app = main_module.create_app(
        _api_settings(tmp_path, rollout_mode="record_only")
    )
    maintenance = Maintenance("missing")
    _configure_db_less_app(app, monkeypatch, maintenance=maintenance)

    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["worker_maintenance"] == {"status": "missing"}
    public_worker = json.dumps(health.json()["worker_maintenance"])
    for forbidden in (
        "worker_instance_id",
        "success_sequence",
        "heartbeat_at",
        "maintenance_succeeded_at",
        "2026-",
    ):
        assert forbidden not in public_worker
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["worker_maintenance"] == {"status": "missing"}


def test_shadow_trusted_mutation_is_gated_but_partial_credentials_keep_401(
    tmp_path, monkeypatch
) -> None:
    app = main_module.create_app(
        _api_settings(tmp_path, rollout_mode="shadow", bootstrap=True)
    )
    maintenance = Maintenance("heartbeat_stale")
    _configure_db_less_app(app, monkeypatch, maintenance=maintenance)

    async def mutation_probe():
        return {"mutated": True}

    service_path = "/v1/relationships/parent-confirmations"
    _replace_post_route(
        app,
        service_path,
        mutation_probe,
        [require_service_identity],
    )
    with TestClient(app) as client:
        blocked = client.post(service_path, headers=_service_headers())
        wrong_bearer = client.post(
            service_path,
            headers={
                "Authorization": "Bearer wrong-service-token",
                "X-Authenticated-HA-User": HA_USER,
            },
        )
        missing_subject = client.post(
            service_path,
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
        wrong_audience = client.post(
            service_path,
            headers={
                "Authorization": f"Bearer {OPERATOR_TOKEN}",
                "X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN,
            },
        )

    assert blocked.status_code == 503
    assert blocked.json()["error"]["details"] == {"status": "heartbeat_stale"}
    assert wrong_bearer.status_code == 401
    assert missing_subject.status_code == 401
    assert wrong_audience.status_code == 401
    assert len(maintenance.calls) == 1


def test_bootstrap_and_native_channel_partial_credentials_preserve_401(
    tmp_path, monkeypatch
) -> None:
    app = main_module.create_app(
        _api_settings(tmp_path, rollout_mode="shadow", bootstrap=True)
    )
    maintenance = Maintenance("missing")
    _configure_db_less_app(app, monkeypatch, maintenance=maintenance)

    async def mutation_probe():
        return {"mutated": True}

    _replace_post_route(
        app,
        "/v1/source-entity-bindings",
        mutation_probe,
        [require_service_identity, require_bootstrap],
    )
    initiative_id = uuid.uuid4()
    initiative_path = f"/v1/initiatives/{initiative_id}/claim"
    _replace_post_route(
        app,
        initiative_path,
        mutation_probe,
        [require_native_service_identity],
    )
    _replace_post_route(
        app,
        "/v1/people",
        mutation_probe,
        [require_operator_bearer, require_bootstrap],
    )

    with TestClient(app) as client:
        bootstrap_missing = client.post(
            "/v1/source-entity-bindings", headers=_service_headers()
        )
        bootstrap_trusted = client.post(
            "/v1/source-entity-bindings",
            headers=_service_headers(**{"X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN}),
        )
        channel_missing = client.post(initiative_path, headers=_service_headers())
        channel_trusted = client.post(
            initiative_path,
            headers=_service_headers(**{"X-Home-Agent-Channel": "private_tauri"}),
        )
        operator_trusted = client.post(
            "/v1/people",
            headers={
                "Authorization": f"Bearer {OPERATOR_TOKEN}",
                "X-Home-Agent-Bootstrap": BOOTSTRAP_TOKEN,
            },
        )
        operator_wrong_audience = client.post(
            "/v1/people",
            headers=_service_headers(),
        )

    assert bootstrap_missing.status_code == 401
    assert channel_missing.status_code == 401
    assert bootstrap_trusted.status_code == 503
    assert channel_trusted.status_code == 503
    assert operator_trusted.status_code == 503
    assert operator_wrong_audience.status_code == 401
    assert len(maintenance.calls) == 3


def test_privacy_essential_mutation_bypasses_maintenance_gate(
    tmp_path, monkeypatch
) -> None:
    app = main_module.create_app(_api_settings(tmp_path, rollout_mode="shadow"))
    maintenance = Maintenance("maintenance_failed")
    _configure_db_less_app(app, monkeypatch, maintenance=maintenance)

    async def privacy_probe():
        return {"accepted": True}

    app.add_api_route(
        "/v1/erasure-requests/test-only/privacy-probe",
        privacy_probe,
        methods=["POST"],
        dependencies=[Depends(require_service_identity)],
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/erasure-requests/test-only/privacy-probe",
            headers=_service_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert maintenance.calls == []


def test_ingest_route_is_not_blocked_by_worker_maintenance(
    tmp_path, monkeypatch
) -> None:
    app = main_module.create_app(_ingest_settings(tmp_path))
    maintenance = Maintenance("maintenance_failed")
    _configure_db_less_app(app, monkeypatch, maintenance=maintenance)

    with TestClient(app) as client:
        response = client.post(
            "/v1/ingest/envelopes",
            headers={"Authorization": f"Bearer {EDGE_TOKEN}"},
            json={"envelopes": []},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_envelope_batch"
    assert maintenance.calls == []


@pytest.mark.asyncio
async def test_preference_opt_out_survives_but_opt_in_requires_worker() -> None:
    route = next(
        route
        for route in semantic_router().routes
        if route.path == "/v1/preferences/{key}" and "PUT" in route.methods
    )
    maintenance = Maintenance("maintenance_stale")

    class Database:
        async def run_serializable(self, operation):
            return await operation()

    class Store:
        database = Database()
        maintenance_inspector = maintenance
        maintenance_observed_after = NOW

        def __init__(self) -> None:
            self.writes = []

        async def set_preference(self, _principal, value):
            self.writes.append(value.enabled)
            return {"enabled": value.enabled}

    store = Store()
    principal = {"principal_id": uuid.uuid4()}
    disabled = PreferenceUpdate(
        key="location_memory",
        enabled=False,
        confirmation_artifact_id=uuid.uuid4(),
    )
    enabled = disabled.model_copy(
        update={"enabled": True, "confirmation_artifact_id": uuid.uuid4()}
    )

    result = await route.endpoint("location_memory", disabled, principal, store)
    assert result == {"enabled": False}
    assert store.writes == [False]
    assert maintenance.calls == []

    with pytest.raises(Exception) as blocked:
        await route.endpoint("location_memory", enabled, principal, store)
    assert getattr(blocked.value, "status_code", None) == 503
    assert getattr(blocked.value, "code", None) == "worker_maintenance_unavailable"
    assert store.writes == [False]
    assert maintenance.calls == [{"observed_after": NOW}]

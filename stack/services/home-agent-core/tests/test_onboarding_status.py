from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, insert, select, update

from app import schema
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import OnboardingStatusView, PersonCreate
from app.phase2 import Phase2OnboardingReadiness
from app.restore import RestoreGateStatus
from app.rollout import RolloutAuthorizationStatus
from app.spool import DisabledRuntimeSpool
from app.store import CoreStore
from tests.binding_helpers import complete_staged_principal_binding


class CurrentRestoreGate:
    async def status(self, *, force: bool = False) -> RestoreGateStatus:
        return RestoreGateStatus(True, "current", 0, 0)


class CurrentRolloutGate:
    async def status(self, *, force: bool = False) -> RolloutAuthorizationStatus:
        return RolloutAuthorizationStatus(True, "authorized")


BindingStatus = Literal["bound", "missing", "unavailable"]


class OnboardingStore:
    def __init__(self, settings: Settings, binding_status: BindingStatus) -> None:
        self.settings = settings
        self.database = object()
        self.binding_status = binding_status
        self.inspected_ha_users: list[str] = []
        self.phase2_inspector_modes: list[str] = []

    async def onboarding_binding_status(self, ha_user_id: str) -> BindingStatus:
        self.inspected_ha_users.append(ha_user_id)
        return self.binding_status


def settings_for(tmp_path, *, rollout_mode: str = "record_only") -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    return Settings(
        database_url=SecretStr("postgresql+psycopg://unused:unused@127.0.0.1:1/unused"),
        runtime_spool_path=tmp_path / "runtime.sqlite",
        storage_monitor_path=tmp_path,
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode=rollout_mode,
    )


def phase2_readiness(
    *,
    rollout_mode: Literal["record_only", "shadow", "canary"],
    qualifying: int,
    observation_days: int,
):
    blockers = []
    if rollout_mode != "record_only":
        blockers.append("rollout_mode_not_record_only")
    if observation_days < 7:
        blockers.append("minimum_observation_window_not_elapsed")
    if qualifying < 500:
        blockers.append("qualifying_redacted_envelope_threshold_not_met")
    return Phase2OnboardingReadiness(
        ready_to_advance=not blockers,
        blockers=tuple(blockers),
    )


def app_for(tmp_path, monkeypatch, *, binding_status, rollout_mode, readiness):
    settings = settings_for(tmp_path, rollout_mode=rollout_mode)
    app = create_app(settings)
    store = OnboardingStore(settings, binding_status)
    app.state.store = store
    app.state.restore_gate = CurrentRestoreGate()
    app.state.rollout_gate = CurrentRolloutGate()

    async def inspect_onboarding(_inspector, *, now=None):
        store.phase2_inspector_modes.append(_inspector.rollout_mode)
        return readiness

    async def authoritative_inspect_must_not_run(*_args, **_kwargs):
        raise AssertionError("onboarding must use the bounded Phase 2 inspection")

    monkeypatch.setattr(
        "app.api.Phase2GateInspector.inspect_onboarding", inspect_onboarding
    )
    monkeypatch.setattr(
        "app.api.Phase2GateInspector.inspect", authoritative_inspect_must_not_run
    )
    return app, store


def service_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer service-token-with-at-least-32-chars",
        "X-Authenticated-HA-User": "authenticated-ha-user-secret-sentinel",
    }


@pytest.mark.parametrize(
    (
        "binding_status",
        "rollout_mode",
        "qualifying",
        "observation_days",
        "expected_state",
        "expected_bound",
        "expected_ready",
    ),
    [
        (
            "missing",
            "record_only",
            12,
            1,
            "collecting_evidence",
            False,
            False,
        ),
        (
            "missing",
            "record_only",
            500,
            8,
            "collecting_evidence",
            False,
            True,
        ),
        (
            "missing",
            "shadow",
            500,
            8,
            "identity_confirmation_required",
            False,
            True,
        ),
        ("missing", "canary", 500, 8, "identity_confirmation_required", False, True),
        ("unavailable", "shadow", 500, 8, "contained", False, True),
        ("bound", "shadow", 500, 8, "bound", True, True),
    ],
)
def test_onboarding_state_transitions_are_fail_closed(
    tmp_path,
    monkeypatch,
    binding_status,
    rollout_mode,
    qualifying,
    observation_days,
    expected_state,
    expected_bound,
    expected_ready,
) -> None:
    readiness = phase2_readiness(
        rollout_mode="record_only",
        qualifying=qualifying,
        observation_days=observation_days,
    )
    app, store = app_for(
        tmp_path,
        monkeypatch,
        binding_status=binding_status,
        rollout_mode=rollout_mode,
        readiness=readiness,
    )

    with TestClient(app) as client:
        response = client.get("/v1/onboarding/status", headers=service_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == expected_state
    assert body["principal_bound"] is expected_bound
    assert body["phase2_ready"] is expected_ready
    assert body["rollout_mode"] == rollout_mode
    assert body["parent_relationship_confirmation"] == "disabled"
    assert body["location_memory_default_off"] is True
    assert body["travel_greetings_default_off"] is True
    assert store.phase2_inspector_modes == ["record_only"]


def test_onboarding_enables_parent_review_only_for_bound_e5h_subject(
    tmp_path, monkeypatch
) -> None:
    readiness = phase2_readiness(
        rollout_mode="record_only", qualifying=500, observation_days=8
    )
    app, store = app_for(
        tmp_path,
        monkeypatch,
        binding_status="bound",
        rollout_mode="record_only",
        readiness=readiness,
    )
    store.settings = store.settings.model_copy(
        update={"readiness_migration": "0021_parent_status_e5h"}
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/onboarding/status",
            headers=service_headers(),
        )

    assert response.status_code == 200
    assert response.json()["parent_relationship_confirmation"] == "enabled"


def test_onboarding_uses_only_authenticated_service_identity_and_bounds_content(
    tmp_path, monkeypatch
) -> None:
    readiness = phase2_readiness(
        rollout_mode="record_only", qualifying=37, observation_days=1
    )
    app, store = app_for(
        tmp_path,
        monkeypatch,
        binding_status="missing",
        rollout_mode="record_only",
        readiness=readiness,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/onboarding/status",
            params={
                "actor_id": "client-actor-secret-sentinel",
                "principal_id": "client-principal-secret-sentinel",
            },
            headers={
                **service_headers(),
                "X-Actor-ID": "client-actor-header-secret-sentinel",
                "X-Principal-ID": "client-principal-header-secret-sentinel",
                "X-Home-Agent-Bootstrap": "client-bootstrap-secret-sentinel",
            },
        )

    assert response.status_code == 200
    assert store.inspected_ha_users == ["authenticated-ha-user-secret-sentinel"]
    assert response.json() == {
        "state": "collecting_evidence",
        "rollout_mode": "record_only",
        "principal_bound": False,
        "phase2_observation_days_required": 7,
        "qualifying_redacted_envelopes_required": 500,
        "phase2_ready": False,
        "phase2_blockers": [
            "minimum_observation_window_not_elapsed",
            "qualifying_redacted_envelope_threshold_not_met",
        ],
        "parent_relationship_confirmation": "disabled",
        "location_memory_default_off": True,
        "travel_greetings_default_off": True,
    }
    serialized = response.text
    assert "authenticated-ha-user-secret-sentinel" not in serialized
    assert "client-actor" not in serialized
    assert "client-principal" not in serialized
    assert "bootstrap" not in serialized
    assert "digest" not in serialized
    assert "policy" not in serialized


def test_onboarding_requires_service_token_and_authenticated_ha_user(
    tmp_path, monkeypatch
) -> None:
    readiness = phase2_readiness(
        rollout_mode="record_only", qualifying=0, observation_days=0
    )
    app, store = app_for(
        tmp_path,
        monkeypatch,
        binding_status="missing",
        rollout_mode="record_only",
        readiness=readiness,
    )

    with TestClient(app) as client:
        no_bearer = client.get(
            "/v1/onboarding/status",
            headers={"X-Authenticated-HA-User": "ha-user"},
        )
        no_ha_user = client.get(
            "/v1/onboarding/status",
            headers={"Authorization": "Bearer service-token-with-at-least-32-chars"},
        )
        operator_bootstrap = client.get(
            "/v1/onboarding/status",
            headers={
                "Authorization": "Bearer operator-token-with-at-least-32-chars",
                "X-Authenticated-HA-User": "ha-user",
                "X-Home-Agent-Bootstrap": "bootstrap-token-with-at-least-32-chars",
            },
        )

    assert no_bearer.status_code == 401
    assert no_ha_user.status_code == 401
    assert operator_bootstrap.status_code == 401
    assert store.inspected_ha_users == []


def test_onboarding_model_rejects_identity_content_and_unbounded_blockers() -> None:
    common = {
        "state": "contained",
        "rollout_mode": "record_only",
        "principal_bound": False,
        "phase2_ready": False,
        "phase2_blockers": ["no_durable_envelopes"],
    }
    with pytest.raises(ValidationError, match="principal_id"):
        OnboardingStatusView(**common, principal_id="identity-secret")
    with pytest.raises(ValidationError, match="phase2_blockers"):
        OnboardingStatusView(
            **{**common, "phase2_blockers": ["person_name_or_private_reason"]}
        )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
async def test_postgres_onboarding_binding_status_fails_closed(
    tmp_path,
) -> None:
    settings = settings_for(tmp_path, rollout_mode="shadow").model_copy(
        update={"database_url": SecretStr(os.environ["TEST_DATABASE_URL"])}
    )
    database = Database(settings.async_database_url())
    store = CoreStore(database, DisabledRuntimeSpool(), settings)
    suffix = uuid.uuid4().hex
    ha_user_id = f"onboarding-{suffix}"
    block_id = uuid.uuid4()
    silent_directive_id = uuid.uuid4()
    person_id = None
    principal_id = None
    binding_id = None
    artifact_id = None
    try:
        assert await store.onboarding_binding_status(ha_user_id) == "missing"
        person = await store.create_person(
            PersonCreate(display_name="Onboarding integration fixture")
        )
        bound = await complete_staged_principal_binding(
            store,
            ha_user_id=ha_user_id,
            person_id=person.person_id,
        )
        person_id = bound.person_id
        principal_id = bound.principal_id
        async with database.transaction() as connection:
            binding_row = (
                (
                    await connection.execute(
                        select(
                            schema.ha_user_bindings.c.binding_id,
                            schema.ha_user_bindings.c.source_artifact_id,
                        ).where(schema.ha_user_bindings.c.ha_user_id == ha_user_id)
                    )
                )
                .mappings()
                .one()
            )
        binding_id = binding_row["binding_id"]
        artifact_id = binding_row["source_artifact_id"]

        assert await store.onboarding_binding_status(ha_user_id) == "bound"
        async with database.transaction() as connection:
            await connection.execute(
                insert(schema.privacy_directives).values(
                    directive_id=silent_directive_id,
                    person_id=person_id,
                    directive="silent",
                    enabled=True,
                )
            )
        assert await store.onboarding_binding_status(ha_user_id) == "unavailable"

        async with database.transaction() as connection:
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.directive_id == silent_directive_id
                )
            )
        async with database.transaction() as connection:
            await connection.execute(
                insert(schema.edge_privacy_user_blocks).values(
                    block_id=block_id,
                    ha_user_id=ha_user_id,
                    reason_code="do_not_track",
                    person_id=person_id,
                )
            )
        assert await store.onboarding_binding_status(ha_user_id) == "unavailable"

        async with database.transaction() as connection:
            await connection.execute(
                delete(schema.edge_privacy_user_blocks).where(
                    schema.edge_privacy_user_blocks.c.block_id == block_id
                )
            )
            await connection.execute(
                update(schema.ha_user_bindings)
                .where(schema.ha_user_bindings.c.binding_id == binding_id)
                .values(revoked_at=datetime.now(UTC))
            )
        assert await store.onboarding_binding_status(ha_user_id) == "unavailable"
    finally:
        async with database.transaction() as connection:
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.directive_id == silent_directive_id
                )
            )
            await connection.execute(
                delete(schema.edge_privacy_user_blocks).where(
                    schema.edge_privacy_user_blocks.c.ha_user_id == ha_user_id
                )
            )
            await connection.execute(
                delete(schema.principal_binding_requests).where(
                    schema.principal_binding_requests.c.ha_user_id == ha_user_id
                )
            )
            if artifact_id is not None:
                await connection.execute(
                    delete(schema.artifact_registry).where(
                        schema.artifact_registry.c.artifact_id == artifact_id
                    )
                )
                await connection.execute(
                    delete(schema.confirmation_artifacts).where(
                        schema.confirmation_artifacts.c.artifact_id == artifact_id
                    )
                )
            if principal_id is not None:
                await connection.execute(
                    delete(schema.principals).where(
                        schema.principals.c.principal_id == principal_id
                    )
                )
            if person_id is not None:
                await connection.execute(
                    delete(schema.people).where(schema.people.c.person_id == person_id)
                )
        await database.close()

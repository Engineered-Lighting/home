from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app import personal_memory_runtime as runtime
from app.errors import OptionalWorkSuspendedError
from app.personal_memory_api import PersonalMemoryBinding
from app.store import CoreStore


def fixture(monkeypatch):
    app=FastAPI()
    settings=NS(role="api",rollout_mode="shadow",readiness_migration=runtime.REVISION,
        policy_digest="a"*64,policy_version="fixture",storage_monitor_path="unused")
    database=NS(engine=NS(url=NS(username="home_agent_api")),
        migration_revision=AsyncMock(return_value=runtime.REVISION))
    store=object.__new__(CoreStore)
    store.database,store.settings=database,settings
    app.state.settings,app.state.database,app.state.store=settings,database,store
    app.state.maintenance_observed_after=object()
    app.state.restore_gate=NS(status=AsyncMock(return_value=NS(current=True)))
    app.state.rollout_gate=NS(status=AsyncMock(return_value=NS(authorized=True)))
    app.state.maintenance_inspector=NS(inspect=AsyncMock(return_value=NS(ready=True)))
    monkeypatch.setattr(runtime,"outbox_health",AsyncMock(return_value=NS(ready=True)))
    monkeypatch.setattr(runtime,"resource_budget_snapshot",AsyncMock(return_value={"ready":True}))
    monkeypatch.setattr(runtime,"create_personal_memory_ingress",lambda **kwargs:kwargs["service"])
    return app


def compose(app):
    return runtime.compose_personal_memory_ingress(app,
        binding=PersonalMemoryBinding("home-assistant:echo",b"b"*64),review_key=b"k"*32)


@pytest.mark.asyncio
async def test_runtime_rechecks_restore_at_delivery(monkeypatch):
    app=fixture(monkeypatch)
    service=compose(app)
    await service.admission()
    app.state.restore_gate.status.return_value=NS(current=False)
    with pytest.raises(OptionalWorkSuspendedError):
        await service.admission()
    assert app.state.restore_gate.status.await_count==2
    assert all(call.kwargs=={"force":True} for call in app.state.restore_gate.status.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["startup","schema","rollout","maintenance","outbox","resources"])
async def test_runtime_fails_closed_when_required_state_is_unavailable(monkeypatch,failure):
    app=fixture(monkeypatch)
    service=compose(app)
    if failure=="startup": app.state.maintenance_observed_after=None
    elif failure=="schema": app.state.database.migration_revision.return_value="0031_relationship_uniqueness_e5r"
    elif failure=="rollout": app.state.rollout_gate.status.return_value=NS(authorized=False)
    elif failure=="maintenance": app.state.maintenance_inspector.inspect.return_value=NS(ready=False)
    elif failure=="outbox": runtime.outbox_health.return_value=NS(ready=False)
    else: runtime.resource_budget_snapshot.return_value={"ready":False}
    with pytest.raises(OptionalWorkSuspendedError):
        await service.admission()


@pytest.mark.parametrize("username",["postgres","home_agent_owner","home_agent_binding_operator"])
def test_operator_and_administrator_pools_cannot_back_online_preferences(monkeypatch,username):
    app=fixture(monkeypatch)
    app.state.database.engine.url.username=username
    with pytest.raises(ValueError): compose(app)


def test_separate_store_or_wrong_revision_cannot_be_silently_substituted(monkeypatch):
    app=fixture(monkeypatch)
    app.state.store.database=object()
    with pytest.raises(ValueError): compose(app)
    app=fixture(monkeypatch)
    app.state.settings.readiness_migration="0031_relationship_uniqueness_e5r"
    with pytest.raises(ValueError): compose(app)

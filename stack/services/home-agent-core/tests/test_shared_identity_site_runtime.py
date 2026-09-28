from unittest.mock import AsyncMock
import asyncio
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from app import shared_identity_site_runtime as runtime
from app.shared_auth_proof import SharedLinkProofDatabase
from app.shared_auth_proof_api import PATH as PROOF_PATH, LOOKUP_PATH, ProofIngressBinding
from app.shared_link_session_api import PATH as SESSION_PATH, SessionRevocationBinding
from app.shared_link_session_revocation import SharedLinkSessionRevocationDatabase
from app.errors import OptionalWorkSuspendedError
from app.store import CoreStore


def configuration(site="echo"):
    return dict(proof_binding=ProofIngressBinding(f"home-assistant:{site}", b"a"*64),
        session_binding=SessionRevocationBinding(f"home-assistant:{site}", b"b"*64),
        proof_database_url=f"postgresql://home_agent_shared_{site}_proof_ingress@localhost/unused",
        session_database_url=f"postgresql://home_agent_shared_{site}_session_ingress@localhost/unused",
        admit_proof=AsyncMock(), admit_revocation=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["echo", "victoria"])
async def test_lifespan_and_separate_admission_preserve_logout(monkeypatch, site):
    proof_close, session_close = AsyncMock(), AsyncMock()
    monkeypatch.setattr(SharedLinkProofDatabase, "close", proof_close)
    monkeypatch.setattr(SharedLinkSessionRevocationDatabase, "close", session_close)
    config = configuration(site)
    app = runtime.create_site_identity_runtime(**config)
    assert {r.path for r in app.routes} == {PROOF_PATH, LOOKUP_PATH, SESSION_PATH}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
        assert (await client.post(PROOF_PATH)).status_code == 503
        async with app.router.lifespan_context(app):
            config["admit_proof"].side_effect = RuntimeError("private admission detail")
            response = await client.post(PROOF_PATH, headers={"authorization": "Bearer " + "a"*64})
            assert response.status_code == 503
            assert "private admission detail" not in response.text
            assert response.headers["cache-control"] == "no-store"
            # Revocation still reaches its own authentication boundary while
            # optional proof work is suspended. No SQL transport is mocked.
            assert (await client.post(SESSION_PATH)).status_code == 401
            assert (await client.post(SESSION_PATH, headers={"authorization": "Bearer " + "b"*64})).status_code == 415
            assert (await client.post("/legacy/core")).status_code == 404
            config["admit_proof"].side_effect = None
            assert (await client.post(LOOKUP_PATH)).status_code == 401
        assert (await client.post(SESSION_PATH)).status_code == 503
    proof_close.assert_awaited_once()
    session_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_startup_closes_both_pools(monkeypatch):
    close_proof, close_session = AsyncMock(), AsyncMock()
    monkeypatch.setattr(SharedLinkProofDatabase, "close", close_proof)
    monkeypatch.setattr(SharedLinkSessionRevocationDatabase, "close", close_session)
    config = configuration()
    config["admit_revocation"].side_effect = ValueError("restore pending")
    app = runtime.create_site_identity_runtime(**config)
    with pytest.raises(ValueError):
        async with app.router.lifespan_context(app):
            pytest.fail("unavailable runtime entered")
    close_proof.assert_awaited_once()
    close_session.assert_awaited_once()


@pytest.mark.parametrize("change", ["issuer", "credential", "proof_role", "session_role", "admission"])
def test_runtime_rejects_cross_site_or_privileged_configuration(change):
    config = configuration()
    if change == "issuer": config["session_binding"] = SessionRevocationBinding("home-assistant:victoria", b"b"*64)
    elif change == "credential": config["session_binding"] = SessionRevocationBinding("home-assistant:echo", b"a"*64)
    elif change == "proof_role": config["proof_database_url"] = "postgresql://home_agent_owner@localhost/unused"
    elif change == "session_role": config["session_database_url"] = "postgresql://postgres@localhost/unused"
    else: config["admit_proof"] = lambda: None
    with pytest.raises(ValueError): runtime.create_site_identity_runtime(**config)


@pytest.mark.asyncio
async def test_admission_is_bounded_and_unauthenticated_requests_do_not_enter():
    config = configuration()
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def hold():
        nonlocal count
        count += 1
        if count == 2: entered.set()
        await release.wait()

    config["admit_proof"] = hold
    app = runtime.create_site_identity_runtime(**config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
            assert (await client.post(PROOF_PATH)).status_code == 401
            assert count == 0
            headers = {"authorization": "Bearer " + "a"*64}
            tasks = [asyncio.create_task(client.post(PROOF_PATH, headers=headers)) for _ in range(2)]
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                response = await client.post(LOOKUP_PATH, headers=headers)
                assert response.status_code == 429 and count == 2
            finally:
                release.set()
                results = await asyncio.gather(*tasks)
            assert all(result.status_code == 415 for result in results)
            assert (await client.post(LOOKUP_PATH, headers=headers)).status_code == 415


def core_fixture(monkeypatch):
    core = FastAPI()
    settings = NS(role="api", rollout_mode="shadow", readiness_migration=runtime.REVISION,
                  storage_monitor_path="unused")
    database = NS(engine=NS(url=make_url("postgresql://home_agent_api@localhost/unused")),
                  migration_revision=AsyncMock(return_value=runtime.REVISION))
    store = object.__new__(CoreStore)
    store.settings, store.database = settings, database
    core.state.settings, core.state.database, core.state.store = settings, database, store
    core.state.maintenance_observed_after = object()
    core.state.restore_gate = NS(status=AsyncMock(return_value=NS(current=True)))
    core.state.rollout_gate = NS(status=AsyncMock(return_value=NS(authorized=True)))
    core.state.maintenance_inspector = NS(inspect=AsyncMock(return_value=NS(ready=True)))
    monkeypatch.setattr(runtime, "resource_budget_snapshot", AsyncMock(return_value={"ready": True}))
    monkeypatch.setattr(runtime, "create_site_identity_runtime", lambda **kwargs: kwargs)
    return core


def compose(core):
    config = configuration()
    del config["admit_proof"], config["admit_revocation"]
    return runtime.compose_site_identity_ingress(core, **config)


@pytest.mark.asyncio
async def test_core_admission_rechecks_restore_but_does_not_gate_logout_on_optional_work(monkeypatch):
    core = core_fixture(monkeypatch)
    composed = compose(core)
    await composed["admit_proof"]()
    core.state.rollout_gate.status.return_value = NS(authorized=False)
    with pytest.raises(OptionalWorkSuspendedError): await composed["admit_proof"]()
    await composed["admit_revocation"]()
    core.state.restore_gate.status.return_value = NS(current=False)
    with pytest.raises(OptionalWorkSuspendedError): await composed["admit_revocation"]()


@pytest.mark.parametrize("change", ["schema", "admin", "other_database", "other_host", "store"])
def test_core_runtime_refuses_unrelated_or_privileged_admission(monkeypatch, change):
    core = core_fixture(monkeypatch)
    if change == "schema": core.state.settings.readiness_migration = "0031_relationship_uniqueness_e5r"
    elif change == "admin": core.state.database.engine.url = make_url("postgresql://postgres@localhost/unused")
    elif change == "other_database": core.state.database.engine.url = make_url("postgresql://home_agent_api@localhost/other")
    elif change == "other_host": core.state.database.engine.url = make_url("postgresql://home_agent_api@elsewhere/unused")
    else: core.state.store.database = object()
    with pytest.raises(ValueError): compose(core)

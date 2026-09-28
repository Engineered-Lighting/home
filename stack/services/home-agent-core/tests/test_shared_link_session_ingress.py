"""Actual adapter/ASGI composition with deterministic SQL transport only."""
from datetime import UTC, datetime, timedelta
import asyncio
from uuid import uuid4

import httpx
import pytest

from app import shared_link_session_revocation as module
from app.shared_link_session_api import PATH, SessionRevocationBinding, create_session_revocation_ingress
from .test_shared_auth_proof_adapter import Engine

NOW = datetime(2026, 9, 26, tzinfo=UTC)
HEADERS = {"authorization": "Bearer "+"a"*64, "content-type": "application/json"}


def fixture(monkeypatch, site="echo"):
    value = module.SharedLinkSessionRevocation(session_commitment="b"*64, revocation_id=uuid4())
    engine = Engine({**value.model_dump(), "issuer_id": f"home-assistant:{site}", "revoked_at": NOW})
    engine.expected_function = "identity.revoke_shared_link_session_bound_v1"
    def factory(url, **options):
        assert url.username == f"home_agent_shared_{site}_session_ingress"
        assert options["pool_size"] == 1 and options["max_overflow"] == 0 and options["hide_parameters"]
        assert "statement_timeout=7000" in options["connect_args"]["options"]
        return engine
    monkeypatch.setattr(module, "create_async_engine", factory)
    database = module.SharedLinkSessionRevocationDatabase(f"postgresql://home_agent_shared_{site}_session_ingress@fixture/unused",
        issuer_id=f"home-assistant:{site}", now=lambda: NOW)
    app = create_session_revocation_ingress(binding=SessionRevocationBinding(database.issuer_id, b"a"*64), database=database)
    return value, engine, database, app


@pytest.mark.parametrize("site", ["echo", "victoria"])
@pytest.mark.asyncio
async def test_shared_link_session_ingress_original_id_replay_has_no_caller_issuer(monkeypatch, site):
    value, engine, _, app = fixture(monkeypatch, site)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://sessions.test") as client:
        first = await client.post(PATH, headers=HEADERS, json={"version": 1, "revocation": value.model_dump(mode="json")})
        again = await client.post(PATH, headers=HEADERS, json={"version": 1, "revocation": value.model_dump(mode="json")})
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert first.headers["cache-control"] == "no-store"
    assert engine.parameters == value.model_dump()
    assert engine.events == ["connect", "isolation", "begin", "kernel", "commit"]*2


@pytest.mark.parametrize("change", ["issuer", "id", "session", "future", "uncertain"])
@pytest.mark.asyncio
async def test_shared_link_session_adapter_rejects_changed_receipt_without_retry(monkeypatch, change):
    value, engine, database, _ = fixture(monkeypatch)
    if change == "issuer": engine.row["issuer_id"] = "home-assistant:victoria"
    if change == "id": engine.row["revocation_id"] = uuid4()
    if change == "session": engine.row["session_commitment"] = "c"*64
    if change == "future": engine.row["revoked_at"] = NOW+timedelta(seconds=2)
    if change == "uncertain": engine.row = ConnectionError("private details")
    with pytest.raises((ValueError, ConnectionError)): await database.revoke(value)
    assert engine.events[-1] == "rollback" and engine.events.count("kernel") == 1


@pytest.mark.parametrize("change,status", [("credential",401),("origin",403),("cookie",403),("header",400),
    ("issuer",422),("duplicate",422),("large",413),("http",403)])
@pytest.mark.asyncio
async def test_shared_link_session_ingress_rejects_untrusted_transport_or_scope(monkeypatch, change, status):
    value, engine, _, app = fixture(monkeypatch)
    headers = dict(HEADERS)
    body = {"version":1,"revocation":value.model_dump(mode="json")}
    if change == "credential": headers["authorization"] = "Bearer "+"c"*64
    if change == "origin": headers["origin"] = "https://browser.test"
    if change == "cookie": headers["cookie"] = "session=untrusted"
    if change == "header": headers["x-authenticated-home-site"] = "victoria"
    if change == "issuer": body["revocation"]["issuer_id"] = "home-assistant:victoria"
    options = {"json":body}
    if change == "duplicate": options = {"content":'{"version":1,"version":1,"revocation":{}}'}
    if change == "large": options = {"content":"x"*1025}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sessions.test" if change == "http" else "https://sessions.test") as client:
        response = await client.post(PATH, headers=headers, **options)
    assert response.status_code == status and engine.events == []


@pytest.mark.parametrize("url", ["postgresql://home_agent_shared_echo_proof_ingress@fixture/db", "postgresql://postgres@fixture/db",
    "postgresql://home_agent_shared_echo_session_ingress@fixture/db?user=postgres",
    "postgresql://home_agent_shared_echo_session_ingress@fixture/db?sslmode=disable"])
def test_shared_link_session_adapter_rejects_credential_overrides(monkeypatch, url):
    monkeypatch.setattr(module, "create_async_engine", lambda *a, **kw: pytest.fail("no connection permitted"))
    with pytest.raises(ValueError): module.SharedLinkSessionRevocationDatabase(url, issuer_id="home-assistant:echo")


@pytest.mark.asyncio
async def test_shared_link_session_ingress_disabled_by_default():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_session_revocation_ingress()), base_url="https://sessions.test") as client:
        assert (await client.post(PATH, headers=HEADERS, json={})).status_code == 503


@pytest.mark.asyncio
async def test_shared_link_session_adapter_validates_before_connect_and_after_commit(monkeypatch):
    value, engine, database, _ = fixture(monkeypatch)
    with pytest.raises(ValueError): await database.revoke(value.model_copy(update={"session_commitment":"invalid"}))
    assert engine.events == []
    database._now = lambda: NOW-timedelta(seconds=2) if "commit" in engine.events else NOW
    with pytest.raises(ValueError): await database.revoke(value)
    assert engine.events[-1] == "commit" and engine.events.count("kernel") == 1


@pytest.mark.asyncio
async def test_shared_link_session_ingress_bounds_concurrency_and_releases_capacity(monkeypatch):
    value, engine, _, app = fixture(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = engine.execute
    count = 0
    async def execute(*args):
        nonlocal count
        count += 1
        if count == 2: entered.set()
        await release.wait()
        return await original(*args)
    engine.execute = execute
    body = {"version":1,"revocation":value.model_dump(mode="json")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://sessions.test") as client:
        pending = [asyncio.create_task(client.post(PATH, headers=HEADERS, json=body)) for _ in range(2)]
        try:
            await asyncio.wait_for(entered.wait(),2)
            assert (await client.post(PATH, headers=HEADERS, json=body)).status_code == 429
        finally: release.set()
        assert all(result.status_code == 200 for result in await asyncio.gather(*pending))
        assert (await client.post(PATH, headers=HEADERS, json=body)).status_code == 200


@pytest.mark.asyncio
async def test_shared_link_session_ingress_timeout_never_retries_or_leaks_details(monkeypatch):
    from app import shared_link_session_api
    monkeypatch.setattr(shared_link_session_api,"TIMEOUT_SECONDS",0.01)
    value, engine, _, app = fixture(monkeypatch)
    calls = 0
    async def execute(*args):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
    engine.execute = execute
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://sessions.test") as client:
        result = await client.post(PATH,headers=HEADERS,json={"version":1,"revocation":value.model_dump(mode="json")})
    assert result.status_code == 503 and result.json() == {"error":"revocation_outcome_unknown"}
    assert calls == 1

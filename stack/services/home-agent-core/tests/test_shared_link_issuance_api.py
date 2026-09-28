"""Private HTTP boundary with the real issuance service/journal and fixture SQL."""
import asyncio
import json

import httpx
import pytest

from app import shared_link_issuance_api as api
from app.shared_link_issuance_service import SharedLinkIssuanceService
from .test_shared_link_issuance_service import setup
from .test_shared_link_commitments import submission
from .test_shared_link_issuance_adapter import NOW

HEADERS = {"authorization": "Bearer "+"c"*64, "content-type": "application/json"}


def fixture(tmp_path, monkeypatch):
    journal, database, engine, context, _ = setup(tmp_path, monkeypatch)
    service = SharedLinkIssuanceService(journal=journal, database=database, now=lambda: NOW)
    app = api.create_link_issuance_ingress(binding=api.LinkIssuanceBinding(b"c"*64), service=service)
    body = {"version": 1, "request": {"subject": submission().echo_subject, "context": context.model_dump(mode="json")}}
    return journal, engine, app, body


@pytest.mark.asyncio
async def test_shared_link_issuance_api_begin_duplicate_and_outcome_are_separate(tmp_path, monkeypatch):
    journal, engine, app, body = fixture(tmp_path, monkeypatch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://private.test", headers=HEADERS) as client:
            first = await client.post(api.BEGIN_PATH, json=body)
            assert first.status_code == 200
            assert first.headers["cache-control"] == "no-store"
            assert set(first.json()) == {"version", "issuance"}
            assert set(first.json()["issuance"]) == {"ceremony_id", "authorization_generation", "revision", "created_at", "expires_at", "echo_registration_revision", "victoria_registration_revision"}
            for field in ("principal_id", "person_id", "legacy_binding_id", "subject", "session_commitment"):
                assert field not in first.text
            assert (await client.post(api.BEGIN_PATH, json=body)).status_code == 503
            recovered = await client.post(api.RECOVER_PATH, json=body)
            assert recovered.json() == first.json()
            assert engine.events.count("issue") == 1
    finally: journal.close()


@pytest.mark.parametrize("change,status", [("credential",401),("cookie",403),("origin",403),("identity",400),
    ("http",403),("query",400),("encoding",400),("large",413),("duplicate",422),("owner",422),("issuer",422),("bad_version",422)])
@pytest.mark.asyncio
async def test_shared_link_issuance_api_rejects_before_authority(tmp_path, monkeypatch, change, status):
    journal, engine, app, body = fixture(tmp_path, monkeypatch)
    headers = dict(HEADERS)
    if change == "credential": headers["authorization"] = "Bearer wrong"
    if change in ("cookie", "origin"): headers[change] = "browser"
    if change == "identity": headers["x-authenticated-user"] = "owner"
    if change == "encoding": headers["content-encoding"] = "gzip"
    if change == "owner": body["request"]["context"]["principal_id"] = str(submission().principal_id)
    if change == "issuer": body["request"]["issuer_id"] = "home-assistant:victoria"
    if change == "bad_version": body["version"] = True
    content = json.dumps(body)
    if change == "duplicate": content = content.replace('"version": 1', '"version": 1, "version": 1')
    if change == "large": content = "x"*2049
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://private.test" if change == "http" else "https://private.test") as client:
            response = await client.post(api.BEGIN_PATH+("?x=1" if change == "query" else ""), headers=headers, content=content)
            assert response.status_code == status
            assert engine.events == []
    finally: journal.close()


@pytest.mark.asyncio
async def test_shared_link_issuance_api_uncertain_recovery_never_redispatches(tmp_path, monkeypatch):
    journal, engine, app, body = fixture(tmp_path, monkeypatch)
    engine.unknown = True
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://private.test", headers=HEADERS) as client:
            assert (await client.post(api.BEGIN_PATH, json=body)).status_code == 503
            response = await client.post(api.RECOVER_PATH, json=body)
            assert response.status_code == 200
            body["request"]["subject"] = "other-owner"
            before = list(engine.events)
            assert (await client.post(api.RECOVER_PATH, json=body)).status_code == 503
            assert engine.events == before
            assert engine.events.count("issue") == 1
    finally: journal.close()


@pytest.mark.asyncio
async def test_shared_link_issuance_api_disabled_by_default():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(api.create_link_issuance_ingress()), base_url="https://private.test") as client:
        assert (await client.post(api.BEGIN_PATH)).status_code == 503
        assert (await client.post(api.RECOVER_PATH)).status_code == 503


@pytest.mark.asyncio
async def test_shared_link_issuance_api_bounds_concurrency_and_recovers_capacity(tmp_path, monkeypatch):
    journal, engine, app, body = fixture(tmp_path, monkeypatch)
    entered = 0
    ready, release = asyncio.Event(), asyncio.Event()
    execute = engine.execute
    async def hold(statement, parameters):
        nonlocal entered
        entered += 1
        if entered == 2: ready.set()
        await release.wait()
        return await execute(statement, parameters)
    monkeypatch.setattr(engine, "execute", hold)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://private.test", headers=HEADERS) as client:
            one = asyncio.create_task(client.post(api.BEGIN_PATH, json=body))
            two = asyncio.create_task(client.post(api.BEGIN_PATH, json=body))
            await asyncio.wait_for(ready.wait(), 2)
            assert (await client.post(api.BEGIN_PATH, json=body)).status_code == 429
            release.set()
            await asyncio.gather(one, two)
            assert (await client.post(api.RECOVER_PATH, json=body)).status_code != 429
    finally: release.set(); journal.close()


@pytest.mark.asyncio
async def test_shared_link_issuance_api_timeout_does_not_retry(tmp_path, monkeypatch):
    journal, engine, app, body = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "TIMEOUT_SECONDS", .01)
    entered = 0
    async def hold(statement, parameters):
        nonlocal entered
        entered += 1
        await asyncio.Event().wait()
    monkeypatch.setattr(engine, "execute", hold)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://private.test", headers=HEADERS) as client:
            response = await client.post(api.BEGIN_PATH, json=body)
            assert response.status_code == 503
            assert response.json() == {"error": "issuance_outcome_unknown"}
            assert entered == 1
    finally: journal.close()

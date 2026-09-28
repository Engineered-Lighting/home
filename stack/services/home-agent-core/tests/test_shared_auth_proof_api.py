"""Actual ASGI route tests with fixture DB; no HA or PostgreSQL acceptance."""
import asyncio
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from app.shared_auth_proof import FreshAuthReceipt
from app import shared_auth_proof_api as ingress
from app.shared_auth_proof_api import ProofIngressBinding, PATH, create_proof_ingress

TOKEN = "a" * 64
HEADERS = {"authorization": "Bearer " + TOKEN, "content-type": "application/json"}


def payload():
    return {"version": 1, "proof": {"proof_id": str(uuid4()), "subject": "same-id",
        "session_commitment": "b" * 64, "challenge_commitment": "c" * 64,
        "authenticated_at": datetime.now(UTC).isoformat(), "registration_revision": 1}}


class Database:
    issuer_id = "home-assistant:echo"
    def __init__(self):
        self.calls = []
        self.error = None

    async def issue(self, value):
        self.calls.append(value)
        if self.error:
            raise self.error
        return FreshAuthReceipt(**value.model_dump(), issuer_id=self.issuer_id,
            issued_at=value.authenticated_at + timedelta(seconds=1),
            expires_at=value.authenticated_at + timedelta(minutes=5))


def fixture():
    database = Database()
    application = create_proof_ingress(binding=ProofIngressBinding(database.issuer_id, TOKEN.encode()), database=database)
    return application, database


def test_disabled_factory_never_accepts_proof():
    with TestClient(create_proof_ingress(), base_url="https://proof.test") as client:
        assert client.post(PATH, headers=HEADERS, json=payload()).status_code == 503


def test_incomplete_or_contradictory_binding_cannot_create_service():
    binding = ProofIngressBinding("home-assistant:victoria", TOKEN.encode())
    with pytest.raises(ValueError):
        create_proof_ingress(binding=binding)
    with pytest.raises(ValueError):
        create_proof_ingress(binding=binding, database=Database())
    assert TOKEN not in repr(binding)


def test_duplicate_authorization_is_rejected():
    app, database = fixture()
    with TestClient(app, base_url="https://proof.test") as client:
        response = client.post(PATH, headers=[("authorization", "Bearer " + TOKEN),
            ("authorization", "Bearer " + TOKEN), ("content-type", "application/json")], json=payload())
        assert response.status_code == 401
    assert not database.calls


def test_exact_versioned_request_reaches_adapter_and_response_is_not_cacheable():
    app, database = fixture()
    body = payload()
    with TestClient(app, base_url="https://proof.test") as client:
        response = client.post(PATH, headers=HEADERS, json=body)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["proof"]["issuer_id"] == "home-assistant:echo"
        assert response.json()["proof"]["proof_id"] == body["proof"]["proof_id"]
        assert len(database.calls) == 1
        assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize("headers,status", [
    ({"authorization": "Bearer " + "d" * 64}, 401),
    ({"origin": "https://home.test"}, 403), ({"cookie": "session=fake"}, 403),
    ({"x-authenticated-ha-user": "owner"}, 400),
    ({"content-encoding": "gzip"}, 400), ({"content-type": "text/plain"}, 415),
])
def test_boundary_rejections_happen_before_database(headers, status):
    app, database = fixture()
    with TestClient(app, base_url="https://proof.test") as client:
        response = client.post(PATH, headers={**HEADERS, **headers}, json=payload())
        assert response.status_code == status
    assert not database.calls


def test_forwarded_scheme_does_not_replace_secure_transport():
    app, database = fixture()
    with TestClient(app) as client:
        assert client.post(PATH, headers={**HEADERS, "x-forwarded-proto": "https"}, json=payload()).status_code == 403
    assert not database.calls


@pytest.mark.parametrize("body", [
    '{"version":1,"version":1,"proof":{}}',
    '{"version":true,"proof":{}}',
    '[]', "invalid", '"' + "x" * 4097 + '"',
])
def test_malformed_duplicate_or_oversized_body_never_calls_database(body):
    app, database = fixture()
    with TestClient(app, base_url="https://proof.test") as client:
        assert client.post(PATH, headers=HEADERS, content=body).status_code in (413, 422)
    assert not database.calls


def test_body_cannot_select_another_issuer():
    app, database = fixture()
    body = payload()
    body["proof"]["issuer_id"] = "home-assistant:victoria"
    with TestClient(app, base_url="https://proof.test") as client:
        assert client.post(PATH, headers=HEADERS, json=body).status_code == 422
    assert not database.calls


@pytest.mark.parametrize("error", [ConnectionError("private database details"), ValueError("receipt mismatch")])
def test_uncertain_database_outcome_is_not_retried_or_reflected(error):
    app, database = fixture()
    database.error = error
    with TestClient(app, base_url="https://proof.test") as client:
        response = client.post(PATH, headers=HEADERS, json=payload())
        assert response.status_code == 503
        assert response.json() == {"error": "proof_outcome_unknown"}
    assert len(database.calls) == 1


@pytest.mark.parametrize("state,status", [("42501", 403), ("22023", 422), ("23505", 409), ("08006", 503)])
def test_database_failure_codes_do_not_expose_sql_or_subject(state, status):
    app, database = fixture()
    original = RuntimeError("private database details")
    original.sqlstate = state
    database.error = DBAPIError("private SQL", {"subject": "private subject"}, original)
    with TestClient(app, base_url="https://proof.test") as client:
        response = client.post(PATH, headers=HEADERS, json=payload())
        assert response.status_code == status
        assert "private" not in response.text
    assert len(database.calls) == 1


@pytest.mark.asyncio
async def test_two_in_flight_requests_bound_admission_without_a_waiting_queue():
    app, database = fixture()
    released = asyncio.Event()
    both_entered = asyncio.Event()
    calls = 0
    original = database.issue
    async def blocked(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            both_entered.set()
        await released.wait()
        return await original(value)
    database.issue = blocked
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proof.test") as client:
        pending = [asyncio.create_task(client.post(PATH, headers=HEADERS, json=payload())) for _ in range(2)]
        try:
            await asyncio.wait_for(both_entered.wait(), 2)
            assert (await client.post(PATH, headers=HEADERS, json=payload())).status_code == 429
        finally:
            released.set()
        assert all(response.status_code == 200 for response in await asyncio.gather(*pending))
        assert (await client.post(PATH, headers=HEADERS, json=payload())).status_code == 200


@pytest.mark.asyncio
async def test_database_timeout_reports_unknown_without_retry_and_releases_capacity(monkeypatch):
    monkeypatch.setattr(ingress, "REQUEST_TIMEOUT_SECONDS", 0.05)
    app, database = fixture()
    original = database.issue
    calls = 0
    cancelled = asyncio.Event()
    async def stalled(value):
        nonlocal calls
        calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    database.issue = stalled
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proof.test") as client:
        response = await client.post(PATH, headers=HEADERS, json=payload())
        assert response.status_code == 503
        assert response.json() == {"error": "proof_outcome_unknown"}
        assert calls == 1 and cancelled.is_set()
        database.issue = original
        assert (await client.post(PATH, headers=HEADERS, json=payload())).status_code == 200


@pytest.mark.asyncio
async def test_stalled_upload_expires_before_database_and_releases_capacity(monkeypatch):
    monkeypatch.setattr(ingress, "REQUEST_TIMEOUT_SECONDS", 0.05)
    app, database = fixture()
    closed = asyncio.Event()
    async def body():
        try:
            yield b'{"version":'
            await asyncio.Event().wait()
        finally:
            closed.set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proof.test") as client:
        response = await client.post(PATH, headers=HEADERS, content=body())
        assert response.status_code == 503
        assert not database.calls
        assert closed.is_set()
        assert (await client.post(PATH, headers=HEADERS, json=payload())).status_code == 200


@pytest.mark.asyncio
async def test_external_request_cancellation_propagates_and_releases_capacity():
    app, database = fixture()
    original = database.issue
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def stalled(value):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    database.issue = stalled
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proof.test") as client:
        pending = asyncio.create_task(client.post(PATH, headers=HEADERS, json=payload()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
        finally:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert cancelled.is_set()
        database.issue = original
        assert (await client.post(PATH, headers=HEADERS, json=payload())).status_code == 200


def test_shared_auth_proof_lookup_absent_from_legacy_and_disabled_factories():
    app, _ = fixture()
    for application in (app, create_proof_ingress()):
        with TestClient(application, base_url="https://proof.test") as client:
            assert client.post(ingress.LOOKUP_PATH, headers=HEADERS, json=payload()).status_code == 404


@pytest.mark.parametrize("outcome", ["found", "missing", "revoked", "wrong_credential", "browser"])
@pytest.mark.asyncio
async def test_shared_auth_proof_lookup_uses_actual_adapter_without_issuance(monkeypatch, outcome):
    from app.shared_auth_proof import SharedLinkProofDatabase
    from tests.test_shared_auth_proof_adapter import Engine
    body = payload()
    original = ingress.FreshAuthSubmission.model_validate_json(json.dumps(body["proof"]))
    row = {**original.model_dump(), "issuer_id": "home-assistant:echo",
        "issued_at": original.authenticated_at,
        "expires_at": original.authenticated_at + timedelta(minutes=5)}
    if outcome == "missing":
        row = None
    elif outcome == "revoked":
        error = RuntimeError("private authority details")
        error.sqlstate = "42501"
        row = DBAPIError("private SQL", {}, error)
    engine = Engine(row)
    engine.expected_function = "identity.inspect_shared_link_auth_proof_v1"
    monkeypatch.setattr("app.shared_auth_proof.create_async_engine", lambda *a, **kw: engine)
    database = SharedLinkProofDatabase("postgresql://home_agent_shared_echo_proof_ingress@fixture/unused",
        issuer_id="home-assistant:echo")
    app = create_proof_ingress(binding=ProofIngressBinding(database.issuer_id, TOKEN.encode()), database=database)
    headers = dict(HEADERS)
    if outcome == "wrong_credential": headers["authorization"] = "Bearer " + "d" * 64
    if outcome == "browser": headers["origin"] = "https://home.test"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proof.test") as client:
        response = await client.post(ingress.LOOKUP_PATH, headers=headers, json=body)
    assert response.status_code == {"found": 200, "missing": 503, "revoked": 403,
        "wrong_credential": 401, "browser": 403}[outcome]
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
    assert engine.events.count("kernel") == (0 if outcome in ("wrong_credential", "browser") else 1)
    if outcome == "found": assert response.json()["proof"]["proof_id"] == body["proof"]["proof_id"]

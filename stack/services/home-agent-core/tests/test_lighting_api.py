"""Private lighting ingress: service transport, bearer binding and error mapping."""
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.lighting_api import PREFIX, SERVE_PREFIX, create_lighting_ingress
from app.personal_memory_api import PersonalMemoryBinding

from .test_lighting_service import Harness, authority

CREDENTIAL = b"e" * 64
AUTH = {"authorization": "Bearer " + CREDENTIAL.decode(), "content-type": "application/json"}


def envelope(request):
    return {"version": 1, "subject": "owner", "session_commitment": "c" * 64, "request": request}


def proposal():
    return {"version": 1, "operation_id": str(uuid.UUID(int=7)), "sites": ["echo", "victoria"],
            "targets": ["kitchen"], "operation": "off", "brightness": None}


def build(tmp_path, *, serve_credential=None, **kwargs):
    """Compose like lighting_server: the journal is opened inside the serving lifespan."""
    holder = {}

    @asynccontextmanager
    async def lifespan(app):
        harness = holder["harness"] = Harness(tmp_path, **kwargs)
        private = create_lighting_ingress(binding=PersonalMemoryBinding("home-assistant:echo", CREDENTIAL),
                                          service=harness.service, serve_credential=serve_credential)
        app.router.routes.extend(private.routes)
        try:
            yield
        finally:
            harness.journal.close()
    return FastAPI(lifespan=lifespan), holder


@pytest.fixture
def ingress(tmp_path):
    app, holder = build(tmp_path)
    with TestClient(app, base_url="https://core.internal") as client:
        yield client, holder["harness"]


def test_propose_confirm_and_outcome_round_trip(ingress):
    client, harness = ingress
    reply = client.post(PREFIX + "propose", json=envelope(proposal()), headers=AUTH)
    assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
    review = reply.json()["result"]["review"]
    confirm = client.post(PREFIX + "confirm", headers=AUTH, json=envelope(
        {"version": 1, "operation_id": review["operation_id"], "reviewed_digest": review["reviewed_digest"]}))
    assert confirm.json()["result"]["status"] == "done"
    outcome = client.post(PREFIX + "outcome", headers=AUTH, json=envelope(
        {"version": 1, "operation_id": review["operation_id"]}))
    assert outcome.json()["result"]["status"] == "done"
    assert sum(len(home.executed) for home in harness.homes.values()) == 2


@pytest.mark.parametrize("headers,status,code", [
    ({"content-type": "application/json"}, 401, "unauthorized"),
    ({**AUTH, "authorization": "Bearer " + "f" * 64}, 401, "unauthorized"),
    ({**AUTH, "origin": "https://home.example"}, 403, "service_transport_required"),
    ({**AUTH, "cookie": "a=b"}, 403, "service_transport_required"),
    ({**AUTH, "x-authenticated-user": "owner"}, 400, "identity_headers_forbidden"),
    ({**AUTH, "content-type": "text/plain"}, 415, "json_required"),
])
def test_only_the_bound_service_transport_is_accepted(ingress, headers, status, code):
    client, harness = ingress
    reply = client.post(PREFIX + "propose", content=b"{}", headers=headers)
    assert (reply.status_code, reply.json()["error"]) == (status, code)
    assert harness.admitted == 0


def test_plain_http_and_unknown_operations_are_refused(tmp_path):
    app, _ = build(tmp_path)
    with TestClient(app, base_url="http://core.internal") as client:
        assert client.post(PREFIX + "propose", json=envelope(proposal()), headers=AUTH).status_code == 403
    (tmp_path / "second").mkdir()
    app, _ = build(tmp_path / "second")
    with TestClient(app, base_url="https://core.internal") as client:
        assert client.post(PREFIX + "execute", json=envelope({}), headers=AUTH).status_code == 404


@pytest.mark.parametrize("request_body", [
    {**proposal(), "operation": "toggle"},
    {**proposal(), "entity_id": "light.kitchen"},
    {**proposal(), "targets": ["scene.movie"]},
    {**proposal(), "version": True},
])
def test_malformed_requests_are_rejected_before_any_work(ingress, request_body):
    client, harness = ingress
    reply = client.post(PREFIX + "propose", json=envelope(request_body), headers=AUTH)
    assert (reply.status_code, reply.json()["error"]) == (422, "invalid_request")
    assert harness.admitted == 0


def test_missing_consent_maps_to_a_typed_refusal(tmp_path):
    app, _ = build(tmp_path, current=authority(granted=()))
    with TestClient(app, base_url="https://core.internal") as client:
        reply = client.post(PREFIX + "propose", json=envelope(proposal()), headers=AUTH)
        assert (reply.status_code, reply.json()["error"]) == (403, "lighting_not_permitted")
        status = client.post(PREFIX + "status", json=envelope({}), headers=AUTH)
        assert status.json()["result"] == {"version": 1, "permitted": {"echo": False, "victoria": False},
                                           "homes": ["echo", "victoria"]}


def test_the_ingress_is_echo_only():
    with pytest.raises(TypeError):
        create_lighting_ingress(binding=PersonalMemoryBinding("home-assistant:victoria", CREDENTIAL), service=None)


SERVE_CREDENTIAL = b"f" * 64
SERVE_AUTH = {**AUTH, "authorization": "Bearer " + SERVE_CREDENTIAL.decode()}


def test_serve_actions_use_only_their_own_credential_and_journal(tmp_path):
    app, holder = build(tmp_path, serve_credential=SERVE_CREDENTIAL, current=authority(granted=()))
    with TestClient(app, base_url="https://core.internal") as client:
        action = proposal()
        operation = {"version": 1, "operation_id": action["operation_id"]}
        assert client.post(SERVE_PREFIX + "action", json=action, headers=AUTH).status_code == 401
        assert client.post(PREFIX + "propose", json=envelope(action), headers=SERVE_AUTH).status_code == 401
        assert client.post(SERVE_PREFIX + "action", json=action,
                           headers={**SERVE_AUTH, "cookie": "session=value"}).status_code == 403
        assert client.post(SERVE_PREFIX + "action", json=envelope(action), headers=SERVE_AUTH).status_code == 422
        first = client.post(SERVE_PREFIX + "action", json=action, headers=SERVE_AUTH)
        assert first.status_code == 200 and first.json()["result"]["status"] == "done"
        assert client.post(SERVE_PREFIX + "action", json=action, headers=SERVE_AUTH).json() == first.json()
        assert client.post(SERVE_PREFIX + "outcome", json=operation, headers=SERVE_AUTH).json() == first.json()
        assert client.post(SERVE_PREFIX + "action", json={**action, "operation": "on"},
                           headers=SERVE_AUTH).status_code == 409
        calls = [sent for home in holder["harness"].homes.values() for sent in home.executed]
        assert len(calls) == 2
        assert {call["request_id"] for call in calls} != {action["operation_id"]}
        assert len({call["request_id"] for call in calls}) == 1

    reopened, holder = build(tmp_path, serve_credential=SERVE_CREDENTIAL)
    with TestClient(reopened, base_url="https://core.internal") as client:
        assert client.post(SERVE_PREFIX + "outcome", json=operation, headers=SERVE_AUTH).json() == first.json()
        assert client.post(SERVE_PREFIX + "action", json=action, headers=SERVE_AUTH).json() == first.json()
        assert all(not home.executed for home in holder["harness"].homes.values())


def test_serve_clarification_retains_the_request_identity_without_sending(tmp_path):
    app, holder = build(tmp_path, serve_credential=SERVE_CREDENTIAL)
    with TestClient(app, base_url="https://core.internal") as client:
        action = {**proposal(), "targets": ["unknown room"]}
        result = client.post(SERVE_PREFIX + "action", json=action, headers=SERVE_AUTH).json()
        assert result["result"]["status"] == "clarify"
        operation = {"version": 1, "operation_id": action["operation_id"]}
        assert client.post(SERVE_PREFIX + "outcome", json=operation, headers=SERVE_AUTH).json() == result
        assert client.post(SERVE_PREFIX + "action", json=proposal(), headers=SERVE_AUTH).status_code == 409
        assert all(not home.executed for home in holder["harness"].homes.values())


def test_serve_routes_do_not_exist_without_their_credential(ingress):
    client, _ = ingress
    assert client.post(SERVE_PREFIX + "action", json=proposal(), headers=SERVE_AUTH).status_code == 404

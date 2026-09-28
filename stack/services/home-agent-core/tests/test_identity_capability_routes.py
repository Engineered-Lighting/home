"""Executable API revision gates with no database or production connections."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.api import semantic_router
from app.config import ReadinessMigration
from app.errors import DomainError
from app.identity_capabilities import supports_identity_capability
from app.owner_person_adapter import OwnerPersonAdapter
from app.owner_partner_adapter import OwnerPartnerAdapter
from app.parent_relationship_adapter import AuthenticatedParentRelationshipAdapter


BINDING = "0017_authenticated_binding_e5c"
PARENTS = ("0021_parent_status_e5h", "0027_owner_person_e5n", "0028_owner_partner_access_e5o",
           "0029_owner_person_role_e5p", "0030_relationship_vocabulary_e5q", "0031_relationship_uniqueness_e5r")
OWNERS = PARENTS[-2:]
UUID7 = "01900000-0000-7000-8000-000000000001"
UUID4 = "01900000-0000-4000-8000-000000000002"
TOKEN = "capability-route-fixture-token-at-least32chars"
HEADERS = {"Authorization": "Bearer " + TOKEN, "X-Authenticated-HA-User": "same-ha-user",
           "X-Authenticated-HA-Issuer": "home-assistant:echo", "X-Authenticated-Home-Site": "echo"}
ROUTES = [
    ("POST", "/v1/principal-binding-proposal/confirm", "binding_adapter", "commit", (BINDING,),
     {"proposal_digest": "a" * 64, "confirmation_nonce": UUID4}),
    ("GET", "/v1/parent-relationship-proposal", "parent_relationship_adapter", "status", PARENTS, None),
    ("POST", "/v1/parent-relationship-proposal", "parent_relationship_adapter", "stage", PARENTS,
     {"ceremony_id": UUID7}),
    ("POST", "/v1/parent-relationship-proposal/confirm", "parent_relationship_adapter", "commit", PARENTS,
     {"proposal_id": UUID7, "proposal_digest": "a" * 64, "confirmation_nonce": UUID4}),
    ("POST", "/v1/household-person", "owner_person_adapter", "create", OWNERS,
     {"ceremony_id": UUID7, "display_name": "Fixture person"}),
    ("POST", "/v1/partner-attestation", "owner_partner_adapter", "attest", OWNERS,
     {"ceremony_id": UUID7, "partner_person_id": UUID7, "attestation_nonce": UUID4}),
]


class AdapterReached(Exception):
    pass


def application(revision):
    app = FastAPI()
    app.state.settings = SimpleNamespace(readiness_migration=revision, service_token=SecretStr(TOKEN),
                                         ha_issuer_id="home-assistant:echo", site_id="echo")
    store = SimpleNamespace(principal_binding_commit_context=AsyncMock(return_value="reviewed-context"))
    app.state.operator_store = store
    calls = []

    def probe(adapter_name, method):
        async def called(**kwargs):
            calls.append((adapter_name, method, kwargs))
            raise AdapterReached()
        return called

    for _, _, name, method, _, _ in ROUTES:
        if not hasattr(app.state, name):
            setattr(app.state, name, SimpleNamespace())
        setattr(getattr(app.state, name), method, probe(name, method))

    @app.exception_handler(AdapterReached)
    async def accepted(_request, _error):
        return JSONResponse({"adapter_reached": True})

    @app.exception_handler(DomainError)
    async def domain(_request, error):
        return JSONResponse({"code": error.code}, status_code=error.status_code)

    app.include_router(semantic_router())
    return app, store, calls


@pytest.mark.parametrize("route,revision", [(route, revision) for route in ROUTES for revision in route[4]])
def test_reviewed_revision_reaches_only_its_typed_adapter(route, revision):
    method, path, name, operation, _, body = route
    app, store, calls = application(revision)
    with TestClient(app) as client:
        result = client.request(method, path, headers=HEADERS, json=body)
    assert result.status_code == 200
    assert len(calls) == 1
    assert calls[0][:2] == (name, operation)
    assert calls[0][2]["ha_user_id"] == "same-ha-user"
    if name == "binding_adapter":
        store.principal_binding_commit_context.assert_awaited_once_with("same-ha-user", "a" * 64)
        assert calls[0][2]["context"] == "reviewed-context"
    else:
        store.principal_binding_commit_context.assert_not_awaited()


DENIED = [(route, revision) for route in ROUTES
          for revision in (*ReadinessMigration.__args__, "0032_unreviewed_shared_identity", "9999_future")
          if revision not in route[4]]


@pytest.mark.parametrize("route,revision", DENIED)
def test_unreviewed_revision_denies_before_body_or_private_read(route, revision, monkeypatch):
    method, path, *_ = route
    app, store, calls = application(revision)

    async def forbidden_body(_request):
        raise AssertionError("disabled capability must not read its body")
    monkeypatch.setattr(Request, "body", forbidden_body)
    with TestClient(app) as client:
        result = client.request(method, path, headers=HEADERS, content=b"invalid private input")
    assert result.status_code == 409
    assert result.json() == {"code": "capability_disabled"}
    assert calls == []
    store.principal_binding_commit_context.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_missing_adapter_denies_before_body_or_private_read(route, monkeypatch):
    method, path, name, _, revisions, _ = route
    app, store, calls = application(revisions[-1])
    setattr(app.state, name, None)

    async def forbidden_body(_request):
        raise AssertionError("missing adapter must not read its body")
    monkeypatch.setattr(Request, "body", forbidden_body)
    with TestClient(app) as client:
        result = client.request(method, path, headers=HEADERS, content=b"invalid private input")
    assert result.status_code == 409
    assert calls == []
    store.principal_binding_commit_context.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_wrong_issuer_cannot_use_an_enabled_adapter(route, monkeypatch):
    method, path, _, _, revisions, _ = route
    app, store, calls = application(revisions[-1])

    async def forbidden_body(_request):
        raise AssertionError("wrong issuer must not read its body")
    monkeypatch.setattr(Request, "body", forbidden_body)
    with TestClient(app) as client:
        result = client.request(method, path, headers={**HEADERS, "X-Authenticated-HA-Issuer": "home-assistant:victoria"}, content=b"private")
    assert result.status_code == 401
    assert calls == []
    store.principal_binding_commit_context.assert_not_awaited()


def test_binding_commit_requires_operator_read_context_before_body(monkeypatch):
    app, _, calls = application(BINDING)
    app.state.operator_store = None

    async def forbidden_body(_request):
        raise AssertionError("missing operator context must not read its body")
    monkeypatch.setattr(Request, "body", forbidden_body)
    with TestClient(app) as client:
        result = client.post(ROUTES[0][1], headers=HEADERS, content=b"private")
    assert result.status_code == 409
    assert calls == []


def test_capability_matrix_is_explicit_and_keeps_binding_quarantined_after_0017():
    expected = {"principal_binding_confirmation": {BINDING}, "parent_relationship_confirmation": set(PARENTS),
                "owner_person_creation": set(OWNERS), "owner_relationship_attestation": set(OWNERS)}
    for capability, revisions in expected.items():
        for revision in (*ReadinessMigration.__args__, "0032_unreviewed_shared_identity", "9999_future"):
            assert supports_identity_capability(revision, capability) is (revision in revisions)
    assert not supports_identity_capability(OWNERS[-1], "unreviewed_capability")


def test_0031_real_owner_adapters_complete_http_responses_using_only_narrow_kernels():
    app, store, probes = application("0031_relationship_uniqueness_e5r")
    calls = []

    class NarrowOwnerDatabase:
        async def create_owner_person(self, call):
            calls.append(call)
            return call.person_id

        async def commit_owner_partner(self, call):
            calls.append(call)
            return call.receipt_id

    database = NarrowOwnerDatabase()
    app.state.owner_person_adapter = OwnerPersonAdapter(database)
    app.state.owner_partner_adapter = OwnerPartnerAdapter(database)
    with TestClient(app) as client:
        created = client.post("/v1/household-person", headers=HEADERS, json=ROUTES[4][5])
        attested = client.post("/v1/partner-attestation", headers=HEADERS,
                               json={**ROUTES[5][5], "predicate": "friend_of"})
    assert created.status_code == attested.status_code == 201
    assert created.json() == {"person_id": str(calls[0].person_id), "display_name": "Fixture person", "privacy_scope": "private"}
    assert attested.json() == {"receipt_id": str(calls[1].receipt_id), "partner_person_id": UUID7,
                              "document_digest": calls[1].document_digest, "subject_person_id": None,
                              "predicate": "friend_of"}
    assert all(call.authenticated_ha_user_id == "same-ha-user" for call in calls)
    assert len(calls[1].document_digest) == 64
    assert calls[0].person_id.version == calls[1].receipt_id.version == 7
    assert probes == []
    store.principal_binding_commit_context.assert_not_awaited()


def test_0031_real_parent_adapter_completes_confirm_and_status_http_responses():
    app, store, probes = application("0031_relationship_uniqueness_e5r")
    now = datetime(2026, 9, 26, tzinfo=UTC)
    calls = []

    class NarrowParentDatabase:
        async def commit(self, call):
            calls.append(call)
            return now

        async def status(self, ha_user_id):
            calls.append(ha_user_id)
            return SimpleNamespace(state="confirmed", confirmed_at=now)

    app.state.parent_relationship_adapter = AuthenticatedParentRelationshipAdapter(NarrowParentDatabase())
    with TestClient(app) as client:
        committed = client.post("/v1/parent-relationship-proposal/confirm", headers=HEADERS, json=ROUTES[3][5])
        recovered = client.get("/v1/parent-relationship-proposal", headers=HEADERS)
    assert committed.status_code == recovered.status_code == 200
    for result in (committed.json(), recovered.json()):
        assert result["state"] == "confirmed"
        assert result["fact_count"] == 2
        assert result["confirmed_at"] == "2026-09-26T00:00:00Z"
        assert result["location_memory_enabled"] is False
        assert result["travel_greetings_enabled"] is False
    assert calls[0].authenticated_ha_user_id == calls[1] == "same-ha-user"
    assert str(calls[0].proposal_id) == UUID7
    assert probes == []
    store.principal_binding_commit_context.assert_not_awaited()

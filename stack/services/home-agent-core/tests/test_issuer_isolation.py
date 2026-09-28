"""Deterministic Core ingress contracts; no PostgreSQL or external HA calls."""
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api import native_principal_from, principal_from
from app.auth import ServiceIdentity, require_edge, require_service_bearer, require_service_identity
from app.config import Settings
from app.errors import AuthenticationError
from app.main import trusted_maintenance_gated_caller


TOKEN = "issuer-isolation-test-service-token-123456"
CLAIMS = {"X-Authenticated-HA-Issuer": "home-assistant:echo", "X-Authenticated-Home-Site": "echo"}
AUTH = {"Authorization": "Bearer " + TOKEN, "X-Authenticated-HA-User": "same-id-in-both-homes"}


def settings(**updates):
    return Settings(
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
        knowledge_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
        service_token=TOKEN, policy_digest="a" * 64, **updates,
    )


def probe_app():
    application = FastAPI()
    application.state.settings = settings()
    store = SimpleNamespace(resolve_principal=AsyncMock(return_value={"principal_id": "existing-echo-owner"}))

    @application.exception_handler(AuthenticationError)
    async def unauthorized(_request, _error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @application.get("/probe")
    async def probe(identity=Depends(require_service_identity)):
        principal = await principal_from(identity, store)
        return {**principal, "issuer": identity.ha_issuer_id, "site": identity.site_id}

    return application, store


@pytest.mark.parametrize("claims", [{}, CLAIMS])
def test_legacy_and_explicit_echo_bind_existing_principal_only(claims):
    application, store = probe_app()
    with TestClient(application) as client:
        response = client.get("/probe", headers={**AUTH, **claims})
    assert response.status_code == 200
    assert response.json() == {"principal_id": "existing-echo-owner", "issuer": "home-assistant:echo", "site": "echo"}
    store.resolve_principal.assert_awaited_once_with("same-id-in-both-homes")


@pytest.mark.parametrize("claims", [
    {"X-Authenticated-HA-Issuer": "home-assistant:victoria", "X-Authenticated-Home-Site": "victoria"},
    {**CLAIMS, "X-Authenticated-HA-Issuer": "unknown"},
    {**CLAIMS, "X-Authenticated-Home-Site": "victoria"},
    {"X-Authenticated-HA-Issuer": "home-assistant:echo"},
    {"X-Authenticated-Home-Site": "echo"},
    {**CLAIMS, "X-Authenticated-HA-Issuer": ""},
])
def test_wrong_unknown_partial_or_empty_issuer_never_reaches_legacy_resolver(claims):
    application, store = probe_app()
    with TestClient(application) as client:
        response = client.get("/probe", headers={**AUTH, **claims})
    assert response.status_code == 401
    store.resolve_principal.assert_not_awaited()


def test_duplicate_identity_claims_are_not_collapsed_into_authority():
    application, store = probe_app()
    headers = [*AUTH.items(), *CLAIMS.items(), ("X-Authenticated-HA-Issuer", "home-assistant:echo")]
    with TestClient(application) as client:
        assert client.get("/probe", headers=headers).status_code == 401
    store.resolve_principal.assert_not_awaited()


def test_claims_alone_cannot_authenticate_an_issuer():
    application, store = probe_app()
    with TestClient(application) as client:
        assert client.get("/probe", headers={**AUTH, **CLAIMS, "Authorization": "Bearer wrong"}).status_code == 401
    store.resolve_principal.assert_not_awaited()


@pytest.mark.parametrize("updates", [
    {"site_id": "victoria"}, {"ha_issuer_id": "home-assistant:victoria"},
    {"ha_issuer_id": "https://ha-over-tailscale.example"}, {"ha_issuer_id": ""},
])
def test_settings_cannot_rebind_bare_ha_namespace_to_another_issuer(updates):
    with pytest.raises(ValidationError):
        settings(**updates)


@pytest.mark.parametrize("resolver", [principal_from, native_principal_from])
def test_future_or_injected_victoria_identity_still_cannot_enter_legacy_resolver(resolver):
    store = SimpleNamespace(resolve_principal=AsyncMock())
    with pytest.raises(AuthenticationError):
        asyncio.run(resolver(ServiceIdentity("same-id-in-both-homes", "home-assistant:victoria", "victoria"), store))
    store.resolve_principal.assert_not_awaited()


def test_maintenance_audience_recognition_obeys_same_issuer_boundary():
    application, _ = probe_app()
    for claims, expected in (({}, True), (CLAIMS, True), ({**CLAIMS, "X-Authenticated-Home-Site": "victoria"}, False)):
        headers = [(key.lower().encode(), value.encode()) for key, value in {**AUTH, **claims}.items()]
        request = Request({"type": "http", "method": "GET", "path": "/probe", "app": application, "headers": headers})
        assert trusted_maintenance_gated_caller(request, application.state.settings) is expected


@pytest.mark.parametrize("dependency", [require_edge, require_service_bearer])
def test_source_and_service_credentials_cannot_carry_other_home_claims(dependency):
    application, _ = probe_app()
    application.state.settings = application.state.settings.model_copy(update={"edge_token": SecretStr(TOKEN)})
    calls = []

    @application.post("/source", dependencies=[Depends(dependency)])
    async def source():
        calls.append(True)
        return {"accepted": True}

    with TestClient(application) as client:
        assert client.post("/source", headers={**AUTH, **CLAIMS, "X-Authenticated-Home-Site": "victoria"}).status_code == 401
        assert not calls
        assert client.post("/source", headers=AUTH).status_code == 200
        assert len(calls) == 1

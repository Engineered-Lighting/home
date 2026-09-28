from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.personal_memory_api import PersonalMemoryBinding, PREFIX, create_personal_memory_ingress
from app.personal_memory_service import PersonalMemoryService
from app.personal_memory_consent_service import PreferenceConsentService


def setup():
    preferences = object.__new__(PersonalMemoryService)
    consent = object.__new__(PreferenceConsentService)
    for operation in ("propose", "confirm", "outcome"):
        setattr(consent, operation, AsyncMock(return_value={"status": "fixture"}))
    app = create_personal_memory_ingress(binding=PersonalMemoryBinding("home-assistant:echo", b"a"*64),
                                         service=preferences, consent=consent)
    return app, consent


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["propose", "confirm", "outcome"])
async def test_sharing_routes_use_server_bound_issuer_and_typed_scope(operation):
    app, service = setup()
    operation_id = uuid4()
    request = {"version": 1, "operation_id": str(operation_id)}
    if operation == "confirm": request["reviewed_digest"] = "c"*64
    body = {"version": 1, "subject": "owner", "session_commitment": "b"*64, "request": request}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
        response = await client.post(PREFIX+"sharing-"+operation, json=body, headers={"authorization": "Bearer "+"a"*64})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    args = getattr(service, operation).await_args.args
    assert args[0] == {"issuer_id": "home-assistant:echo", "subject": "owner", "session_commitment": "b"*64}
    assert (args[1].operation_id if operation == "confirm" else args[1]) == operation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"source": "camera.memory"}, {"authority": {}},
    {"capability": "lighting.execute"}, {"version": True}, {"operation_id": "not-an-id"}])
async def test_browser_cannot_expand_sharing_scope(patch):
    app, service = setup()
    body = {"version": 1, "subject": "owner", "session_commitment": "b"*64,
            "request": {"version": 1, "operation_id": str(uuid4()), **patch}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
        response = await client.post(PREFIX+"sharing-propose", json=body, headers={"authorization": "Bearer "+"a"*64})
    assert response.status_code == 422
    service.propose.assert_not_awaited()


def test_victoria_binding_cannot_be_silently_used_as_echo_consent_authority():
    _, consent = setup()
    with pytest.raises(TypeError):
        create_personal_memory_ingress(binding=PersonalMemoryBinding("home-assistant:victoria", b"a"*64),
                                      service=object.__new__(PersonalMemoryService), consent=consent)


@pytest.mark.asyncio
async def test_existing_preference_configuration_does_not_enable_sharing():
    app = create_personal_memory_ingress(binding=PersonalMemoryBinding("home-assistant:echo", b"a"*64),
                                        service=object.__new__(PersonalMemoryService))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
        response = await client.post(PREFIX+"sharing-propose")
    assert response.status_code == 503
    assert response.json()["error"] == "preference_sharing_disabled"

from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest

from app.personal_memory_api import PersonalMemoryBinding, create_personal_memory_ingress, PREFIX
from app.personal_memory_service import PersonalMemoryService


def setup(issuer="home-assistant:echo"):
    service=object.__new__(PersonalMemoryService)
    for name in ("read","propose","confirm","outcome"):
        setattr(service,name,AsyncMock(return_value={"status":"fixture"}))
    app=create_personal_memory_ingress(binding=PersonalMemoryBinding(issuer,b"a"*64),service=service)
    return app,service


def payload(operation="read"):
    value={"version":1,"subject":"owner","session_commitment":"b"*64,"request":{}}
    if operation=="propose":
        value["request"]={"version":1,"operation_id":str(uuid4()),"operation":"remember",
            "expected_revision":0,"preference":{"value":"warm"}}
    if operation in ("confirm","outcome"):
        value["request"]={"version":1,"operation_id":str(uuid4()),"reviewed_digest":"c"*64}
    if operation=="confirm": value["gesture_id"]=str(uuid4())
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["read","propose","confirm","outcome"])
async def test_private_operations_fix_issuer_and_parse_typed_requests(operation):
    app,service=setup("home-assistant:victoria")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://core.test") as client:
        response=await client.post(PREFIX+operation,json=payload(operation),headers={"authorization":"Bearer "+"a"*64})
    assert response.status_code==200 and response.headers["cache-control"]=="no-store"
    call=getattr(service,operation).await_args
    assert call.args[0]==dict(issuer_id="home-assistant:victoria",subject="owner",session_commitment="b"*64)
    assert "owner" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("patch",[{"issuer_id":"home-assistant:victoria"},{"principal_id":str(uuid4())},
    {"version":True},{"subject":" owner"},{"request":{"site_id":"victoria"}}])
async def test_caller_cannot_supply_identity_or_scope(patch):
    app,service=setup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://core.test") as client:
        response=await client.post(PREFIX+"read",json={**payload(),**patch},headers={"authorization":"Bearer "+"a"*64})
    assert response.status_code==422
    service.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("base,extra,status",[("http://core.test",{},403),
    ("https://core.test",{"origin":"https://home.test"},403),
    ("https://core.test",{"cookie":"session=anything"},403),
    ("https://core.test",{"x-authenticated-user":"owner"},400),
    ("https://core.test",{"authorization":"Bearer wrong"},401)])
async def test_private_transport_boundary(base,extra,status):
    app,service=setup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=base) as client:
        response=await client.post(PREFIX+"read",json=payload(),headers={"authorization":"Bearer "+"a"*64,**extra})
    assert response.status_code==status
    service.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_failure_reports_unknown_without_reexecution_or_error_contents():
    app,service=setup()
    service.confirm.side_effect=RuntimeError("private database details")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://core.test") as client:
        response=await client.post(PREFIX+"confirm",json=payload("confirm"),headers={"authorization":"Bearer "+"a"*64})
    assert response.status_code==503 and response.json()["error"]=="personal_memory_outcome_unknown"
    assert "private database" not in response.text
    assert service.confirm.await_count==1


@pytest.mark.asyncio
async def test_unprovisioned_ingress_stays_closed():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_personal_memory_ingress()),base_url="https://core.test") as client:
        response=await client.post(PREFIX+"read",json=payload())
    assert response.status_code==503

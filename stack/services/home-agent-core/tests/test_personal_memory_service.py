from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.errors import ForbiddenError
from app.personal_memory_contract import PreferenceReviewCommitment
from app.personal_memory_service import PersonalMemoryService
from app.personal_memory_storage import PersonalMemoryStorage
from app.store import CoreStore


def fixture():
    class Database:
        @asynccontextmanager
        async def transaction(self, *, serializable):
            assert serializable
            yield connection
    connection=SimpleNamespace(execute=AsyncMock())
    store=object.__new__(CoreStore)
    store.database=Database()
    storage=PersonalMemoryStorage(PreferenceReviewCommitment(b"k"*32),policy_digest="a"*64,policy_version="fixture")
    storage.resolve_authority=AsyncMock(return_value=SimpleNamespace(principal_id=uuid4()))
    storage.read=AsyncMock(side_effect=[{"preference":"old"},{"preference":None}])
    admission=AsyncMock()
    service=PersonalMemoryService(store=store,storage=storage,admission=admission)
    service.fixture_connection=connection
    return service,storage,admission


SESSION=dict(issuer_id="home-assistant:echo",subject="owner",session_commitment="b"*64)


@pytest.mark.asyncio
async def test_delivery_reads_current_value_instead_of_returning_deleted_snapshot():
    service,storage,_=fixture()
    assert await service.read(SESSION)=={"preference":None}
    assert storage.resolve_authority.await_count==2


@pytest.mark.asyncio
async def test_revocation_between_retrieval_and_delivery_suppresses_private_value():
    service,storage,_=fixture()
    storage.resolve_authority.side_effect=[storage.resolve_authority.return_value,ForbiddenError("revoked")]
    with pytest.raises(ForbiddenError): await service.read(SESSION)
    assert storage.read.await_count==1


@pytest.mark.asyncio
async def test_restore_gate_closure_before_delivery_suppresses_private_value():
    service,storage,admission=fixture()
    admission.side_effect=[None,ForbiddenError("restore quarantine")]
    with pytest.raises(ForbiddenError): await service.read(SESSION)
    assert storage.read.await_count==1


@pytest.mark.asyncio
async def test_service_rejects_caller_owner_claim_before_database_lookup():
    service,storage,_=fixture()
    with pytest.raises(ForbiddenError): await service.read({**SESSION,"principal_id":"caller-owner"})
    storage.resolve_authority.assert_not_awaited()
    service.fixture_connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_rls_scope_uses_governed_owner_not_authenticated_subject():
    service,storage,_=fixture()
    await service.read(SESSION)
    calls=service.fixture_connection.execute.await_args_list
    assert len(calls)==2
    for call in calls:
        assert str(call.args[0])=="SELECT set_config('app.principal_id',:principal,true)"
        assert call.args[1]=={"principal":str(storage.resolve_authority.return_value.principal_id)}
        assert call.args[1]["principal"]!=SESSION["subject"]


@pytest.mark.asyncio
async def test_failed_lookup_never_establishes_rls_scope():
    service,storage,_=fixture()
    storage.resolve_authority.side_effect=ForbiddenError("unlinked")
    with pytest.raises(ForbiddenError):
        await service.read(SESSION)
    service.fixture_connection.execute.assert_not_awaited()

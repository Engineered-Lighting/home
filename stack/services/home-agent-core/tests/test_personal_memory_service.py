from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

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
            yield object()
    store=object.__new__(CoreStore)
    store.database=Database()
    storage=PersonalMemoryStorage(PreferenceReviewCommitment(b"k"*32),policy_digest="a"*64,policy_version="fixture")
    storage.resolve_authority=AsyncMock(return_value=object())
    storage.read=AsyncMock(side_effect=[{"preference":"old"},{"preference":None}])
    admission=AsyncMock()
    return PersonalMemoryService(store=store,storage=storage,admission=admission),storage,admission


SESSION=dict(issuer_id="home-assistant:echo",subject="owner",session_commitment="b"*64)


@pytest.mark.asyncio
async def test_delivery_reads_current_value_instead_of_returning_deleted_snapshot():
    service,storage,_=fixture()
    assert await service.read(SESSION)=={"preference":None}
    assert storage.resolve_authority.await_count==2


@pytest.mark.asyncio
async def test_revocation_between_retrieval_and_delivery_suppresses_private_value():
    service,storage,_=fixture()
    storage.resolve_authority.side_effect=[object(),ForbiddenError("revoked")]
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

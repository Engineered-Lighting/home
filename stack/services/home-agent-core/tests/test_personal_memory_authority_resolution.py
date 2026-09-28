"""Focused authority lease and capability regression checks."""
from datetime import timedelta
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from app.errors import ForbiddenError
from app.personal_memory_storage import PersonalMemoryStorage
from .test_personal_memory_contract import fixture, NOW


def setup():
    signer,_,authority,_,_,_=fixture()
    authority=authority.model_copy(update={"valid_until":NOW+timedelta(seconds=50)})
    storage=PersonalMemoryStorage(signer,policy_digest="a"*64,policy_version="test")
    storage._lookup_authority=AsyncMock(return_value=authority)
    async def execute(statement,params=None):
        if str(statement)=="SHOW transaction_isolation":
            return SimpleNamespace(scalar_one=lambda:"serializable")
        return SimpleNamespace(scalar_one=lambda:NOW)
    connection=SimpleNamespace(execute=AsyncMock(side_effect=execute))
    return storage,connection,authority


@pytest.mark.asyncio
async def test_read_under_write_review_rechecks_original_write_grants():
    storage,connection,authority=setup()
    await storage._admit(connection,authority,write=False)
    assert storage._lookup_authority.await_args.kwargs["write"] is True
    assert storage._lookup_authority.await_args.kwargs["subject"]==authority.subject


@pytest.mark.asyncio
async def test_read_authority_never_upgrades_to_write():
    storage,connection,authority=setup()
    with pytest.raises(ForbiddenError):
        await storage._admit(connection,authority.model_copy(update={"access":"read"}),write=True)
    storage._lookup_authority.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value",[("subject","other"),("echo_grant_revision",7),("authorization_generation",9)])
async def test_changed_identity_or_grant_invalidates_existing_review(field,value):
    storage,connection,authority=setup()
    storage._lookup_authority.return_value=authority.model_copy(update={field:value})
    with pytest.raises(ForbiddenError):
        await storage._admit(connection,authority,write=True)
    assert not any("pg_advisory_xact_lock" in str(call.args[0]) for call in connection.execute.await_args_list)


@pytest.mark.asyncio
async def test_retained_review_cannot_renew_its_authority_lease():
    storage,connection,authority=setup()
    storage._lookup_authority.return_value=authority.model_copy(update={"valid_until":NOW+timedelta(seconds=60)})
    resolved=await storage.resolve_authority(connection,issuer_id=authority.issuer_id,
        subject=authority.subject,session_commitment=authority.session_commitment,write=True,retained=authority)
    assert resolved.valid_until==authority.valid_until

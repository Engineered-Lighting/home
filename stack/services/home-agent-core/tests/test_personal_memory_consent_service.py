from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.crypto import FieldCipher
from app.errors import ForbiddenError
from app.personal_memory_consent import SharingConfirmation
from app.personal_memory_consent_journal import ConsentJournal
from app.personal_memory_consent_service import ConsentDatabase, PreferenceConsentService
from app.personal_memory_grants import PreferenceGrantStorage
from .test_personal_memory_consent import fixture, NOW


def setup(tmp_path):
    signer, authority, _, _ = fixture()
    database = object.__new__(ConsentDatabase)
    events = []
    @asynccontextmanager
    async def transaction():
        events.append("begin")
        try:
            yield object()
        except BaseException:
            events.append("rollback")
            raise
        else: events.append("commit")
    database.transaction = transaction
    storage = PreferenceGrantStorage(signer)
    storage.resolve = AsyncMock(return_value=authority)
    storage.confirm = AsyncMock()
    storage.outcome = AsyncMock(return_value="committed")
    journal = ConsentJournal(tmp_path/"consent.sqlite", cipher=FieldCipher(b"j"*32))
    service = PreferenceConsentService(database=database, storage=storage, journal=journal,
        admission=AsyncMock(), grant_lifetime=timedelta(days=30), now=lambda: NOW)
    session = {"issuer_id": "home-assistant:echo", "subject": authority.subject,
               "session_commitment": authority.session_commitment}
    return service, session, events


@pytest.mark.asyncio
async def test_review_is_stable_and_duplicate_confirmation_looks_up_without_writing(tmp_path):
    service, session, events = setup(tmp_path)
    try:
        operation = uuid4()
        review = await service.propose(session, operation)
        assert await service.propose(session, operation) == review
        service.storage.confirm.assert_not_awaited()
        confirmation = SharingConfirmation(operation_id=operation, reviewed_digest=review.reviewed_digest)
        first = await service.confirm(session, confirmation)
        second = await service.confirm(session, confirmation)
        assert first == second and first["status"] == "committed"
        service.storage.confirm.assert_awaited_once()
        assert service.storage.outcome.await_count == 4
        assert "rollback" not in events
    finally: service.journal.close()


@pytest.mark.asyncio
async def test_uncertain_write_remains_lookup_only_even_if_no_receipt_is_found(tmp_path):
    service, session, events = setup(tmp_path)
    try:
        review = await service.propose(session, uuid4())
        confirmation = SharingConfirmation(operation_id=review.operation_id, reviewed_digest=review.reviewed_digest)
        service.storage.confirm.side_effect = ConnectionError("unknown commit")
        with pytest.raises(ConnectionError): await service.confirm(session, confirmation)
        service.storage.outcome.return_value = "unknown"
        assert (await service.confirm(session, confirmation))["status"] == "unknown"
        service.storage.confirm.assert_awaited_once()
        assert events.count("rollback") == 1
    finally: service.journal.close()


@pytest.mark.asyncio
async def test_revocation_before_outcome_delivery_suppresses_success_without_redispatch(tmp_path):
    service, session, _ = setup(tmp_path)
    try:
        review = await service.propose(session, uuid4())
        confirmation = SharingConfirmation(operation_id=review.operation_id, reviewed_digest=review.reviewed_digest)
        service.storage.outcome.side_effect = ["committed", ValueError("revoked")]
        with pytest.raises(ValueError): await service.confirm(session, confirmation)
        service.storage.confirm.assert_awaited_once()
    finally: service.journal.close()


@pytest.mark.asyncio
async def test_wrong_issuer_or_changed_digest_cannot_dispatch(tmp_path):
    service, session, _ = setup(tmp_path)
    try:
        with pytest.raises(ForbiddenError): await service.propose({**session, "issuer_id": "home-assistant:victoria"}, uuid4())
        review = await service.propose(session, uuid4())
        with pytest.raises(ValueError):
            await service.confirm(session, SharingConfirmation(operation_id=review.operation_id, reviewed_digest="f"*64))
        service.storage.confirm.assert_not_awaited()
    finally: service.journal.close()


@pytest.mark.parametrize("url", ["postgresql://postgres@localhost/core", "postgresql://home_agent_owner@localhost/core",
    "postgresql://home_agent_preference_consent@localhost/core?user=postgres",
    "postgresql://home_agent_preference_consent@localhost/core?sslmode=disable"])
def test_database_cannot_fall_back_to_admin_or_url_overrides(url):
    with pytest.raises(ValueError): ConsentDatabase(url)

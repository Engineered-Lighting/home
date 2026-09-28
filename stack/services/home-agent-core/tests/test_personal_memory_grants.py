"""Deterministic write/lookup behavior; PostgreSQL permissions need hosted gates."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.errors import ForbiddenError
from app.personal_memory_grants import PreferenceGrantStorage, ROLE
from .test_personal_memory_consent import fixture, NOW


class Result:
    def __init__(self, value): self.value = value
    def scalar_one(self): return self.value
    def scalar_one_or_none(self): return self.value
    def one(self): return self.value


class Connection:
    def __init__(self, *, fail_insert=False, late=False):
        self.calls = []
        self.fail_insert, self.late = fail_insert, late
        self.clock_reads = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "clock_timestamp()" in sql:
            self.clock_reads += 1
            return Result(NOW+timedelta(seconds=60) if self.late and self.clock_reads > 1 else NOW)
        if "COALESCE(max(revision)" in sql: return Result(8)
        if "INSERT INTO" in sql and self.fail_insert: raise ConnectionError("uncertain write")
        return Result(None)


@pytest.mark.asyncio
async def test_writer_creates_only_four_reviewed_grants_and_keeps_monotonic_revision():
    signer, authority, review, confirmation = fixture()
    storage = PreferenceGrantStorage(signer)
    storage.resolve = AsyncMock(return_value=authority)
    connection = Connection()
    await storage.confirm(connection, authority=authority, review=review, confirmation=confirmation)
    inserts = [params for sql, params in connection.calls if "INSERT INTO" in sql]
    assert len(inserts) == 4 and len({row["id"] for row in inserts}) == 4
    assert {(row["site"], row["capability"]) for row in inserts} == {
        (site, capability) for site in ("echo", "victoria") for capability in ("memory.read", "personal_memory.write")}
    assert all(row["revision"] == 8 and row["expiry"] == review.grants_expire_at and
               row["link"] == authority.link_id and row["source"] == "core.personal-preferences.v1" for row in inserts)
    assert not any("DELETE" in sql for sql, _ in connection.calls)


@pytest.mark.asyncio
async def test_conflicting_authority_fails_before_any_mutation():
    signer, authority, review, confirmation = fixture()
    storage = PreferenceGrantStorage(signer)
    storage.resolve = AsyncMock(return_value=authority.model_copy(update={"authorization_generation": 2}))
    connection = Connection()
    with pytest.raises(ForbiddenError):
        await storage.confirm(connection, authority=authority, review=review, confirmation=confirmation)
    assert not connection.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["uncertain", "expired"])
async def test_write_failure_propagates_for_transaction_rollback_without_retry(failure):
    signer, authority, review, confirmation = fixture()
    storage = PreferenceGrantStorage(signer)
    storage.resolve = AsyncMock(return_value=authority)
    connection = Connection(fail_insert=failure == "uncertain", late=failure == "expired")
    with pytest.raises(ConnectionError if failure == "uncertain" else ForbiddenError):
        await storage.confirm(connection, authority=authority, review=review, confirmation=confirmation)
    assert sum("INSERT INTO" in sql for sql, _ in connection.calls) == (1 if failure == "uncertain" else 4)
    storage.resolve.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, 0, 3])
async def test_outcome_is_read_only_and_requires_all_four_committed_rows(missing):
    signer, authority, review, _ = fixture()
    storage = PreferenceGrantStorage(signer)
    anchor = authority.model_dump(include={"link_id", "principal_id", "person_id", "link_revision", "authorization_generation"})
    storage._anchor = AsyncMock(return_value=anchor)
    calls = []
    async def execute(statement, params):
        assert str(statement).lstrip().startswith("SELECT")
        calls.append(params)
        return Result(None if missing == len(calls)-1 else params["id"])
    connection = Connection()
    connection.execute = execute
    result = await storage.outcome(connection, subject=authority.subject,
        session_commitment=authority.session_commitment, authority=authority, review=review)
    assert result == ("committed" if missing is None else "unknown")
    assert len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("role,isolation", [("home_agent_api", "serializable"),
    ("home_agent_owner", "serializable"), ("postgres", "serializable"), (ROLE, "read committed")])
async def test_normal_api_administrators_and_weak_transactions_cannot_write_grants(role, isolation):
    signer, authority, _, _ = fixture()
    storage = PreferenceGrantStorage(signer)
    connection = Connection()
    connection.execute = AsyncMock(return_value=Result((role, role, isolation, True)))
    with pytest.raises(ForbiddenError):
        await storage.resolve(connection, subject=authority.subject, session_commitment=authority.session_commitment)
    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_correct_role_name_with_unsafe_privileges_is_rejected():
    signer, authority, _, _ = fixture()
    storage = PreferenceGrantStorage(signer)
    connection = Connection()
    connection.execute = AsyncMock(return_value=Result((ROLE, ROLE, "serializable", False)))
    with pytest.raises(ForbiddenError):
        await storage.resolve(connection, subject=authority.subject, session_commitment=authority.session_commitment)
    connection.execute.assert_awaited_once()

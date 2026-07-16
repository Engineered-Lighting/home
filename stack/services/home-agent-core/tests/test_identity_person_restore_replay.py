from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from app.crypto import sha256_json
from app.ledger import (
    IDENTITY_PERSON_ERASURE_OPERATION_CODES,
    IDENTITY_PERSON_ERASURE_RESIDUAL_CODES,
    IdentityPersonErasureLedgerRecord,
    LedgerEntry,
    LedgerHead,
    LedgerIntegrityError,
)
from app.restore import RestoreReplay, identity_person_restore_payload


class _Result:
    def __init__(self, *, mapping=None, scalar=None) -> None:
        self.mapping = mapping
        self.scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self.mapping

    def scalar_one(self):
        return self.scalar


class _Connection:
    def __init__(self, entry: LedgerEntry) -> None:
        self.entry = entry
        self.receipt = None
        self.kernel_calls = 0
        self.kernel_payloads: list[dict] = []

    async def execute(self, statement):
        sql = str(statement)
        if sql.startswith("SELECT") and "operations.erasure_replay_receipts" in sql:
            return _Result(mapping=self.receipt)
        if "replay_identity_person_retrieval_block_v2" in sql:
            compiled = statement.compile(dialect=postgresql.dialect())
            payload = next(
                value
                for value in compiled.params.values()
                if isinstance(value, dict) and value.get("version") == 2
            )
            self.kernel_calls += 1
            self.kernel_payloads.append(payload)
            return _Result(scalar=self.entry.record.block_id)
        if sql.startswith("INSERT INTO operations.erasure_replay_receipts"):
            self.receipt = {
                "outbox_id": self.entry.record.outbox_id,
                "erasure_request_id": self.entry.record.erasure_request_id,
                "record_hash": self.entry.record_hash,
                "record_digest": self.entry.record_digest,
            }
            return _Result()
        if sql.startswith("UPDATE operations.outbox"):
            return _Result()
        raise AssertionError(f"unexpected replay statement: {sql}")


class _Database:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.scopes: list[dict] = []

    @asynccontextmanager
    async def transaction(self, **scope):
        self.scopes.append(scope)
        yield self.connection


class _Ledger:
    def __init__(self, entry: LedgerEntry) -> None:
        self.entry = entry

    def head(self) -> LedgerHead:
        return LedgerHead(self.entry.epoch, self.entry.record_hash)

    def entries_after(self, epoch: int) -> list[LedgerEntry]:
        return [self.entry] if epoch < self.entry.epoch else []


def _entry() -> LedgerEntry:
    record = IdentityPersonErasureLedgerRecord(
        outbox_id=uuid.UUID("01900000-0000-7000-8000-000000000001"),
        erasure_request_id=uuid.UUID("01900000-0000-7000-8000-000000000002"),
        person_id=uuid.UUID("01900000-0000-7000-8000-000000000003"),
        operation_id=uuid.UUID("01900000-0000-7000-8000-000000000004"),
        block_id=uuid.UUID("01900000-0000-7000-8000-000000000005"),
        block_commitment="b" * 64,
        operation_codes=list(IDENTITY_PERSON_ERASURE_OPERATION_CODES),
        policy_digest="c" * 64,
        completed_at=datetime(2026, 7, 13, 2, tzinfo=UTC),
        source_created_at=datetime(2026, 7, 13, 1, tzinfo=UTC),
        external_pending_codes=[],
        legacy_untracked_codes=[],
        exact_residual_codes=list(IDENTITY_PERSON_ERASURE_RESIDUAL_CODES),
        checkpoint_affected=True,
        backup_expiry_at=None,
    )
    return LedgerEntry(
        epoch=1,
        record_hash="e" * 64,
        record_digest=sha256_json(record.model_dump(mode="json")),
        record=record,
    )


@pytest.mark.asyncio
async def test_v2_restore_replays_tombstone_and_reverifies_existing_receipt(
    monkeypatch,
) -> None:
    entry = _entry()
    connection = _Connection(entry)
    database = _Database(connection)
    replay = RestoreReplay(database, _Ledger(entry))  # type: ignore[arg-type]
    state = RestoreReplay._DatabaseState(0, "0" * 64)

    async def database_state():
        return state

    async def set_database_state(_connection, head):
        nonlocal state
        state = RestoreReplay._DatabaseState(head.epoch, head.head_hash)

    monkeypatch.setattr(replay, "_database_state", database_state)
    monkeypatch.setattr(replay, "_set_database_state", set_database_state)

    assert await replay.replay() == 1
    assert connection.kernel_calls == 1
    assert connection.kernel_payloads == [identity_person_restore_payload(entry)]
    assert database.scopes == [{"principal_id": None, "serializable": True}]

    # A restored replay receipt is not trusted as a substitute for the
    # canonical tombstone. Resetting only the database ledger head causes the
    # idempotent database kernel to verify/recreate that tombstone again.
    state = RestoreReplay._DatabaseState(0, "0" * 64)
    database.scopes.clear()

    assert await replay.replay() == 0
    assert connection.kernel_calls == 2
    assert connection.kernel_payloads[-1] == identity_person_restore_payload(entry)
    assert database.scopes == [{"principal_id": None, "serializable": True}]

    state = RestoreReplay._DatabaseState(0, "0" * 64)
    connection.receipt["record_digest"] = "f" * 64
    with pytest.raises(LedgerIntegrityError, match="receipt conflicts"):
        await replay.replay()
    assert connection.kernel_calls == 2

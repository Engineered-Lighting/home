"""Real local SQLite recovery checks; no HA or PostgreSQL acceptance."""
import asyncio
import sqlite3
from uuid import uuid4
from unittest.mock import AsyncMock
from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidTag

from app.crypto import FieldCipher
from app.shared_link_journal import SharedLinkJournal
from app.shared_link_issuance import SharedLinkIssuanceReceipt
from app import shared_link_issuance as issuance
from app.auth import ServiceIdentity
from app.errors import ForbiddenError
from app.shared_link_commitments import SharedLinkBegin, SharedLinkCeremonyContext
from .test_shared_link_commitments import submission
from .test_shared_link_issuance_adapter import database, NOW


def journal(path, key=b"j"*32, **kwargs):
    return SharedLinkJournal(path, cipher=FieldCipher(key), now=lambda: NOW, **kwargs)


def test_shared_link_journal_encrypts_and_preserves_exact_request_after_reopen(tmp_path):
    path = tmp_path / "requests.sqlite"
    value = submission()
    store = journal(path)
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert store._db.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert store.retain(value) == "prepared"
    store.close()
    assert value.echo_subject.encode() not in path.read_bytes()
    store = journal(path)
    try:
        assert store.claim(value.ceremony_id) == value
        assert store.retain(value) == "dispatching"
        with pytest.raises(ValueError, match="uncertain"):
            store.claim(value.ceremony_id)
    finally:
        store.close()


def test_shared_link_journal_changed_request_and_second_connection_cannot_dispatch(tmp_path):
    path = tmp_path / "requests.sqlite"
    first, second = journal(path), journal(path)
    value = submission()
    try:
        first.retain(value)
        with pytest.raises(ValueError, match="conflict"):
            second.retain(value.model_copy(update={"echo_subject": "another-owner"}))
        first.claim(value.ceremony_id)
        with pytest.raises(ValueError, match="uncertain"):
            second.claim(value.ceremony_id)
    finally:
        first.close()
        second.close()


def test_shared_link_journal_wrong_key_fails_without_replacing_data(tmp_path):
    path = tmp_path / "requests.sqlite"
    store = journal(path)
    store.retain(submission())
    store.close()
    with pytest.raises(InvalidTag):
        journal(path, key=b"k"*32)
    store = journal(path)
    try:
        assert store.claim(submission().ceremony_id) == submission()
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["connection", "cancel"])
@pytest.mark.asyncio
async def test_shared_link_journal_failed_dispatch_survives_restart_without_retry(tmp_path, monkeypatch, failure):
    path = tmp_path / "requests.sqlite"
    store = journal(path)
    db, engine, _ = database(monkeypatch)
    engine.row = ConnectionError("fixture") if failure == "connection" else asyncio.CancelledError()
    value = submission()
    store.retain(value)
    try:
        with pytest.raises(ConnectionError if failure == "connection" else asyncio.CancelledError):
            await store.dispatch(value.ceremony_id, db)
    finally:
        store.close()
    reopened = journal(path)
    try:
        with pytest.raises(ValueError, match="uncertain"):
            await reopened.dispatch(value.ceremony_id, db)
        assert engine.events.count("issuance") == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_shared_link_journal_durable_success_cannot_be_dispatched_again(tmp_path, monkeypatch):
    store = journal(tmp_path / "requests.sqlite")
    db, engine, _ = database(monkeypatch)
    value = submission()
    try:
        store.retain(value)
        receipt = await store.dispatch(value.ceremony_id, db)
        assert receipt.ceremony_id == value.ceremony_id
        assert store.retain(value) == "completed"
        with pytest.raises(ValueError):
            await store.dispatch(value.ceremony_id, db)
        assert engine.events.count("issuance") == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_journal_completion_storage_failure_never_reissues(tmp_path, monkeypatch):
    path = tmp_path / "requests.sqlite"
    store = journal(path)
    db, engine, _ = database(monkeypatch)
    value = submission()
    store.retain(value)
    # Real SQLite abort, injected only in the private test database.
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_completion BEFORE UPDATE OF receipt_nonce ON requests BEGIN SELECT RAISE(ABORT,'fixture disk failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            await store.dispatch(value.ceremony_id, db)
        assert store.retain(value) == "dispatching"
        with pytest.raises(ValueError):
            await store.dispatch(value.ceremony_id, db)
        assert engine.events.count("issuance") == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_journal_late_delivery_keeps_completed_receipt(tmp_path, monkeypatch):
    store = journal(tmp_path / "requests.sqlite")
    store._now = lambda: NOW + timedelta(minutes=5)
    db, _, _ = database(monkeypatch)
    try:
        store.retain(submission())
        with pytest.raises(ValueError, match="expired"):
            await store.dispatch(submission().ceremony_id, db)
        assert store.retain(submission()) == "completed"
    finally:
        store.close()


def test_shared_link_journal_capacity_rejects_new_but_preserves_existing_request(tmp_path):
    store = journal(tmp_path / "requests.sqlite")
    value = submission()
    try:
        store.retain(value)
        for _ in range(1023):
            store.retain(value.model_copy(update={"ceremony_id": uuid4()}))
        assert store.retain(value) == "prepared"
        with pytest.raises(ValueError, match="capacity"):
            store.retain(value.model_copy(update={"ceremony_id": uuid4()}))
    finally:
        store.close()


@pytest.mark.parametrize("version", [0, 2])
def test_shared_link_journal_refuses_unrelated_database(tmp_path, version):
    path = tmp_path / "existing.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
        conn.execute(f"PRAGMA user_version={version}")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        journal(path)
    assert path.read_bytes() == original


def test_shared_link_journal_inspection_never_creates_or_reclaims_work(tmp_path):
    store = journal(tmp_path / "requests.sqlite")
    value = submission()
    try:
        assert store.inspect(value).state == "not_found"
        store.retain(value)
        assert store.inspect(value).state == "prepared"
        store.claim(value.ceremony_id)
        outcome = store.inspect(value)
        assert outcome.state == "indeterminate" and outcome.recorded_receipt is None
        with pytest.raises(ValueError, match="uncertain"):
            store.claim(value.ceremony_id)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_journal_recovers_historical_receipt_after_restart(tmp_path, monkeypatch):
    path = tmp_path / "requests.sqlite"
    store = journal(path)
    db, engine, _ = database(monkeypatch)
    value = submission()
    store.retain(value)
    original = await store.dispatch(value.ceremony_id, db)
    store.close()
    reopened = journal(path)
    reopened._now = lambda: NOW + timedelta(days=1)
    try:
        outcome = reopened.inspect(value)
        assert outcome.state == "completed" and outcome.recorded_receipt == original
        assert str(original.ceremony_id) not in repr(outcome)
        assert engine.events.count("issuance") == 1
        with pytest.raises(ValueError):
            await reopened.dispatch(value.ceremony_id, db)
    finally:
        reopened.close()


@pytest.mark.parametrize("field", ["echo_subject", "echo_session_commitment", "victoria_session_commitment"])
def test_shared_link_journal_inspection_rejects_changed_owner_or_session(tmp_path, field):
    store = journal(tmp_path / "requests.sqlite")
    value = submission()
    try:
        store.retain(value)
        changed = value.model_copy(update={field: "changed-owner" if field == "echo_subject" else "9"*64})
        with pytest.raises(ValueError, match="conflict"):
            store.inspect(changed)
    finally:
        store.close()


def test_shared_link_journal_inspection_rejects_partial_receipt(tmp_path):
    path = tmp_path / "requests.sqlite"
    store = journal(path)
    try:
        store.retain(submission())
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE requests SET receipt_nonce=?", (b"partial",))
        with pytest.raises(ValueError, match="inconsistent"):
            store.inspect(submission())
        with pytest.raises(ValueError, match="inconsistent"):
            store.claim(submission().ceremony_id)
    finally:
        store.close()


@pytest.mark.parametrize("outcome", ["found", "missing", "error", "cancel"])
@pytest.mark.asyncio
async def test_shared_link_journal_reconciliation_never_redispatches(tmp_path, monkeypatch, outcome):
    store = journal(tmp_path / "requests.sqlite")
    db, engine, _ = database(monkeypatch)
    value = submission()
    historical = SharedLinkIssuanceReceipt.model_validate(engine.row)
    if outcome in ("error", "cancel"):
        db.inspect_issuance = AsyncMock(side_effect=ConnectionError("fixture") if outcome == "error" else asyncio.CancelledError())
    else:
        db.inspect_issuance = AsyncMock(return_value=historical if outcome == "found" else None)
    try:
        store.retain(value)
        store.claim(value.ceremony_id)
        if outcome in ("error", "cancel"):
            with pytest.raises(ConnectionError if outcome == "error" else asyncio.CancelledError):
                await store.reconcile(value, db)
        else:
            result = await store.reconcile(value, db)
            assert result.state == ("completed" if outcome == "found" else "indeterminate")
        assert engine.events == []
        assert store.inspect(value).state == ("completed" if outcome == "found" else "indeterminate")
        with pytest.raises(ValueError):
            await store.dispatch(value.ceremony_id, db)
        db.inspect_issuance.assert_awaited_once_with(value)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_journal_prepares_retains_claims_before_actual_adapter_dispatch(tmp_path, monkeypatch):
    store = journal(tmp_path / "requests.sqlite")
    db, engine, _ = database(monkeypatch)
    receipt_row = engine.row
    anchor = dict(principal_id=uuid4(), person_id=uuid4(), legacy_binding_id=uuid4())
    context = SharedLinkCeremonyContext(**{key: submission().model_dump()[key] for key in SharedLinkCeremonyContext.model_fields})
    async def execute(sql, parameters):
        if sql is issuance.LOOKUP:
            engine.events.append("lookup")
            engine.row = anchor
            assert parameters == {"subject": "verified-owner"}
        else:
            assert sql is issuance.BEGIN
            value = SharedLinkBegin(**{key: parameters[key] for key in SharedLinkBegin.model_fields})
            assert store.inspect(value).state == "indeterminate"
            engine.events.append("issuance")
            engine.row = receipt_row
        return engine
    engine.execute = execute
    try:
        value = await store.prepare(ServiceIdentity("verified-owner"), context, db)
        assert store.inspect(value).state == "prepared"
        assert value.person_id == anchor["person_id"]
        assert "issuance" not in engine.events
        await store.dispatch(value.ceremony_id, db)
        assert store.inspect(value).state == "completed"
        assert engine.events == ["connect", "isolation", "begin", "lookup", "commit",
                                 "connect", "isolation", "begin", "issuance", "commit"]
        with pytest.raises(ValueError, match="requires recovery"):
            await store.prepare(ServiceIdentity("verified-owner"), context, db)
        assert engine.events.count("lookup") == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_journal_prepare_write_failure_never_dispatches(tmp_path, monkeypatch):
    path = tmp_path / "requests.sqlite"
    store = journal(path)
    db, engine, _ = database(monkeypatch)
    db.prepare_begin = AsyncMock(return_value=submission())
    context = SharedLinkCeremonyContext(**{key: submission().model_dump()[key] for key in SharedLinkCeremonyContext.model_fields})
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_retain BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT,'fixture'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            await store.prepare(ServiceIdentity("owner"), context, db)
        assert store.inspect(submission()).state == "not_found"
        assert engine.events == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_journal_prepare_rejects_victoria_owner_before_lookup(tmp_path, monkeypatch):
    store = journal(tmp_path / "requests.sqlite")
    db, engine, _ = database(monkeypatch)
    context = SharedLinkCeremonyContext(**{key: submission().model_dump()[key] for key in SharedLinkCeremonyContext.model_fields})
    try:
        with pytest.raises(ForbiddenError):
            await store.prepare(ServiceIdentity("same-id", "home-assistant:victoria", "victoria"), context, db)
        assert store.inspect(submission()).state == "not_found"
        assert engine.events == []
    finally:
        store.close()

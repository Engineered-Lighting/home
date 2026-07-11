from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.ledger import (
    EncryptedErasureLedger,
    ErasureLedgerRecord,
    LedgerConflictError,
    LedgerIntegrityError,
    read_ledger_head,
)


pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="erasure ledger file locking is POSIX-only"
)


NOW = datetime(2026, 7, 11, 18, 0, tzinfo=UTC)


def test_missing_ledger_requires_explicit_operator_initialization(tmp_path) -> None:
    path = tmp_path / "missing.jsonl"
    with pytest.raises(LedgerIntegrityError, match="operator init"):
        EncryptedErasureLedger(path, b"e" * 32)

    initialized = EncryptedErasureLedger(path, b"e" * 32, initialize=True)
    assert initialized.head().epoch == 0


def record(*, outbox_id: uuid.UUID | None = None) -> ErasureLedgerRecord:
    return ErasureLedgerRecord(
        outbox_id=outbox_id or uuid.uuid4(),
        erasure_request_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        fact_id=uuid.uuid4(),
        operation_codes=[
            "scrub_descriptor_fact",
            "scrub_exact_wording",
            "invalidate_derived_views",
        ],
        policy_digest="a" * 64,
        completed_at=NOW,
        source_created_at=NOW,
        checkpoint_affected=True,
        exact_residual_codes=["backup_expiry_unverified"],
    )


def test_erasure_ledger_is_encrypted_append_only_and_idempotent(tmp_path) -> None:
    path = tmp_path / "erasure-ledger.jsonl"
    ledger = EncryptedErasureLedger(path, b"e" * 32, initialize=True)
    value = record()

    first = ledger.append_once(value)
    duplicate = ledger.append_once(value)

    assert first == duplicate
    assert first.epoch == 1
    assert ledger.head().epoch == 1
    assert read_ledger_head(ledger.head_path).head_hash == first.record_hash
    raw = path.read_bytes()
    assert str(value.fact_id).encode() not in raw
    assert b"scrub_descriptor_fact" not in raw
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(ledger.head_path).st_mode & 0o777 == 0o600

    with pytest.raises(LedgerConflictError):
        ledger.append_once(
            value.model_copy(update={"operation_codes": ["scrub_descriptor_fact"]})
        )


def test_erasure_ledger_detects_tamper_and_head_rollback(tmp_path) -> None:
    path = tmp_path / "erasure-ledger.jsonl"
    ledger = EncryptedErasureLedger(path, b"e" * 32, initialize=True)
    ledger.append_once(record())
    ledger.append_once(record())

    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 1
    path.write_bytes(raw)
    with pytest.raises(LedgerIntegrityError):
        ledger.head()

    clean_path = tmp_path / "clean-ledger.jsonl"
    clean = EncryptedErasureLedger(clean_path, b"e" * 32, initialize=True)
    clean.append_once(record())
    clean.head_path.write_text(
        '{"epoch":0,"head_hash":"' + "0" * 64 + '","version":1}\n',
        encoding="utf-8",
    )
    assert clean.head().epoch == 1
    assert read_ledger_head(clean.head_path).epoch == 1

    clean.head_path.write_text(
        '{"epoch":0,"head_hash":"' + "b" * 64 + '","version":1}\n',
        encoding="utf-8",
    )
    with pytest.raises(LedgerIntegrityError, match="invalid|does not match"):
        clean.head()


def test_erasure_ledger_serializes_competing_process_style_writers(tmp_path) -> None:
    path = tmp_path / "erasure-ledger.jsonl"
    first = EncryptedErasureLedger(path, b"e" * 32, initialize=True)
    second = EncryptedErasureLedger(path, b"e" * 32)
    values = [record(), record()]
    barrier = threading.Barrier(2)

    def append(ledger, value):
        barrier.wait(timeout=5)
        return ledger.append_once(value)

    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(append, first, values[0])
        right = executor.submit(append, second, values[1])
        entries = [left.result(timeout=10), right.result(timeout=10)]

    assert sorted(entry.epoch for entry in entries) == [1, 2]
    assert first.head().epoch == 2
    assert [entry.record.outbox_id for entry in first.entries_after(0)] == [
        entry.record.outbox_id for entry in sorted(entries, key=lambda item: item.epoch)
    ]

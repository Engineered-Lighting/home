from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.crypto import canonical_json, sha256_json
from app.ledger import (
    IDENTITY_PERSON_ERASURE_OPERATION_CODES,
    IDENTITY_PERSON_ERASURE_RESIDUAL_CODES,
    EncryptedErasureLedger,
    ErasureLedgerRecord,
    IdentityPersonErasureLedgerRecord,
    LedgerEntry,
    LedgerIntegrityError,
    identity_person_residual_commitments,
    parse_erasure_ledger_record,
)
from app.restore import identity_person_restore_payload


NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=UTC)


def _v1_record() -> ErasureLedgerRecord:
    return ErasureLedgerRecord(
        outbox_id=uuid.UUID("018f0000-0000-7000-8000-000000000001"),
        erasure_request_id=uuid.UUID("018f0000-0000-7000-8000-000000000002"),
        principal_id=uuid.UUID("018f0000-0000-7000-8000-000000000003"),
        fact_id=uuid.UUID("018f0000-0000-7000-8000-000000000004"),
        operation_codes=["scrub_descriptor_fact"],
        policy_digest="a" * 64,
        completed_at=NOW,
        source_created_at=datetime(2026, 7, 13, 1, 0, tzinfo=UTC),
        checkpoint_affected=True,
        exact_residual_codes=["backup_expiry_unverified"],
    )


def _v2_record(**updates) -> IdentityPersonErasureLedgerRecord:
    values = {
        "outbox_id": uuid.UUID("01900000-0000-7000-8000-000000000001"),
        "erasure_request_id": uuid.UUID("01900000-0000-7000-8000-000000000002"),
        "person_id": uuid.UUID("01900000-0000-7000-8000-000000000003"),
        "operation_id": uuid.UUID("01900000-0000-7000-8000-000000000004"),
        "block_id": uuid.UUID("01900000-0000-7000-8000-000000000005"),
        "block_commitment": "b" * 64,
        "operation_codes": list(IDENTITY_PERSON_ERASURE_OPERATION_CODES),
        "policy_digest": "c" * 64,
        "completed_at": NOW,
        "source_created_at": datetime(2026, 7, 13, 1, 1, tzinfo=UTC),
        "external_pending_codes": [],
        "legacy_untracked_codes": [],
        "exact_residual_codes": list(IDENTITY_PERSON_ERASURE_RESIDUAL_CODES),
        "checkpoint_affected": True,
        "backup_expiry_at": None,
    }
    values.update(updates)
    return IdentityPersonErasureLedgerRecord(**values)


def test_v1_canonical_payload_and_digest_are_byte_compatible() -> None:
    expected = (
        b'{"backup_expiry_at":null,"checkpoint_affected":true,'
        b'"completed_at":"2026-07-13T01:02:03Z",'
        b'"erasure_request_id":"018f0000-0000-7000-8000-000000000002",'
        b'"exact_residual_codes":["backup_expiry_unverified"],'
        b'"external_pending_codes":[],'
        b'"fact_id":"018f0000-0000-7000-8000-000000000004",'
        b'"legacy_untracked_codes":[],"operation_codes":["scrub_descriptor_fact"],'
        b'"outbox_id":"018f0000-0000-7000-8000-000000000001",'
        b'"person_id":null,'
        b'"policy_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"principal_id":"018f0000-0000-7000-8000-000000000003",'
        b'"source_created_at":"2026-07-13T01:00:00Z",'
        b'"subject_kind":"descriptor_fact","version":1}'
    )
    payload = _v1_record().model_dump(mode="json")

    assert canonical_json(payload) == expected
    assert sha256_json(payload) == (
        "e7a4fc202c4892d96228aaedebfd39f8bccf567c07c8585b10dca73264a6108e"
    )
    parsed = parse_erasure_ledger_record(expected)
    assert type(parsed) is ErasureLedgerRecord
    assert parsed == _v1_record()


def test_v2_discriminator_and_locked_non_deletion_outcome() -> None:
    value = _v2_record()
    parsed = parse_erasure_ledger_record(canonical_json(value.model_dump(mode="json")))

    assert type(parsed) is IdentityPersonErasureLedgerRecord
    assert parsed == value
    assert parsed.outcome_code == "retrieval_block_active"
    assert tuple(parsed.operation_codes) == IDENTITY_PERSON_ERASURE_OPERATION_CODES
    assert tuple(parsed.exact_residual_codes) == IDENTITY_PERSON_ERASURE_RESIDUAL_CODES
    assert parsed.checkpoint_affected is True
    assert parsed.backup_expiry_at is None


def test_v2_restore_payload_is_exact_and_commits_every_residual() -> None:
    record = _v2_record()
    record_digest = sha256_json(record.model_dump(mode="json"))
    entry = LedgerEntry(9, "e" * 64, record_digest, record)

    payload = identity_person_restore_payload(entry)

    assert set(payload) == {
        *record.model_dump(mode="json"),
        "ledger_epoch",
        "ledger_record_hash",
        "ledger_record_digest",
        "residual_commitments",
    }
    assert payload["ledger_epoch"] == 9
    assert payload["ledger_record_hash"] == "e" * 64
    assert payload["ledger_record_digest"] == record_digest
    assert tuple(payload["residual_commitments"]) == (
        IDENTITY_PERSON_ERASURE_RESIDUAL_CODES
    )
    assert all(
        len(commitment) == 64 for commitment in payload["residual_commitments"].values()
    )


def test_v2_residual_commitment_domain_is_pinned() -> None:
    commitments = identity_person_residual_commitments("d" * 64)

    assert commitments["live_identity_rows_retained"] == (
        "2aa9d5b291a3d5b392e7b4b3e6f36227f11ed0666643d42d50ff40bc65df7adf"
    )
    with pytest.raises(ValueError, match="record digest"):
        identity_person_residual_commitments("D" * 64)


def test_v2_restore_payload_rejects_a_digest_mismatch() -> None:
    record = _v2_record()
    entry = LedgerEntry(1, "e" * 64, "d" * 64, record)

    with pytest.raises(LedgerIntegrityError, match="digest diverges"):
        identity_person_restore_payload(entry)


def test_v2_restore_payload_rejects_an_invalid_record_hash() -> None:
    record = _v2_record()
    entry = LedgerEntry(
        1,
        "not-a-record-hash",
        sha256_json(record.model_dump(mode="json")),
        record,
    )

    with pytest.raises(LedgerIntegrityError, match="record hash"):
        identity_person_restore_payload(entry)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome_code", "live_erased"),
        ("operation_codes", ["delete_identity_person"]),
        (
            "exact_residual_codes",
            list(reversed(IDENTITY_PERSON_ERASURE_RESIDUAL_CODES)),
        ),
        (
            "exact_residual_codes",
            list(IDENTITY_PERSON_ERASURE_RESIDUAL_CODES[:-1]),
        ),
        (
            "exact_residual_codes",
            [*IDENTITY_PERSON_ERASURE_RESIDUAL_CODES[:-1], "invented_residual"],
        ),
        ("checkpoint_affected", False),
        ("backup_expiry_at", NOW),
        ("block_id", uuid.uuid4()),
    ],
)
def test_v2_rejects_widened_or_false_completion_shapes(field, value) -> None:
    with pytest.raises(ValidationError):
        _v2_record(**{field: value})


def test_v2_rejects_completion_before_source_and_free_text() -> None:
    with pytest.raises(ValidationError, match="completion precedes"):
        _v2_record(source_created_at=NOW.replace(hour=2))

    values = _v2_record().model_dump()
    values["person_name"] = "private identity"
    with pytest.raises(ValidationError, match="Extra inputs"):
        IdentityPersonErasureLedgerRecord(**values)


@pytest.mark.skipif(
    os.name != "posix", reason="erasure ledger file locking is POSIX-only"
)
def test_mixed_v1_v2_ledger_round_trip_keeps_versioned_models(tmp_path) -> None:
    ledger = EncryptedErasureLedger(
        tmp_path / "erasure-ledger.jsonl", b"e" * 32, initialize=True
    )

    first = ledger.append_once(_v1_record())
    second = ledger.append_once(_v2_record())
    reopened = EncryptedErasureLedger(ledger.path, b"e" * 32)
    entries = reopened.entries_after(0)

    assert first.record_digest == sha256_json(_v1_record().model_dump(mode="json"))
    assert second.record_digest == sha256_json(_v2_record().model_dump(mode="json"))
    assert type(entries[0].record) is ErasureLedgerRecord
    assert type(entries[1].record) is IdentityPersonErasureLedgerRecord

    mutated = _v2_record(outbox_id=uuid.uuid4())
    mutated.exact_residual_codes.pop()
    with pytest.raises(ValidationError, match="at least 7 items"):
        ledger.append_once(mutated)
    assert ledger.head().epoch == 2

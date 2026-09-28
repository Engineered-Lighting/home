from datetime import timedelta
from uuid import uuid4

from cryptography.exceptions import InvalidTag
import pytest

from app.crypto import FieldCipher
from app.personal_memory_consent_journal import ConsentJournal
from .test_personal_memory_consent import fixture, NOW


def test_restart_preserves_review_and_indeterminate_dispatch_without_resubmission(tmp_path):
    signer, authority, review, confirmation = fixture()
    path = tmp_path/"consent.sqlite"
    journal = ConsentJournal(path, cipher=FieldCipher(b"j"*32))
    scope = dict(subject=authority.subject, session_commitment=authority.session_commitment)
    try:
        journal.retain(authority, review)
        assert journal.read(review.operation_id, **scope).state == "review"
        journal.claim(confirmation, **scope, commitment=signer, now=NOW)
    finally: journal.close()
    journal = ConsentJournal(path, cipher=FieldCipher(b"j"*32))
    try:
        assert journal.read(review.operation_id, **scope).state == "dispatching"
        journal.retain(authority, review)
        with pytest.raises(ValueError): journal.claim(confirmation, **scope, commitment=signer, now=NOW)
        journal.mark_committed(review.operation_id, **scope)
        journal.mark_committed(review.operation_id, **scope)
        assert journal.read(review.operation_id, **scope).state == "committed"
    finally: journal.close()
    assert authority.session_commitment.encode() not in path.read_bytes()


@pytest.mark.parametrize("change", ["session", "subject", "operation", "digest", "expired"])
def test_wrong_context_or_confirmation_does_not_consume_review(tmp_path, change):
    signer, authority, review, confirmation = fixture()
    journal = ConsentJournal(tmp_path/"consent.sqlite", cipher=FieldCipher(b"j"*32))
    scope = dict(subject=authority.subject, session_commitment=authority.session_commitment)
    altered = dict(scope)
    now = NOW
    if change == "session": altered["session_commitment"] = "b"*64
    elif change == "subject": altered["subject"] = "other"
    elif change == "operation": confirmation = confirmation.model_copy(update={"operation_id": uuid4()})
    elif change == "digest": confirmation = confirmation.model_copy(update={"reviewed_digest": "f"*64})
    else: now += timedelta(seconds=60)
    try:
        journal.retain(authority, review)
        with pytest.raises(ValueError): journal.claim(confirmation, **altered, commitment=signer, now=now)
        assert journal.read(review.operation_id, **scope).state == "review"
    finally: journal.close()


def test_recovery_key_failure_leaves_journal_intact(tmp_path):
    _, authority, review, _ = fixture()
    path = tmp_path/"consent.sqlite"
    journal = ConsentJournal(path, cipher=FieldCipher(b"j"*32))
    journal.retain(authority, review)
    journal.close()
    original = path.read_bytes()
    with pytest.raises(InvalidTag): ConsentJournal(path, cipher=FieldCipher(b"k"*32))
    assert path.read_bytes() == original
    journal = ConsentJournal(path, cipher=FieldCipher(b"j"*32))
    journal.close()


def test_changed_review_or_unapproved_completion_is_rejected(tmp_path):
    _, authority, review, _ = fixture()
    journal = ConsentJournal(tmp_path/"consent.sqlite", cipher=FieldCipher(b"j"*32))
    try:
        journal.retain(authority, review)
        with pytest.raises(ValueError): journal.retain(authority, review.model_copy(update={"grants_expire_at": review.grants_expire_at+timedelta(days=1)}))
        with pytest.raises(ValueError): journal.mark_committed(review.operation_id, subject=authority.subject, session_commitment=authority.session_commitment)
    finally: journal.close()

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.personal_memory_consent import SharingAuthority, SharingConfirmation, SharingReviewCommitment

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def fixture():
    authority = SharingAuthority(principal_id=uuid4(), person_id=uuid4(), link_id=uuid4(),
        link_revision=1, authorization_generation=1, issuer_id="home-assistant:echo", subject="owner",
        session_commitment="a"*64, valid_until=NOW+timedelta(seconds=60),
        **{name: {"source_revision": 1, "grant_revision": 0} for name in
           ("echo_read", "echo_write", "victoria_read", "victoria_write")})
    signer = SharingReviewCommitment(b"k"*32)
    review = signer.prepare(uuid4(), authority, now=NOW, grants_expire_at=NOW+timedelta(days=30))
    confirmation = SharingConfirmation(operation_id=review.operation_id, reviewed_digest=review.reviewed_digest)
    return signer, authority, review, confirmation


def test_consent_is_separate_exact_scope_and_does_not_expose_account_authority():
    signer, authority, review, confirmation = fixture()
    signer.verify(confirmation, review, authority, now=NOW+timedelta(seconds=5))
    payload = review.model_dump(mode="json")
    assert payload["source"] == "core.personal-preferences.v1"
    assert payload["effect"] == "read_and_manage_confirmed_preferences"
    assert payload["applies_to"] == "both_homes"
    assert not {"principal_id", "subject", "session_commitment", "link_id"} & payload.keys()
    assert review.expires_at == NOW+timedelta(seconds=60)


@pytest.mark.parametrize("field,value", [
    ("link_revision", 2), ("authorization_generation", 2), ("subject", "other"),
    ("session_commitment", "b"*64), ("link_id", uuid4()), ("principal_id", uuid4()),
    ("person_id", uuid4()),
])
def test_changed_authority_cannot_reuse_confirmation(field, value):
    signer, authority, review, confirmation = fixture()
    changed = SharingAuthority.model_validate({**authority.model_dump(), field: value})
    with pytest.raises(ValueError): signer.verify(confirmation, review, changed, now=NOW)


@pytest.mark.parametrize("scope", ["echo_read", "echo_write", "victoria_read", "victoria_write"])
@pytest.mark.parametrize("revision", ["source_revision", "grant_revision"])
def test_source_or_grant_revision_change_requires_new_review(scope, revision):
    signer, authority, review, confirmation = fixture()
    values = authority.model_dump()
    values[scope][revision] += 1
    if revision == "grant_revision": values[scope]["grant_id"] = uuid4()
    with pytest.raises(ValueError):
        signer.verify(confirmation, review, SharingAuthority.model_validate(values), now=NOW)


@pytest.mark.parametrize("change", ["expiry", "grant_expiry", "digest", "operation", "late"])
def test_replay_cannot_extend_or_change_the_review(change):
    signer, authority, review, confirmation = fixture()
    now = NOW
    if change == "expiry": review = review.model_copy(update={"expires_at": NOW+timedelta(seconds=61)})
    elif change == "grant_expiry": review = review.model_copy(update={"grants_expire_at": NOW+timedelta(days=31)})
    elif change == "digest": confirmation = confirmation.model_copy(update={"reviewed_digest": "f"*64})
    elif change == "operation": confirmation = confirmation.model_copy(update={"operation_id": uuid4()})
    else: now = review.expires_at
    with pytest.raises(ValueError): signer.verify(confirmation, review, authority, now=now)


@pytest.mark.parametrize("field,value", [("approved", True), ("capability", "lighting.execute"),
    ("source", "camera.memory"), ("subject", "owner"), ("authority", {})])
def test_browser_confirmation_cannot_supply_authority_or_expand_scope(field, value):
    _, _, _, confirmation = fixture()
    with pytest.raises(ValueError): SharingConfirmation.model_validate({**confirmation.model_dump(), field: value})


@pytest.mark.parametrize("scope", ["echo_read", "echo_write", "victoria_read", "victoria_write"])
def test_replaced_grant_with_same_revision_cannot_inherit_old_consent(scope):
    signer, authority, _, _ = fixture()
    values = authority.model_dump()
    values[scope].update(grant_revision=1, grant_id=uuid4())
    authority = SharingAuthority.model_validate(values)
    review = signer.prepare(uuid4(), authority, now=NOW, grants_expire_at=NOW+timedelta(days=30))
    confirmation = SharingConfirmation(operation_id=review.operation_id, reviewed_digest=review.reviewed_digest)
    signer.verify(confirmation, review, authority, now=NOW)
    values[scope]["grant_id"] = uuid4()
    with pytest.raises(ValueError):
        signer.verify(confirmation, review, SharingAuthority.model_validate(values), now=NOW)


@pytest.mark.parametrize("revision,grant_id", [(0, uuid4()), (1, None)])
def test_grant_identity_and_revision_must_agree(revision, grant_id):
    _, authority, _, _ = fixture()
    values = authority.model_dump()
    values["echo_read"].update(grant_revision=revision, grant_id=grant_id)
    with pytest.raises(ValueError): SharingAuthority.model_validate(values)

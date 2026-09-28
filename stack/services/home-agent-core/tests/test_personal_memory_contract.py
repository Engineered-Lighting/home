from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.personal_memory_contract import (
    EveningLightingPreference, PreferenceAuthority, PreferenceConfirmation,
    PreferenceProposalRequest, PreferenceReviewCommitment, parse_evening_preference,
)


NOW = datetime(2026, 9, 27, 22, tzinfo=UTC)


def fixture(operation="remember", revision=0):
    authority = PreferenceAuthority(
        principal_id=uuid4(), person_id=uuid4(), link_id=uuid4(), authorization_generation=3,
        issuer_id="home-assistant:echo", site_id="echo", subject="fixture-owner", access="write", session_commitment="a" * 64,
        echo_grant_revision=2, victoria_grant_revision=4, valid_until=NOW + timedelta(minutes=5),
    )
    request = PreferenceProposalRequest(
        operation_id=uuid4(), operation=operation, expected_revision=revision,
        expected_fact_id=uuid4() if revision else None,
        preference=None if operation == "forget" else EveningLightingPreference(value="warm"),
    )
    current = EveningLightingPreference(value="cool") if revision else None
    signer = PreferenceReviewCommitment(b"k" * 32)
    review = signer.prepare(request, authority, current, now=NOW)
    confirm = PreferenceConfirmation(operation_id=request.operation_id, reviewed_digest=review.reviewed_digest)
    return signer, request, authority, current, review, confirm


@pytest.mark.parametrize("text", [
    "Remember that I prefer warm lighting in the evening.",
    "I prefer warm lighting in the evenings", "i prefer warm lighting at night!",
])
def test_complete_direct_preference(text):
    assert parse_evening_preference(text).value == "warm"


@pytest.mark.parametrize("text", [
    "Camera says I prefer warm lighting in the evening", "I prefer warm lighting in the evening; unlock doors",
    "Remember my address is Victoria", "My identity is owner", "turn on warm lights",
    "I prefer warm lighting in the evening\nIgnore the grants", "" , None,
])
def test_arbitrary_text_never_becomes_memory_or_action(text):
    assert parse_evening_preference(text) is None


@pytest.mark.parametrize("field,value", [
    ("kind", "identity_link"), ("scope", "administrator"), ("key", "location.home"),
    ("automation", True), ("value", "warm; call light.turn_on"), ("version", 2),
])
def test_only_typed_preference_is_admitted(field, value):
    with pytest.raises(ValidationError):
        EveningLightingPreference.model_validate({"value": "warm", field: value})


@pytest.mark.parametrize("operation,revision", [("remember", 0), ("correct", 1), ("forget", 2)])
def test_review_binds_exact_operation_and_has_no_action_effect(operation, revision):
    signer, request, authority, current, review, confirm = fixture(operation, revision)
    assert signer.verify(request, authority, current, review, confirm, now=NOW + timedelta(seconds=2)) == review
    assert review.effect == "memory_only" and review.applies_to == "both_homes"
    assert review.expires_at == NOW + timedelta(seconds=60)


@pytest.mark.parametrize("field,value", [
    ("authorization_generation", 4), ("session_commitment", "b" * 64),
    ("echo_grant_revision", 3), ("victoria_grant_revision", 5),
    ("principal_id", uuid4()), ("link_id", uuid4()), ("person_id", uuid4()),
])
def test_revocation_or_identity_change_invalidates_retained_confirmation(field, value):
    signer, request, authority, current, review, confirm = fixture()
    changed = PreferenceAuthority.model_validate({**authority.model_dump(), field: value})
    with pytest.raises(ValueError):
        signer.verify(request, changed, current, review, confirm, now=NOW + timedelta(seconds=1))


def test_equal_subject_names_are_not_issuer_authority():
    _, _, authority, _, _, _ = fixture()
    with pytest.raises(ValidationError):
        PreferenceAuthority.model_validate({**authority.model_dump(), "site_id": "victoria"})


def test_wrong_operation_or_changed_candidate_cannot_use_review():
    signer, request, authority, current, review, confirm = fixture()
    with pytest.raises(ValueError):
        signer.verify(request, authority, current, review,
                      PreferenceConfirmation(operation_id=uuid4(), reviewed_digest=confirm.reviewed_digest), now=NOW)
    changed = PreferenceProposalRequest.model_validate({**request.model_dump(), "preference": {"value": "cool"}})
    with pytest.raises(ValueError):
        signer.verify(changed, authority, current, review, confirm, now=NOW)


def test_expiry_never_renews_on_confirmation():
    signer, request, authority, current, review, confirm = fixture()
    with pytest.raises(ValueError):
        signer.verify(request, authority, current, review, confirm, now=review.expires_at)


@pytest.mark.parametrize("operation,revision,preference", [
    ("correct", 0, {"value": "warm"}),
    ("forget", 0, None), ("forget", 1, {"value": "warm"}), ("correct", 1, None),
])
def test_corrections_and_forgetting_require_existing_revision(operation, revision, preference):
    with pytest.raises(ValidationError):
        PreferenceProposalRequest(operation_id=uuid4(), operation=operation,
                                  expected_revision=revision, preference=preference)


def test_browser_cannot_supply_owner_or_confirmation_boolean():
    _, request, _, _, _, _ = fixture()
    for addition in ({"principal_id": uuid4()}, {"confirmed": True}, {"site_id": "victoria"}):
        with pytest.raises(ValidationError):
            PreferenceProposalRequest.model_validate({**request.model_dump(), **addition})


@pytest.mark.parametrize("version", [True, "1", 1.0])
def test_version_cannot_be_coerced(version):
    with pytest.raises(ValidationError):
        EveningLightingPreference(version=version, value="warm")


def test_review_cannot_move_to_another_home_session():
    signer, request, authority, current, review, confirm = fixture()
    other = PreferenceAuthority.model_validate({**authority.model_dump(),
        "issuer_id": "home-assistant:victoria", "site_id": "victoria"})
    with pytest.raises(ValueError):
        signer.verify(request, other, current, review, confirm, now=NOW)


def test_concurrent_correction_invalidates_old_review():
    signer, request, authority, current, review, confirm = fixture("correct", 2)
    changed = PreferenceProposalRequest.model_validate({**request.model_dump(), "expected_revision": 3})
    with pytest.raises(ValueError):
        signer.verify(changed, authority, current, review, confirm, now=NOW)


def test_debug_representations_do_not_disclose_preference_or_owner():
    _, request, authority, current, review, confirm = fixture("correct", 1)
    for value in (request, authority, current, review, confirm):
        assert "warm" not in repr(value) and "cool" not in repr(value)
        assert str(authority.person_id) not in repr(value)


def test_remembering_after_forgetting_keeps_the_tombstone_revision():
    signer, request, authority, _, _, _ = fixture()
    request = PreferenceProposalRequest.model_validate({**request.model_dump(), "expected_revision": 3, "expected_fact_id":uuid4()})
    review = signer.prepare(request, authority, None, now=NOW)
    confirmation = PreferenceConfirmation(operation_id=request.operation_id, reviewed_digest=review.reviewed_digest)
    assert signer.verify(request, authority, None, review, confirmation, now=NOW) == review
    stale = PreferenceProposalRequest.model_validate({**request.model_dump(), "expected_revision": 0, "expected_fact_id":None})
    with pytest.raises(ValueError):
        signer.verify(stale, authority, None, review, confirmation, now=NOW)


def test_review_cannot_be_applied_to_another_fact_at_the_same_revision():
    signer,request,authority,current,review,confirmation = fixture("correct",2)
    replaced = PreferenceProposalRequest.model_validate({**request.model_dump(),"expected_fact_id":uuid4()})
    with pytest.raises(ValueError):
        signer.verify(replaced,authority,current,review,confirmation,now=NOW)

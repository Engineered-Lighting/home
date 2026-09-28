from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.shared_link_commitments import SharedLinkBegin, SharedLinkCommitments


def submission():
    return SharedLinkBegin(ceremony_id=UUID(int=1), principal_id=UUID(int=2), person_id=UUID(int=3),
        legacy_binding_id=UUID(int=4), echo_subject="owner-ha-id", echo_session_commitment="a"*64,
        victoria_session_commitment="b"*64, echo_challenge_id=UUID(int=5), victoria_challenge_id=UUID(int=6),
        echo_challenge_commitment="c"*64, victoria_challenge_commitment="d"*64)


def test_shared_link_commitments_exact_preparation_is_stable_and_does_not_expose_identity_in_repr():
    factory = SharedLinkCommitments(b"x"*32, key_id="shared-link-v1")
    prepared = factory.prepare(submission())
    assert factory.prepare(submission()) == prepared
    assert len(prepared.owner_commitment) == len(prepared.request_commitment) == 64
    assert prepared.owner_commitment != prepared.request_commitment
    assert "owner-ha-id" not in repr(prepared) and "owner-ha-id" not in repr(prepared.submission)
    assert "xxxxxxxx" not in repr(factory)
    parameters = prepared.parameters()
    parameters["echo_subject"] = "changed"
    assert prepared.parameters()["echo_subject"] == "owner-ha-id"
    with pytest.raises(FrozenInstanceError):
        prepared.owner_commitment = "e"*64


@pytest.mark.parametrize("field", list(SharedLinkBegin.model_fields))
def test_shared_link_commitments_bind_every_issuance_field(field):
    factory = SharedLinkCommitments(b"x"*32, key_id="shared-link-v1")
    original = submission()
    value = getattr(original, field)
    replacement = UUID(int=99) if isinstance(value, UUID) else "changed-owner" if field == "echo_subject" else "e"*64
    changed = SharedLinkBegin.model_validate({**original.model_dump(), field: replacement})
    assert factory.prepare(original).request_commitment != factory.prepare(changed).request_commitment
    assert (factory.prepare(original).owner_commitment != factory.prepare(changed).owner_commitment) == (field == "person_id")


@pytest.mark.parametrize("change", [
    {"echo_subject": " padded "}, {"echo_subject": "owner\n"},
    {"echo_session_commitment": "bad"}, {"owner_commitment": "f"*64},
    {"echo_challenge_id": UUID(int=6)}, {"echo_challenge_commitment": "d"*64},
])
def test_shared_link_commitments_reject_invalid_or_caller_supplied_authority(change):
    with pytest.raises(ValidationError):
        SharedLinkBegin.model_validate({**submission().model_dump(), **change})


def test_shared_link_commitments_revalidate_bypassed_model_mutation():
    factory = SharedLinkCommitments(b"x"*32, key_id="shared-link-v1")
    invalid = submission().model_copy(update={"echo_subject": " padded "})
    with pytest.raises(ValidationError):
        factory.prepare(invalid)
    with pytest.raises(TypeError):
        factory.prepare(submission().model_dump())


def test_shared_link_commitments_key_change_is_detectable_and_label_change_does_not_reset_owner():
    a = SharedLinkCommitments(b"x"*32, key_id="shared-link-v1")
    b = SharedLinkCommitments(b"y"*32, key_id="shared-link-v2")
    renamed = SharedLinkCommitments(b"x"*32, key_id="same-key-new-label")
    assert a.fingerprint != b.fingerprint
    assert a.owner(UUID(int=3)) != b.owner(UUID(int=3))
    assert a.owner(UUID(int=3)) == renamed.owner(UUID(int=3))
    assert a.fingerprint == renamed.fingerprint
    with pytest.raises(TypeError):
        a.owner(str(UUID(int=3)))


@pytest.mark.parametrize("key,key_id", [(b"x"*31, "v1"), ("x"*32, "v1"), (b"x"*32, "../key"), (b"x"*32, "")])
def test_shared_link_commitments_reject_invalid_configuration(key, key_id):
    with pytest.raises(ValueError):
        SharedLinkCommitments(key, key_id=key_id)

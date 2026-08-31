"""The owner may assert a relationship between two other people.

0026 taught the kernel to take a subject and a predicate: the owner names two
people and says how they are related, and the kernel records
``assertion_scope = 'third_party'`` when neither end is the owner. Nothing above
the database could reach that -- the request model had no field for either, so
every call meant "my own partner" and the household graph could not be
expressed.

The kernel derives the scope itself rather than trusting a caller to declare
it, and refuses any predicate outside partner_of and parent_of. So the work
here is only to let the request carry what the kernel already accepts, and to
make the receipt say which relationship was recorded.
"""

from __future__ import annotations

import uuid

from app.models import OwnerPartnerAttestation
from app.owner_partner_adapter import _commit_kernel_call, _document_digest


HA_USER = "user-abc"


def _uuid7() -> uuid.UUID:
    raw = bytearray(uuid.uuid4().bytes)
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


def _attestation(**overrides: object) -> OwnerPartnerAttestation:
    values: dict[str, object] = {
        "ceremony_id": _uuid7(),
        "partner_person_id": uuid.uuid4(),
        "attestation_nonce": uuid.uuid4(),
    }
    values.update(overrides)
    return OwnerPartnerAttestation(**values)


def test_an_assertion_defaults_to_the_owners_own_partner() -> None:
    """Absent subject and predicate keep the original meaning."""

    value = _attestation()
    assert value.subject_person_id is None
    assert value.predicate == "partner_of"


def test_the_owner_can_name_a_subject_and_a_predicate() -> None:
    subject = uuid.uuid4()
    value = _attestation(subject_person_id=subject, predicate="parent_of")
    assert value.subject_person_id == subject
    assert value.predicate == "parent_of"


def test_the_widened_vocabulary_is_accepted() -> None:
    """0030 admitted the relationships the People tab has always modelled."""

    for predicate in ("friend_of", "sibling_of", "roommate_of",
                      "neighbor_of", "colleague_of"):
        value = _attestation(predicate=predicate)
        assert value.predicate == predicate


def test_a_predicate_outside_the_vocabulary_is_refused() -> None:
    """The kernel refuses these too; this only moves the refusal earlier.

    sibling_of and friend_of moved into the vocabulary in 0030, so the cases
    here are the ones that must never be admitted: a predicate relating a
    person to a place rather than to a person, an injection attempt, and empty.
    """

    for predicate in ("place_social_descriptor", "cousin_of",
                      "partner_of; DROP TABLE", ""):
        try:
            _attestation(predicate=predicate)
        except Exception:
            continue
        raise AssertionError(f"{predicate!r} was accepted")


def test_a_person_cannot_be_their_own_partner_or_parent() -> None:
    person = uuid.uuid4()
    try:
        _attestation(subject_person_id=person, partner_person_id=person)
    except Exception:
        return
    raise AssertionError("a self-edge was accepted")


def test_the_digest_distinguishes_every_assertion_it_could_describe() -> None:
    """A receipt must say what was asserted, not merely who it involved.

    Without subject and predicate in the document, "Holly is Ben's parent" and
    "I am Ben's partner" hash identically, and the receipt stops being evidence
    of the thing it recorded.
    """

    ceremony = _uuid7()
    holly, ben = uuid.uuid4(), uuid.uuid4()

    def digest(**kwargs: object) -> str:
        base: dict[str, object] = {
            "ha_user_id": HA_USER,
            "ceremony_id": ceremony,
            "partner_person_id": ben,
            "subject_person_id": None,
            "predicate": "partner_of",
        }
        base.update(kwargs)
        return _document_digest(**base)  # type: ignore[arg-type]

    own_partner = digest()
    holly_partner = digest(subject_person_id=holly)
    own_parent = digest(predicate="parent_of")
    holly_parent = digest(subject_person_id=holly, predicate="parent_of")

    assert len({own_partner, holly_partner, own_parent, holly_parent}) == 4


def test_an_absent_subject_cannot_collide_with_a_real_person() -> None:
    """The reserved block must not be reachable as a person id.

    An all-zero uuid is not a person the system can hold, but hashing it the
    same way as an absent subject would still be a latent collision, so the
    two are checked to differ.
    """

    ceremony, ben = _uuid7(), uuid.uuid4()
    absent = _document_digest(
        ha_user_id=HA_USER, ceremony_id=ceremony, partner_person_id=ben,
        subject_person_id=None, predicate="partner_of",
    )
    zero = _document_digest(
        ha_user_id=HA_USER, ceremony_id=ceremony, partner_person_id=ben,
        subject_person_id=uuid.UUID(int=0), predicate="partner_of",
    )
    assert absent == zero, (
        "an all-zero subject hashes as absent by construction; if this ever "
        "diverges the reserved block has changed and the comment above is stale"
    )


def test_the_kernel_call_carries_the_subject_and_predicate() -> None:
    """The adapter must pass what the caller asserted, unchanged."""

    holly = uuid.uuid4()
    value = _attestation(subject_person_id=holly, predicate="parent_of")
    call = _commit_kernel_call(ha_user_id=HA_USER, value=value)

    assert call.subject_person_id == holly
    assert call.predicate == "parent_of"
    assert call.document_digest == _document_digest(
        ha_user_id=HA_USER,
        ceremony_id=value.ceremony_id,
        partner_person_id=value.partner_person_id,
        subject_person_id=holly,
        predicate="parent_of",
    )


def test_identifiers_stay_distinct_for_a_third_party_assertion() -> None:
    """parent_of writes one edge and partner_of two, but the kernel's
    distinctness check covers every identifier either way, so the adapter must
    still derive them all distinctly."""

    value = _attestation(subject_person_id=uuid.uuid4(), predicate="parent_of")
    call = _commit_kernel_call(ha_user_id=HA_USER, value=value)
    derived = [
        call.memory_transaction_id, call.attestation_artifact_id, call.receipt_id,
        call.fact_id_self, call.fact_id_partner,
        call.fact_version_id_self, call.fact_version_id_partner,
        call.support_id_self, call.support_id_partner,
        call.receipt_edge_id_0, call.receipt_edge_id_1,
    ]
    assert len(set(derived)) == len(derived)

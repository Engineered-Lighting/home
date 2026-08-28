"""The attestation route, its adapter, and how they find each other.

A route that reads request.app.state.<name> which main.py never sets fails only
when someone calls it, with a capability message that looks deliberate. These
tests pin the wiring so that cannot happen silently.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
from pydantic import ValidationError

from app.models import OwnerPartnerAttestation
from app.owner_partner_adapter import _commit_kernel_call, _document_digest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _uuid7() -> uuid.UUID:
    value = (1 << 80) | 0x1234
    value &= ~(0xF << 76)
    value |= 0x7 << 76
    value &= ~(0x3 << 62)
    value |= 0x2 << 62
    return uuid.UUID(int=value)


def _attestation(**overrides: object) -> OwnerPartnerAttestation:
    values: dict[str, object] = {
        "ceremony_id": _uuid7(),
        "partner_person_id": uuid.uuid4(),
        "attestation_nonce": uuid.uuid4(),
    }
    values.update(overrides)
    return OwnerPartnerAttestation(**values)  # type: ignore[arg-type]


def test_the_route_reads_an_attribute_main_actually_sets() -> None:
    api = (APP / "api.py").read_text()
    main = (APP / "main.py").read_text()
    assert 'state, "owner_partner_adapter"' in api or \
        "state.owner_partner_adapter" in api
    assert "application.state.owner_partner_adapter" in main


def test_the_adapter_shares_the_committer_credential() -> None:
    """Only home_agent_binding_committer holds EXECUTE, so the adapter must
    reach the database that authenticates as it."""

    main = (APP / "main.py").read_text()
    assert "OwnerPartnerAdapter(parent_relationship_database)" in main
    assert "if parent_relationship_database is not None" in main


def test_the_body_cannot_carry_authority_or_identifiers() -> None:
    for forbidden in ("authority", "receipt_id", "fact_id", "memory_transaction_id"):
        with pytest.raises(ValidationError):
            _attestation(**{forbidden: uuid.uuid4()})


def test_the_ceremony_seed_must_be_uuid7_and_the_nonce_uuid4() -> None:
    with pytest.raises(ValidationError):
        _attestation(ceremony_id=uuid.uuid4())
    with pytest.raises(ValidationError):
        _attestation(attestation_nonce=_uuid7())


def test_identifiers_are_derived_distinct_and_bound_to_the_account() -> None:
    value = _attestation()
    first = _commit_kernel_call(ha_user_id="ha-user-a", value=value)
    again = _commit_kernel_call(ha_user_id="ha-user-a", value=value)
    other = _commit_kernel_call(ha_user_id="ha-user-b", value=value)

    assert first == again, "derivation must be deterministic to allow replay"
    assert first.receipt_id != other.receipt_id, (
        "the same ceremony under a different account must not collide"
    )

    derived = [
        first.memory_transaction_id, first.attestation_artifact_id,
        first.receipt_id, first.fact_id_self, first.fact_id_partner,
        first.fact_version_id_self, first.fact_version_id_partner,
        first.support_id_self, first.support_id_partner,
        first.receipt_edge_id_0, first.receipt_edge_id_1,
    ]
    assert len(set(derived)) == len(derived), "a collision would merge two rows"
    assert all(identifier.version == 7 for identifier in derived)


def test_the_document_digest_binds_the_partner_to_the_attester() -> None:
    """A receipt must not be reinterpretable as being about someone else."""

    ceremony, partner, other = _uuid7(), uuid.uuid4(), uuid.uuid4()
    base = _document_digest(
        ha_user_id="ha-user-a", ceremony_id=ceremony, partner_person_id=partner
    )
    assert base != _document_digest(
        ha_user_id="ha-user-a", ceremony_id=ceremony, partner_person_id=other
    )
    assert base != _document_digest(
        ha_user_id="ha-user-b", ceremony_id=ceremony, partner_person_id=partner
    )
    assert len(base) == 64


def test_the_route_is_pinned_to_the_provisioning_revision() -> None:
    """Before that migration the GRANT does not exist, so an unpinned route
    would fail with a permission error instead of a capability message."""

    api = (APP / "api.py").read_text()
    assert 'OWNER_PARTNER_ADAPTER_REVISION = "0025_owner_partner_caller_e5l"' in api
    assert "readiness_migration" in api

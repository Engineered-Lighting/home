"""Record a partnership the owner asserts.

Unlike the parent-relationship flow, nobody confirms this but the owner. There
is no second party to stage a proposal for and no review code to read aloud, so
the ceremony is a single attested call rather than stage-then-confirm.

What does NOT change is where authority comes from. The client supplies no
primary keys: every identifier is derived by domain-separated SHA-256 from the
ceremony seed, so a caller cannot choose an id that collides with another row or
replays someone else's ceremony. The kernel independently refuses a repeated
identifier, so the derivation is a convenience for honest callers and not the
security boundary.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.exc import DBAPIError

from .db import OwnerPartnerCommitKernelCall
from .errors import ConflictError, ForbiddenError, ValidationDomainError
from .models import OwnerPartnerAttestation, OwnerPartnerAttestationView

# Domain separation is per-ceremony-type on purpose: the same seed used for a
# parent ceremony must not derive the same identifiers here.
_OUTPUT_ID_DOMAIN = b"home-agent:owner-partner:e5k:output-id:v1\0"

_DOMAINS = (
    "memory-transaction",
    "attestation-artifact",
    "authority-receipt",
    "fact-self",
    "fact-partner",
    "fact-version-self",
    "fact-version-partner",
    "support-self",
    "support-partner",
    "receipt-edge-0",
    "receipt-edge-1",
)


def _derived_uuid7(seed: uuid.UUID, *, domain: str, material: bytes) -> uuid.UUID:
    if seed.version != 7:
        raise ValidationDomainError("partner ceremony identity must be UUIDv7")
    timestamp_ms = seed.int >> 80
    digest = hashlib.sha256(
        _OUTPUT_ID_DOMAIN + domain.encode("ascii") + b"\0" + seed.bytes + b"\0" + material
    ).digest()
    # Keep the seed's timestamp so derived rows sort with the ceremony that
    # produced them, and stamp version 7 and the RFC variant.
    value = (timestamp_ms << 80) | (int.from_bytes(digest[:10], "big") & ((1 << 80) - 1))
    value &= ~(0xF << 76)
    value |= 0x7 << 76
    value &= ~(0x3 << 62)
    value |= 0x2 << 62
    return uuid.UUID(int=value)


def _document_digest(
    *, ha_user_id: str, ceremony_id: uuid.UUID, partner_person_id: uuid.UUID
) -> str:
    """What the owner is attesting to, bound to who is attesting it.

    The kernel stores this on the receipt and on the artifact, so a receipt
    cannot be reinterpreted as being about a different partner later.
    """

    return hashlib.sha256(
        b"home-agent:owner-partner:e5k:document:v1\0"
        + hashlib.sha256(ha_user_id.encode("utf-8")).digest()
        + ceremony_id.bytes
        + partner_person_id.bytes
    ).hexdigest()


def _commit_kernel_call(
    *, ha_user_id: str, value: OwnerPartnerAttestation
) -> OwnerPartnerCommitKernelCall:
    digest = _document_digest(
        ha_user_id=ha_user_id,
        ceremony_id=value.ceremony_id,
        partner_person_id=value.partner_person_id,
    )
    material = (
        value.attestation_nonce.bytes
        + bytes.fromhex(digest)
        + hashlib.sha256(ha_user_id.encode("utf-8")).digest()
    )
    derived = {
        domain: _derived_uuid7(value.ceremony_id, domain=domain, material=material)
        for domain in _DOMAINS
    }
    if len(set(derived.values())) != len(_DOMAINS):
        # Cannot happen with distinct domains, but a collision would silently
        # collapse two rows into one, so refuse rather than trust the hash.
        raise ValidationDomainError("derived partner identifiers collided")
    return OwnerPartnerCommitKernelCall(
        authenticated_ha_user_id=ha_user_id,
        ceremony_id=value.ceremony_id,
        partner_person_id=value.partner_person_id,
        document_digest=digest,
        memory_transaction_id=derived["memory-transaction"],
        attestation_artifact_id=derived["attestation-artifact"],
        receipt_id=derived["authority-receipt"],
        fact_id_self=derived["fact-self"],
        fact_id_partner=derived["fact-partner"],
        fact_version_id_self=derived["fact-version-self"],
        fact_version_id_partner=derived["fact-version-partner"],
        support_id_self=derived["support-self"],
        support_id_partner=derived["support-partner"],
        receipt_edge_id_0=derived["receipt-edge-0"],
        receipt_edge_id_1=derived["receipt-edge-1"],
    )


def _raise_commit_error(error: DBAPIError) -> None:
    message = str(getattr(error.orig, "diag", None) and error.orig.diag.message_primary)
    if "owner_partner_e5k_already_recorded" in message:
        raise ConflictError("this partnership is already recorded")
    if "owner_partner_e5k_privacy_blocked" in message:
        raise ForbiddenError("a privacy directive or erasure prevents this")
    if "owner_partner_e5k_reflexive" in message:
        raise ValidationDomainError("a person cannot be their own partner")
    if "owner_partner_e5k_partner_unavailable" in message:
        raise ForbiddenError("that person is not available to be recorded")
    if "owner_partner_e5k_binding_missing" in message:
        raise ForbiddenError("no confirmed binding for this account")
    # Never surface a kernel internal to the caller.
    raise ConflictError("the attestation did not commit")


class OwnerPartnerAdapter:
    def __init__(self, database) -> None:  # ParentRelationshipAuthorityDatabase
        self.database = database

    async def attest(
        self, *, ha_user_id: str, value: OwnerPartnerAttestation
    ) -> OwnerPartnerAttestationView:
        call = _commit_kernel_call(ha_user_id=ha_user_id, value=value)
        for attempt in range(3):
            try:
                receipt_id = await self.database.commit_owner_partner(call)
                return OwnerPartnerAttestationView(
                    receipt_id=receipt_id,
                    partner_person_id=value.partner_person_id,
                    document_digest=call.document_digest,
                )
            except DBAPIError as error:
                sqlstate = getattr(error.orig, "sqlstate", None)
                # Serialization failure and deadlock are expected under
                # SERIALIZABLE and are safe to retry: the kernel is idempotent
                # on ceremony_id, so a retry either commits or replays.
                if sqlstate in {"40001", "40P01"} and attempt < 2:
                    continue
                _raise_commit_error(error)
        raise ConflictError("the attestation did not commit")

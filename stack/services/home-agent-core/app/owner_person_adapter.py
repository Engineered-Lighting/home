"""Add a person the owner vouches for.

The legacy per-item import was retired because it created people with no
auditable provenance and no privacy state decided. This path is narrower on
purpose: the caller says who the person is and how the household may treat
them, and the kernel writes both together or neither.

As with the relationship ceremonies, the client supplies no primary keys. Every
identifier is derived by domain-separated SHA-256 from the ceremony seed, so a
caller can neither collide with an existing row nor replay someone else's
ceremony under a chosen id.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.exc import DBAPIError

from .db import OwnerPersonCreateKernelCall
from .errors import ConflictError, ForbiddenError, ValidationDomainError
from .models import OwnerPersonCreate, OwnerPersonView

_OUTPUT_ID_DOMAIN = b"home-agent:owner-person:e5n:output-id:v1\0"

_DOMAINS = ("person", "attestation-artifact", "directive")


def _derived_uuid7(seed: uuid.UUID, *, domain: str, material: bytes) -> uuid.UUID:
    if seed.version != 7:
        raise ValidationDomainError("person ceremony identity must be UUIDv7")
    timestamp_ms = seed.int >> 80
    digest = hashlib.sha256(
        _OUTPUT_ID_DOMAIN + domain.encode("ascii") + b"\0" + seed.bytes + b"\0" + material
    ).digest()
    value = (timestamp_ms << 80) | (int.from_bytes(digest[:10], "big") & ((1 << 80) - 1))
    value &= ~(0xF << 76)
    value |= 0x7 << 76
    value &= ~(0x3 << 62)
    value |= 0x2 << 62
    return uuid.UUID(int=value)


def _document_digest(*, ha_user_id: str, value: OwnerPersonCreate) -> str:
    """What is being attested, bound to who is attesting it.

    The name and the privacy state are both inside the digest: a receipt must
    not be reinterpretable as vouching for a different person, or for the same
    person under a more permissive directive.
    """

    return hashlib.sha256(
        b"home-agent:owner-person:e5n:document:v1\0"
        + hashlib.sha256(ha_user_id.encode("utf-8")).digest()
        + value.ceremony_id.bytes
        + value.display_name.strip().encode("utf-8")
        + b"\0"
        + value.privacy_scope.encode("ascii")
        + b"\0"
        + (value.directive or "none").encode("ascii")
    ).hexdigest()


def _create_kernel_call(
    *, ha_user_id: str, value: OwnerPersonCreate
) -> OwnerPersonCreateKernelCall:
    digest = _document_digest(ha_user_id=ha_user_id, value=value)
    material = bytes.fromhex(digest) + hashlib.sha256(
        ha_user_id.encode("utf-8")
    ).digest()
    derived = {
        domain: _derived_uuid7(value.ceremony_id, domain=domain, material=material)
        for domain in _DOMAINS
    }
    if len(set(derived.values())) != len(_DOMAINS):
        raise ValidationDomainError("derived person identifiers collided")
    return OwnerPersonCreateKernelCall(
        authenticated_ha_user_id=ha_user_id,
        ceremony_id=value.ceremony_id,
        display_name=value.display_name.strip(),
        pronouns=value.pronouns,
        privacy_scope=value.privacy_scope,
        directive=value.directive,
        directive_expires_at=value.directive_expires_at,
        document_digest=digest,
        person_id=derived["person"],
        attestation_artifact_id=derived["attestation-artifact"],
        directive_id=derived["directive"],
    )


def _raise_create_error(error: DBAPIError) -> None:
    message = str(getattr(error.orig, "diag", None) and error.orig.diag.message_primary)
    if "owner_person_e5n_display_name_invalid" in message:
        raise ValidationDomainError("a display name is required")
    if "owner_person_e5n_privacy_scope_invalid" in message:
        raise ValidationDomainError("that privacy scope is not recognised")
    if "owner_person_e5n_directive_invalid" in message:
        raise ValidationDomainError("that privacy directive is not recognised")
    if "owner_person_e5n_expiry_missing" in message:
        raise ValidationDomainError("auto_expire requires an expiry")
    if "owner_person_e5n_expiry_unexpected" in message:
        raise ValidationDomainError("only auto_expire takes an expiry")
    if "owner_person_e5n_binding_missing" in message:
        raise ForbiddenError("no confirmed binding for this account")
    if "owner_person_e5n_attester_blocked" in message:
        raise ForbiddenError("this account cannot add people")
    raise ConflictError("the person was not created")


class OwnerPersonAdapter:
    def __init__(self, database) -> None:  # ParentRelationshipAuthorityDatabase
        self.database = database

    async def create(
        self, *, ha_user_id: str, value: OwnerPersonCreate
    ) -> OwnerPersonView:
        call = _create_kernel_call(ha_user_id=ha_user_id, value=value)
        for attempt in range(3):
            try:
                person_id = await self.database.create_owner_person(call)
                return OwnerPersonView(
                    person_id=person_id,
                    display_name=call.display_name,
                    privacy_scope=call.privacy_scope,
                )
            except DBAPIError as error:
                sqlstate = getattr(error.orig, "sqlstate", None)
                # Safe to retry: the kernel is idempotent on person_id, which is
                # derived, so a retry either creates or replays.
                if sqlstate in {"40001", "40P01"} and attempt < 2:
                    continue
                _raise_create_error(error)
        raise ConflictError("the person was not created")

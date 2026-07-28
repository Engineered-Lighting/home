"""Authenticated E5g adapter for atomic two-parent confirmation."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.exc import DBAPIError

from .db import (
    ParentRelationshipAuthorityDatabase,
    ParentRelationshipCommitKernelCall,
    ParentRelationshipStageKernelCall,
)
from .errors import (
    CapabilityDisabledError,
    ConflictError,
    NotFoundError,
    ValidationDomainError,
)
from .models import (
    ParentRelationshipConfirmation,
    ParentRelationshipConfirmationView,
    ParentRelationshipPreviewCandidate,
    ParentRelationshipPreviewRequest,
    ParentRelationshipPreviewView,
)


REVIEW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _derived_uuid7(
    seed: uuid.UUID,
    *,
    domain: str,
    material: bytes,
) -> uuid.UUID:
    if seed.version != 7:
        raise ValidationDomainError("parent ceremony identity must be UUIDv7")
    timestamp_ms = seed.int >> 80
    digest = hashlib.sha256(
        b"home-agent:parent-relationship:e5g:output-id:v1\0"
        + domain.encode("ascii")
        + b"\0"
        + seed.bytes
        + b"\0"
        + material
    ).digest()
    random_bits = int.from_bytes(digest, "big") & ((1 << 74) - 1)
    random_a = random_bits >> 62
    random_b = random_bits & ((1 << 62) - 1)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return uuid.UUID(int=value)


def _review_code(
    ceremony_id: uuid.UUID,
    ha_user_id: str,
    ordinal: int,
) -> str:
    digest = hashlib.sha256(
        b"home-agent:parent-relationship:e5g:review-code:v1\0"
        + ceremony_id.bytes
        + b"\0"
        + ha_user_id.encode("utf-8")
        + b"\0"
        + str(ordinal).encode("ascii")
    ).digest()
    return "".join(REVIEW_ALPHABET[value % len(REVIEW_ALPHABET)] for value in digest[:16])


def _stage_kernel_call(
    *,
    ha_user_id: str,
    value: ParentRelationshipPreviewRequest,
) -> ParentRelationshipStageKernelCall:
    material = hashlib.sha256(ha_user_id.encode("utf-8")).digest()
    domains = (
        "request",
        "proposal",
        "operator-request",
        "proposal-edge-0",
        "proposal-edge-1",
    )
    identifiers = tuple(
        _derived_uuid7(
            value.ceremony_id,
            domain=domain,
            material=material,
        )
        for domain in domains
    )
    review_codes = (
        _review_code(value.ceremony_id, ha_user_id, 0),
        _review_code(value.ceremony_id, ha_user_id, 1),
    )
    if len(set(identifiers)) != len(identifiers) or len(set(review_codes)) != 2:
        raise ConflictError("parent preview identity derivation failed")
    return ParentRelationshipStageKernelCall(
        authenticated_ha_user_id=ha_user_id,
        request_id=identifiers[0],
        proposal_id=identifiers[1],
        operator_request_id=identifiers[2],
        proposal_edge_id_0=identifiers[3],
        proposal_edge_id_1=identifiers[4],
        review_code_0=review_codes[0],
        review_code_1=review_codes[1],
    )


def _commit_kernel_call(
    *,
    ha_user_id: str,
    value: ParentRelationshipConfirmation,
) -> ParentRelationshipCommitKernelCall:
    material = (
        value.confirmation_nonce.bytes
        + bytes.fromhex(value.proposal_digest)
        + hashlib.sha256(ha_user_id.encode("utf-8")).digest()
    )
    domains = (
        "confirmation-artifact",
        "memory-transaction",
        "authority-receipt",
        "fact-0",
        "fact-version-0",
        "confirmation-support-0",
        "legacy-support-0",
        "receipt-edge-0",
        "fact-1",
        "fact-version-1",
        "confirmation-support-1",
        "legacy-support-1",
        "receipt-edge-1",
    )
    identifiers = tuple(
        _derived_uuid7(
            value.proposal_id,
            domain=domain,
            material=material,
        )
        for domain in domains
    )
    if (
        len(set(identifiers)) != len(identifiers)
        or value.proposal_id in identifiers
    ):
        raise ConflictError("parent confirmation identity derivation failed")
    return ParentRelationshipCommitKernelCall(
        authenticated_ha_user_id=ha_user_id,
        proposal_id=value.proposal_id,
        proposal_digest=value.proposal_digest,
        confirmation_nonce=value.confirmation_nonce,
        confirmation_artifact_id=identifiers[0],
        memory_transaction_id=identifiers[1],
        authority_receipt_id=identifiers[2],
        fact_id_0=identifiers[3],
        fact_version_id_0=identifiers[4],
        confirmation_support_id_0=identifiers[5],
        legacy_support_id_0=identifiers[6],
        receipt_edge_id_0=identifiers[7],
        fact_id_1=identifiers[8],
        fact_version_id_1=identifiers[9],
        confirmation_support_id_1=identifiers[10],
        legacy_support_id_1=identifiers[11],
        receipt_edge_id_1=identifiers[12],
    )


def _raise_stage_error(exc: DBAPIError) -> None:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "22023":
        raise ValidationDomainError(
            "parent preview input is invalid"
        ) from exc
    if sqlstate == "42501":
        raise CapabilityDisabledError(
            "parent preview authority is unavailable"
        ) from exc
    if sqlstate in {"23505", "23514", "25000", "55000"}:
        raise ConflictError(
            "parent preview is not currently available"
        ) from exc
    raise exc


def _raise_commit_error(exc: DBAPIError) -> None:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "P0002":
        raise NotFoundError("parent proposal does not exist") from exc
    if sqlstate == "22023":
        raise ValidationDomainError(
            "parent confirmation input is invalid"
        ) from exc
    if sqlstate == "42501":
        raise CapabilityDisabledError(
            "parent confirmation authority is unavailable"
        ) from exc
    if sqlstate in {"23505", "23514", "25000", "55000"}:
        raise ConflictError(
            "parent proposal is no longer confirmable"
        ) from exc
    raise exc


class AuthenticatedParentRelationshipAdapter:
    def __init__(self, database: ParentRelationshipAuthorityDatabase) -> None:
        self.database = database

    async def stage(
        self,
        *,
        ha_user_id: str,
        value: ParentRelationshipPreviewRequest,
    ) -> ParentRelationshipPreviewView:
        call = _stage_kernel_call(ha_user_id=ha_user_id, value=value)
        for attempt in range(3):
            try:
                staged = await self.database.stage(call)
                candidates = [
                    ParentRelationshipPreviewCandidate(
                        ordinal=0,
                        reviewed_display_label=staged.parent_0_display_label,
                        review_code=staged.parent_0_review_code,
                    ),
                    ParentRelationshipPreviewCandidate(
                        ordinal=1,
                        reviewed_display_label=staged.parent_1_display_label,
                        review_code=staged.parent_1_review_code,
                    ),
                ]
                return ParentRelationshipPreviewView(
                    proposal_id=staged.proposal_id,
                    proposal_digest=staged.proposal_digest,
                    expires_at=staged.expires_at,
                    child_display_label=staged.child_display_label,
                    candidates=candidates,
                    confirmation_statement=(
                        f"Confirm that {candidates[0].reviewed_display_label} "
                        f"and {candidates[1].reviewed_display_label} are "
                        f"parents of {staged.child_display_label}."
                    ),
                )
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate in {"40001", "40P01"} and attempt < 2:
                    continue
                _raise_stage_error(exc)
        raise ConflictError("parent preview did not stage")

    async def commit(
        self,
        *,
        ha_user_id: str,
        value: ParentRelationshipConfirmation,
    ) -> ParentRelationshipConfirmationView:
        call = _commit_kernel_call(ha_user_id=ha_user_id, value=value)
        for attempt in range(3):
            try:
                committed_at = await self.database.commit(call)
                return ParentRelationshipConfirmationView(
                    confirmed_at=committed_at
                )
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate in {"40001", "40P01"} and attempt < 2:
                    continue
                _raise_commit_error(exc)
        raise ConflictError("parent confirmation did not commit")

    async def close(self) -> None:
        await self.database.close()

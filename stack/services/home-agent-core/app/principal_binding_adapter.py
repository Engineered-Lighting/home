"""Authenticated E5c adapter for the split-credential binding kernel."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError

from .db import PrincipalBindingCommitDatabase, PrincipalBindingKernelCall
from .errors import (
    CapabilityDisabledError,
    ConflictError,
    NotFoundError,
    ValidationDomainError,
)
from .models import PrincipalBindingConfirmation, PrincipalBindingConfirmationView


@dataclass(frozen=True, slots=True)
class PrincipalBindingCommitContext:
    proposal_id: uuid.UUID


def _derived_uuid7(
    proposal_id: uuid.UUID,
    confirmation_nonce: uuid.UUID,
    proposal_digest: str,
    domain: str,
) -> uuid.UUID:
    if proposal_id.version != 7:
        raise ConflictError("binding proposal identity is invalid")
    timestamp_ms = proposal_id.int >> 80
    material = (
        b"home-agent:principal-binding:e5c:output-id:v1\0"
        + domain.encode("ascii")
        + b"\0"
        + proposal_id.bytes
        + confirmation_nonce.bytes
        + bytes.fromhex(proposal_digest)
    )
    random_bits = int.from_bytes(hashlib.sha256(material).digest(), "big")
    random_bits &= (1 << 74) - 1
    random_a = random_bits >> 62
    random_b = random_bits & ((1 << 62) - 1)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return uuid.UUID(int=value)


def _kernel_call(
    *,
    context: PrincipalBindingCommitContext,
    ha_user_id: str,
    value: PrincipalBindingConfirmation,
) -> PrincipalBindingKernelCall:
    domains = (
        "authority-receipt",
        "principal",
        "confirmation-artifact",
        "binding",
    )
    output_ids = tuple(
        _derived_uuid7(
            context.proposal_id,
            value.confirmation_nonce,
            value.proposal_digest,
            domain,
        )
        for domain in domains
    )
    if len(set(output_ids)) != len(output_ids):
        raise ConflictError("binding output identity derivation failed")
    return PrincipalBindingKernelCall(
        proposal_id=context.proposal_id,
        authenticated_ha_user_id=ha_user_id,
        proposal_digest=value.proposal_digest,
        confirmation_nonce=value.confirmation_nonce,
        authority_receipt_id=output_ids[0],
        principal_id=output_ids[1],
        confirmation_artifact_id=output_ids[2],
        binding_id=output_ids[3],
    )


class AuthenticatedPrincipalBindingAdapter:
    def __init__(self, database: PrincipalBindingCommitDatabase) -> None:
        self.database = database

    async def commit(
        self,
        *,
        context: PrincipalBindingCommitContext,
        ha_user_id: str,
        value: PrincipalBindingConfirmation,
    ) -> PrincipalBindingConfirmationView:
        if value.confirmation_nonce.version != 4:
            raise ValidationDomainError(
                "binding confirmation nonce must be UUIDv4"
            )
        call = _kernel_call(context=context, ha_user_id=ha_user_id, value=value)
        for attempt in range(3):
            try:
                confirmed_at = await self.database.commit(call)
                return PrincipalBindingConfirmationView(
                    confirmed_at=confirmed_at
                )
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate in {"40001", "40P01"} and attempt < 2:
                    continue
                if sqlstate == "P0002":
                    raise NotFoundError(
                        "binding proposal does not exist"
                    ) from exc
                if sqlstate == "22023":
                    raise ValidationDomainError(
                        "binding confirmation input is invalid"
                    ) from exc
                if sqlstate == "42501":
                    raise CapabilityDisabledError(
                        "principal binding commit authority is unavailable"
                    ) from exc
                if sqlstate in {"23505", "23514", "25000", "55000"}:
                    raise ConflictError(
                        "binding proposal is no longer confirmable"
                    ) from exc
                raise
        raise ConflictError("binding confirmation did not commit")

    async def close(self) -> None:
        await self.database.close()

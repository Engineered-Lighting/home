"""The exact lighting action the owner reviews and confirms.

A review freezes every operation (home, entity, operation, brightness), each
home's allowlist revision, the owner authority snapshot and a short expiry,
and binds them with an HMAC digest. Confirmation must name that digest; any
change to authority, inventory or scope after review makes it invalid.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .crypto import canonical_json
from .lighting_authority import LightingAuthority
from .lighting_contract import MAX_OPERATIONS, Contract, LightOperation, Site

REVIEW_SECONDS = 60


class SiteRevision(Contract):
    site_id: Site
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class LightingActionReview(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    operations: tuple[LightOperation, ...] = Field(min_length=1, max_length=MAX_OPERATIONS)
    revisions: tuple[SiteRevision, ...] = Field(min_length=1, max_length=2)
    expires_at: datetime
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware review expiry required")
        return value

    @model_validator(mode="after")
    def consistent(self):
        sites = [revision.site_id for revision in self.revisions]
        if len(set(sites)) != len(sites) or {op.site_id for op in self.operations} != set(sites):
            raise ValueError("one revision for exactly each home with operations")
        return self

    def revision(self, site: str) -> str:
        return next(r.revision for r in self.revisions if r.site_id == site)

    def browser_view(self) -> dict:
        """What the owner sees: names and changes, never entity or owner identifiers."""
        return {"version": 1, "operation_id": str(self.operation_id), "expires_at": self.expires_at.isoformat(),
                "reviewed_digest": self.reviewed_digest,
                "operations": [{"site_id": op.site_id, "name": op.name, "operation": op.operation,
                                "brightness": op.brightness} for op in self.operations]}


class LightingActionConfirmation(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


class LightingActionCommitment:
    def __init__(self, key):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("dedicated lighting review key required")
        self._key = hmac.new(key, b"home-agent:lighting:v1:action-review", hashlib.sha256).digest()

    def _digest(self, operation_id, authority, operations, revisions, expires_at):
        payload = {"operation_id": operation_id, "authority": authority.model_dump(exclude={"valid_until"}),
                   "operations": [op.model_dump() for op in operations],
                   "revisions": [r.model_dump() for r in revisions], "expires_at": expires_at}
        return hmac.new(self._key, canonical_json(payload), hashlib.sha256).hexdigest()

    def prepare(self, operation_id, authority, operations, revisions, *, now):
        if (type(operation_id) is not UUID or type(authority) is not LightingAuthority
                or not isinstance(now, datetime) or now.tzinfo is None):
            raise TypeError("governed lighting authority required")
        operations = tuple(operations)
        revisions = tuple(SiteRevision(site_id=site, revision=revision) for site, revision in revisions)
        if any(not authority.granted(op.site_id) for op in operations):
            raise ValueError("lighting is not permitted at every named home")
        expires_at = min(now + timedelta(seconds=REVIEW_SECONDS), authority.valid_until)
        if expires_at <= now:
            raise ValueError("lighting authority already expired")
        return LightingActionReview(operation_id=operation_id, operations=operations, revisions=revisions,
                                    expires_at=expires_at,
                                    reviewed_digest=self._digest(operation_id, authority, operations, revisions,
                                                                 expires_at))

    def verify(self, confirmation, review, authority, *, now):
        if (type(confirmation) is not LightingActionConfirmation or type(review) is not LightingActionReview
                or type(authority) is not LightingAuthority):
            raise TypeError("exact retained lighting review required")
        expected = self._digest(review.operation_id, authority, review.operations, review.revisions, review.expires_at)
        if (confirmation.operation_id != review.operation_id
                or not hmac.compare_digest(confirmation.reviewed_digest, review.reviewed_digest)
                or not hmac.compare_digest(expected, review.reviewed_digest)):
            raise ValueError("lighting authority or reviewed action changed")
        if not now < review.expires_at:
            raise ValueError("lighting review expired")

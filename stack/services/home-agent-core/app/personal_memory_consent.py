"""Exact owner review for sharing the fixed preference source between homes.

This contract grants no authority. A governed writer must obtain the authority
snapshot itself, persist the review, and revalidate it in the grant transaction.
Account linking and model output cannot stand in for the owner's confirmation.
"""
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .crypto import canonical_json
from .personal_memory_contract import Contract, SOURCE


class SourceRevision(Contract):
    source_revision: int = Field(gt=0, lt=9223372036854775807)
    grant_revision: int = Field(ge=0, lt=9223372036854775807)
    grant_id: UUID | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def exact_grant(self):
        if (self.grant_revision == 0) != (self.grant_id is None):
            raise ValueError("existing grant revisions require their exact identity")
        return self


class SharingAuthority(Contract):
    """Private current lookup; never deserialize this from a browser request."""
    principal_id: UUID = Field(repr=False)
    person_id: UUID = Field(repr=False)
    link_id: UUID = Field(repr=False)
    link_revision: int = Field(gt=0, lt=9223372036854775807)
    authorization_generation: int = Field(gt=0, lt=9223372036854775807)
    issuer_id: Literal["home-assistant:echo"]
    subject: str = Field(min_length=1, max_length=64, repr=False)
    session_commitment: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    echo_read: SourceRevision
    echo_write: SourceRevision
    victoria_read: SourceRevision
    victoria_write: SourceRevision
    valid_until: datetime

    @field_validator("valid_until", mode="before")
    @classmethod
    def wire_timestamp(cls, value, info):
        if info.mode == "json" and type(value) is str:
            return datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def canonical(self):
        if (self.subject.strip() != self.subject or any(ord(c) < 32 or ord(c) == 127 for c in self.subject)
                or self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None):
            raise ValueError("invalid sharing authority")
        return self


class SharingOperation(Contract):
    version: Literal[1] = 1
    operation_id: UUID


class SharingConfirmation(SharingOperation):
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


class SharingReview(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    source: Literal["core.personal-preferences.v1"] = SOURCE
    applies_to: Literal["both_homes"] = "both_homes"
    effect: Literal["read_and_manage_confirmed_preferences"] = "read_and_manage_confirmed_preferences"
    grants_expire_at: datetime
    expires_at: datetime
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)

    @field_validator("grants_expire_at", "expires_at", mode="before")
    @classmethod
    def wire_timestamp(cls, value, info):
        if info.mode == "json" and type(value) is str:
            return datetime.fromisoformat(value)
        return value

    @field_validator("grants_expire_at", "expires_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware sharing timestamp required")
        return value


class SharingReviewCommitment:
    def __init__(self, key):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("dedicated sharing review key required")
        self._key = hmac.new(key, b"home-agent:personal-memory:v1:sharing-consent", hashlib.sha256).digest()

    def prepare(self, operation_id, authority, *, now, grants_expire_at, expires_at=None):
        if type(operation_id) is not UUID or type(authority) is not SharingAuthority:
            raise TypeError("governed sharing authority required")
        authority = SharingAuthority.model_validate(authority.model_dump())
        for value in (now, grants_expire_at):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("aware sharing timestamps required")
        limit = min(now + timedelta(seconds=60), authority.valid_until, grants_expire_at)
        expiry = limit if expires_at is None else expires_at
        if (not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry.utcoffset() is None
                or not now < expiry <= limit):
            raise ValueError("sharing review expired or extended")
        payload = {"operation_id": operation_id, "authority": authority.model_dump(), "source": SOURCE,
                   "capabilities": ["memory.read", "personal_memory.write"],
                   "grants_expire_at": grants_expire_at, "expires_at": expiry}
        digest = hmac.new(self._key, canonical_json(payload), hashlib.sha256).hexdigest()
        return SharingReview(operation_id=operation_id, grants_expire_at=grants_expire_at,
                             expires_at=expiry, reviewed_digest=digest)

    def verify(self, confirmation, review, authority, *, now):
        if type(confirmation) is not SharingConfirmation or type(review) is not SharingReview:
            raise TypeError("exact retained sharing review required")
        confirmation = SharingConfirmation.model_validate(confirmation.model_dump())
        review = SharingReview.model_validate(review.model_dump())
        expected = self.prepare(review.operation_id, authority, now=now,
            grants_expire_at=review.grants_expire_at, expires_at=review.expires_at)
        if (confirmation.operation_id != review.operation_id or
                not hmac.compare_digest(confirmation.reviewed_digest, review.reviewed_digest) or
                not hmac.compare_digest(expected.reviewed_digest, review.reviewed_digest)):
            raise ValueError("sharing authority or reviewed scope changed")

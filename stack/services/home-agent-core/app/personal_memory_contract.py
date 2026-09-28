"""Personal-memory v1: the first, deliberately typed preference contract.

These types and review commitments do not authenticate an owner or write data.
The governed transaction must resolve the issuer-qualified session, check its
current link/source grants, serialize revisions, and enforce erasure fences.
No generic fact, identity, location or automation predicate is admitted here.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .crypto import canonical_json


SOURCE = "core.personal-preferences.v1"
Site = Literal["echo", "victoria"]
Tone = Literal["warm", "neutral", "cool"]


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def integer_version(cls, value):
        if isinstance(value, dict) and "version" in value and type(value["version"]) is not int:
            raise ValueError("integer contract version required")
        return value


class EveningLightingPreference(Contract):
    version: Literal[1] = 1
    kind: Literal["personal_preference"] = "personal_preference"
    key: Literal["lighting.evening.tone"] = "lighting.evening.tone"
    scope: Literal["owner"] = "owner"
    value: Tone = Field(repr=False)


class PreferenceProposalRequest(Contract):
    """Browser fields; owner, source, authority and confirmation are absent."""
    version: Literal[1] = 1
    operation_id: UUID
    operation: Literal["remember", "correct", "forget"]
    expected_revision: int = Field(ge=0, lt=9223372036854775807)
    expected_fact_id: UUID | None = Field(default=None, repr=False)
    preference: EveningLightingPreference | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def shape(self):
        if (self.expected_revision == 0) != (self.expected_fact_id is None):
            raise ValueError("existing revisions require their exact fact identity")
        if self.operation != "remember" and self.expected_revision == 0:
            raise ValueError("changes require an existing revision")
        if (self.operation == "forget") != (self.preference is None):
            raise ValueError("forget has no proposed value; writes require a typed preference")
        return self


class PreferenceConfirmation(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


class PreferenceAuthority(Contract):
    """Private result of current governed lookup, NEVER browser deserialization.

    Both linked-account grants are required for the portable owner preference.
    Values alone do not prove that lookup occurred. Database execution must check
    them again under its transaction and delivery must revalidate revocation.
    """
    principal_id: UUID = Field(repr=False)
    person_id: UUID = Field(repr=False)
    link_id: UUID = Field(repr=False)
    authorization_generation: int = Field(gt=0, lt=9223372036854775807)
    issuer_id: Literal["home-assistant:echo", "home-assistant:victoria"]
    site_id: Site
    subject: str = Field(min_length=1, max_length=64, repr=False)
    access: Literal["read", "write"]
    session_commitment: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    echo_grant_revision: int = Field(gt=0, lt=9223372036854775807)
    victoria_grant_revision: int = Field(gt=0, lt=9223372036854775807)
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware timestamp required")
        return value

    @field_validator("subject")
    @classmethod
    def canonical_subject(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("canonical authenticated subject required")
        return value

    @model_validator(mode="after")
    def qualified(self):
        if self.issuer_id != "home-assistant:" + self.site_id:
            raise ValueError("issuer and site disagree")
        return self


class PreferenceReview(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    operation: Literal["remember", "correct", "forget"]
    expected_revision: int = Field(ge=0)
    expected_fact_id: UUID | None = Field(default=None, repr=False)
    current: EveningLightingPreference | None = Field(repr=False)
    proposed: EveningLightingPreference | None = Field(repr=False)
    source_site: Site
    applies_to: Literal["both_homes"] = "both_homes"
    effect: Literal["memory_only"] = "memory_only"
    expires_at: datetime
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


def parse_evening_preference(text: str) -> EveningLightingPreference | None:
    """Recognize a complete direct statement; never extract from arbitrary prose."""
    if not isinstance(text, str) or len(text) > 160:
        return None
    match = re.fullmatch(
        r"\s*(?:remember that\s+)?i prefer (warm|neutral|cool) lighting "
        r"(?:in the evening|in the evenings|at night)[.!]?\s*", text, re.IGNORECASE,
    )
    return EveningLightingPreference(value=match[1].lower()) if match else None


class PreferenceReviewCommitment:
    """Exact review binding for the governed proposal transaction.

    Retain the proposal and original authority snapshot durably before returning
    a review. Verification is not a substitute for atomic revision/grant checks.
    The key must be operator-provisioned, not generated on each process start.
    """
    def __init__(self, key: bytes):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("dedicated 32-byte review key required")
        self._key = hmac.new(key, b"home-agent:personal-memory:v1:review", hashlib.sha256).digest()

    def prepare(self, request: PreferenceProposalRequest, authority: PreferenceAuthority,
                current: EveningLightingPreference | None, *, now: datetime,
                expires_at: datetime | None = None) -> PreferenceReview:
        if type(request) is not PreferenceProposalRequest or type(authority) is not PreferenceAuthority:
            raise TypeError("validated proposal and governed authority required")
        PreferenceAuthority.aware(now)
        if now >= authority.valid_until:
            raise ValueError("authority expired")
        # A forgotten record keeps its revision. Remembering again must name
        # that tombstone revision rather than resetting the history to zero.
        if (request.operation == "remember") != (current is None):
            raise ValueError("current record does not match proposal")
        if current is not None and type(current) is not EveningLightingPreference:
            raise TypeError("typed current preference required")
        deadline = min(now + timedelta(seconds=60), authority.valid_until)
        if expires_at is not None:
            PreferenceAuthority.aware(expires_at)
            if not now < expires_at <= deadline:
                raise ValueError("invalid retained review expiry")
            deadline = expires_at
        body = dict(version=1, operation_id=request.operation_id, operation=request.operation,
                    expected_revision=request.expected_revision, expected_fact_id=request.expected_fact_id, current=current,
                    proposed=request.preference, source_site=authority.site_id,
                    applies_to="both_homes", effect="memory_only", expires_at=deadline)
        payload = {**body, "current": current.model_dump() if current else None,
                   "proposed": request.preference.model_dump() if request.preference else None,
                   "authority": authority.model_dump(), "source": SOURCE}
        digest = hmac.new(self._key, canonical_json(payload), hashlib.sha256).hexdigest()
        return PreferenceReview(**body, reviewed_digest=digest)

    def verify(self, request, authority, current, review, confirmation, *, now):
        if type(review) is not PreferenceReview or type(confirmation) is not PreferenceConfirmation:
            raise TypeError("retained review and explicit confirmation required")
        expected = self.prepare(request, authority, current, now=now, expires_at=review.expires_at)
        if (expected != review or confirmation.operation_id != request.operation_id or
                not hmac.compare_digest(confirmation.reviewed_digest, expected.reviewed_digest)):
            raise ValueError("review changed or confirmation does not match")
        return expected

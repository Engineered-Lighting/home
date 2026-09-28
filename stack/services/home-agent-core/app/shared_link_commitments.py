"""Server-owned preparation for shared-link issuance; not authentication.

Construct only after resolving the authenticated Echo owner and obtaining the
Victoria session through its trusted BFF boundary. Browser JSON must never be
passed directly into this contract. No runtime configuration enables it yet.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .crypto import canonical_json

DIGEST = r"^[0-9a-f]{64}$"


class SharedLinkCeremonyContext(BaseModel):
    """Server-owned IDs and sessions; no caller-selected owner anchor.

    Both session commitments must come from their authenticated BFF boundaries.
    This model validates shape, not the provenance of those sessions.
    """
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    ceremony_id: UUID = Field(repr=False)
    echo_session_commitment: str = Field(pattern=DIGEST, repr=False)
    victoria_session_commitment: str = Field(pattern=DIGEST, repr=False)
    echo_challenge_id: UUID = Field(repr=False)
    victoria_challenge_id: UUID = Field(repr=False)
    echo_challenge_commitment: str = Field(pattern=DIGEST, repr=False)
    victoria_challenge_commitment: str = Field(pattern=DIGEST, repr=False)

    @model_validator(mode="after")
    def distinct_challenges(self):
        if (self.echo_challenge_id == self.victoria_challenge_id or
                self.echo_challenge_commitment == self.victoria_challenge_commitment):
            raise ValueError("each issuer requires a distinct challenge")
        return self


class SharedLinkBegin(BaseModel):
    """Immutable trusted inputs; stable IDs must be retained across dispatch."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    ceremony_id: UUID = Field(repr=False)
    principal_id: UUID = Field(repr=False)
    person_id: UUID = Field(repr=False)
    legacy_binding_id: UUID = Field(repr=False)
    echo_subject: str = Field(min_length=1, max_length=64, repr=False)
    echo_session_commitment: str = Field(pattern=DIGEST, repr=False)
    victoria_session_commitment: str = Field(pattern=DIGEST, repr=False)
    echo_challenge_id: UUID = Field(repr=False)
    victoria_challenge_id: UUID = Field(repr=False)
    echo_challenge_commitment: str = Field(pattern=DIGEST, repr=False)
    victoria_challenge_commitment: str = Field(pattern=DIGEST, repr=False)

    @field_validator("echo_subject")
    @classmethod
    def exact_subject(cls, value):
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid Echo subject")
        return value

    @model_validator(mode="after")
    def distinct_challenges(self):
        if (self.echo_challenge_id == self.victoria_challenge_id or
                self.echo_challenge_commitment == self.victoria_challenge_commitment):
            raise ValueError("each issuer requires a distinct challenge")
        return self


@dataclass(frozen=True, slots=True)
class PreparedSharedLinkBegin:
    submission: SharedLinkBegin = field(repr=False)
    owner_commitment: str = field(repr=False)
    request_commitment: str = field(repr=False)
    key_id: str
    key_fingerprint: str = field(repr=False)

    def parameters(self):
        """Fresh dictionary for a trusted database adapter, never browser output."""
        return {**self.submission.model_dump(), "owner_commitment": self.owner_commitment,
                "request_commitment": self.request_commitment}


class SharedLinkCommitments:
    """Dedicated stable key; changing it requires a reviewed history migration.

    Reuse of unrelated encryption/ledger keys is a provisioning error. Recovery
    must restore the same key reference and verify this fingerprint before new
    issuance; otherwise old revocations could be bypassed by a new owner digest.
    This class deliberately offers no fallback key, key generation or rotation.
    """
    __slots__ = ("_owner_key", "_request_key", "key_id", "fingerprint")

    def __init__(self, key: bytes, *, key_id: str):
        if type(key) is not bytes or len(key) != 32 or not isinstance(key_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", key_id):
            raise ValueError("invalid shared-link commitment configuration")
        self._owner_key = hmac.new(key, b"home-agent:shared-link:v1:owner", hashlib.sha256).digest()
        self._request_key = hmac.new(key, b"home-agent:shared-link:v1:begin-request", hashlib.sha256).digest()
        self.key_id = key_id
        self.fingerprint = hmac.new(key, b"home-agent:shared-link:v1:key-fingerprint", hashlib.sha256).hexdigest()

    def owner(self, person_id: UUID) -> str:
        if not isinstance(person_id, UUID):
            raise TypeError("resolved person UUID required")
        # Stable across principal/binding replacement: authorization history
        # follows the durable person anchor, not a mutable HA account name.
        return hmac.new(self._owner_key, canonical_json({"person_id": str(person_id)}), hashlib.sha256).hexdigest()

    def prepare(self, submission: SharedLinkBegin) -> PreparedSharedLinkBegin:
        if type(submission) is not SharedLinkBegin:
            raise TypeError("trusted shared-link submission required")
        # Revalidate even model_construct/model_copy(update=...) values: those
        # Pydantic APIs can bypass validation despite frozen=True.
        verified = SharedLinkBegin.model_validate(submission.model_dump())
        owner = self.owner(verified.person_id)
        material = {"version": 1, "purpose": "link_echo_victoria",
                    "echo_issuer": "home-assistant:echo", "victoria_issuer": "home-assistant:victoria",
                    "owner_commitment": owner, "request": verified.model_dump(mode="json")}
        request = hmac.new(self._request_key, canonical_json(material), hashlib.sha256).hexdigest()
        return PreparedSharedLinkBegin(verified, owner, request, self.key_id, self.fingerprint)

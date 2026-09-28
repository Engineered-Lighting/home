"""Bind a trusted owner gesture to retained issuance and fresh proof receipts.

These internal objects validate provenance consistency, not authentication.
The route must authenticate the owner, enforce CSRF and obtain explicit consent
to the reviewed pair of accounts. Never deserialize browser JSON into these
inputs. Keys and original operation IDs require durable provisioning/recovery.
"""
import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import ServiceIdentity
from .crypto import canonical_json
from .shared_auth_proof import FreshAuthReceipt
from .shared_link_commitments import SharedLinkBegin
from .shared_link_issuance import SharedLinkIssuanceReceipt
from .shared_link_confirmation import SharedLinkConfirmation


class SharedLinkConfirmationChoice(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    gesture_id: UUID = Field(repr=False)
    session_commitment: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    proposal_id: UUID = Field(repr=False)
    receipt_id: UUID = Field(repr=False)
    link_id: UUID = Field(repr=False)
    echo_binding_id: UUID = Field(repr=False)
    victoria_binding_id: UUID = Field(repr=False)

    @model_validator(mode="after")
    def separate_ids(self):
        ids = (self.gesture_id, self.proposal_id, self.receipt_id, self.link_id,
               self.echo_binding_id, self.victoria_binding_id)
        if len(set(ids)) != len(ids): raise ValueError("distinct confirmation operation IDs required")
        return self


class SharedLinkConfirmationReview(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError("aware review time required")
        return value


class SharedLinkConfirmationApproval(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    gesture_id: UUID = Field(repr=False)
    session_commitment: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def aware(cls, value):
        return SharedLinkConfirmationReview.aware(value)


class SharedLinkConfirmationPreparer:
    def __init__(self, key: bytes, *, key_id: str, now=lambda: datetime.now(UTC)):
        if (type(key) is not bytes or len(key) != 32 or not callable(now) or
            not isinstance(key_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", key_id)):
            raise ValueError("dedicated confirmation key required")
        self._proposal_key = hmac.new(key, b"home-agent:shared-link:v1:confirmation-proposal", hashlib.sha256).digest()
        self._gesture_key = hmac.new(key, b"home-agent:shared-link:v1:owner-confirmation", hashlib.sha256).digest()
        self.key_id = key_id
        self._now = now

    def _context(self, identity: ServiceIdentity, beginning: SharedLinkBegin,
                issuance: SharedLinkIssuanceReceipt, echo: FreshAuthReceipt,
                victoria: FreshAuthReceipt, choice: SharedLinkConfirmationChoice):
        if (type(identity) is not ServiceIdentity or type(beginning) is not SharedLinkBegin or
            type(issuance) is not SharedLinkIssuanceReceipt or type(echo) is not FreshAuthReceipt or
            type(victoria) is not FreshAuthReceipt or type(choice) is not SharedLinkConfirmationChoice):
            raise TypeError("trusted confirmation context required")
        beginning = SharedLinkBegin.model_validate(beginning.model_dump())
        issuance = SharedLinkIssuanceReceipt.model_validate(issuance.model_dump())
        echo = FreshAuthReceipt.model_validate(echo.model_dump())
        victoria = FreshAuthReceipt.model_validate(victoria.model_dump())
        choice = SharedLinkConfirmationChoice.model_validate(choice.model_dump())
        now = self._now()
        if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or
            (identity.ha_issuer_id, identity.site_id, identity.ha_user_id) !=
                ("home-assistant:echo", "echo", beginning.echo_subject) or
            choice.session_commitment != beginning.echo_session_commitment or
            issuance.ceremony_id != beginning.ceremony_id or issuance.revision != 1 or
            issuance.authorization_generation >= 9223372036854775807 or
            issuance.expires_at != issuance.created_at + timedelta(minutes=5) or
            not issuance.created_at <= now < issuance.expires_at or echo.proof_id == victoria.proof_id):
            raise ValueError("confirmation context unavailable")
        for site, proof in (("echo", echo), ("victoria", victoria)):
            if (proof.issuer_id != f"home-assistant:{site}" or
                proof.session_commitment != getattr(beginning, f"{site}_session_commitment") or
                proof.challenge_commitment != getattr(beginning, f"{site}_challenge_commitment") or
                proof.registration_revision != getattr(issuance, f"{site}_registration_revision") or
                not issuance.created_at <= proof.authenticated_at <= proof.issued_at <= now < proof.expires_at or
                proof.expires_at != min(issuance.expires_at, proof.authenticated_at + timedelta(minutes=5))):
                raise ValueError("confirmation proof unavailable")
        if echo.subject != identity.ha_user_id:
            raise ValueError("confirmation owner mismatch")
        material = {"version": 1, "purpose": "link_echo_victoria", "key_id": self.key_id,
            "beginning": beginning.model_dump(mode="json"), "issuance": issuance.model_dump(mode="json"),
            "echo_proof": echo.model_dump(mode="json"), "victoria_proof": victoria.model_dump(mode="json"),
            "choice": choice.model_dump(mode="json")}
        return material, now, beginning, issuance, choice, min(echo.expires_at, victoria.expires_at, issuance.expires_at)

    def _digest(self, material, created_at, expires_at):
        return hmac.new(self._proposal_key, canonical_json({"evidence": material,
            "reviewed_at": created_at.isoformat(), "expires_at": expires_at.isoformat()}), hashlib.sha256).hexdigest()

    def review(self, identity, beginning, issuance, echo, victoria, choice) -> SharedLinkConfirmationReview:
        material, now, _, _, _, expiry = self._context(identity, beginning, issuance, echo, victoria, choice)
        return SharedLinkConfirmationReview(proposal_digest=self._digest(material, now, expiry), created_at=now, expires_at=expiry)

    def verify_review(self, identity, beginning, issuance, echo, victoria, choice, *, review):
        """Validate a retained review without manufacturing owner consent."""
        if type(review) is not SharedLinkConfirmationReview:
            raise TypeError("retained review required")
        review = SharedLinkConfirmationReview.model_validate(review.model_dump())
        material, now, _, _, _, expiry = self._context(identity, beginning, issuance, echo, victoria, choice)
        proposal = self._digest(material, review.created_at, review.expires_at)
        if (review.expires_at != expiry or
            not max(echo.issued_at, victoria.issued_at) <= review.created_at <= now < expiry or
            not hmac.compare_digest(proposal, review.proposal_digest)):
            raise ValueError("confirmation differs from retained review")
        return review

    def prepare(self, identity, beginning, issuance, echo, victoria, choice, *,
                review: SharedLinkConfirmationReview, approval: SharedLinkConfirmationApproval) -> SharedLinkConfirmation:
        if type(review) is not SharedLinkConfirmationReview or type(approval) is not SharedLinkConfirmationApproval:
            raise TypeError("retained review and authenticated approval required")
        review = SharedLinkConfirmationReview.model_validate(review.model_dump())
        approval = SharedLinkConfirmationApproval.model_validate(approval.model_dump())
        material, now, beginning, issuance, choice, expiry = self._context(identity, beginning, issuance, echo, victoria, choice)
        proposal = self._digest(material, review.created_at, review.expires_at)
        if (review.expires_at != expiry or not max(echo.issued_at, victoria.issued_at) <= review.created_at <= approval.approved_at <= now < expiry or
            approval.gesture_id != choice.gesture_id or approval.session_commitment != choice.session_commitment or
            not hmac.compare_digest(proposal, review.proposal_digest) or
            not hmac.compare_digest(proposal, approval.reviewed_digest)):
            raise ValueError("confirmation differs from approved review")
        confirmation = hmac.new(self._gesture_key, canonical_json({"proposal_digest": proposal,
            "gesture_id": str(choice.gesture_id), "session_commitment": choice.session_commitment,
            "approved_at": approval.approved_at.isoformat()}), hashlib.sha256).hexdigest()
        return SharedLinkConfirmation(ceremony_id=beginning.ceremony_id, echo_subject=identity.ha_user_id,
            session_commitment=choice.session_commitment, expected_revision=issuance.revision,
            expected_generation=issuance.authorization_generation, proposal_digest=proposal,
            confirmation_commitment=confirmation,
            **choice.model_dump(exclude={"gesture_id", "session_commitment"}))

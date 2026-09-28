"""Revalidate private coordinator evidence before preparing owner review.

This does not confirm a link. The caller is the credential-bound coordinator;
browser input must never supply these proof receipts or operation identifiers.
"""
from pydantic import BaseModel, ConfigDict, Field

from .auth import ServiceIdentity
from .shared_auth_proof import FreshAuthReceipt, FreshAuthSubmission, SharedLinkProofDatabase
from .shared_link_commitments import SharedLinkCeremonyContext
from .shared_link_confirmation_preparation import SharedLinkConfirmationChoice
from .shared_link_issuance_service import SharedLinkIssuanceService
from .shared_link_review_service import SharedLinkReviewService


class ReviewEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    context: SharedLinkCeremonyContext = Field(repr=False)
    echo: FreshAuthReceipt = Field(repr=False)
    victoria: FreshAuthReceipt = Field(repr=False)
    choice: SharedLinkConfirmationChoice = Field(repr=False)


class SharedLinkReviewAssembly:
    def __init__(self, *, issuance, review, echo_proofs, victoria_proofs):
        if (type(issuance) is not SharedLinkIssuanceService or type(review) is not SharedLinkReviewService or
                type(echo_proofs) is not SharedLinkProofDatabase or type(victoria_proofs) is not SharedLinkProofDatabase or
                echo_proofs.issuer_id != "home-assistant:echo" or victoria_proofs.issuer_id != "home-assistant:victoria"):
            raise TypeError("dedicated review assembly dependencies required")
        self._issuance, self._review = issuance, review
        self._proofs = (echo_proofs, victoria_proofs)

    def uses_review(self, review):
        return review is self._review

    async def prepare(self, identity, evidence):
        if type(identity) is not ServiceIdentity or type(evidence) is not ReviewEvidence:
            raise TypeError("trusted review evidence required")
        value = ReviewEvidence.model_validate(evidence.model_dump())
        if ((identity.ha_issuer_id, identity.site_id) != ("home-assistant:echo", "echo") or
                value.echo.subject != identity.ha_user_id or
                value.choice.session_commitment != value.context.echo_session_commitment):
            raise ValueError("review owner mismatch")
        for site in ("echo", "victoria"):
            proof = getattr(value, site)
            if (proof.issuer_id != f"home-assistant:{site}" or
                    proof.session_commitment != getattr(value.context, f"{site}_session_commitment") or
                    proof.challenge_commitment != getattr(value.context, f"{site}_challenge_commitment")):
                raise ValueError("review proof mismatch")
        issued = await self._issuance.recover(identity, value.context)
        for database, proof in zip(self._proofs, (value.echo, value.victoria)):
            submission = FreshAuthSubmission.model_validate({name: getattr(proof, name) for name in FreshAuthSubmission.model_fields})
            if await database.inspect(submission) != proof:
                raise ValueError("review proof authority unavailable")
        return await self._review.prepare_review(identity, issued, value.echo, value.victoria, value.choice, recover_existing=True)

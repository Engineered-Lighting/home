"""Private retained review evidence. Never a browser request/response model."""
from pydantic import BaseModel, ConfigDict, Field

from .shared_auth_proof import FreshAuthReceipt
from .shared_link_commitments import SharedLinkBegin
from .shared_link_issuance import SharedLinkIssuanceReceipt
from .shared_link_confirmation_preparation import (
    SharedLinkConfirmationChoice, SharedLinkConfirmationReview, SharedLinkConfirmationApproval,
)


class SharedLinkReviewEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    beginning: SharedLinkBegin = Field(repr=False)
    issuance: SharedLinkIssuanceReceipt = Field(repr=False)
    echo: FreshAuthReceipt = Field(repr=False)
    victoria: FreshAuthReceipt = Field(repr=False)
    choice: SharedLinkConfirmationChoice = Field(repr=False)
    review: SharedLinkConfirmationReview = Field(repr=False)
    approval: SharedLinkConfirmationApproval | None = Field(default=None, repr=False)

    def arguments(self, identity):
        return (identity, self.beginning, self.issuance, self.echo, self.victoria, self.choice)

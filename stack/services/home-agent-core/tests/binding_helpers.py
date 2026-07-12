from __future__ import annotations

import uuid

from app.models import (
    OperatorPrincipalBindingProposalStage,
    PrincipalBindingConfirmation,
    PrincipalView,
)
from app.store import CoreStore


async def complete_staged_principal_binding(
    store: CoreStore,
    *,
    ha_user_id: str,
    person_id: uuid.UUID,
) -> PrincipalView:
    """Exercise the same two-party binding protocol used by the live system."""

    request = await store.request_principal_binding(ha_user_id)
    assert request.state == "awaiting_operator_review"
    assert request.review_code is not None

    pending = await store.list_pending_principal_binding_requests()
    matches = [
        item for item in pending.requests if item.review_code == request.review_code
    ]
    assert len(matches) == 1
    pending_request = matches[0]

    staged = await store.stage_principal_binding_proposal(
        OperatorPrincipalBindingProposalStage(
            request_id=pending_request.request_id,
            review_code=pending_request.review_code,
            person_id=person_id,
            operator_request_id=uuid.uuid4(),
        )
    )
    proposal = await store.principal_binding_proposal_status(ha_user_id)
    assert proposal.state == "ready_for_confirmation"
    assert proposal.proposal_digest is not None
    assert proposal.reviewed_display_label == staged.reviewed_display_label

    await store.confirm_principal_binding_proposal(
        ha_user_id,
        PrincipalBindingConfirmation(
            proposal_digest=proposal.proposal_digest,
            confirmation_nonce=uuid.uuid4(),
        ),
    )
    resolved = await store.resolve_principal(ha_user_id)
    return PrincipalView(
        principal_id=resolved["principal_id"],
        person_id=resolved["person_id"],
        ha_user_id=ha_user_id,
        status=resolved["status"],
    )

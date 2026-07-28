from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.db import ParentRelationshipStageKernelResult
from app.ids import uuid7
from app.models import (
    ParentRelationshipConfirmation,
    ParentRelationshipPreviewRequest,
)
from app.parent_relationship_adapter import (
    AuthenticatedParentRelationshipAdapter,
    _commit_kernel_call,
    _stage_kernel_call,
)


def _preview(
    ceremony_id: uuid.UUID | None = None,
) -> ParentRelationshipPreviewRequest:
    return ParentRelationshipPreviewRequest(
        ceremony_id=ceremony_id or uuid7()
    )


def _confirmation(
    *,
    proposal_id: uuid.UUID | None = None,
    nonce: uuid.UUID | None = None,
) -> ParentRelationshipConfirmation:
    return ParentRelationshipConfirmation(
        proposal_id=proposal_id or uuid7(),
        proposal_digest="a" * 64,
        confirmation_nonce=nonce or uuid.uuid4(),
    )


def test_e5g_stage_ids_and_review_codes_are_stable_and_opaque() -> None:
    value = _preview()
    first = _stage_kernel_call(ha_user_id="ha-user", value=value)
    replay = _stage_kernel_call(ha_user_id="ha-user", value=value)

    assert first == replay
    identifiers = {
        first.request_id,
        first.proposal_id,
        first.operator_request_id,
        first.proposal_edge_id_0,
        first.proposal_edge_id_1,
    }
    assert len(identifiers) == 5
    assert all(identifier.version == 7 for identifier in identifiers)
    assert all(identifier.variant == uuid.RFC_4122 for identifier in identifiers)
    assert first.review_code_0 != first.review_code_1
    assert len(first.review_code_0) == len(first.review_code_1) == 16

    other_user = _stage_kernel_call(ha_user_id="other-ha-user", value=value)
    assert other_user.proposal_id != first.proposal_id
    assert other_user.review_code_0 != first.review_code_0


def test_e5g_commit_ids_are_stable_distinct_uuidv7_values() -> None:
    value = _confirmation()
    first = _commit_kernel_call(ha_user_id="ha-user", value=value)
    replay = _commit_kernel_call(ha_user_id="ha-user", value=value)

    assert first == replay
    identifiers = {
        first.confirmation_artifact_id,
        first.memory_transaction_id,
        first.authority_receipt_id,
        first.fact_id_0,
        first.fact_version_id_0,
        first.confirmation_support_id_0,
        first.legacy_support_id_0,
        first.receipt_edge_id_0,
        first.fact_id_1,
        first.fact_version_id_1,
        first.confirmation_support_id_1,
        first.legacy_support_id_1,
        first.receipt_edge_id_1,
    }
    assert len(identifiers) == 13
    assert value.proposal_id not in identifiers
    assert all(identifier.version == 7 for identifier in identifiers)
    assert all(identifier.variant == uuid.RFC_4122 for identifier in identifiers)

    changed_nonce = _commit_kernel_call(
        ha_user_id="ha-user",
        value=_confirmation(proposal_id=value.proposal_id),
    )
    assert changed_nonce.authority_receipt_id != first.authority_receipt_id


def test_e5g_models_reject_non_protocol_ids_and_identity_fields() -> None:
    with pytest.raises(ValidationError, match="UUIDv7"):
        _preview(uuid.uuid4())
    with pytest.raises(ValidationError, match="UUIDv7"):
        _confirmation(proposal_id=uuid.uuid4())
    with pytest.raises(ValidationError, match="UUIDv4"):
        _confirmation(nonce=uuid7())
    with pytest.raises(ValidationError, match="Extra inputs"):
        ParentRelationshipPreviewRequest.model_validate(
            {
                "ceremony_id": str(uuid7()),
                "parent_person_id": str(uuid7()),
            }
        )


@pytest.mark.asyncio
async def test_e5g_adapter_stages_canonical_content_minimized_preview() -> None:
    now = datetime.now(UTC)

    class FakeDatabase:
        calls = []

        async def stage(self, value):
            self.calls.append(value)
            return ParentRelationshipStageKernelResult(
                request_id=value.request_id,
                proposal_id=value.proposal_id,
                proposal_digest="b" * 64,
                expires_at=now + timedelta(minutes=15),
                child_display_label="Marcelo",
                parent_0_display_label="Amelia",
                parent_0_review_code=value.review_code_0,
                parent_1_display_label="Marcelo Sr.",
                parent_1_review_code=value.review_code_1,
            )

    database = FakeDatabase()
    adapter = AuthenticatedParentRelationshipAdapter(database)  # type: ignore[arg-type]
    result = await adapter.stage(
        ha_user_id="ha-user",
        value=_preview(),
    )

    assert result.state == "ready_for_confirmation"
    assert result.confirmation_statement == (
        "Confirm that Amelia and Marcelo Sr. are parents of Marcelo."
    )
    assert [candidate.reviewed_display_label for candidate in result.candidates] == [
        "Amelia",
        "Marcelo Sr.",
    ]
    assert result.creates_exactly_two_parent_facts is True
    assert result.does_not_assert_ownership_residence_or_presence is True
    assert result.location_memory_enabled is False
    assert result.travel_greetings_enabled is False
    assert len(database.calls) == 1
    assert database.calls[0].authenticated_ha_user_id == "ha-user"


@pytest.mark.asyncio
async def test_e5g_adapter_commits_only_the_typed_kernel_call() -> None:
    confirmed_at = datetime.now(UTC)

    class FakeDatabase:
        calls = []

        async def commit(self, value):
            self.calls.append(value)
            return confirmed_at

    database = FakeDatabase()
    adapter = AuthenticatedParentRelationshipAdapter(database)  # type: ignore[arg-type]
    result = await adapter.commit(
        ha_user_id="ha-user",
        value=_confirmation(),
    )

    assert result.state == "confirmed"
    assert result.confirmed_at == confirmed_at
    assert result.fact_count == 2
    assert result.location_memory_enabled is False
    assert result.travel_greetings_enabled is False
    assert len(database.calls) == 1
    assert database.calls[0].authenticated_ha_user_id == "ha-user"

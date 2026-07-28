from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ids import uuid7
from app.models import PrincipalBindingConfirmation
from app.principal_binding_adapter import (
    AuthenticatedPrincipalBindingAdapter,
    PrincipalBindingCommitContext,
    _kernel_call,
)


def _confirmation(nonce: uuid.UUID | None = None) -> PrincipalBindingConfirmation:
    return PrincipalBindingConfirmation(
        proposal_digest="a" * 64,
        confirmation_nonce=nonce or uuid.uuid4(),
    )


def test_e5c_output_ids_are_stable_distinct_uuidv7_values() -> None:
    context = PrincipalBindingCommitContext(
        proposal_id=uuid7(),
    )
    value = _confirmation()

    first = _kernel_call(
        context=context,
        ha_user_id="ha-user",
        value=value,
    )
    replay = _kernel_call(
        context=context,
        ha_user_id="ha-user",
        value=value,
    )

    assert first == replay
    output_ids = {
        first.authority_receipt_id,
        first.principal_id,
        first.confirmation_artifact_id,
        first.binding_id,
    }
    assert len(output_ids) == 4
    assert all(value.version == 7 for value in output_ids)
    assert all(value.variant == uuid.RFC_4122 for value in output_ids)


def test_e5c_output_ids_change_with_confirmation_nonce() -> None:
    context = PrincipalBindingCommitContext(
        proposal_id=uuid7(),
    )
    first = _kernel_call(
        context=context,
        ha_user_id="ha-user",
        value=_confirmation(),
    )
    second = _kernel_call(
        context=context,
        ha_user_id="ha-user",
        value=_confirmation(),
    )
    assert first.authority_receipt_id != second.authority_receipt_id
    assert first.principal_id != second.principal_id
    assert first.confirmation_artifact_id != second.confirmation_artifact_id
    assert first.binding_id != second.binding_id


def test_binding_confirmation_accepts_only_uuidv4_nonce() -> None:
    with pytest.raises(ValidationError, match="random UUIDv4"):
        _confirmation(uuid7())


@pytest.mark.asyncio
async def test_e5c_adapter_passes_only_typed_kernel_call() -> None:
    confirmed_at = datetime.now(UTC)

    class FakeCommitDatabase:
        calls = []

        async def commit(self, value):
            self.calls.append(value)
            return confirmed_at

    database = FakeCommitDatabase()
    adapter = AuthenticatedPrincipalBindingAdapter(database)  # type: ignore[arg-type]
    result = await adapter.commit(
        context=PrincipalBindingCommitContext(
            proposal_id=uuid7(),
        ),
        ha_user_id="ha-user",
        value=_confirmation(),
    )

    assert result.state == "bound"
    assert result.confirmed_at == confirmed_at
    assert result.location_memory_enabled is False
    assert result.travel_greetings_enabled is False
    assert len(database.calls) == 1
    assert database.calls[0].authenticated_ha_user_id == "ha-user"

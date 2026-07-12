from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.errors import CapabilityDisabledError
from app.models import IngestEnvelope
from app.store import CoreStore, stable_observation_root


def _envelope(*, edge_instance_id: str, source_name: str) -> IngestEnvelope:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    return IngestEnvelope(
        edge_instance_id=edge_instance_id,
        source_name=source_name,
        epoch=uuid.UUID("d017707d-cdf1-402e-9b17-9fafba3e0724"),
        sequence=7,
        event_type="conversation_turn",
        source_observed_at=now,
        edge_received_at=now,
        payload={"content_discarded": True},
        dependency_domain="home_assistant:conversation",
        coverage="continuous",
    )


def test_fallback_observation_root_is_replay_stable_and_tuple_unambiguous() -> None:
    first = _envelope(edge_instance_id="edge\x1fsource", source_name="name")
    second = _envelope(edge_instance_id="edge", source_name="source\x1fname")

    # A delimiter-joined representation of these distinct source tuples is
    # identical. Canonical structured encoding must still derive distinct IDs.
    assert "\x1f".join(
        (first.edge_instance_id, first.source_name)
    ) == "\x1f".join((second.edge_instance_id, second.source_name))
    assert stable_observation_root(first) == stable_observation_root(first)
    assert stable_observation_root(first) != stable_observation_root(second)


class _NeverConnection:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("rollout containment must precede database state")


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout_mode", ["record_only", "shadow"])
async def test_non_canary_rollout_hard_suppresses_precise_location_before_consent(
    rollout_mode: str,
) -> None:
    """A stale enabled preference cannot survive as retention authority."""

    store = object.__new__(CoreStore)
    store.settings = SimpleNamespace(rollout_mode=rollout_mode)

    decision = await store._retention_decision(
        _NeverConnection(),
        object(),
        precise_location=True,
    )

    assert decision == (False, "suppressed_rollout_mode", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout_mode", ["record_only", "shadow"])
async def test_non_canary_rollout_hard_suppresses_direct_visit_projection(
    rollout_mode: str,
) -> None:
    """Projection stays fenced even if a caller already has stale raw rows."""

    store = object.__new__(CoreStore)
    store.settings = SimpleNamespace(rollout_mode=rollout_mode)

    await store._project_location_envelope(
        _NeverConnection(),
        object(),
        stream_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout_mode", ["record_only", "shadow"])
async def test_non_canary_rollout_rejects_direct_visit_creation(
    rollout_mode: str,
) -> None:
    store = object.__new__(CoreStore)
    store.settings = SimpleNamespace(rollout_mode=rollout_mode)

    with pytest.raises(CapabilityDisabledError, match="requires rollout mode canary"):
        await store.create_visit({}, object())

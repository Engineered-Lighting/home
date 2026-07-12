from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import column
from sqlalchemy.dialects import postgresql

from app.models import Phase2JourneyEvaluation
from app.phase2 import (
    Phase2EnvelopeSummary,
    build_phase2_readiness,
    consent_precedes_visit,
    half_open_observation_window,
    is_relevant_envelope,
)


START = datetime(2026, 7, 11, 21, 50, tzinfo=UTC)


def summary(*, relevant: int, snapshots: int = 0, gaps: int = 0):
    return Phase2EnvelopeSummary(
        started_at=START,
        total=relevant + snapshots + gaps,
        relevant=relevant,
        snapshots=snapshots,
        gaps=gaps,
        quarantined=0,
    )


def qualifying_journey() -> Phase2JourneyEvaluation:
    return Phase2JourneyEvaluation(
        visit_id=uuid.uuid4(),
        qualifies=True,
        reason_codes=["qualifying_controlled_journey"],
    )


def test_only_real_transition_envelopes_advance_the_event_path() -> None:
    assert is_relevant_envelope("location_fix", "continuous")
    assert is_relevant_envelope("state_changed", "recorder_reconstructed")
    assert not is_relevant_envelope("conversation_turn", "continuous")
    assert not is_relevant_envelope("snapshot", "snapshot_only")
    assert not is_relevant_envelope("coverage_gap", "gap")
    assert not is_relevant_envelope("location_fix", "unknown")


def test_current_consent_cannot_retroactively_qualify_an_old_visit() -> None:
    assert consent_precedes_visit(
        enabled=True,
        consent_updated_at=START,
        visit_created_at=START + timedelta(seconds=1),
    )
    assert not consent_precedes_visit(
        enabled=True,
        consent_updated_at=START + timedelta(seconds=1),
        visit_created_at=START,
    )
    assert not consent_precedes_visit(
        enabled=False,
        consent_updated_at=START,
        visit_created_at=START + timedelta(seconds=1),
    )


def test_journey_envelope_window_is_half_open() -> None:
    expression = half_open_observation_window(
        column("observed_at"), START, START + timedelta(minutes=10)
    )
    compiled = str(
        expression.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "observed_at >=" in compiled
    assert "observed_at <" in compiled
    assert "observed_at <=" not in compiled


def test_500_events_cannot_bypass_the_seven_day_window() -> None:
    result = build_phase2_readiness(
        now=START + timedelta(days=7) - timedelta(seconds=1),
        rollout_mode="record_only",
        envelopes=summary(relevant=500, snapshots=200, gaps=4),
        submitted_journey_count=0,
        unique_journey_count=0,
        journeys=[],
    )

    assert result.evidence_requirement_met
    assert result.threshold_path == "events"
    assert not result.time_requirement_met
    assert not result.ready_to_advance
    assert result.blockers == ["minimum_observation_window_not_elapsed"]
    assert result.relevant_envelopes == 500
    assert result.excluded_snapshot_envelopes == 200
    assert result.excluded_gap_envelopes == 4


def test_three_explicitly_selected_journeys_can_satisfy_evidence_path() -> None:
    journeys = [qualifying_journey() for _ in range(3)]
    result = build_phase2_readiness(
        now=START + timedelta(days=7),
        rollout_mode="record_only",
        envelopes=summary(relevant=12),
        submitted_journey_count=3,
        unique_journey_count=3,
        journeys=journeys,
    )

    assert result.time_requirement_met
    assert result.evidence_requirement_met
    assert result.threshold_path == "journeys"
    assert result.qualifying_controlled_journeys == 3
    assert result.ready_to_advance
    assert result.blockers == []


def test_existing_visits_are_never_automatically_treated_as_journeys() -> None:
    result = build_phase2_readiness(
        now=START + timedelta(days=8),
        rollout_mode="record_only",
        envelopes=summary(relevant=499, snapshots=100, gaps=100),
        submitted_journey_count=0,
        unique_journey_count=0,
        journeys=[],
    )

    assert result.qualifying_controlled_journeys == 0
    assert result.threshold_path == "none"
    assert not result.evidence_requirement_met
    assert not result.ready_to_advance
    assert result.blockers == ["evidence_threshold_not_met"]
    assert result.journeys_are_automatically_inferred is False


def test_gate_cannot_authorize_advancement_from_a_later_rollout_mode() -> None:
    result = build_phase2_readiness(
        now=START + timedelta(days=8),
        rollout_mode="shadow",
        envelopes=summary(relevant=500),
        submitted_journey_count=0,
        unique_journey_count=0,
        journeys=[],
    )

    assert not result.ready_to_advance
    assert result.blockers == ["rollout_mode_not_record_only"]


def test_empty_database_has_explicit_blockers() -> None:
    result = build_phase2_readiness(
        now=START,
        rollout_mode="record_only",
        envelopes=Phase2EnvelopeSummary(None, 0, 0, 0, 0, 0),
        submitted_journey_count=0,
        unique_journey_count=0,
        journeys=[],
    )

    assert result.started_at is None
    assert result.eligible_at is None
    assert result.blockers == [
        "no_durable_envelopes",
        "evidence_threshold_not_met",
    ]

"""Deterministic, read-only observability for the Phase 2 record-only gate.

The event path uses durable envelope headers, so suppressed raw location still
exercises Edge delivery without becoming retained location content.  The
journey path is deliberately non-discovering: an operator must select both the
principal and visit IDs, and Core then proves that each selected visit was
consent-gated, departed, continuously observed, and untouched by a snapshot or
coverage gap.  Merely having a visit row never counts as a controlled journey.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, func, or_, select

from . import schema
from .db import Database
from .models import Phase2JourneyEvaluation, Phase2ReadinessView, RolloutMode


MINIMUM_OBSERVATION_WINDOW = timedelta(days=7)
RELEVANT_ENVELOPE_TARGET = 500
CONTROLLED_JOURNEY_TARGET = 3
MINIMUM_JOURNEY_DWELL = timedelta(minutes=10)
RELEVANT_EVENT_TYPES = ("state_changed", "location_fix")
RELEVANT_COVERAGES = ("continuous", "recorder_reconstructed")
DISQUALIFYING_COVERAGES = ("snapshot_only", "gap", "unknown")


@dataclass(frozen=True, slots=True)
class ControlledJourneyReference:
    """Opaque operator selection; it is never persisted by the inspector."""

    principal_id: uuid.UUID
    visit_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Phase2EnvelopeSummary:
    started_at: datetime | None
    total: int
    relevant: int
    snapshots: int
    gaps: int
    quarantined: int


def is_relevant_envelope(event_type: str, coverage: str) -> bool:
    """Return whether one accepted envelope advances the 500-event path."""

    return event_type in RELEVANT_EVENT_TYPES and coverage in RELEVANT_COVERAGES


def consent_precedes_visit(
    *,
    enabled: bool | None,
    consent_updated_at: datetime | None,
    visit_created_at: datetime,
) -> bool:
    """Reject current consent that was granted only after an old visit."""

    return bool(
        enabled is True
        and consent_updated_at is not None
        and consent_updated_at <= visit_created_at
    )


def half_open_observation_window(column, observed_from: datetime, observed_to: datetime):
    """SQL predicate for the visit's canonical ``[start, end)`` range."""

    return and_(column >= observed_from, column < observed_to)


def build_phase2_readiness(
    *,
    now: datetime,
    rollout_mode: RolloutMode,
    envelopes: Phase2EnvelopeSummary,
    submitted_journey_count: int,
    unique_journey_count: int,
    journeys: Iterable[Phase2JourneyEvaluation],
) -> Phase2ReadinessView:
    """Build a stable gate result from already-inspected evidence."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("phase2 evaluation time must be timezone-aware")
    evaluated_at = now.astimezone(UTC)
    started_at = (
        envelopes.started_at.astimezone(UTC)
        if envelopes.started_at is not None
        else None
    )
    eligible_at = (
        started_at + MINIMUM_OBSERVATION_WINDOW
        if started_at is not None
        else None
    )
    elapsed_seconds = (
        max(0, int((evaluated_at - started_at).total_seconds()))
        if started_at is not None
        else 0
    )
    time_met = eligible_at is not None and evaluated_at >= eligible_at

    journey_results = list(journeys)
    qualifying_journeys = sum(item.qualifies for item in journey_results)
    events_met = envelopes.relevant >= RELEVANT_ENVELOPE_TARGET
    journeys_met = qualifying_journeys >= CONTROLLED_JOURNEY_TARGET
    evidence_met = events_met or journeys_met
    threshold_path = (
        "both"
        if events_met and journeys_met
        else "events"
        if events_met
        else "journeys"
        if journeys_met
        else "none"
    )

    blockers: list[str] = []
    if rollout_mode != "record_only":
        blockers.append("rollout_mode_not_record_only")
    if started_at is None:
        blockers.append("no_durable_envelopes")
    elif not time_met:
        blockers.append("minimum_observation_window_not_elapsed")
    if not evidence_met:
        blockers.append("evidence_threshold_not_met")

    return Phase2ReadinessView(
        evaluated_at=evaluated_at,
        rollout_mode=rollout_mode,
        started_at=started_at,
        eligible_at=eligible_at,
        elapsed_seconds=elapsed_seconds,
        time_requirement_met=time_met,
        total_envelopes=envelopes.total,
        relevant_envelopes=envelopes.relevant,
        relevant_envelopes_remaining=max(
            0, RELEVANT_ENVELOPE_TARGET - envelopes.relevant
        ),
        excluded_snapshot_envelopes=envelopes.snapshots,
        excluded_gap_envelopes=envelopes.gaps,
        quarantined_envelopes=envelopes.quarantined,
        submitted_controlled_journeys=submitted_journey_count,
        unique_controlled_journeys=unique_journey_count,
        qualifying_controlled_journeys=qualifying_journeys,
        controlled_journeys_remaining=max(
            0, CONTROLLED_JOURNEY_TARGET - qualifying_journeys
        ),
        controlled_journeys=journey_results,
        evidence_requirement_met=evidence_met,
        threshold_path=threshold_path,
        ready_to_advance=not blockers,
        blockers=blockers,
    )


class Phase2GateInspector:
    """Read only the metadata required to evaluate the Phase 2 gate."""

    def __init__(self, database: Database, *, rollout_mode: RolloutMode) -> None:
        self.database = database
        self.rollout_mode = rollout_mode

    async def inspect(
        self,
        references: Iterable[ControlledJourneyReference] = (),
        *,
        now: datetime | None = None,
    ) -> Phase2ReadinessView:
        submitted = list(references)
        unique: list[ControlledJourneyReference] = []
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for reference in submitted:
            key = (reference.principal_id, reference.visit_id)
            if key not in seen:
                seen.add(key)
                unique.append(reference)

        summary = await self._envelope_summary()
        journey_results = [
            await self._inspect_controlled_journey(reference, summary.started_at)
            for reference in unique
        ]
        return build_phase2_readiness(
            now=now or datetime.now(UTC),
            rollout_mode=self.rollout_mode,
            envelopes=summary,
            submitted_journey_count=len(submitted),
            unique_journey_count=len(unique),
            journeys=journey_results,
        )

    async def _envelope_summary(self) -> Phase2EnvelopeSummary:
        relevant = and_(
            schema.envelopes.c.event_type.in_(RELEVANT_EVENT_TYPES),
            schema.envelopes.c.coverage.in_(RELEVANT_COVERAGES),
        )
        snapshot = or_(
            schema.envelopes.c.event_type == "snapshot",
            schema.envelopes.c.coverage == "snapshot_only",
        )
        gap = or_(
            schema.envelopes.c.event_type == "coverage_gap",
            schema.envelopes.c.coverage.in_(("gap", "unknown")),
        )
        async with self.database.transaction() as connection:
            row = (
                await connection.execute(
                    select(
                        func.min(schema.envelopes.c.ingested_at).label("started_at"),
                        func.count().label("total"),
                        func.count().filter(relevant).label("relevant"),
                        func.count().filter(snapshot).label("snapshots"),
                        func.count().filter(gap).label("gaps"),
                    ).select_from(schema.envelopes)
                )
            ).mappings().one()
            quarantined = (
                await connection.execute(
                    select(func.count()).select_from(schema.quarantine)
                )
            ).scalar_one()
        return Phase2EnvelopeSummary(
            started_at=row["started_at"],
            total=int(row["total"]),
            relevant=int(row["relevant"]),
            snapshots=int(row["snapshots"]),
            gaps=int(row["gaps"]),
            quarantined=int(quarantined),
        )

    async def _inspect_controlled_journey(
        self,
        reference: ControlledJourneyReference,
        gate_started_at: datetime | None,
    ) -> Phase2JourneyEvaluation:
        reasons: list[str] = []
        async with self.database.transaction(
            principal_id=reference.principal_id
        ) as connection:
            visit = (
                (
                    await connection.execute(
                        select(
                            schema.visits.c.visit_id,
                            schema.visits.c.principal_id,
                            schema.visits.c.first_root_observation_id,
                            schema.visits.c.state,
                            schema.visits.c.coverage,
                            schema.visits.c.created_at,
                            func.lower(schema.visits.c.observed_range).label(
                                "observed_from"
                            ),
                            func.upper(schema.visits.c.observed_range).label(
                                "observed_to"
                            ),
                        ).where(
                            schema.visits.c.visit_id == reference.visit_id,
                            schema.visits.c.principal_id == reference.principal_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if visit is None:
                return Phase2JourneyEvaluation(
                    visit_id=reference.visit_id,
                    qualifies=False,
                    reason_codes=["visit_not_found_for_principal"],
                )

            consent = (
                (
                    await connection.execute(
                        select(
                            schema.preferences.c.enabled,
                            schema.preferences.c.updated_at,
                        ).where(
                            schema.preferences.c.principal_id
                            == reference.principal_id,
                            schema.preferences.c.key == "location_memory",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if consent is None or consent["enabled"] is not True:
                reasons.append("location_consent_not_enabled")
            elif not consent_precedes_visit(
                enabled=consent["enabled"],
                consent_updated_at=consent["updated_at"],
                visit_created_at=visit["created_at"],
            ):
                reasons.append("location_consent_granted_after_visit")
            if gate_started_at is None or visit["created_at"] < gate_started_at:
                reasons.append("visit_outside_gate_window")
            if visit["state"] != "departed":
                reasons.append("visit_not_departed")
            if visit["coverage"] != "sufficient":
                reasons.append("visit_coverage_not_sufficient")

            observed_from = visit["observed_from"]
            observed_to = visit["observed_to"]
            if (
                observed_from is None
                or observed_to is None
                or observed_to <= observed_from
            ):
                reasons.append("invalid_observed_range")
                return Phase2JourneyEvaluation(
                    visit_id=reference.visit_id,
                    qualifies=False,
                    reason_codes=reasons,
                )
            if observed_to - observed_from < MINIMUM_JOURNEY_DWELL:
                reasons.append("dwell_shorter_than_ten_minutes")

            roots = (
                (
                    await connection.execute(
                        select(
                            schema.envelopes.c.stream_id,
                            schema.envelopes.c.event_type,
                            schema.envelopes.c.coverage,
                            schema.envelopes.c.metadata,
                        ).where(
                            schema.envelopes.c.root_observation_id
                            == visit["first_root_observation_id"]
                        )
                    )
                )
                .mappings()
                .all()
            )
            qualifying_roots = [
                row
                for row in roots
                if row["event_type"] == "location_fix"
                and row["coverage"] == "continuous"
                and row["metadata"].get("projection_type") == "device_tracker"
            ]
            stream_ids = {row["stream_id"] for row in qualifying_roots}
            if not qualifying_roots:
                reasons.append("first_root_not_continuous_device_tracker_transition")
            elif len(stream_ids) != 1:
                reasons.append("first_root_spans_multiple_streams")
            else:
                stream_id = next(iter(stream_ids))
                noncontinuous = (
                    await connection.execute(
                        select(func.count())
                        .select_from(schema.envelopes)
                        .where(
                            schema.envelopes.c.stream_id == stream_id,
                            half_open_observation_window(
                                schema.envelopes.c.source_observed_at,
                                observed_from,
                                observed_to,
                            ),
                            or_(
                                schema.envelopes.c.event_type.in_(
                                    ("snapshot", "coverage_gap")
                                ),
                                schema.envelopes.c.coverage.in_(
                                    DISQUALIFYING_COVERAGES
                                ),
                            ),
                        )
                    )
                ).scalar_one()
                if noncontinuous:
                    reasons.append("snapshot_or_gap_envelope_overlaps_journey")

                interval_gaps = (
                    await connection.execute(
                        select(func.count())
                        .select_from(schema.coverage_intervals)
                        .where(
                            schema.coverage_intervals.c.stream_id == stream_id,
                            schema.coverage_intervals.c.status.in_(
                                ("gap", "unknown", "snapshot_only")
                            ),
                            func.lower(schema.coverage_intervals.c.interval)
                            < observed_to,
                            func.upper(schema.coverage_intervals.c.interval)
                            > observed_from,
                        )
                    )
                ).scalar_one()
                if interval_gaps:
                    reasons.append("coverage_gap_overlaps_journey")

        return Phase2JourneyEvaluation(
            visit_id=reference.visit_id,
            qualifies=not reasons,
            reason_codes=reasons or ["qualifying_controlled_journey"],
        )

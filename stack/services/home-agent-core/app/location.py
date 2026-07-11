from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Sequence


Freshness = Literal["fresh", "aging", "stale"]


@dataclass(frozen=True, slots=True)
class LocationFix:
    latitude: float
    longitude: float
    accuracy_m: float | None
    observed_at: datetime
    root_observation_id: uuid.UUID
    tracker_source: str

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("invalid latitude")
        if not -180 <= self.longitude <= 180:
            raise ValueError("invalid longitude")
        if self.accuracy_m is not None and self.accuracy_m < 0:
            raise ValueError("accuracy cannot be negative")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AnchorDecision:
    accepted: bool
    reason_code: str
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int | None = None
    root_observation_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalityDecision:
    accepted: bool
    reason_code: str
    latitude: float | None = None
    longitude: float | None = None
    maximum_accuracy_m: float | None = None
    root_observation_ids: tuple[uuid.UUID, ...] = ()


def freshness_at(observed_at: datetime, *, now: datetime) -> Freshness:
    age = now.astimezone(UTC) - observed_at.astimezone(UTC)
    if age < timedelta(0):
        return "stale"
    if age <= timedelta(minutes=15):
        return "fresh"
    if age <= timedelta(minutes=45):
        return "aging"
    return "stale"


def derive_specific_anchor(
    fixes: Sequence[LocationFix],
    *,
    minimum_dwell: timedelta = timedelta(minutes=10),
) -> AnchorDecision:
    """Derive a conservative property anchor from one sensor over time.

    This establishes temporal consistency, not independent corroboration.
    """

    eligible = sorted(
        (fix for fix in fixes if fix.accuracy_m is not None and fix.accuracy_m <= 50),
        key=lambda fix: fix.observed_at,
    )
    if len(eligible) < 2:
        return AnchorDecision(False, "insufficient_anchor_eligible_fixes")
    if eligible[-1].observed_at - eligible[0].observed_at < minimum_dwell:
        return AnchorDecision(False, "insufficient_dwell")
    if len({fix.tracker_source for fix in eligible}) != 1:
        return AnchorDecision(False, "tracker_source_conflict")

    latitude = statistics.median(fix.latitude for fix in eligible)
    longitude = statistics.median(fix.longitude for fix in eligible)
    median_accuracy = statistics.median(float(fix.accuracy_m) for fix in eligible)
    radius = min(100, max(40, math.ceil(2 * median_accuracy)))

    # Every anchor fix must be mutually compatible with the resulting circle.
    # A small allowance avoids rejecting normal GPS jitter at the boundary.
    if any(
        haversine_m(latitude, longitude, fix.latitude, fix.longitude) > radius + 25
        for fix in eligible
    ):
        return AnchorDecision(False, "incompatible_fixes")

    # Replayed derivatives of the same root do not satisfy the two-fix rule.
    roots = tuple(dict.fromkeys(fix.root_observation_id for fix in eligible))
    if len(roots) < 2:
        return AnchorDecision(False, "duplicate_observation_root")
    return AnchorDecision(
        accepted=True,
        reason_code="anchor_supported",
        latitude=latitude,
        longitude=longitude,
        radius_m=radius,
        root_observation_ids=roots,
    )


def derive_locality_presence(
    fixes: Sequence[LocationFix],
    *,
    minimum_dwell: timedelta = timedelta(minutes=10),
) -> LocalityDecision:
    """Establish stable coarse locality evidence without creating a property anchor.

    Fixes through 250 metres are useful for matching a reviewed locality.  The
    result deliberately has no locator radius: it must never be promoted into
    a specific-place locator or used by the teaching transaction.
    """

    eligible = sorted(
        (fix for fix in fixes if fix.accuracy_m is not None and fix.accuracy_m <= 250),
        key=lambda fix: fix.observed_at,
    )
    if len(eligible) < 2:
        return LocalityDecision(False, "insufficient_locality_eligible_fixes")
    if eligible[-1].observed_at - eligible[0].observed_at < minimum_dwell:
        return LocalityDecision(False, "insufficient_dwell")
    if len({fix.tracker_source for fix in eligible}) != 1:
        return LocalityDecision(False, "tracker_source_conflict")

    latitude = statistics.median(fix.latitude for fix in eligible)
    longitude = statistics.median(fix.longitude for fix in eligible)
    if any(
        haversine_m(latitude, longitude, fix.latitude, fix.longitude) > 250
        for fix in eligible
    ):
        return LocalityDecision(False, "incompatible_fixes")

    roots = tuple(dict.fromkeys(fix.root_observation_id for fix in eligible))
    if len(roots) < 2:
        return LocalityDecision(False, "duplicate_observation_root")
    return LocalityDecision(
        accepted=True,
        reason_code="locality_supported",
        latitude=latitude,
        longitude=longitude,
        maximum_accuracy_m=max(float(fix.accuracy_m) for fix in eligible),
        root_observation_ids=roots,
    )


def visit_state(
    fixes: Sequence[LocationFix],
    *,
    now: datetime,
    coverage: Literal["sufficient", "partial", "gap"],
) -> Literal["candidate", "visiting", "conflicted"]:
    if coverage == "gap":
        return "conflicted"
    decision = derive_locality_presence(fixes)
    if not decision.accepted:
        return (
            "candidate"
            if decision.reason_code != "tracker_source_conflict"
            else "conflicted"
        )
    if freshness_at(max(fix.observed_at for fix in fixes), now=now) == "stale":
        return "candidate"
    return "visiting"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_008.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))

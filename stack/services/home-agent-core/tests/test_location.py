from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.location import (
    LocationFix,
    derive_locality_presence,
    derive_specific_anchor,
    freshness_at,
    visit_state,
)


NOW = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)


def fix(
    minutes: int,
    *,
    latitude: float = -22.40,
    longitude: float = -43.14,
    accuracy: float = 20,
    root: uuid.UUID | None = None,
    source: str = "device_tracker.iphone",
) -> LocationFix:
    return LocationFix(
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy,
        observed_at=NOW + timedelta(minutes=minutes),
        root_observation_id=root or uuid.uuid4(),
        tracker_source=source,
    )


def test_anchor_requires_two_distinct_roots_and_ten_minute_dwell() -> None:
    supported = derive_specific_anchor([fix(0), fix(10, latitude=-22.40005)])
    too_short = derive_specific_anchor([fix(0), fix(9)])
    root = uuid.uuid4()
    replayed_derivative = derive_specific_anchor(
        [fix(0, root=root), fix(10, root=root)]
    )

    assert supported.accepted
    assert supported.radius_m == 40
    assert too_short.reason_code == "insufficient_dwell"
    assert replayed_derivative.reason_code == "duplicate_observation_root"


def test_anchor_rejects_tracker_switch_and_incompatible_jitter() -> None:
    switched = derive_specific_anchor([fix(0), fix(10, source="device_tracker.other")])
    jumped = derive_specific_anchor([fix(0), fix(10, latitude=-22.41)])

    assert switched.reason_code == "tracker_source_conflict"
    assert jumped.reason_code == "incompatible_fixes"


@pytest.mark.parametrize(
    ("accuracy", "locality_accepted", "specific_accepted"),
    [
        (50, True, True),
        (51, True, False),
        (250, True, False),
        (251, False, False),
    ],
)
def test_locality_and_specific_quality_boundaries(
    accuracy: float,
    locality_accepted: bool,
    specific_accepted: bool,
) -> None:
    fixes = [fix(0, accuracy=accuracy), fix(10, accuracy=accuracy)]

    assert derive_locality_presence(fixes).accepted is locality_accepted
    assert derive_specific_anchor(fixes).accepted is specific_accepted


def test_freshness_and_visit_state_do_not_hide_gaps() -> None:
    fixes = [fix(0), fix(10)]
    assert freshness_at(NOW, now=NOW + timedelta(minutes=15)) == "fresh"
    assert freshness_at(NOW, now=NOW + timedelta(minutes=16)) == "aging"
    assert freshness_at(NOW, now=NOW + timedelta(minutes=46)) == "stale"
    assert (
        visit_state(fixes, now=NOW + timedelta(minutes=10), coverage="sufficient")
        == "visiting"
    )
    assert (
        visit_state(fixes, now=NOW + timedelta(minutes=10), coverage="gap")
        == "conflicted"
    )

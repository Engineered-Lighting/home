import math

import pytest
from conftest import F01, F02, HEAD, LEFT, RIGHT, test_settings

from lighting_brain.aim import (add, direction_from_aim, norm, place_reader, plan_spot, scale, segment_point_distance,
                                solve_aim, sub, unit)
from lighting_brain.geometry import Fixture

S = test_settings()
REF_Y = (0.0, 1.0, 0.0)


# --------------------------------------------------- solveAim port (home-apartment-aiming.js)


def test_solve_aim_conventions_match_home_solver():
    origin = (0.0, 0.0, 2.0)
    ahead_low = solve_aim(origin, (0.0, 2.0, 0.0), REF_Y)
    assert ahead_low.executable and ahead_low.pan_deg == pytest.approx(0.0)
    assert ahead_low.tilt_deg == pytest.approx(45.0)  # positive tilt points down
    right = solve_aim(origin, (2.0, 0.0, 2.0), REF_Y)
    assert right.pan_deg == pytest.approx(90.0)  # right = (ref.y, -ref.x) = +x
    left = solve_aim(origin, (-2.0, 0.0, 2.0), REF_Y)
    assert left.pan_deg == pytest.approx(-90.0)
    above = solve_aim(origin, (0.0, 1.0, 3.0), REF_Y)
    assert above.tilt_deg == pytest.approx(-45.0)
    behind = solve_aim(origin, (0.0, -1.0, 2.0), REF_Y)
    assert abs(behind.pan_deg) == pytest.approx(180.0)


def test_solve_aim_vertical_target_is_not_executable():
    result = solve_aim((1.0, 1.0, 2.0), (1.0, 1.0, 0.0), REF_Y, current_pan_deg=12.0)
    assert result.ok and not result.executable
    assert result.pan_deg == 12.0 and result.tilt_deg == pytest.approx(90.0)
    assert result.ambiguity == "vertical_target_pan_indeterminate"
    assert not solve_aim((1, 1, 1), (1, 1, 1), REF_Y).ok


def test_solve_aim_limits():
    limited = solve_aim((0, 0, 2), (0, 1, 0), REF_Y, {"tilt_max_deg": 30.0})
    assert not limited.executable and limited.violations == ("tilt_limit",)
    limited = solve_aim((0, 0, 2), (2, 0, 2), REF_Y, {"pan_min_deg": -45.0, "pan_max_deg": 45.0})
    assert limited.violations == ("pan_limit",)


@pytest.mark.parametrize("target", [(1.3, 2.1, 0.4), (-2.0, 0.5, 1.0), (0.4, -3.0, 0.2), (3.0, 3.0, 2.5)])
@pytest.mark.parametrize("ref", [(0.0, 1.0, 0.0), (0.832, 0.555, 0.0), (-0.748, -0.664, 0.0)])
def test_solve_aim_round_trips_through_direction_from_aim(target, ref):
    origin = (0.2, 0.2, 2.26)
    solution = solve_aim(origin, target, ref)
    direction = direction_from_aim(solution.pan_deg, solution.tilt_deg, ref)
    expected = unit(sub(target, origin))
    assert all(a == pytest.approx(b, abs=1e-9) for a, b in zip(direction, expected))


# --------------------------------------------------- hand-region point


def test_hand_point_level_head():
    reader, why = place_reader(LEFT, RIGHT, HEAD, 0.0, None, S)
    assert why is None
    assert reader.facing == pytest.approx((0.0, 1.0, 0.0))  # z x (right - left)
    assert reader.hand == pytest.approx((1.6, 0.75 + 0.35, 0.95 - 0.25))


def test_hand_point_follows_head_pitch_down():
    reader, _ = place_reader(LEFT, RIGHT, HEAD, 30.0, None, S)
    forward, drop = 0.35 * math.cos(math.radians(30)), 0.35 * math.sin(math.radians(30))
    assert reader.hand == pytest.approx((1.6, 0.75 + forward, 0.95 - drop - 0.25))


def test_head_pitch_is_clamped():
    reader, _ = place_reader(LEFT, RIGHT, HEAD, 120.0, None, S)
    capped, _ = place_reader(LEFT, RIGHT, HEAD, S.head_pitch_max_deg, None, S)
    assert reader.hand == pytest.approx(capped.hand)


def test_facing_points_away_from_camera_over_shoulder_labels():
    # Camera in front of the reader: the contract rule (away from the camera)
    # overrides the anatomical labels.
    reader, _ = place_reader(LEFT, RIGHT, HEAD, 0.0, (1.6, 3.5, 2.0), S)
    assert reader.facing == pytest.approx((0.0, -1.0, 0.0))
    assert reader.facing_source == "away_from_camera"
    reader, _ = place_reader(LEFT, RIGHT, HEAD, 0.0, (1.6, 0.05, 2.0), S)
    assert reader.facing == pytest.approx((0.0, 1.0, 0.0))


def test_missing_or_collapsed_shoulders():
    assert place_reader(None, RIGHT, HEAD, 0.0, None, S) == (None, "shoulders_missing")
    assert place_reader(LEFT, [1.45, 0.75, 0.95], HEAD, 0.0, None, S) == (None, "shoulders_too_close")


def test_head_estimated_when_pose_has_none():
    reader, _ = place_reader(LEFT, RIGHT, None, 0.0, None, S)
    assert reader.head == pytest.approx((1.6, 0.75, 0.95 + S.head_fallback_up_m))
    assert reader.head_source == "estimated_from_shoulders"


# --------------------------------------------------- fixture choice


def reader_at(pitch=20.0):
    return place_reader(LEFT, RIGHT, HEAD, pitch, (1.6, 0.05, 2.0), S)[0]


def test_prefers_behind_and_above(geometry):
    plan = plan_spot(reader_at(), geometry.fixtures, S)
    assert plan.ok and plan.fixture_id == F01
    by_id = {c.fixture_id: c for c in plan.candidates}
    assert by_id[F01].behind_cos > by_id[F02].behind_cos > 0
    assert by_id[F01].above_head_m > 0
    # The two fixtures in front of the reader are refused.
    assert {c.reason for c in plan.candidates if not c.ok} == {"in_front_of_reader"}


def test_above_head_beats_below_head():
    reader = reader_at()
    low = Fixture("low", (1.6, -0.6, 0.9), (0.0, 1.0, 0.0))  # directly behind, below head height
    high = Fixture("high", (0.4, 0.1, 2.3), (0.0, 1.0, 0.0))  # behind-left, above head
    plan = plan_spot(reader, (low, high), S)
    assert plan.fixture_id == "high"


def test_head_sphere_rejects_beam_through_head():
    reader = reader_at()
    # A fixture on the hand -> head line, beyond the head: the beam to the
    # hand passes straight through the head.
    through = add(reader.head, scale(sub(reader.head, reader.hand), 1.5))
    fixture = Fixture("through-head", through, (0.0, 1.0, 0.0))
    plan = plan_spot(reader, (fixture,), S)
    assert not plan.ok
    assert plan.candidates[0].reason == "beam_through_head"
    assert plan.candidates[0].head_clearance_m < S.head_clearance_m
    assert plan.reason == "no_clear_fixture:beam_through_head"


def test_head_sphere_boundary():
    reader = reader_at()
    origin = (0.2, 0.2, 2.26)
    clearance = segment_point_distance(origin, reader.hand, reader.head)
    fixture = Fixture("f", origin, (0.0, 1.0, 0.0))
    assert plan_spot(reader, (fixture,), test_settings(head_clearance_m=clearance - 0.01)).ok
    blocked = plan_spot(reader, (fixture,), test_settings(head_clearance_m=clearance + 0.01))
    assert blocked.candidates[0].reason == "beam_through_head"


def test_pan_tilt_limits_reject_fixture(geometry):
    reader = reader_at()
    tight = Fixture(F01, (0.2, 0.2, 2.26), (0.832, 0.555, 0.0), {"pan_min_deg": -180, "pan_max_deg": 180,
                                                                  "tilt_min_deg": -90, "tilt_max_deg": 10})
    plan = plan_spot(reader, (tight, geometry.fixture(F02)), S)
    assert plan.fixture_id == F02
    assert {c.fixture_id: c.reason for c in plan.candidates}[F01] == "pan_tilt_infeasible:tilt_limit"


def test_paused_fixture_is_excluded_and_preference_holds(geometry):
    reader = reader_at()
    assert plan_spot(reader, geometry.fixtures, S, excluded={F01}).fixture_id == F02
    # F02 scores a little lower than F01; the hysteresis bonus keeps it lit.
    assert plan_spot(reader, geometry.fixtures, S, prefer=F02).fixture_id == F02


def test_unregistered_fixture_position():
    plan = plan_spot(reader_at(), (Fixture("x", None, (0.0, 1.0, 0.0)),), S)
    assert plan.candidates[0].reason == "no_registered_position"


def test_spot_target_is_the_hand_point(geometry):
    reader = reader_at()
    plan = plan_spot(reader, geometry.fixtures, S)
    assert plan.target_m == reader.hand
    assert norm(sub(plan.target_m, reader.head)) > S.head_clearance_m

"""Unit tests for each rule in the RULES table (pure evaluate())."""
import copy
import math
from datetime import datetime, timezone

import pytest
from conftest import (F01, F02, F03, F04, HEAD, LEFT, NOON, RIGHT, activity, cost, health, healthy, occupancy, person,
                      pose, position, reading_scene, test_settings, zone)

from lighting_brain.inputs import Inputs
from lighting_brain.rules import RULES, Override, evaluate, held_output, proximity_level

S = test_settings()


def feed(events, now, settings=S, inputs=None):
    inputs = inputs or Inputs()
    for e in events:
        assert inputs.apply(e, now, settings) is None, e
    return inputs


def run(geometry, inputs, now, settings=S, overrides=None, previous=None):
    return evaluate(inputs, overrides or {}, geometry, settings, now, previous)


def reading_inputs(t0=NOON, t=NOON + 4):
    """Reading since t0, fresh at t."""
    inputs = feed(reading_scene(t0), t0)
    return feed([activity(t), pose(t), occupancy(t)], t, inputs=inputs)


def test_rules_table_is_complete_and_ordered():
    assert [r.name for r in RULES] == ["room_off", "proximity_ambient", "degraded_ambient", "reading_spot_on",
                                       "reading_spot_off", "manual_pause"]
    assert all(r.condition for r in RULES)


# ------------------------------------------------------------------ room_off


def test_room_off_when_never_occupied(geometry):
    ev = run(geometry, Inputs(), NOON)
    assert ev.mode == "off" and ev.mode_rule == "room_off"
    assert ev.why == "Room empty · all off"
    for fo in ev.fixtures:
        assert fo.ambient.radial_level == fo.ambient.down_level == 0 and not fo.spot.on
        assert fo.ambient_rule == fo.spot_rule == "room_off"


def test_room_off_after_two_minutes_of_vacancy(geometry):
    inputs = feed([occupancy(NOON), *healthy(NOON)], NOON)
    feed([occupancy(NOON + 10, present=False)], NOON + 10, inputs=inputs)
    held = run(geometry, inputs, NOON + 129)
    assert held.mode == "ambient" and held.why == "Room just emptied · ambient until off"
    ev = run(geometry, inputs, NOON + 130)
    assert ev.mode == "off" and ev.mode_rule == "room_off"
    assert ev.why == "Room empty for 2 min · all off"
    assert all(fo.ambient.fade_s == 3.0 for fo in ev.fixtures)


def test_bridge_silence_counts_from_last_present_heartbeat(geometry):
    inputs = feed([occupancy(NOON), *healthy(NOON)], NOON)
    assert run(geometry, inputs, NOON + 100).mode == "ambient"  # expired at +90, vacant since NOON
    assert run(geometry, inputs, NOON + 120).mode == "off"


# ------------------------------------------------------------------ proximity_ambient


@pytest.mark.parametrize("distance,expected", [(5.0, 0.10), (3.0, 0.10), (2.0, 0.35), (1.5, 0.475),
                                               (1.0, 0.60), (0.2, 0.60)])
def test_proximity_level_is_linear_between_far_and_near(distance, expected):
    assert proximity_level(distance, S) == pytest.approx(expected)


def test_occupied_without_position_is_flat(geometry):
    ev = run(geometry, feed([occupancy(NOON), *healthy(NOON)], NOON), NOON)
    assert ev.mode == "ambient" and ev.mode_rule == "occupied_ambient"
    assert ev.why == "Room occupied · ambient"
    for fo in ev.fixtures:
        a = fo.ambient
        assert (a.radial_level, a.down_level, a.cct_k, a.fade_s) == (0.30, 0.30, 2700, 2.0)
        assert fo.ambient_rule == "occupied_ambient"


def test_proximity_per_fixture(geometry):
    under_f01 = (0.2, 0.2, 1.26)  # exactly 1.0 m below the aim origin of fixture 01
    ev = run(geometry, feed([occupancy(NOON), position(NOON, under_f01), *healthy(NOON)], NOON), NOON)
    assert ev.mode_rule == "proximity_ambient" and ev.why == "Near fixture 01 · brighter"
    assert ev.fixture(F01).ambient.radial_level == 0.60
    assert ev.fixture(F01).ambient.fade_s == 0.8
    for fid in (F02, F03, F04):
        d = math.dist(under_f01, geometry.fixture(fid).aim_origin_m)
        assert ev.fixture(fid).ambient.radial_level == pytest.approx(round(proximity_level(d, S), 3))


def test_nearest_of_several_people_counts(geometry):
    inputs = feed([occupancy(NOON, people=2), position(NOON, (0.2, 0.2, 1.26), (3.8, 3.4, 1.26)),
                   *healthy(NOON)], NOON)
    ev = run(geometry, inputs, NOON)
    assert ev.fixture(F01).ambient.radial_level == ev.fixture(F03).ambient.radial_level == 0.60


def test_pose_shoulders_also_give_a_position(geometry):
    ev = run(geometry, feed([occupancy(NOON), pose(NOON), *healthy(NOON)], NOON), NOON)
    assert len(ev.facts.positions) == 1 and ev.facts.positions[0] == pytest.approx((1.6, 0.75, 0.95))
    assert ev.mode_rule == "proximity_ambient"


def test_position_expires_after_three_seconds(geometry):
    inputs = feed([occupancy(NOON), position(NOON, (0.2, 0.2, 1.26)), *healthy(NOON)], NOON)
    assert run(geometry, inputs, NOON + 3).mode_rule == "proximity_ambient"
    assert run(geometry, inputs, NOON + 3.1).mode_rule == "occupied_ambient"


def test_deadband_holds_small_moves(geometry):
    inputs = feed([occupancy(NOON), position(NOON, (1.0, 1.0, 1.3)), *healthy(NOON)], NOON)
    first = run(geometry, inputs, NOON)
    feed([position(NOON + 0.5, (1.03, 1.0, 1.3))], NOON + 0.5, inputs=inputs)  # under 0.05 of level
    second = run(geometry, inputs, NOON + 0.5, previous=first)
    assert [f.ambient for f in second.fixtures] == [f.ambient for f in first.fixtures]
    feed([position(NOON + 1, (0.5, 0.5, 1.3))], NOON + 1, inputs=inputs)
    third = run(geometry, inputs, NOON + 1, previous=second)
    assert third.fixture(F01).ambient.radial_level - first.fixture(F01).ambient.radial_level >= 0.05


def test_nearest_name_has_hysteresis(geometry):
    # Halfway between fixtures 01 and 02 along the back wall.
    inputs = feed([occupancy(NOON), position(NOON, (2.0, 0.6, 1.3)), *healthy(NOON)], NOON)
    first = run(geometry, inputs, NOON)
    named = first.nearest_fixture
    nudge = 0.1 if named == F01 else -0.1  # 10 cm toward the other one: not enough to switch
    feed([position(NOON + 1, (2.0 + nudge, 0.6, 1.3))], NOON + 1, inputs=inputs)
    assert run(geometry, inputs, NOON + 1, previous=first).nearest_fixture == named
    feed([position(NOON + 2, (2.0 + 8 * nudge, 0.6, 1.3))], NOON + 2, inputs=inputs)
    assert run(geometry, inputs, NOON + 2, previous=first).nearest_fixture != named


def test_zone_events_are_optional_but_still_presence(geometry):
    ev = run(geometry, feed([zone(NOON), *healthy(NOON)], NOON), NOON)
    assert ev.facts.room_present and ev.mode == "ambient"


def test_two_people_why(geometry):
    inputs = feed([occupancy(NOON, people=2), *healthy(NOON)], NOON)
    assert run(geometry, inputs, NOON).why == "2 people · ambient only"


# ------------------------------------------------------------------ night window


def at_utc(hour, minute=0):
    return datetime(2026, 10, 19, hour, minute, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("hour,minute,night", [
    (22, 59, False), (23, 0, True), (23, 30, True), (0, 0, True), (5, 59, True), (6, 0, False), (12, 0, False),
])
def test_night_window(geometry, hour, minute, night):
    t = at_utc(hour, minute)
    ev = run(geometry, feed([occupancy(t), *healthy(t)], t), t)
    a = ev.fixture(F01).ambient
    if night:
        assert ev.mode == "night_ambient" and (a.radial_level, a.cct_k) == (0.105, 2200)  # 0.30 x 0.35
        assert ev.why == "Night · Room occupied · ambient"
    else:
        assert ev.mode == "ambient" and (a.radial_level, a.cct_k) == (0.30, 2700)


def test_night_scales_proximity(geometry):
    t = at_utc(23, 30)
    ev = run(geometry, feed([occupancy(t), position(t, (0.2, 0.2, 1.26)), *healthy(t)], t), t)
    assert ev.fixture(F01).ambient.radial_level == 0.21 and ev.why == "Night · Near fixture 01 · brighter"


def test_night_window_uses_the_settings_timezone():
    vancouver = test_settings(timezone="-07:00")  # PDT on this date
    assert vancouver.is_night(at_utc(6, 30))  # 23:30 local
    assert not vancouver.is_night(at_utc(14, 0))  # 07:00 local
    assert not test_settings(night_start="23:00", night_end="23:00").is_night(at_utc(23, 30))
    assert test_settings(night_start="01:00", night_end="05:00").is_night(at_utc(2, 0))


def test_default_timezone_is_victoria():
    from lighting_brain.settings import Settings

    pytest.importorskip("tzdata") if not _has_zone_db() else None
    assert Settings().timezone == "America/Vancouver"
    assert Settings().is_night(at_utc(7, 0))  # midnight PDT


def _has_zone_db():
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("America/Vancouver")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ degraded_ambient


@pytest.mark.parametrize("component,why", [("jev", "JEV offline · ambient only"),
                                           ("deep_stack", "Deep stack offline · ambient only")])
def test_degraded_ambient_when_unhealthy(geometry, component, why):
    inputs = reading_inputs()
    feed([health(NOON + 4, component, ok=False)], NOON + 4, inputs=inputs)
    ev = run(geometry, inputs, NOON + 4)
    assert ev.mode == "degraded_ambient" and ev.why == why
    assert not any(fo.spot.on for fo in ev.fixtures)
    assert ev.fixture(F01).ambient.radial_level > 0 and ev.fixture(F01).ambient_rule == "proximity_ambient"
    assert ev.fixture(F01).spot_rule == "degraded_ambient"


def test_degraded_when_health_goes_stale(geometry):
    inputs = feed([occupancy(NOON), zone(NOON), *healthy(NOON)], NOON)
    for t in (NOON + 30, NOON + 60, NOON + 90, NOON + 120, NOON + 150):
        feed([occupancy(t), zone(t)], t, inputs=inputs)
    assert run(geometry, inputs, NOON + 150).mode == "ambient"
    ev = run(geometry, inputs, NOON + 151)
    assert ev.mode == "degraded_ambient" and ev.why == "JEV offline · ambient only"


def test_no_health_yet_is_degraded(geometry):
    ev = run(geometry, feed([occupancy(NOON), zone(NOON)], NOON), NOON)
    assert ev.mode == "degraded_ambient" and ev.why == "JEV not reporting · ambient only"


def test_degraded_does_not_override_off(geometry):
    assert run(geometry, Inputs(), NOON).mode == "off"


# ------------------------------------------------------------------ reading_spot_on


def test_reading_spot_on(geometry):
    ev = run(geometry, reading_inputs(), NOON + 4)
    assert ev.mode == "reading" and ev.mode_rule == "reading_spot_on"
    assert ev.why == "Reading at sofa · spotlight on"
    spot = ev.fixture(F01).spot
    assert spot.on and (spot.level, spot.cct_k, spot.fade_s) == (0.8, 3500, 1.5)
    assert spot.target_m == pytest.approx(ev.facts.reader.hand, abs=1e-3)
    assert ev.fixture(F01).ambient.radial_level > 0  # ambient stays underneath
    assert not any(fo.spot.on for fo in ev.fixtures if fo.fixture_id != F01)
    assert ev.rule_whys["reading_spot_on"] == "Reading at sofa · spotlight on from fixture 01"


def test_reading_needs_three_seconds(geometry):
    inputs = reading_inputs(NOON, NOON + 2)
    ev = run(geometry, inputs, NOON + 2.9)
    assert not any(fo.spot.on for fo in ev.fixtures)
    assert ev.rule_whys["reading_spot_off"] == "Reading for under 3 s · no spotlight"
    assert run(geometry, inputs, NOON + 3).mode == "reading"


@pytest.mark.parametrize("change,why", [
    (lambda t: [occupancy(t, people=2)], "2 people · ambient only"),
    (lambda t: [zone(t, people=2)], "2 people · ambient only"),
    (lambda t: [pose(t, people=[person(), person(track_id="t2")])], "2 people · ambient only"),
    (lambda t: [activity(t, zone_hint=None), pose(t, people=[person([3.0, 3.0, 0.95], [3.36, 3.0, 0.95], None)])],
     "No one at sofa · no spotlight"),
    (lambda t: [activity(t, zone_hint="desk")], "Activity at desk · no spotlight"),
    (lambda t: [pose(t, people=[person(left=None)])], "Pose has no shoulders · no spotlight"),
    (lambda t: [pose(t, people=[person(left=[3.0, 3.0, 0.95], right=[3.36, 3.0, 0.95], head=None)])],
     "Pose not at sofa · no spotlight"),
])
def test_each_precondition_drops_the_spot_at_once(geometry, change, why):
    inputs = reading_inputs()
    feed(change(NOON + 5), NOON + 5, inputs=inputs)
    ev = run(geometry, inputs, NOON + 5)
    assert not any(fo.spot.on for fo in ev.fixtures), why
    assert ev.rule_whys["reading_spot_off"] == why
    assert ev.fixture(F01).spot_rule == "reading_spot_off"
    if "people" in why:
        assert ev.why == why and ev.mode == "ambient"


def test_stale_pose_drops_the_spot(geometry):
    inputs = reading_inputs()
    feed([activity(NOON + 12), zone(NOON + 12), occupancy(NOON + 12)], NOON + 12, inputs=inputs)
    assert run(geometry, inputs, NOON + 13.999).mode == "reading"  # bound source expires at +14, exclusive
    ev = run(geometry, inputs, NOON + 14.5)
    assert ev.mode == "ambient" and ev.rule_whys["reading_spot_off"] == "Pose stale · no spotlight"


def test_no_clear_fixture(geometry):
    # Fixtures 01/02 paused, 03/04 in front of the reader: nothing can light the book.
    overrides = {fid: Override(fid, "change", NOON, NOON + 1800) for fid in (F01, F02)}
    ev = run(geometry, reading_inputs(), NOON + 4, overrides=overrides)
    assert ev.rule_whys["reading_spot_off"].startswith("No clear fixture (")


def test_spot_target_deadband_and_fixture_hysteresis(geometry):
    inputs = reading_inputs()
    first = run(geometry, inputs, NOON + 4)
    nudged = [[x + 0.03, y, z] for x, y, z in (LEFT, RIGHT, HEAD)]
    feed([pose(NOON + 5, people=[person(*nudged)]), activity(NOON + 5)], NOON + 5, inputs=inputs)
    second = run(geometry, inputs, NOON + 5, previous=first)
    assert second.fixture(F01).spot.target_m == first.fixture(F01).spot.target_m
    moved = [[x + 0.3, y, z] for x, y, z in (LEFT, RIGHT, HEAD)]
    feed([pose(NOON + 6, people=[person(*moved)]), activity(NOON + 6)], NOON + 6, inputs=inputs)
    third = run(geometry, inputs, NOON + 6, previous=second)
    lit = third.spot_fixture
    assert lit.spot.target_m != first.fixture(F01).spot.target_m


# ------------------------------------------------------------------ reading_spot_off


def test_spot_holds_ten_seconds_after_reading_stops(geometry):
    inputs = reading_inputs(NOON, NOON + 4)
    # JEV says "resting" from +6; the last reading answer was at +4.
    for t in (NOON + 6, NOON + 9, NOON + 12):
        feed([activity(t, label="resting"), pose(t), zone(t), occupancy(t)], t, inputs=inputs)
    held = run(geometry, inputs, NOON + 13.9)
    assert held.mode == "reading" and held.why == "Reading at sofa · spotlight holding"
    off = run(geometry, inputs, NOON + 14)
    assert off.mode == "ambient" and not any(fo.spot.on for fo in off.fixtures)
    assert off.rule_whys["reading_spot_off"] == "No reading for 10 s · no spotlight"
    assert off.fixture(F01).spot.fade_s == 1.5


def test_short_reading_blip_then_stop_never_lights(geometry):
    inputs = feed(reading_scene(NOON), NOON)
    feed([activity(NOON + 2, label="phone_use")], NOON + 2, inputs=inputs)
    assert not any(fo.spot.on for fo in run(geometry, inputs, NOON + 3).fixtures)


# ------------------------------------------------------------------ manual_pause


def test_manual_pause_holds_fixture_and_others_keep_running(geometry):
    inputs = reading_inputs()
    lit = run(geometry, inputs, NOON + 4)
    overrides = {F01: Override(F01, "change", NOON + 4, NOON + 4 + 1800, held_output(lit, F01))}
    ev = run(geometry, inputs, NOON + 5, overrides=overrides, previous=lit)
    f01 = ev.fixture(F01)
    assert f01.paused_until == NOON + 1804 and f01.pause_reason == "change"
    assert f01.ambient == lit.fixture(F01).ambient and f01.spot == lit.fixture(F01).spot  # untouched
    assert f01.ambient_rule == "manual_pause"
    assert ev.fixture(F02).spot.on  # the spot moved to the next best fixture
    assert ev.rule_whys["manual_pause"] == "Manual change in Home · fixture 01 paused"


def test_manual_pause_survives_room_off_until_the_room_empties(geometry):
    inputs = feed([occupancy(NOON), zone(NOON), *healthy(NOON)], NOON)
    lit = run(geometry, inputs, NOON)
    overrides = {F02: Override(F02, "aim", NOON + 1, NOON + 1801, held_output(lit, F02))}
    feed([occupancy(NOON + 10, present=False), zone(NOON + 10, present=False)], NOON + 10, inputs=inputs)
    ev = run(geometry, inputs, NOON + 129, overrides=overrides)
    assert ev.fixture(F02).paused_until is not None
    ev = run(geometry, inputs, NOON + 130, overrides=overrides)
    assert ev.ended_overrides == ((F02, "fixture 02 back to Auto · room empty"),)
    assert ev.fixture(F02).paused_until is None and ev.fixture(F02).ambient.radial_level == 0


def test_manual_pause_ends_after_thirty_minutes(geometry):
    inputs = feed([occupancy(NOON), zone(NOON), *healthy(NOON)], NOON)
    overrides = {F01: Override(F01, "change", NOON, NOON + 1800)}
    assert run(geometry, inputs, NOON + 1799, overrides=overrides).fixture(F01).paused_until == NOON + 1800
    ev = run(geometry, inputs, NOON + 1800, overrides=overrides)
    assert ev.ended_overrides == ((F01, "fixture 01 back to Auto · 30 min passed"),)


def test_pause_set_in_an_empty_room_lasts_thirty_minutes(geometry):
    overrides = {F01: Override(F01, "change", NOON, NOON + 1800)}
    ev = run(geometry, Inputs(), NOON + 600, overrides=overrides)
    assert ev.fixture(F01).paused_until == NOON + 1800 and not ev.ended_overrides


# ------------------------------------------------------------------ cost, ages, purity


def test_cost_meter_last_hour(geometry):
    inputs = Inputs()
    now = NOON + 4000
    for t, calls, usd in ((NOON, 50, 0.5), (NOON + 500, 10, 0.1), (NOON + 3900, 12, None), (NOON + 3990, 8, 0.02)):
        assert inputs.apply(cost(t, calls, usd, ttl=10_000), now, S) is None
    facts = run(geometry, inputs, now).facts
    assert facts.jev_calls_last_hour == 30 and facts.est_usd_last_hour == pytest.approx(0.12)
    assert run(geometry, Inputs(), now).facts.est_usd_last_hour is None


def test_evaluate_is_pure(geometry):
    inputs = reading_inputs()
    snapshot = copy.deepcopy(inputs)
    overrides = {F02: Override(F02, "change", NOON, NOON + 1800)}
    a = run(geometry, inputs, NOON + 4, overrides=overrides)
    b = run(geometry, inputs, NOON + 4, overrides=copy.deepcopy(overrides))
    assert a.fixtures == b.fixtures and a.why == b.why and a.mode == b.mode
    assert inputs == snapshot


def test_all_levels_and_timings_come_from_settings(geometry):
    custom = test_settings(prox_near_level=0.9, prox_near_m=2.5, ambient_cct_k=3000, spot_level=0.6,
                           spot_cct_k=4000, reading_hold_on_s=1.0, room_off_after_s=30.0)
    inputs = feed(reading_scene(NOON), NOON, settings=custom)
    ev = run(geometry, inputs, NOON + 1, settings=custom)
    assert ev.fixture(F01).ambient.radial_level == 0.9 and ev.fixture(F01).ambient.cct_k == 3000
    flat = test_settings(occupied_level=0.45)
    assert run(geometry, feed([occupancy(NOON), *healthy(NOON)], NOON, settings=flat), NOON,
               settings=flat).fixture(F01).ambient.radial_level == 0.45
    assert (ev.fixture(F01).spot.level, ev.fixture(F01).spot.cct_k) == (0.6, 4000)
    assert math.isclose(ev.facts.vacant_for, 0.0)

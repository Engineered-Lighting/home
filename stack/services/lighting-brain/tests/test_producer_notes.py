"""CONTRACT "Producer notes" (v1 clarifications, 2026-10-19)."""
import pytest
from conftest import (F01, NOON, FakeClock, activity, health, healthy, occupancy, person, pose, reading_scene,
                      position, test_settings, zone)
from test_rules import feed, run
from test_timeline import Replay, ambient_on, spot_on

from lighting_brain.brain import Brain
from lighting_brain.geometry import parse_geometry
from lighting_brain.rules import SENSOR_HOLD_WHY

S = test_settings()


# ------------------------------------------------- 1. Frigate down: unknown, not absent


def sofa_lit_then_frigate_down():
    inputs = feed([occupancy(NOON), *healthy(NOON)], NOON)
    feed([health(NOON + 10, "frigate", ok=False, detail="frigate_unreachable")], NOON + 10, inputs=inputs)
    return inputs


def test_expired_occupancy_with_frigate_down_holds(geometry):
    inputs = sofa_lit_then_frigate_down()
    assert run(geometry, inputs, NOON + 80).mode == "ambient"  # occupancy still fresh
    for t in (NOON + 100, NOON + 300, NOON + 1799):
        ev = run(geometry, inputs, t)
        assert ev.facts.occupancy_unknown
        assert ev.mode == "degraded_ambient" and ev.why == SENSOR_HOLD_WHY
        assert ev.fixture(F01).ambient.radial_level == 0.30  # held, not room_off at +120
    ev = run(geometry, inputs, NOON + 1800)
    assert ev.mode == "off" and ev.mode_rule == "room_off"
    assert ev.why == "No one seen for 30 min · all off"


def test_person_evidence_from_any_source_postpones_room_off(geometry):
    inputs = sofa_lit_then_frigate_down()
    feed([pose(NOON + 1000)], NOON + 1000, inputs=inputs)  # AL-3 still sees someone
    assert run(geometry, inputs, NOON + 2799).mode == "degraded_ambient"
    assert run(geometry, inputs, NOON + 2800).mode == "off"
    # person_count 0 is not evidence.
    inputs = sofa_lit_then_frigate_down()
    feed([activity(NOON + 1000, label="other", persons=0, zone_hint=None)], NOON + 1000, inputs=inputs)
    assert run(geometry, inputs, NOON + 1800).mode == "off"


def test_unknown_occupancy_off_is_a_setting(geometry):
    short = test_settings(unknown_occupancy_off_s=600.0)
    inputs = sofa_lit_then_frigate_down()
    assert run(geometry, inputs, NOON + 599, settings=short).mode == "degraded_ambient"
    assert run(geometry, inputs, NOON + 600, settings=short).why == "No one seen for 10 min · all off"


def test_frigate_healthy_expiry_is_still_absent(geometry):
    inputs = feed([occupancy(NOON), *healthy(NOON)], NOON)
    feed([health(NOON + 60, "frigate")], NOON + 60, inputs=inputs)
    assert run(geometry, inputs, NOON + 120).mode == "off"  # 120 s from the last present


@pytest.fixture
def replay(geometry):
    clock = FakeClock(NOON)
    return Replay(Brain(geometry, test_settings(), clock), clock)


def test_replay_frigate_outage_holds_the_reading_spot(replay):
    replay.run(10, present=True, label="resting")
    replay.run(20, label="reading")
    assert spot_on(replay.state())
    replay.run(200, frigate_ok=False)  # occupancy expires at ~110
    state = replay.state()
    assert state["mode"] == "degraded_ambient" and state["why"] == SENSOR_HOLD_WHY
    assert spot_on(state) and ambient_on(state)  # held: JEV still places the reader on the sofa
    assert not any(s["mode"] == "off" for _, s in replay.timeline)
    # Reading preconditions still turn the spot off during the outage.
    replay.run(240, label="resting")
    off = replay.first(lambda s: not spot_on(s), after=200)
    assert off is not None and off <= 200 + 12
    assert ambient_on(replay.state()) and replay.state()["why"] == SENSOR_HOLD_WHY


def test_replay_no_new_spot_while_the_occupancy_sensor_is_down(replay):
    replay.run(10, present=True, label="resting")
    replay.run(150, frigate_ok=False)
    replay.run(200, label="reading")
    assert not any(spot_on(s) for _, s in replay.timeline)
    assert replay.state()["why"] == SENSOR_HOLD_WHY


def test_replay_occupancy_sensor_recovers(replay):
    replay.run(10, present=True, label="resting")
    replay.run(150, frigate_ok=False)
    replay.run(170, frigate_ok=True, label="reading")
    state = replay.state()
    assert state["mode"] == "reading" and state["why"] == "Reading at sofa · spotlight on"


# ------------------------------------------------- 2. v1.1: the sofa without zones


def test_reader_placed_at_the_seat_by_position_alone(geometry):
    inputs = feed([occupancy(NOON), activity(NOON, zone_hint=None), pose(NOON), *healthy(NOON)], NOON)
    feed([activity(NOON + 4, zone_hint=None), pose(NOON + 4)], NOON + 4, inputs=inputs)
    ev = run(geometry, inputs, NOON + 4)
    assert ev.facts.at_sofa and ev.mode == "reading"


def test_person_position_near_the_seat_counts(geometry):
    inputs = feed([occupancy(NOON), position(NOON, (1.7, 0.9, 1.0)), activity(NOON, zone_hint=None),
                   *healthy(NOON)], NOON)
    assert run(geometry, inputs, NOON).facts.at_sofa
    feed([position(NOON + 1, (3.5, 3.0, 1.0))], NOON + 1, inputs=inputs)
    assert not run(geometry, inputs, NOON + 1).facts.at_sofa


def test_zone_hint_sofa_works_without_a_seat_mark(geometry):
    import json

    from conftest import EXAMPLE_GEOMETRY

    data = json.loads(EXAMPLE_GEOMETRY.read_text())
    data["zones"]["sofa"]["seat_m"] = None  # the owner has not marked the sofa yet
    no_seat = parse_geometry(data)
    inputs = feed(reading_scene(NOON, zone_hint="sofa"), NOON)
    feed([activity(NOON + 4, zone_hint="sofa"), pose(NOON + 4)], NOON + 4, inputs=inputs)
    assert run(no_seat, inputs, NOON + 4).mode == "reading"
    inputs = feed(reading_scene(NOON), NOON)  # no hint and no seat: nothing places the reader
    feed([activity(NOON + 4, zone_hint=None), pose(NOON + 4)], NOON + 4, inputs=inputs)
    assert run(no_seat, inputs, NOON + 4).rule_whys["reading_spot_off"] == "No one at sofa · no spotlight"


def test_sofa_zone_events_are_not_required_or_used_for_the_spot(geometry):
    # A stray "sofa absent" zone event no longer blocks a reader the pose places at the seat.
    inputs = feed([occupancy(NOON), zone(NOON, present=False), activity(NOON, zone_hint=None), pose(NOON),
                   *healthy(NOON)], NOON)
    feed([activity(NOON + 4, zone_hint=None), pose(NOON + 4)], NOON + 4, inputs=inputs)
    assert run(geometry, inputs, NOON + 4).mode == "reading"


# ------------------------------------------------- 3. people_count null with present true


def test_null_occupancy_count_uses_al3_person_count(geometry):
    scene = [occupancy(NOON, people=None), activity(NOON), pose(NOON), *healthy(NOON)]
    inputs = feed(scene, NOON)
    feed([activity(NOON + 4), pose(NOON + 4)], NOON + 4, inputs=inputs)
    ev = run(geometry, inputs, NOON + 4)
    assert ev.facts.people == 1 and ev.mode == "reading"
    # One placed person, but the detector counted two boxes: ambient only.
    feed([pose(NOON + 5, people=[person()], count=2)], NOON + 5, inputs=inputs)
    ev = run(geometry, inputs, NOON + 5)
    assert ev.facts.people == 2 and ev.why == "2 people · ambient only"


def test_people_count_unknown_everywhere(geometry):
    inputs = feed([occupancy(NOON, people=None), activity(NOON, persons=None),
                   *healthy(NOON)], NOON)
    feed([activity(NOON + 4, persons=None)], NOON + 4, inputs=inputs)
    assert run(geometry, inputs, NOON + 4).rule_whys["reading_spot_off"] == "People count unknown · no spotlight"


# ------------------------------------------------- 4. JEV egress blocked, then released


def test_jev_egress_blocked_is_degraded(geometry):
    inputs = feed(reading_scene(NOON), NOON)
    feed([activity(NOON + 4), pose(NOON + 4)], NOON + 4, inputs=inputs)
    feed([health(NOON + 5, "jev", ok=False, detail="jev_egress_blocked_indeterminate")], NOON + 5, inputs=inputs)
    ev = run(geometry, inputs, NOON + 5)
    assert ev.mode == "degraded_ambient" and ev.why == "JEV offline · ambient only"
    assert not ev.fixture(F01).spot.on and ev.fixture(F01).ambient.radial_level > 0


def test_replay_jev_auto_release_brings_the_spot_back(replay):
    replay.run(10, present=True, label="reading")
    assert spot_on(replay.state())
    replay.run(60, jev_ok=False, jev_detail="jev_egress_blocked_indeterminate")
    assert not spot_on(replay.state()) and replay.state()["mode"] == "degraded_ambient"
    replay.run(80, jev_ok=True, jev_detail=None)
    back = replay.first(spot_on, after=60)
    assert back is not None and 60 + 3 <= back <= 60 + 5  # a fresh 3 s reading hold
    assert replay.state()["why"] == "Reading at sofa · spotlight on"

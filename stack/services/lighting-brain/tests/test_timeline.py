"""Timeline replays of every MVP "Done" case (MVP-SPEC-20261019).

A small world simulator plays the producers at their contract cadences
(occupancy/zone heartbeats every 30 s, health every 60 s, JEV every 3 s while
occupied, pose every second) into a real Brain on a fake clock, ticking every
0.5 s like the service. No sleeps.
"""
from dataclasses import dataclass, field

import pytest
from conftest import (F01, NOON, FakeClock, activity, batch, health, occupancy, person, pose, test_settings, zone)

from lighting_brain.brain import Brain

STEP = 0.5


@dataclass
class World:
    present: bool = False
    zone: str | None = None
    people: int = 1
    label: str | None = None
    jev_ok: bool = True
    deep_ok: bool = True
    bridge_up: bool = True
    frigate_ok: bool = True  # AL-2: when False, only health frigate ok:false is sent
    jev_detail: str | None = None
    jetson_up: bool = True
    events_delay_s: float = 0.0  # network/producer lag: observed_at = now - delay


@dataclass
class Replay:
    brain: Brain
    clock: FakeClock
    world: World = field(default_factory=World)
    timeline: list = field(default_factory=list)  # (t, state)
    responses: list = field(default_factory=list)
    _last: dict = field(default_factory=dict)
    _sent: dict = field(default_factory=dict)

    def _due(self, key, period, signature):
        t = self.clock()
        if self._sent.get(key) != signature or t - self._last.get(key, -1e9) >= period - 1e-9:
            self._sent[key], self._last[key] = signature, t
            return True
        return False

    def _emit(self):
        w, t = self.world, self.clock()
        obs = t - w.events_delay_s
        events = []
        if w.bridge_up and not w.frigate_ok:
            if self._due("health:frigate", 60, False):
                events.append(health(obs, "frigate", ok=False, detail="frigate_unreachable"))
        elif w.bridge_up:
            people = w.people if w.present else 0
            if self._due("occupancy", 30, (w.present, people)):
                events.append(occupancy(obs, w.present, people))
            for name in ("sofa", "desk"):
                here = w.present and w.zone == name
                if self._due(f"zone:{name}", 30, here):
                    events.append(zone(obs, name, here, people))
            if self._due("health:frigate", 60, True):
                events.append(health(obs, "frigate"))
        if w.jetson_up:
            for comp, ok in (("jev", w.jev_ok), ("deep_stack", w.deep_ok), ("camera", True)):
                if self._due(f"health:{comp}", 60, ok):
                    detail = w.jev_detail if comp == "jev" and not ok else None
                    events.append(health(obs, comp, ok, detail=detail))
            if w.present and w.deep_ok:
                if w.jev_ok and w.label and self._due("activity", 3, w.label):
                    events.append(activity(obs, w.label, persons=w.people, zone_hint=w.zone))
                if self._due("pose", 1, w.people):
                    people = [person(track_id=f"t{i}") for i in range(w.people)]
                    events.append(pose(obs, people))
        if events:
            self.responses.append(self.brain.ingest("victoria", batch(*events)))

    def run(self, until, **changes):
        """Apply world changes now, then advance to `until` (seconds after NOON)."""
        for key, value in changes.items():
            setattr(self.world, key, value)
        end = NOON + until
        while True:
            self._emit()
            self.brain.tick()
            self.timeline.append((self.clock() - NOON, self.brain.state()))
            if self.clock() + STEP > end + 1e-9:
                break
            self.clock.tick(STEP)
        return self

    def state(self):
        return self.timeline[-1][1]

    def first(self, predicate, after=0.0):
        return next((t for t, s in self.timeline if t >= after and predicate(s)), None)


def spot_on(state):
    return any(f["spot"]["on"] for f in state["fixtures"])


def ambient_on(state):
    return any(f["ambient"]["radial_level"] > 0 for f in state["fixtures"])


def all_off(state):
    return not spot_on(state) and not ambient_on(state)


@pytest.fixture
def replay(geometry):
    clock = FakeClock(NOON)
    return Replay(Brain(geometry, test_settings(), clock), clock)


def settle_reader(replay, t=10):
    """Sit on the sofa from 0 (ambient), start reading at t, run to t + 8."""
    replay.run(t, present=True, zone="sofa", label="resting")
    return replay.run(t + 8, label="reading")


def test_empty_room_stays_off(replay):
    replay.run(600)
    assert all(all_off(s) and s["mode"] == "off" for _, s in replay.timeline)
    assert replay.state()["why"] == "Room empty · all off"
    assert len(replay.state()["decisions"]) == 1  # only the startup entry


def test_entering_the_sofa_zone_fades_ambient_up(replay):
    replay.run(5)
    replay.run(20, present=True, zone="sofa", label="resting")
    t = replay.first(ambient_on)
    assert t == pytest.approx(5.0)
    state = replay.state()
    f01 = next(f for f in state["fixtures"] if f["fixture_id"] == F01)
    assert f01["ambient"] == {"radial_level": 0.3, "down_level": 0.3, "cct_k": 2700, "fade_s": 2.0}
    assert state["mode"] == "ambient" and state["why"] == "Sofa occupied · ambient"
    assert state["decisions"][0]["rule"] == "zone_ambient"


def test_reading_brings_the_spot_within_five_seconds(replay):
    replay.run(10, present=True, zone="sofa", label="resting")
    replay.run(30, label="reading")
    t = replay.first(spot_on)
    assert 10 + 3 <= t <= 10 + 5
    state = replay.state()
    lit = [f for f in state["fixtures"] if f["spot"]["on"]]
    assert [f["fixture_id"] for f in lit] == [F01]
    assert lit[0]["spot"]["cct_k"] == 3500 and lit[0]["spot"]["level"] == 0.8
    assert lit[0]["ambient"]["radial_level"] == 0.3  # ambient stays underneath
    assert state["why"] == "Reading at sofa · spotlight on"
    decision = next(d for d in state["decisions"] if d["rule"] == "reading_spot_on")
    assert decision["why"] == "Reading at sofa · spotlight on from fixture 01"
    assert decision["inputs"]["activity"] == "reading" and decision["inputs"]["zone"] == "sofa"


def test_stopping_reading_fades_the_spot_after_ten_seconds(replay):
    settle_reader(replay)
    replay.run(60, label="resting")
    last_reading = 18 - (18 - 10) % 3  # JEV answers every 3 s; last "reading" before 18
    t = replay.first(lambda s: not spot_on(s), after=18)
    assert t == pytest.approx(last_reading + 10, abs=STEP)
    state = replay.state()
    assert state["mode"] == "ambient"
    off = next(d for d in state["decisions"] if d["rule"] == "reading_spot_off")
    assert off["why"] == "No reading for 10 s · no spotlight"
    f01 = next(f for f in state["fixtures"] if f["fixture_id"] == F01)
    assert f01["spot"]["fade_s"] == 1.5


def test_leaving_turns_everything_off_after_two_minutes(replay):
    settle_reader(replay)
    replay.run(300, present=False, zone=None, label=None)
    left = 18
    t = replay.first(all_off, after=left)
    assert t == pytest.approx(left + 120, abs=STEP)
    assert replay.first(lambda s: not spot_on(s), after=left) <= left + 1  # spot drops at once
    state = replay.state()
    assert state["mode"] == "off"
    assert state["decisions"][0]["rule"] == "room_off"
    assert state["decisions"][0]["why"] == "Room empty for 2 min · all off"
    assert all(f["ambient"]["fade_s"] == 3.0 for f in state["fixtures"])


def test_a_second_person_gives_ambient_only(replay):
    settle_reader(replay)
    replay.run(40, people=2)
    t = replay.first(lambda s: not spot_on(s), after=18)
    assert t == pytest.approx(18, abs=STEP)
    state = replay.state()
    assert state["mode"] == "ambient" and ambient_on(state)
    assert state["why"] == "2 people · ambient only"
    # And nobody alone on the sofa reading with a friend ever gets the spot.
    replay.run(80)
    assert not any(spot_on(s) for t, s in replay.timeline if t > 18.5)


def test_manual_change_pauses_only_that_fixture(replay):
    settle_reader(replay)
    replay.brain.set_override("victoria", {"schema": "activity-lighting-override-1", "fixture_id": F01,
                                           "reason": "change", "at": replay.clock()})
    replay.run(30)
    state = replay.state()
    by = {f["fixture_id"]: f for f in state["fixtures"]}
    assert by[F01]["paused_until"] == pytest.approx(NOON + 18 + 1800)
    assert by[F01]["pause_reason"] == "change"
    lit = [fid for fid, f in by.items() if f["spot"]["on"] and f["paused_until"] is None]
    assert lit == ["victoria-den-proposed-fixture-02"]  # the others keep running
    pause = next(d for d in state["decisions"] if d["rule"] == "manual_pause")
    assert pause["why"] == "Manual change in Home · fixture 01 paused"
    # Leaving the room ends the pause (after room_off).
    replay.run(200, present=False, zone=None, label=None)
    state = replay.state()
    assert all(f["paused_until"] is None for f in state["fixtures"])
    assert any(d["why"] == "fixture 01 back to Auto · room empty" for d in state["decisions"])


def test_manual_pause_cleared_from_home(replay):
    settle_reader(replay)
    replay.brain.set_override("victoria", {"schema": "activity-lighting-override-1", "fixture_id": F01,
                                           "reason": "aim", "at": replay.clock()})
    replay.run(25)
    result = replay.brain.clear_override("victoria", F01)
    assert result == {"ok": True, "fixture_id": F01, "cleared": True}
    replay.run(27)
    state = replay.state()
    assert state["decisions"][0]["rule"] == "manual_pause"
    assert state["decisions"][0]["why"] == "fixture 01 back to Auto · cleared in Home"
    assert next(f for f in state["fixtures"] if f["fixture_id"] == F01)["paused_until"] is None


def test_losing_jev_gives_ambient_only(replay):
    settle_reader(replay)
    replay.run(40, jev_ok=False)
    t = replay.first(lambda s: not spot_on(s), after=18)
    assert t == pytest.approx(18, abs=STEP)
    state = replay.state()
    assert state["mode"] == "degraded_ambient" and ambient_on(state)
    assert state["why"] == "JEV offline · ambient only"
    assert state["inputs"]["health"]["jev"] is False


def test_jetson_going_silent_gives_ambient_only(replay):
    settle_reader(replay)
    replay.run(200, jetson_up=False)
    # Activity (ttl 12 s) and pose (ttl 10 s) go stale first, then health (150 s).
    t = replay.first(lambda s: not spot_on(s), after=18)
    assert 18 < t <= 18 + 11
    state = replay.state()
    assert state["mode"] == "degraded_ambient" and ambient_on(state)
    assert not any(spot_on(s) for t, s in replay.timeline if t > 30)


def test_stale_and_expired_events_are_ignored(replay):
    replay.run(5)
    # A producer replaying a backlog: everything is older than its ttl.
    late = replay.brain.ingest("victoria", batch(occupancy(NOON - 200), zone(NOON - 200),
                                                 activity(NOON - 100), pose(NOON - 100)))
    assert late["accepted"] == 0
    assert {i["reason"] for i in late["ignored"]} == {"expired"}
    replay.run(10)
    assert all_off(replay.state())
    # Out-of-order: an older "present" after a newer "absent" is stale.
    replay.brain.ingest("victoria", batch(occupancy(NOON + 10, present=False)))
    stale = replay.brain.ingest("victoria", batch(occupancy(NOON + 9)))
    assert stale["ignored"] == [{"index": 0, "type": "occupancy", "reason": "stale"}]
    assert replay.brain.health()["events"]["ignored_stale"] == 1
    assert replay.brain.health()["events"]["ignored_expired"] == 4


def test_lagging_producers_still_light_within_five_seconds(replay):
    replay.world.events_delay_s = 1.0
    replay.run(10, present=True, zone="sofa", label="resting")
    replay.run(30, label="reading")
    # The 3 s hold runs on observed_at (JEV saw reading at 9); still within 5 s.
    assert 9 + 3 <= replay.first(spot_on) <= 10 + 5


def test_night_reading_scene_uses_night_ambient(geometry):
    from datetime import datetime, timezone

    night = datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc).timestamp()
    clock = FakeClock(night)
    r = Replay(Brain(geometry, test_settings(), clock), clock)
    # Replay.run measures from NOON; drive the clock relative to the night start instead.
    r.world.present, r.world.zone, r.world.label = True, "sofa", "reading"
    for _ in range(20):
        r._emit()
        r.brain.tick()
        clock.tick(STEP)
    state = r.brain.state()
    f01 = next(f for f in state["fixtures"] if f["fixture_id"] == F01)
    assert (f01["ambient"]["radial_level"], f01["ambient"]["cct_k"]) == (0.1, 2200)
    assert f01["spot"]["on"] and state["mode"] == "reading"


def test_decision_log_explains_each_step_and_keeps_twenty(replay):
    settle_reader(replay)
    replay.run(60, label="resting")
    replay.run(200, present=False, zone=None, label=None)
    rules = [d["rule"] for d in reversed(replay.state()["decisions"])]
    assert rules[:5] == ["room_off", "zone_ambient", "reading_spot_on", "reading_spot_off", "room_off"]
    for d in replay.state()["decisions"]:
        assert set(d) == {"at", "rule", "why", "inputs", "change"} and d["why"]
    body = {"schema": "activity-lighting-override-1", "fixture_id": F01, "reason": "change", "at": 0}
    for i in range(15):
        replay.brain.set_override("victoria", body)
        replay.run(201 + i)
        replay.brain.clear_override("victoria", F01)
    decisions = replay.state()["decisions"]
    assert len(decisions) == 20
    assert [d["at"] for d in decisions] == sorted((d["at"] for d in decisions), reverse=True)  # newest first

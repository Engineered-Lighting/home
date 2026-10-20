"""Timeline replays of every MVP "Done" case, contract v1.1 (no zone events).

A small world simulator plays the producers into a real Brain on a fake
clock, ticking every 0.5 s:
- the HA bridge: whole-room occupancy and Frigate health;
- the Jetson: person_position each step (2 Hz), person_pose every second,
  JEV activity every 3 s, plus health.
No sleeps.
"""
from dataclasses import dataclass, field

import pytest
from conftest import (F01, F02, F03, F04, HEAD, LEFT, NOON, RIGHT, SOFA_MID, FakeClock, activity, batch, health,
                      occupancy, person, pose, position, test_settings)

from lighting_brain.brain import Brain
from lighting_brain.rules import short_name

STEP = 0.5
CENTRE = (2.0, 1.8, 1.3)
ALL = (F01, F02, F03, F04)


def shifted_person(at, track_id="slot-0"):
    """The default sofa reader, moved so the shoulder midpoint sits at `at`."""
    dx, dy, dz = (at[i] - SOFA_MID[i] for i in range(3))

    def move(p):
        return [p[0] + dx, p[1] + dy, p[2] + dz]

    return person(move(LEFT), move(RIGHT), move(HEAD), track_id=track_id)


@dataclass
class World:
    present: bool = False
    at: tuple = SOFA_MID  # chest point of the (first) person
    people: int = 1
    label: str | None = None
    hint: str | None = None  # AL-3 sends null without the private zone export
    jev_ok: bool = True
    deep_ok: bool = True
    bridge_up: bool = True
    frigate_ok: bool = True  # AL-2: when False, only health frigate ok:false is sent
    jetson_up: bool = True
    positions_on: bool = True  # the Jetson quick position source
    pose_on: bool = True
    jitter_m: float = 0.0
    jev_detail: str | None = None
    events_delay_s: float = 0.0  # producer lag: observed_at = now - delay


@dataclass
class Replay:
    brain: Brain
    clock: FakeClock
    world: World = field(default_factory=World)
    timeline: list = field(default_factory=list)  # (t, state)
    responses: list = field(default_factory=list)
    _last: dict = field(default_factory=dict)
    _sent: dict = field(default_factory=dict)
    _n: int = 0

    def _due(self, key, period, signature):
        t = self.clock()
        if self._sent.get(key) != signature or t - self._last.get(key, -1e9) >= period - 1e-9:
            self._sent[key], self._last[key] = signature, t
            return True
        return False

    def _where(self):
        w = self.world
        self._n += 1
        j = w.jitter_m * (1 if self._n % 2 else -1)
        return (w.at[0] + j, w.at[1] - j, w.at[2])

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
            if self._due("health:frigate", 60, True):
                events.append(health(obs, "frigate"))
        if w.jetson_up:
            for comp, ok in (("jev", w.jev_ok), ("deep_stack", w.deep_ok), ("camera", True)):
                if self._due(f"health:{comp}", 60, ok):
                    detail = w.jev_detail if comp == "jev" and not ok else None
                    events.append(health(obs, comp, ok, detail=detail))
            if w.present:
                at = self._where()
                others = [(at[0] + 1.2, at[1] + 0.8, at[2]) for _ in range(w.people - 1)]
                if w.positions_on:
                    events.append(position(obs, at, *others))
                if w.deep_ok:
                    if w.jev_ok and w.label and self._due("activity", 3, (w.label, w.hint)):
                        events.append(activity(obs, w.label, persons=w.people, zone_hint=w.hint))
                    if w.pose_on and self._due("pose", 1, (w.people, at)):
                        # AL-3 projects one person; person_count is the detector's count.
                        events.append(pose(obs, [shifted_person(at)], count=w.people))
        if events:
            self.responses.append(self.brain.ingest("victoria", batch(*events)))

    @property
    def start(self):
        return self.brain.started_at

    def run(self, until, **changes):
        """Apply world changes now, then advance to `until` (seconds after the start)."""
        for key, value in changes.items():
            setattr(self.world, key, value)
        end = self.start + until
        while True:
            self._emit()
            self.brain.tick()
            self.timeline.append((self.clock() - self.start, self.brain.state()))
            if self.clock() + STEP > end + 1e-9:
                break
            self.clock.tick(STEP)
        return self

    def walk(self, start, end, seconds):
        """Walk in a straight line, one step per tick."""
        steps = int(seconds / STEP)
        t0 = self.clock() - self.start
        for i in range(1, steps + 1):
            k = i / steps
            self.world.at = tuple(start[j] + k * (end[j] - start[j]) for j in range(3))
            self.run(t0 + i * STEP)
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


def level(state, fid):
    return next(f for f in state["fixtures"] if f["fixture_id"] == fid)["ambient"]["radial_level"]


def levels(state):
    return [level(state, f) for f in ALL]


def make_replay(geometry, settings=None, start=NOON):
    clock = FakeClock(start)
    return Replay(Brain(geometry, settings or test_settings(), clock), clock)


@pytest.fixture
def replay(geometry):
    return make_replay(geometry)


def settle_reader(replay, t=10):
    """On the sofa from 0 (ambient), reading from t, run to t + 8."""
    replay.run(t, present=True, label="resting")
    return replay.run(t + 8, label="reading")


# ------------------------------------------------------------------ Done cases


def test_empty_room_stays_off(replay):
    replay.run(600)
    assert all(all_off(s) and s["mode"] == "off" for _, s in replay.timeline)
    assert replay.state()["why"] == "Room empty · all off"
    assert len(replay.state()["decisions"]) == 1  # only the startup entry


def test_entering_the_room_fades_ambient_up_by_distance(replay):
    replay.run(5)
    replay.run(20, present=True, at=(3.0, 2.8, 1.3), label="resting")  # near fixture 03's corner
    assert replay.first(ambient_on) == pytest.approx(5.0)
    state = replay.state()
    assert state["mode"] == "ambient" and state["why"] == "Near fixture 03 · brighter"
    assert level(state, F03) == max(levels(state)) and level(state, F03) > level(state, F01)
    f03 = next(f for f in state["fixtures"] if f["fixture_id"] == F03)["ambient"]
    assert (f03["cct_k"], f03["fade_s"]) == (2700, 0.8) and f03["radial_level"] == f03["down_level"]
    assert "proximity_ambient" in [d["rule"] for d in state["decisions"]]


@pytest.mark.parametrize("fid,corner", [(F01, (0.4, 0.4, 1.3)), (F02, (3.6, 0.4, 1.3)),
                                        (F03, (3.6, 3.2, 1.3)), (F04, (0.4, 3.2, 1.3))])
def test_walking_toward_a_fixture_raises_it_and_away_lowers_it(replay, fid, corner):
    replay.run(2, present=True, at=CENTRE)
    start = len(replay.timeline) - 1
    replay.walk(CENTRE, corner, 6.0)
    toward = [level(s, fid) for _, s in replay.timeline[start:]]
    assert all(b >= a for a, b in zip(toward, toward[1:]))  # never dims on the way in
    assert toward[0] < 0.30 and toward[-1] == pytest.approx(0.60, abs=0.05)
    assert replay.state()["why"] == f"Near {short_name(fid)} · brighter"
    start = len(replay.timeline) - 1
    replay.walk(corner, CENTRE, 6.0)
    away = [level(s, fid) for _, s in replay.timeline[start:]]
    assert all(b <= a for a, b in zip(away, away[1:]))  # never brightens on the way out
    assert away[-1] < 0.30


def test_occupancy_without_any_position_is_flat(replay):
    replay.run(10, present=True, positions_on=False, pose_on=False, label="resting")
    state = replay.state()
    assert levels(state) == [0.30] * 4
    assert all(f["ambient"]["fade_s"] == 2.0 for f in state["fixtures"])
    assert state["why"] == "Room occupied · ambient"
    assert state["decisions"][0]["rule"] == "occupied_ambient"


def test_positions_dropping_out_fall_back_to_flat(replay):
    replay.run(10, present=True, at=(0.4, 0.4, 1.3))
    assert level(replay.state(), F01) == pytest.approx(0.60, abs=0.01)
    replay.run(25, positions_on=False, pose_on=False)  # position ttl 3 s, pose ttl 10 s
    assert levels(replay.state()) == [0.30] * 4


def test_jitter_inside_the_deadband_does_not_flicker(replay):
    replay.run(10, present=True, at=CENTRE)
    version = replay.brain.version
    start = len(replay.timeline)
    replay.run(60, jitter_m=0.02)  # +/- 2 cm every step
    assert len({tuple(levels(s)) for _, s in replay.timeline[start:]}) == 1
    assert replay.brain.version == version  # no pushes from jitter


def test_push_rate_stays_bounded(geometry):
    clock = FakeClock(NOON)
    brain = Brain(geometry, test_settings(), clock)
    brain.ingest("victoria", batch(occupancy(NOON)))
    pushes = []
    for i in range(200):  # 10 s of 20 Hz position events swinging corner to corner
        clock.tick(0.05)
        at = (0.4, 0.4, 1.3) if i % 2 else (3.6, 3.2, 1.3)
        before = brain.version
        brain.ingest("victoria", batch(position(clock(), at)))
        if brain.version != before:
            pushes.append(clock())
    for t in pushes:
        assert sum(1 for u in pushes if t <= u < t + 1.0 - 1e-3) <= 4
    assert len(pushes) >= 4 * 9  # still lively: about 4 per second


def test_reading_at_the_sofa_with_no_zones_brings_the_spot(replay):
    replay.run(10, present=True, label="resting")
    replay.run(30, label="reading")
    assert 10 + 3 <= replay.first(spot_on) <= 10 + 5
    state = replay.state()
    lit = [f for f in state["fixtures"] if f["spot"]["on"]]
    assert [f["fixture_id"] for f in lit] == [F01]
    assert lit[0]["spot"]["cct_k"] == 3500 and lit[0]["spot"]["level"] == 0.8
    assert lit[0]["ambient"]["radial_level"] > 0  # ambient stays underneath
    assert state["why"] == "Reading at sofa · spotlight on"
    assert state["occupancy"]["zones"] == {"sofa": False, "desk": False}  # no zone events at all
    decision = next(d for d in state["decisions"] if d["rule"] == "reading_spot_on")
    assert decision["why"] == "Reading at sofa · spotlight on from fixture 01"


def test_reading_away_from_the_sofa_gets_no_spot(replay):
    replay.run(30, present=True, at=(3.0, 2.8, 1.0), label="reading")
    assert not any(spot_on(s) for _, s in replay.timeline)


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


def test_leaving_turns_everything_off_after_two_minutes(replay):
    settle_reader(replay)
    replay.run(300, present=False, label=None)
    left = 18
    assert replay.first(all_off, after=left) == pytest.approx(left + 120, abs=STEP)
    assert replay.first(lambda s: not spot_on(s), after=left) <= left + 1  # spot drops at once
    state = replay.state()
    assert state["mode"] == "off"
    assert state["decisions"][0]["rule"] == "room_off"
    assert state["decisions"][0]["why"] == "Room empty for 2 min · all off"
    assert all(f["ambient"]["fade_s"] == 3.0 for f in state["fixtures"])
    held = [s for t, s in replay.timeline if left + 5 < t < left + 119]
    assert held and all(s["why"] == "Room just emptied · ambient until off" for s in held)


def test_a_second_person_gives_ambient_only(replay):
    settle_reader(replay)
    replay.run(40, people=2)
    assert replay.first(lambda s: not spot_on(s), after=18) == pytest.approx(18, abs=STEP)
    state = replay.state()
    assert state["mode"] == "ambient" and ambient_on(state)
    assert state["why"] == "2 people · ambient only"
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
    lit = [fid for fid, f in by.items() if f["spot"]["on"] and f["paused_until"] is None]
    assert lit == [F02]  # the others keep running
    pause = next(d for d in state["decisions"] if d["rule"] == "manual_pause")
    assert pause["why"] == "Manual change in Home · fixture 01 paused"
    replay.run(200, present=False, label=None)
    state = replay.state()
    assert all(f["paused_until"] is None for f in state["fixtures"])
    assert any(d["why"] == "fixture 01 back to Auto · room empty" for d in state["decisions"])


def test_manual_pause_cleared_from_home(replay):
    settle_reader(replay)
    replay.brain.set_override("victoria", {"schema": "activity-lighting-override-1", "fixture_id": F01,
                                           "reason": "aim", "at": replay.clock()})
    replay.run(25)
    assert replay.brain.clear_override("victoria", F01) == {"ok": True, "fixture_id": F01, "cleared": True}
    replay.run(27)
    clear = next(d for d in replay.state()["decisions"] if d["rule"] == "manual_pause")
    assert clear["why"] == "fixture 01 back to Auto · cleared in Home"


def test_losing_jev_gives_ambient_only(replay):
    settle_reader(replay)
    replay.run(40, jev_ok=False)
    assert replay.first(lambda s: not spot_on(s), after=18) == pytest.approx(18, abs=STEP)
    state = replay.state()
    assert state["mode"] == "degraded_ambient" and ambient_on(state)
    assert state["why"] == "JEV offline · ambient only"
    assert state["inputs"]["health"]["jev"] is False


def test_jetson_going_silent_gives_ambient_only(replay):
    settle_reader(replay)
    replay.run(200, jetson_up=False)
    t = replay.first(lambda s: not spot_on(s), after=18)
    assert 18 < t <= 18 + 11
    state = replay.state()
    assert state["mode"] == "degraded_ambient" and ambient_on(state)
    assert levels(state) == [0.30] * 4  # no positions: flat


def test_stale_and_expired_events_are_ignored(replay):
    replay.run(5)
    late = replay.brain.ingest("victoria", batch(occupancy(NOON - 200), position(NOON - 100, CENTRE),
                                                 activity(NOON - 100), pose(NOON - 100)))
    assert late["accepted"] == 0 and {i["reason"] for i in late["ignored"]} == {"expired"}
    replay.run(10)
    assert all_off(replay.state())
    replay.brain.ingest("victoria", batch(occupancy(NOON + 10, present=False)))
    stale = replay.brain.ingest("victoria", batch(occupancy(NOON + 9)))
    assert stale["ignored"] == [{"index": 0, "type": "occupancy", "reason": "stale"}]


def test_lagging_producers_still_light_within_five_seconds(replay):
    replay.world.events_delay_s = 1.0
    replay.run(10, present=True, label="resting")
    replay.run(30, label="reading")
    assert 9 + 3 <= replay.first(spot_on) <= 10 + 5


def test_night_scales_proximity_and_still_reads(geometry):
    from datetime import datetime, timezone

    night = make_replay(geometry, start=datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc).timestamp())
    night.run(10, present=True, label="reading")
    day = make_replay(geometry)
    day.run(10, present=True, label="reading")
    for fid in ALL:
        assert level(night.state(), fid) == pytest.approx(level(day.state(), fid) * 0.35, abs=0.002)
    f01 = next(f for f in night.state()["fixtures"] if f["fixture_id"] == F01)
    assert f01["ambient"]["cct_k"] == 2200 and f01["spot"]["on"] and night.state()["mode"] == "reading"


def test_decision_log_explains_each_step_and_keeps_twenty(replay):
    settle_reader(replay)
    replay.run(60, label="resting")
    replay.run(200, present=False, label=None)
    rules = [d["rule"] for d in reversed(replay.state()["decisions"])]
    assert rules == ["room_off", "proximity_ambient", "reading_spot_on", "reading_spot_off",
                     "occupied_ambient", "room_off"]
    for d in replay.state()["decisions"]:
        assert set(d) == {"at", "rule", "why", "inputs", "change"} and d["why"]
    body = {"schema": "activity-lighting-override-1", "fixture_id": F01, "reason": "change", "at": 0}
    for i in range(15):
        replay.brain.set_override("victoria", body)
        replay.run(201 + i)
        replay.brain.clear_override("victoria", F01)
    decisions = replay.state()["decisions"]
    assert len(decisions) == 20
    assert [d["at"] for d in decisions] == sorted((d["at"] for d in decisions), reverse=True)

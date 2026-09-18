"""Story S scenarios at the stories.py constants, plus a recorded-night replay.

Scripted nights follow the simulator's timelines (tools/lighting-sim). The
recorded replay reads ``living-lights-sim-night/v1`` files from the
directory named by ``LL_PUB_FIXTURES`` and skips when it is unset; recorded
nights never live in the repository (see tests/fixtures/README.md).
"""
import datetime as dt
import json
import os
import pathlib
import unittest

from lighting_publisher import stories
from lighting_publisher.estimator import (AWAKE, AWAY, LIKELY_ASLEEP, AsleepEstimator, CameraSignal,
                                          EstimatorInputs)

TZ = dt.timezone(dt.timedelta(hours=1))
BASE = dt.datetime(2026, 9, 12, tzinfo=TZ)
CAMERAS = ("kitchen", "living_room", "dining_room")
ZONE_CAMERA = {"sink": "kitchen", "island_left": "kitchen", "sofa": "living_room", "front_left": "living_room",
               "office": "living_room", "front_door": "living_room", "dining_left": "dining_room"}


def at(hhmm: str, day: int = 0) -> dt.datetime:
    hours, minutes, *rest = (int(x) for x in hhmm.split(":"))
    return BASE + dt.timedelta(days=day, hours=hours, minutes=minutes, seconds=rest[0] if rest else 0)


class Night:
    """A scripted night: zone occupancy, phone, TV, commands, latch, observer."""

    def __init__(self, start: dt.datetime, end: dt.datetime, observer_fresh: bool = True):
        self.start, self.end = start, end
        self.observer_fresh = observer_fresh
        self.edges: list[tuple[dt.datetime, str, object]] = []
        self.state = {"tv": "off", "home": True, "profile": "evening", "brighten": None, "wake": None,
                      "latch_on": None, "latch_writer": None, "latch_changed": None,
                      "observer_override": {}}
        for zone in ZONE_CAMERA:
            self.state["zone:" + zone] = False

    def at(self, when: dt.datetime, key: str, value) -> "Night":
        self.edges.append((when, key, value))
        return self

    def occupy(self, zone: str, start: dt.datetime, end: dt.datetime) -> "Night":
        return self.at(start, "zone:" + zone, True).at(end, "zone:" + zone, False)

    def inputs(self, now: dt.datetime, changed: dict) -> EstimatorInputs:
        s = self.state
        cams = {}
        for cam in CAMERAS:
            zones = [z for z, c in ZONE_CAMERA.items() if c == cam]
            frigate = any(s["zone:" + z] for z in zones)
            override = s["observer_override"].get(cam)
            present = frigate if override is None else override
            cams[cam] = CameraSignal(frigate_person=frigate, frigate_changed=changed.get("cam:" + cam),
                                     observer_present=present if self.observer_fresh else None,
                                     observer_fresh=self.observer_fresh)
        return EstimatorInputs(
            local_time=now.astimezone(TZ), profile=s["profile"], user_at_home=s["home"],
            user_at_home_changed=changed.get("home"), front_door_occupied=s["zone:front_door"],
            front_door_changed=changed.get("zone:front_door"), cameras=cams, tv_state=s["tv"],
            p_attention0=None, last_brighten=s["brighten"], last_wake=s["wake"],
            latch_on=s["latch_on"], latch_writer=s["latch_writer"], latch_changed=s["latch_changed"])

    def run(self, est: AsleepEstimator, step_s: int = 30, mirror_latch: bool = False):
        """Tick the estimator; with ``mirror_latch`` the HA latch follows it."""
        edges = sorted(self.edges, key=lambda e: e[0])
        changed: dict = {}
        decisions = []
        now = self.start
        while now <= self.end:
            while edges and edges[0][0] <= now:
                when, key, value = edges.pop(0)
                if key.startswith("zone:") and self.state[key] != value:
                    changed[key] = when
                    changed["cam:" + ZONE_CAMERA[key[5:]]] = when
                if key == "home" and self.state[key] != value:
                    changed["home"] = when
                if key == "latch":
                    self.state["latch_on"], self.state["latch_writer"], self.state["latch_changed"] = value[0], value[1], when
                    continue
                self.state[key] = value
            d = est.update(now, self.inputs(now, changed))
            if mirror_latch and self.state["latch_writer"] != "manual" and d.reassert:
                self.state["latch_on"], self.state["latch_writer"], self.state["latch_changed"] = d.asleep, "mirror", now
            decisions.append((now, d))
            now += dt.timedelta(seconds=step_s)
        return decisions


def transitions(est: AsleepEstimator):
    return [(e["t"], e["from"], e["to"], e["reason"]) for e in est.journal if e["event"] == "transition"]


def first(est: AsleepEstimator, to: str) -> dt.datetime | None:
    for e in est.journal:
        if e["event"] == "transition" and e["to"] == to:
            return dt.datetime.fromisoformat(e["t"])
    return None


def states_between(decisions, a, b) -> set:
    return {d.state for t, d in decisions if a <= t <= b}


def evening_then_bed(observer_fresh: bool = True, tv_off: bool = True) -> Night:
    n = Night(at("21:00"), at("09:00", 1), observer_fresh=observer_fresh)
    n.at(at("22:05"), "tv", "on")
    n.occupy("sofa", at("22:10"), at("23:45"))
    n.occupy("sink", at("23:47"), at("23:52"))
    n.occupy("front_left", at("23:52"), at("23:56"))
    if tv_off:
        n.at(at("23:50"), "tv", "off")
    n.occupy("sink", at("07:30", 1), at("07:50", 1))
    return n


class S1EveningThenBed(unittest.TestCase):
    def test_latches_after_fifteen_quiet_minutes_in_window(self):
        est = AsleepEstimator()
        d = evening_then_bed().run(est)
        latched = first(est, LIKELY_ASLEEP)
        self.assertEqual(latched, at("00:11", 1))
        self.assertEqual(transitions(est)[0][3], "quiet_window")
        self.assertEqual(states_between(d, at("21:00"), at("00:10", 1)), {AWAKE})
        self.assertEqual(states_between(d, at("00:11", 1), at("07:39", 1)), {LIKELY_ASLEEP})
        self.assertEqual(transitions(est)[1], (at("07:40", 1).isoformat(timespec="seconds"), LIKELY_ASLEEP, AWAKE, "occupancy"))
        self.assertEqual(stories.IDLE_S, 900)
        self.assertEqual(stories.WAKE_S, 600)

    def test_legacy_tv_rule_without_beliefs(self):
        est = AsleepEstimator()
        d = evening_then_bed(tv_off=False).run(est)
        self.assertEqual(first(est, LIKELY_ASLEEP), at("00:11", 1))
        evidence = [x for t, x in d if t == at("00:11", 1)][0].evidence
        self.assertEqual(evidence["no_answer_tv_rule"], "legacy")
        self.assertEqual(evidence["tv_rule"], "legacy")
        self.assertEqual(evidence["tv_state"], "on")

    def test_tv_on_with_belief_needs_attention_zero(self):
        n = evening_then_bed(tv_off=False)
        est = AsleepEstimator()
        n.state["p"] = 0.3
        n.at(at("01:00", 1), "p", 0.9)
        real_inputs = n.inputs

        def with_belief(now, changed):
            base = real_inputs(now, changed)
            return EstimatorInputs(**{**base.__dict__, "p_attention0": n.state["p"]})
        n.inputs = with_belief
        n.run(est)
        self.assertEqual(first(est, LIKELY_ASLEEP), at("01:15", 1))

    def test_brighten_command_delays_the_latch(self):
        n = evening_then_bed()
        n.at(at("00:05", 1), "brighten", at("00:05", 1))
        est = AsleepEstimator()
        n.run(est)
        self.assertEqual(first(est, LIKELY_ASLEEP), at("00:20", 1))

    def test_outside_window_needs_overnight_profile(self):
        n = Night(at("21:00"), at("23:59"))
        n.occupy("sofa", at("21:05"), at("21:30"))
        est = AsleepEstimator()
        n.run(est)
        self.assertIsNone(first(est, LIKELY_ASLEEP))
        n = Night(at("21:00"), at("23:59"))
        n.occupy("sofa", at("21:05"), at("21:30"))
        n.at(at("21:00"), "profile", "overnight")
        est = AsleepEstimator()
        n.run(est)
        self.assertEqual(first(est, LIKELY_ASLEEP), at("21:45"))


class S3NightExcursionKeeps(unittest.TestCase):
    def test_four_minute_trip_does_not_clear(self):
        n = evening_then_bed()
        n.occupy("sink", at("03:00", 1), at("03:04", 1))
        est = AsleepEstimator()
        d = n.run(est)
        self.assertEqual(states_between(d, at("00:30", 1), at("07:00", 1)), {LIKELY_ASLEEP})

    def test_ten_minute_trip_clears(self):
        n = evening_then_bed()
        n.occupy("sink", at("05:00", 1), at("05:10", 1))
        est = AsleepEstimator()
        n.run(est)
        self.assertIn((at("05:10", 1).isoformat(timespec="seconds"), LIKELY_ASLEEP, AWAKE, "occupancy"), transitions(est))


class S5LeavingGoesAway(unittest.TestCase):
    def test_departure_then_arrival(self):
        n = Night(at("21:00"), at("09:00", 1))
        n.occupy("sofa", at("22:10"), at("22:55"))
        n.occupy("front_door", at("22:56"), at("22:58"))
        n.at(at("23:01"), "home", False)
        n.at(at("07:00", 1), "home", True)
        n.occupy("front_door", at("07:00", 1), at("07:03", 1))
        est = AsleepEstimator()
        d = n.run(est)
        got = transitions(est)
        self.assertEqual(got[0], (at("23:01").isoformat(timespec="seconds"), AWAKE, AWAY, "departure_door_then_phone_off"))
        self.assertEqual(states_between(d, at("23:01"), at("06:59", 1)), {AWAY})
        self.assertEqual(got[1], (at("07:00", 1).isoformat(timespec="seconds"), AWAY, AWAKE, "arrival"))

    def test_departure_from_asleep_without_door_confirms_after_ten_minutes(self):
        n = evening_then_bed()
        n.at(at("02:20", 1), "home", False)
        est = AsleepEstimator()
        n.run(est)
        self.assertIn((at("02:30", 1).isoformat(timespec="seconds"), LIKELY_ASLEEP, AWAY, "departure_phone_off_confirmed"),
                      transitions(est))


class S6GuestKeepsAwake(unittest.TestCase):
    def test_person_on_sofa_all_night(self):
        n = Night(at("21:00"), at("09:00", 1))
        n.occupy("sofa", at("22:10"), at("07:00", 1))
        est = AsleepEstimator()
        d = n.run(est)
        self.assertEqual({x.state for _, x in d}, {AWAKE})


class S7StuckSensor(unittest.TestCase):
    """Known limitation: a stuck Frigate count with a stale observer keeps the
    house awake, exactly like the legacy latch. A fresh observer that sees
    nobody makes Frigate alone not credible and the house latches."""

    def test_stale_observer_keeps_awake(self):
        n = evening_then_bed(observer_fresh=False)
        n.at(at("22:30"), "zone:office", True)
        est = AsleepEstimator()
        d = n.run(est)
        self.assertEqual({x.state for _, x in d}, {AWAKE})

    def test_fresh_observer_disagreeing_latches(self):
        n = evening_then_bed(observer_fresh=True)
        n.at(at("22:30"), "zone:office", True)
        n.at(at("22:30"), "observer_override", {"living_room": False})
        est = AsleepEstimator()
        n.run(est)
        self.assertEqual(first(est, LIKELY_ASLEEP), at("00:11", 1))


class S9ReconnectHolds(unittest.TestCase):
    def test_twenty_second_presence_blip(self):
        n = evening_then_bed()
        n.at(at("03:00", 1), "home", False)
        n.at(at("03:00:20", 1), "home", True)
        est = AsleepEstimator()
        d = n.run(est, step_s=10)
        self.assertEqual(states_between(d, at("00:30", 1), at("07:00", 1)), {LIKELY_ASLEEP})


class S9bArrivalClears(unittest.TestCase):
    def _run(self, phone_at: str, door_at: str):
        n = evening_then_bed()
        n.at(at("02:20", 1), "home", False)
        n.at(at(phone_at, 1), "home", True)
        n.occupy("front_door", at(door_at, 1), at(door_at, 1) + dt.timedelta(minutes=2))
        est = AsleepEstimator()
        n.run(est, step_s=10)
        return est

    def test_phone_first(self):
        est = self._run("03:00", "03:00:30")
        self.assertIn((at("03:00:30", 1).isoformat(timespec="seconds"), AWAY, AWAKE, "arrival"), transitions(est))

    def test_door_first(self):
        est = self._run("03:00:40", "03:00")
        self.assertIn((at("03:00:40", 1).isoformat(timespec="seconds"), AWAY, AWAKE, "arrival"), transitions(est))

    def test_arrival_without_departure_clears_asleep(self):
        n = evening_then_bed()
        n.at(at("03:00", 1), "home", True)
        n.occupy("front_door", at("03:00:30", 1), at("03:02", 1))
        est = AsleepEstimator()
        n.run(est, step_s=10)
        self.assertIn((at("03:00:30", 1).isoformat(timespec="seconds"), LIKELY_ASLEEP, AWAKE, "arrival"), transitions(est))


class S13ManualHonored(unittest.TestCase):
    def test_manual_clear_holds_45_minutes_then_rules_resume(self):
        n = evening_then_bed()
        n.at(at("02:00", 1), "latch", (False, "manual"))
        est = AsleepEstimator()
        d = n.run(est, mirror_latch=True)
        got = transitions(est)
        self.assertEqual(got[1], (at("02:00", 1).isoformat(timespec="seconds"), LIKELY_ASLEEP, AWAKE, "manual"))
        self.assertEqual(states_between(d, at("02:00", 1), at("02:44:30", 1)), {AWAKE})
        self.assertFalse(any(x.reassert for t, x in d if at("02:00", 1) <= t < at("02:45", 1)))
        self.assertEqual(got[2], (at("02:45", 1).isoformat(timespec="seconds"), AWAKE, LIKELY_ASLEEP, "quiet_window"))
        self.assertEqual(stories.REARM_S, 45 * 60)

    def test_manual_set_is_followed_too(self):
        n = Night(at("21:00"), at("23:00"))
        n.occupy("sofa", at("21:00"), at("23:00"))
        n.at(at("21:30"), "latch", (True, "manual"))
        est = AsleepEstimator()
        d = n.run(est)
        self.assertEqual(states_between(d, at("21:30"), at("22:14"), ), {LIKELY_ASLEEP})
        self.assertEqual(states_between(d, at("22:15"), at("23:00")), {AWAKE})


class ReassertFlag(unittest.TestCase):
    def test_disagreement_with_mirrored_latch(self):
        n = evening_then_bed()
        n.at(at("21:00"), "latch", (False, "mirror"))
        est = AsleepEstimator()
        d = n.run(est)
        before = [x for t, x in d if t == at("23:00")][0]
        self.assertFalse(before.reassert)
        after = [x for t, x in d if t == at("00:11", 1)][0]
        self.assertTrue(after.reassert)
        self.assertEqual(after.as_attributes()["reassert"], True)
        self.assertEqual(after.as_attributes()["since"], at("00:11", 1).isoformat(timespec="seconds"))

    def test_unknown_latch_never_reasserts(self):
        est = AsleepEstimator()
        d = evening_then_bed().run(est)
        self.assertFalse(any(x.reassert for _, x in d))


class RecordedNights(unittest.TestCase):
    """Replay ``living-lights-sim-night/v1`` files; skip without LL_PUB_FIXTURES."""

    ALLOWED_EXITS = {"occupancy", "arrival", "wake_command", "brighten_command", "manual"}

    @staticmethod
    def fixture_files() -> list[pathlib.Path]:
        root = os.environ.get("LL_PUB_FIXTURES")
        if not root:
            return []
        return sorted(pathlib.Path(root).glob("*.json"))

    @staticmethod
    def replay(night: dict) -> tuple[AsleepEstimator, list]:
        start = dt.datetime.fromisoformat(night["start"])
        end = dt.datetime.fromisoformat(night["end"])
        state = dict(night["initial"])
        events = sorted(night["events"], key=lambda e: e["t"])
        events = [(dt.datetime.fromisoformat(e["t"]), e["entity"], e["state"]) for e in events]
        changed: dict[str, dt.datetime] = {}
        est = AsleepEstimator()
        decisions = []
        now = start
        while now <= end:
            while events and events[0][0] <= now:
                when, entity, value = events.pop(0)
                if state.get(entity) != value:
                    changed[entity] = when
                state[entity] = value
            cams = {c: CameraSignal(frigate_person=state.get(f"binary_sensor.{c}_person_occupancy") == "on",
                                    frigate_changed=changed.get(f"binary_sensor.{c}_person_occupancy"),
                                    observer_present=None, observer_fresh=False) for c in CAMERAS}
            inputs = EstimatorInputs(
                local_time=now, profile=None,
                user_at_home=state.get("input_boolean.user_at_home", "on") == "on",
                user_at_home_changed=changed.get("input_boolean.user_at_home"),
                front_door_occupied=state.get("binary_sensor.front_door_person_occupancy") == "on",
                front_door_changed=changed.get("binary_sensor.front_door_person_occupancy"),
                cameras=cams, tv_state=state.get("media_player.lg_tv", "off"), p_attention0=None)
            decisions.append((now, est.update(now, inputs)))
            now += dt.timedelta(seconds=60)
        return est, decisions

    def test_recorded_nights(self):
        files = self.fixture_files()
        if not files:
            self.skipTest("LL_PUB_FIXTURES unset; recorded nights live outside the repo")
        summary = []
        for path in files:
            with open(path, "r", encoding="utf-8") as handle:
                night = json.load(handle)
            self.assertEqual(night.get("schema"), "living-lights-sim-night/v1", path.name)
            est, decisions = self.replay(night)
            exits = [e for e in est.journal if e["event"] == "transition" and e["from"] == LIKELY_ASLEEP]
            for e in exits:
                self.assertIn(e["reason"].split("_")[0], self.ALLOWED_EXITS | {"departure"}, (path.name, e))
            latched = first(est, LIKELY_ASLEEP)
            recorded = night.get("actual", {}).get("asleep_transitions", [])
            summary.append({"night": night.get("night"), "first_latch": latched.isoformat() if latched else None,
                            "exits": [(e["t"], e["reason"]) for e in exits],
                            "recorded_transitions": len(recorded)})
        print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    unittest.main()

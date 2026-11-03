"""Tests for den-core decide v0. Events are built by hand against the SHARED INTERFACE text."""
import ast
import hashlib
import json
import random
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decide  # noqa: E402

T0 = 1793764800.0
FL, FR, RL, RR = decide.LIGHTS


def on(b, k):
    return {"on": True, "brightness": b, "kelvin": k}


NORMAL = {b: on(180, 2700) for b in decide.LIGHTS}
LOOK = on(255, 2700)
OFF = {"on": False, "brightness": None, "kelvin": None}
CANDIDATES = ["keep_current", "look:reading@0", "base", "off"]
DECISION_KEYS = {"decision_id", "ts", "trigger", "policy", "actuator_domain", "inputs", "requested_scene",
                 "transitions", "candidates", "skipped", "hard_conflicts", "status", "why"}
SOURCES = {"person_state": "need", "mode": "incumbent", "tick": "runner"}


def dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def ev(t, kind, data, seq=0):
    return {"schema": "den-event-1", "t": float(t), "seq": seq, "kind": kind,
            "source": SOURCES.get(kind, "synthetic"), "data": data}


def ps(t, state, support="observed", kind="reading"):
    active = state != "none"
    return ev(t, "person_state", {
        "schema_version": "person_state/v1",
        "state_id": "room@%.3f" % t,
        "emitted_ts": float(t),
        "track_id": "room",
        "identity": "unbound",
        "focused_engagement": {"state": state, "since_s": 0.0 if active else None,
                               "onset_ts": float(t) if active else None, "confidence": None, "kind": kind},
        "visual_demand": {"level": None, "basis": "qwen:reading" if active else None, "confidence": None},
        "posture": "sitting",
        "field_support": {"focused_engagement": {"last_support_capture_ts": float(t) - 1.0,
                                                 "value_set_ts": float(t), "support_status": support}},
        "sources": {"focused_engagement": [{"origin": "qwen", "ref": "req-%d" % round(t)}]},
        "interpreter": {"path": "rules", "version": "need-v0"},
        "flags": [],
    })


def hold(bulb, b, k, since, lifetime="room_vacant_2_minutes"):
    return {"entity_id": bulb, "pinned": on(b, k), "since": float(since), "lifetime": lifetime}


def mode(rev, holds=(), gaming=False, normal=NORMAL):
    return {"policy": "den_lighting@1.0.4", "revision": rev, "gaming": gaming, "occupancy": "on",
            "holds": sorted(holds, key=lambda h: h["entity_id"]), "normal": normal}


def light(t, bulb, is_on, b, k):
    return ev(t, "light_observed", {"entity_id": bulb, "on": is_on, "brightness": b, "kelvin": k,
                                    "context": "unknown"})


def occ(t, value):
    return ev(t, "occupancy", {"value": value, "via": "synthetic"})


def tick(t):
    return ev(t, "tick", {})


def arrival(t=T0, lit=False):
    """Four bulbs observed (off by default), a mode, then occupancy on one second later."""
    seen = [light(t, b, lit, 180 if lit else None, 2700 if lit else None) for b in decide.LIGHTS]
    return seen + [ev(t, "mode", mode(1)), occ(t + 1, "on")]


def feed(policy, events):
    """Stamp seq like the runner does and return every output as a den-event-1 dict."""
    out, seq = [], 0
    for event in events:
        event = dict(event, seq=seq)
        seq += 1
        for o in policy.handle(event):
            out.append({"schema": "den-event-1", "t": event["t"], "seq": seq, "kind": o["kind"],
                        "source": policy.id, "data": o["data"]})
            seq += 1
    return out


def decisions(policy, events):
    return [o["data"] for o in feed(policy, events)]


def rule(d):
    return d["why"].split(":", 1)[0]


def chosen(d):
    return d["why"].split("chose ", 1)[1].split(" ", 1)[0]


def evening():
    """A composed 10-minute scenario: arrival, reading on and off, a hold, then vacancy."""
    events = arrival(T0)
    events += [ps(T0 + 60, "engaged"), ps(T0 + 64, "engaged"), ps(T0 + 180, "ending"), ps(T0 + 200, "none"),
               ev(T0 + 240, "mode", mode(2, [hold(RL, 80, 2202, T0 + 240)])),
               ps(T0 + 300, "starting"), ps(T0 + 304, "engaged"), ps(T0 + 400, "none"),
               occ(T0 + 420, "off"), ev(T0 + 560, "mode", mode(3))]
    events += [tick(T0 + 2 * k) for k in range(1, 301)]
    return sorted(events, key=lambda e: (e["t"], e["kind"] == "tick"))


class OccupancyTest(unittest.TestCase):
    def test_unknown_occupancy_fails_closed_even_when_reading(self):
        p = decide.make_policy()
        events = arrival()[:-1] + [ps(T0 + 5, "engaged"), occ(T0 + 6, "unknown")]
        events += [tick(T0 + 2 * k) for k in range(1, 100)]
        self.assertEqual(decisions(p, events), [])
        self.assertIsNone(decide.plan(p.state, T0 + 200, p.config))

    def test_arrival_gives_base_on_all_four(self):
        [d] = decisions(decide.make_policy(), arrival())
        self.assertEqual((d["ts"], rule(d), d["trigger"]), (T0 + 1, "base", "mode change"))
        self.assertEqual(d["requested_scene"], NORMAL)
        self.assertEqual(d["skipped"], [])


class ReadingTest(unittest.TestCase):
    def setUp(self):
        self.p = decide.make_policy()
        decisions(self.p, arrival())

    def test_engaged_lights_the_look_on_all_four(self):
        [d] = decisions(self.p, [ps(T0 + 10, "engaged")])
        self.assertEqual((rule(d), d["trigger"]), ("reading_look", "person_state change"))
        self.assertEqual(d["requested_scene"], {b: LOOK for b in decide.LIGHTS})
        self.assertEqual(d["inputs"]["person_state"], ["room@%.3f" % (T0 + 10)])
        self.assertEqual(d["inputs"]["field_support"]["room@%.3f" % (T0 + 10)]["focused_engagement"]
                         ["support_status"], "observed")

    def test_ending_keeps_the_look_and_none_returns_to_base(self):
        decisions(self.p, [ps(T0 + 10, "engaged")])
        self.assertEqual(decisions(self.p, [ps(T0 + 14, "ending")]), [])
        [d] = decisions(self.p, [ps(T0 + 18, "none")])
        self.assertEqual((rule(d), d["requested_scene"]), ("base", NORMAL))

    def test_starting_does_not_light_it(self):
        self.assertEqual(decisions(self.p, [ps(T0 + 10, "starting")]), [])
        self.assertEqual(decide.plan(self.p.state, T0 + 10, self.p.config)["rule"], "base")

    def test_engaged_with_expired_support_gives_base(self):
        decisions(self.p, [ps(T0 + 10, "engaged")])
        [d] = decisions(self.p, [ps(T0 + 14, "engaged", support="expired")])
        self.assertEqual((rule(d), d["requested_scene"]), ("base", NORMAL))


class HoldTest(unittest.TestCase):
    def setUp(self):
        self.p = decide.make_policy()
        decisions(self.p, arrival())
        [self.d] = decisions(self.p, [ev(T0 + 10, "mode", mode(2, [hold(RL, 80, 2202, T0 + 10)]))])

    def test_hold_pins_the_bulb_at_read_back(self):
        d = self.d
        self.assertEqual((rule(d), d["trigger"]), ("base", "hold change"))
        self.assertEqual(d["requested_scene"], {**NORMAL, RL: on(80, 2202)})
        self.assertEqual(d["skipped"], [{"channel": RL, "reason": "held",
                                         "hold_ref": "hold:light.den_rear_left@1793764810.000"}])
        self.assertEqual(d["inputs"]["hold_set"]["revision"], 2)
        self.assertEqual(d["inputs"]["revisions"], {"hold_set": 2, "mode": 2})
        self.assertEqual([h["entity_id"] for h in d["inputs"]["hold_set"]["held"]], [RL])

    def test_bulb_stays_pinned_while_reading_goes_on_and_off(self):
        out = decisions(self.p, [ps(T0 + 20, "engaged"), ps(T0 + 24, "ending"), ps(T0 + 28, "none"),
                                 ps(T0 + 32, "engaged"), ps(T0 + 36, "none")])
        self.assertEqual([rule(d) for d in out], ["reading_look", "base", "reading_look", "base"])
        for d in out:
            self.assertEqual(d["requested_scene"][RL], on(80, 2202))
            self.assertEqual([s["channel"] for s in d["skipped"]], [RL])
        self.assertEqual(out[0]["requested_scene"], {FL: LOOK, FR: LOOK, RL: on(80, 2202), RR: LOOK})

    def test_later_mode_without_the_hold_returns_it_to_the_rule(self):
        decisions(self.p, [ps(T0 + 20, "engaged")])
        [d] = decisions(self.p, [ev(T0 + 30, "mode", mode(3))])
        self.assertEqual((rule(d), d["trigger"]), ("reading_look", "hold change"))
        self.assertEqual(d["requested_scene"], {b: LOOK for b in decide.LIGHTS})
        self.assertEqual(d["skipped"], [])


class VacancyTest(unittest.TestCase):
    def test_vacancy_off_after_120_s_keeps_the_held_bulb(self):
        p = decide.make_policy()
        decisions(p, arrival() + [ev(T0 + 10, "mode", mode(2, [hold(RL, 80, 2202, T0 + 10)]))])
        t = T0 + 100
        self.assertEqual(decisions(p, [occ(t, "off")] + [tick(t + s) for s in range(2, 119, 2)]), [])
        [d] = decisions(p, [tick(t + 120)])
        self.assertEqual((d["ts"], rule(d), d["trigger"]), (t + 120, "vacant_off", "time step"))
        self.assertEqual(d["requested_scene"], {FL: OFF, FR: OFF, RL: on(80, 2202), RR: OFF})
        [d] = decisions(p, [tick(t + 122), occ(t + 130, "on")])
        self.assertEqual((rule(d), d["trigger"]), ("base", "mode change"))
        self.assertEqual(d["requested_scene"], {**NORMAL, RL: on(80, 2202)})

    def test_short_vacancy_turns_nothing_off(self):
        p = decide.make_policy()
        decisions(p, arrival())
        events = [occ(T0 + 100, "off")] + [tick(T0 + 100 + s) for s in range(2, 100, 2)] + [occ(T0 + 200, "on")]
        events += [tick(T0 + 200 + s) for s in range(2, 200, 2)]
        self.assertEqual(decisions(p, events), [])


class GamingTest(unittest.TestCase):
    def test_no_decisions_while_gaming_then_replan(self):
        p = decide.make_policy()
        decisions(p, arrival())
        during = [ev(T0 + 10, "mode", mode(2, gaming=True)), ps(T0 + 12, "engaged"),
                  occ(T0 + 14, "off"), occ(T0 + 16, "on")] + [tick(T0 + s) for s in range(18, 200, 2)]
        self.assertEqual(decisions(p, during), [])
        [d] = decisions(p, [ev(T0 + 200, "mode", mode(3))])
        self.assertEqual((rule(d), d["trigger"]), ("reading_look", "mode change"))

    def test_game_end_with_the_same_scene_is_silent(self):
        p = decide.make_policy()
        decisions(p, arrival())
        events = [ev(T0 + 10, "mode", mode(2, gaming=True)), tick(T0 + 12), ev(T0 + 100, "mode", mode(3))]
        self.assertEqual(decisions(p, events), [])


class ChangeOnlyTest(unittest.TestCase):
    def test_repeating_identical_events_emits_nothing_new(self):
        events = arrival() + [ps(T0 + 10, "engaged"), ev(T0 + 20, "mode", mode(2, [hold(RL, 80, 2202, T0 + 20)]))]
        once = decisions(decide.make_policy(), events)
        p = decide.make_policy()
        twice = decisions(p, [e for event in events for e in (event, event)])
        self.assertEqual(len(once), 3)
        self.assertEqual([d["requested_scene"] for d in twice], [d["requested_scene"] for d in once])
        self.assertEqual(decisions(p, events[-2:] + [tick(T0 + 22), tick(T0 + 24), ps(T0 + 26, "engaged")]), [])


class CandidateTest(unittest.TestCase):
    def test_four_candidates_with_integer_costs_and_lowest_chosen(self):
        out = decisions(decide.make_policy(), evening())
        self.assertTrue(out)
        for d in out:
            self.assertEqual([c["scene_id"] for c in d["candidates"]], CANDIDATES)
            for c in d["candidates"]:
                self.assertIs(type(c["cost"]), int)
                self.assertTrue(c["why"].endswith(".") and c["why"].count(". ") == 0)
            lowest = min(c["cost"] for c in d["candidates"])
            first = next(c["scene_id"] for c in d["candidates"] if c["cost"] == lowest)
            self.assertEqual(chosen(d), first)

    def test_keep_current_is_chosen_when_bulbs_already_match(self):
        p = decide.make_policy()
        [d] = decisions(p, arrival(lit=True))
        self.assertEqual(chosen(d), "keep_current")
        self.assertEqual([c["cost"] for c in d["candidates"]], [0, 4, 0, 4])
        self.assertEqual(decide.plan(p.state, T0 + 1, p.config)["chosen"], "keep_current")

    def test_unknown_actuator_state_counts_as_a_miss(self):
        events = [ev(T0, "mode", mode(1)), occ(T0 + 1, "on")]
        [d] = decisions(decide.make_policy(), events)
        self.assertEqual([c["cost"] for c in d["candidates"]], [4, 4, 0, 4])
        self.assertEqual(d["inputs"]["actuator_state"], {b: None for b in decide.LIGHTS})

    def test_match_rule_is_den_lightings(self):
        m = decide.matches
        self.assertTrue(m(on(180, 2700), on(181, 2700)))
        self.assertFalse(m(on(180, 2700), on(182, 2700)))
        self.assertTrue(m(on(180, 2700), on(180, 2714)))  # 370.37 vs 368.46 mired
        self.assertFalse(m(on(180, 2700), on(180, 2716)))
        self.assertTrue(m(OFF, {"on": False, "brightness": 12, "kelvin": 3000}))
        self.assertTrue(m({"on": True, "brightness": None, "kelvin": None}, on(10, 6000)))
        self.assertFalse(m(on(180, 2700), {"on": True, "brightness": None, "kelvin": None}))
        self.assertFalse(m(on(180, 2700), None))


class NoIncumbentTest(unittest.TestCase):
    def test_without_mode_every_bulb_is_skipped_and_nothing_emitted(self):
        p = decide.make_policy()
        self.assertEqual(decisions(p, [occ(T0, "on"), tick(T0 + 2), ps(T0 + 3, "none")]), [])
        result = decide.plan(p.state, T0 + 4, p.config)
        self.assertEqual((result["rule"], result["requested_scene"]), ("base", {}))
        self.assertEqual(result["skipped"], [{"channel": b, "reason": "no_base_scene", "hold_ref": None}
                                             for b in decide.LIGHTS])

    def test_reading_look_still_lights_all_four(self):
        p = decide.make_policy()
        [d] = decisions(p, [occ(T0, "on"), ps(T0 + 3, "engaged")])
        self.assertEqual((rule(d), d["requested_scene"]), ("reading_look", {b: LOOK for b in decide.LIGHTS}))
        self.assertEqual(d["inputs"]["hold_set"], {"revision": 0, "held": []})


class LookIsTheOnlyDifferenceTest(unittest.TestCase):
    def test_requested_scenes_differ_from_den_lighting_only_by_the_look(self):
        for d in decisions(decide.make_policy(), evening()):
            held = {h["entity_id"]: h["pinned"] for h in d["inputs"]["hold_set"]["held"]}
            # What den_lighting itself wants: pinned if held, off after vacancy, else its normal scene.
            incumbent = {b: held.get(b, OFF if rule(d) == "vacant_off" else NORMAL[b]) for b in decide.LIGHTS}
            differ = {b for b in decide.LIGHTS if d["requested_scene"][b] != incumbent[b]}
            if rule(d) == "reading_look":
                self.assertEqual(differ, set(decide.LIGHTS) - set(held))
                self.assertTrue(all(d["requested_scene"][b] == LOOK for b in differ))
            else:
                self.assertEqual(differ, set())


class NothingIsSentTest(unittest.TestCase):
    def test_outputs_are_only_requested_decision_records(self):
        out = feed(decide.make_policy(), evening())
        for o in out:
            d = o["data"]
            self.assertEqual(o["kind"], "decision")
            self.assertEqual(set(d), DECISION_KEYS)  # no actuation: the runner sets it
            self.assertEqual((d["status"], d["policy"], d["actuator_domain"]), ("requested", "decide@v0", "hue-den-4"))
            self.assertEqual((d["transitions"], d["hard_conflicts"]), ({}, []))
            self.assertEqual((d["inputs"]["look"], d["inputs"]["base"]), ("reading@0", "den_lighting normal (mode.normal)"))
            self.assertEqual(d["decision_id"], "decide-%d-%d" % (round(o["t"] * 1000), out.index(o) + 1))
            self.assertLess(len(dumps(o)), 16384)

    def test_module_imports_only_offline_stdlib(self):
        tree = ast.parse(Path(decide.__file__).read_text(encoding="ascii"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(names, {"copy", "hashlib", "json"})


class PurityTest(unittest.TestCase):
    def test_same_events_give_identical_canonical_json(self):
        a = [dumps(o) for o in feed(decide.make_policy(), evening())]
        b = [dumps(o) for o in feed(decide.make_policy(), evening())]
        self.assertEqual(a, b)
        self.assertEqual(len(a), 8)

    def test_no_clock_randomness_socket_or_files(self):
        expected = [dumps(o) for o in feed(decide.make_policy(), evening())]
        boom = mock.Mock(side_effect=AssertionError("decide must not touch this"))
        with mock.patch.object(time, "time", boom), mock.patch.object(time, "monotonic", boom), \
                mock.patch.object(random, "random", boom), mock.patch.object(socket, "socket", boom), \
                mock.patch("builtins.open", boom):
            p = decide.make_policy()
            got = [dumps(o) for o in feed(p, evening())]
        self.assertEqual(got, expected)
        boom.assert_not_called()

    def test_plan_is_under_100_ms_per_call_over_24_minutes(self):
        states = ["starting", "engaged", "engaged", "ending", "none"]
        events = arrival(T0)
        events += [ps(T0 + 3 * k, states[k % 5], "expired" if k % 7 == 0 else "observed") for k in range(1, 480)]
        events += [ev(T0 + 700, "mode", mode(2, [hold(RL, 80, 2202, T0 + 700)])), occ(T0 + 1400, "off")]
        events += [light(T0 + 5 * k, decide.LIGHTS[k % 4], True, 180, 2700) for k in range(1, 288)]
        events += [tick(T0 + 2 * k) for k in range(1, 721)]
        events.sort(key=lambda e: (e["t"], e["kind"] == "tick"))
        original, spans = decide.plan, []

        def timed(*args):
            start = time.perf_counter()
            result = original(*args)
            spans.append(time.perf_counter() - start)
            return result

        with mock.patch.object(decide, "plan", timed):
            out = decisions(decide.make_policy(), events)
        self.assertGreater(len(spans), 1400)
        self.assertGreater(len(out), 100)
        self.assertLess(max(spans), 0.1)


class ComposedScenarioTest(unittest.TestCase):
    def test_exact_sequence_of_ten_minutes(self):
        got = [(d["ts"] - T0, rule(d), d["trigger"]) for d in decisions(decide.make_policy(), evening())]
        self.assertEqual(got, [
            (1.0, "base", "mode change"),
            (60.0, "reading_look", "person_state change"),
            (200.0, "base", "person_state change"),
            (240.0, "base", "hold change"),
            (304.0, "reading_look", "person_state change"),
            (400.0, "base", "person_state change"),
            (540.0, "vacant_off", "time step"),
            (560.0, "vacant_off", "hold change"),
        ])


class PolicyAttributeTest(unittest.TestCase):
    def test_attributes_config_and_stable_sha(self):
        a, b = decide.make_policy(), decide.make_policy()
        self.assertEqual((a.id, a.version, a.code_path), ("decide", "decide-v0", decide.__file__))
        canonical = json.dumps(a.config, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(a.config_sha256, hashlib.sha256(canonical.encode("ascii")).hexdigest())
        self.assertEqual(a.config_sha256, b.config_sha256)
        self.assertEqual(a.config, decide.DEFAULT_CONFIG)
        [d] = decisions(a, arrival())
        self.assertEqual(d["inputs"]["config_sha256"], a.config_sha256)

    def test_parked_decisions_are_labelled_config(self):
        config = decide.DEFAULT_CONFIG
        self.assertEqual(set(config["decisions"]),
                         {"gaming", "hold-lifetime", "normal-scene", "manual-vs-safety", "compensation"})
        for entry in config["decisions"].values():
            self.assertEqual(set(entry), {"value", "status", "effect"})
        self.assertTrue(config["looks"]["reading@0"]["status"].startswith("placeholder"))
        self.assertEqual(len(config["not_used"]), 13)

    def test_override_deep_merges_and_changes_the_sha(self):
        p = decide.make_policy({"vacancy_s": 60, "looks": {"reading@0": {"scene": on(200, 3000)}}})
        self.assertEqual(p.config["vacancy_s"], 60)
        self.assertEqual(p.config["looks"]["reading@0"]["status"], decide.DEFAULT_CONFIG["looks"]["reading@0"]["status"])
        self.assertEqual(decide.DEFAULT_CONFIG["looks"]["reading@0"]["scene"], LOOK)
        self.assertNotEqual(p.config_sha256, decide.make_policy().config_sha256)
        decisions(p, arrival() + [occ(T0 + 10, "off")])
        [d] = decisions(p, [tick(T0 + 70)])
        self.assertEqual(rule(d), "vacant_off")


if __name__ == "__main__":
    unittest.main()

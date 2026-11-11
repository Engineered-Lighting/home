"""Tests for the coordinator's spot mode, test 13 of the den simulation's plan. From the worktree root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Everything is synthetic: den-core's own evenings and invented geometry. Nothing here is a real room or reaches a host.
"""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import random
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import coordinator  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import score  # noqa: E402

HAVE_POLICY = incumbent.find_policy() is not None
T0 = 1793066610.0
CHANNELS = ["F1.spot", "F2.spot", "F3.spot", "F4.spot"]
SPOT = [0.0, 0.0, 0.75]  # invented: the book point
SEAT, HEAD = [0.0, -0.3, 0.0], [0.0, -0.3, 1.25]
# Invented sockets. For a reader facing +y (heading 0): F1 behind, F2 in front, F3 behind but 9 m off, F4 behind with a tilt limit it
# can't meet. Facing -y, F1 and F2 swap. Each alone at full gives FULL at the spot.
FIXTURES = {"F1": {"aim_origin_m": [-1.0, -2.0, 2.4], "horizontal_world_reference": [0, 1, 0], "limits": None},
            "F2": {"aim_origin_m": [1.0, 2.0, 2.4], "horizontal_world_reference": [0, 1, 0], "limits": None},
            "F3": {"aim_origin_m": [0.0, -9.0, 2.4], "horizontal_world_reference": [0, 1, 0], "limits": None},
            "F4": {"aim_origin_m": [0.5, -1.0, 2.4], "horizontal_world_reference": [0, 1, 0], "limits": {"tilt_max_deg": 20.0}}}
FULL = {"F1.spot": 246.171, "F2.spot": 580.0, "F3.spot": 12.0, "F4.spot": 400.0}
LEVELS = [1, 28, 57, 85, 113, 142, 170, 198, 200, 227, 254]  # hop 16's: the solve's own brightness values, and 1


def light_model(full=None, fixtures=None):
    """A typed model in hop 16's shape: one basis row per fixture at the one spot, unit relative, fake, with its fixtures."""
    channels, basis, cct, dimming = [], {}, {}, {}
    for key, value in (full or FULL).items():
        channels.append({"channel_key": key, "entity_id": key, "fixture_id": key.split(".")[0], "channel_id": "spot", "kind": "spot",
                         "cct_range_k": [2700, 2700]})
        basis[key + "@2700K"] = {"sofa-book": value}
        cct[key] = {"kelvin": [2700], "basis_keys": [key + "@2700K"], "entity_id": key}
        dimming[key] = {"levels": LEVELS, "g": [round((u / 254) ** 2.5, 6) for u in LEVELS], "shared_with": None}
    return {"version": "lm-test-spot-1", "fake": True, "basis_unit": "relative", "glare": None, "spot_model": None,
            "error": {"median_pct": None, "p90_pct": None}, "channels": channels, "basis": basis, "cct": cct, "dimming": dimming,
            "sample_points": [{"point_id": "sofa-book", "kind": "task", "xyz": SPOT, "normal": [0, 0, 1]}],
            "surfaces": {"sofa-book": ["sofa-book"]}, "aim_points": {"sofa-book": {"point_m": SPOT, "normal": [0, 0, 1]}},
            "fixtures": copy.deepcopy(fixtures or FIXTURES), "provenance": {"lumens": "assumed", "beam": "datasheet"}}


def g(u):
    """The typed model's dimming at brightness u, as light_model writes it."""
    return round((u / 254) ** 2.5, 6)


def person(t, track="trk-1", state="engaged", heading=0.0, head=HEAD, surface="sofa-book"):
    return {"state_id": "%s@%.3f" % (track, t), "track_id": track, "emitted_ts": t,
            "focused_engagement": {"state": state, "kind": "reading", "since_s": 3.0},
            "field_support": {"focused_engagement": {"support_status": "observed", "last_support_capture_ts": t - 1.0}},
            "working_region": {"surface_id": surface}, "attention": {"head_xyz": head}, "position": {"xyz": SEAT},
            "heading": None if heading is None else {"deg": heading}, "sources": {}, "flags": []}


def scene(states=("starting", "engaged", "engaged"), **options):
    """Occupancy from the track list, then a PersonState every 4 s from 7.2 s."""
    return [(T0, "occupancy", {"value": "on", "via": "den-track"})] + [
        (T0 + 7.2 + 4 * n, "person_state", person(T0 + 7.2 + 4 * n, state=state, **options)) for n, state in enumerate(states)]


def spot_config(path, **extra):
    return {"channels": CHANNELS, "actuator_domain": "virtual-den-4", "light_model": {"source": "file", "path": str(path)}, **extra}


class SpotTest(unittest.TestCase):
    """Test 13: the coordinator's spot mode on invented geometry and a typed model in hop 16's shape, run as hop 17 runs it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.model = self.tmp / "light-model.json"
        self.model.write_text(json.dumps(light_model()), encoding="utf-8")

    def click(self, items, name="run", **extra):
        """Hop 17's In and Out: runner.run with the plant off and no tail, events.write, coordinator_outputs under
        redirect_stdout in a new folder, then check.py. (folder, header, decisions.jsonl's rows)."""
        out = self.tmp / name
        out.mkdir()
        policy = coordinator.make_policy(spot_config(self.model, **extra))
        header, log = runner.run(runner.tagged(items, "synthetic"), [policy], synthetic=True, tail_s=0, plant=False)
        events.write(out / "log.jsonl", header, log)
        with contextlib.redirect_stdout(io.StringIO()):
            run.coordinator_outputs(out, log, policy.light)
        self.assertEqual(check.check(out / "log.jsonl")["reason"], "%d lines replayed byte-identically" % (len(log) + 1))
        decisions = [json.loads(line) for line in (out / "decisions.jsonl").read_text("utf-8").splitlines()]
        self.assert_spot(decisions)
        return out, header, decisions

    def assert_spot(self, decisions):
        """What every spot Decision says, in any scene."""
        for d in decisions:
            self.assertEqual((d["actuator_domain"], d["actuation"], d["version"]), ("virtual-den-4", "synthetic", "decision/v2"))
            self.assertEqual(sorted(d["inputs"]["actuator_state"]), CHANNELS)  # the four spots, never a Hue id
            for state in d["inputs"]["actuator_state"].values():
                self.assertIsInstance(state, dict)  # never null in this mode
                self.assertTrue(events.is_number(state["level"]))
            self.assertEqual(sorted(d["applied_scene"]), CHANNELS)
            lit = [c for c in CHANNELS if d["requested_scene"][c]["on"]]
            self.assertLessEqual(len(lit), len(d["targets"]), "one beam per reader at most")
            self.assertEqual(lit, [c for c in CHANNELS if d["applied_scene"][c]["readback"]["on"]])
            for channel, row in d["applied_scene"].items():
                self.assertEqual((row["aim_source"], row["aim_status"]), ("commanded", "unknown"))  # never settled
                kinds = [step["kind"] for step in row["steps"]]
                for step in row["steps"]:
                    self.assertTrue(events.is_number(step["sent_at"]) and events.is_number(step["result"]["at"]))
                if "move" in kinds:
                    move, fade_in = row["steps"][kinds.index("move")], row["steps"][kinds.index("move") + 1]
                    self.assertEqual((fade_in["kind"], fade_in["sent_at"]), ("fade_in", move["result"]["at"]))  # out at the landing
                    self.assertEqual(move["result"]["at"] - move["sent_at"], coordinator.SPOT_MOVE_S)
                if channel in lit:
                    self.assertEqual(row["readback"]["level"], d["requested_scene"][channel]["level"])
                    self.assertEqual(d["requested_scene"][channel]["target_m"], SPOT)
            surfaces = d["predicted"]["surfaces"]
            self.assertEqual(sorted(surfaces), ["room", "sofa-book", "unit"])
            self.assertTrue(all(events.is_number(surfaces[s]) for s in ("room", "sofa-book")))
            if not lit:
                self.assertEqual(surfaces["sofa-book"], 0.0)

    def test_reading_lights_the_one_fixture_the_rule_allows_with_the_shortfall_shown(self):
        out, header, decisions = self.click(scene(), light_states=[{"id": "frame-0", "ts": T0 - 0.25}])
        self.assertEqual(len(decisions), 1)  # at the first engaged PersonState, then nothing changes
        d = decisions[0]
        self.assertEqual(round(d["ts"] - T0, 1), 11.2)
        self.assertEqual({c: s["level"] for c, s in d["requested_scene"].items()}, {"F1.spot": 1.0, "F2.spot": 0.0, "F3.spot": 0.0, "F4.spot": 0.0})
        self.assertEqual(d["requested_scene"]["F1.spot"], {"on": True, "level": 1.0, "cct_k": 2700, "target_cct_k": 2700, "clip_reason": None,
                                                           "aim": "sofa-book", "target_m": SPOT})
        self.assertEqual(d["targets"], {"trk-1": {"surface_id": "sofa-book", "lx": None, "value": 300, "unit": "relative",
                                                  "note": "modeled lux, assumed lumens"}})
        self.assertEqual(d["predicted"]["surfaces"], {"room": 246.171, "sofa-book": 246.171, "unit": "relative"})
        self.assertEqual({s["channel"]: s["reason"] for s in d["skipped"]},
                         {"F2.spot": "in_front_of_reader", "F3.spot": "too_far", "F4.spot": "pan_tilt_infeasible:tilt_limit"})
        self.assertEqual([s["kind"] for s in d["applied_scene"]["F1.spot"]["steps"]], ["move", "fade_in"])
        self.assertEqual(d["applied_scene"]["F1.spot"]["steps"][1]["result"]["readback"], {"level": 1.0, "cct_k": 2700})
        self.assertEqual(d["inputs"]["t_exec"]["F1.spot"], d["ts"] + coordinator.SPOT_MOVE_S + 2)  # the gate's look_fade_s
        self.assertEqual((d["inputs"]["light_state"], d["inputs"]["light_state_ts"]), ("frame-0", T0 - 0.25))
        self.assertIn("F1.spot lit for trk-1 at level 1.0, predicting 246.171 of 300 asked at sofa-book, modeled lux, assumed lumens", d["why"])
        self.assertIn("refused for trk-1: F2.spot in front of reader, F3.spot too far, F4.spot pan tilt infeasible", d["why"])
        self.assertEqual([c["why"] for c in d["candidates"]], ["rejected: cost 1.000000, above the chosen %.6f" % d["candidates"][1]["cost"], "chosen"])
        self.assertEqual((header["plant"], header["tail_s"]), (False, 0))
        self.assertEqual(score.report(out / "log.jsonl", preview=True)[1], 2)  # plumbing: score.py refuses it

    def test_frames_change_only_the_light_state_fields(self):
        # Hop 25's first run places the all-off frames from when each Decision comes, so a frame must change nothing else.
        plain = self.click(scene(("starting", "engaged", "none")), "plain")[2]
        framed = self.click(scene(("starting", "engaged", "none")), "framed", light_states=[{"id": "off-0", "ts": T0 + 11.199}])[2]
        strip = lambda d: {**d, "inputs": {k: v for k, v in d["inputs"].items() if not k.startswith("light_state")}}  # noqa: E731
        self.assertEqual([events.dumps(strip(d)) for d in framed], [events.dumps(strip(d)) for d in plain])
        self.assertEqual([d["inputs"]["light_state"] for d in framed], ["off-0", "off-0"])
        self.assertEqual({d["inputs"]["light_state_status"] for d in plain}, {coordinator.NO_FRAME_GIVEN})

    def test_facing_the_other_way_lights_the_other_fixture_at_a_solved_level(self):
        d = self.click(scene(heading=180.0))[2][0]
        self.assertEqual([c for c in CHANNELS if d["requested_scene"][c]["on"]], ["F2.spot"])
        self.assertEqual(d["requested_scene"]["F2.spot"]["level"], 198 / 254)  # unrounded, as _level gives it
        self.assertEqual(d["skipped"][0], {"channel": "F1.spot", "reason": "in_front_of_reader", "hold_ref": None, "track_id": "trk-1"})

    def test_with_no_heading_only_the_in_front_test_is_skipped_and_one_beam_lights(self):
        d = self.click(scene(heading=None))[2][0]
        self.assertEqual({s["channel"] for s in d["skipped"]}, {"F3.spot", "F4.spot"})
        self.assertEqual([c for c in CHANNELS if d["requested_scene"][c]["on"]], ["F2.spot"])  # F1 passes too, and stays off
        self.assertIn("trk-1 has no heading: the in-front test is skipped", d["why"])

    def test_two_readers_get_a_beam_each_at_most(self):
        items = scene() + [(T0 + 11.4, "person_state", person(T0 + 11.4, track="trk-2", heading=180.0))]
        d = self.click(items)[2][-1]
        lit = {c: d["recipients"][c]["targets"][0]["track_id"] for c in CHANNELS if d["requested_scene"][c]["on"]}
        self.assertEqual(lit, {"F1.spot": "trk-1", "F2.spot": "trk-2"})

    def use_model(self, full=None, fixtures=None):
        self.model.write_text(json.dumps(light_model(full, fixtures)), encoding="utf-8")

    def test_the_heading_turns_clockwise_from_plus_y(self):
        # Heading 90 faces +x. A sits behind and to the left of the reader at the seat, B in front and to the right: read
        # counterclockwise, or with sine and cosine swapped, the reader faces -x or +y and the two swap.
        a, b = [-1.0, 0.7, 2.4], [1.0, -1.3, 2.4]
        rule = coordinator._spot_rule()
        fixture = lambda origin: {"aim_origin_m": origin, "horizontal_world_reference": [0, 1, 0], "limits": None}  # noqa: E731
        for heading, (want_a, want_b) in ((90.0, (None, "in_front_of_reader")), (270.0, ("in_front_of_reader", None))):
            with self.subTest(heading=heading):
                reader = person(T0, heading=heading)
                self.assertEqual((rule.refusal(fixture(a), reader, SPOT), rule.refusal(fixture(b), reader, SPOT)), (want_a, want_b))
        self.assertEqual(rule.startup_line({"A": fixture(a), "B": fixture(b)}, [0.0, -0.3, 0.45], [1.0, 0.0], SPOT),
                         ["A clear", "B refused: in front of reader"])
        # Through the coordinator: facing +x, F1 (to the left and behind) lights and F2 (in front) is refused.
        d = self.click(scene(heading=90.0))[2][0]
        self.assertEqual([c for c in CHANNELS if d["requested_scene"][c]["on"]], ["F1.spot"])
        self.assertIn({"channel": "F2.spot", "reason": "in_front_of_reader", "hold_ref": None, "track_id": "trk-1"}, d["skipped"])

    def test_a_tracked_non_reader_is_not_a_reader(self):
        # A reading track and a phone track facing the other way: only the reader gets a target, and F2 stays off.
        items = scene() + [(T0 + 11.4, "person_state", person(T0 + 11.4, track="trk-2", state="none", heading=180.0))]
        decisions = self.click(items)[2]
        self.assertEqual([sorted(d["targets"]) for d in decisions], [["trk-1"]])
        self.assertEqual([c for c in CHANNELS if decisions[0]["requested_scene"][c]["on"]], ["F1.spot"])

    def test_two_readers_who_want_the_same_fixture_get_it_once(self):
        # Both face +y, so F1 is the only fixture either may have: the first reader keeps it, the second gets no beam, and
        # F1 is neither refused nor offered for the second.
        items = scene() + [(T0 + 11.4, "person_state", person(T0 + 11.4, track="trk-2"))]
        d = self.click(items)[2][-1]
        lit = {c: d["recipients"][c]["targets"][0]["track_id"] for c in CHANNELS if d["requested_scene"][c]["on"]}
        self.assertEqual(lit, {"F1.spot": "trk-1"})
        self.assertIn("no beam for trk-2", d["why"])
        self.assertEqual(sorted(s["channel"] for s in d["skipped"] if s["track_id"] == "trk-2"), ["F2.spot", "F3.spot", "F4.spot"])
        self.assertEqual([c["scene_id"] for c in d["candidates"] if c["track_id"] == "trk-2"], ["off"])

    def test_the_look_level_200_is_tried_and_can_win(self):
        # F1 gives exactly the target at the look's brightness, 200, between the solve's 198 and 227.
        self.use_model(full={**FULL, "F1.spot": 300 / g(200)})
        d = self.click(scene())[2][0]
        self.assertEqual(d["requested_scene"]["F1.spot"]["level"], 200 / 254)
        self.assertEqual([c["scene_id"] for c in d["candidates"] if c["why"] == "chosen"], ["F1.spot@200"])

    def test_the_excess_weighs_half_the_shortfall_and_off_pulls_a_little(self):
        # F1 gives 360 at 198 (20% over) and about 246 at 170 (18% short): at half weight the excess costs less, at full it
        # would not. Each cost is the formula's, the pull toward off included.
        self.use_model(full={**FULL, "F1.spot": 360 / g(198)})
        d = self.click(scene(), "excess")[2][0]
        chosen = [c for c in d["candidates"] if c["why"] == "chosen"]
        self.assertEqual([c["scene_id"] for c in chosen], ["F1.spot@198"])
        self.assertAlmostEqual(chosen[0]["cost"], 0.5 * 0.2 ** 2 + 0.001 * 198 / 255, places=6)
        # A fixture that gives nothing at the spot costs a little more than off at its dimmest level, so off is chosen
        # over it by the pull, not by a tie.
        self.use_model(full={**FULL, "F1.spot": 0.0})
        d = self.click(scene(), "dark")[2][0]
        self.assertFalse(any(s["on"] for s in d["requested_scene"].values()))
        f1 = [c for c in d["candidates"] if c["scene_id"].startswith("F1.spot")]
        self.assertEqual([c["scene_id"] for c in f1], ["F1.spot@28"])
        self.assertAlmostEqual(f1[0]["cost"], 1.0 + 0.001 * 28 / 255, places=6)
        self.assertEqual(f1[0]["why"], "rejected: cost %.6f, above the chosen 1.000000" % f1[0]["cost"])

    def test_of_two_equal_fixtures_the_first_is_chosen_and_the_tie_is_said(self):
        # With no heading, F1 and F2 both pass; give them the same light at the spot. The first of equals wins, and the
        # other is not called costlier.
        self.use_model(full={**FULL, "F2.spot": FULL["F1.spot"]})
        d = self.click(scene(heading=None))[2][0]
        self.assertEqual([c for c in CHANNELS if d["requested_scene"][c]["on"]], ["F1.spot"])
        rows = {c["scene_id"]: c for c in d["candidates"]}
        self.assertEqual(rows["F1.spot@255"]["cost"], rows["F2.spot@255"]["cost"])
        self.assertEqual(rows["F2.spot@255"]["why"], "rejected: cost %.6f, no lower than the chosen %.6f" % ((rows["F1.spot@255"]["cost"],) * 2))
        self.assertTrue(rows["off"]["why"].startswith("rejected: cost 1.000000, above the chosen"))

    def test_reading_ending_turns_every_spot_off(self):
        decisions = self.click(scene(("starting", "engaged", "ending", "none")))[2]
        self.assertEqual([round(d["ts"] - T0, 1) for d in decisions], [11.2, 19.2])  # ending still reads, as decide's rule says
        off = decisions[-1]
        self.assertFalse(any(s["on"] for s in off["requested_scene"].values()))
        self.assertEqual([s["kind"] for s in off["applied_scene"]["F1.spot"]["steps"]], ["fade_out"])
        self.assertEqual(off["inputs"]["actuator_state"]["F1.spot"], {"on": True, "level": 1.0, "cct_k": 2700, "aim": "sofa-book"})
        self.assertEqual((off["why"], off["predicted"]["surfaces"]["sofa-book"]), ("base: every spot off.", 0.0))

    def test_a_head_on_the_line_refuses_that_fixture_and_the_reader_gets_one_decision_with_no_steps(self):
        on_line = [SPOT[i] + 0.3 * (FIXTURES["F1"]["aim_origin_m"][i] - SPOT[i]) for i in range(3)]
        decisions = self.click(scene(head=on_line))[2]
        self.assertEqual(len(decisions), 1)  # three PersonStates and the ticks, and one Decision
        d = decisions[0]
        self.assertEqual(d["skipped"][0]["reason"], "beam_through_head")  # F1, which lit with the head off the line
        self.assertEqual(len(d["skipped"]), 4)
        self.assertEqual([row["steps"] for row in d["applied_scene"].values()], [[]] * 4)
        self.assertIn("no beam for trk-1; refused for trk-1: F1.spot beam through head", d["why"])

    def test_no_head_no_beam(self):
        d = self.click(scene(head=None))[2][0]
        self.assertEqual({s["reason"] for s in d["skipped"]}, {"no_head"})
        self.assertFalse(any(s["on"] for s in d["requested_scene"].values()))

    def test_a_region_other_than_the_spot_gets_no_target_and_says_why(self):
        decisions = self.click(scene(surface="handheld"))[2]
        self.assertEqual(len(decisions), 1)
        self.assertEqual((decisions[0]["targets"], decisions[0]["skipped"]), ({}, []))
        self.assertEqual(decisions[0]["why"], "reading_look: trk-1 reads at handheld, which has no spot: no target.")

    def test_nobody_reading_writes_no_decision_and_an_empty_file(self):
        for name, items in (("phone", scene(("none", "none"))), ("nobody", scene(()))):
            out, _, decisions = self.click(items, name)
            self.assertEqual((decisions, (out / "decisions.jsonl").read_bytes()), ([], b""))

    def test_the_model_hash_is_the_same_for_every_scene(self):
        runs = [self.click(items, name)[1] for name, items in (("reading", scene()), ("phone", scene(("none",))), ("away", scene(heading=180.0)))]
        hashes = {h["policies"][0]["config"]["light_model"]["sha256"] for h in runs}
        self.assertEqual(hashes, {events.sha256_json(light_model())})

    def test_a_decision_takes_under_100_ms_and_reads_no_clock_randomness_socket_or_file(self):
        p = coordinator.make_policy(spot_config(self.model))
        boom = mock.Mock(side_effect=AssertionError("the coordinator must not touch this"))
        slowest = 0.0
        with mock.patch.object(time, "time", boom), mock.patch.object(time, "monotonic", boom), \
                mock.patch.object(random, "random", boom), mock.patch.object(socket, "socket", boom), mock.patch("builtins.open", boom):
            for t, kind, data in scene(("starting", "engaged", "none")):
                start = time.perf_counter()
                p.handle({"schema": events.SCHEMA, "t": t, "seq": 0, "kind": kind, "source": "synthetic", "data": data})
                slowest = max(slowest, time.perf_counter() - start)
        boom.assert_not_called()
        self.assertEqual(p.made, 2)
        self.assertLess(slowest, 0.1)

    def test_a_domain_with_no_channels_and_a_channel_with_no_fixture_are_refused(self):
        for config, words in (({"actuator_domain": "virtual-den-4", "channels": []}, "needs config channels"),
                              ({"actuator_domain": "virtual-den-4"}, "needs config channels"),
                              (spot_config(self.model, actuator_domain="hue-den-4"), "writes actuator_domain virtual-den-4"),
                              (spot_config(self.model, channels=["F1.spot", "F5.spot"]), "a channel and fixture"),
                              (spot_config(self.model, channels=["F1.spot", "F1.spot"]), "each once")):
            with self.subTest(config=config), self.assertRaises(ValueError) as caught:
                coordinator.make_policy(config)
            self.assertIn(words, str(caught.exception))

    def test_only_spot_mode_pins_the_rule_and_a_changed_rule_or_threshold_refuses_the_log(self):
        self.assertEqual(sorted(coordinator.make_policy().config["code"]), sorted(coordinator.PINS))
        spot = coordinator.make_policy(spot_config(self.model)).config
        self.assertEqual(sorted(spot["code"]), sorted({*coordinator.PINS, *coordinator.SPOT_PINS}))
        self.assertEqual(spot["spot_target"], coordinator.load_gate()["fixed"]["service_floor_lx"]["value"]["fine"])
        out = self.click(scene())[0]
        for name, old, new in (("settings", "max_beam_m: float = 8.0", "max_beam_m: float = 9.0"), ("spot_rule", "0.5, 0.23", "0.5, 0.24")):
            edited = self.tmp / (name + ".py")
            edited.write_text(coordinator.SPOT_PINS[name].read_text("utf-8").replace(old, new), encoding="utf-8")
            with self.subTest(name=name), mock.patch.dict(coordinator.SPOT_PINS, {name: edited}):
                self.assertEqual(check.check(out / "log.jsonl")["reason"], "coordinator refused its logged config: code changed: " + name)

    def test_controls_a_second_lit_fixture_and_a_settled_aim_fail_the_checks(self):
        d = self.click(scene())[2][0]
        for change in (lambda x: x["requested_scene"]["F2.spot"].update(on=True, level=0.5),
                       lambda x: x["applied_scene"]["F1.spot"].update(aim_status="settled"),
                       lambda x: x["applied_scene"]["F1.spot"]["steps"][1].update(sent_at=x["ts"]),
                       lambda x: x["inputs"]["actuator_state"].update({"F3.spot": None})):
            bad = copy.deepcopy(d)
            change(bad)
            with self.assertRaises(AssertionError):
                self.assert_spot([bad])

    def test_the_rule_imports_the_standard_library_and_says_the_startup_line(self):
        tree = ast.parse(coordinator.SPOT_PINS["spot_rule"].read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(names, {"importlib", "importlib.util", "math", "sys"})
        self.assertEqual({n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}, {"pathlib"})
        rule = coordinator._spot_rule()
        fixtures = light_model()["fixtures"]
        self.assertEqual(rule.startup_line(fixtures, [0.0, -0.3, 0.45], [0.0, 1.0], SPOT),
                         ["F1 clear", "F2 refused: in front of reader", "F3 refused: too far", "F4 refused: pan tilt infeasible"])
        self.assertEqual(rule.startup_line(fixtures, [0.0, -0.3, 0.45], [0.0, -1.0], SPOT)[:2], ["F1 refused: in front of reader", "F2 clear"])
        self.assertFalse(any(path.name == "__pycache__" for path in coordinator.BRAIN.iterdir()))
# The golden Hue Decision log, taken from the unedited coordinator at 03721630, before spot mode: decisions.jsonl's sha256 for
# run.py --synthetic evening with these flags and --policies need incumbent coordinator. The header is left out, because it pins
# coordinator.py's own hash; each Decision still logs config_sha256, so these move if PINS or _defaults do.
HUE_BEFORE_SPOT_MODE = {
    (): "8cc52378729ee17d2d0dc772378bf6deeec2d4709886bc9517e62dc3ca2440e4",
    ("--touch",): "88f0e28311c32eded49baa4149526dfdfba2cbbf8cfc2501a3fec6310cea9243",
    ("--game",): "7a4e3cd81f53db544a62b9651e8381d50d76d05c541cd093023f4be0ef6ebc6b",
    ("--touch", "--game"): "100824e7a7102ff021c974306b1fab0ea8c21fbf2a3c27466c5171cfcaf965a8",
}


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class HueGoldenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_hue_decisions_equal_the_golden_ones_taken_before_spot_mode(self):
        for n, (flags, sha) in enumerate(HUE_BEFORE_SPOT_MODE.items()):
            with self.subTest(flags=flags), mock.patch("builtins.print"):
                out = self.tmp / str(n)
                run.main(["--synthetic", "evening", *flags, "--policies", "need", "incumbent", "coordinator", "--out", str(out)])
                self.assertEqual(hashlib.sha256((out / "decisions.jsonl").read_bytes()).hexdigest(), sha)


if __name__ == "__main__":
    unittest.main()

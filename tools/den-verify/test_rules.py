"""The rules the lighting seam fixed: what "verified" means, unknown state, a person's change, two evenings, unknown causes.

On den-verify's own synthetic shape, so they hold with or without den-light. test_light_model.py has Stage 1's shape and
test_seam_den_core.py the real tools. Everything here is synthetic.

    python -B -m unittest discover -s tools/den-verify -p "test_*.py"
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import BadFile, Clock, write_jsonl  # noqa: E402
from score import Run  # noqa: E402
from test_verify import Quiet, by_id, closed, point, residual, send  # noqa: E402

B0, B1, B2, B3 = syn.HUE
T = syn.T0
TARGETS = {"A": {"surface_id": "p0"}, "B": {"surface_id": "p1"}}
TWO = {"p0": {B0: 100.0}, "p1": {B0: 100.0}}


def model(points=None):
    return syn.hue_model(points or TWO, kelvins=(2700,))


def run(decisions, frames, points=None, overrides=None, data=None, **kw):
    return Run(closed(overrides), LightModel(data or model(points)), decisions, frames, clock=Clock(syn.UTC_OFFSET_S), **kw).run()


def frame(at, value=None, ident=None, **values):
    return syn.light_state(at, values or {"p0": value, "p1": value}, anchored=False, ident=ident)


def landed(did="d", at=T, read="ls-r", before=0.2, after=0.8, targets=TARGETS, **kw):
    return syn.hue_decision(did, at + 0.5, {B0: before}, {B0: {"level": after, "steps": send(at + 0.5)}}, read, targets=targets, **kw)


def not_verified(report, did="d"):
    return by_id(report["decisions"]["not_verified"], "decision_id", did)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def preview(self, decisions, frames, data=None, *flags):
        write_jsonl(self.tmp / "decisions.jsonl", decisions)
        write_jsonl(self.tmp / "frames.jsonl", frames)
        (self.tmp / "model.json").write_text(json.dumps(data or model()), encoding="utf-8")
        out = self.tmp / ("out%d" % len(list(self.tmp.iterdir())))
        with Quiet() as q:
            code = cli.main(["run", "--decisions", str(self.tmp / "decisions.jsonl"), "--light-states", str(self.tmp / "frames.jsonl"),
                             "--model", str(self.tmp / "model.json"), "--utc-offset-h", "-7", "--out", str(out), "--preview", *flags])
        self.assertEqual(code, 0, q.buf.getvalue())
        return q.buf.getvalue(), json.loads((out / "report-preview.json").read_text("utf-8"))


class VerifiedMeansSomethingWasJudged(unittest.TestCase):
    def test_a_landing_is_verified(self):
        decisions, _, report = run([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        self.assertEqual(report["decisions"]["verified"], 1)
        self.assertEqual(report["decisions"]["not_verified"], [])
        self.assertIs(decisions[0]["verify"]["within_tolerance"], True)
        self.assertNotIn("verify_status", decisions[0])

    def test_a_miss_is_verified_too(self):
        _, _, report = run([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, 20.0)])
        self.assertEqual(report["decisions"]["verified"], 1)
        self.assertEqual(report["hit_rate"]["verdict"], "fail")

    def test_no_frames_at_all_verifies_nothing_and_says_why(self):
        decisions, _, report = run([landed("d-1"), landed("d-2", at=T + 100)], [])
        self.assertEqual(report["decisions"]["total"], 2)
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        for d in ("d-1", "d-2"):
            entry = not_verified(report, d)
            self.assertEqual(entry["kind"], "no frame after settling")
            self.assertIn("no frame after", entry["reason"])
            self.assertIn("no read frame", entry["reason"])
            record = by_id(decisions, "decision_id", d)
            self.assertIsNone(record["verify"])
            self.assertEqual(record["verify_status"], entry["reason"])

    def test_a_decision_that_names_no_read_frame(self):
        dec = landed(read=None)
        dec["inputs"]["light_state_status"] = "no LightState given"
        decisions, _, report = run([dec], [frame(T, 20.0), frame(T + 5, 80.0)])
        self.assertEqual(report["decisions"]["verified"], 0)
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "no read frame")
        self.assertIn("no read frame", entry["reason"])
        self.assertIn("no LightState given", entry["reason"])
        self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"unverifiable"})
        self.assertEqual(decisions[0]["verify_status"], entry["reason"])
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"], report["hit_rate"]["verdict"]), (0, 0, "insufficient"))

    def test_under_the_zero_background_no_read_frame_stops_nothing(self):
        decisions, _, report = run([landed(read=None)], [frame(T, 20.0), frame(T + 5, 80.0)], overrides={"background": "zero"})
        self.assertEqual(report["decisions"]["verified"], 1)
        self.assertEqual(report["hit_rate"]["hits"], 2)
        self.assertEqual({i["status"] for i in decisions[0]["verify"]["increments"]}, {"unverifiable"})

    def test_a_read_frame_it_names_that_is_not_there(self):
        _, _, report = run([landed(read="ls-gone")], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "no read frame")
        self.assertIn("ls-gone", entry["reason"])

    def test_no_frame_after_settling(self):
        _, _, report = run([landed()], [frame(T, 20.0, "ls-r"), frame(T + 1, 40.0)])
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "no frame after settling")
        self.assertIn("no frame after", entry["reason"])
        self.assertNotIn("no read frame", entry["reason"])

    def test_every_frame_after_it_mid_fade(self):
        later = landed("d-2", at=T + 4)
        _, _, report = run([landed(), later], [frame(T, 20.0, "ls-r"), frame(T + 4.6, 40.0)])
        self.assertEqual(not_verified(report)["kind"], "no frame after settling")

    def test_superseded_before_it_settled(self):
        first, second = landed("d-1"), landed("d-2", at=T + 1)
        decisions, _, report = run([first, second], [frame(T, 20.0, "ls-r"), frame(T + 6, 80.0)])
        self.assertEqual(not_verified(report, "d-1")["kind"], "superseded")
        self.assertEqual(by_id(decisions, "decision_id", "d-1")["verify_status"], "superseded before it settled: d-2 sent a step first")

    def test_a_decision_with_no_applied_scene_gets_that_reason_not_a_zero_point_verified(self):
        for name, change in (("absent", None), ("null", {"applied_scene": None}), ("empty", {"applied_scene": {}})):
            dec = landed()
            del dec["applied_scene"]
            dec.update(change or {})
            with self.subTest(name):
                decisions, states, report = run([dec], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
                self.assertEqual(report["decisions"]["verified"], 0)
                entry = not_verified(report)
                self.assertEqual(entry["kind"], "no applied_scene")
                self.assertIn("no applied_scene", entry["reason"])
                self.assertIsNone(decisions[0]["verify"])
                self.assertEqual(decisions[0]["verify_status"], entry["reason"])
                self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
                self.assertEqual(report["rows"], [])
                self.assertEqual(states[1]["residual"], [])
                self.assertIn("no applied_scene", states[1]["residual_status"])

    def test_a_decision_that_names_no_surface(self):
        decisions, _, report = run([landed(targets={})], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(not_verified(report)["kind"], "no surface named")
        self.assertEqual(decisions[0]["verify"]["points"], [])
        self.assertEqual(decisions[0]["verify_status"], not_verified(report)["reason"])

    def test_a_surface_outside_the_map_names_the_flag_and_is_not_verified(self):
        dec = landed(targets={"A": {"surface_id": "room"}})
        decisions, _, report = run([dec], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        self.assertEqual(report["decisions"]["verified"], 0)
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "no surface map")
        self.assertIn("--surfaces", entry["reason"])
        self.assertIn("room", entry["reason"])
        self.assertIn("--surfaces", point(decisions[0], "room")["why"])
        self.assertEqual(point(decisions[0], "room")["status"], "unverifiable")

    def test_a_surface_the_frame_does_not_show_is_not_verified(self):
        decisions, _, report = run([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, q=1.0)])
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "nothing readable")
        self.assertIn("not in frame", entry["reason"])
        self.assertEqual(point(decisions[0], "p0")["why"], "not in frame")

    def test_nothing_lit_and_nothing_changed(self):
        dec = landed(before=0.0, after=0.0)
        _, _, report = run([dec], [frame(T, 0.0, "ls-r"), frame(T + 5, 0.0)])
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(not_verified(report)["kind"], "nothing lit")

    def test_a_stale_read_frame_is_listed_with_why_and_keeps_its_own_list(self):
        first = landed("d-1", at=T + 0.2 - 0.5)
        second = landed("d-2", at=T + 4.2, before=0.8, after=0.9)
        decisions, _, report = run([first, second], [frame(T, 20.0, "ls-r"), frame(T + 10, 90.0)])
        self.assertEqual([s["decision_id"] for s in report["decisions"]["read_frame_stale"]], ["d-2"])
        entry = not_verified(report, "d-2")
        self.assertEqual(entry["kind"], "read frame stale")
        self.assertIn("d-1", entry["reason"])
        self.assertEqual({p["status"] for p in by_id(decisions, "decision_id", "d-2")["verify"]["points"]}, {"unverifiable"})

    def test_a_decision_with_every_point_left_out_is_listed_and_nothing_counts(self):
        # B1, held at full, gives 900 of 980 and B0 moves too little to show: the hold rules leave both points out
        points = {"p0": {B0: 100.0, B1: 900.0}, "p1": {B0: 100.0, B1: 900.0}}
        dec = syn.hue_decision("d", T + 0.5, {B0: 0.2, B1: 1.0}, {B0: {"level": 0.8, "steps": send(T + 0.5)}, B1: {"status": "held", "level": 1.0}}, "ls-r",
                               targets=TARGETS, hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1], "pinned": {"level_pct": 100, "cct_k": 2700}}]})
        decisions, _, report = run([dec], [frame(T, 920.0, "ls-r"), frame(T + 5, 980.0)], points)
        self.assertEqual(report["excluded_held_or_superseded"], 2)
        self.assertEqual(report["decisions"]["verified"], 0)
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "left out")
        self.assertIn("held or superseded", entry["reason"])
        self.assertIsNone(decisions[0]["verify"]["within_tolerance"])
        self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"within"})  # read, and still written

    def test_one_point_judged_is_enough_and_the_other_says_why(self):
        decisions, _, report = run([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, p0=80.0)])
        self.assertEqual(report["decisions"]["verified"], 1)
        self.assertEqual(report["decisions"]["not_verified"], [])
        self.assertEqual(point(decisions[0], "p1")["why"], "not in frame")
        self.assertNotIn("why", point(decisions[0], "p0"))
        self.assertNotIn("verify_status", decisions[0])

    def test_a_decision_not_admitted_is_verified_and_left_out_of_the_hit_rate_as_the_note_says(self):
        first = landed("d-1", at=T)
        second = landed("d-2", at=T + 1.5, before=0.8, after=0.9)
        # d-2 goes out while d-1's fade is landing, on a frame taken before d-1 went out
        _, _, report = run([first, second], [frame(T, 20.0, "ls-r"), frame(T + 1.9, 80.0, "ls-m"), frame(T + 9, 90.0)], overrides={"background": "zero"})
        self.assertEqual([x["decision_id"] for x in report["decisions"]["not_admitted"]], ["d-2"])
        self.assertEqual(report["decisions"]["verified"], 1)
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (0, 0))

    def test_verified_is_exactly_the_decisions_with_a_verdict_of_their_own(self):
        cases = [landed("d-1"), landed("d-2", at=T + 100, read=None), landed("d-3", at=T + 200, read="ls-r3"), landed("d-4", at=T + 300, read="ls-r4", targets={}),
                 landed("d-5", at=T + 400, read="ls-r5")]
        frames = [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0), frame(T + 105, 80.0), frame(T + 200, 20.0, "ls-r3"), frame(T + 205, 20.0),
                  frame(T + 300, 20.0, "ls-r4"), frame(T + 305, 80.0), frame(T + 400, 20.0, "ls-r5")]
        decisions, _, report = run(cases, frames)
        judged = [d["decision_id"] for d in decisions if d.get("verify") and d["verify"]["within_tolerance"] is not None]
        self.assertEqual(judged, ["d-1", "d-3"])
        self.assertEqual(report["decisions"]["verified"], 2)
        self.assertEqual(sorted(e["decision_id"] for e in report["decisions"]["not_verified"]), ["d-2", "d-4", "d-5"])
        self.assertEqual(report["decisions"]["total"], report["decisions"]["verified"] + len(report["decisions"]["not_verified"]))
        self.assertEqual(report["decisions"]["not_verified_by_kind"], {"no read frame": 1, "no surface named": 1, "no frame after settling": 1})


class WhatTheConsoleSays(Cli):
    def test_its_first_line_gives_both_counts(self):
        out, report = self.preview([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        self.assertEqual(out.splitlines()[0], "decisions verified 1 of 1, 0 not verified; hit rate 1.0 (2 of 2), gate 0.85: pass (preview)")
        self.assertEqual(report["hit_rate"]["verdict"], "pass")

    def test_with_nothing_judged_it_never_says_all_verified(self):
        out, _ = self.preview([landed("d-1"), landed("d-2", at=T + 100)], [])
        first = out.splitlines()[0]
        self.assertEqual(first, "decisions verified 0 of 2, 2 not verified; hit rate None (0 of 0), gate 0.85: insufficient (preview)")
        self.assertIn("not verified: 2 no frame after settling", out)
        out.encode("ascii")

    def test_the_next_line_counts_each_reason_and_the_report_lists_each_decision(self):
        decisions = [landed("d-1"), landed("d-2", at=T + 100, read=None), landed("d-3", at=T + 200, targets={"A": {"surface_id": "room"}})]
        out, report = self.preview(decisions, [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0), frame(T + 105, 80.0), frame(T + 205, 80.0)])
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("decisions verified 1 of 3, 2 not verified; "), lines[0])
        self.assertEqual(lines[1], "not verified: 1 no read frame, 1 no surface map (--surfaces); report-preview.json lists each with why")
        self.assertEqual(len(report["decisions"]["not_verified"]), 2)

    def test_it_says_what_the_model_ran_on(self):
        out, _ = self.preview([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        self.assertIn("light model lm-hue-test: predict.py (rung 8 Stage 1, tools/den-light)", out)


class UnknownIsNeverZero(Cli):
    """A lit bulb with no level, or a null entry, is unknown: what depends on it is unverifiable, not a hit or a miss."""
    # B0 lights p0 and p1; B1 lights p1 and p2; p3 is lit by B0 alone, so B1's state says nothing there
    POINTS = {"p0": {B0: 100.0}, "p1": {B0: 100.0, B1: 100.0}, "p2": {B1: 100.0}, "p3": {B0: 50.0}}
    TARGETS = {f"T{i}": {"surface_id": p} for i, p in enumerate(POINTS)}

    def decision(self, b1_before, b1_applied=None, hold_set=None):
        """B0 goes 0.2 to 0.8. B1's own entry in actuator_state is b1_before, and in applied_scene b1_applied, if given."""
        dec = syn.hue_decision("d", T + 0.5, {B0: 0.2, B1: 0.5}, {B0: {"level": 0.8, "steps": send(T + 0.5)}}, "ls-r", targets=self.TARGETS, hold_set=hold_set)
        dec["inputs"]["actuator_state"][B1] = b1_before
        if b1_applied is not None:
            dec["applied_scene"][B1] = b1_applied
        return dec

    def frames(self, b1=0.5, scale=1.0):
        def light(b0, b1_level):
            return {p: scale * (v.get(B0, 0) * b0 + v.get(B1, 0) * b1_level) for p, v in self.POINTS.items()}

        return [syn.light_state(T, light(0.2, 0.5), anchored=False, ident="ls-r"), syn.light_state(T + 5, light(0.8, b1), anchored=False)]

    def go(self, dec, frames=None, overrides=None, **kw):
        return run([dec], frames or self.frames(), self.POINTS, overrides=overrides, **kw)

    SAYS_NOTHING = ("null", None), ("empty", {}), ("on with no level", {"on": True}), ("on with a null level", {"on": True, "level": None, "cct_k": 2700}), ("a null level", {"level": None})

    def test_before_the_decision_it_leaves_what_it_reaches_unverifiable(self):
        for name, entry in self.SAYS_NOTHING:
            with self.subTest(name):
                decisions, _, report = self.go(self.decision(entry))
                d = decisions[0]
                for p in ("p1", "p2"):
                    self.assertEqual(point(d, p)["status"], "unverifiable", p)
                    self.assertIn("unknown state", point(d, p)["why"])
                    self.assertIn(syn.hue_channel(B1), point(d, p)["why"])  # the model's own name for it
                    self.assertIn("before", point(d, p)["why"])
                    self.assertIsNone(point(d, p)["residual"])
                for p in ("p0", "p3"):  # B1 gives them nothing, at any level
                    self.assertEqual(point(d, p)["status"], "within", p)
                self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (2, 0))
                self.assertEqual(report["points"]["unverifiable"], 2)
                self.assertEqual(report["decisions"]["verified"], 1)

    def test_the_report_says_which_decisions_and_why(self):
        _, _, report = self.go(self.decision(None))
        [entry] = report["decisions"]["unknown_state"]
        self.assertEqual((entry["decision_id"], entry["points"]), ("d", 2))
        self.assertIn(syn.hue_channel(B1), entry["why"])

    def test_no_reading_turns_it_into_a_hit_or_a_miss(self):
        for scale in (0.0, 0.5, 1.0, 2.0, 100.0):
            decisions, _, report = self.go(self.decision(None), self.frames(scale=scale))
            self.assertEqual(point(decisions[0], "p1")["status"], "unverifiable", scale)
            self.assertEqual(report["points"]["unverifiable"], 2, scale)
            self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 2, scale)

    def test_with_every_point_depending_on_it_the_decision_is_not_verified_and_says_unknown_state(self):
        points = {"p1": {B0: 100.0, B1: 100.0}, "p2": {B1: 100.0}}
        dec = self.decision(None)
        dec["targets"] = {"A": {"surface_id": "p1"}, "B": {"surface_id": "p2"}}
        frames = [syn.light_state(T, {"p1": 70.0, "p2": 50.0}, anchored=False, ident="ls-r"), syn.light_state(T + 5, {"p1": 130.0, "p2": 50.0}, anchored=False)]
        decisions, _, report = run([dec], frames, points)
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        entry = not_verified(report)
        self.assertEqual(entry["kind"], "unknown state")
        self.assertIn(syn.hue_channel(B1), entry["reason"])
        self.assertIsNone(decisions[0]["verify"]["within_tolerance"])

    def test_a_correct_reading_of_canon_fields_keeps_working(self):
        for name, entry in (("lit", {"on": True, "level": 0.5, "cct_k": 2700}), ("no on key", {"level": 0.5, "cct_k": 2700})):
            with self.subTest(name):
                _, _, report = self.go(self.decision(entry))
                self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"], report["points"]["unverifiable"]), (4, 0, 0))
        self.assertEqual(self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}), self.frames(b1=0.1))[2]["hit_rate"]["misses"], 2)

    def test_an_off_bulb_is_level_0_with_or_without_a_level(self):
        for entry in ({"on": False}, {"on": False, "level": None, "cct_k": None}, {"on": False, "level": 0.0}, {"on": False, "level": 0.7}, {"on": True, "level": 0, "cct_k": None}):
            with self.subTest(entry=entry):
                frames = self.frames(b1=0.0)
                frames[0] = syn.light_state(T, {p: v.get(B0, 0) * 0.2 for p, v in self.POINTS.items()}, anchored=False, ident="ls-r")
                decisions, _, report = self.go(self.decision(entry), frames)
                self.assertEqual(point(decisions[0], "p1")["status"], "within")
                self.assertEqual(report["points"]["unverifiable"], 1)  # p2: nothing lit and nothing changed
                self.assertNotIn("unknown", point(decisions[0], "p2")["why"])
                self.assertEqual(report["decisions"]["unknown_state"], [])

    def test_a_channel_listed_with_no_read_back_level_is_unknown_after(self):
        before = {"on": True, "level": 0.5, "cct_k": 2700}
        for name, entry in (("no read-back", {"status": "sent", "steps": []}), ("null read-back", {"status": "sent", "readback": None, "steps": []}),
                            ("no level", {"status": "sent", "readback": {"cct_k": 2700}, "steps": []}), ("null level", {"status": "sent", "readback": {"on": True, "level": None, "cct_k": 2700}, "steps": []})):
            with self.subTest(name):
                decisions, _, report = self.go(self.decision(before, entry))
                self.assertEqual(point(decisions[0], "p1")["status"], "unverifiable")
                self.assertIn("after", point(decisions[0], "p1")["why"])
                self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (2, 0))

    def test_a_null_entry_in_applied_scene_is_unknown_too(self):
        dec = self.decision({"on": True, "level": 0.5, "cct_k": 2700})
        dec["applied_scene"][B1] = None
        decisions, _, _ = self.go(dec)
        self.assertEqual(point(decisions[0], "p2")["status"], "unverifiable")

    def test_a_read_back_of_off_is_level_0(self):
        for entry in ({"on": False}, {"on": False, "level": None}, {"level": 0}):
            with self.subTest(entry=entry):
                before = {"on": True, "level": 0.5, "cct_k": 2700}
                decisions, _, _ = self.go(self.decision(before, {"status": "sent", "readback": entry, "steps": send(T + 0.5)}), self.frames(b1=0.0))
                self.assertEqual(point(decisions[0], "p2")["status"], "within")
                self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"within"})

    def test_a_channel_in_the_applied_scene_with_no_entry_before_is_unknown_before(self):
        dec = self.decision({"on": True, "level": 0.5, "cct_k": 2700}, {"status": "sent", "readback": {"level": 0.5, "cct_k": 2700}, "steps": []})
        del dec["inputs"]["actuator_state"][B1]
        decisions, _, _ = self.go(dec)
        self.assertEqual(point(decisions[0], "p2")["status"], "unverifiable")
        self.assertIn("before", point(decisions[0], "p2")["why"])

    def test_under_the_zero_background_only_the_state_after_can_stop_the_level_check(self):
        zero = {"background": "zero"}
        frames = self.frames()
        decisions, _, report = self.go(self.decision(None, {"status": "sent", "readback": {"level": 0.5, "cct_k": 2700}, "steps": []}), frames, zero)
        self.assertEqual(point(decisions[0], "p2")["status"], "within")
        inc = {i["point_id"]: i for i in decisions[0]["verify"]["increments"]}
        self.assertEqual(inc["p2"]["status"], "unverifiable")  # the change needs the state before
        self.assertIsNone(inc["p2"]["predicted_change"])
        self.assertEqual(inc["p0"]["status"], "within")
        decisions, _, _ = self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}, {"status": "sent", "readback": None, "steps": []}), frames, zero)
        self.assertEqual(point(decisions[0], "p2")["status"], "unverifiable")
        self.assertIn("after", point(decisions[0], "p2")["why"])
        # a bulb read nowhere and not listed: nothing says where it is
        decisions, _, _ = self.go(self.decision(None), frames, zero)
        self.assertEqual(point(decisions[0], "p2")["status"], "unverifiable")

    def test_under_the_zero_background_an_unknown_bulb_before_widens_nothing(self):
        # an off reading far off still misses: nothing says the bulb was bright, so no allowance comes of it
        points = {"p": {B1: 100.0}}
        dec = self.decision(None, {"status": "sent", "readback": {"level": 0.0}, "steps": send(T + 0.5)})
        dec["targets"] = {"A": {"surface_id": "p"}}
        del dec["applied_scene"][B0]
        del dec["inputs"]["actuator_state"][B0]
        frames = [syn.light_state(T, {"p": 50.0}, anchored=False, ident="ls-r"), syn.light_state(T + 5, {"p": 50.0}, anchored=False)]
        decisions, _, report = run([dec], frames, points, overrides={"background": "zero"})
        self.assertEqual(point(decisions[0], "p")["status"], "unverifiable")
        self.assertEqual(point(decisions[0], "p")["why"], "prediction not positive")
        self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 0)

    def held_copy(self, pct):
        return {"revision": 4, "held": [{"entity_id": B1, "channels": [B1], "pinned": {"level_pct": pct, "cct_k": 2700, "source": "read-back"}}]}

    def test_a_listed_channel_with_no_read_back_level_takes_the_copys_pin(self):
        # the last checker's mutant: `if rb.get("level") is not None:` to `if True:` kept a bulb with no read-back out of the pin
        for name, entry in (("no read-back", {"status": "held", "steps": []}), ("null read-back", {"status": "held", "readback": None, "steps": []}),
                            ("null level", {"status": "held", "readback": {"level": None, "cct_k": 2700}, "steps": []})):
            with self.subTest(name):
                decisions, states, report = self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}, entry, self.held_copy(30)), self.frames(b1=0.3))
                self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"within"})
                self.assertEqual(report["points"]["unverifiable"], 0)
                self.assertAlmostEqual(residual(states, T + 5, "p2")["predicted"], 30.0, places=6)

    def test_a_listed_channel_with_a_read_back_level_keeps_it_over_the_copys_pin(self):
        entry = {"status": "held", "readback": {"level": 0.5, "cct_k": 2700}, "steps": []}
        _, states, _ = self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}, entry, self.held_copy(30)))
        self.assertAlmostEqual(residual(states, T + 5, "p2")["predicted"], 50.0, places=6)

    def test_a_pin_with_no_level_of_its_own_leaves_a_channel_with_no_read_back_unknown(self):
        entry = {"status": "held", "readback": None, "steps": []}
        decisions, _, _ = self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}, entry, self.held_copy(None)))
        self.assertEqual(point(decisions[0], "p2")["status"], "unverifiable")

    def test_the_copys_null_pin_leaves_a_read_state_as_it_was(self):
        # not in the applied scene, read at 0.5: the copy adds nothing to that
        decisions, _, _ = self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}, None, self.held_copy(None)))
        self.assertEqual(point(decisions[0], "p2")["status"], "within")

    def test_a_pin_of_off_is_level_0(self):
        copy = self.held_copy(None)
        copy["held"][0]["pinned"]["on"] = False
        decisions, _, _ = self.go(self.decision({"on": True, "level": 0.5, "cct_k": 2700}, {"status": "held", "readback": None, "steps": []}, copy), self.frames(b1=0.0))
        self.assertEqual(point(decisions[0], "p1")["status"], "within")

    def test_a_person_setting_a_bulb_to_no_known_level_makes_it_unknown_from_then_on(self):
        correction = {"correction_id": "c", "ts": T + 20, "action": {"settle_s": 0.0}, "snapshot": {"light_state": ["ls-r"]},
                      "hold": {"entity_id": B1, "channels": [B1], "pinned": {"level_pct": None, "source": "read-back"}, "since": T + 20, "ended": None, "hold_set_revision": 5}}
        before = {"on": True, "level": 0.5, "cct_k": 2700}
        dec = self.decision(before, {"status": "sent", "readback": {"level": 0.5, "cct_k": 2700}, "steps": []})
        frames = self.frames() + [syn.light_state(T + 30, {p: 1.0 for p in self.POINTS}, anchored=False)]
        decisions, states, report = self.go(dec, frames, corrections=[correction])
        self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"within"})  # read before the person's change
        s = next(s for s in states if s["ts"] == T + 30)
        self.assertEqual(sorted(e["surface_id"] for e in s["residual"]), ["p0", "p3"])

    def test_a_residual_has_no_entry_where_the_state_is_unknown(self):
        _, states, _ = self.go(self.decision(None))
        self.assertEqual(sorted(e["surface_id"] for e in states[1]["residual"]), ["p0", "p3"])

    def test_a_frame_with_nothing_it_could_write_says_so(self):
        points = {"p2": {B1: 100.0}}
        dec = self.decision(None)
        dec["targets"] = {"A": {"surface_id": "p2"}}
        _, states, report = run([dec], [syn.light_state(T, {"p2": 50.0}, anchored=False, ident="ls-r"), syn.light_state(T + 5, {"p2": 50.0}, anchored=False)], points)
        self.assertEqual(states[1]["residual"], [])
        self.assertIn("d", states[1]["residual_status"])
        self.assertEqual(report["frames"]["residual_written"], 0)

    def test_the_owner_is_told_on_the_console(self):
        out, _ = self.preview([self.decision(None)], self.frames(), model(self.POINTS))
        self.assertIn("1 decision(s) have a bulb in an unknown state: 2 point(s) that depend on it were not judged", out)
        out.encode("ascii")


class AModelErrorStopsOneDecision(Cli):
    """missing-colour-temperature-crashes-run: verify null with a reason for that decision, and the run goes on."""
    DUAL = syn.hue_model({"p0": {B0: 100.0, B1: 50.0}}, kelvins=(2202, 6500))

    def records(self, cct_k=None):
        one = syn.hue_decision("d-1", T + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(T + 0.5)}}, "ls-1", targets={"A": {"surface_id": "p0"}})
        two = syn.hue_decision("d-2", T + 100.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(T + 100.5)}}, "ls-2", targets={"A": {"surface_id": "p0"}})
        for d in (one, two):
            d["inputs"]["actuator_state"][B0]["cct_k"] = 2700
        one["applied_scene"][B0]["readback"]["cct_k"] = cct_k
        v = LightModel(self.DUAL).predict({B0: {"level": 1.0, "cct_k": 2700}})["p0"]
        frames = [syn.light_state(T, {"p0": v * 0.2}, anchored=False, ident="ls-1"), syn.light_state(T + 5, {"p0": v * 0.8}, anchored=False),
                  syn.light_state(T + 100, {"p0": v * 0.2}, anchored=False, ident="ls-2"), syn.light_state(T + 105, {"p0": v * 0.8}, anchored=False)]
        return [one, two], frames

    def assert_goes_on(self, decisions, states, report, *words):
        first = by_id(decisions, "decision_id", "d-1")
        self.assertIsNone(first["verify"])
        entry = not_verified(report, "d-1")
        self.assertEqual(entry["kind"], "model error")
        self.assertEqual(first["verify_status"], entry["reason"])
        for word in words:
            self.assertIn(word, entry["reason"])
        self.assertIs(by_id(decisions, "decision_id", "d-2")["verify"]["within_tolerance"], True)
        self.assertEqual((report["hit_rate"]["hits"], report["decisions"]["verified"]), (1, 1))
        self.assertEqual(states[1]["residual"], [])
        self.assertIn("d-1", states[1]["residual_status"])
        self.assertTrue(states[3]["residual"])

    def test_a_channel_with_no_colour_temperature(self):
        self.assert_goes_on(*run(*self.records(), data=self.DUAL), "needs cct_k")  # predict.py's own words

    def test_a_channel_at_0_kelvin(self):
        self.assert_goes_on(*run(*self.records(0), data=self.DUAL), "needs cct_k")  # predict.py refuses 0 K before any division

    def test_a_channel_the_model_has_no_basis_for(self):
        decisions, frames = self.records(2700)
        decisions[0]["applied_scene"]["light.other"] = {"status": "sent", "readback": {"level": 0.5, "cct_k": 2700}, "steps": []}
        self.assert_goes_on(*run(decisions, frames, data=self.DUAL), "light.other")

    def test_through_the_command_line_with_no_traceback(self):
        decisions, frames = self.records()
        out, report = self.preview(decisions, frames, self.DUAL)
        self.assertIn("decisions verified 1 of 2, 1 not verified", out)
        self.assertIn("1 model error", out)
        self.assertEqual(report["hit_rate"]["hits"], 1)

    def test_every_decision_failing_is_insufficient_not_a_crash(self):
        decisions, frames = self.records()
        _, _, report = run(decisions[:1], frames[:2], data=self.DUAL)
        self.assertEqual((report["decisions"]["verified"], report["hit_rate"]["verdict"]), (0, "insufficient"))


class PersonBeforeDecision(Cli):
    """A person's change between a decision's read frame and the decision is the stale frame's mechanism: actuator_state shows it
    and the frame does not. The read frame's D would take up the difference, and a perfect landing read 0 of 6."""
    names = [f"p{i}" for i in range(6)]
    was = {B0: 0.3, B1: 0.5}
    now = {B0: 0.8, B1: 0.5}
    POINTS = {n: {B0: 300.0, B1: 100.0} for n in names}

    def snap(self, at, levels, ident=None):
        return syn.light_state(at, {n: 300 * levels.get(B0, 0) + 100 * levels.get(B1, 0) for n in self.names}, anchored=False, ident=ident)

    def correction(self, at, settle=0.0, cid="c-1"):
        return {"correction_id": cid, "ts": at, "action": {"settle_s": settle}, "snapshot": {"light_state": ["ls-r"]}, "hold": None}

    def case(self, corrections=(), read_at=T, read_levels=None, landed_at=None, overrides=None):
        """The decision at T + 4.7 reads B0 at 0.8 and asks 0.85; the read frame shows `was`, the person's change coming after it."""
        targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}
        dec = syn.hue_decision("d", T + 4.7, self.now, {B0: {"level": 0.85, "steps": send(T + 4.7)}}, "ls-r", targets=targets)
        frames = [self.snap(read_at, read_levels or self.was, "ls-r"), self.snap(T + 10, landed_at or {**self.now, B0: 0.85})]
        return run([dec], frames, self.POINTS, overrides=overrides, corrections=list(corrections))

    def assert_stale(self, result, cid="c-1"):
        decisions, _, report = result
        self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"unverifiable"})
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 0, "insufficient"))
        [entry] = report["decisions"]["read_frame_stale"]
        self.assertEqual(entry["decision_id"], "d")
        self.assertIn(cid, entry["why"])
        self.assertEqual(not_verified(report)["kind"], "read frame stale")

    def assert_counts(self, result):
        _, _, report = result
        self.assertEqual(report["decisions"]["read_frame_stale"], [])
        self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 6)

    def test_with_no_word_of_the_change_it_reads_0_of_6_on_a_perfect_landing(self):
        # why this rule exists: the record has no word of the change without the Correction
        _, _, report = self.case()
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (0, 6))

    def test_a_person_changing_a_light_after_the_read_frame_makes_it_stale(self):
        self.assert_stale(self.case([self.correction(T + 2)]))

    def test_a_bulb_that_stayed_stuck_does_not_read_as_a_hit_on_that_frame(self):
        self.assert_stale(self.case([self.correction(T + 2)], landed_at=self.was))

    def test_no_reading_changes_it(self):
        for landed_at in (self.was, self.now, {B0: 0.0, B1: 0.0}, {B0: 1.0, B1: 1.0}):
            self.assert_stale(self.case([self.correction(T + 2)], landed_at=landed_at))

    def test_a_correction_with_a_hold_counts_as_much_as_one_without(self):
        hold = {"entity_id": B0, "channels": [B0], "pinned": {"level_pct": 80, "cct_k": 2700}, "since": T + 2, "ended": None, "hold_set_revision": 1}
        self.assert_stale(self.case([{**self.correction(T + 2), "hold": hold}]))

    def test_under_the_zero_background_the_level_check_stands(self):
        zero = {"background": "zero"}
        decisions, states, report = self.case([self.correction(T + 2)], overrides=zero)
        self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"within"})
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (6, 0))
        self.assertEqual({i["status"] for i in decisions[0]["verify"]["increments"]}, {"unverifiable"})
        self.assertEqual([s["decision_id"] for s in report["decisions"]["read_frame_stale"]], ["d"])
        self.assertTrue(states[1]["residual"])
        decisions, _, report = self.case([self.correction(T + 2)], overrides=zero, landed_at=self.was)
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (0, 6))

    def test_a_residual_under_it_has_nothing_and_says_why(self):
        _, states, _ = self.case([self.correction(T + 2)])
        self.assertEqual(states[1]["residual"], [])
        self.assertIn("c-1", states[1]["residual_status"])

    def test_a_correction_after_the_decision_does_not_trigger_it(self):
        for at, settle in ((T + 4.71, 0.0), (T + 6, 0.0), (T + 6, 1.0), (T + 30, 5.0)):
            with self.subTest(at=at - T, settle=settle):
                self.assert_counts(self.case([self.correction(at, settle)], read_levels=self.now))

    def test_a_change_far_before_the_read_frame_does_not_trigger_it(self):
        for at, settle in ((T - 100, 0.0), (T - 100, 5.0), (T - 1.0, 0.0), (T - 1.5, 0.0)):
            with self.subTest(at=at - T, settle=settle):
                self.assert_counts(self.case([self.correction(at, settle)], read_levels=self.now))

    def test_the_change_is_its_window_from_settling_to_its_margin(self):
        # busy from ts - settle_s to ts + settle_margin_s (1 s), as for a verify frame: half open at its end
        self.assert_counts(self.case([self.correction(T - 1.0)], read_levels=self.now))  # ends as the frame is taken
        self.assert_stale(self.case([self.correction(T - 0.999)]))                       # the frame is inside it
        self.assert_stale(self.case([self.correction(T - 0.5, 3.0)]))                    # and inside one that began before it
        self.assert_stale(self.case([self.correction(T + 4.7)]))                         # at the decision
        self.assert_stale(self.case([self.correction(T + 6.0, 1.3)]))                    # begun by the decision: its state may show it
        self.assert_counts(self.case([self.correction(T + 6.0, 1.29)], read_levels=self.now))  # begun after it

    def test_any_correction_in_the_window_is_enough_and_the_first_is_named(self):
        far = self.correction(T - 100, cid="c-far")
        self.assert_stale(self.case([far, self.correction(T + 2)]))

    def test_a_frame_taken_after_the_persons_change_counts_both_ways(self):
        for landed_at, hits in (({**self.now, B0: 0.85}, 6), (self.was, 0)):
            _, _, report = self.case([self.correction(T + 2)], read_at=T + 3, read_levels=self.now, landed_at=landed_at)
            self.assertEqual(report["decisions"]["read_frame_stale"], [])
            self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (hits, 6 - hits))

    def test_the_steps_rule_still_stands_beside_it(self):
        first = syn.hue_decision("d-0", T + 0.2, self.was, {B0: {"level": 0.8, "steps": send(T + 0.2)}}, "ls-r")
        second = syn.hue_decision("d", T + 4.7, self.now, {B0: {"level": 0.85, "steps": send(T + 4.7)}}, "ls-r", targets={"A": {"surface_id": "p0"}})
        _, _, report = run([first, second], [self.snap(T, self.was, "ls-r"), self.snap(T + 10, {**self.now, B0: 0.85})], self.POINTS)
        self.assertIn("d-0", by_id(report["decisions"]["read_frame_stale"], "decision_id", "d")["why"])

    def test_the_owner_is_told_on_the_console(self):
        decisions, frames = [], [self.snap(T, self.was, "ls-r"), self.snap(T + 10, {**self.now, B0: 0.85})]
        dec = syn.hue_decision("d", T + 4.7, self.now, {B0: {"level": 0.85, "steps": send(T + 4.7)}}, "ls-r", targets={"A": {"surface_id": "p0"}})
        write_jsonl(self.tmp / "corrections.jsonl", [self.correction(T + 2)])
        out, report = self.preview([dec], frames, model(self.POINTS), "--corrections", str(self.tmp / "corrections.jsonl"))
        self.assertIn("1 decision(s) read a frame from before a step landed or a person changed a light", out)
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        out.encode("ascii")

    def test_the_note_says_so(self):
        note = " ".join((HERE / "VERIFY-V1.md").read_text("utf-8").split())
        self.assertIn("A person's change whose window starts at or before the decision and ends after the read frame makes that frame stale too", note)


class UnknownCauses(Cli):
    """With no window data or no occupancy file, what would read as drift cannot be told from daylight or a person."""

    def scene(self, windows=None, after=None, did="d", at=T):
        """B0 goes 0.2 to 0.8 and lands 30% short of its change: the increment misses too, the way a drift would."""
        ref = syn.light_state(at, {"p0": 20.0}, anchored=False, ident=f"ls-{did}")
        low = syn.light_state(at + 5, {"p0": after if after is not None else 20.0 + 60 * 0.6}, anchored=False)
        ref["window_daylight"], low["window_daylight"] = ([{"window_id": "w", "level": lv, "unit": "fraction of noon"}] if lv is not None else [] for lv in windows or (None, None))
        dec = syn.hue_decision(did, at + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(at + 0.5)}}, f"ls-{did}", targets={"A": {"surface_id": "p0"}})
        return [dec], [ref, low], model({"p0": {B0: 100.0}})

    def cause(self, windows=None, occupancy=None, after=None):
        decisions, frames, data = self.scene(windows, after)
        out, states, report = run(decisions, frames, data=data, occupancy=occupancy)
        [e] = out[0]["verify"]["explanation"]
        return e, states[1]["residual"][0]["flags"], report

    STEADY = (0.5, 0.5)

    def test_real_drift_evidence_still_reads_drift_when_both_inputs_were_given(self):
        e, flags, report = self.cause(self.STEADY, [])
        self.assertEqual(e["cause"], "drift")
        self.assertIn("drift", flags)
        self.assertNotIn("unknown", e)
        self.assertEqual(report["misses_explained"]["drift"], 1)
        self.assertEqual(report["frames"]["occupied"], 0)

    def test_no_window_data_reads_unexplained_naming_it(self):
        e, flags, report = self.cause((None, None), [])
        self.assertEqual(e["cause"], "unexplained")
        self.assertEqual(e["unknown"], ["window daylight"])
        self.assertIn("window daylight", e["basis"])
        self.assertIn("unexplained", flags)
        self.assertNotIn("drift", flags)
        self.assertEqual((report["misses_explained"]["drift"], report["misses_explained"]["unexplained"]), (0, 1))
        self.assertEqual(report["misses_explained"]["unknown_inputs"], {"window daylight": 1, "occupancy": 0})

    def test_window_data_on_one_frame_only_is_no_comparison(self):
        for windows in ((0.5, None), (None, 0.5)):
            self.assertEqual(self.cause(windows, [])[0]["unknown"], ["window daylight"])

    def test_a_window_level_of_null_is_no_data(self):
        decisions, frames, data = self.scene(self.STEADY)
        frames[1]["window_daylight"][0]["level"] = None
        out, _, _ = run(decisions, frames, data=data, occupancy=[])
        self.assertEqual(out[0]["verify"]["explanation"][0]["unknown"], ["window daylight"])

    def test_no_occupancy_file_reads_unexplained_naming_it(self):
        e, flags, report = self.cause(self.STEADY, None)
        self.assertEqual(e["cause"], "unexplained")
        self.assertEqual(e["unknown"], ["occupancy"])
        self.assertIn("occupancy", e["basis"])
        self.assertNotIn("drift", flags)
        self.assertEqual(report["misses_explained"]["unknown_inputs"], {"window daylight": 0, "occupancy": 1})

    def test_neither_names_both(self):
        e, _, report = self.cause((None, None), None)
        self.assertEqual((e["cause"], e["unknown"]), ("unexplained", ["window daylight", "occupancy"]))
        self.assertEqual(report["misses_explained"]["share_explained"], 0.0)

    def test_an_empty_occupancy_file_is_nobody_there_and_not_unknown(self):
        e, _, report = self.cause(self.STEADY, [])
        self.assertEqual(e["cause"], "drift")
        self.assertEqual(report["frames"]["occupied"], 0)
        self.assertNotIn("occupancy_status", report["frames"])

    def test_a_miss_with_real_window_data_still_reads_daylight_whatever_else_is_unknown(self):
        for occupancy in (None, []):
            e, flags, _ = self.cause((0.2, 0.6), occupancy, after=20.0 + 60 * 1.5)
            self.assertEqual(e["cause"], "daylight", occupancy)
            self.assertIn("daylight", flags)
            self.assertNotIn("unknown", e)

    def test_a_shadow_needs_an_occupancy_file_and_with_one_still_reads(self):
        people = [{"start": T + 4, "end": T + 6, "people": [{"track_id": "A", "xy": [0.0, 0.0]}]}]
        self.assertEqual(self.cause(self.STEADY, people)[0]["cause"], "shadow")
        self.assertEqual(self.cause(self.STEADY, None)[0]["cause"], "unexplained")

    def test_an_occupied_frame_is_not_drift_and_says_nothing_of_occupancy_being_unknown(self):
        people = [{"start": T + 4, "end": T + 6, "people": []}]
        e, flags, report = self.cause(self.STEADY, people)
        self.assertEqual((e["cause"], e.get("unknown")), ("unexplained", None))
        self.assertIn("occupied", flags)
        self.assertEqual(report["frames"]["occupied"], 1)

    def test_a_miss_that_would_not_read_as_drift_names_what_is_unknown_too(self):
        # the increment is within, since the read frame sits far above: only the level misses
        decisions, frames, data = self.scene((None, None), after=30.0)
        out, _, _ = run(decisions, frames, data=data, occupancy=None)
        [e] = out[0]["verify"]["explanation"]
        self.assertEqual((e["cause"], e["unknown"]), ("unexplained", ["window daylight", "occupancy"]))

    def test_the_old_words_stand_where_nothing_is_unknown(self):
        decisions, frames, data = self.scene(self.STEADY, after=20.0 + 60 * 1.5)
        out, _, _ = run(decisions, frames, data=data, occupancy=[{"start": T + 4, "end": T + 6, "people": []}])
        [e] = out[0]["verify"]["explanation"]
        self.assertEqual(e, {"point_id": "p0", "cause": "unexplained", "basis": "no daylight change, shadow or drift"})

    def test_it_changes_no_hit_or_miss(self):
        seen = set()
        for windows in ((None, None), self.STEADY, (0.5, None)):
            for occupancy in (None, [], [{"start": T + 4, "end": T + 6, "people": []}]):
                for after in (20.0 + 60 * 0.6, 80.0, 20.0 + 60 * 1.5):
                    decisions, frames, data = self.scene(windows, after)
                    hr = run(decisions, frames, data=data, occupancy=occupancy)[2]["hit_rate"]
                    seen.add((after, hr["hits"], hr["misses"], hr["value"], hr["verdict"]))
        self.assertEqual(len(seen), 3, seen)

    def test_report_frames_says_occupancy_is_unknown_rather_than_0(self):
        decisions, frames, data = self.scene(self.STEADY)
        _, _, report = run(decisions, frames, data=data, occupancy=None)
        self.assertEqual(report["frames"]["occupied"], "unknown")
        self.assertIn("--occupancy", report["frames"]["occupancy_status"])
        self.assertEqual(report["rows"][0]["occupied"], "unknown")

    def test_through_the_command_line(self):
        decisions, frames, data = self.scene((None, None))
        out, report = self.preview(decisions, frames, data)
        self.assertEqual(report["frames"]["occupied"], "unknown")
        self.assertEqual(report["misses_explained"]["unexplained"], 1)
        self.assertIn("1 miss(es) can't be told from drift or the rest: unknown window daylight 1, occupancy 1", out)
        empty = self.tmp / "occupancy.jsonl"
        empty.write_text("", encoding="utf-8")
        _, report = self.preview(decisions, frames, data, "--occupancy", str(empty))
        self.assertEqual(report["frames"]["occupied"], 0)
        self.assertEqual(report["misses_explained"]["unknown_inputs"]["occupancy"], 0)

    def test_the_note_says_so(self):
        note = " ".join((HERE / "VERIFY-V1.md").read_text("utf-8").split())
        self.assertIn("With no window data on the two frames, or no occupancy file, nothing can rule out what they would have shown", note)


class SurfacesNotLogged(Cli):
    def test_the_report_and_console_count_them(self):
        one, two, three = landed("d-1"), landed("d-2", at=T + 100), landed("d-3", at=T + 200)
        one["predicted"]["surfaces_not_logged"] = 5
        two["predicted"]["surfaces_not_logged"] = 3
        frames = [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0), frame(T + 105, 80.0), frame(T + 205, 80.0)]
        decisions = [one, two, three]
        self.assertEqual(run(decisions, frames)[2]["surfaces_not_logged"], {"surfaces": 8, "decisions": 2})
        out, report = self.preview(decisions, frames)
        self.assertEqual(report["surfaces_not_logged"], {"surfaces": 8, "decisions": 2})
        self.assertIn("8 surface(s) across 2 decision(s) were not logged, so not verified", out)
        out.encode("ascii")

    def test_with_none_there_is_no_line_and_the_count_is_0(self):
        out, report = self.preview([landed()], [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
        self.assertEqual(report["surfaces_not_logged"], {"surfaces": 0, "decisions": 0})
        self.assertNotIn("not logged", out)

    def test_a_count_of_0_is_no_decision(self):
        dec = landed()
        dec["predicted"]["surfaces_not_logged"] = 0
        self.assertEqual(run([dec], [])[2]["surfaces_not_logged"], {"surfaces": 0, "decisions": 0})

    def test_a_decision_that_could_not_be_verified_is_counted_too(self):
        dec = landed()
        dec["predicted"]["surfaces_not_logged"] = 4
        self.assertEqual(run([dec], [])[2]["surfaces_not_logged"], {"surfaces": 4, "decisions": 1})

    def test_it_moves_no_hit_or_miss(self):
        dec = landed()
        frames = [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)]
        before = run([dec], frames)[2]["hit_rate"]
        dec["predicted"]["surfaces_not_logged"] = 4
        self.assertEqual(run([dec], frames)[2]["hit_rate"], before)


class TwoEvenings(unittest.TestCase):
    """Frames are keyed by ts. Two may share a derived ls-HHMMSS id on different days, and each decision reads its own."""
    DAY = 86400.0

    def evening(self, at, ambient, did):
        """Frames with no id at the same clock times each day, and a decision naming its read frame by the derived id."""
        ref = frame(at, 20.0 + ambient)
        dec = landed(did, at=at, read="ls-190329")
        return [dec], [ref, frame(at + 5, 80.0 + ambient)]

    def two(self, ambient=(0.0, 300.0)):
        a, b = self.evening(T, ambient[0], "d-1"), self.evening(T + self.DAY, ambient[1], "d-2")
        return a[0] + b[0], a[1] + b[1]

    def test_no_crash_each_reads_its_own_days_frame_and_the_interval_comes(self):
        decisions, frames = self.two()
        out, _, report = run(decisions, frames)
        for d in out:
            self.assertEqual(point(d, "p0")["status"], "within", d["decision_id"])  # the other day's frame reads 300 away
        self.assertEqual(by_id(out, "decision_id", "d-1")["verify"]["ts"], T + 5)
        self.assertEqual(by_id(out, "decision_id", "d-2")["verify"]["ts"], T + self.DAY + 5)
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["sessions"], hr["days_with_nothing_counted"]), (4, 0, 2, 0))
        self.assertEqual(hr["interval"], [1.0, 1.0])
        self.assertIn("session-bootstrap", hr["interval_status"])
        self.assertEqual(report["decisions"]["verified"], 2)

    def test_the_interval_reads_the_days_apart(self):
        a, b = self.evening(T, 0.0, "d-1"), self.evening(T + self.DAY, 0.0, "d-2")
        b[1][1] = frame(T + self.DAY + 5, 20.0)  # stuck on the second evening
        hr = run(a[0] + b[0], a[1] + b[1])[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["sessions"]), (2, 2, 2))
        self.assertEqual(hr["interval"], [0.0, 1.0])

    def test_two_frames_sharing_a_derived_id_are_not_an_error_nor_one_ids_own_id_twice(self):
        run([], [frame(T, 1.0), frame(T + self.DAY, 1.0)])
        run([], [frame(T, 1.0, "same"), frame(T + 100, 1.0, "same")])

    def read(self, inputs, frames):
        """The decision's own read frame by the value it reads: frames hold 20 at p0 if chosen right."""
        dec = landed(read=None)
        dec["inputs"].update(inputs)
        decisions, _, report = run([dec], frames + [frame(T + 5, 80.0)])
        return point(decisions[0], "p0"), report

    def test_the_ts_names_the_frame_whatever_the_id_says(self):
        p, _ = self.read({"light_state_ts": T, "light_state": "ls-other"}, [frame(T, 20.0), frame(T - 2, 70.0, "ls-other")])
        self.assertEqual(p["status"], "within")

    def test_within_half_a_millisecond_of_the_ts(self):
        p, _ = self.read({"light_state_ts": T + 0.0004}, [frame(T, 20.0)])
        self.assertEqual(p["status"], "within")
        p, report = self.read({"light_state_ts": T + 0.0006}, [frame(T, 20.0)])
        self.assertEqual((p["status"], report["decisions"]["verified"]), ("unverifiable", 0))
        self.assertIn("no read frame", not_verified(report)["reason"])

    def test_a_ts_that_names_no_frame_does_not_fall_back_on_the_id(self):
        p, report = self.read({"light_state_ts": T - 1.0, "light_state": "ls-r"}, [frame(T, 20.0, "ls-r")])
        self.assertEqual(p["status"], "unverifiable")
        self.assertIn(repr(T - 1.0), not_verified(report)["reason"])

    def test_a_reference_by_id_more_than_12_hours_away_is_not_taken(self):
        decision_at = T + 0.5
        for away, status in ((12 * 3600 + 1, "unverifiable"), (-12 * 3600 - 1, "unverifiable"), (12 * 3600, "within"), (-12 * 3600, "within")):
            at = decision_at + away
            p, report = self.read({"light_state": "ls-r"}, [frame(at, 20.0, "ls-r")])
            self.assertEqual(p["status"], status, away)
            if status == "unverifiable":
                self.assertIn("12 hours", not_verified(report)["reason"])

    def test_the_nearest_of_two_frames_of_one_id(self):
        p, _ = self.read({"light_state": "ls-r"}, [frame(T - 5, 70.0, "ls-r"), frame(T - 1, 20.0, "ls-r"), frame(T + 3, 70.0, "ls-r")])
        self.assertEqual(p["status"], "within")

    def test_den_cores_derived_id_reads_with_no_ts_too(self):
        p, _ = self.read({"light_state": "ls-%.3f" % T}, [frame(T, 20.0)])
        self.assertEqual(p["status"], "within")

    def test_den_verifys_own_derived_id_still_reads(self):
        p, _ = self.read({"light_state": "ls-190329"}, [frame(T, 20.0)])
        self.assertEqual(p["status"], "within")

    def dated_by_clock(self, did, at, **inputs):
        dec = landed(did, at=at, read="ls-190329")
        dec["ts"] = "19:03:29.5"
        dec["applied_scene"][B0]["steps"][0]["sent_at"] = "19:03:30"
        dec["inputs"]["person_state"] = []
        dec["inputs"].update(inputs)
        return dec

    def test_a_clock_time_is_dated_by_a_number_in_its_own_record(self):
        decisions = [self.dated_by_clock("d-1", T, person_state=[f"A@{T + 1}"]),
                     self.dated_by_clock("d-2", T + self.DAY, light_state_ts=T + self.DAY)]
        _, frames = self.two()
        out, _, report = run(decisions, frames)
        self.assertEqual(by_id(out, "decision_id", "d-2")["verify"]["ts"], T + self.DAY + 5)
        self.assertEqual(by_id(out, "decision_id", "d-1")["verify"]["ts"], T + 5)
        self.assertEqual(report["hit_rate"]["hits"], 4)

    def test_a_clock_time_with_only_an_id_two_days_share_cannot_be_dated_and_is_refused(self):
        _, frames = self.two()
        with self.assertRaisesRegex(BadFile, "d-1.*no date"):
            run([self.dated_by_clock("d-1", T)], frames)

    def test_a_clock_time_with_an_id_only_one_frame_has_takes_that_frames_day(self):
        _, frames = self.two()
        frames[2]["id"] = "ls-day2"
        dec = self.dated_by_clock("d-2", T + self.DAY, light_state="ls-day2")
        out, _, _ = run([dec], frames)
        self.assertEqual(out[0]["verify"]["ts"], T + self.DAY + 5)

    def test_a_clock_time_with_frames_of_one_evening_still_takes_that_evening(self):
        dec = self.dated_by_clock("d-1", T, light_state=None)
        out, _, _ = run([dec], self.evening(T, 0.0, "x")[1], overrides={"background": "zero"})
        self.assertEqual(out[0]["verify"]["ts"], T + 5)

    def test_a_corrections_clock_time_is_dated_the_same_way_on_its_own_evening(self):
        decisions, frames = self.two()
        c = {"correction_id": "c", "ts": "19:03:40", "action": {"settle_s": 1.0}, "snapshot": {"light_state": ["ls-190329"], "person_state": [f"A@{T + self.DAY}"]},
             "hold": {"entity_id": B3, "channels": [B3], "pinned": {"level_pct": 50, "cct_k": 2700}, "since": "19:03:40", "hold_set_revision": 9, "ended": None}}
        at = T + self.DAY + 11  # 19:03:40 on the second evening: T is 19:03:29
        _, states, _ = run(decisions, frames + [frame(at, 80.0), frame(T + 11, 80.0)], corrections=[c])
        self.assertEqual(next(s for s in states if s["ts"] == at)["residual_status"], "not a verify frame: mid_change")
        self.assertNotIn("residual_status", next(s for s in states if s["ts"] == T + 11))  # the first evening has no such change
        for undated in ({"snapshot": {"light_state": ["ls-190329"]}}, {"snapshot": {}}):
            with self.assertRaisesRegex(BadFile, "correction c.*no date"):
                run(decisions, frames, corrections=[{**c, **undated}])

    @unittest.skipUnless(shutil.which("powershell.exe"), "no powershell.exe here")
    def test_the_join_the_readme_gives_reads_as_one_run_with_its_interval(self):
        a, b = self.evening(T, 0.0, "d-1"), self.evening(T + self.DAY, 300.0, "d-2")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, rows in (("d1.jsonl", a[0]), ("d2.jsonl", b[0]), ("f1.jsonl", a[1]), ("f2.jsonl", b[1])):
                write_jsonl(tmp / name, rows)
            (tmp / "m.json").write_text(json.dumps(model()), encoding="utf-8")
            script = tmp / "join.ps1"
            script.write_text(f"Set-Location -LiteralPath '{tmp}'\r\n"
                              "Get-Content d1.jsonl, d2.jsonl | Set-Content -Encoding utf8 decisions.jsonl\r\n"
                              "Get-Content f1.jsonl, f2.jsonl | Set-Content -Encoding utf8 frames.jsonl\r\n", encoding="utf-8")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)], check=True, timeout=120, capture_output=True)
            with Quiet():
                self.assertEqual(cli.main(["run", "--decisions", str(tmp / "decisions.jsonl"), "--light-states", str(tmp / "frames.jsonl"), "--model", str(tmp / "m.json"),
                                           "--utc-offset-h", "-7", "--out", str(tmp / "o"), "--preview"]), 0)
            report = json.loads((tmp / "o" / "report-preview.json").read_text("utf-8"))
        self.assertEqual(report["hit_rate"]["sessions"], 2)
        self.assertEqual(report["hit_rate"]["interval"], [1.0, 1.0])
        self.assertIn("Get-Content C:\\den-verify\\s1\\decisions.jsonl, C:\\den-verify\\s2\\decisions.jsonl | Set-Content -Encoding utf8 C:\\den-verify\\decisions.jsonl",
                      (HERE / "README.md").read_text("utf-8"))

    def test_the_first_frame_of_the_next_evening_is_not_a_verify_frame_for_last_night(self):
        dec = landed("d-1")
        out, _, report = run([dec], [frame(T, 20.0, "ls-r"), frame(T + self.DAY, 80.0)])
        self.assertIsNone(out[0]["verify"])
        self.assertEqual(not_verified(report, "d-1")["kind"], "no frame after settling")

    def test_a_verify_frame_12_hours_on_is_still_its_own(self):
        out, _, _ = run([landed("d-1")], [frame(T, 20.0, "ls-r"), frame(T + 3.5 + 12 * 3600, 80.0)])
        self.assertEqual(out[0]["verify"]["ts"], T + 3.5 + 12 * 3600)

    def test_last_nights_decision_is_not_in_force_for_the_next_evenings_first_frame(self):
        decisions, frames = self.two()
        _, states, _ = run(decisions, frames)
        first = next(s for s in states if s["ts"] == T + self.DAY)
        self.assertEqual(first["residual"], [])
        self.assertEqual(first["residual_status"], "no decision in force")
        self.assertTrue(next(s for s in states if s["ts"] == T + 5)["residual"])

    def test_the_readme_says_one_run_gets_the_interval(self):
        said = " ".join((HERE / "README.md").read_text("utf-8").split())
        self.assertIn("Several evenings in one run give the session-bootstrap interval", said)
        self.assertIn("Get-Content", said.split("Several evenings in one run")[1][:600])
        self.assertNotIn(r"C:\den-verify\s1\decisions.jsonl --light-states C:\den-verify\s1", said)


class TheWordInTheMethod(unittest.TestCase):
    AT_START = "b68d7cb647cfc7aef803544771c5e84783936837abc9bfe1fb3e14a1eb065376"  # the verify-v1 block at c188f7fe, before this round

    def test_no_rule_or_number_moved(self):
        m = method.load()
        self.assertEqual(m.block_sha256, self.AT_START)

    def test_the_report_gives_both_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_jsonl(tmp / "d.jsonl", [landed()])
            write_jsonl(tmp / "f.jsonl", [frame(T, 20.0, "ls-r"), frame(T + 5, 80.0)])
            (tmp / "m.json").write_text(json.dumps(model()), encoding="utf-8")
            with Quiet():
                self.assertEqual(cli.main(["run", "--decisions", str(tmp / "d.jsonl"), "--light-states", str(tmp / "f.jsonl"), "--model", str(tmp / "m.json"),
                                           "--out", str(tmp / "o"), "--preview"]), 0)
            report = json.loads((tmp / "o" / "report-preview.json").read_text("utf-8"))
        m = method.load()
        self.assertEqual(report["method"]["sha256"], m.sha256)
        self.assertEqual(report["method"]["block_sha256"], self.AT_START)
        self.assertEqual(m.sha256, hashlib.sha256(method.lf_text(HERE / "VERIFY-V1.md").encode("utf-8")).hexdigest())

    def test_a_changed_number_changes_the_block_hash_and_a_changed_sentence_does_not(self):
        text = method.lf_text(HERE / "VERIFY-V1.md")
        base = method.parse(text)
        self.assertEqual(method.parse("A new sentence.\n\n" + text).block_sha256, base.block_sha256)
        self.assertNotEqual(method.parse("A new sentence.\n\n" + text).sha256, base.sha256)
        self.assertNotEqual(method.parse(text.replace("tolerance = 0.15 ", "tolerance = 0.16 ")).block_sha256, base.block_sha256)

    def test_the_note_and_the_readme_define_the_word(self):
        note = " ".join((HERE / "VERIFY-V1.md").read_text("utf-8").split())
        self.assertIn("A decision counts as **verified** when something was judged", note)
        for words in ("no read frame", "no frame after settling", "no surface map", "no `applied_scene`", "unknown state"):
            self.assertIn(words, note)
        readme = " ".join((HERE / "README.md").read_text("utf-8").split())
        self.assertIn("A decision counts as verified only when something was judged", readme)


if __name__ == "__main__":
    unittest.main()

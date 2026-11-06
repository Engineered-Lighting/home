"""The rules the lighting seam fixed: what "verified" means, unknown state, a person's change, two evenings, unknown causes.

On den-verify's own synthetic shape, so they hold with or without den-light. test_light_model.py has Stage 1's shape and
test_seam_den_core.py the real tools. Everything here is synthetic.

    python -B -m unittest discover -s tools/den-verify -p "test_*.py"
"""
import hashlib
import json
import shutil
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
from records import Clock, write_jsonl  # noqa: E402
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

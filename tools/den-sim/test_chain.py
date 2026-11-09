"""Test 6 (detector label) and test 7's hop 6 controls on chain.py's own functions (plan, "Done means"):

- rows_hold: recorded rows read ADAPTER only under hop 6's conditions. An edited, empty or truncated rows
  file, an empty usable part, a frame over 1.0 s, a lost weight pin, a count, a name that isn't a model, and
  rows never checked against the model each read MISSING with why, never nobody;
- weight_tools: the weights are read from detector-run.json whenever it parses, so rows that fail another
  condition never read as changed weights;
- no_track: a person box with no track never reads nobody;
- torso: said relative to the limit only;
- person_to_aim takes the newest entry with both shoulders;
- the click path calls no fake_detector.

Each has a control that must make it fail. The parts on real rows need DEN_SIM_SET=<a prepared set>, and only
read it: they work on hard-linked copies whose changed files are replaced, never written through.

    python -B -X utf8 tools\\den-sim\\test_chain.py
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chain  # noqa: E402  installs chain.py's socket refusal in this process too

SET = Path(os.environ["DEN_SIM_SET"]).resolve() if os.environ.get("DEN_SIM_SET") else None
NEEDS_SET = unittest.skipUnless(SET and (SET / "display.json").exists(), "needs DEN_SIM_SET=<a prepared set>")
PINS = chain.detect.lock()["weights"]


def linked_copy(source, target):
    for path in source.rglob("*"):
        dest = target / path.relative_to(source)
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, dest)


def replace(path, text):
    path.unlink()
    path.write_text(text, encoding="utf-8", newline="\n")


@NEEDS_SET
class RowsHold(unittest.TestCase):
    def setUp(self):
        display = json.loads((SET / "display.json").read_text(encoding="utf-8"))["scenes"]
        self.tmp = tempfile.TemporaryDirectory(prefix="den-sim-rows-")
        self.take = Path(self.tmp.name) / "take"
        linked_copy(SET / next(d["take"] for d in display if d["label"] == "Reading on the couch"), self.take)
        self.hop = json.loads((self.take / "prepare-receipt.json").read_text(encoding="utf-8"))["hops"]["6"]
        check = next(self.take.glob("check-*.json"))
        self.rows = chain.usable_rows(self.take, json.loads(check.read_text(encoding="utf-8")))
        self.lines = (self.take / "detections.jsonl").read_text(encoding="utf-8").splitlines()

    def tearDown(self):
        self.tmp.cleanup()

    def why(self, rows=None, hop=None):
        held, why = chain.rows_hold(self.take, self.rows if rows is None else rows, self.hop if hop is None else hop, PINS)
        self.assertEqual(held is None, why is not None)
        self.assertNotIn("nobody", why or "")
        return why

    def recorded(self):
        """As if prepare had recorded the files as they are now: the digest conditions pass, the rest must hold."""
        hop = json.loads(json.dumps(self.hop))
        for key, name in (("rows", "detections.jsonl"), ("run", "detector-run.json")):
            hop[key]["sha256"] = chain.sha256_file(self.take / name)
        return hop

    def edit_run(self, change):
        run = json.loads((self.take / "detector-run.json").read_text(encoding="utf-8"))
        change(run)
        replace(self.take / "detector-run.json", json.dumps(run))

    def test_the_recorded_rows_hold(self):
        held, why = chain.rows_hold(self.take, self.rows, self.hop, PINS)
        self.assertIsNone(why)
        self.assertEqual(len(held["rows"]), len(self.rows))
        self.assertGreater(held["with_person"], 0)
        self.assertEqual(held["names"], ["rtmpose-m", "yolo26s"])

    def test_the_weights_are_read_whenever_detector_run_json_parses(self):
        """Rows that fail another condition never read as changed weights (condition 4): only a real change does."""
        replace(self.take / "detections.jsonl", "\n".join([self.lines[0] + " ", *self.lines[1:]]) + "\n")
        self.assertEqual(self.why(), "detections.jsonl is not the one prepare recorded")
        weights = chain.weight_tools(self.take, PINS)
        self.assertEqual([w["path"] for w in weights], ["weights yolo26s.pt", "weights rtmpose-m-body7.onnx", "weights artifact.json"])
        self.assertTrue(all(w["actual"] == w["expected"] for w in weights), weights)
        self.edit_run(lambda run: run["counts"].update(over_limit=1))
        self.assertTrue(all(w["actual"] == w["expected"] for w in chain.weight_tools(self.take, PINS)), "an over-limit count is no weight change")
        self.edit_run(lambda run: run["weights"].update({"rtmpose-m-body7.onnx": "0" * 64}))
        changed = [w["path"] for w in chain.weight_tools(self.take, PINS) if w["actual"] != w["expected"]]
        self.assertEqual(changed, ["weights rtmpose-m-body7.onnx"], "control: a changed digest is told")
        replace(self.take / "detector-run.json", "{not json")
        self.assertEqual(chain.weight_tools(self.take, PINS), [], "a file that can't be read gives none: hop 6 is MISSING for it already")

    def test_an_edited_empty_or_truncated_rows_file_reads_missing(self):
        replace(self.take / "detections.jsonl", "\n".join([self.lines[0] + " ", *self.lines[1:]]) + "\n")
        self.assertEqual(self.why(), "detections.jsonl is not the one prepare recorded")
        replace(self.take / "detections.jsonl", "")
        self.assertEqual(self.why(), "detections.jsonl is not the one prepare recorded")
        self.assertEqual(self.why(hop=self.recorded()), "detections.jsonl holds no rows")
        replace(self.take / "detections.jsonl", "\n".join(self.lines[:-1]) + "\n")
        self.assertEqual(self.why(), "detections.jsonl is not the one prepare recorded")
        self.assertEqual(self.why(hop=self.recorded()), "the rows cover %d of the %d usable frames" % (len(self.rows) - 1, len(self.rows)))
        replace(self.take / "detections.jsonl", "\n".join(self.lines)[:-40] + "\n")
        self.assertEqual(self.why(hop=self.recorded()), "detections.jsonl or detector-run.json is not what detect.py writes")

    def test_an_empty_usable_part_reads_missing(self):
        self.assertEqual(self.why(rows=[]), "the check's usable part is empty, so no rows can cover it")

    def test_a_frame_over_one_second_of_compute_reads_missing(self):
        late = [json.loads(line) for line in self.lines]
        late[5]["available_ts"] = late[5]["frame"]["capture_ts"] + 1.2
        replace(self.take / "detections.jsonl", "\n".join(json.dumps(row) for row in late) + "\n")
        self.assertRegex(self.why(hop=self.recorded()), r"^1 frames took over 1 s of compute")
        replace(self.take / "detections.jsonl", "\n".join(self.lines) + "\n")
        self.edit_run(lambda run: run["counts"].update(over_limit=1))
        self.assertRegex(self.why(hop=self.recorded()), r"^1 frames took over 1 s of compute")

    def test_a_lost_pin_a_count_and_a_name_that_is_no_model_read_missing(self):
        self.edit_run(lambda run: run["weights"].update({"yolo26s.pt": "0" * 64}))
        self.assertEqual(self.why(), "detector-run.json is not the one prepare recorded")
        self.assertEqual(self.why(hop=self.recorded()), "detector-run.json does not hold both pinned weight digests")
        self.edit_run(lambda run: run["weights"].update({"yolo26s.pt": PINS["yolo26s.pt"]}) or run["counts"].update(with_person=run["counts"]["with_person"] - 1))
        self.assertEqual(self.why(hop=self.recorded()), "the rows' counts are not detector-run.json's")
        self.edit_run(lambda run: run["counts"].update(with_person=run["counts"]["with_person"] + 1))
        replace(self.take / "detections.jsonl", "\n".join(self.lines).replace('"boxes":"yolo26s"', '"boxes":"fake-scripted-1"') + "\n")
        self.assertEqual(self.why(hop=self.recorded()), "the rows name fake-scripted-1, not a real model")

    def test_rows_never_checked_against_the_model_read_missing(self):
        self.assertEqual(self.why(hop={**self.hop, "checked": None}), "prepare did not check these rows against the model")
        self.assertEqual(self.why(hop={}), "prepare recorded no detector rows for this take")


class NoTrack(unittest.TestCase):
    def test_a_person_box_with_no_track_never_reads_nobody(self):
        run = {"gate": {"counts": {"validity": {"rejected:no_room_position:depth_absent": 12}}}}
        said = chain.no_track(12, 82, run, "UNSCORED: 12 open slots\nleft out: 0 not a person, 12 rejected, 0 with no position\n")
        self.assertEqual(said, "the detector saw a person on 12 of 82 usable frames, and no track formed: observations "
                               "rejected:no_room_position:depth_absent 12. left out: 0 not a person, 12 rejected, 0 with no position")
        never = re.compile(r"\bnobody\b|saw no one")
        self.assertIsNone(never.search(said))
        self.assertIsNotNone(never.search("the detector saw nobody, nothing was asked"), "control: the nobody sentence is caught")


def joint(j, z, status="supported"):
    return {"j": j, "xyz": [1.0, 2.0, z], "status": status}


class Torso(unittest.TestCase):
    def observations(self, heights, at=100.0):
        return [{"kind": "keypoints", "validity": "valid", "capture_ts": at + i * 0.25,
                 "joints_room": [joint(5, heights[0]), joint(6, heights[1]), joint(11, heights[2]), joint(12, heights[3]), joint(0, 1.5)]}
                for i in range(9)]

    def test_said_relative_to_the_limit_only(self):
        line = chain.torso(self.observations([0.92, 0.90, 0.76, 0.74]), seat_top_m=0.45)
        self.assertEqual(line["margin_m"], -0.07)
        self.assertTrue(line["line"].startswith("torso 0.07 m under the limit (median of 36 supported"), line["line"])
        absolute = re.compile(r"(?<![0-9])(0\.83|0\.90|0\.45)(?![0-9])")
        self.assertIsNone(absolute.search(line["line"].replace("0.45 m)", "")), "no absolute height, seat or limit")
        self.assertIsNotNone(absolute.search("torso at 0.83 m"), "control: an absolute height is caught")
        self.assertTrue(chain.torso(self.observations([1.4, 1.4, 1.0, 1.0]), seat_top_m=0.45)["line"].startswith("torso 0.30 m over the limit"))
        uncertain = self.observations([0.8] * 4)
        for obs in uncertain:
            for entry in obs["joints_room"]:
                entry["status"] = "uncertain"
        self.assertIsNone(chain.torso(uncertain, 0.45)["margin_m"], "only supported joints count")
        self.assertIsNone(chain.torso([], 0.45))


class PersonToAim(unittest.TestCase):
    def test_the_newest_entry_with_both_shoulders(self):
        both = {"ts": 10.0, "joints_room": [{"j": 5, "xyz": [1, 1, 1]}, {"j": 6, "xyz": [2, 1, 1]}]}
        one = {"ts": 11.0, "joints_room": [{"j": 5, "xyz": [1, 1, 1]}, {"j": 6, "xyz": None}]}
        person = {"state": "engaged", "kind": "reading", "support": "observed"}
        got, problem = chain.person_to_aim([{"track_id": 1, "keypoint_series": [both, one]}], person)
        self.assertIsNone(problem)
        self.assertEqual(got["from_bundle_at"], 10.0, "the newest entry lost a shoulder, so the one before it is used")
        self.assertEqual(max([both, one], key=lambda e: e["ts"])["ts"], 11.0, "control: the newest entry alone is the one without")
        self.assertEqual(chain.person_to_aim([{"track_id": 1, "keypoint_series": [one]}], person)[1], "no bundle entry has both shoulders in the room frame")


class NoFake(unittest.TestCase):
    def test_the_click_path_calls_no_fake_detector(self):
        code = {path.relative_to(HERE).as_posix(): path.read_text(encoding="utf-8") for path in sorted(HERE.rglob("*"))
                if path.suffix in (".py", ".mjs", ".js", ".json") and not path.name.startswith("test")}
        self.assertTrue({"chain.py", "brain.mjs", "run.mjs", "scene/prepare.py"} <= set(code))
        calls = re.compile(r"fake_detector|--fake\b")
        self.assertEqual([name for name, text in code.items() if calls.search(text)], [])
        self.assertTrue(calls.search("import fake_detector\n"), "control: a planted import is found")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Test 6 (detector label) and test 7's hop 6 controls on chain.py's own functions (plan, "Done means"):

- rows_hold: recorded rows read ADAPTER only under hop 6's conditions. An edited, empty or truncated rows
  file, an empty usable part, rows of other frames, a frame over 1.0 s, a lost weight pin, a count, a name
  that isn't a model, rows recorded for another take or check, and rows never checked against the model (or
  checked and failed) each read MISSING with why, never nobody;
- found_line: the joints RTMPose-m placed are counted, and a person with none reads box only;
- weight_tools: the weights are read from detector-run.json whenever it parses, so rows that fail another
  condition never read as changed weights;
- no_track: a person box with no track never reads nobody, and only person observations are counted;
- person_words: hop 15's row says a distance, which half of the couch test failed, the spot and the engagement;
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
        self.digests = dict(take_digest=json.loads((self.take / "prepare-receipt.json").read_text(encoding="utf-8"))["take_digest"],
                            check_sha256=chain.sha256_file(check))

    def tearDown(self):
        self.tmp.cleanup()

    def why(self, rows=None, hop=None, **digests):
        held, why = chain.rows_hold(self.take, self.rows if rows is None else rows, self.hop if hop is None else hop, PINS, **{**self.digests, **digests})
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
        held, why = chain.rows_hold(self.take, self.rows, self.hop, PINS, **self.digests)
        self.assertIsNone(why)
        self.assertEqual(len(held["rows"]), len(self.rows))
        self.assertGreater(held["with_person"], 0)
        self.assertEqual(held["names"], ["rtmpose-m", "yolo26s"])
        self.assertEqual(chain.found_line(held), "YOLO26s found a person on %d of %d usable frames, and RTMPose-m placed joints on %d of them"
                         % (held["with_person"], len(self.rows), held["with_joints"]))

    def test_a_person_with_no_joints_reads_box_only(self):
        """Person under 0.5: YOLO26s's box, no crop, so no joints. The sentence says box only, never joints placed."""
        bare = [json.loads(line) for line in self.lines]
        for row in bare:
            for d in row["detections"]:
                d["keypoints"] = None
        replace(self.take / "detections.jsonl", "\n".join(json.dumps(row) for row in bare) + "\n")
        held, why = chain.rows_hold(self.take, self.rows, self.recorded(), PINS, **self.digests)
        self.assertIsNone(why)
        self.assertEqual((held["with_joints"], held["with_person"] > 0), (0, True))
        self.assertEqual(chain.found_line(held), "YOLO26s found a person on %d of %d usable frames, and RTMPose-m placed no joints on any of them: box only"
                         % (held["with_person"], len(self.rows)))
        self.assertIn("placed joints", chain.found_line({**held, "with_joints": 1}), "control: one frame with joints says so")

    def test_rows_of_other_frames_read_missing(self):
        """As many rows as usable frames, one of them for a frame outside the usable part: the rgb_seq sets differ."""
        moved = [json.loads(line) for line in self.lines]
        moved[3]["frame"]["rgb_seq"] = max(int(row["rgb_seq"]) for row in self.rows) + 1000
        replace(self.take / "detections.jsonl", "\n".join(json.dumps(row) for row in moved) + "\n")
        self.assertEqual(len(moved), len(self.rows))
        self.assertEqual(self.why(hop=self.recorded()), "the rows cover %d of the %d usable frames" % (len(self.rows) - 1, len(self.rows)))

    def test_rows_recorded_for_another_take_or_check_read_missing(self):
        self.assertEqual(self.why(take_digest="0" * 64), "detector-run.json was recorded for another take")
        self.assertEqual(self.why(check_sha256="0" * 64), "detector-run.json was recorded against another check than this take's")
        self.assertIsNone(self.why(), "control: this take's own digests hold")

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
        self.edit_run(lambda run: run["weights"].update({"yolo26s.pt": PINS["yolo26s.pt"], "rtmpose-m-body7.onnx": "0" * 64}))
        self.assertEqual(self.why(hop=self.recorded()), "detector-run.json does not hold both pinned weight digests", "the pose weights too")
        self.edit_run(lambda run: run["weights"].update({"rtmpose-m-body7.onnx": PINS["rtmpose-m-body7.onnx"]}))
        self.edit_run(lambda run: run["weights"].update({"yolo26s.pt": PINS["yolo26s.pt"]}) or run["counts"].update(with_person=run["counts"]["with_person"] - 1))
        self.assertEqual(self.why(hop=self.recorded()), "the rows' counts are not detector-run.json's")
        self.edit_run(lambda run: run["counts"].update(with_person=run["counts"]["with_person"] + 1))
        replace(self.take / "detections.jsonl", "\n".join(self.lines).replace('"boxes":"yolo26s"', '"boxes":"fake-scripted-1"') + "\n")
        self.assertEqual(self.why(hop=self.recorded()), "the rows name fake-scripted-1, not a real model")

    def test_rows_never_checked_against_the_model_read_missing(self):
        self.assertEqual(self.why(hop={**self.hop, "checked": None}), "prepare did not check these rows against the model")
        self.assertEqual(self.why(hop={**self.hop, "checked": {**self.hop["checked"], "ok": False}}), "prepare did not check these rows against the model",
                         "checked, and the model did not reproduce them")
        self.assertEqual(self.why(hop={}), "prepare recorded no detector rows for this take")


class NoTrack(unittest.TestCase):
    def test_a_person_box_with_no_track_never_reads_nobody(self):
        person = [{"kind": "person_box", "validity": "rejected:no_room_position:depth_absent"}] * 12
        furniture = [{"kind": "object_box", "validity": "rejected:no_room_position:depth_discontinuity"}] * 30 + [{"kind": "object_box", "validity": "valid"}] * 40
        said = chain.no_track(12, 82, person + furniture, "UNSCORED: 12 open slots\nleft out: 0 not a person, 12 rejected, 0 with no position\n")
        self.assertEqual(said, "the detector saw a person on 12 of 82 usable frames, and no track formed: person observations "
                               "rejected:no_room_position:depth_absent 12. left out: 0 not a person, 12 rejected, 0 with no position")
        self.assertNotIn("depth_discontinuity", said, "the couch's and the book's boxes are not why no track formed")
        self.assertIn("valid 1.", chain.no_track(12, 82, person + [{"kind": "keypoints", "validity": "valid"}], "x\ny\n"), "control: a person's keypoints count")
        never = re.compile(r"\bnobody\b|saw no one")
        self.assertIsNone(never.search(said))
        self.assertIsNotNone(never.search("the detector saw nobody, nothing was asked"), "control: the nobody sentence is caught")


class PersonWords(unittest.TestCase):
    """Hop 15's row in words: a distance, yes or no and which half failed, the spot and the engagement. The rule itself
    is test_person.py's."""

    def test_which_half_failed_and_no_coordinate(self):
        card = dict(track_id="trk-1", seat_distance_m=0.12, on_couch=True, failed=[], region="sofa-book", engagement="engaged", held=False)
        self.assertEqual(chain.person_words(card), "trk-1 0.12 m from the seat point, on the couch: yes, spot sofa-book, engagement engaged")
        off = dict(card, seat_distance_m=1.04, on_couch=False, failed=["position", "height"], region="handheld", engagement="none")
        self.assertEqual(chain.person_words(off), "trk-1 1.04 m from the seat point, on the couch: no, the position half and height half failed, spot handheld, engagement none")
        self.assertIn("not tested, the bundle was not safe", chain.person_words(dict(card, on_couch=None, held=True)))
        self.assertNotEqual(chain.person_words(off), chain.person_words(dict(off, failed=["height"])), "control: the half named follows the result")


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
        later = {"ts": 10.5, "joints_room": [{"j": 5, "xyz": [3, 1, 1]}, {"j": 6, "xyz": [4, 1, 1]}]}
        got, _ = chain.person_to_aim([{"track_id": 1, "keypoint_series": [later, both, one]}], person)
        self.assertEqual((got["from_bundle_at"], got["person"]["shoulders_m"]["left"]), (10.5, [3, 1, 1]), "of two with both shoulders, the newer")


class NoFake(unittest.TestCase):
    def test_the_click_path_calls_no_fake_detector(self):
        code = {path.relative_to(HERE).as_posix(): path.read_text(encoding="utf-8") for path in sorted(HERE.rglob("*"))
                if path.suffix in (".py", ".mjs", ".js", ".json") and not path.name.startswith("test")}
        self.assertTrue({"chain.py", "brain.mjs", "run.mjs", "scene/prepare.py"} <= set(code))
        calls = re.compile(r"fake_detector|--fake(?![\w-])")   # replay's --fake flag, not ask_one.py's --fake-vllm, a test's vLLM
        self.assertEqual([name for name, text in code.items() if calls.search(text)], [])
        self.assertTrue(calls.search("import fake_detector\n"), "control: a planted import is found")
        self.assertTrue(calls.search("'--fake', rows"), "control: a planted --fake is found")


if __name__ == "__main__":
    unittest.main(verbosity=2)

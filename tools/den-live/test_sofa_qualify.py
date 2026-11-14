"""Offline capture and combined RGB sofa qualification checks."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import sofa_qualify as qualify


CORRECTION = {"applied": True, "factor": 0.889, "calibration_sha256": "a" * 64}
PROFILE = SimpleNamespace(correction_factor=0.889, calibration_sha256="a" * 64, clock_evidence_id="fixture",
                          head=SimpleNamespace(max_separation_s=0.085))


def bundle(sequence, session="synthetic-camera"):
    frames = {plane: {"sequence": sequence, "host_monotonic_timestamp_seconds": 20 + sequence + skew,
                      "device_timestamp_seconds": 10 + sequence + skew, "exposure_duration_seconds": 0.002}
              for plane, skew in (("rgb", 0.010), ("depth", 0.0), ("left", 0.0), ("right", 0.003))}
    data = io.BytesIO()
    np.savez_compressed(data, metadata_json=np.frombuffer(json.dumps({"schema": 1, "session": session,
                        "frames": frames, "depth_correction": CORRECTION}).encode(), dtype=np.uint8))
    return data.getvalue()


class FakeCamera:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.requests = []

    def get(self, path, timeout):
        self.requests.append(path)
        if path == "/calibration":
            return 200, json.dumps({"session": "synthetic-camera", "depth_correction": CORRECTION}).encode()
        return 200, next(self.frames)


class SofaQualificationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="den-sofa-session-")
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        for name in qualify.SESSION_INPUTS[1:]:
            (self.folder / name).write_text("{}", encoding="utf-8")
        (self.folder / "frames").mkdir()
        self.now = 100.0

    def sleep(self, seconds):
        self.now += seconds

    def capture(self, frames):
        camera = FakeCamera(frames)
        rows = qualify.capture(self.folder, camera, PROFILE, cue=lambda _: None, sleep=self.sleep,
                               clock=lambda: self.now, now_ns=lambda: int(self.now * 1e9))
        self.assertEqual(set(camera.requests), {"/frame.npz", "/calibration"})
        return rows

    def generated_session(self):
        count = len(qualify.CUES) * qualify.PER_CUE
        self.capture(bundle(i) for i in range(count))
        return qualify.load_session(self.folder)

    def test_a_holdout_session_keeps_its_own_cues(self):
        cues = qualify.HOLDOUT_CUES
        rows = qualify.capture(self.folder, FakeCamera(bundle(i) for i in range(len(cues) * qualify.PER_CUE)), PROFILE,
                               cue=lambda _: None, sleep=self.sleep, clock=lambda: self.now,
                               now_ns=lambda: int(self.now * 1e9), cues=cues)
        self.assertEqual([row["cue"] for row in rows], [cue for cue in cues for _ in range(qualify.PER_CUE)])
        self.assertEqual(qualify.read_json(self.folder / "manifest.json")["cues"], list(cues))
        self.assertEqual(qualify.load_session(self.folder)[0], rows)
        self.assertEqual(list(cues)[-1], qualify.sofa_mask_score.EMPTY_CUE, "the empty room comes last, for the background")
        self.assertEqual(qualify.CUE_SETS["holdout"], cues)

    def test_original_capture_and_deduplication(self):
        self.assertEqual(tuple(qualify.CUES), (*qualify.sofa_mask_score.POSE_CUES, qualify.sofa_mask_score.EMPTY_CUE))
        captured = [bundle(i) for i in range(len(qualify.CUES) * qualify.PER_CUE)]
        rows = self.capture([captured[0], *captured])
        self.assertEqual([row["key"][1] for row in rows], list(range(len(captured))))
        self.assertEqual([(self.folder / row["file"]).read_bytes() for row in rows], captured)
        self.assertEqual(qualify.load_session(self.folder)[0], rows)

    def test_session_restart_leaves_incomplete_manifest(self):
        with self.assertRaisesRegex(qualify.sofa_check.Stop, "restarted"):
            self.capture([bundle(1), bundle(2, "new-camera")])
        self.assertFalse(qualify.read_json(self.folder / "manifest.json")["complete"])
        with self.assertRaisesRegex(qualify.sofa_check.Stop, "incomplete"):
            qualify.load_session(self.folder)

    def test_replay_fails_and_edited_frame_or_candidate_invalidates_saved_session(self):
        with self.assertRaisesRegex(qualify.sofa_check.Stop, "advancing"):
            self.capture([bundle(2), bundle(1)])
        rows, _, _ = self.generated_session()
        frame = self.folder / rows[0]["file"]
        original = frame.read_bytes()
        frame.write_bytes(bundle(500))
        with self.assertRaisesRegex(qualify.sofa_check.Stop, "capture manifest"):
            qualify.load_session(self.folder)
        frame.write_bytes(original)
        (self.folder / "body-rgb-candidate.json").write_text('{"edited":true}', encoding="utf-8")
        with self.assertRaisesRegex(qualify.sofa_check.Stop, "frozen session input changed"):
            qualify.load_session(self.folder)

    def test_exact_witness_clock_and_mask_are_both_required(self):
        rows, samples, scope = self.generated_session()
        self.assertEqual(scope, qualify.ROOM_SCOPE)
        self.assertEqual(qualify.clock_for_mask(qualify.score_clock(samples), rows, PROFILE), (None, "unavailable"))
        ref = qualify.reference_template(samples)
        ref.update(basis="independent_acquisition_bounds", evidence_id="fixture", clock_drift_ppm=1, receiver_drift_ppm=1)
        for record in ref["samples"]:
            for name, exposure in record["exposures"].items():
                stamp = record["camera_metadata"][name]["host_monotonic_timestamp_seconds"]
                exposure.update(start_lower_s=stamp - 0.005, end_upper_s=stamp + 0.002)
        clock = qualify.score_clock(samples, ref)
        pairs, verdict = qualify.clock_for_mask(clock, rows, PROFILE)
        self.assertEqual(verdict, "measured")
        self.assertEqual(set(pairs), {tuple(row["key"]) for row in rows})
        with patch("builtins.print"):
            self.assertEqual(qualify.report(self.folder, clock, verdict, {"verdict": "pass", "basis": "candidate_separation"}), 1)
            self.assertEqual(qualify.report(self.folder, clock, verdict, {"verdict": "pass", "basis": "measured_separation"}), 0)
        result = qualify.read_json(self.folder / "qualification.json")
        self.assertEqual(result["verdict"], "sampled_session_pass")
        self.assertNotIn("candidate_separations", result["clock"])
        self.assertNotIn("synthetic-camera", json.dumps(result))
        self.assertEqual(qualify.clock_for_mask(clock, rows[:-1], PROFILE)[1], "missing_exact_pair")
        self.assertEqual(qualify.clock_for_mask(clock, rows, SimpleNamespace(clock_evidence_id="other"))[1], "evidence_id_mismatch")
        clock["max_rgb_depth_stamp_delta_s"] = 0.068
        self.assertEqual(qualify.clock_for_mask(clock, rows, PROFILE)[1], "pairing_limit_exceeded")

    def test_camera_relative_trial_completes_without_a_room_model_or_witness(self):
        for name in qualify.SESSION_INPUTS[1:]:
            (self.folder / name).unlink()
        count = len(qualify.CUES) * qualify.PER_CUE
        camera = FakeCamera(bundle(i) for i in range(count))
        qualify.capture(self.folder, camera, None, cue=lambda _: None, sleep=self.sleep,
                        clock=lambda: self.now, now_ns=lambda: int(self.now * 1e9))
        rows, samples, scope = qualify.load_session(self.folder)
        self.assertEqual(scope, qualify.CAMERA_SCOPE)
        self.assertEqual(len(rows), count)
        self.assertFalse((self.folder / "room-model.jsonl").exists())
        clock = qualify.score_clock(samples)
        mask = {"verdict": "pass", "basis": "candidate_separation"}
        with patch("builtins.print"):
            self.assertEqual(qualify.report(self.folder, clock, "unavailable", mask, scope=scope), 0)
        result = qualify.read_json(self.folder / "qualification.json")
        self.assertEqual(result["verdict"], "camera_trial_pass")
        self.assertEqual(result["rgb_pairing"], "pass")
        self.assertEqual(result["clock_check"], "unavailable")
        clock["max_rgb_depth_stamp_delta_s"] = 0.068
        with patch("builtins.print"):
            self.assertEqual(qualify.report(self.folder, clock, "unavailable", mask, scope=scope), 1)
        self.assertEqual(qualify.read_json(self.folder / "qualification.json")["rgb_pairing"], "fail")


if __name__ == "__main__":
    unittest.main()

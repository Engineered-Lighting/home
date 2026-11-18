"""body_profiles.py: the figure's two private profiles from a scored session. Synthetic inputs only.

    python -B -m unittest test_body_profiles
"""
import json
import tempfile
import unittest
from pathlib import Path

import body_profiles as bp

DIGEST = "a" * 64
REGISTRATION = {"fit": {"result": "registered", "transform_id": "den-oak@test"}, "scan_revision": "scan-test",
                "depth_scale": 1.05,
                "source_correction": {"frames": 3, "applied": 3, "factors": [0.9], "calibrations": [DIGEST]}}
CANDIDATE = {"qualification_id": "day-test", "clock_evidence_id": "clock-test", "transform_id": "den-oak@test",
             "scan_revision": "scan-test", "min_valid_depth_fraction": 0.5,
             "head": {"head_radius_m": 0.15, "neck_radius_m": 0.07, "max_head_shoulder_m": 0.4, "alignment_px": 3.0,
                      "localization_m": 0.03, "max_motion_m_s": 0.8, "max_separation_s": 0.105}}
CALIBRATION = {"depthai": "3.10.0", "device_id": "fictional-device",
               "depth_correction": {"applied": True, "factor": 0.9, "calibration_sha256": DIGEST}}


def frames(fps=30, n=8, step=12):
    return [{"frames": {"rgb": {"sequence": 100 + i * step, "host_monotonic_timestamp_seconds": 50 + i * step / fps}}}
            for i in range(n)]


class Profiles(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.session = Path(scratch.name)
        self.write(body_rgb_candidate=CANDIDATE, registration=REGISTRATION, camera_calibration=CALIBRATION, manifest={"frames": []})
        self.write(qualification=self.passing(self.session / "body-rgb-candidate.json"))

    @staticmethod
    def passing(candidate_path):
        import hashlib
        return {"mask": {"verdict": "pass", "basis": "zero_separation"}, "annotations_sha256": "a" * 64,
                "pose_digest": "b" * 64, "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest()}

    def write(self, **files):
        for name, data in files.items():
            (self.session / (name.replace("_", "-") + ".json")).write_text(json.dumps(data), encoding="utf-8")

    def test_a_passed_session_gives_a_clock_bound_to_its_camera_and_the_candidate_unchanged(self):
        clock, rgb, data, record = bp.build(self.session, frame_meta=frames())
        self.assertEqual(rgb, CANDIDATE)
        self.assertEqual(data, (self.session / "body-rgb-candidate.json").read_bytes(), "byte for byte")
        import hashlib
        self.assertEqual(record["candidate_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(record["mask"]["basis"], "zero_separation")
        self.assertEqual((record["annotations_sha256"], record["pose_digest"]), ("a" * 64, "b" * 64))
        out = self.session / "out"
        out.mkdir()
        (out / bp.RGB_FILE).write_bytes(data)
        (out / bp.QUALIFICATION_FILE).write_text(json.dumps(record), encoding="utf-8")
        self.assertTrue(bp.qualified_bytes(data, out / bp.QUALIFICATION_FILE))
        self.assertFalse(bp.qualified_bytes(data + b" ", out / bp.QUALIFICATION_FILE))
        shown = json.dumps(dict(json.loads(data), head="shown")).encode()
        self.assertTrue(bp.qualified_bytes(shown, out / "no-record.json"), "the owner shows the head: nothing to score")
        self.assertFalse(bp.qualified_bytes(bytes([255]) + b" not json", out / bp.QUALIFICATION_FILE))

        self.assertEqual(clock["schema"], "den-body-clock-profile-1")
        self.assertEqual((clock["evidence_id"], clock["device_id"], clock["calibration_sha256"], clock["fps"]),
                         ("clock-test", "fictional-device", DIGEST, 30))
        self.assertEqual((clock["rgb"], clock["depth"]), (bp.SDK_BOUNDS["rgb"], bp.SDK_BOUNDS["depth"]))
        self.assertTrue(all(0 < v <= 1 for plane in ("rgb", "depth") for v in clock[plane].values()))

    def test_refuses_a_score_that_names_no_candidate_or_no_inputs(self):
        for missing in ("candidate_sha256", "annotations_sha256", "pose_digest"):
            with self.subTest(missing=missing):
                qualification = self.passing(self.session / "body-rgb-candidate.json")
                qualification.pop(missing)
                self.write(qualification=qualification)
                with self.assertRaises(bp.Refused):
                    bp.build(self.session, frame_meta=frames())

    def test_fps_is_read_from_the_session(self):
        self.assertEqual(bp.build(self.session, frame_meta=frames(fps=15, step=6))[0]["fps"], 15)
        with self.assertRaises(bp.Refused):
            bp.build(self.session, frame_meta=frames(n=2))

    def test_refuses_without_a_passed_score_or_with_a_camera_that_does_not_match(self):
        for label, files in {
            "open score": {"qualification": {"mask": {"verdict": "incomplete"}}},
            "failed score": {"qualification": {"mask": {"verdict": "fail"}}},
            "a pass at the old max separation": {"qualification": {"mask": {"verdict": "pass",
                                                                            "basis": "candidate_separation"}}},
            "other sdk": {"camera_calibration": dict(CALIBRATION, depthai="3.9.0")},
            "correction off": {"camera_calibration": dict(CALIBRATION, depth_correction={"applied": False})},
        }.items():
            with self.subTest(label=label):
                self.setUp()
                self.write(**files)
                with self.assertRaises(bp.Refused):
                    bp.build(self.session, frame_meta=frames())
        self.setUp()
        self.write(body_rgb_candidate=dict(CANDIDATE, transform_id="den-oak@other"))
        with self.assertRaises(ValueError):
            bp.build(self.session, frame_meta=frames())

    def test_only_the_scored_candidate_is_written(self):
        import hashlib
        newer = self.session / "candidate-2.json"
        block = dict(CANDIDATE, head=dict(CANDIDATE["head"], torso_cut_m=0.1))
        newer.write_text(json.dumps(block), encoding="utf-8")
        self.write(qualification=self.passing(newer))
        self.assertEqual(bp.build(self.session, frame_meta=frames(), candidate_path=newer)[1], block)
        with self.assertRaises(bp.Refused):
            bp.build(self.session, frame_meta=frames())   # the capture's own candidate wasn't the one scored

    def test_never_writes_over_an_existing_profile(self):
        out = self.session / "out"
        out.mkdir()
        bp.write_new(out / bp.RGB_FILE, CANDIDATE)
        with self.assertRaises(FileExistsError):
            bp.write_new(out / bp.RGB_FILE, CANDIDATE)


if __name__ == "__main__":
    unittest.main()

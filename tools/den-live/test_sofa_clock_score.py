import copy
import unittest

from sofa_clock_score import REFERENCE_BASIS, reference_template, score_clock


def frame(index, session="sofa-session"):
    return {
        "session": session,
        "frames": {
            plane: {
                "sequence": index * 2 + offset,
                "host_monotonic_timestamp_seconds": 100 + index + shift,
                "device_timestamp_seconds": 20 + index + shift,
                "exposure_duration_seconds": None if plane == "depth" else 0.002,
            }
            for plane, offset, shift in (
                ("rgb", 0, 0.010), ("left", 10, 0.0), ("right", 20, 0.004), ("depth", 30, 0.005)
            )
        },
    }


def witness(samples):
    ref = reference_template(samples)
    ref.update(basis=REFERENCE_BASIS, evidence_id="scope-run-1", clock_drift_ppm=70, receiver_drift_ppm=30)
    for row, sample in zip(ref["samples"], samples):
        for plane, (before, after) in {"rgb": (0.006, 0.001), "left": (0.020, 0.002),
                                       "right": (0.002, 0.013)}.items():
            stamp = sample["frames"][plane]["host_monotonic_timestamp_seconds"]
            row["exposures"][plane].update(start_lower_s=stamp - before, end_upper_s=stamp + after)
    return ref


class SofaClockScoreTests(unittest.TestCase):
    def setUp(self):
        self.samples = [frame(1), frame(2)]
        self.reference = witness(self.samples)

    def test_camera_metadata_and_blank_template_do_not_qualify_optical_clock(self):
        template = reference_template(self.samples)
        self.assertEqual(template["samples"][0]["frame_key"], ["sofa-session", 2, 32])
        self.assertEqual(template["samples"][0]["exposures"]["right"]["sequence"], 22)
        self.assertIsNone(template["samples"][0]["exposures"]["right"]["start_lower_s"])
        self.assertEqual(template["samples"][0]["camera_metadata"], self.samples[0]["frames"])
        for reference in (None, template):
            result = score_clock(self.samples, reference)
            self.assertEqual(result["status"], "unavailable")
            self.assertAlmostEqual(result["max_rgb_depth_stamp_delta_s"], 0.005)
            self.assertEqual(result["scored"], 0)
            self.assertNotIn("candidate", result)

    def test_uses_both_stereo_exposures_around_depth_output_stamp(self):
        result = score_clock(self.samples, self.reference)
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["scored"], 2)
        self.assertEqual(result["candidate"]["evidence_id"], "scope-run-1")
        for plane, before, after in (("rgb", 0.006, 0.001), ("depth", 0.025, 0.012)):
            for field, expected in (("before_stamp_s", before), ("after_stamp_s", after)):
                margin = result["candidate"][plane][field]
                self.assertGreater(margin, expected)
                self.assertAlmostEqual(margin, expected / (1 - 70e-6), places=8)
        self.assertGreater(result["max_exposure_separation_s"], 0.031)
        self.assertLess(result["max_exposure_separation_s"], 0.032)
        self.assertGreaterEqual(result["max_candidate_separation_s"], result["max_exposure_separation_s"])
        self.assertEqual([row["frame_key"] for row in result["candidate_separations"]],
                         [["sofa-session", 2, 32], ["sofa-session", 4, 34]])

    def test_candidate_recombines_worst_margins_from_different_frames(self):
        row = self.reference["samples"][1]["exposures"]["rgb"]
        stamp = self.samples[1]["frames"]["rgb"]["host_monotonic_timestamp_seconds"]
        row["end_upper_s"] = stamp + 0.021
        row = self.reference["samples"][1]["exposures"]["left"]
        stamp = self.samples[1]["frames"]["left"]["host_monotonic_timestamp_seconds"]
        row["start_lower_s"] = stamp - 0.003
        result = score_clock(self.samples, self.reference)
        self.assertGreater(result["max_candidate_separation_s"], result["max_exposure_separation_s"] + 0.01)

    def test_rejects_wrong_plane_unmatched_or_duplicate_bundle(self):
        for path, value in (("plane", 23), ("bundle", 3), ("duplicate", None)):
            with self.subTest(path=path):
                reference = copy.deepcopy(self.reference)
                if path == "plane":
                    reference["samples"][0]["exposures"]["right"]["sequence"] = value
                elif path == "bundle":
                    reference["samples"][0]["frame_key"][1] = value
                else:
                    reference["samples"].append(reference["samples"][0])
                result = score_clock(self.samples, reference)
                self.assertEqual(result["status"], "invalid")
                self.assertNotIn("candidate", result)

    def test_missing_evidence_never_yields_partial_clock_bounds(self):
        for path in ("bundle", "endpoint", "rate", "rgb_duration"):
            with self.subTest(path=path):
                reference, samples = copy.deepcopy(self.reference), copy.deepcopy(self.samples)
                if path == "bundle":
                    reference["samples"].pop()
                elif path == "endpoint":
                    reference["samples"][1]["exposures"]["right"]["end_upper_s"] = None
                elif path == "rate":
                    reference["receiver_drift_ppm"] = None
                else:
                    samples[1]["frames"]["rgb"]["exposure_duration_seconds"] = None
                result = score_clock(samples, reference)
                self.assertEqual(result["status"], "unavailable")
                self.assertNotIn("candidate", result)

    def test_rejects_restart_replay_and_backwards_stereo_timestamp(self):
        backwards = copy.deepcopy(self.samples[1])
        backwards["frames"]["left"]["device_timestamp_seconds"] = 1.0
        for newer in (frame(0, "new-session"), self.samples[0], backwards):
            with self.subTest(newer=newer):
                result = score_clock([self.samples[0], newer], self.reference)
                self.assertEqual(result["status"], "invalid")

    def test_rejects_nonsensical_exposure_and_rate(self):
        for path, value in (("basis", "browser_javascript"), ("rate", True),
                            ("short", 0.001), ("unbounded", float("inf"))):
            with self.subTest(path=path):
                reference = copy.deepcopy(self.reference)
                if path == "basis":
                    reference["basis"] = value
                elif path == "rate":
                    reference["clock_drift_ppm"] = value
                else:
                    row = reference["samples"][0]["exposures"]["right"]
                    row["end_upper_s"] = row["start_lower_s"] + value
                self.assertEqual(score_clock(self.samples, reference)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()

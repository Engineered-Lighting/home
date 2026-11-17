"""Exact-row binding for optional RGB head geometry, with no runtime model dependencies."""
import copy
import math
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import head_binding as H
import detect_worker

FRAME = ["run", 2, 7]
IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
ROOM = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]


def fixture():
    points = [[100 + i, 80 + i, 0.9] for i in range(17)]
    row = {"detections": [{"id": "rgb-42:0", "class": "person", "keypoints": points}]}
    observation = {"obs_id": "opaque-observation-id", "kind": "keypoints", "joint_set": "coco17",
                   "validity": "valid", "transform_id": "registered-1", "freshness": {"status": "fresh"},
                   "keypoints": copy.deepcopy(points)}
    candidate = {"detection_id": "rgb-42:0", "detection_index": 0, "frame_key": FRAME,
                 "transform_id": "registered-1", "forward_rgb": [0.0, 0.0, -1.0], "up_rgb": [0.0, -1.0, 0.0],
                 "source_joint_ids": [0, 1, 2, 3, 4], "basis": H.BASIS, "angular_sd_deg": 25.0, "fit_rmse_px": 0.7}
    return row, [observation], {"candidates": [candidate], "reasons": {}}


def bind(row, observations, payload, *, rgb_rotation=IDENTITY, room_rotation=ROOM):
    return H.bind_candidates(row, observations, payload, frame_key=FRAME, transform_id="registered-1",
                             rgb_from_left=rgb_rotation, room_from_left=room_rotation, capture_mono=123.5)


class HeadBindingTest(unittest.TestCase):
    def test_rgb_to_left_inverse_then_room_rotation_and_current_time(self):
        row, observations, payload = fixture()
        rgb_rotation = [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]
        result, reasons = bind(row, observations, payload, rgb_rotation=rgb_rotation)
        self.assertEqual(reasons, {})
        estimate = result["opaque-observation-id"]
        self.assertEqual(estimate.forward, (1.0, 0.0, 0.0))
        self.assertEqual(estimate.up, (0.0, 0.0, 1.0))
        self.assertEqual((estimate.yaw_deg, estimate.pitch_deg, estimate.as_of_mono), (90.0, 0.0, 123.5))
        self.assertEqual((estimate.basis, estimate.source_joint_ids), (H.BASIS, (0, 1, 2, 3, 4)))

    def test_one_row_keeps_rejected_observation_indices_and_never_matches_by_position(self):
        row, observations, payload = fixture()
        row["detections"].insert(0, {"id": "rejected", "keypoints": None})
        observations.insert(0, {"obs_id": "first", "validity": "rejected:depth"})
        payload["candidates"][0]["detection_index"] = 1
        result, _ = bind(row, observations, payload)
        self.assertEqual(list(result), ["opaque-observation-id"])
        payload["candidates"][0]["detection_index"] = 0
        self.assertEqual(bind(row, observations, payload)[0], {})

    def test_frame_detection_and_registration_mismatches_abstain(self):
        for field, value in (("frame_key", ["run", 2, 6]), ("frame_key", ["other", 2, 7]),
                             ("frame_key", ["run", 3, 7]), ("detection_id", "other"),
                             ("detection_index", 1), ("detection_index", True), ("transform_id", "old")):
            with self.subTest(field=field, value=value):
                row, observations, payload = fixture()
                payload["candidates"][0][field] = value
                self.assertEqual(bind(row, observations, payload)[0], {})

    def test_current_pixels_and_accepted_pose_are_required(self):
        for change in ({"validity": "rejected:detector_result_expired"}, {"kind": "person_box"},
                       {"joint_set": "other"}, {"transform_id": "old"}, {"freshness": {"status": "expired"}}):
            with self.subTest(change=change):
                row, observations, payload = fixture()
                observations[0].update(change)
                self.assertEqual(bind(row, observations, payload)[0], {})
        row, observations, payload = fixture()
        observations[0]["keypoints"][0][0] += 1
        self.assertEqual(bind(row, observations, payload)[0], {})
        self.assertEqual(bind(row, [], payload)[0], {})

    def test_missing_nonface_joints_keep_the_pinned_observers_normalized_null_format(self):
        row, observations, payload = fixture()
        row["detections"][0]["keypoints"][16] = None
        observations[0]["keypoints"][16] = [None, None, None]
        self.assertTrue(bind(row, observations, payload)[0])

    def test_duplicate_candidate_and_duplicate_detector_ids_cannot_override_one_observation(self):
        row, observations, payload = fixture()
        payload["candidates"].append(copy.deepcopy(payload["candidates"][0]))
        self.assertEqual(bind(row, observations, payload)[0], {})
        row["detections"].append(copy.deepcopy(row["detections"][0]))
        observations.append(copy.deepcopy(observations[0]))
        self.assertEqual(bind(row, observations, payload)[0], {})

    def test_bad_axes_uncertainty_and_model_basis_abstain(self):
        for field, value in (("forward_rgb", [0, 0, 0]), ("forward_rgb", [0, 0, 2]),
                             ("up_rgb", [0, 0, -1]), ("up_rgb", [float("nan"), 0, 0]),
                             ("angular_sd_deg", 19.9), ("angular_sd_deg", 45.1),
                             ("angular_sd_deg", True), ("fit_rmse_px", -1),
                             ("basis", "stereo_face_axis"), ("source_joint_ids", [0, True, 2, 3, 4])):
            with self.subTest(field=field):
                row, observations, payload = fixture()
                payload["candidates"][0][field] = value
                self.assertEqual(bind(row, observations, payload)[0], {})
        row, observations, payload = fixture()
        self.assertEqual(bind(row, observations, payload, rgb_rotation=[[1, 0, 0], [0, 1, 0], [0, 0, -1]])[0], {})

    def test_projected_room_up_rejects_double_landmark_swap_but_accepts_downward_pitch(self):
        row, observations, payload = fixture()
        payload["candidates"][0]["up_rgb"] = [0, 1, 0]
        self.assertEqual(bind(row, observations, payload)[0], {}, "the fitted head is upside down in room axes")
        angle = math.radians(60)
        payload["candidates"][0].update(forward_rgb=[0, math.cos(angle), -math.sin(angle)],
                                         up_rgb=[0, math.sin(angle), math.cos(angle)])
        result, reasons = bind(row, observations, payload, room_rotation=IDENTITY)
        self.assertEqual(reasons, {})
        self.assertAlmostEqual(result["opaque-observation-id"].pitch_deg, -60)

    def test_malformed_optional_packets_and_absence_are_bounded(self):
        row, observations, _ = fixture()
        for payload in (None, [], {}, {"candidates": [None]}, {"candidates": [{}] * 65}):
            with self.subTest(payload_type=type(payload).__name__):
                self.assertEqual(bind(row, observations, payload)[0], {})
        self.assertEqual(H.diagnostics({"candidates": [], "reasons": {"private-text": 2,
                         "low_confidence": 1, "bad-count": -1, "bad-type": True}}),
                         (0, {"other": 2, "low_confidence": 1}))

    def test_optional_worker_errors_do_not_mutate_the_detector_row(self):
        row, _, payload = fixture()
        original = copy.deepcopy(row)
        header = {"head_calibration": {}, "frame_key": FRAME}
        def failed(*_args):
            raise ValueError("private input must not appear in the reply")
        self.assertEqual(detect_worker.optional_head(row, header, estimate=failed),
                         {"candidates": [], "reasons": {"worker_error": 1}})
        payload["candidates"][0]["fit_rmse_px"] = math.nan
        self.assertEqual(detect_worker.optional_head(row, header, estimate=lambda *_: payload),
                         {"candidates": [], "reasons": {"worker_error": 1}})
        self.assertEqual(row, original)
        self.assertIsNone(detect_worker.optional_head(row, {}, estimate=failed))

    def test_main_binding_import_and_unconfigured_worker_stay_standard_library_only(self):
        script = """
import importlib.abc, sys
sys.path.insert(0, sys.argv[1])
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('numpy', 'cv2', 'torch', 'onnxruntime', 'head_pose'):
            raise AssertionError('unexpected optional dependency')
sys.meta_path.insert(0, Block())
import head_binding, detect_worker
assert detect_worker.optional_head({}, {}) is None
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(HERE)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()

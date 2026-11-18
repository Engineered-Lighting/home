"""Numerical RGB head pose tests using generated landmarks, without camera images or model weights."""
import copy
import math
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import head_pose as H


CALIBRATION = {"k": [520.0, 510.0, 320.0, 180.0, 0.0], "d": [0.0] * 8, "size": [640, 360],
               "transform_id": "synthetic-camera@1", "pixel_sd_px": 4.0,
               "room_from_rgb": [[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]}
FRAME_KEY = ["synthetic-run", 1, 17]
FRONT = np.array([[-1.0, 0, 0], [0, 0, -1.0], [0, -1.0, 0]])


def rotation(yaw=0, pitch=0, roll=0):
    z = cv2.Rodrigues(np.array([0.0, 0.0, math.radians(yaw)]))[0]
    x = cv2.Rodrigues(np.array([math.radians(pitch), 0.0, 0.0]))[0]
    y = cv2.Rodrigues(np.array([0.0, math.radians(roll), 0.0]))[0]
    return FRONT @ z @ x @ y


def pixels(matrix, translation, calibration):
    """Independent rational-camera projection, including skew, for the synthetic correspondence truth."""
    camera = np.asarray(H.HEAD_TEMPLATE) @ matrix.T + translation
    x, y = camera[:, 0] / camera[:, 2], camera[:, 1] / camera[:, 2]
    k1, k2, p1, p2, k3, k4, k5, k6 = calibration["d"]
    radius = x * x + y * y
    radial = (1 + k1 * radius + k2 * radius ** 2 + k3 * radius ** 3) / (
        1 + k4 * radius + k5 * radius ** 2 + k6 * radius ** 3)
    xd = x * radial + 2 * p1 * x * y + p2 * (radius + 2 * x * x)
    yd = y * radial + p1 * (radius + 2 * y * y) + 2 * p2 * x * y
    fx, fy, cx, cy, skew = calibration["k"]
    return np.column_stack((fx * xd + skew * yd + cx, fy * yd + cy))


def row(matrix=None, translation=(0.0, 0.0, 2.0), calibration=CALIBRATION):
    matrix = FRONT if matrix is None else matrix
    face = pixels(matrix, np.asarray(translation), calibration)
    return {"frame": {"width": 640, "height": 360},
            "detections": [{"class": "person", "id": "synthetic-person", "score": 0.95,
                            "keypoints": [[float(u), float(v), 0.95] for u, v in face] + [None] * 12}]}


class HeadPose(unittest.TestCase):
    def estimate(self, value=None, calibration=CALIBRATION):
        return H.estimate(row() if value is None else value, calibration, FRAME_KEY)

    def test_known_rotations_and_exact_frame_identity(self):
        for yaw, pitch, roll in ((0, 0, 0), (-35, 0, 0), (40, -25, 10), (20, 35, -12)):
            with self.subTest(yaw=yaw, pitch=pitch, roll=roll):
                matrix = rotation(yaw, pitch, roll)
                result = self.estimate(row(matrix))
                self.assertEqual(len(result["candidates"]), 1, result["reasons"])
                candidate = result["candidates"][0]
                np.testing.assert_allclose(candidate["forward_rgb"], matrix[:, 1], atol=1e-4)
                np.testing.assert_allclose(candidate["up_rgb"], matrix[:, 2], atol=1e-4)
                self.assertAlmostEqual(np.dot(candidate["forward_rgb"], candidate["up_rgb"]), 0, places=7)
                self.assertEqual(candidate["frame_key"], FRAME_KEY)
                self.assertEqual((candidate["detection_index"], candidate["detection_id"], candidate["transform_id"]),
                                 (0, "synthetic-person", CALIBRATION["transform_id"]))
                self.assertEqual(candidate["source_joint_ids"], [0, 1, 2, 3, 4])
                self.assertEqual(candidate["basis"], "rgb_coco5_pnp_model")
                self.assertGreaterEqual(candidate["angular_sd_deg"], 20)
                self.assertLessEqual(candidate["angular_sd_deg"], 45)
                self.assertLess(candidate["fit_rmse_px"], 0.001)

    def test_nonzero_skew_and_rational_distortion(self):
        calibration = copy.deepcopy(CALIBRATION)
        calibration["k"][4] = 47.0
        calibration["d"] = [-0.16, 0.035, 0.003, -0.002, 0.005, -0.015, 0.004, 0.001]
        matrix = rotation(28, -30, 15)
        result = self.estimate(row(matrix, translation=(0.4, -0.25, 1.7), calibration=calibration), calibration)
        self.assertEqual(len(result["candidates"]), 1, result["reasons"])
        np.testing.assert_allclose(result["candidates"][0]["forward_rgb"], matrix[:, 1], atol=1e-4)
        np.testing.assert_allclose(result["candidates"][0]["up_rgb"], matrix[:, 2], atol=1e-4)

    def test_low_confidence_missing_outside_small_and_collapsed_landmarks_abstain(self):
        for reason, change in (
                ("low_confidence", lambda points: points[0].__setitem__(2, 0.49)),
                ("low_confidence", lambda points: points.__setitem__(4, None)),
                ("outside_frame", lambda points: points[0].__setitem__(0, -1.0)),
                ("collapsed_ears", lambda points: points[4].__setitem__(slice(0, 2), points[3][:2])),
                ("landmark_order", lambda points: points.__setitem__(slice(1, 3), points[1:3][::-1]))):
            value = row()
            change(value["detections"][0]["keypoints"])
            result = self.estimate(value)
            self.assertEqual(result, {"candidates": [], "reasons": {reason: 1}})
        self.assertEqual(self.estimate(row(translation=(0, 0, 25)))["reasons"], {"face_small": 1})

    def test_degenerate_correspondences_and_invalid_inputs_abstain(self):
        value = row()
        for index, point in enumerate(value["detections"][0]["keypoints"][:5]):
            point[:2] = [320 + (10, -8, 8, -20, 20)[index], 180]
        self.assertEqual(self.estimate(value)["reasons"], {"degenerate_geometry": 1})
        invalid = copy.deepcopy(CALIBRATION)
        invalid["pixel_sd_px"] = float("nan")
        self.assertEqual(self.estimate(calibration=invalid)["reasons"], {"calibration_invalid": 1})
        self.assertEqual(H.estimate(row(), CALIBRATION, ["wrong"])["reasons"], {"frame_invalid": 1})
        invalid = copy.deepcopy(CALIBRATION)
        invalid["room_from_rgb"][0][0] = -1.0
        self.assertEqual(self.estimate(calibration=invalid)["reasons"], {"calibration_invalid": 1})
        value = row()
        value["frame"]["width"] = 1920
        self.assertEqual(self.estimate(value)["reasons"], {"row_invalid": 1})

    def test_pixel_noise_floor_and_face_scale_control_uncertainty(self):
        close = self.estimate(row(translation=(0, 0, 1.5)))["candidates"][0]
        far = self.estimate(row(translation=(0, 0, 2.5)))["candidates"][0]
        self.assertGreater(far["angular_sd_deg"], close["angular_sd_deg"])
        noisy = copy.deepcopy(CALIBRATION)
        noisy["pixel_sd_px"] = 30
        self.assertEqual(self.estimate(calibration=noisy)["reasons"], {"uncertainty": 1})
        self.assertGreater(close["angular_sd_deg"], H.MODEL_SD_DEG, "zero residual retains the supplied pixel noise")
        self.assertEqual(self.estimate(row(translation=(0, 0, 3.0)))["reasons"], {"uncertainty": 1})

    def test_projected_room_up_rejects_inverted_labels_and_preserves_downlook(self):
        value = row(translation=(0, 0, 3))
        points = value["detections"][0]["keypoints"]
        points[1], points[2] = points[2], points[1]
        points[3], points[4] = points[4], points[3]
        self.assertEqual(self.estimate(value)["reasons"], {"head_roll": 1})
        matrix = rotation(0, -55, 0)
        down = self.estimate(row(matrix, translation=(0, 0, 1.5)))
        self.assertEqual(len(down["candidates"]), 1, down["reasons"])
        np.testing.assert_allclose(down["candidates"][0]["forward_rgb"], matrix[:, 1], atol=1e-4)
        # RTMPose's SimCC responses can exceed one. They are detector scores, without a probability ceiling.
        value = row()
        for point in value["detections"][0]["keypoints"][:5]:
            point[2] = 1.1
        self.assertEqual(len(self.estimate(value)["candidates"]), 1)

    def test_competing_numerical_solutions_abstain(self):
        value = row(translation=(0, 0, 3))
        # Fixed synthetic image perturbations found to support two distinct physical PnP minima within pixel noise.
        noise = ((2.699709, -1.375126), (-3.942256, 1.976093), (2.866278, 1.573893),
                 (-7.674808, -4.606024), (1.840892, 4.483098))
        for point, delta in zip(value["detections"][0]["keypoints"][:5], noise):
            point[0] += delta[0]
            point[1] += delta[1]
        self.assertEqual(self.estimate(value)["reasons"], {"ambiguous_solution": 1})

    def test_budget_and_person_limit_are_bounded(self):
        with patch.object(H.time, "monotonic", side_effect=[0.0, H.MAX_ESTIMATE_S + 0.001]):
            self.assertEqual(self.estimate()["reasons"], {"deadline": 1})
        value = row()
        value["detections"] = [{**copy.deepcopy(value["detections"][0]), "id": "person-%d" % i} for i in range(4)]
        result = self.estimate(value)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["reasons"], {"person_limit": 2})


if __name__ == "__main__":
    unittest.main()

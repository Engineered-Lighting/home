"""Offline head-envelope checks using the pinned den-observe geometry."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from body import head_exclusion
from body_head import HeadProfile, head_evidence
from perceive import Tools
from sofa_check import archive


PROFILE = HeadProfile(head_radius_m=0.17, neck_radius_m=0.09, max_head_shoulder_m=0.40,
                      alignment_px=2.0, localization_m=0.012, max_motion_m_s=1.0, max_separation_s=0.085)
FRAME_KEY = ["fixture-camera", 21, 22]


class HeadEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        root = Path(scratch.name)
        tools = Tools(archive(root, "observe"), archive(root, "track"))
        import camgeom
        import synth

        cls.geom = camgeom
        # An oblique, downward view with different x/y focal lengths and skew.
        pose = synth.camera_pose((0.45, -0.60, 2.35), tilt_deg=31)
        cls.camera = tools.roommodel.Camera(synth.room_model([synth.camera_entry(room_from_left=pose)]),
                                            "den-oak@2026-11-12", max_error_bound_m=0.10)
        cls.camera.rig.left_k = (440.0, 400.0, 310.0, 190.0, 3.0)
        cls.binding = {"transform_id": cls.camera.transform_id, "content_revision": "test-scan",
                       "run_id": "test-run", "track_epoch": 1, "depth_profile": "test-rgb"}

    def pose(self, *, centre_x=0.0, centre_y=2.0, centre_z=1.45):
        # Full face and both shoulders with the upper neck sloping sideways.
        offsets = ((0, -0.035, 0), (-0.04, -0.02, 0.03), (0.045, -0.02, 0.03),
                   (-0.075, 0.015, 0.0), (0.085, 0.015, 0.0),
                   (-0.30, 0.08, -0.27), (0.04, 0.08, -0.28))
        anchors = [[centre + delta for centre, delta in zip((centre_x, centre_y, centre_z), offset)]
                   for offset in offsets]
        to_depth = self.geom.inverse(*self.camera.room_from_left)
        image_points = [self.camera.rig.to_rgb(self.geom.apply(*to_depth, point)) for point in anchors]
        keypoints = [[*point, 0.9] for point in image_points] + [[None, None, None]] * 10
        return {"kind": "keypoints", "joint_set": "coco17", "validity": "valid", "visibility": "visible",
                "transform_id": self.camera.transform_id, "keypoints": keypoints,
                "image_box": [0, 0, 640, 360],
                "joints_room": [dict(j=index, status="supported", lift="stereo", xyz=point)
                                for index, point in enumerate(anchors)]}

    def evidence(self, pose=None, *, separation_s=0.055, profile=PROFILE):
        return head_evidence(self.pose() if pose is None else pose, self.camera, frame_key=FRAME_KEY,
                             binding=self.binding, profile=profile, separation_s=separation_s)

    def test_masks_independent_head_and_upper_neck_points_in_oblique_view(self):
        evidence = self.evidence()
        self.assertIsNotNone(evidence)
        left, top, right, bottom = head_exclusion(evidence, frame_key=FRAME_KEY, binding=self.binding)
        to_depth = self.geom.inverse(*self.camera.room_from_left)
        # Independent points on an asymmetric head and along the sloped neck;
        # a rectangle using only projected nose/eye/ear coordinates misses them.
        head_samples = [(0.02, 1.96, 1.58), (-0.13, 1.97, 1.48), (0.13, 2.01, 1.48),
                        (0.01, 2.12, 1.47), (-0.035, 1.94, 1.35)]
        neck_samples = [(-0.10, 2.03, 1.22), (-0.19, 2.06, 1.22), (-0.08, 2.14, 1.25)]
        for sample in head_samples + neck_samples:
            col, row = self.geom.project(self.geom.apply(*to_depth, sample), self.camera.rig.left_k)
            self.assertTrue(left <= col < right and top <= row < bottom, sample)
        self.assertEqual(evidence["alignment_px"], PROFILE.alignment_px)
        self.assertGreater(evidence["localization_px"], 0)
        self.assertGreater(evidence["motion_px"], 0)

    def test_refuses_missing_or_inconsistent_measurements(self):
        mutations = {
            "missing ear": lambda pose: pose["joints_room"].pop(3),
            "plane fallback": lambda pose: pose["joints_room"][0].update(lift="plane:1.2"),
            "duplicate depth anchor": lambda pose: pose["joints_room"][1].update(xyz=pose["joints_room"][0]["xyz"]),
            "partial visibility": lambda pose: pose.update(visibility="partial"),
            "wrong transform": lambda pose: pose.update(transform_id="other"),
            "wrong rgb pixel": lambda pose: pose["keypoints"][2].__setitem__(0, 10),
            "implausible shoulder reach": lambda pose: pose["joints_room"][5]["xyz"].__setitem__(0, 10),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                pose = self.pose()
                mutate(pose)
                self.assertIsNone(self.evidence(pose))

    def test_motion_budget_expands_the_mask_and_refuses_excess_or_edge_crops(self):
        slow = self.evidence(separation_s=0.020)
        fast = self.evidence(separation_s=0.085)
        self.assertGreater(fast["motion_px"], slow["motion_px"])
        self.assertIsNone(self.evidence(separation_s=0.086))
        self.assertIsNone(self.evidence(separation_s=float("nan")))
        self.assertIsNone(self.evidence(self.pose(centre_x=-1.9)))
        self.assertIsNone(self.evidence(profile=replace(PROFILE, max_motion_m_s=100)))

    def test_profile_is_explicit_and_finite(self):
        for bad in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(PROFILE, alignment_px=bad)


if __name__ == "__main__":
    unittest.main()

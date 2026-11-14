"""Offline head-envelope checks using the pinned den-observe geometry."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import unittest.mock

import math

from body import head_exclusion, torso_cut as cut_of
from body_head import HeadProfile, head_evidence, torso_cut
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
            "occluded visibility": lambda pose: pose.update(visibility="occluded"),
            "wrong transform": lambda pose: pose.update(transform_id="other"),
            "wrong rgb pixel": lambda pose: pose["keypoints"][2].__setitem__(0, 10),
            "implausible shoulder reach": lambda pose: pose["joints_room"][5]["xyz"].__setitem__(0, 10),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                pose = self.pose()
                mutate(pose)
                self.assertIsNone(self.evidence(pose))

    def test_a_partial_pose_with_the_whole_head_in_view_is_accepted(self):
        pose = self.pose()
        pose["visibility"] = "partial"   # e.g. legs leaving the RGB frame on the sofa
        self.assertEqual(self.evidence(pose), self.evidence())

    def test_a_head_the_rgb_view_cuts_off_is_refused_even_with_its_landmarks_inside(self):
        import body_head

        def anchors_inside(pose):
            return all(0 <= x < 640 and 0 <= y < 360 for x, y, _ in pose["keypoints"][:7])

        refused_by_rgb_edge = []
        for step in range(150):
            pose = self.pose(centre_z=1.45 + step * 0.01)
            if not anchors_inside(pose):
                break
            if self.evidence(pose) is None:
                with unittest.mock.patch.object(body_head, "_head_inside_rgb", return_value=True):
                    if self.evidence(pose) is not None:
                        refused_by_rgb_edge.append(step)
        self.assertTrue(refused_by_rgb_edge, "near the RGB edge, the head check alone refuses")
        self.assertIsNotNone(self.evidence(self.pose()))

    def test_motion_budget_expands_the_mask_and_refuses_excess_or_edge_crops(self):
        slow = self.evidence(separation_s=0.020)
        fast = self.evidence(separation_s=0.085)
        self.assertGreater(fast["motion_px"], slow["motion_px"])
        self.assertIsNone(self.evidence(separation_s=0.086))
        self.assertIsNone(self.evidence(separation_s=float("nan")))
        self.assertIsNone(self.evidence(self.pose(centre_x=-1.9)))
        self.assertIsNone(self.evidence(profile=replace(PROFILE, max_motion_m_s=100)))

    def with_hips(self, pose, hips=((-0.15, 2.1, 0.70), (0.05, 2.1, 0.70))):
        for index, xyz in zip((11, 12), hips):
            pose["joints_room"].append(dict(j=index, status="supported", lift="stereo", xyz=list(xyz)))
        return pose

    def cut(self, pose=None, *, profile=replace(PROFILE, torso_cut_m=0.08), separation_s=0.055):
        return torso_cut(self.with_hips(self.pose()) if pose is None else pose, self.camera, frame_key=FRAME_KEY,
                         binding=self.binding, profile=profile, separation_s=separation_s)

    def test_the_shoulder_line_cut_takes_every_head_and_neck_point_without_a_face(self):
        evidence = self.cut()
        self.assertIsNotNone(evidence)
        shoulder, axis, margin, *_ = cut_of(evidence, frame_key=FRAME_KEY, binding=self.binding)
        self.assertAlmostEqual(math.hypot(*axis), 1.0)
        # One constant margin: the profile's largest separation, never this frame's smaller one.
        self.assertAlmostEqual(margin, 0.08 + PROFILE.localization_m + PROFILE.max_motion_m_s * PROFILE.max_separation_s)
        self.assertEqual(self.cut(separation_s=0.0)["cut"]["margin_m"], evidence["cut"]["margin_m"])
        self.assertEqual(len(evidence["cut"]["shoulders_px"]), 2)
        self.assertAlmostEqual(evidence["cut"]["ceiling_m"], (1.45 - 0.27 + 1.45 - 0.28) / 2)
        to_depth = self.geom.inverse(*self.camera.room_from_left)
        depth_m = self.geom.apply(*to_depth, (-0.13, 2.08, 1.175))[2]   # at the shoulders
        margin_px = margin * self.camera.rig.left_k[0] / depth_m
        head_samples = [(0.02, 1.96, 1.58), (-0.13, 1.97, 1.48), (0.13, 2.01, 1.48), (0.01, 2.12, 1.47),
                        (-0.035, 1.94, 1.35)]
        neck_samples = [(-0.10, 2.03, 1.22), (-0.19, 2.06, 1.22), (-0.08, 2.14, 1.25)]
        for sample in head_samples + neck_samples:
            col, row = self.geom.project(self.geom.apply(*to_depth, sample), self.camera.rig.left_k)
            along = (col - shoulder[0]) * axis[0] + (row - shoulder[1]) * axis[1]
            self.assertGreater(along, -margin_px, sample)
        pose = self.with_hips(self.pose())
        for joint in pose["joints_room"]:
            if joint["j"] < 5:
                joint.update(status="unsupported", lift="none")   # turned away: no face at all
        self.assertIsNone(self.evidence(pose))
        self.assertEqual(self.cut(pose)["cut"], evidence["cut"])

    def test_the_cut_needs_a_measured_plausible_torso_and_a_qualified_setting(self):
        self.assertIsNone(self.cut(profile=PROFILE), "off unless the profile qualifies a cut")
        mutations = {
            "no hips at all": lambda pose: [pose["joints_room"].pop(), pose["joints_room"].pop()],
            "uncertain hips, no keypoints": lambda pose: [j.update(status="uncertain") for j in pose["joints_room"][-2:]],
            "hips on the shoulders": lambda pose: [j.update(xyz=[x, 2.08, 1.17]) for j, x in
                                                   zip(pose["joints_room"][-2:], (-0.30, 0.04))],
            "a second hip": lambda pose: pose["joints_room"].append(dict(pose["joints_room"][-1])),
            "hips beside the shoulders": lambda pose: [j.update(xyz=[x, 2.08, 1.10]) for j, x in
                                                       zip(pose["joints_room"][-2:], (0.45, 0.65))],
            "a missing shoulder lift": lambda pose: pose["joints_room"][5].update(status="unsupported", xyz=None),
            "occluded": lambda pose: pose.update(visibility="occluded"),
            "wrong transform": lambda pose: pose.update(transform_id="other"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                pose = self.with_hips(self.pose())
                mutate(pose)
                self.assertIsNone(self.cut(pose))
        self.assertIsNone(self.cut(separation_s=PROFILE.max_separation_s + 0.001))
        with self.assertRaises(ValueError):
            replace(PROFILE, torso_cut_m=0.0)
        with self.assertRaises(ValueError):
            replace(PROFILE, torso_cut_m=float("nan"))

    def test_the_cut_must_face_the_head_the_detector_sees(self):
        pose = self.with_hips(self.pose())
        self.assertIsNotNone(self.cut(pose))
        flipped = self.with_hips(self.pose(), hips=((-0.15, 2.1, 1.90), (0.05, 2.1, 1.90)))   # a wrong hip above the head
        self.assertIsNone(self.cut(flipped), "the face is on the hips' side: clear")
        bowed = self.with_hips(self.pose())
        to_depth = self.geom.inverse(*self.camera.room_from_left)
        chest = self.camera.rig.to_rgb(self.geom.apply(*to_depth, (-0.13, 2.0, 1.05)))
        bowed["keypoints"][0] = [*chest, 0.9]   # the nose below the shoulder line
        self.assertIsNone(self.cut(bowed), "any face point on the hips' side clears")
        unseen = self.with_hips(self.pose())
        for index in range(5):
            unseen["keypoints"][index][2] = 0.1
        self.assertIsNone(self.cut(unseen), "no face or ear point seen: clear")

    def test_hips_without_depth_come_from_their_keypoints_at_the_shoulders_depth(self):
        lifted = self.cut()
        pose = self.with_hips(self.pose())
        to_depth = self.geom.inverse(*self.camera.room_from_left)
        for joint in pose["joints_room"][-2:]:   # at a desk: the hips have keypoints but no depth
            pose["keypoints"][joint["j"]] = [*self.camera.rig.to_rgb(self.geom.apply(*to_depth, joint["xyz"])), 0.45]
            joint.update(status="unsupported", lift="none", xyz=None)
        from_keypoints = self.cut(pose)
        self.assertIsNotNone(from_keypoints)
        (a, b), (c, d) = lifted["cut"]["axis_px"], from_keypoints["cut"]["axis_px"]
        self.assertGreater(a * c + b * d, math.cos(math.radians(5)), "the same direction, within parallax")
        self.assertEqual(from_keypoints["cut"]["shoulder_px"], lifted["cut"]["shoulder_px"])
        one = self.with_hips(self.pose())
        one["joints_room"].pop()
        self.assertIsNotNone(self.cut(one), "one hip is enough")
        for joint in pose["joints_room"][-2:]:
            pose["keypoints"][joint["j"]][2] = 0.2
        self.assertIsNone(self.cut(pose), "nor from keypoints the detector barely saw")
        for joint in pose["joints_room"][-2:]:
            pose["keypoints"][joint["j"]][2] = 0.45
            pose["keypoints"][joint["j"]][1] = 360 * 1.2   # extrapolated below the frame's edge: still placed
        self.assertIsNotNone(self.cut(pose))
        for joint in pose["joints_room"][-2:]:
            pose["keypoints"][joint["j"]][1] = 360 * 1.3   # too far out to trust
        self.assertIsNone(self.cut(pose))

    def test_profile_is_explicit_and_finite(self):
        for bad in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(PROFILE, alignment_px=bad)


if __name__ == "__main__":
    unittest.main()

"""Contract 2's remaining person fields (person_fields.py): body_facing, head_point_m and wrists_m, on den-track's own
body_heading at its pin."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perceive  # noqa: E402
import person_fields as pf  # noqa: E402
import sofa_check as sc  # noqa: E402
from contracts import PERCEPTION_S, BodyFacing, HeadPoint, Orientation3D, SeatedState, TrackEvidence  # noqa: E402
from test_live import TOOLS  # noqa: E402

SHOULDER_HALF, HIP_HALF = 0.20, 0.17


def entry(j, xyz, *, lift="stereo", status="supported", var=0.0004):
    return {"j": j, "xyz": list(xyz), "cov": [[var, 0, 0], [0, var, 0], [0, 0, var]], "status": status, "lift": lift}


def body(facing_deg, *, at=(1.0, 2.0), hips_deg=None, shoulders=True, hips=True, var=0.0004, **kw):
    """A keypoints observation for someone at `at` facing `facing_deg` (clockwise from +y); the hips may face their
    own way (hips_deg). Shoulders and hips only, unless more entries are given (kw: extra)."""
    def pair(deg, half, z, js):
        # Facing (sin, cos) has their right at (cos, -sin): left to right runs along that.
        a = math.radians(deg)
        rx, ry = math.cos(a), -math.sin(a)
        return [entry(js[0], (at[0] - rx * half, at[1] - ry * half, z), var=var, **kw),
                entry(js[1], (at[0] + rx * half, at[1] + ry * half, z), var=var, **kw)]
    joints = []
    if shoulders:
        joints += pair(facing_deg, SHOULDER_HALF, 1.0, pf.SHOULDERS)
    if hips:
        joints += pair(facing_deg if hips_deg is None else hips_deg, HIP_HALF, 0.5, pf.HIPS)
    return {"kind": "keypoints", "joint_set": "coco17", "joints_room": joints}


class Fields(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tools = perceive.Tools(sc.archive(TOOLS, "observe"), sc.archive(TOOLS, "track"))
        cls.heading = staticmethod(tools.records.body_heading)
        cls.max_sd = tools.track_method.Method(
            str(tools.track_tree / "tools" / "den-track" / "METHOD.md")).tracker["heading_max_sd_deg"]

    def facing(self):
        return pf.Facing(self.heading, self.max_sd)

    def test_den_tracks_bearing_and_sd_gate(self):
        self.assertEqual(self.max_sd, 30.0, "den-track's heading_max_sd_deg at its pin")
        for deg in (0.0, 90.0, 181.0, 300.0):
            value = self.heading(pf.facing_pose(body(deg)))
            self.assertAlmostEqual(pf.turn(value[0], deg), 0.0, places=6)
        noisy = self.facing().update("t", 0, 10.0, body(90.0, var=0.05))   # a 22 cm sd on each joint
        self.assertIsNone(noisy, "an sd over 30 degrees is no sample")

    def test_both_stereo_shoulders_are_needed_and_the_hips_join_within_sixty_degrees(self):
        self.assertIsNone(pf.facing_pose(body(0.0, shoulders=False)), "hips alone never face")
        one = body(0.0)
        one["joints_room"] = [e for e in one["joints_room"] if e["j"] != 6]
        self.assertIsNone(pf.facing_pose(one), "one shoulder")
        self.assertIsNone(pf.facing_pose(body(0.0, lift="plane:floor")), "a plane lift isn't stereo")
        self.assertIsNone(pf.facing_pose(body(0.0, status="uncertain")), "nor is an uncertain joint")
        used = lambda pose: sorted(e["j"] for e in pf.facing_pose(pose)["joints_room"])
        self.assertEqual(used(body(0.0)), [5, 6, 11, 12])
        self.assertEqual(used(body(0.0, hips_deg=55.0)), [5, 6, 11, 12])
        self.assertEqual(used(body(0.0, hips_deg=65.0)), [5, 6], "hips 65 degrees off the shoulders stay out")
        self.assertEqual(used(body(0.0, hips_deg=180.0)), [5, 6], "swapped hips stay out")
        twisted = self.heading(pf.facing_pose(body(0.0, hips_deg=40.0)))[0]
        self.assertTrue(10.0 < twisted < 20.0, "the two lines averaged as den-track does, the longer shoulders' more")
        self.assertEqual(self.heading(pf.facing_pose(body(0.0, hips_deg=90.0)))[0] % 360.0, 0.0)

    def test_the_published_facing_is_the_circular_median_over_the_last_second(self):
        f = self.facing()
        out = [f.update("t", 0, 10.0 + 0.2 * i, body(deg)) for i, deg in enumerate((350.0, 10.0, 5.0, 340.0, 2.0))]
        self.assertAlmostEqual(out[-1].deg, 2.0, places=6, msg="across north, not 199")
        self.assertEqual((out[-1].n, out[-1].as_of_mono), (5, 10.8))
        self.assertLessEqual(out[-1].sd_deg, 30.0)
        later = f.update("t", 0, 11.6, body(90.0))   # the window keeps 10.6-11.6 only
        self.assertEqual(later.n, 3)
        self.assertIsInstance(later, BodyFacing)

    def test_a_flip_waits_for_a_second_sample(self):
        f = self.facing()
        for i in range(3):
            first = f.update("t", 0, 10.0 + 0.2 * i, body(90.0))
        self.assertAlmostEqual(first.deg, 90.0, places=6)
        once = f.update("t", 0, 10.6, body(270.0))   # a left-right swap, one frame
        self.assertAlmostEqual(once.deg, 90.0, places=6)
        self.assertEqual((once.n, once.as_of_mono), (3, 10.4), "the flipped sample isn't counted yet")
        back = f.update("t", 0, 10.8, body(92.0))
        self.assertAlmostEqual(back.deg, 90.0, places=6, msg="and a sample that doesn't flip drops it")
        self.assertEqual(back.n, 4)
        f.update("t", 0, 11.0, body(270.0))
        turned = f.update("t", 0, 11.2, body(268.0))
        self.assertAlmostEqual(pf.turn(turned.deg, 269.0), 1.0, places=6)
        self.assertEqual(turned.n, 2, "two flipped samples in a row: the turn is real, and the old side's go")
        near = f.update("t", 0, 11.4, body(330.0))
        self.assertEqual((round(near.deg, 6), near.n), (270.0, 3), "a turn under 150 degrees counts at once")

    def test_it_expires_with_the_pose_and_starts_again_with_the_revision(self):
        f = self.facing()
        f.update("t", 0, 10.0, body(90.0))
        self.assertAlmostEqual(f.update("t", 0, 10.0 + PERCEPTION_S, None).deg, 90.0, places=6, msg="held to the expiry")
        self.assertIsNone(f.update("t", 0, 10.01 + PERCEPTION_S, {}), "and gone after it")
        fresh = f.update("t", 0, 12.0, body(270.0))
        self.assertAlmostEqual(fresh.deg, 270.0, places=6, msg="an expired facing makes no flip to wait on")
        f.update("t", 0, 12.2, body(270.0))
        self.assertIsNone(f.update("t", 1, 12.4, None), "a new association revision starts again")
        self.assertAlmostEqual(f.update("t", 1, 12.6, body(90.0)).deg, 90.0, places=6)
        f.update("u", 0, 12.6, body(0.0))
        f.keep(["u"])
        self.assertIsNone(f.update("t", 1, 12.8, None), "a track not in the frame is forgotten")

    def test_head_point_and_wrists(self):
        stereo = {"nose": ((1.0, 2.1, 1.20), "supported")}
        self.assertIsNone(pf.head_point(stereo), "one face joint is no head point")
        stereo.update(left_eye=((0.97, 2.05, 1.24), "supported"), right_eye=((1.03, 2.05, 1.26), "supported"),
                      left_wrist=((0.8, 2.3, 0.6), "supported"))
        self.assertEqual(pf.head_point(stereo), HeadPoint((1.0, 2.05, 1.24), 3))
        self.assertEqual(dict(pf.wrists(stereo)), {"left_wrist": (0.8, 2.3, 0.6)})
        self.assertEqual(dict(pf.wrists({})), {})


class Posture(unittest.TestCase):
    """posture: sitting while seated; walking at 0.3 m/s or more; standing with the hips above the seated band (or the
    head at 1.30 m or more without them); else unclear. Qwen's own words for subject_posture, less lying."""

    @staticmethod
    def joints(hip_z=None, head_z=None):
        out = {}
        if hip_z is not None:
            out.update(left_hip=((0.9, 2.0, hip_z), "supported"), right_hip=((1.1, 2.0, hip_z), "supported"))
        if head_z is not None:
            out.update(nose=((1.0, 2.1, head_z), "supported"), left_eye=((0.97, 2.1, head_z + 0.03), "supported"))
        return out

    def test_the_rule(self):
        seated, standing_up = SeatedState(True, 1.0, "hips"), SeatedState(False, None, None)
        self.assertEqual(pf.posture(seated, self.joints(0.5), 0.0), "sitting")
        self.assertEqual(pf.posture(seated, {}, 1.0), "sitting", "seated decides first")
        self.assertEqual(pf.posture(standing_up, self.joints(1.0), 0.05), "standing")
        self.assertEqual(pf.posture(None, self.joints(1.0), None), "standing", "no speed yet")
        self.assertEqual(pf.posture(standing_up, self.joints(1.0), 0.6), "walking")
        # The seated rule's own evidence (2026-11-14): hips on the sofa band's edge under a seated head are no evidence
        # of standing (the copy's 0.75 m called them standing), and with no head, hips must be over 0.95 m.
        self.assertEqual(pf.posture(standing_up, self.joints(0.8, head_z=1.25), 0.0), "unclear", "the band's edge")
        self.assertEqual(pf.posture(standing_up, self.joints(0.9), 0.0), "unclear")
        self.assertEqual(pf.posture(standing_up, self.joints(0.8, head_z=1.6), 0.0), "standing", "the head")
        self.assertEqual(pf.posture(standing_up, {}, 0.6), "walking", "moving with no stereo pose")
        self.assertEqual(pf.posture(standing_up, self.joints(0.5), 0.6), "unclear", "low hips that aren't seated, moving")
        self.assertEqual(pf.posture(standing_up, self.joints(0.5), 0.0), "unclear", "low hips, not seated yet")
        self.assertEqual(pf.posture(standing_up, self.joints(head_z=1.6), 0.0), "standing", "no hips: the head")
        self.assertEqual(pf.posture(standing_up, self.joints(head_z=1.1), 0.0), "unclear")
        self.assertEqual(pf.posture(standing_up, {}, 0.1), "unclear")
        self.assertEqual(set(pf.POSTURES), {"sitting", "standing", "walking", "unclear"})

    def test_the_speed_is_over_the_last_second(self):
        s = pf.Speed()
        self.assertIsNone(s.update("t", 10.0, (0.0, 0.0)))
        self.assertIsNone(s.update("t", 10.4, (0.2, 0.0)), "under half a second of positions")
        self.assertAlmostEqual(s.update("t", 10.8, (0.4, 0.0)), 0.5)
        self.assertAlmostEqual(s.update("t", 11.6, (0.4, 0.0)), 0.0, msg="the window keeps 10.6-11.6 only")
        s.keep(["u"])
        self.assertIsNone(s.update("t", 11.8, (0.4, 0.0)), "a track not in the frame is forgotten")


class MeasuredOrientation(unittest.TestCase):
    @staticmethod
    def head(yaw=0.0, pitch=0.0, var=0.000001):
        """A symmetric face with its anatomical forward axis tilted about local right, then turned in room z."""
        a, p = math.radians(yaw), math.radians(pitch)

        def transform(x, y, z):
            forward = y * math.cos(p) - z * math.sin(p)
            height = y * math.sin(p) + z * math.cos(p)
            return (1.0 + x * math.cos(a) + forward * math.sin(a),
                    2.0 - x * math.sin(a) + forward * math.cos(a), 1.6 + height)
        # Eye/ear centre is just above the nose. Their average gives the coarse face-forward proxy.
        points = ((0, 0.12, -0.02), (-0.032, 0.08, 0.02), (0.032, 0.08, 0.02),
                  (-0.075, 0, 0), (0.075, 0, 0))
        return {"kind": "keypoints", "joint_set": "coco17",
                "joints_room": [entry(j, transform(*xyz), var=var) for j, xyz in enumerate(points)]}

    def test_head_anatomical_direction_rotates_in_room_and_has_a_model_floor(self):
        for yaw, pitch in ((0, 0), (90, -30), (355, 25), (180, 0)):
            value = pf.head_orientation(self.head(yaw, pitch), 12.5)
            self.assertIsInstance(value, Orientation3D)
            self.assertAlmostEqual(pf.turn(value.yaw_deg, yaw), 0, places=6)
            self.assertAlmostEqual(value.pitch_deg, pitch, places=6)
            self.assertAlmostEqual(math.hypot(*value.forward), 1.0)
            self.assertAlmostEqual(math.hypot(*value.up), 1.0)
            self.assertAlmostEqual(sum(a * b for a, b in zip(value.forward, value.up)), 0)
            self.assertGreater(value.angular_sd_deg, pf.HEAD_MODEL_SD_DEG)
            self.assertEqual((value.as_of_mono, value.source_joint_ids, value.basis),
                             (12.5, (0, 1, 2, 3, 4), "stereo_face_axis"))
        self.assertIsNone(pf.head_orientation(self.head(pitch=89), 12.5), "vertical direction has no useful yaw")

    def test_head_requires_complete_consistent_measured_landmarks(self):
        for j in pf.HEAD_ORIENTATION_JOINTS:
            pose = self.head()
            pose["joints_room"] = [e for e in pose["joints_room"] if e["j"] != j]
            self.assertIsNone(pf.head_orientation(pose, 1))
        for key, value in (("lift", "plane:floor"), ("status", "uncertain"), ("xyz", [0, float("nan"), 1]),
                           ("cov", None), ("cov", [[1, 2, 0], [2, 1, 0], [0, 0, 1]])):
            pose = self.head()
            pose["joints_room"][0][key] = value
            self.assertIsNone(pf.head_orientation(pose, 1), (key, value))
        pose = self.head()
        pose["joints_room"].append(pose["joints_room"][0])
        self.assertIsNone(pf.head_orientation(pose, 1), "duplicate landmark ids")
        pose = self.head()
        pose["joints_room"][1]["xyz"], pose["joints_room"][2]["xyz"] = (
            pose["joints_room"][2]["xyz"], pose["joints_room"][1]["xyz"])
        self.assertIsNone(pf.head_orientation(pose, 1), "eyes and ears contradict left/right")
        pose = self.head()
        pose["joints_room"][0]["xyz"][1] = 1.9
        self.assertIsNone(pf.head_orientation(pose, 1), "nose behind ears contradicts eye direction")

    def test_torso_uses_full_plane_and_rejects_degenerate_or_conflicting_joints(self):
        value = pf.torso_orientation(body(72, var=0.000001), 10)
        self.assertAlmostEqual(value.yaw_deg, 72)
        self.assertAlmostEqual(value.pitch_deg, 0)
        self.assertEqual(value.basis, "stereo_torso_plane")
        self.assertEqual(value.source_joint_ids, (5, 6, 11, 12))
        self.assertGreater(value.angular_sd_deg, pf.TORSO_MODEL_SD_DEG)
        pose = body(0, var=0.000001)
        for e in pose["joints_room"][:2]:
            e["xyz"][1] += 0.20
        leaning = pf.torso_orientation(pose, 10)
        self.assertAlmostEqual(leaning.pitch_deg, -math.degrees(math.atan2(0.20, 0.5)))
        for pose in (body(0, hips=False), body(0, shoulders=False), body(0, hips_deg=180),
                     body(0, lift="plane:floor"), body(0, status="uncertain")):
            self.assertIsNone(pf.torso_orientation(pose, 10))
        pose = body(0)
        pose["joints_room"][1]["xyz"] = pose["joints_room"][0]["xyz"][:]
        self.assertIsNone(pf.torso_orientation(pose, 10), "zero-width shoulders")

    def test_covariance_increases_uncertainty_and_excessive_noise_abstains(self):
        for build, function in ((self.head, pf.head_orientation),
                                (lambda var: body(20, var=var), pf.torso_orientation)):
            quiet = function(build(var=0.000001), 10)
            noisy = function(build(var=0.0001), 10)
            self.assertIsNotNone(noisy)
            self.assertGreater(noisy.angular_sd_deg, quiet.angular_sd_deg)
            self.assertIsNone(function(build(var=0.05), 10))
        pose = self.head()
        pose["joints_room"][0]["cov"][0][1] = 1
        self.assertIsNone(pf.head_orientation(pose, 10), "nonsymmetric covariance")

    def test_projection_preserves_current_measurement_and_old_constructor_defaults(self):
        value = pf.head_orientation(self.head(), 10)
        projected = pf.orientation_projection(value)
        self.assertEqual(projected["as_of_mono"], 10)
        self.assertEqual(projected["source_joint_ids"], [0, 1, 2, 3, 4])
        self.assertEqual(projected["forward"], list(value.forward))
        self.assertIsNone(pf.orientation_projection(None))
        self.assertIsNone(pf.head_orientation(None, 10))
        old = TrackEvidence("t", "confirmed", (1, 2), 0.1, "stereo", ("o",), "confident", 1, 10, {})
        self.assertEqual((old.head_orientation, old.torso_orientation), (None, None))


class OrientationIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import fixture_rgb
        cls.scene = fixture_rgb.Scene()
        cls.addClassCleanup(cls.scene.close)

    def test_normal_perception_projects_only_current_safe_orientation(self):
        from unittest.mock import patch
        p = self.scene.perceiver()
        poses = []
        original = p.t.observe.observe

        def add_covariant_geometry(*args, **kwargs):
            observations = original(*args, **kwargs)
            for obs in observations:
                if obs["kind"] == "keypoints":
                    obs["joints_room"] = body(45, var=0.000001)["joints_room"] + MeasuredOrientation.head()["joints_room"]
                    poses.append(obs)
            return observations

        with patch.object(p.t.observe, "observe", side_effect=add_covariant_geometry):
            for i in range(17):
                at = 100 + i / 5
                bundle, reply = self.scene.sample(at, i)
                event = p.finish(p.prepare(bundle, at), reply, {"status": "ok"}, at + 0.04)
                if p.snapshot.tracks and p.snapshot.tracks[0].association_status != "confident":
                    self.assertIsNone(p.snapshot.tracks[0].head_orientation)
            track, frame = p.snapshot.tracks[0], p.snapshot.frame
            self.assertTrue(poses)
            self.assertIsNotNone(track.head_orientation)
            self.assertIsNotNone(track.torso_orientation)
            self.assertEqual(track.head_orientation.as_of_mono, frame.capture_mono)
            self.assertEqual(event["tracks"][0]["head_orientation"], pf.orientation_projection(track.head_orientation))
            self.assertEqual(event["tracks"][0]["torso_orientation"], pf.orientation_projection(track.torso_orientation))
            at = 103.4
            bundle, reply = self.scene.sample(at, 17, extra=True)
            p.finish(p.prepare(bundle, at), reply, {"status": "ok"}, at + 0.04)
            self.assertTrue(p.snapshot.tracks)
            self.assertTrue(all(t.head_orientation is None and t.torso_orientation is None for t in p.snapshot.tracks),
                            "ambiguous people never carry previous orientation forward")


if __name__ == "__main__":
    unittest.main()

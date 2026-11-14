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
from contracts import PERCEPTION_S, BodyFacing, HeadPoint  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()

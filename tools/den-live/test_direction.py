"""The light direction's pieces (direction.py): the board's S-curve, the fade curves, the page and the aim, the facing and
the room's ambient scene. The S-curve's timing is held to den-core's scurve (the pinned coordinator tree), and its shape
to the shared vectors Home's copy is tested against."""

import json
import math
import tempfile
import unittest
from pathlib import Path

import coordinator_live
import direction
import sofa_check

HERE = Path(__file__).resolve().parent
VECTORS = HERE / "fixtures" / "direction-scurve-vectors.json"
AXES = [{"v_max": 30.0, "a_max": 100.0, "j_max": 400.0}, {"v_max": 30.0, "a_max": 100.0, "j_max": 400.0}]


def vectors():
    """The shared S-curve vectors: limits, then s and ds/dt at fractions of the duration. Home's copy of the S-curve
    (home-den-spot-motion.mjs) is tested against the same file."""
    cases = []
    for delta in ((0.5, 0.0), (4.6, -1.2), (20.0, 5.0), (90.0, 30.0), (0.0, 12.0)):
        limits = direction.path_limits(delta, AXES)
        curve = direction.SCurve(*limits)
        points = [round(curve.duration * k / 10, 9) for k in range(11)]
        cases.append({"delta_deg": list(delta), "limits": [round(x, 12) for x in limits],
                      "duration_s": round(curve.duration, 9),
                      "samples": [[t, round(curve.s(t), 9), round(curve.speed(t), 9)] for t in points]})
    return {"schema": "den-direction-scurve-vectors-1", "axes": AXES, "cases": cases}


class SCurveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def test_the_duration_is_den_cores_planner_time(self):
        """Home is timed by the same function the coordinator plans with (C12 v2 plan 5.1, den-core's scurve)."""
        for v, a, j in ((1.0, 2.0, 8.0), (0.2, 5.0, 100.0), (5.0, 0.5, 1.0), (30.0, 100.0, 400.0), (0.05, 0.1, 0.2)):
            with self.subTest(limits=(v, a, j)):
                self.assertAlmostEqual(direction.SCurve(v, a, j).duration, self.core.scurve.scurve_time(1.0, v, a, j),
                                       places=9)
        for delta in ((0.3, 0.0), (4.6, -1.2), (45.0, 10.0), (170.0, 80.0)):
            with self.subTest(delta=delta):
                glide = direction.Glide((0, 0), tuple(round(d * 100) for d in delta), AXES, 0.0)
                expected = self.core.scurve.move_time(tuple(round(d * 100) / 100 for d in delta), AXES)
                self.assertAlmostEqual(glide.duration, expected, places=9)

    def test_the_path_starts_and_lands_at_rest_with_no_jump(self):
        for limits in ((1.0, 2.0, 8.0), (5.0, 0.5, 1.0), (0.3, 3.0, 9.0)):
            curve = direction.SCurve(*limits)
            steps = [curve.duration * k / 400 for k in range(401)]
            s = [curve.s(t) for t in steps]
            speed = [curve.speed(t) for t in steps]
            self.assertEqual((s[0], s[-1]), (0.0, 1.0))
            self.assertTrue(all(b >= a - 1e-12 for a, b in zip(s, s[1:])), "never goes back")
            self.assertLess(max(speed), limits[0] * 1.000001)
            self.assertLess(speed[1], speed[len(speed) // 4], "starts from rest")
            for k in (50, 133, 200, 311):   # symmetric: the landing mirrors the start
                self.assertAlmostEqual(s[k] + s[400 - k], 1.0, places=6)
            jumps = max(abs(b - a) for a, b in zip(speed, speed[1:]))
            self.assertLess(jumps, limits[1] * curve.duration / 400 * 1.01, "speed changes within the acceleration limit")

    def test_a_glide_rounds_to_the_boards_centidegrees(self):
        glide = direction.Glide((1000, -500), (1460, -620), AXES, 10.0)
        self.assertEqual(glide.at(10.0), (1000, -500))
        self.assertEqual(glide.at(10.0 + glide.duration), (1460, -620))
        middle = glide.at(10.0 + glide.duration / 2)
        self.assertEqual(middle, (1230, -560))
        published = glide.published()
        self.assertEqual((published["from_deg"], published["to_deg"]), ([-10.0, 5.0], [-14.6, 6.2]))
        self.assertEqual(published["shape"], "scurve-7")
        self.assertIsNone(direction.Glide((5, 5), (5, 5), AXES, 0.0).published())

    def test_the_shared_vectors_are_this_curve(self):
        """fixtures/direction-scurve-vectors.json is what this S-curve gives; Home's test reads the same file."""
        self.assertEqual(json.loads(VECTORS.read_text(encoding="utf-8")), vectors())


class FadeTest(unittest.TestCase):
    def test_the_curves(self):
        for curve in ("smooth", "out", "in", "linear"):
            values = [direction.ease(curve, k / 50) for k in range(51)]
            self.assertEqual((values[0], values[-1]), (0.0, 1.0))
            self.assertTrue(all(b >= a for a, b in zip(values, values[1:])))
        self.assertGreater(direction.ease("out", .5), .7, "an exit lets most of the light go first")
        self.assertLess(direction.ease("in", .5), .3, "an unsure exit holds the light first")
        self.assertAlmostEqual(direction.ease("smooth", .5), .5)

    def test_a_ramp_pauses(self):
        ramp = direction.Ramp(0.0, 0.8, 2.0, "smooth", 10.0, "fade_in")
        self.assertAlmostEqual(ramp.value(11.0), 0.4)
        ramp.paused(0.5)
        self.assertAlmostEqual(ramp.value(11.5), 0.4)
        self.assertFalse(ramp.done(12.4))
        self.assertTrue(ramp.done(12.5))

    def test_level_codes_are_contract_fives(self):
        for level in (0, .002, .5, 1 / 254, 0.99, 1):
            self.assertEqual(direction.level_code(level), math.floor(level * 254 + .5))


def reader(dx=0.0, wrist_dz=0.0):
    """A seated reader facing -x, the book held at chest height (den-core joint rows, confidence 1)."""
    rows = {"left_wrist": (1.0 + dx, 1.1, .85 + wrist_dz), "right_wrist": (1.0 + dx, .9, .85),
            "nose": (1.3 + dx, 1.0, 1.25), "left_eye": (1.32 + dx, 1.03, 1.28), "right_eye": (1.32 + dx, .97, 1.28)}
    return {name: [*xyz, 1.0] for name, xyz in rows.items()}


class AimTest(unittest.TestCase):
    def test_the_page_is_between_the_hands_toward_the_eyes(self):
        page = direction.page_point(reader(), .2)
        self.assertAlmostEqual(page[2], .85 + .2 * (1.28 - .85), places=6)
        self.assertAlmostEqual(page[1], 1.0, places=6)
        self.assertGreater(page[0], 1.0, "toward the eyes")
        joints = reader()
        del joints["right_wrist"]
        self.assertIsNone(direction.page_point(joints, .2))

    def test_a_page_turn_never_moves_a_lit_aim_and_a_shift_moves_it_once(self):
        aim = direction.AimFilter(.06, 1.5)
        t = 0.0
        for _ in range(8):
            t += .4
            settled, _basis = aim.update(t, direction.page_point(reader(), .2), None, None, lit=True)
        moves = 0
        for k in range(3):   # a page turn: one hand up 15 cm for 0.8 s
            t += .4
            point = direction.page_point(reader(wrist_dz=.15), .2)
            now, _basis = aim.update(t, point, None, None, lit=True)
            moves += now != settled
        self.assertEqual(moves, 0, "a page turn never moves the aim")
        changes, last = [], settled
        for _ in range(12):   # shift 30 cm along the sofa and stay
            t += .4
            now, _basis = aim.update(t, direction.page_point(reader(dx=-.3), .2), None, None, lit=True)
            if now != last:
                changes.append(t)
                last = now
        self.assertEqual(len(changes), 1, "one move, not a chase")
        self.assertLess(math.dist(last, direction.page_point(reader(dx=-.3), .2)), .01)

    def test_the_first_aim_is_the_median_and_dark_aims_follow_at_once(self):
        aim = direction.AimFilter(.06, 1.5)
        page = direction.page_point(reader(), .2)
        aim.update(0.0, (page[0] + .4, page[1], page[2]), None, None, lit=False)   # one wild first frame
        for t in (.4, .8):
            got, _basis = aim.update(t, page, None, None, lit=False)
        self.assertLess(math.dist(got, page), .01, "not locked on the first point")
        moved = direction.page_point(reader(dx=-.1), .2)
        for t in (1.2, 1.6, 2.0):
            got, _basis = aim.update(t, moved, None, None, lit=False)
        self.assertLess(math.dist(got, moved), .01, "dark, it follows within a second")

    def test_missing_wrists_hold_the_page_then_fall_back(self):
        aim = direction.AimFilter(.06, 1.5)
        page = direction.page_point(reader(), .2)
        aim.update(0.0, page, None, None, lit=True)
        shoulders = (1.2, 1.0, .7)
        got, basis = aim.update(1.0, None, shoulders, "shoulders", lit=True)
        self.assertEqual((got, basis), (page, "page"), "a book hiding the wrists keeps the page")
        for t in (2.5, 3.0, 3.5, 4.0, 4.5):
            got, basis = aim.update(t, None, shoulders, "shoulders", lit=True)
        self.assertEqual(basis, "shoulders")
        self.assertLess(math.dist(got, shoulders), 1e-9, "the bases never mix in one window")


class FacingTest(unittest.TestCase):
    def test_seated_facing_is_averaged_and_standing_is_raw(self):
        facing = direction.FacingFilter()
        swing = [math.radians(a) for a in (0, 8, -8, 6, -6, 0)]
        for k, angle in enumerate(swing):
            got = facing.update(k * .4, (math.cos(angle), math.sin(angle)), True)
        self.assertLess(abs(math.degrees(math.atan2(got[1], got[0]))), 1.0)
        self.assertEqual(facing.update(3.0, (0.0, 1.0), False), (0.0, 1.0))


class AmbientTest(unittest.TestCase):
    def test_the_room_dims_after_the_spot_and_comes_back_with_its_exit(self):
        settings = dict(direction.SETTINGS)
        room = direction.Ambient()
        self.assertEqual(room.published(settings, 0.0), {"level": .75, "scale": 1.0, "cct_k": 3000})
        room.direct(True, settings, 10.0)
        self.assertEqual(room.published(settings, 10.4)["scale"], 1.0, "a beat after the spot")
        middle = room.published(settings, 10.5 + 2.0)
        self.assertAlmostEqual(middle["scale"], .7, places=3)
        low = room.published(settings, 10.5 + 4.0)
        self.assertEqual((low["scale"], low["level"], low["cct_k"]), (.4, .3, 2700), "dimmed and warmed")
        room.direct(False, settings, 20.0)
        self.assertEqual(room.published(settings, 21.5)["scale"], 1.0, "back with the exit")


if __name__ == "__main__":
    unittest.main()

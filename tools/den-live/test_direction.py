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


# A measured profile can decelerate harder than it accelerates (the firmware's d_max); den-core's fictional one can't.
ASYMMETRIC = [{"v_max": 120.0, "a_max": 60.0, "j_max": 2000.0, "d_max": 120.0},
              {"v_max": 90.0, "a_max": 40.0, "j_max": 1500.0, "d_max": 80.0}]


def vectors():
    """The shared S-curve vectors: the glide's published limits [v, a, j, d] (stretched where a timed MOVE's floor
    applies), then s and ds/dt at fractions of the duration. Home's copy of the S-curve (home-den-spot-playback.mjs) is
    tested against the same file."""
    cases = []
    for axes, delta, floor in ((AXES, (0.5, 0.0), 0.0), (AXES, (4.6, -1.2), 0.0), (AXES, (20.0, 5.0), 0.0),
                               (AXES, (90.0, 30.0), 0.0), (AXES, (0.0, 12.0), 0.0), (AXES, (4.6, -1.2), 1.5),
                               (ASYMMETRIC, (35.0, -10.0), 0.0), (ASYMMETRIC, (3.0, 1.0), 2.0)):
        glide = direction.Glide((0, 0), tuple(round(d * 100) for d in delta), axes, 0.0, floor)
        curve = glide.curve
        points = [round(curve.duration * k / 10, 9) for k in range(11)]
        cases.append({"delta_deg": list(delta), "min_duration_s": floor, "limits": glide.published()["limits"],
                      "duration_s": round(curve.duration, 9),
                      "samples": [[t, round(curve.s(t), 9), round(curve.speed(t), 9)] for t in points]})
    return {"schema": "den-direction-scurve-vectors-2", "cases": cases}


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

    def test_a_timed_moves_floor_stretches_the_same_curve(self):
        """The firmware's min_duration_ms (62ea043): k = max(1, min/T), and the S-curve k times slower is the one with
        speed / k, acceleration and deceleration / k^2 and jerk / k^3 (its duration.position_at uses t / k)."""
        base = direction.Glide((0, 0), (460, -120), AXES, 0.0)
        slow = direction.Glide((0, 0), (460, -120), AXES, 0.0, 1.5)
        self.assertAlmostEqual(slow.duration, 1.5, places=9)
        k = 1.5 / base.duration
        for f in (.1, .25, .5, .8, .97):
            self.assertAlmostEqual(slow.curve.s(f * slow.duration), base.curve.s(f * base.duration), places=9)
        self.assertEqual(direction.Glide((0, 0), (460, -120), AXES, 0.0, .1).duration, base.duration,
                         "never faster than the profile")
        self.assertAlmostEqual(slow.curve.limits[0], base.curve.limits[0] / k, places=12)

    def test_a_harder_brake_lands_sooner(self):
        """With d_max above a_max the deceleration ramp is shorter, as the board plans it (two ramps, one shared
        jerk); the path still runs from 0 to 1, never back."""
        even = direction.Glide((0, 0), (3500, -1000), [dict(axis, d_max=axis["a_max"]) for axis in ASYMMETRIC], 0.0)
        hard = direction.Glide((0, 0), (3500, -1000), ASYMMETRIC, 0.0)
        self.assertLess(hard.duration, even.duration)
        self.assertLess(hard.curve.down.T, hard.curve.up.T)
        s = [hard.curve.s(hard.duration * k / 200) for k in range(201)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(s, s[1:])))
        self.assertEqual((s[0], s[-1]), (0.0, 1.0))

    def test_the_shared_vectors_are_this_curve(self):
        """fixtures/direction-scurve-vectors.json is what this S-curve gives; Home's test reads the same file."""
        self.assertEqual(json.loads(VECTORS.read_text(encoding="utf-8")), vectors())


class FadeTest(unittest.TestCase):
    def test_the_curves(self):
        for curve in ("ease_out", "ease_in_out", "linear"):
            values = [direction.ease(curve, k / 50) for k in range(51)]
            self.assertEqual((values[0], values[-1]), (0.0, 1.0))
            self.assertTrue(all(b >= a for a, b in zip(values, values[1:])))
        self.assertGreater(direction.ease("ease_out", .5), .7, "an exit lets most of the light go first")
        self.assertGreater(direction.ease("ease_out", .05), .07, "a rise is visible at its first step")
        self.assertAlmostEqual(direction.ease("ease_out", 1 / 3), .5, msg="and half way at a third of its time")
        self.assertAlmostEqual(direction.ease("ease_in_out", .5), .5)

    def test_a_ramp_pauses(self):
        ramp = direction.Ramp(0.0, 0.8, 2.0, "ease_in_out", 10.0, "fade_in")
        self.assertAlmostEqual(ramp.value(11.0), 0.4)
        ramp.paused(0.5)
        self.assertAlmostEqual(ramp.value(11.5), 0.4)
        self.assertFalse(ramp.done(12.4))
        self.assertTrue(ramp.done(12.5))

    def test_output_is_the_dimming_curve_and_level_for_output_its_inverse(self):
        self.assertAlmostEqual(direction.output(.5), .1842, places=4, msg="level 0.5 gives 18 % of full")
        self.assertEqual((direction.output(0), direction.output(1)), (0.0, 1.0))
        for level in (0, .01, .05, .08, .3, .5, .9, 1):
            self.assertAlmostEqual(direction.level_for_output(direction.output(level)), level, places=9)

    def test_a_crossfade_in_light_keeps_the_page_whole(self):
        """Two complementary ramps eased in light add up to the same light all the way; eased in levels they'd give
        37 % of it half way."""
        down = direction.Ramp(1.0, 0.0, 1.2, "ease_in_out", 0.0, "crossfade_out", "light")
        up = direction.Ramp(0.0, 1.0, 1.2, "ease_in_out", 0.0, "crossfade_in", "light")
        for k in range(13):
            t = k * .1
            self.assertAlmostEqual(direction.output(down.value(t)) + direction.output(up.value(t)), 1.0, places=9)
        level_down = direction.Ramp(1.0, 0.0, 1.2, "ease_in_out", 0.0, "crossfade_out")
        level_up = direction.Ramp(0.0, 1.0, 1.2, "ease_in_out", 0.0, "crossfade_in")
        self.assertLess(direction.output(level_down.value(.6)) + direction.output(level_up.value(.6)), .4)
        self.assertEqual(up.published(.6)["space"], "light")

    def test_level_codes_are_contract_fives(self):
        for level in (0, .002, .5, 1 / 254, 0.99, 1):
            self.assertEqual(direction.level_code(level), math.floor(level * 254 + .5))


def reader(dx=0.0, wrist_dz=0.0):
    """A seated reader facing -x, the book held at chest height (den-core joint rows, confidence 1)."""
    rows = {"left_wrist": (1.0 + dx, 1.1, .85 + wrist_dz), "right_wrist": (1.0 + dx, .9, .85),
            "nose": (1.3 + dx, 1.0, 1.25), "left_eye": (1.32 + dx, 1.03, 1.28), "right_eye": (1.32 + dx, .97, 1.28)}
    return {name: [*xyz, 1.0] for name, xyz in rows.items()}


class AimTest(unittest.TestCase):
    def test_the_page_faces_the_eyes_and_the_hold_sets_its_direction(self):
        eyes = (1.32, 1.0, 1.28)
        slopes = {}
        for label, wrist_z in (("chest", .85), ("lap", .55)):
            joints = reader()
            joints["left_wrist"][2] = joints["right_wrist"][2] = wrist_z
            hands = (1.0, 1.0, wrist_z)
            page = direction.page_point(joints, .05)
            offset = [page[i] - hands[i] for i in range(3)]
            sight = [eyes[i] - hands[i] for i in range(3)]
            self.assertAlmostEqual(math.hypot(*offset), .05, places=6, msg=label)
            self.assertAlmostEqual(sum(a * b for a, b in zip(offset, sight)), 0.0, places=6,
                                   msg=f"{label}: up the page is square to the line of sight")
            self.assertAlmostEqual(page[1], 1.0, places=6)
            self.assertLess(page[0], 1.0, f"{label}: never back toward the eyes")
            self.assertGreater(page[2], wrist_z, f"{label}: above the hands")
            slopes[label] = (page[2] - wrist_z) / (1.0 - page[0])
        self.assertGreater(slopes["chest"], 1.5 * slopes["lap"], "it rises more at the chest; in the lap it lies ahead")
        below = reader()
        for name in ("left_wrist", "right_wrist"):
            below[name][0] = 1.31
        self.assertEqual(direction.page_point(below, .05)[:2], (1.31, 1.0), "hands under the eyes: the hands")
        joints = reader()
        del joints["right_wrist"]
        self.assertIsNone(direction.page_point(joints, .05))

    def test_a_page_turn_never_moves_a_lit_aim_and_a_shift_moves_it_once(self):
        aim = direction.AimFilter(.06, 1.5)
        t = 0.0
        for _ in range(8):
            t += .4
            settled, _basis = aim.update(t, direction.page_point(reader(), .05), None, None, lit=True)
        moves = 0
        for k in range(3):   # a page turn: one hand up 15 cm for 0.8 s
            t += .4
            point = direction.page_point(reader(wrist_dz=.15), .05)
            now, _basis = aim.update(t, point, None, None, lit=True)
            moves += now != settled
        self.assertEqual(moves, 0, "a page turn never moves the aim")
        changes, last = [], settled
        for _ in range(12):   # shift 30 cm along the sofa and stay
            t += .4
            now, _basis = aim.update(t, direction.page_point(reader(dx=-.3), .05), None, None, lit=True)
            if now != last:
                changes.append(t)
                last = now
        self.assertEqual(len(changes), 1, "one move, not a chase")
        self.assertLess(math.dist(last, direction.page_point(reader(dx=-.3), .05)), .01)

    def test_the_first_aim_is_the_median_and_dark_aims_follow_at_once(self):
        aim = direction.AimFilter(.06, 1.5)
        page = direction.page_point(reader(), .05)
        aim.update(0.0, (page[0] + .4, page[1], page[2]), None, None, lit=False)   # one wild first frame
        for t in (.4, .8):
            got, _basis = aim.update(t, page, None, None, lit=False)
        self.assertLess(math.dist(got, page), .01, "not locked on the first point")
        moved = direction.page_point(reader(dx=-.1), .05)
        for t in (1.2, 1.6, 2.0):
            got, _basis = aim.update(t, moved, None, None, lit=False)
        self.assertLess(math.dist(got, moved), .01, "dark, it follows within a second")

    def test_missing_wrists_hold_the_page_then_fall_back(self):
        aim = direction.AimFilter(.06, 1.5)
        page = direction.page_point(reader(), .05)
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
        self.assertEqual(room.published(settings, 0.0), {"level": .75, "scale": 1.0, "cct_k": 3000, "goal_level": .75,
                                                         "goal_scale": 1.0, "goal_cct_k": 3000, "phase": "steady",
                                                         "ramp_s": 0.0, "ramp_starts_mono": None})
        room.direct(True, settings, 10.0)
        cue = room.published(settings, 10.4)
        self.assertEqual(cue["scale"], 1.0, "a beat after the spot")
        self.assertEqual({key: cue[key] for key in ("goal_level", "goal_scale", "goal_cct_k", "phase", "ramp_s",
                                                    "ramp_starts_mono")},
                         {"goal_level": .3, "goal_scale": .4, "goal_cct_k": 2700, "phase": "dim", "ramp_s": 4.0,
                          "ramp_starts_mono": 10.5}, "the cue's goal and time, for a bulb that fades itself")
        middle = room.published(settings, 10.5 + 2.0)
        self.assertAlmostEqual(middle["scale"], .7, places=3)
        low = room.published(settings, 10.5 + 4.0)
        self.assertEqual((low["scale"], low["level"], low["cct_k"]), (.4, .3, 2700), "dimmed and warmed")
        room.direct(False, settings, 20.0)
        self.assertEqual(room.published(settings, 20.0)["phase"], "restore")
        back = room.published(settings, 21.5)
        self.assertEqual((back["scale"], back["phase"]), (1.0, "steady"), "back with the exit")


if __name__ == "__main__":
    unittest.main()

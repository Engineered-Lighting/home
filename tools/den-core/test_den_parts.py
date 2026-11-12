"""Tests for the den lighting coordinator's parts (coordinator plan, step 1).

    python -B -m unittest discover -s tools/den-core -p "test_den_*.py"
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import body  # noqa: E402
import bulbs  # noqa: E402
import eye  # noqa: E402
import joints  # noqa: E402
import learn  # noqa: E402
import need_v1  # noqa: E402
import scurve  # noqa: E402
import share  # noqa: E402
import situation  # noqa: E402
import spot_light  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def skeleton(x, y, seated=True, facing=(0.0, 1.0), wrists=True, nose=True):
    """COCO joints for one person at (x, y): seated hips at 0.45 m, standing at 0.95 m."""
    hip = 0.45 if seated else 0.95
    fx, fy = facing
    lx, ly = -fy, fx
    sz = hip + 0.5
    j = {"left_shoulder": [x + lx * 0.2, y + ly * 0.2, sz, 0.9], "right_shoulder": [x - lx * 0.2, y - ly * 0.2, sz, 0.9],
         "left_hip": [x + lx * 0.15, y + ly * 0.15, hip, 0.9], "right_hip": [x - lx * 0.15, y - ly * 0.15, hip, 0.9],
         "left_eye": [x + fx * 0.08 + lx * 0.03, y + fy * 0.08 + ly * 0.03, sz + 0.25, 0.9],
         "right_eye": [x + fx * 0.08 - lx * 0.03, y + fy * 0.08 - ly * 0.03, sz + 0.25, 0.9]}
    if nose:
        j["nose"] = [x + fx * 0.1, y + fy * 0.1, sz + 0.23, 0.9]
    if wrists:
        j["left_wrist"] = [x + fx * 0.3 + lx * 0.1, y + fy * 0.3 + ly * 0.1, sz - 0.3, 0.9]
        j["right_wrist"] = [x + fx * 0.3 - lx * 0.1, y + fy * 0.3 - ly * 0.1, sz - 0.3, 0.9]
    return j


def person_at(x, y, facing=(0.0, 1.0)):
    j = skeleton(x, y, facing=facing)
    face = body.facing(j)
    return {"track_id": "trk-1", "head": body.head(j), "shoulder_mid": body.shoulder_mid(j), "facing": face,
            "target": body.task_point(j, face)[0], "task": True}


def fixture(origin, ref=(0.0, 1.0, 0.0)):
    return {"aim_origin_m": list(origin), "horizontal_world_reference": list(ref),
            "range": scurve.FICTIONAL_PROFILE["range"], "usable": True}


class SCurveTest(unittest.TestCase):
    def test_fictional_profile_times(self):
        self.assertAlmostEqual(scurve.scurve_time(4.6, 30, 100, 400), 0.717, places=2)
        self.assertAlmostEqual(scurve.scurve_time(30, 30, 100, 400), 1.550, places=2)
        self.assertAlmostEqual(scurve.scurve_time(180, 30, 100, 400), 6.550, places=2)

    def test_shared_parameter_matches_single_axis_and_takes_the_tighter_axis(self):
        axes = scurve.FICTIONAL_PROFILE["axes"]
        self.assertAlmostEqual(scurve.move_time((4.6, 0.0), axes), scurve.scurve_time(4.6, 30, 100, 400), places=6)
        both = scurve.move_time((30.0, 10.0), axes)
        self.assertAlmostEqual(both, scurve.scurve_time(30, 30, 100, 400), places=6)
        self.assertEqual(scurve.move_time((0.0, 0.0), axes), 0.0)


class JointsTest(unittest.TestCase):
    GEOMETRIES = [((0, 0, 2.5), (1.0, 0.7, 0.6), (0.3, 1, 0)), ((2, 1, 2.4), (0.5, 3.0, 0.8), (-1, 0, 0)),
                  ((0, 0, 2.5), (-2.0, -0.4, 1.0), (0, 1, 0))]

    def test_aim_py_convention_is_negated(self):
        for origin, target, ref in self.GEOMETRIES:
            a = eye.aim.solve_aim(origin, target, ref)
            pan, tilt = joints.room_to_joints(target, origin, ref)
            self.assertAlmostEqual(pan, -a.pan_deg, places=6)
            self.assertAlmostEqual(tilt, -a.tilt_deg, places=6)

    def test_compile_target_convention_is_tilt_minus_90(self):
        for origin, target, ref in self.GEOMETRIES:
            f, l, u = joints.basis(ref)
            d = [target[i] - origin[i] for i in range(3)]
            local = [sum(d[i] * axis[i] for i in range(3)) for axis in (f, l, u)]
            tilt_compile = math.degrees(math.atan2(math.hypot(local[0], local[1]), -local[2]))  # fixture_execution.py
            self.assertAlmostEqual(joints.room_to_joints(target, origin, ref)[1], tilt_compile - 90.0, places=6)

    def test_direction_round_trips(self):
        for origin, target, ref in self.GEOMETRIES:
            pan, tilt = joints.room_to_joints(target, origin, ref)
            d = joints.direction(pan, tilt, ref)
            want = [target[i] - origin[i] for i in range(3)]
            n = math.sqrt(sum(c * c for c in want))
            for i in range(3):
                self.assertAlmostEqual(d[i], want[i] / n, places=9)

    def test_range_route_and_rounding(self):
        rng = scurve.FICTIONAL_PROFILE["range"]
        self.assertTrue(joints.in_range(170.0, -80.0, rng))
        self.assertFalse(joints.in_range(171.0, 0.0, rng))
        self.assertFalse(joints.route_ok((170.0, 0.0), (-170.0, 0.0)))  # no wrap: 340 degrees the long way
        self.assertTrue(joints.route_ok((90.0, 0.0), (-89.0, 0.0)))
        self.assertEqual((joints.to_cdeg(0.125), joints.to_cdeg(-0.125), joints.to_cdeg(12.344)), (13, -13, 1234))


class EyeTest(unittest.TestCase):
    def setUp(self):
        self.person = person_at(0.0, 0.0)  # facing +y

    def _behind(self, degrees_behind):
        """A fixture 3 m away horizontally, `degrees_behind` past the reader's side-on line, 2.5 m up."""
        a = math.radians(degrees_behind)
        return fixture((3.0 * math.cos(a), -3.0 * math.sin(a), 2.5))

    def test_in_front_is_never_admitted(self):
        for d in (-0.5, -5.0, -50.0):
            c = eye.evaluate("F", self._behind(d), self.person, self.person["target"], lit_for_them=False)
            self.assertFalse(c.ok)
            self.assertEqual(c.reason, "in_front_of_reader")

    def test_beside_you_is_admitted_the_owners_sofa(self):
        for d in (0.5, 1.2, 2.8):  # F3 and F2 on the real sofa sat 1.2 and 2.8 degrees behind side-on
            self.assertTrue(eye.evaluate("F", self._behind(d), self.person, self.person["target"], False).ok, d)

    def test_a_lit_light_is_kept_until_five_degrees_past_side_on(self):
        kept = eye.evaluate("F", self._behind(-4.0), self.person, self.person["target"], lit_for_them=True)
        self.assertTrue(kept.ok)
        dropped = eye.evaluate("F", self._behind(-6.0), self.person, self.person["target"], lit_for_them=True)
        self.assertFalse(dropped.ok)

    def test_a_side_on_jitter_cant_flip_a_lit_light(self):
        """The den simulation's F3 flip: a 1 cm move took F3 from 0.48 behind to 0.46 past side-on."""
        for d in (0.48, -0.46):
            self.assertTrue(eye.evaluate("F3", self._behind(d), self.person, self.person["target"], True).ok)

    def test_no_facing_means_no_spot(self):
        c = eye.evaluate("F", self._behind(30.0), dict(self.person, facing=None), self.person["target"], False)
        self.assertEqual(c.reason, "no_facing")

    def test_behind_and_above_beats_nearer_at_the_side(self):
        behind = eye.evaluate("B", fixture((0.3, -2.5, 2.5)), self.person, self.person["target"], False)
        side = eye.evaluate("S", self._behind(2.0), self.person, self.person["target"], False)
        self.assertGreater(behind.score, side.score)

    def test_path_through_own_head_is_caught(self):
        origin, ref = (0.0, -3.0, 2.5), (0.0, 1.0, 0.0)
        head = self.person["head"]
        above = joints.room_to_joints((0.0, 1.0, 2.0), origin, ref)   # the centre passes over the head
        below = joints.room_to_joints((0.0, 0.6, 0.7), origin, ref)   # and under it: the sweep crosses it
        ok, closest = eye.path_clear(origin, ref, above, below, 4.0, head)
        self.assertFalse(ok)
        self.assertLess(closest, eye.HEAD_CLEARANCE_M)
        beside = joints.room_to_joints((0.9, 0.6, 0.7), origin, ref)
        self.assertTrue(eye.path_clear(origin, ref, below, beside, 4.0, head)[0])


class SpotLightTest(unittest.TestCase):
    def test_lowest_level_that_reaches_the_target_codex_19(self):
        # Pick a full value where one level gives just under 300 and the next just over.
        fractions = [spot_light.dimming_fraction(b / 254) for b in spot_light.LEVELS]
        k = 6
        full = 299.0 / fractions[k]
        level, value, short = spot_light.level_for(full, 300.0)
        self.assertGreaterEqual(value, 300.0)
        self.assertEqual(level, spot_light.LEVELS[k + 1] / 254)
        self.assertEqual(short, 0.0)

    def test_short_lights_at_full_and_reports_it(self):
        self.assertEqual(spot_light.level_for(139.0, 300.0), (1.0, 139.0, 161.0))

    def test_matches_homes_lit_model(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        model = os.path.join(REPO, "app", "src", "home-3d", "lit-model.mjs").replace("\\", "/")
        script = ("import {candelaFromLumens, spotHalfAngle, litDimmingFraction} from 'file:///%s';"
                  "console.log(JSON.stringify([candelaFromLumens(450, spotHalfAngle(20)), litDimmingFraction(0.37),"
                  " litDimmingFraction(0.02)]))" % model.lstrip("/"))
        got = json.loads(subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                                        check=True).stdout)
        mine = [spot_light.candela_from_lumens(450, spot_light.spot_half_angle(20)), spot_light.dimming_fraction(0.37),
                spot_light.dimming_fraction(0.02)]
        for a, b in zip(got, mine):
            self.assertAlmostEqual(a, b, places=9)


class BodyTest(unittest.TestCase):
    def test_seated_after_two_seconds_and_standing_ends_it_at_once(self):
        samples = [(t / 4.0, skeleton(0, 0)) for t in range(0, 12)]
        self.assertFalse(body.seated(samples[:6], 1.25))
        self.assertTrue(body.seated(samples, 2.75))
        stood = samples + [(3.0, skeleton(0, 0, seated=False))]
        self.assertFalse(body.seated(stood, 3.0, was_seated=True))
        self.assertFalse(body.seated(samples, 6.0, was_seated=True))  # no sample for over 3 s

    def test_shifting_along_the_sofa_stays_seated(self):
        samples = [(t / 4.0, skeleton(0, 0)) for t in range(0, 12)]
        shifting = samples + [(3.0 + k / 4.0, skeleton(0.1 * k, 0)) for k in range(1, 5)]  # 0.4 m/s along it
        self.assertTrue(body.seated(shifting, 4.0, was_seated=True))
        self.assertFalse(body.seated(shifting, 4.0, was_seated=False))  # but you don't sit down at that speed

    def test_moving_is_not_seated(self):
        samples = [(t / 4.0, skeleton(t * 0.1, 0)) for t in range(0, 12)]  # 0.4 m/s
        self.assertFalse(body.seated(samples, 2.75))

    def test_facing_from_shoulders_and_the_nose_wins_a_disagreement(self):
        j = skeleton(0, 0, facing=(1.0, 0.0))
        self.assertAlmostEqual(body.facing(j)[0], 1.0)
        swapped = dict(j, left_shoulder=j["right_shoulder"], right_shoulder=j["left_shoulder"])
        self.assertAlmostEqual(body.facing(swapped)[0], 1.0)
        self.assertIsNone(body.facing({k: v for k, v in j.items() if "shoulder" not in k}))

    def test_task_point_sources(self):
        j = skeleton(0, 0)
        face = body.facing(j)
        self.assertEqual(body.task_point(j, face)[1], "wrists")
        self.assertEqual(body.task_point(skeleton(0, 0, wrists=False), face)[1], "shoulders")
        bare = {"left_eye": [0, 0, 1.2, 0.9]}
        self.assertEqual(body.task_point(bare, None, seat_mark=(0.2, 0.0, 0.75))[1], "seat_mark")
        self.assertEqual(body.task_point(bare, None, seat_mark=(3.0, 0.0, 0.75)), (None, None))

    def test_page_turn_codex_14_gives_no_move_and_a_held_shift_gives_one(self):
        f = body.ShiftFilter()
        f.update(0.0, (0, 0, 0.7))
        for k, dx in enumerate([0, 0, .15, .15, .15, 0, 0, 0, 0, 0]):
            self.assertIsNone(f.update(1.0 + k * 0.2, (dx, 0, 0.7)))
        g = body.ShiftFilter()
        g.update(0.0, (0, 0, 0.7))
        moved = [g.update(1.0 + k * 0.2, (0.25, 0, 0.7)) for k in range(10)]
        self.assertEqual(sum(1 for m in moved if m), 1)
        self.assertAlmostEqual(g.aim[0], 0.25)


class ShareTest(unittest.TestCase):
    def setUp(self):
        self.reader = person_at(0.0, 0.0)  # facing +y; fixtures with y < 0 are behind
        self.fixtures = {"F1": fixture((0.3, -2.0, 2.5)), "F2": fixture((-1.5, -2.5, 2.5)),
                         "F3": fixture((1.0, 3.0, 2.5), (0, -1, 0)), "F4": fixture((-1.0, 3.5, 2.5), (0, -1, 0))}
        self.art = [{"label_id": "painting-a", "point": (2.0, 5.0, 1.5), "front": (0.0, -1.0, 0.0)},
                    {"label_id": "painting-b", "point": (-2.0, 5.0, 1.5), "front": (0.0, -1.0, 0.0)}]

    def test_one_light_on_the_book_the_others_on_art(self):
        jobs, _ = share.allocate(self.fixtures, [self.reader], self.art, {})
        kinds = sorted(j["kind"] for j in jobs.values())
        self.assertEqual(kinds, ["accent", "accent", "task"])
        self.assertEqual([j["who"] for j in jobs.values() if j["kind"] == "task"], ["trk-1"])
        self.assertEqual(len({j["who"] for j in jobs.values()}), 3)

    def test_a_reader_takes_their_best_light_from_art_and_the_art_moves(self):
        current = {"F1": {"kind": "accent", "who": "painting-a"}}
        jobs, _ = share.allocate(self.fixtures, [self.reader], self.art, current)
        self.assertEqual(jobs["F1"]["kind"], "task")
        self.assertIn("painting-a", [j["who"] for j in jobs.values()])

    def test_no_labels_means_the_free_lights_rest(self):
        jobs, _ = share.allocate(self.fixtures, [self.reader], [], {})
        self.assertEqual(list(jobs.values())[0]["kind"], "task")
        self.assertEqual(len(jobs), 1)

    def test_art_beam_never_through_a_seated_head_and_never_from_behind_the_piece(self):
        head = (1.5, 4.0, 1.9)  # someone sitting right under the beam to painting-a
        jobs, notes = share.allocate({"F3": self.fixtures["F3"]}, [], self.art[:1], {}, seated_heads=[head])
        self.assertEqual(jobs, {})
        self.assertIn("through_a_seated_head", [n["reason"] for n in notes])
        behind = [{"label_id": "x", "point": (2.0, 5.0, 1.5), "front": (0.0, 1.0, 0.0)}]
        _, notes = share.allocate({"F3": self.fixtures["F3"]}, [], behind, {})
        self.assertIn("behind_the_piece", [n["reason"] for n in notes])

    def _beside(self, degrees_behind, distance):
        a = math.radians(degrees_behind)
        return fixture((distance * math.cos(a), -distance * math.sin(a), 2.5))

    def test_two_lights_about_equally_beside_you_the_brighter_wins(self):
        """The owner's sofa: F2 2.8 deg behind at 2.7 m, F3 1.2 deg behind at 2.0 m. F3 is nearer, so brighter."""
        fixtures = {"F2": self._beside(2.8, 2.6), "F3": self._beside(1.2, 1.6)}
        jobs, _ = share.allocate(fixtures, [self.reader], [], {})
        self.assertEqual(list(jobs), ["F3"])

    def test_clearly_more_behind_you_wins_even_if_dimmer_goals_q2(self):
        fixtures = {"BEHIND": self._beside(40.0, 3.5), "NEAR": self._beside(1.0, 1.6)}
        jobs, _ = share.allocate(fixtures, [self.reader], [], {})
        self.assertEqual(list(jobs), ["BEHIND"])
        self.assertLess(share.brightness(fixtures["BEHIND"], self.reader["target"]),
                        share.brightness(fixtures["NEAR"], self.reader["target"]))

    def test_a_lit_light_keeps_its_job_over_a_brighter_near_tie(self):
        fixtures = {"F2": self._beside(2.8, 2.6), "F3": self._beside(1.2, 1.6)}
        jobs, _ = share.allocate(fixtures, [self.reader], [], {"F2": {"kind": "task", "who": "trk-1"}})
        self.assertEqual(list(jobs), ["F2"])

    def test_a_kept_job_stays_put(self):
        jobs, _ = share.allocate(self.fixtures, [self.reader], [], {"F2": {"kind": "task", "who": "trk-1"}})
        self.assertEqual(jobs["F2"]["kind"], "task")

    def test_art_from_labels(self):
        doc = {"labels": [
            {"id": "p1", "name": "small", "kind": "picture", "status": "confirmed",
             "box": {"centre_m": [1, 5, 1.5], "size_m": [0.4, 0.04, 0.3], "yaw_deg": 180}},
            {"id": "p2", "name": "big", "kind": "picture", "status": "confirmed",
             "box": {"centre_m": [3, 5, 1.5], "size_m": [1.2, 0.04, 0.9], "yaw_deg": 180}},
            {"id": "p3", "kind": "picture", "status": "suggested", "box": {"centre_m": [0, 0, 0], "size_m": [1, 1, 1]}},
            {"id": "d", "kind": "table", "status": "confirmed", "box": {"centre_m": [0, 0, 0], "size_m": [1, 1, 1]}}]}
        pieces = share.art_from_labels(doc)
        self.assertEqual([p["label_id"] for p in pieces], ["p2", "p1"])
        self.assertAlmostEqual(pieces[0]["point"][1], 5 - 0.02)  # the front face faces -y at yaw 180


class SituationTest(unittest.TestCase):
    def test_activity_order(self):
        self.assertEqual(situation.activity(None, False, 0, 0), "unknown")
        self.assertEqual(situation.activity(False, True, 1, 1), "vacant")
        self.assertEqual(situation.activity(True, True, 1, 1), "gaming")
        self.assertEqual(situation.activity(True, False, 1, 1), "task")
        self.assertEqual(situation.activity(True, False, 0, 1), "sitting")
        self.assertEqual(situation.activity(True, False, 0, 1, qwen_ok=False), "unknown")
        self.assertEqual(situation.activity(True, None, 0, 0), "unknown")
        self.assertEqual(situation.activity(True, False, 0, 0), "present")

    def test_part_of_day(self):
        self.assertEqual(situation.part_of_day(8 * 3600), "day")
        self.assertEqual(situation.part_of_day(20 * 3600), "evening")
        self.assertEqual(situation.part_of_day(2 * 3600), "night")
        self.assertEqual(situation.part_of_day(2 * 3600, utc_offset_h=-8), "evening")


def facts_line(t, activity, people="one"):
    stamp = "1970-01-01T00:%02d:%02d.000Z" % (t // 60, t % 60)
    return {"kind": "facts", "t": t, "data": {
        "outcome": "completed", "observed_at": stamp, "available_at": stamp, "request_id": "r%d" % t,
        "observation": {"status": "valid", "facts": {"people_visible": people, "activity_hypothesis": activity}}}}


class NeedV1Test(unittest.TestCase):
    def _run(self, answers, config=None):
        policy, out = need_v1.make_policy(config), []
        for t, activity in answers:
            out += policy.handle(facts_line(t, activity))
        return policy, out

    def test_hobby_engages_with_its_kind(self):
        policy, out = self._run([(10, "hobby"), (12, "hobby")])
        self.assertEqual(out[-1]["data"]["focused_engagement"]["state"], "engaged")
        self.assertEqual(out[-1]["data"]["focused_engagement"]["kind"], "hobby")

    def test_craft_counts_as_a_hobby(self):
        _, out = self._run([(10, "craft"), (12, "craft")])
        self.assertEqual(out[-1]["data"]["focused_engagement"]["kind"], "hobby")

    def test_phone_patience_is_a_setting(self):
        policy, _ = self._run([(10, "reading"), (12, "reading"), (14, "phone_use"), (25, "phone_use")],
                              {"phone_patience_s": 10})
        self.assertFalse(policy.engaged)

    def test_unclear_does_not_restart_the_patience_clock_codex_15(self):
        answers = [(10, "reading"), (12, "reading"), (14, "phone_use"), (24, "unclear"), (34, "phone_use"), (44, "phone_use")]
        policy, _ = self._run(answers)
        self.assertFalse(policy.engaged)  # 14 + 30 = 44
        old = __import__("need").make_policy()
        for t, a in answers:
            old.handle(facts_line(t, a))
        self.assertTrue(old.engaged)  # v0: the unclear answer at 24 restarts the clock

    def test_need_v0_is_untouched(self):
        import need
        self.assertEqual(need.VERSION, "need-v0")
        self.assertEqual(need.sample(facts_line(1, "hobby")["data"])["status"], "idle")


class LearnTest(unittest.TestCase):
    def _record(self, seq, before, after):
        return {"seq": seq, "entity_id": "light.lamp", "at": seq, "before": before, "after": after}

    def test_halfway_on_brightness_and_warmth(self):
        learner = learn.Learner()
        row, why = learner.apply(self._record(1, {"on": True, "brightness": 200, "kelvin": 4000},
                                              {"on": True, "brightness": 100, "kelvin": 2000}), "sitting/evening", 1, True, True)
        self.assertIsNone(why)
        self.assertEqual(row["brightness"], 150)
        self.assertEqual(row["kelvin"], round(1e6 / ((250 + 500) / 2)))

    def test_on_off_flips_only_after_two_in_a_row(self):
        learner = learn.Learner()
        on, off = {"on": True, "brightness": 200, "kelvin": 2700}, {"on": False}
        learner.apply(self._record(1, on, off), "present/day", 1, True, True)
        self.assertTrue(learner.scene("present/day", "light.lamp")["on"])
        learner.apply(self._record(2, on, off), "present/day", 1, True, True)
        self.assertFalse(learner.scene("present/day", "light.lamp")["on"])

    def test_who_it_does_not_learn_from(self):
        learner, r = learn.Learner(), self._record(5, {"on": True, "brightness": 1}, {"on": True, "brightness": 2})
        self.assertEqual(learner.apply(r, "task/night", 2, True, True)[1], "people_2")
        self.assertEqual(learner.apply(dict(r, seq=6), "task/night", 1, False, True)[1], "coordinator_off")
        self.assertEqual(learner.apply(dict(r, seq=7), "unknown/night", 1, True, True)[1], "situation_unknown")
        self.assertEqual(learner.apply(dict(r, seq=8), "task/night", 1, True, True, own_fade=True)[1], "own_fade")
        self.assertEqual(learner.apply(dict(r, seq=9), "task/night", 1, True, False)[1], "vacant")
        learner.apply(dict(r, seq=10), "task/night", 1, True, True)
        self.assertEqual(learner.apply(dict(r, seq=10), "task/night", 1, True, True)[1], "duplicate")

    def test_back_and_forth_settles_in_between(self):
        learner, values = learn.Learner(), []
        for seq in range(10):
            target = 200 if seq % 2 == 0 else 100
            row, _ = learner.apply(self._record(seq, {"on": True, "brightness": 150}, {"on": True, "brightness": target}),
                                   "sitting/day", 1, True, True)
            values.append(row["brightness"])
        self.assertTrue(all(100 < v < 200 for v in values[2:]))

    def test_save_load_and_clear(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "learned.json")
            learner = learn.Learner()
            learner.apply(self._record(1, {"on": True, "brightness": 100}, {"on": True, "brightness": 50}), "task/day", 1, True, True)
            learn.save(path, learner)
            self.assertEqual(learn.load(path).scene("task/day", "light.lamp")["brightness"], 75)
            self.assertEqual(learn.clear(path, "20261112").entries, {})
            self.assertTrue(os.path.exists(os.path.join(folder, "learned-20261112.json")))
            self.assertFalse(os.path.exists(path))
        finally:
            shutil.rmtree(folder)


class BulbsTest(unittest.TestCase):
    def test_nothing_learned_sends_nothing(self):
        planner = bulbs.BulbPlanner(["light.a", "light.b"])
        self.assertIsNone(planner.plan(0, "sitting/evening", learn.Learner(), on=True))

    def test_learned_light_only_then_renewal_then_explicit_empty_plan(self):
        learner = learn.Learner({"sitting/evening": {"light.a": {"on": True, "brightness": 90, "kelvin": 2200}}})
        planner = bulbs.BulbPlanner(["light.a", "light.b"])
        first = planner.plan(0, "sitting/evening", learner, on=True)
        self.assertEqual(list(first["data"]["scenes"]), ["light.a"])
        self.assertEqual(first["data"]["generation"], 1)
        self.assertIsNone(planner.plan(5, "sitting/evening", learner, on=True))
        renew = planner.plan(10, "sitting/evening", learner, on=True)
        self.assertEqual(renew["data"]["generation"], 1)
        cleared = planner.plan(11, "present/evening", learner, on=True)
        self.assertEqual((cleared["data"]["scenes"], cleared["data"]["generation"]), ({}, 2))
        self.assertIsNone(planner.plan(30, "present/evening", learner, on=True))

    def test_off_sends_nothing_and_on_starts_clean(self):
        learner = learn.Learner({"task/day": {"light.a": {"on": True, "brightness": 90, "kelvin": 2200}}})
        planner = bulbs.BulbPlanner(["light.a"])
        planner.plan(0, "task/day", learner, on=True)
        self.assertIsNone(planner.plan(1, "task/day", learner, on=False))
        again = planner.plan(2, "task/day", learner, on=True)
        self.assertEqual(again["data"]["generation"], 2)


if __name__ == "__main__":
    unittest.main()

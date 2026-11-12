"""End-to-end tests for the den lighting coordinator's core, and report.py (coordinator plan, step 1).

    python -B -m unittest discover -s tools/den-core -p "test_den_*.py"
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import den_coordinator  # noqa: E402
import learn  # noqa: E402
import report  # noqa: E402
import scurve  # noqa: E402
from test_den_parts import skeleton  # noqa: E402

READER_XY = (1.2, 1.0)  # facing +y; F2 sits behind


def config(**extra):
    room = report.demo_room()
    return dict({"fixtures": room["fixtures"], "lights": ["light.a", "light.b"], "utc_offset_h": 0}, **extra)


def reading_state(t, kind="reading", state="engaged"):
    return {"kind": "person_state", "t": t, "data": {
        "track_id": "trk-1", "state_id": "room@%.3f" % t,
        "focused_engagement": {"state": state, "kind": kind},
        "field_support": {"focused_engagement": {"support_status": "observed", "last_support_capture_ts": t}},
        "sources": {"focused_engagement": [{"origin": "qwen", "ref": "r1"}]}}}


def people(t, *bodies):
    return {"kind": "people", "t": t, "data": {"tracks": [
        {"track_id": tid, "confirmed": True, "joints3d": j, "capture_ts": t - 0.8} for tid, j in bodies]}}


class Run:
    def __init__(self, **extra):
        self.c = den_coordinator.make_policy(config(**extra))
        self.out = []

    def send(self, kind, t, data=None):
        got = self.c.handle({"kind": kind, "t": t, "data": data or {}})
        self.out += got
        return got

    def sit(self, t0, seconds, xy=READER_XY, seated=True, extra=()):
        for k in range(int(seconds * 4)):
            t = t0 + k / 4.0
            self.out += self.c.handle(people(t, ("trk-1", skeleton(*xy, seated=seated)), *extra))
            if k % 4 == 0:
                self.send("tick", t)
        return t0 + seconds

    def kinds(self, kind):
        return [o["data"] for o in self.out if o["kind"] == kind]

    def intents(self, action):
        return [i for i in self.kinds("intent") if i["action"] == action]


class CoordinatorTest(unittest.TestCase):
    def on(self, run, t=0.0):
        run.send("ha", t, {"on": True, "occupied": True, "game_running": False, "qwen_ok": True})

    def test_pre_aim_then_reading_lights_the_light_behind_the_shoulder(self):
        run = Run()
        self.on(run)
        t = run.sit(0.0, 3.0)
        pre = [i for i in run.intents("aim_move") if i["fixture_id"] == "F2"]
        self.assertTrue(pre)
        self.assertTrue(all(i["level"] == 0 and not i["lit_during_move"] for i in pre))  # pre-aim moves dark
        run.send("person_state", t, reading_state(t)["data"])
        lit = [i for i in run.intents("aim_move") if i["level"] > 0]
        self.assertEqual([i["fixture_id"] for i in lit], ["F2"])
        decision = run.kinds("decision")[-1]
        self.assertEqual(decision["spots"]["F2"]["job"], {"kind": "task", "who": "trk-1"})
        self.assertEqual(decision["situation"]["activity"], "task")
        self.assertIn("trk-1", decision["evidence"])
        run.send("tick", t + 1)
        self.assertTrue(run.intents("keep_lit"))
        self.assertTrue(run.kinds("kept"))

    def test_needs_room_level_record_stands_for_a_lone_person(self):
        run = Run()
        self.on(run)
        t = run.sit(0.0, 3.0)
        run.send("person_state", t, dict(reading_state(t)["data"], track_id="room"))
        self.assertTrue([i for i in run.intents("aim_move") if i["level"] > 0])

    def test_hobby_counts_and_phone_does_not(self):
        run = Run()
        self.on(run)
        t = run.sit(0.0, 3.0)
        run.send("person_state", t, reading_state(t, kind="hobby")["data"])
        self.assertTrue([i for i in run.intents("aim_move") if i["level"] > 0])
        other = Run()
        self.on(other)
        t = other.sit(0.0, 3.0)
        other.send("person_state", t, reading_state(t, kind="phone")["data"])
        self.assertFalse([i for i in other.intents("aim_move") if i["level"] > 0])

    def _reading(self, **extra):
        run = Run(**extra)
        self.on(run)
        t = run.sit(0.0, 3.0)
        run.send("person_state", t, reading_state(t)["data"])
        return run, t

    def test_standing_up_turns_it_off_on_the_next_sample(self):
        run, t = self._reading()
        before = len(run.out)
        run.out += run.c.handle(people(t + 0.25, ("trk-1", skeleton(*READER_XY, seated=False))))
        offs = [o["data"] for o in run.out[before:] if o["kind"] == "intent" and o["data"]["action"] == "aim_move"
                and o["data"]["fixture_id"] == "F2" and o["data"]["level"] == 0]
        self.assertTrue(offs)

    def test_a_second_person_turns_the_reading_light_off(self):
        run, t = self._reading()
        run.sit(t, 1.0, extra=[("trk-2", skeleton(3.0, 3.0, seated=False, facing=(-1.0, 0.0)))])
        f2 = [i for i in run.intents("aim_move") if i["fixture_id"] == "F2"]
        self.assertEqual(f2[-1]["level"], 0)

    def test_a_shift_glides_lit_by_default_and_dark_when_the_setting_says_so(self):
        for style, lit in (("lit", True), ("dark", False)):
            run, t = self._reading(settings={"move_style": style})
            before = len(run.intents("aim_move"))
            run.sit(t, 3.0, xy=(READER_XY[0] - 0.3, READER_XY[1]))
            moves = [i for i in run.intents("aim_move")[before:] if i["fixture_id"] == "F2" and i["level"] > 0]
            self.assertTrue(moves, style)
            self.assertEqual(moves[-1]["lit_during_move"], lit, style)

    def test_a_fixture_that_cant_move_lit_moves_dark(self):
        rooms = report.demo_room()["fixtures"]
        profile = dict(scurve.FICTIONAL_PROFILE, lit_motion=False)
        fixtures = {fid: dict(row, profile=profile) for fid, row in rooms.items()}
        run, t = self._reading(fixtures=fixtures)
        before = len(run.intents("aim_move"))
        run.sit(t, 3.0, xy=(READER_XY[0] - 0.3, READER_XY[1]))
        moves = [i for i in run.intents("aim_move")[before:] if i["fixture_id"] == "F2" and i["level"] > 0]
        self.assertTrue(moves)
        self.assertFalse(moves[-1]["lit_during_move"])

    def test_off_sends_nothing_and_writes_one_shadow_decision(self):
        run = Run()
        run.send("ha", 0.0, {"on": False, "occupied": True, "game_running": False})
        t = run.sit(0.0, 3.0)
        run.send("person_state", t, reading_state(t)["data"])
        run.sit(t, 2.0)
        self.assertEqual(run.kinds("intent"), [])
        self.assertEqual(run.kinds("bulb_plan"), [])
        shadows = run.kinds("decision")
        self.assertTrue(shadows)
        self.assertTrue(all(d["actuation"] == "shadow" for d in shadows))
        self.assertLessEqual(len(shadows), 4)  # once per change of what it would do, not every frame

    def test_stop_latches_until_resume(self):
        run, t = self._reading()
        run.send("stop", t)
        self.assertEqual(sorted(i["fixture_id"] for i in run.intents("stop")), ["F1", "F2", "F3", "F4"])
        before = len(run.intents("aim_move"))
        run.sit(t, 2.0)
        self.assertEqual(len(run.intents("aim_move")), before)
        run.send("resume", t + 2)
        run.sit(t + 2, 1.0)
        self.assertTrue([i for i in run.intents("aim_move")[before:] if i["level"] > 0])

    def test_a_hand_held_real_fixture_is_left_alone(self):
        run, t = self._reading()
        run.send("fixture", t, {"fixture_id": "F2", "real": True, "status": "installed", "hand": True})
        run.sit(t, 1.0)
        last = run.kinds("decision")[-1]
        self.assertNotIn("F2", last["jobs"])
        self.assertIn("F4", last["jobs"])  # the next light behind the reader takes over

    def test_on_resumes_only_what_offs_own_automation_stopped(self):
        rows = report.demo_room()["fixtures"]
        run = Run(fixtures={"F2": dict(rows["F2"], real=True, stopped=True, stopped_by="coordinator_off"),
                            "F4": dict(rows["F4"], real=True, stopped=True, stopped_by="hand")})
        run.send("ha", 0.0, {"on": True, "occupied": True, "game_running": False})
        self.assertEqual([i["fixture_id"] for i in run.intents("resume")], ["F2"])   # the owner's own Stop on F4 holds

    def test_a_latched_stop_makes_a_fixture_unusable(self):
        rows = report.demo_room()["fixtures"]
        run, t = self._reading(fixtures={"F2": dict(rows["F2"], real=True)})
        run.send("fixture", t, {"fixture_id": "F2", "status": "installed", "stopped": True, "stopped_by": "hand"})
        run.sit(t, 1.0)
        self.assertNotIn("F2", run.kinds("decision")[-1]["jobs"])

    def test_actuation_says_what_was_driven(self):
        run, _ = self._reading()
        self.assertEqual(run.kinds("decision")[-1]["actuation"], "virtual")   # no real fixture: Home only
        rows = report.demo_room()["fixtures"]
        real = Run(fixtures={fid: dict(row, real=(fid == "F2")) for fid, row in rows.items()})
        self.on(real)
        real.send("fixture", 0.0, {"fixture_id": "F2", "status": "installed"})
        t = real.sit(0.0, 3.0)
        real.send("fixture", t, {"fixture_id": "F2", "status": "installed"})   # a fresh status
        real.send("person_state", t, reading_state(t)["data"])
        self.assertEqual(real.kinds("decision")[-1]["actuation"], "live")
        off = Run()
        off.send("ha", 0.0, {"on": False, "occupied": True, "game_running": False})
        off.sit(0.0, 3.0)
        self.assertEqual(off.kinds("decision")[-1]["actuation"], "shadow")

    def test_dim_falls_back_to_dark_without_dim_motion(self):
        rows = report.demo_room()["fixtures"]
        profile = dict(scurve.FICTIONAL_PROFILE, dim_motion=False)
        run, t = self._reading(fixtures={fid: dict(row, profile=profile) for fid, row in rows.items()},
                               settings={"move_style": "dim"})
        before = len(run.intents("aim_move"))
        run.sit(t, 3.0, xy=(READER_XY[0] - 0.3, READER_XY[1]))
        moves = [i for i in run.intents("aim_move")[before:] if i["fixture_id"] == "F2" and i["level"] > 0]
        self.assertTrue(moves)
        self.assertFalse(moves[-1]["lit_during_move"])

    def test_learning_then_a_bulb_plan(self):
        run = Run()
        self.on(run)
        t = run.sit(0.0, 3.0)
        record = {"seq": 7, "entity_id": "light.a", "at": t, "before": {"on": True, "brightness": 200, "kelvin": 2700},
                  "after": {"on": True, "brightness": 100, "kelvin": 2700}}
        run.send("ha", t, {"last_correction": record})
        learned = run.kinds("learned")[-1]
        self.assertIsNone(learned["skipped"])
        self.assertEqual(learned["learned"]["brightness"], 150)
        plan = run.kinds("bulb_plan")[-1]
        self.assertEqual(plan["data"]["scenes"]["light.a"]["brightness"], 150)
        run.send("ha", t + 1, {"last_correction": record})  # the same record again is not counted twice
        self.assertEqual(len(run.kinds("learned")), 1)

    def test_gaming_is_its_own_situation(self):
        run = Run()
        run.send("ha", 0.0, {"on": True, "occupied": True, "game_running": True})
        run.sit(0.0, 3.0)
        self.assertEqual(run.kinds("decision")[-1]["situation"]["activity"], "gaming")

    def test_pre_aim_off_means_no_move_until_reading(self):
        run = Run(settings={"pre_aim": False})
        self.on(run)
        run.c.handle(people(0.0, ("trk-1", skeleton(3.0, 3.0, seated=False))))
        run.out += run.c.handle(people(0.25, ("trk-1", skeleton(3.0, 3.0, seated=False))))
        self.assertEqual(run.intents("aim_move"), [])

    def test_art_gets_the_free_lights(self):
        labels = {"labels": [{"id": "painting", "name": "art", "kind": "picture", "status": "confirmed",
                              "box": {"centre_m": [1.0, 4.4, 1.5], "size_m": [0.8, 0.04, 0.6], "yaw_deg": 180}}]}
        run, t = self._reading()
        run.send("labels", t, labels)
        last = run.kinds("decision")[-1]
        kinds = sorted(j["kind"] for j in last["jobs"].values())
        self.assertIn("accent", kinds)
        self.assertEqual(kinds.count("task"), 1)

    def test_each_fixture_call_carries_what_the_shadow_log_needs(self):
        run, _ = self._reading()
        call = [i for i in run.intents("aim_move") if i["level"] > 0][-1]
        for field in ("decision_id", "predicted_s", "profile_sha256", "pose_age_s", "activity_valid_until"):
            self.assertIsNotNone(call[field], field)
        self.assertEqual(len(call["profile_sha256"]), 64)

    def test_a_desk_fixture_counts_only_during_the_desk_test(self):
        desk_f2 = dict(report.demo_room()["fixtures"]["F2"], real=True)
        run = Run(fixtures={"F2": desk_f2})
        run.send("fixture", 0.0, {"fixture_id": "F2", "status": "desk"})
        self.on(run)
        t = run.sit(0.0, 3.0)
        run.send("fixture", t, {"fixture_id": "F2", "status": "desk"})
        run.send("person_state", t, reading_state(t)["data"])
        self.assertFalse([i for i in run.intents("aim_move") if i["level"] > 0])
        run.send("desk", t, {"on": True, "until": t + 3 * 3600})
        run.send("fixture", t, {"fixture_id": "F2", "status": "desk"})
        self.assertTrue([i for i in run.intents("aim_move") if i["level"] > 0 and i["fixture_id"] == "F2"])

    def test_unknown_move_style_is_refused(self):
        run = Run()
        with self.assertRaises(ValueError):
            run.send("settings", 0.0, {"move_style": "wiggle"})


class ReportTest(unittest.TestCase):
    def test_demo(self):
        text = "\n".join(report.lines(report.demo_room()))
        self.assertIn("CHOSEN F2", text)
        self.assertIn("A page turn (15 cm out and back in 0.6 s): no move", text)
        self.assertIn("learned 55%", text)

    def test_real_room_reads_the_newest_fit_and_prints_no_coordinates(self):
        home = tempfile.mkdtemp()
        try:
            room = report.demo_room()
            fit = os.path.join(home, "private", "fits", "fit-a")
            os.makedirs(os.path.join(fit, "light"))
            model = {"fixtures": room["fixtures"], "aim_points": {"sofa-book": {"point_m": [1.234567, 1.0, 0.75]}},
                     "provenance": {"fixtures": {"status": "your registration export"}}}
            with open(os.path.join(fit, "light", "light-model.json"), "w") as f:
                json.dump(model, f)
            with open(os.path.join(fit, "transform.json"), "w") as f:
                json.dump({"facing_unit": [0.0, 1.0]}, f)
            buffer = io.StringIO()
            old = os.environ.get("DEN_SIM_HOME")
            os.environ["DEN_SIM_HOME"] = home
            try:
                with redirect_stdout(buffer):
                    report.main([])
            finally:
                if old is None:
                    del os.environ["DEN_SIM_HOME"]
                else:
                    os.environ["DEN_SIM_HOME"] = old
            text = buffer.getvalue()
            self.assertIn("your registration export", text)
            self.assertNotIn("1.234", text)
            self.assertNotIn("0.75", text)
        finally:
            shutil.rmtree(home)


if __name__ == "__main__":
    unittest.main()

"""The owner's own changes through the preview (plan 04b): each is recorded and, within den-core's gates, learned; the
parts it touched are the owner's until the next reason to act; and what was learned comes back at the next episode
(the spot) or the next cue (the room's glow), shown as learned from you, until Clear."""

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import coordinator_live
import preview
import seated as seated_rule
import sofa_check
import test_preview as base
import test_preview_people as people

WALL = datetime.datetime(2026, 11, 14, 20, 0, tzinfo=datetime.timezone.utc).timestamp()   # evening, at UTC+0
TWO = {"F3": base.FIXTURES["F3"], "F4": {"aim_origin_m": [.6, .2, 2.5], "horizontal_world_reference": [0, 1, 0]}}


class PreviewManualTest(unittest.TestCase):
    read, frame, seat = base.PreviewTest.read, base.PreviewTest.frame, base.PreviewTest.seat
    track = people.PreviewPeopleTest.track   # the reader t1, and a second person while self.guest is set

    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = tmp.name
        self.now, self.seq, self.encoded, self.n, self.guest = 100.0, 0, [], 0, None
        self.rule, self.rule_epoch = seated_rule.Published(), 1
        self.preview = self.make_preview()

    def make_preview(self, fixtures=None, **overrides):
        made = preview.Preview(self.core, binding=base.BINDING, fixtures=fixtures or base.FIXTURES, state=self.path,
                               clock=lambda: self.now, **{"utc_offset_h": 0, "threaded": False,
                                                          "append": self.encoded.append, **overrides})
        made.wall = lambda: WALL
        made.configure(base.INSTANT)
        return made

    # ---- helpers ------------------------------------------------------------------------------------------------

    def fixtures(self):
        return self.preview.latest["fixtures"]

    def lit(self):
        return [name for name, row in self.fixtures().items() if row["on"]]

    def frames(self, seconds, **track):
        for _ in range(round(seconds / .25)):
            self.now += .25
            self.track(**track)

    def light(self):
        """Seated, one yes, lit: the name of the one fixture lit."""
        self.seat()
        self.read()
        self.frames(3)
        self.assertEqual(len(self.lit()), 1, self.fixtures())
        return self.lit()[0]

    def stand(self):
        for _ in range(4):
            self.now += .5
            self.track(seated=False)

    def change(self, name, before, after, *, event_id=None, snapshot=None):
        self.n += 1
        latest = self.preview.latest
        data = {"schema": "den-manual-1", "event_id": event_id or "ev-%07d" % self.n, "fixture_id": name,
                "before": before, "after": after, "at_ms": int(WALL * 1000) + self.n,
                "snapshot": snapshot or {"epoch": latest["epoch"], "seq": latest["seq"]}}
        return self.preview.manual_change(data)

    def dim(self, name, level=None, **kw):
        row = self.fixtures()[name]
        return self.change(name, {"spotlight": {"brightness": row["level"]}},
                           {"spotlight": {"brightness": row["level"] / 2 if level is None else level}}, **kw)

    def moves(self, name):
        return [row for row in self.encoded if row["action"] == "aim_move" and row["fixture_id"] == name]

    def log(self):
        path = Path(self.path) / "coordinator" / "manual-events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def learned(self):
        return self.preview.state()[1]["learned_from_you"]

    # ---- the spot ------------------------------------------------------------------------------------------------

    def test_a_change_to_the_reading_light_is_recorded_learned_and_the_light_is_the_owners(self):
        name = self.light()
        sent = len(self.moves(name))
        status, reply = self.dim(name)
        self.assertEqual(status, 200)
        self.assertEqual((reply["recorded"], reply["learned"], reply["why_not"]), (True, True, None))
        self.assertEqual((reply["hand"]["parts"], reply["hand"]["ends"]), (["spot"], {"spot": "task_end"}))
        self.assertEqual((reply["epoch"], reply["from_seq"]), (self.preview.epoch, self.preview.latest["seq"]))
        row = self.fixtures()[name]
        self.assertEqual((row["reason"], row["on"], row["hand"]["event_id"]), ("hand", False, reply["event_id"]))
        [line] = self.log()
        self.assertEqual((line["key"], line["context"]["people"], line["why_not"]), ("task/evening", 1, None))
        spot = self.learned()["task/evening"]["spot"]
        self.assertLess(spot["lux_scale"], 1.0)   # dimmed: halfway from what the coordinator gave toward it
        self.assertEqual(spot["n"], 1)
        self.frames(3)
        self.assertEqual(self.lit(), [])   # den-live never takes it back while the task goes on
        self.assertEqual(len(self.moves(name)), sent, "den-live sends a held fixture nothing, not even a release")
        self.assertEqual(self.fixtures()[name]["reason"], "hand")

    def test_standing_up_ends_the_hold_and_the_next_episode_brings_the_learned_light_back(self):
        name = self.light()
        self.dim(name)
        taught = self.learned()["task/evening"]["spot"]["lux_scale"]
        self.stand()
        self.assertIsNone(self.fixtures()[name]["hand"])
        self.assertNotEqual(self.fixtures()[name]["reason"], "hand")
        name = self.light()
        row = self.fixtures()[name]
        geometry = self.preview.rows[name]
        expected = self.core.spot_light.level_for(self.core.share.brightness(geometry, row["target_m"]),
                                                  self.core.SPOT_TARGET * taught)[0]
        self.assertEqual(row["level"], expected)
        self.assertEqual(row["learned"], {"lux_scale": round(taught, 3), "offset_m": None, "n": 1})

    def test_the_owners_aim_comes_back_as_an_offset_from_the_page(self):
        name = self.light()
        page = list(self.preview.track["target"])
        aimed = [page[0] + .1, page[1], page[2]]
        status, reply = self.change(name, {"target": {"point": self.fixtures()[name]["target_m"]}},
                                    {"target": {"point": aimed, "normal": [0, 0, 1]}})
        self.assertTrue(reply["learned"])
        self.assertEqual(self.learned()["task/evening"]["spot"]["offset_m"], [.05, 0, 0])
        self.stand()
        name = self.light()
        page = self.preview.track["target"]
        offset = [a - b for a, b in zip(self.fixtures()[name]["target_m"], page)]
        for got, want in zip(offset, (.05, 0, 0)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertTrue(self.preview.latest_decision["proposal"]["learned"]["aim"])

    def test_aiming_the_light_somewhere_else_teaches_the_task_nothing(self):
        name = self.light()
        page = list(self.preview.track["target"])
        status, reply = self.change(name, {"target": {"point": self.fixtures()[name]["target_m"]}},
                                    {"target": {"point": [page[0] + 1.0, page[1], page[2] + 1.0]}})
        self.assertEqual((reply["recorded"], reply["learned"], reply["why_not"]), (True, False, "pointed_elsewhere"))
        self.assertEqual(reply["hand"]["parts"], ["spot"])   # it's still the owner's

    def test_a_held_task_light_is_the_task_light_so_nothing_else_lights_and_the_room_stays_dimmed(self):
        self.preview = self.make_preview(TWO)
        name = self.light()
        self.dim(name)
        self.frames(3)
        self.assertEqual(self.lit(), [])
        task_scale = self.preview.settings["ambient_task_scale"]
        self.assertEqual(self.preview.latest["ambient"]["goal_scale"], task_scale)
        self.assertTrue(self.preview.latest_decision["proposal"]["learned"]["served_by_hand"])
        self.change(name, {"spotlight": {"on": True}}, {"spotlight": {"on": False}})   # and now off, by hand
        self.frames(1)
        self.assertEqual(self.lit(), [])   # still served: they said no
        self.assertEqual(self.preview.latest["ambient"]["goal_scale"], 1.0)   # no task light, no dimmed room
        self.stand()
        self.assertEqual({row["hand"] for row in self.fixtures().values()}, {None})

    def test_a_light_put_on_for_the_owners_own_use_is_released_when_a_task_starts(self):
        self.preview = self.make_preview(TWO)
        self.seat()
        status, reply = self.change("F4", {"spotlight": {"on": False, "brightness": 0}},
                                    {"spotlight": {"on": True, "brightness": .5}})
        self.assertEqual((reply["learned"], reply["why_not"], reply["hand"]["ends"]),
                         (False, "not_the_task_light", {"spot": "task_start"}))
        self.frames(2)
        self.assertEqual(self.fixtures()["F4"]["reason"], "hand")
        self.read()
        self.frames(3)
        self.assertIsNone(self.fixtures()["F4"]["hand"])
        self.assertEqual(len(self.lit()), 1)

    # ---- the room's glow -----------------------------------------------------------------------------------------

    def test_the_glow_the_owner_set_while_sitting_comes_back_at_the_next_cue(self):
        self.seat()
        house = self.preview.latest["ambient"]["level"]
        status, reply = self.change("F3", {"radials": [{"number": 1, "brightness": house}]},
                                    {"radials": [{"number": 1, "brightness": .35}]})
        self.assertEqual((reply["learned"], reply["hand"]["ends"]), (True, {"radials": "next_cue"}))
        self.assertAlmostEqual(self.learned()["sitting/evening"]["room"]["level"], (house + .35) / 2)
        self.frames(1)
        self.assertEqual(self.preview.latest["ambient"]["goal_level"], house)   # not until the next cue
        self.assertEqual(self.fixtures()["F3"]["hand"]["parts"], ["radials"])
        self.read()
        self.frames(1)
        self.assertIsNone(self.fixtures()["F3"]["hand"])   # the task's cue: the zones are the coordinator's again
        self.read(people_visible="none")
        self.frames(1)
        ambient = self.preview.latest["ambient"]
        self.assertAlmostEqual(ambient["goal_level"], round((house + .35) / 2, 4))
        self.assertEqual(ambient["learned"]["n"], 1)

    def test_a_learned_warmth_follows_the_cue_and_never_jumps(self):
        """direction.Ambient with a learned goal and warmth: the warmth runs on a line in the cue's scale, from the
        warmth it had, and the next default cue leaves the learned warmth without a step. With neither, it's the
        direction's own curve."""
        import direction
        settings = {**direction.SETTINGS, "ambient_delay_s": 0.0}
        own, ambient = direction.Ambient(), direction.Ambient()
        own.direct(True, settings, 0.0)
        ambient.direct(True, settings, 0.0)
        self.assertEqual(own.published(settings, 2.0), ambient.published(settings, 2.0))   # nothing learned: the same
        learned = direction.Ambient()
        learned.direct(True, settings, 0.0, goal=.5, cct_k=2200)
        seen = [learned.published(settings, t / 4)["cct_k"] for t in range(0, 25)]
        self.assertEqual(seen[0], direction.AMBIENT_CCT_K[0])
        self.assertEqual(seen[-1], 2200)
        self.assertEqual(seen, sorted(seen, reverse=True))   # steadily warmer, no step back
        self.assertEqual(learned.published(settings, 6.0)["goal_cct_k"], 2200)
        learned.direct(False, settings, 10.0)   # the task ends: the direction's own restore
        after = [learned.published(settings, 10.0 + t / 4)["cct_k"] for t in range(0, 12)]
        self.assertEqual(after[0], 2200)   # no step at the cue: it starts from the learned warmth
        self.assertEqual(after[-1], direction.AMBIENT_CCT_K[0])
        self.assertEqual(after, sorted(after))   # and cools steadily over the restore

    # ---- the gates -----------------------------------------------------------------------------------------------

    def test_learning_waits_for_one_person_a_known_snapshot_and_the_coordinator_on(self):
        name = self.light()
        self.assertEqual(self.dim(name, snapshot={"epoch": "another-run", "seq": 1})[1]["why_not"],
                         "unknown_snapshot")
        name = self.lit()[0] if self.lit() else name
        self.preview.manual_resume({"fixture_id": name})
        self.frames(1)
        name = self.lit()[0]
        level = self.fixtures()[name]["level"]
        status, reply = self.change(name, {"spotlight": {"brightness": level - .5}}, {"spotlight": {"brightness": .1}})
        self.assertEqual(reply["why_not"], "before_mismatch")
        self.preview.manual_resume({"fixture_id": name})
        self.frames(1)
        old = dict(self.preview.latest)
        self.frames(31)   # the reply's validity has run out by now too: what matters is the snapshot's age
        self.assertEqual(self.dim(name, snapshot={"epoch": old["epoch"], "seq": old["seq"]})[1]["why_not"],
                         "stale_snapshot")
        self.assertEqual(self.learned(), {})
        self.assertEqual([line["learn"] for line in self.log()], [None, None, None])   # every one recorded

    def test_with_someone_else_there_nothing_is_learned_but_the_light_is_still_the_owners(self):
        name = self.light()
        self.guest = people.AWAY
        self.frames(.5)
        status, reply = self.dim(name)
        self.assertEqual((reply["recorded"], reply["learned"], reply["why_not"]), (True, False, "people_2"))
        self.assertEqual(reply["hand"]["parts"], ["spot"])

    def test_off_records_without_learning_or_holding_and_ends_every_hold(self):
        name = self.light()
        self.dim(name)
        self.assertTrue(self.preview.hands)
        self.preview.switch({"on": False})
        self.assertEqual(self.preview.hands, {})
        status, reply = self.dim(name, level=.2)
        self.assertEqual((reply["learned"], reply["why_not"], reply["hand"]), (False, "coordinator_off", None))

    # ---- retries, restarts, Resume, vacancy, Clear --------------------------------------------------------------

    def test_a_retry_gets_the_same_reply_and_a_restart_keeps_what_was_learned(self):
        name = self.light()
        level = self.fixtures()[name]["level"]
        data = {"schema": "den-manual-1", "event_id": "ev-retried", "fixture_id": name,
                "before": {"spotlight": {"brightness": level}}, "after": {"spotlight": {"brightness": level / 2}},
                "at_ms": int(WALL * 1000), "snapshot": {"epoch": self.preview.epoch, "seq": self.preview.latest["seq"]}}
        first = self.preview.manual_change(data)
        self.assertEqual(self.preview.manual_change(dict(data)), first)
        self.assertEqual(len(self.log()), 1)
        learned = self.learned()
        self.preview = self.make_preview()
        self.assertEqual(self.learned(), learned)
        status, reply = self.preview.manual_change(data)
        self.assertEqual((status, reply["recorded"], reply["learned"], reply["hand"]), (200, True, True, None))
        self.assertEqual(len(self.log()), 1)

    def test_resume_gives_the_fixture_back_and_it_lights_again(self):
        name = self.light()
        self.dim(name)
        status, reply = self.preview.manual_resume({"fixture_id": name})
        self.assertEqual((status, reply["released"]), (200, ["spot"]))
        self.frames(2)
        self.assertEqual(self.lit(), [name])
        self.assertIsNone(self.fixtures()[name]["hand"])
        self.assertEqual(self.preview.manual_resume({"fixture_id": "F9"})[0], 400)

    def test_an_empty_den_ends_a_hold(self):
        self.seat()
        self.change("F3", {"downward": {"on": False}}, {"downward": {"on": True, "brightness": .4}})
        self.assertIsNotNone(self.fixtures()["F3"]["hand"])
        self.now += 5
        self.preview.tick()   # a camera gap, nobody tracked for now: not yet an empty den
        self.assertIsNone(self.preview.track)
        self.assertIsNotNone(self.fixtures()["F3"]["hand"])
        self.now += preview.VACANT_S
        self.preview.tick()
        self.assertIsNone(self.fixtures()["F3"]["hand"])

    def test_clear_forgets_keeps_the_old_log_and_the_light_goes_back_to_the_page(self):
        name = self.light()
        self.dim(name)
        self.stand()
        name = self.light()
        self.assertIsNotNone(self.fixtures()[name]["learned"])
        status, reply = self.preview.learned_clear({})
        self.assertEqual((status, reply["fixtures"]), (200, "cleared"))
        self.assertEqual(self.learned(), {})
        self.assertEqual(self.log(), [])
        self.assertEqual(len(list((Path(self.path) / "coordinator").glob("manual-events-*.jsonl"))), 1)
        self.frames(1)
        self.assertIsNone(self.fixtures()[name]["learned"])

    # ---- the routes and the clock --------------------------------------------------------------------------------

    def test_the_routes_and_a_bad_body(self):
        routes = self.preview.routes()
        for path, handler in (("/manual", self.preview.manual_change), ("/manual/resume", self.preview.manual_resume),
                              ("/learned/clear", self.preview.learned_clear)):
            for prefix in ("/coordinator", "/coordinator/virtual"):
                self.assertEqual(routes[("POST", prefix + path)], handler)
        self.assertEqual(self.preview.manual_change({"schema": "den-manual-1"})[0], 400)
        self.assertEqual(self.change("F9", {"downward": {"on": False}}, {"downward": {"on": True}})[0], 400)

    def test_the_part_of_day_is_this_machines_own_and_any_if_it_cant_be_read(self):
        local = self.make_preview(utc_offset_h=None)
        expected = self.core.situation.part_of_day(WALL, __import__("time").localtime(WALL).tm_gmtoff / 3600.0)
        self.assertEqual(local._part_of_day(WALL), expected)
        with patch("preview.time.localtime", side_effect=OSError("no zone")):
            self.assertEqual(local._part_of_day(WALL), "any")
        self.assertEqual(self.preview._part_of_day(WALL), "evening")


if __name__ == "__main__":
    unittest.main()

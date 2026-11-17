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
import fixture_learning
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
        if not path.exists():
            return []
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [record for record in records if "event" in record]

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

    def test_when_the_hold_ends_the_fixture_is_dark_where_the_hand_left_it(self):
        """The direction chat's rule: no snap back. den-live's books take the hand's aim (Home's pan and tilt, or the
        surface point), so after the hold it's dark there, and it's re-admitted from there like any other."""
        name = self.light()
        row = self.fixtures()[name]
        sent = len(self.moves(name))
        pan, tilt = row["pan_deg"] + 10, row["tilt_deg"] - 5
        self.change(name, {"pan_deg": row["pan_deg"], "tilt_deg": row["tilt_deg"]}, {"pan_deg": pan, "tilt_deg": tilt})
        self.assertEqual((self.fixtures()[name]["pan_deg"], self.fixtures()[name]["tilt_deg"]), (pan, tilt))
        self.stand()
        row = self.fixtures()[name]
        self.assertEqual((row["on"], row["hand"], row["pan_deg"], row["tilt_deg"]), (False, None, pan, tilt))
        self.assertEqual(len(self.moves(name)), sent, "nothing was sent: not the hand's aim, not a cut")
        point = [1.0, 1.5, .4]   # a surface the owner aimed it at, with no angles sent
        self.change(name, {"target": None}, {"target": {"point": point, "normal": [0, 0, 1]}})
        expected = [-a / 100 for a in map(self.core.joints.to_cdeg, self.core.joints.room_to_joints(
            point, self.preview.rows[name]["aim_origin_m"], self.preview.rows[name]["horizontal_world_reference"]))]
        self.assertEqual([self.fixtures()[name]["pan_deg"], self.fixtures()[name]["tilt_deg"]], expected)

    def test_a_held_beam_through_a_head_is_warned_about_and_never_cut(self):
        name = self.light()
        sent = len(self.moves(name))
        head = list(self.preview.track["head"])
        status, reply = self.change(name, {"target": None}, {"target": {"point": head, "normal": [0, 0, 1]}})
        self.assertEqual(reply["hand"]["warning"], "beam_through_head")
        self.frames(1)
        self.assertEqual(self.fixtures()[name]["hand"]["warning"], "beam_through_head")
        self.assertEqual(len(self.moves(name)), sent)   # warned, not cut: manual wins
        page = list(self.preview.track["target"])
        self.change(name, {"target": None}, {"target": {"point": page, "normal": [0, 0, 1]}})
        self.assertIsNone(self.fixtures()[name]["hand"]["warning"])
        self.change(name, {"target": None}, {"target": {"point": head, "normal": [0, 0, 1]}})
        self.change(name, {"spotlight": {"on": True}}, {"spotlight": {"on": False}})
        self.assertIsNone(self.fixtures()[name]["hand"]["warning"])   # off: nothing to warn about

    def test_a_444_shaped_change_is_recorded_learned_and_answered_as_home_reads_it(self):
        """#444 (eafd0885) treats a change as recorded on 200 with ok true and its own event_id, reads from_seq, and
        ends its hold once a snapshot at seq >= from_seq has shown the hand and then shows none."""
        name = self.light()
        row = self.fixtures()[name]
        event_id = "home-6f1c2c86-0f3e-4a43-9b56-6c1d0c7e9a10"
        status, reply = self.change(name, {"spotlight": {"on": True, "brightness": row["level"], "color_temp_kelvin": None},
                                           "downward": None, "radials": []},
                                    {"spotlight": {"on": True, "brightness": row["level"] / 2, "color_temp_kelvin": None},
                                     "downward": {"on": True, "brightness": .3, "color_temp_kelvin": None},
                                     "radials": [{"number": 1, "on": True, "brightness": .2, "color_temp_kelvin": None}]},
                                    event_id=event_id)
        self.assertEqual((status, reply["ok"], reply["event_id"], reply["recorded"]), (200, True, event_id, True))
        self.assertTrue(reply["learned"])
        self.assertIsInstance(reply["from_seq"], int)
        self.assertEqual(self.preview.latest["seq"], reply["from_seq"])
        self.assertEqual(self.fixtures()[name]["hand"]["parts"], ["downward", "radials", "spot"])
        self.assertEqual(set(self.learned()["task/evening"]), {"spot", "room"})
        self.stand()   # the task's light ends with the task; the glow's parts with its cue
        self.assertIsNone(self.fixtures()[name]["hand"])

    def test_the_spot_and_the_room_are_in_learned_from_you_and_learned_stays_the_bulbs(self):
        """#444 (2fbc5bd0) lists `learned` as bulbs and reads learned_from_you beside it: nothing of the fixtures'
        goes into the bulbs' map, or the panel would show it twice."""
        name = self.light()
        self.dim(name)
        state = self.preview.state()[1]
        self.assertIn("spot", state["learned_from_you"]["task/evening"])
        self.assertNotIn("learned", state)   # no bulbs overlay here, so no bulbs' learning either

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

    def on(self, name="F3", level=.5):
        return self.change(name, {"spotlight": {"on": False, "brightness": 0}},
                           {"spotlight": {"on": True, "brightness": level}})

    def test_a_learned_no_spot_is_taught_back_by_turning_the_light_on(self):
        """Review R1/R3: two offs teach "no spot"; a light the owner then turns on during the unlit read is their
        reading light (it's the task's, it lasts the episode, through a phone break), and it votes: two such reads
        bring the coordinator's own light back."""
        for _ in range(2):
            name = self.light()
            self.change(name, {"spotlight": {"on": True}}, {"spotlight": {"on": False}})
            self.stand()
        self.assertTrue(self.learned()["task/evening"]["spot"]["off"])
        self.seat()
        self.read()
        self.frames(3)
        self.assertEqual(self.lit(), [])   # unlit, as taught
        status, reply = self.on()
        self.assertEqual((reply["learned"], reply["why_not"], reply["hand"]["ends"]), (True, None, {"spot": "episode_end"}))
        self.assertTrue(self.learned()["task/evening"]["spot"]["off"])   # one vote; den-core's rule takes two
        task_scale = self.preview.settings["ambient_task_scale"]
        self.assertEqual(self.preview.latest["ambient"]["goal_scale"], task_scale)   # served: the room dims
        self.frames(11)   # a phone break: the reply's validity runs out
        self.assertIsNone(self.preview.task_until())
        self.assertIsNotNone(self.fixtures()["F3"]["hand"])
        self.read()   # back to the book: still their light
        self.frames(1)
        self.assertIsNotNone(self.fixtures()["F3"]["hand"])
        self.assertEqual(self.preview.latest["ambient"]["goal_scale"], task_scale)
        self.stand()
        self.assertIsNone(self.fixtures()["F3"]["hand"])
        self.seat()
        self.read()
        self.frames(3)
        self.assertEqual(self.lit(), [])
        self.on()
        self.assertFalse(self.learned()["task/evening"]["spot"]["off"])   # the second vote undoes "no spot"
        self.stand()
        self.light()   # the coordinator's own light is back

    def test_a_hold_is_one_correction_however_many_changes_it_takes(self):
        """Review R4: dim, dim again, then off, in one hold. Each later change is read against the hand's own last
        state (Home's `before`), and the hold teaches once: from where it began, toward where the owner left it."""
        name = self.light()
        row = self.fixtures()[name]
        level, target = row["level"], row["target_m"]
        replies = [self.change(name, {"spotlight": {"brightness": level}}, {"spotlight": {"brightness": .6 * level}}),
                   self.change(name, {"spotlight": {"brightness": .6 * level}}, {"spotlight": {"brightness": .3 * level}}),
                   self.change(name, {"spotlight": {"on": True}}, {"spotlight": {"on": False}})]
        self.assertEqual([reply["learned"] for _status, reply in replies], [True, True, True])
        spot = self.preview.learning.entries["task/evening"]["spot"]
        expected = fixture_learning.halfway(1.0, self.preview._lux(name, target, .3 * level))
        self.assertEqual(spot["n"], 1)
        self.assertAlmostEqual(spot["lux_scale"], min(max(expected, .25), 4.0))
        self.assertEqual((spot["off"], spot["pending"]), (False, True))   # the hold's last word: an off vote
        self.assertEqual(self.make_preview().learning.public(), self.preview.learning.public())   # replay agrees

    def test_the_glow_held_is_one_correction_too(self):
        self.seat()
        house = self.preview.latest["ambient"]["level"]
        self.change("F3", {"radials": [{"number": 1, "brightness": house}]}, {"radials": [{"number": 1, "brightness": .35}]})
        status, reply = self.change("F3", {"radials": [{"number": 1, "brightness": .35}]},
                                    {"radials": [{"number": 1, "brightness": .2}]})
        self.assertEqual((reply["learned"], reply["why_not"]), (True, None))
        room = self.preview.learning.entries["sitting/evening"]["room"]
        self.assertEqual(room["n"], 1)
        self.assertAlmostEqual(room["level"], (house + .2) / 2)

    def test_a_yes_in_a_camera_gap_still_gets_what_was_learned(self):
        """Review L2: the yes lands while nobody is tracked (the situation reads unknown); what was learned is fixed at
        the next frame, not skipped for the session."""
        name = self.light()
        page = list(self.preview.track["target"])
        self.change(name, {"target": None}, {"target": {"point": [page[0] + .1, page[1], page[2]]}})
        self.stand()
        self.seat()
        self.now += 1.6   # the newest frame is too old: nobody tracked
        self.preview.tick()
        self.assertIsNone(self.preview.track)
        self.read(at=self.preview.captured)
        self.assertIsNone(self.preview.spot_applied)
        self.frames(3)
        name = self.lit()[0]
        self.assertEqual(self.fixtures()[name]["learned"]["offset_m"], [.05, 0, 0])

    def test_an_aim_or_an_off_teaches_no_brightness(self):
        """Review L3: an aim-only first correction learns the aim, and nothing about the light."""
        name = self.light()
        page = list(self.preview.track["target"])
        self.change(name, {"target": None}, {"target": {"point": [page[0] + .1, page[1], page[2]]}})
        self.assertIsNone(self.learned()["task/evening"]["spot"]["lux_scale"])

    def test_a_torn_or_corrupt_log_never_stops_the_coordinator(self):
        """Review L1/L4: a torn tail doesn't swallow the next change, and bad bytes are skipped, not fatal."""
        name = self.light()
        log = Path(self.path) / "coordinator" / "manual-events.jsonl"
        self.dim(name)
        with log.open("ab") as raw:
            raw.write(b'\xff\xfe{"event": {"event_id": "torn')
        self.dim(name, level=.1)
        restarted = self.make_preview()
        self.assertEqual(len([line for line in restarted.manual_log.read() if "event" in line]), 2)
        self.assertEqual(restarted.state()[1]["manual_log"]["skipped"], 1)
        self.assertEqual(restarted.learning.public(), self.preview.learning.public())

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
        # Review R5: a learned warmth at the level the glow is already at glides over the cue's time, both ways.
        held = direction.Ambient()
        held.direct(False, settings, 0.0, goal=1.0, cct_k=2538)
        steps = [held.published(settings, t / 8)["cct_k"] for t in range(0, 17)]
        self.assertEqual(steps[0], direction.AMBIENT_CCT_K[0])
        self.assertEqual(steps[-1], 2538)
        self.assertEqual(steps, sorted(steps, reverse=True))
        self.assertGreater(len(set(steps)), 6)   # a glide, not a step
        held.direct(False, settings, 5.0)
        back = [held.published(settings, 5.0 + t / 8)["cct_k"] for t in range(0, 17)]
        self.assertEqual((back[0], back[-1]), (2538, direction.AMBIENT_CCT_K[0]))
        self.assertEqual(back, sorted(back))
        self.assertGreater(len(set(back)), 6)

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
        self.assertEqual((status, reply["recorded"], reply["learned"]), (200, True, True))
        self.assertEqual(reply["hand"]["event_id"], data["event_id"])
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

    def snapshot(self, status, presence, frame=True, epoch=1):
        """Perception with nobody on it: fresh and empty, fresh but too dark to tell (degraded), or stale."""
        self.seq += 1
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": epoch, "seq": self.seq,
                                     "frame": {**base.BINDING, "capture_mono": self.now} if frame else None,
                                     "status": status, "presence": presence, "eligibility_revision": 1,
                                     "eligible_track_id": None, "tracks": []})

    def own_light_and_glow(self):
        """Seated, no task: the owner puts F3's spot on for themselves and sets its downlight."""
        self.seat()
        self.change("F3", {"spotlight": {"on": False, "brightness": 0}}, {"spotlight": {"on": True, "brightness": .5}})
        self.change("F3", {"downward": {"on": False}}, {"downward": {"on": True, "brightness": .4}})
        self.assertEqual(self.fixtures()["F3"]["hand"]["parts"], ["downward", "spot"])

    def test_an_empty_den_ends_a_hold(self):
        """Only fresh frames that show the den empty count, for VACANT_S from the last one with someone on it."""
        self.own_light_and_glow()
        for _ in range(round((preview.VACANT_S - 1) / .5)):
            self.now += .5
            self.snapshot("fresh", "empty")
        self.assertIsNotNone(self.fixtures()["F3"]["hand"])
        for _ in range(4):
            self.now += .5
            self.snapshot("fresh", "empty")
        self.assertIsNone(self.fixtures()["F3"]["hand"])

    def read_in(self, epoch):
        """A yes for the reader in this camera epoch (base.read's is epoch 1's)."""
        at, seq = self.now, round(self.now * 100)
        frame = base.FrameRef("run", epoch, seq, "synthetic", "rgb", base.PlaneRef(seq, at, at, 640, 360), None, None,
                              None, base.BINDING["transform_id"], base.BINDING["scan_revision"], at, at, at)
        self.preview.offer_inference(base.InferenceReply(
            str(at), "synthetic", "run", epoch, "t1", 2, 1, (frame,),
            {"status": "valid", "facts": {"people_visible": "one", "activity_hypothesis": "reading"}},
            {"safe": True, "status": "confident", "window_start_mono": at, "window_end_mono": at},
            {}, {}, at, self.now, at + 10, {}))

    def outage(self, seconds=4.0, epoch=2):
        """The camera stops: stale snapshots, and after 3 s perception's new camera epoch (den-live drops the track,
        the binding and the episode)."""
        for _ in range(round(seconds / .5)):
            self.now += .5
            self.snapshot("stale", "unknown", frame=False, epoch=epoch)

    def test_a_camera_outage_never_ends_a_hold(self):
        """The spine, 2026-11-14: manual always wins, so an outage with an epoch bump, then the same person seated
        again, leaves every hold in place: their own light, their glow, and their reading light (still the task's)."""
        with self.subTest("their own light and glow"):
            self.own_light_and_glow()
            self.outage()
            self.assertIsNone(self.preview.episode)
            self.frames(3, epoch=2)   # seated again, a new episode
            self.assertIsNotNone(self.preview.episode)
            self.assertEqual(self.fixtures()["F3"]["hand"]["parts"], ["downward", "spot"])
        with self.subTest("their reading light and glow"):
            self.setUp()
            name = self.light()
            self.dim(name)
            self.change(name, {"radials": [{"number": 1, "brightness": .3}]}, {"radials": [{"number": 1, "brightness": .1}]})
            self.outage()
            self.frames(3, epoch=2)
            self.assertEqual(self.fixtures()[name]["hand"]["parts"], ["radials", "spot"])
            self.read_in(2)   # back at the book: the hold carries on with the new episode, and serves its task
            self.frames(2, epoch=2)
            self.assertIsNotNone(self.preview.task_until())
            self.assertEqual(self.fixtures()[name]["hand"]["parts"], ["radials", "spot"])
            self.assertTrue(self.preview.latest_decision["proposal"]["learned"]["served_by_hand"])
            self.assertLess(self.preview.latest["ambient"]["goal_scale"], 1.0)   # dimmed (to the glow it learned here)
            self.assertEqual(self.lit(), [])
            for _ in range(4):   # and standing up, seen on a fresh frame, is still its end
                self.now += .5
                self.track(seated=False, epoch=2)
            self.assertIsNone(self.fixtures()[name]["hand"])

    def test_a_reading_light_kept_through_a_phone_break_goes_when_the_episode_is_lost_without_a_task(self):
        """The spine's rule's other half: a new episode ends the task's light when the task had already ended (here a
        phone break under a light the owner turned on during an unlit read), or when the den was seen empty."""
        for _ in range(2):
            name = self.light()
            self.change(name, {"spotlight": {"on": True}}, {"spotlight": {"on": False}})
            self.stand()
        self.seat()
        self.read()
        self.frames(3)
        self.on()
        self.frames(11)   # the phone break: the task ends, the light stays theirs
        self.assertIsNotNone(self.fixtures()["F3"]["hand"])
        self.outage()
        self.assertIsNone(self.fixtures()["F3"]["hand"])
        with self.subTest("the den seen empty between episodes"):
            self.setUp()
            name = self.light()
            self.dim(name)
            self.change(name, {"downward": {"on": False}}, {"downward": {"on": True, "brightness": .4}})
            for _ in range(4):
                self.now += .5
                self.snapshot("fresh", "empty")
            self.assertIsNotNone(self.fixtures()[name]["hand"])   # empty for 2 s: not yet VACANT_S
            self.frames(3, epoch=2)   # someone seated again, a new track: a new episode after an empty den
            self.assertIsNone(self.fixtures()[name]["hand"])

    def test_a_camera_gap_or_a_dark_room_ends_no_hold(self):
        """Review R2: a camera outage, a too-dark den or stale snapshots say nothing about who's there. The owner's own
        spot and their downlight stay theirs for well past VACANT_S."""
        for kind in ("stale", "degraded", "ticks"):
            with self.subTest(kind):
                self.setUp()
                self.own_light_and_glow()
                for _ in range(round((preview.VACANT_S + 10) / .5)):
                    self.now += .5
                    if kind == "stale":
                        self.snapshot("stale", "unknown", frame=False)
                    elif kind == "degraded":
                        self.snapshot("degraded", "unknown")
                    else:
                        self.preview.tick()
                self.assertIsNone(self.preview.track)
                self.assertEqual(self.fixtures()["F3"]["hand"]["parts"], ["downward", "spot"])

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

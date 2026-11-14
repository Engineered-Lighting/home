"""Several people (plan 04, step 3): a second person never darkens the reader's light, no beam crosses anyone's head,
two people with no binding light nothing, and the bulbs get no reading scene while others are there."""

import math
import tempfile
import unittest
from pathlib import Path

import coordinator_live
import preview
import seated as seated_rule
import sofa_check
import test_preview as base

AWAY = [3.4, 3.2, 1.7]   # a standing head far from F3's beam to the reader's book


class PreviewPeopleTest(unittest.TestCase):
    read, frame, seat, light, row = (base.PreviewTest.read, base.PreviewTest.frame, base.PreviewTest.seat,
                                     base.PreviewTest.light, base.PreviewTest.row)

    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = tmp.name
        self.now, self.seq, self.encoded = 100.0, 0, []
        self.rule, self.rule_epoch = seated_rule.Published(), 1
        self.preview = preview.Preview(self.core, binding=base.BINDING, fixtures=base.FIXTURES, state=self.path,
                                       clock=lambda: self.now, utc_offset_h=0, threaded=False,
                                       append=self.encoded.append)
        self.preview.configure({"fade_in_s": 0, "pre_aim": False})
        self.guest = None   # the second person's head, or None

    def track(self, *, seated=True, epoch=1, eligible=True, shift=0.0, **_ignored):
        """The reader t1, plus a second person t2 standing with their head at self.guest. The ask names an eligible
        track only while one person is in view, as ask.py does."""
        self.seq += 1
        pose = base.joints(seated, shift=shift)
        reader = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                  "association_revision": 2, "current_obs_ids": ("pose",), "supported_pose": pose,
                  "seated": self.rule.update("t1", 2, self.now, pose)}
        tracks = [reader]
        if self.guest is not None:
            tracks.append({"track_id": "t2", "state": "confirmed", "position_basis": "stereo",
                           "association_status": "confident", "association_revision": 1, "current_obs_ids": ("pose",),
                           "supported_pose": {}, "head_point_m": {"xyz": list(self.guest)}, "seated": None})
        alone = self.guest is None
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": epoch, "seq": self.seq,
                                     "frame": {**base.BINDING, "capture_mono": self.now}, "status": "fresh",
                                     "presence": "present", "eligibility_revision": 1,
                                     "eligible_track_id": "t1" if seated and eligible and alone else None,
                                     "tracks": tracks})

    def walk(self, head, seconds, step=.25):
        self.guest, seen = head, []
        for _ in range(int(round(seconds / step))):
            self.now += step
            self.track()
            seen.append(self.row()["on"])
        return seen

    def test_a_head_in_the_beam_as_it_points_is_cut_even_off_the_line_to_the_page(self):
        """Other people's heads are checked on the beam as it actually points, not only on the line to the page: a
        fixture holding or rising off that line is cut at once when someone's head is in it."""
        self.light()
        row = self.preview.rows["F3"]
        aim = self.core.joints.direction(row["current"][0] / 100, row["current"][1] / 100,
                                         row["horizontal_world_reference"])
        on_beam = [row["aim_origin_m"][i] + 1.0 * aim[i] for i in range(3)]   # a metre down the beam
        self.assertFalse(all(self.walk(on_beam, .5)), "someone's head in the beam: dark at once")
        self.assertEqual(self.preview.rows["F3"]["reason"], preview.THROUGH_ANOTHER_HEAD)

    def test_a_head_past_the_book_is_judged_as_admission_judged_it_so_the_light_never_blinks(self):
        """Admission and the cut measure everyone else's head the same way: to the beam's centre from the fixture as far
        as the page. A head past the book along the beam (a partner's head in the reader's lap, the book held above it)
        is as far from the light as it is from the page. The cut used to measure it to the beam's whole line, so the
        light was admitted, cut, admitted again after CUT_HOLD_S and cut again: a blink every 1.6 s."""
        self.light()
        row = self.preview.rows["F3"]
        origin, page = row["aim_origin_m"], self.preview.track["target"]
        line = [page[i] - origin[i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in line))
        ahead = [c / length for c in line]
        side = [ahead[1], -ahead[0], 0.0]
        norm = math.sqrt(sum(c * c for c in side))
        past = [page[i] + .25 * ahead[i] + .10 * side[i] / norm for i in range(3)]   # 25 cm past the book, 10 cm off
        self.assertGreater(self.core.eye.centre_clear(origin, page, past), self.core.eye.HEAD_CLEARANCE_M,
                           "admission clears it")
        self.read()   # the task runs on
        refused = []
        seen = []
        for _ in range(round((3 * preview.CUT_HOLD_S + 1) / .25)):
            self.now += .25
            self.guest = past
            self.track()
            seen.append(self.row()["on"])
            refused.append(self.preview.latest_decision["proposal"]["refused"].get("F3"))
        self.assertEqual(seen, [True] * len(seen), "lit throughout: never cut and lit again")
        self.assertNotIn(preview.THROUGH_ANOTHER_HEAD, refused)
        self.assertNotEqual(self.preview.rows["F3"]["reason"], preview.THROUGH_ANOTHER_HEAD)

    def test_a_light_rises_in_place_only_where_its_beam_already_clears_everyone(self):
        """A dark fixture near enough the page rises where it points, without a move, only if that beam clears the
        reader and everyone else by admission's margin, as the line to the page was admitted. Otherwise it moves dark to
        the page first: rising in place into someone's head was a cut and, a CUT_HOLD_S later, the same rise again."""
        self.light()
        aim = self.preview.rows["F3"]["current"]
        for _ in range(8):   # the book 9 cm along: the lit frame holds, a little off the new page
            self.now += .25
            self.track(shift=-.09)
        row = self.preview.rows["F3"]
        self.assertEqual(row["current"], aim, "the frame held")
        origin, page = row["aim_origin_m"], self.preview.track["target"]
        beam = self.core.joints.direction(aim[0] / 100, aim[1] / 100, row["horizontal_world_reference"])
        new = [page[i] - origin[i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in new))
        on_beam = [origin[i] + 1.8 * beam[i] for i in range(3)]
        on_line = [origin[i] + 1.8 * new[i] / length for i in range(3)]
        away = [on_beam[i] - on_line[i] for i in range(3)]
        norm = math.sqrt(sum(c * c for c in away))
        guest = [on_beam[i] + .10 * away[i] / norm for i in range(3)]   # 10 cm off the held beam, farther off the page's
        self.assertGreater(self.core.eye.centre_clear(origin, page, guest), self.core.eye.HEAD_CLEARANCE_M,
                           "admission clears the line to the page")
        self.read()
        seen = []
        for _ in range(round((3 * preview.CUT_HOLD_S + 1) / .25)):
            self.now += .25
            self.guest = guest
            self.track(shift=-.09)
            seen.append(self.row()["on"])
        self.assertFalse(seen[0], "their head in the held beam: cut at once")
        self.assertIn(True, seen, "lit again once the hold is over")
        back = seen.index(True)
        self.assertEqual(seen[back:], [True] * (len(seen) - back), "and it stays lit: no second cut")
        self.assertNotEqual(self.preview.rows["F3"]["current"], aim, "it moved to the page dark, then rose")

    def test_a_passer_by_never_darkens_the_reader(self):
        self.light()
        self.assertEqual(self.walk(AWAY, 5.0), [True] * 20)
        self.assertEqual(self.preview.lighting_context()["people"], 2)
        self.guest = None
        self.now += .25
        self.track()
        self.assertTrue(self.row()["on"])
        self.assertEqual(self.preview.lighting_context()["people"], 1)

    def test_a_head_in_the_beam_takes_the_light_away_and_it_comes_back(self):
        self.light()
        origin, book = base.FIXTURES["F3"]["aim_origin_m"], self.preview.track["target"]
        self.walk([(a + b) / 2 for a, b in zip(origin, book)], .5)   # a head halfway along the beam
        self.assertFalse(self.row()["on"])
        self.assertEqual(self.preview.latest_decision["proposal"]["refused"]["F3"], preview.THROUGH_ANOTHER_HEAD)
        self.walk(AWAY, .5)
        self.read()   # the next yes, once the way is clear
        for _ in range(8):
            self.now += .25
            self.track()
        self.assertTrue(self.row()["on"])

    def test_two_people_and_no_binding_light_nothing(self):
        self.guest = AWAY
        for _ in range(8):
            self.now += .5
            self.track()
        self.assertIsNone(self.preview.track)
        self.read()
        self.now += .25
        self.track()
        self.assertFalse(self.row()["on"])

    def test_the_bulbs_get_no_reading_scene_while_someone_else_is_there(self):
        self.light()
        self.assertEqual(self.preview.latest["bulbs"]["reason"], "reading")
        self.walk(AWAY, 1.0)
        self.assertTrue(self.row()["on"])
        self.assertEqual((self.preview.latest["bulbs"]["reason"], self.preview.latest["bulbs"]["release"]),
                         ("several_people", True))

    def test_a_passer_by_in_front_of_the_reader_darkens_until_they_pass_and_the_episode_stays(self):
        """03's review: the reader's track coasts while the passer is the only one seen. The light goes out (no current
        support), the episode stays, and it lights again once the reader is seen, with no new answer."""
        self.light()
        episode = self.preview.episode
        self.guest = AWAY
        self.now += .25
        self.seq += 1
        reader = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                  "association_revision": 2, "current_obs_ids": (), "supported_pose": {}, "seated": None}
        passer = {"track_id": "t2", "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                  "association_revision": 1, "current_obs_ids": ("pose",), "supported_pose": {},
                  "head_point_m": {"xyz": list(AWAY)}, "seated": None}
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": 1, "seq": self.seq,
                                     "frame": {**base.BINDING, "capture_mono": self.now}, "status": "fresh",
                                     "presence": "present", "eligibility_revision": 1, "eligible_track_id": None,
                                     "tracks": [reader, passer]})
        self.assertFalse(self.row()["on"])
        self.assertIs(self.preview.episode, episode)
        self.guest = None
        self.now += .25
        self.track()
        self.assertTrue(self.row()["on"])

    def test_a_multiple_reply_neither_renews_nor_revokes(self):
        """Contract 3's owner: "multiple" is unknown, not no. It's taken, keeps the light, and renews nothing."""
        self.light()
        episode = self.preview.episode
        until = episode.valid_until
        self.now += .25
        self.track()
        self.read(people_visible="multiple")
        self.assertIn(str(self.now), episode.requests)
        self.assertEqual(episode.valid_until, until)
        self.assertTrue(self.row()["on"])
        self.now += .25
        self.track()
        self.read(people_visible="none")
        self.assertIsNone(episode.valid_until)   # "none" still ends the task; the direction fades it out
        for _ in range(int((self.preview.settings["fade_out_s"] + .5) / .25)):
            self.now += .25
            self.track()
        self.assertFalse(self.row()["on"])


if __name__ == "__main__":
    unittest.main()

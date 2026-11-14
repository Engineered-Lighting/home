"""Several people (plan 04, step 3): a second person never darkens the reader's light, no beam crosses anyone's head,
two people with no binding light nothing, and the bulbs get no reading scene while others are there."""

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

    def track(self, *, seated=True, epoch=1, eligible=True, **_ignored):
        """The reader t1, plus a second person t2 standing with their head at self.guest. The ask names an eligible
        track only while one person is in view, as ask.py does."""
        self.seq += 1
        pose = base.joints(seated)
        reader = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                  "association_revision": 2, "current_obs_ids": ("pose",), "supported_pose": pose,
                  "seated": self.rule.update("t1", 2, self.now, pose)}
        tracks = [reader]
        if self.guest is not None:
            tracks.append({"track_id": "t2", "state": "confirmed", "position_basis": "stereo",
                           "association_status": "confident", "association_revision": 1, "current_obs_ids": ("pose",),
                           "supported_pose": {}, "coarse_head_room_m": list(self.guest), "seated": None})
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
                  "coarse_head_room_m": list(AWAY), "seated": None}
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
        self.assertFalse(self.row()["on"])


if __name__ == "__main__":
    unittest.main()

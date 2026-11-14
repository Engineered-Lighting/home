"""fixture_learning.py: den-core learn's rule on den-live's quantities, held to den-core's own Learner."""

import tempfile
import unittest
from pathlib import Path

import coordinator_live
import fixture_learning
import sofa_check
from fixture_learning import Learning

KEY = "task/evening"


def spot(**values):
    return {"spot": {"lux_before": None, "lux_scale": None, "offset_m": None, "off": None, **values}}


class LearningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        _core, cls.learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def test_each_correction_moves_halfway_from_what_was_there(self):
        learning = Learning()
        learning.apply(KEY, spot(lux_before=1.2, lux_scale=.6), 1)
        self.assertAlmostEqual(learning.spot(KEY)["lux_scale"], .9)   # halfway from what the coordinator gave
        learning.apply(KEY, spot(lux_before=.9, lux_scale=.5), 2)
        self.assertAlmostEqual(learning.spot(KEY)["lux_scale"], .7)   # then halfway from what was learned
        learning.apply(KEY, spot(offset_m=[.1, 0, -.04]), 3)
        self.assertEqual(learning.spot(KEY)["offset_m"], [.05, 0, -.02])
        self.assertAlmostEqual(learning.spot(KEY)["lux_scale"], .7)   # an aim-only change leaves the light alone
        self.assertEqual(learning.spot(KEY)["n"], 3)
        self.assertIsNone(learning.spot("task/day"))

    def test_back_and_forth_settles_in_between_and_the_scale_stays_in_range(self):
        learning = Learning()
        for scale in (.5, 1.5, .5, 1.5, .5, 1.5):
            learning.apply(KEY, spot(lux_scale=scale), 0)
        self.assertTrue(.5 < learning.spot(KEY)["lux_scale"] < 1.5)
        for _ in range(8):
            learning.apply(KEY, spot(lux_scale=40.0), 0)
        self.assertEqual(learning.spot(KEY)["lux_scale"], fixture_learning.LUX_SCALE[1])

    def test_no_spot_takes_two_offs_in_a_row(self):
        learning = Learning()
        learning.apply(KEY, spot(off=True), 0)
        self.assertFalse(learning.spot(KEY)["off"])
        learning.apply(KEY, spot(off=False, lux_scale=1.0), 0)   # turned back on: the vote is gone
        learning.apply(KEY, spot(off=True), 0)
        self.assertFalse(learning.spot(KEY)["off"])
        learning.apply(KEY, spot(off=True), 0)
        self.assertTrue(learning.spot(KEY)["off"])
        learning.apply(KEY, spot(off=False), 0)
        learning.apply(KEY, spot(off=False), 0)
        self.assertFalse(learning.spot(KEY)["off"])

    def test_the_room_glow_moves_halfway_and_warmth_in_mireds(self):
        learning = Learning()
        learning.apply("sitting/evening", {"room": {"level": .3, "level_before": .75, "cct_k": 2200,
                                                    "cct_before": 3000}}, 5)
        room = learning.room("sitting/evening")
        self.assertAlmostEqual(room["level"], .525)
        self.assertEqual(room["cct_k"], round(1e6 / ((1e6 / 3000 + 1e6 / 2200) / 2)))
        self.assertEqual(learning.public()["sitting/evening"]["room"],
                         {"level": .525, "cct_k": room["cct_k"], "n": 1, "last_ms": 5})

    def test_level_warmth_and_off_agree_with_den_cores_learner(self):
        """The same corrections through den-core's Learner (0-255 brightness for one bulb) and through this store
        (a 0-1 room level, and the spot's off vote) learn the same light, the same warmth, and the same on/off."""
        learner, learning = self.learn.Learner(), Learning()
        before = {"on": True, "brightness": 191, "kelvin": 3000}
        for seq, (level, kelvin) in enumerate([(.6, 2700), (.2, 2400), (.9, 3500), (.5, 2700), (.5, 2700)]):
            after = {"on": True, "brightness": round(level * 255), "kelvin": kelvin}
            learner.apply({"seq": seq, "entity_id": "light.x", "before": before, "after": after, "at": seq},
                          "sitting/evening", 1, True, True)
            learning.apply("sitting/evening", {"room": {"level": level, "level_before": .75, "cct_k": kelvin,
                                                        "cct_before": 3000}}, seq)
            ours, theirs = learning.room("sitting/evening"), learner.scene("sitting/evening", "light.x")
            self.assertLessEqual(abs(ours["level"] * 255 - theirs["brightness"]), 1.5, seq)
            self.assertLessEqual(abs(ours["cct_k"] - theirs["kelvin"]), 2, seq)
        votes, learning = self.learn.Learner(), Learning()
        for seq, on in enumerate((False, True, False, False, True)):
            votes.apply({"seq": seq, "entity_id": "light.x", "before": before, "after": {"on": on}, "at": seq},
                        KEY, 1, True, True)
            learning.apply(KEY, spot(off=not on), seq)
            self.assertEqual(not learning.spot(KEY)["off"], votes.scene(KEY, "light.x")["on"], seq)

    def test_replay_rebuilds_the_store_from_the_log(self):
        lines = [{"event": {"event_id": "a", "at_ms": 1}, "key": KEY, "learn": spot(lux_scale=.6, lux_before=1.0)},
                 {"event": {"event_id": "b", "at_ms": 2}, "key": KEY, "learn": None, "why_not": "people_2"},
                 {"event": {"event_id": "c", "at_ms": 3}, "key": KEY, "learn": spot(offset_m=[.1, 0, 0])}]
        learning = Learning.replay(lines)
        self.assertEqual(learning.public()[KEY]["spot"],
                         {"lux_scale": .8, "offset_m": [.05, 0, 0], "off": False, "n": 2, "last_ms": 3})

    def test_an_aim_further_than_the_limit_is_pointing_elsewhere(self):
        page = [1, 1, .7]
        self.assertEqual(fixture_learning.offset([1.1, 1, .7], page), [1.1 - 1, 0, 0])
        self.assertIsNone(fixture_learning.offset([1.4, 1, .7], page))
        self.assertIsNone(fixture_learning.offset(None, page))


if __name__ == "__main__":
    unittest.main()

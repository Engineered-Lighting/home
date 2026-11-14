"""The preview's bulbs: the plan published with every snapshot (shadow first), the Bulbs switch (Off by default, saved),
Off and Stop releasing the bulbs, and the overlay sending the preview's own plan only while Bulbs is On."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bulb_plan
import coordinator_live
import preview
import seated as seated_rule
import sofa_check
import test_preview as base
from hue_overlay import HueOverlay, ReadOnly
from test_hue_overlay import LIGHTS, FakeDenLighting


class PreviewBulbsTest(unittest.TestCase):
    # The preview test's own helpers: a seated reader, a yes, a lit F3.
    track, read, frame, seat, light, row = (base.PreviewTest.track, base.PreviewTest.read, base.PreviewTest.frame,
                                            base.PreviewTest.seat, base.PreviewTest.light, base.PreviewTest.row)

    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, cls.learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = tmp.name
        # The shared test room's F3 sits at the reader's side, so the direction's slide reach (a head 25 cm along the
        # sofa) is always under its beam: these rules run without it, as test_preview's do; it has a test of its own.
        reach = patch.object(preview, "SLIDE_REACH_M", 0.0)
        reach.start()
        self.addCleanup(reach.stop)
        self.now, self.seq, self.encoded = 100.0, 0, []
        self.rule, self.rule_epoch = seated_rule.Published(), 1
        self.preview = self.make_preview()
        self.preview.configure({"fade_in_s": 0, "pre_aim": False})

    def make_preview(self, **overrides):
        return preview.Preview(self.core, binding=base.BINDING, fixtures=base.FIXTURES, state=self.path,
                               clock=lambda: self.now, utc_offset_h=0,
                               **{"threaded": False, "append": self.encoded.append, **overrides})

    def bulbs(self):
        return self.preview.latest["bulbs"]

    def attach(self, ha):
        overlay = HueOverlay(self.core, self.learn, ReadOnly(ha), self.preview.lighting_context, self.path,
                             sending=self.preview.bulbs_switch, desired=self.preview.bulb_desired, writer=ha,
                             clock=lambda: self.now)
        self.preview.hue = overlay
        return overlay

    def stand(self):
        for _ in range(4):
            self.now += .5
            self.track(seated=False)

    def test_reading_publishes_the_reading_scene_and_standing_releases_it_with_nothing_sent(self):
        self.assertFalse(self.preview.bulbs_on)
        self.assertEqual(self.bulbs()["schema"], bulb_plan.SCHEMA)
        self.light()
        record = self.bulbs()
        self.assertEqual((record["mode"], record["reason"], record["release"], record["source"]),
                         ("shadow", "reading", False, "den_live"))
        self.assertEqual(record["scenes"], {bulb_plan.ALL: {"on": True, "brightness": 77, "kelvin": 2700}})
        self.assertEqual(record["transition_s"], 4.0)
        self.assertIsNone(record["sent_generation"])
        since = record["since_mono"]
        self.now += .25
        self.track()
        self.assertEqual(self.bulbs()["since_mono"], since)   # an unchanged plan keeps its start
        self.stand()
        self.assertTrue(self.bulbs()["release"])
        self.assertEqual(self.preview.state()[1]["bulb_plan"], self.bulbs())
        self.assertIn("situation", self.preview.state()[1])

    def test_off_and_stop_release_the_bulbs(self):
        self.light()
        self.preview.switch({"on": False})
        self.assertEqual((self.bulbs()["release"], self.bulbs()["reason"]), (True, "off"))
        self.preview.switch({"on": True})
        self.preview.stop({})
        self.assertEqual((self.bulbs()["release"], self.bulbs()["reason"]), (True, "stopped"))

    def test_the_bulbs_switch_starts_off_and_is_saved(self):
        state = Path(self.path) / "coordinator" / "bulbs-control.json"
        self.assertFalse(state.exists())
        self.assertEqual(self.preview.bulbs({"on": "yes"})[0], 400)
        self.assertEqual(self.preview.routes()[("POST", "/coordinator/virtual/bulbs")], self.preview.bulbs)
        self.assertEqual(self.preview.bulbs({"on": True}), (200, {"ok": True, "on": True}))
        self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {"on": True})
        self.assertTrue(self.make_preview().bulbs_on)
        self.preview.bulbs({"on": False})
        self.assertFalse(self.make_preview().bulbs_on)
        state.write_text("{not json", encoding="utf-8")
        self.assertFalse(self.make_preview().bulbs_on)   # unreadable is Off

    def test_with_bulbs_off_a_whole_reading_sends_nothing_but_shows_den_lightings_own_bulbs(self):
        ha = FakeDenLighting()
        overlay = self.attach(ha)
        overlay.step()
        self.light()
        overlay.step()
        self.stand()
        overlay.step()
        self.light()
        overlay.step()
        self.assertEqual(ha.calls, [])
        record = self.bulbs()
        self.assertEqual((record["mode"], record["source"]), ("shadow", "home_assistant"))
        self.assertEqual(record["scenes"], {"light.a": {"on": True, "brightness": 77, "kelvin": 2700},
                                            "light.b": {"on": True, "brightness": 30, "kelvin": 2700}})

    def test_with_bulbs_on_the_overlay_sends_the_previews_own_plan(self):
        ha = FakeDenLighting()
        overlay = self.attach(ha)
        overlay.step()
        self.preview.bulbs({"on": True})
        self.assertTrue(overlay.wake_event.is_set())
        overlay.wake_event.clear()
        self.light()
        self.assertTrue(overlay.wake_event.is_set())   # the plan changed: the worker steps now
        overlay.step()
        self.assertEqual([service for service, _ in ha.calls], ["coordinator_claim", "coordinator_plan"])
        self.assertEqual(ha.plan["scenes"], self.bulbs()["scenes"])
        self.preview.tick()
        self.assertEqual((self.bulbs()["mode"], self.bulbs()["sent_generation"]), ("sending", 1))
        self.stand()
        overlay.step()
        self.assertTrue(ha.calls[-1][1]["release"])
        self.light()
        overlay.step()
        self.assertIsNotNone(ha.plan)
        self.preview.bulbs({"on": False})
        overlay.step()
        self.assertTrue(ha.calls[-1][1]["release"])
        self.assertIsNone(ha.plan)
        self.assertTrue(set(ha.plan or {}) <= set(LIGHTS))

    def test_gaming_is_dark_for_the_spots_and_den_lightings_scene_for_the_bulbs(self):
        ha = FakeDenLighting()
        ha.state_of = ha.state

        def playing(entity):
            row = ha.state_of(entity)
            if entity == "sensor.den_lighting_control":
                row["attributes"]["gaming_signal"] = "playing"
            return row
        ha.state = playing
        overlay = self.attach(ha)
        overlay.step()
        self.seat()
        self.read()
        for _ in range(12):
            self.now += .25
            self.track()
        self.assertFalse(self.row()["on"])
        self.assertTrue(self.preview.latest_decision["proposal"]["gaming"])
        self.assertEqual(self.bulbs()["reason"], "gaming")
        self.assertEqual(ha.calls, [])


if __name__ == "__main__":
    unittest.main()

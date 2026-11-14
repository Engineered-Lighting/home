"""The bulbs' worker against a fake den_lighting 1.2: nothing sent while Bulbs is Off, the plan sent and renewed while
it's On, a release when it goes Off, and learning Clear across a lost response and a Home Assistant restart."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import bulb_plan
import coordinator_live
import ha_link
import sofa_check
from hue_overlay import BulbsOff, HueOverlay, ReadOnly, RENEW_S, STATUS_ENTITY

LIGHTS = ["light.a", "light.b"]


class FakeDenLighting:
    """den_lighting 1.2's coordinator services and status, as far as the overlay can see them."""

    def __init__(self, *, active=True):
        self.active, self.epoch, self.publisher = active, "epoch-1", None
        self.claim_revision = self.generation = 0
        self.plan = None
        self.calls, self.reads = [], []
        self.bulbs = {"light.a": {"state": "on", "attributes": {"brightness": 200, "color_temp_kelvin": 3000}},
                      "light.b": {"state": "on", "attributes": {"brightness": 30, "color_temp_kelvin": 3000}}}

    def coordinator(self):
        return {"enabled": self.active, "active": self.active, "epoch": self.epoch, "publisher": self.publisher,
                "claim_revision": self.claim_revision, "generation": self.generation, "fresh": self.plan is not None,
                "lights": LIGHTS, "warmest": {"light.a": 2000, "light.b": 2202}}

    def state(self, entity):
        self.reads.append(entity)
        if entity == STATUS_ENTITY:
            return {"state": "automatic", "attributes": {
                "occupancy": "on", "gaming_signal": "idle", "coordinator": self.coordinator(),
                "correction_stream_id": "stream", "correction_watermark": 3, "last_correction": None}}
        return self.bulbs.get(entity)

    def call(self, domain, service, data, *, response=False):
        assert domain == "den_lighting" and response
        self.calls.append((service, dict(data)))
        if not self.active:
            raise ha_link.HaError("Home Assistant said 500")
        if service == "coordinator_claim":
            assert data["epoch"] == self.epoch and data["claim_revision"] == self.claim_revision
            self.publisher, self.plan = data["publisher"], None
            self.claim_revision += 1
            return self.coordinator()
        assert (data["epoch"], data["publisher"]) == (self.epoch, self.publisher), "stale authority"
        if service == "coordinator_clear":
            self.plan = None
            return {"stream_id": "stream", "watermark": 3, "coordinator": self.coordinator()}
        assert data["generation"] > self.generation, "stale generation"
        assert not (data.get("release") and data["scenes"])
        assert set(data["scenes"]) <= set(LIGHTS), "a plan may only name this den's lights"
        self.generation = data["generation"]
        self.plan = None if data.get("release") else data
        return self.coordinator()


class OverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, cls.learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root, self.now, self.on = Path(folder.name), 100.0, False
        self.ha = FakeDenLighting()
        self.plan = None
        self.overlay = self.make()

    def make(self):
        context = lambda: {"people": 1, "task": True, "seated": True, "activity_known": True, "since_mono": 0.0}
        return HueOverlay(self.core, self.learn, ReadOnly(self.ha), context, self.root, sending=lambda: self.on,
                          desired=lambda: self.plan, writer=self.ha, clock=lambda: self.now)

    def reading(self):
        scenes = bulb_plan.desired("task/evening", people=1, lights=LIGHTS,
                                   house=self.overlay.inputs()["house"])
        return {"key": ("task/evening", True, "home_assistant"), "plan": scenes}

    def test_off_by_default_reads_but_never_calls(self):
        self.overlay.step()
        self.plan = self.reading()
        for _ in range(30):
            self.now += 1
            self.overlay.step()
        self.assertEqual(self.ha.calls, [])
        inputs = self.overlay.inputs()
        self.assertEqual((inputs["lights"], inputs["active"]), (LIGHTS, True))
        self.assertEqual(inputs["house"]["light.b"], {"on": True, "brightness": 30, "kelvin": 3000})
        self.assertEqual(self.plan["plan"]["scenes"]["light.b"]["brightness"], 30)   # never brighter than it was
        with self.assertRaises(BulbsOff):
            self.overlay._call("coordinator_claim", {"epoch": "epoch-1"})
        self.assertFalse(hasattr(self.overlay.ha, "call"))   # reads go through a client that can't write

    def test_on_claims_sends_the_previews_plan_and_renews_with_a_new_generation(self):
        self.overlay.step()
        self.plan, self.on = self.reading(), True
        self.overlay.step()
        self.assertEqual([service for service, _ in self.ha.calls], ["coordinator_claim", "coordinator_plan"])
        sent = self.ha.calls[-1][1]
        self.assertEqual(sent, bulb_plan.payload(self.plan["plan"], epoch="epoch-1", publisher=self.overlay.publisher,
                                                 generation=1))
        self.now += 1
        self.overlay.step()
        self.assertEqual(len(self.ha.calls), 2)   # unchanged and not yet due: nothing sent
        self.now += RENEW_S
        self.overlay.step()
        self.assertEqual(self.ha.calls[-1][1]["generation"], 2)   # 1.2 never extends an exact retry
        self.assertEqual(self.ha.calls[-1][1]["scenes"], sent["scenes"])

    def test_the_end_of_the_task_releases_and_off_hands_the_bulbs_back_at_once(self):
        self.overlay.step()
        self.plan, self.on = self.reading(), True
        self.overlay.step()
        self.plan = {"key": ("sitting/evening", True, "home_assistant"),
                     "plan": bulb_plan.release("sitting/evening", "den_lighting_rules")}
        self.overlay.step()
        self.assertTrue(self.ha.calls[-1][1]["release"])
        self.assertIsNone(self.ha.plan)
        self.plan = self.reading()
        self.overlay.step()
        self.assertIsNotNone(self.ha.plan)
        self.on = False
        self.overlay.step()
        self.assertEqual((self.ha.calls[-1][0], self.ha.calls[-1][1].get("release")), ("coordinator_plan", True))
        self.assertIsNone(self.ha.plan)
        count = len(self.ha.calls)
        for _ in range(5):
            self.now += RENEW_S
            self.overlay.step()
        self.assertEqual(len(self.ha.calls), count)

    def test_a_release_with_nothing_of_ours_live_sends_nothing(self):
        self.overlay.step()
        self.on = True
        self.plan = {"key": ("vacant/night", False, "home_assistant"), "plan": bulb_plan.release("vacant/night", "vacant")}
        self.overlay.step()
        self.assertEqual([service for service, _ in self.ha.calls], ["coordinator_claim"])

    def test_den_lightings_own_switch_off_means_no_call_even_with_bulbs_on(self):
        self.ha.active = False
        self.overlay.step()
        self.plan, self.on = self.reading(), True
        for _ in range(3):
            self.overlay.step()
        self.assertEqual(self.ha.calls, [])

    def test_a_plan_for_every_bulb_waits_for_den_lightings_list(self):
        self.on = True
        self.plan = {"key": ("task/evening", True, "home_assistant"),
                     "plan": bulb_plan.desired("task/evening", people=1)}
        self.overlay.step()
        self.assertEqual([service for service, _ in self.ha.calls], ["coordinator_claim"])

    def test_clear_while_off_is_local_and_calls_nothing(self):
        self.overlay.learner.entries["sitting/evening"] = {"light.a": {"on": True}}
        self.overlay.request_clear()
        self.overlay.step()
        self.assertEqual(self.ha.calls, [])
        self.assertEqual(self.overlay.readback()["learned"], {})
        self.assertEqual(self.overlay.state["correction"], {"stream_id": "stream", "seq": 3})


class HueOverlayClearTest(unittest.TestCase):
    """d215cb90's case, ported: Clear while sending survives a lost response and a Home Assistant restart."""

    def test_clear_recovers_when_ha_restarts_after_a_lost_response(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, learn = coordinator_live.load_core(sofa_check.archive(root / "trees", "coordinator"))
            ha = Mock()
            context = Mock(side_effect=AssertionError("Clear must run before planning or learning"))
            overlay = HueOverlay(core, learn, ha, context, root, sending=lambda: True)
            overlay.learner.entries["sitting/evening"] = {"light.den": {"on": True}}
            overlay.latest["learned"] = overlay.learner.entries
            old = {"epoch": "old", "active": True, "publisher": overlay.publisher}
            new = {"epoch": "new", "active": True, "publisher": None, "claim_revision": 0}
            ha.state.side_effect = [{"attributes": {"coordinator": row}} for row in (old, new, new)]
            ha.call.side_effect = [ha_link.HaError("timeout"), ha_link.HaError("stale authority"),
                                   {**new, "publisher": overlay.publisher}, {"stream_id": "stream", "watermark": 9}]

            overlay.request_clear()
            for _ in range(2):
                with self.assertRaises(ha_link.HaError):
                    overlay.step()
                self.assertTrue(overlay.readback()["clear_pending"])
            overlay.step()

            self.assertEqual([(call.args[1], call.args[2]["epoch"]) for call in ha.call.call_args_list],
                             [("coordinator_clear", "old"), ("coordinator_clear", "old"),
                              ("coordinator_claim", "new"), ("coordinator_clear", "new")])
            self.assertEqual(overlay.readback()["learned"], {})
            self.assertFalse(overlay.readback()["clear_pending"])
            restored = HueOverlay(core, learn, ha, context, root)
            self.assertEqual(restored.state["correction"], {"stream_id": "stream", "seq": 9})
            self.assertEqual(restored.learner.entries, {})


if __name__ == "__main__":
    unittest.main()

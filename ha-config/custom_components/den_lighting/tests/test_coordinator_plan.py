"""Coordinator leases, socket policy, and correction records without a Home Assistant runtime."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("den_policy_11", ROOT / "policy.py")
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
A, B, C, D = p.LIGHTS
WARM = {"on": True, "brightness": 90, "kelvin": 2200}


class PlanTests(unittest.TestCase):
    def make(self, coordinator=True, gaming=True):
        c = p.Controller()
        for e in p.LIGHTS:
            c.observe(e, p.Scene(True, 254, 3000), 1000)
        c.set_occupancy("on", 1000)
        c.set_enabled(True, 1000)
        c.set_gaming_enabled(gaming, 1000)
        c.set_coordinator(coordinator, 1000)
        return c

    def plan(self, c, generation=1, scenes=None, now=1000, valid=60, transition=2,
             situation="sitting/evening", release=False):
        if c.publisher is None:
            c.claim(c.authority_epoch, "sender", c.claim_revision)
        c.apply_plan("plan-%d" % generation, generation, valid, transition, situation,
                     {A: WARM} if scenes is None else scenes, now, epoch=c.authority_epoch,
                     publisher=c.publisher, release=release)

    def test_a_plan_sets_only_the_lights_it_names_under_its_own_transition(self):
        c = self.make()
        self.plan(c, transition=3)
        cmds = c.reconcile(1001)
        self.assertEqual([cmd.entity_id for cmd in cmds], [A])
        self.assertEqual((cmds[0].target, cmds[0].transition, cmds[0].plan_id, cmds[0].generation),
                         (p.Scene(True, 90, 2200), 3.0, "plan-1", 1))

    def test_refused_while_the_switch_or_automatic_is_off(self):
        with self.assertRaises(ValueError):
            self.plan(self.make(coordinator=False))
        c = self.make()
        c.set_enabled(False, 1000)
        with self.assertRaises(ValueError):
            self.plan(c)

    def test_a_hold_beats_the_plan(self):
        c = self.make()
        c.manual(A, p.Scene(True, 200, 3000), 1000)
        self.plan(c)
        self.assertEqual(c.reconcile(1001), [])
        self.assertEqual(c.desired(A, 1001), p.Scene(True, 200, 3000))

    def test_vacancy_beats_the_plan(self):
        c = self.make()
        self.plan(c, valid=120)
        c.set_occupancy("off", 1000)
        self.assertEqual(c.desired(A, 1010), None)
        self.assertEqual(c.desired(A, 1121), p.Scene(False))

    def test_a_lapsed_plan_falls_back_gently(self):
        c = self.make()
        self.plan(c, valid=10)
        for cmd in c.reconcile(1001):
            c.observe(cmd.entity_id, cmd.target, 1002)
        back = c.reconcile(1011)
        self.assertEqual([(cmd.entity_id, cmd.target, cmd.transition) for cmd in back],
                         [(A, p.Scene(True, 254, 3000), 5)])
        self.assertIsNone(c.plan)

    def test_off_forgets_the_plan_and_on_never_brings_it_back(self):
        c = self.make()
        self.plan(c)
        c.set_coordinator(False, 1001)
        self.assertIsNone(c.plan)
        self.assertEqual(c.desired(A, 1002), p.Scene(True, 254, 3000))
        c.set_coordinator(True, 1003)
        self.assertEqual(c.desired(A, 1004), p.Scene(True, 254, 3000))

    def test_an_older_or_conflicting_generation_is_refused(self):
        c = self.make()
        self.plan(c, generation=5)
        with self.assertRaises(ValueError):
            self.plan(c, generation=4)
        with self.assertRaises(ValueError):
            self.plan(c, generation=5, scenes={})

    def test_quiet_renewal_and_duplicate_past_expiry(self):
        c = self.make()
        self.plan(c)
        self.assertEqual(len(c.reconcile(1000)), 1)
        self.plan(c, generation=2, now=1010)
        self.assertEqual(c.reconcile(1010), [])
        self.assertTrue(c.plan_fresh(1061))
        self.plan(c, generation=2, now=1062)
        self.assertFalse(c.plan_fresh(1070))
        c.reconcile(1070)
        self.plan(c, generation=2, now=1071)
        self.assertIsNone(c.plan)

    def test_claim_and_both_receiver_restart_and_off_on_reject_retired_writes(self):
        c = self.make()
        self.plan(c, generation=8)
        epoch = c.authority_epoch
        with self.assertRaises(ValueError):
            c.claim(epoch, "delayed", 0)
        c.claim(epoch, "replacement", 1)
        c.claim(epoch, "replacement", 1)
        self.assertEqual(c.plan_generation, 8)
        self.assertIsNone(c.plan)
        with self.assertRaises(ValueError):
            c.apply_plan("old", 9, 60, 2, "sitting/evening", {A: WARM}, 1010,
                         epoch=epoch, publisher="sender")
        c.set_coordinator(False, 1010)
        c.set_coordinator(True, 1011)
        restarted = p.Controller.restore(c.dump(), (A, C, D))
        for controller in (c, restarted):
            with self.assertRaises(ValueError):
                controller.claim(epoch, "replacement", 2)
            self.assertIsNone(controller.plan)

    def test_an_empty_plan_clears_the_last_one(self):
        c = self.make()
        self.plan(c)
        for cmd in c.reconcile(1001):
            c.observe(cmd.entity_id, cmd.target, 1002)
        self.plan(c, generation=2, scenes={}, now=1003)
        back = c.reconcile(1004)
        self.assertEqual([(cmd.entity_id, cmd.target) for cmd in back], [(A, p.Scene(True, 254, 3000))])
        self.assertTrue(c.plan_fresh(1004))
        self.assertEqual(c.plan["situation"], "sitting/evening")
        self.plan(c, generation=3, scenes={}, now=1005, release=True)
        self.assertFalse(c.plan_fresh(1005))

    def test_a_plan_bypasses_gaming_for_the_lights_it_names(self):
        c = self.make()
        c.gaming_sample("playing", 1000, 1000)
        c.gaming_sample("playing", 1010, 1010)
        self.plan(c, now=1010, situation="gaming/evening")
        targets = {cmd.entity_id: cmd.target for cmd in c.reconcile(1011)}
        self.assertEqual(targets[A], p.Scene(True, 90, 2200))
        self.assertEqual(targets[B].brightness, 38)  # the others keep today's gaming scene
        c.manual(A, p.Scene(True, 130, 2700), 1011)
        self.assertEqual(c.desired(A, 1060), p.Scene(True, 130, 2700))

    def test_only_this_dens_lights_and_valid_values(self):
        c = self.make()
        for bad in ({"light.elsewhere": WARM}, {A: {"on": "yes"}}):
            with self.assertRaises(ValueError):
                self.plan(c, scenes=bad)
        with self.assertRaises(ValueError):
            self.plan(c, generation=9, valid=500)


class CorrectionTests(unittest.TestCase):
    def test_a_confirmed_change_leaves_a_record_with_before_and_after(self):
        tests = PlanTests()
        c = tests.make()
        c.gaming_sample("idle", 1000, 1000)
        tests.plan(c, scenes={})
        before = p.Scene(True, 90, 2200)
        record = c.begin_correction(A, before, "context", 1005, 1005)
        after = p.Scene(True, 40, 2200)
        c.manual(A, after, 1005)
        self.assertIsNone(c.last_correction)
        c.confirm_correction(record, after)
        r = c.last_correction
        self.assertEqual((r["seq"], r["entity_id"], r["actor"], r["plan_id"], r["situation"]),
                         (1, A, "owner", "plan-1", "sitting/evening"))
        self.assertEqual(r["before"], {"on": True, "brightness": 90, "kelvin": 2200})
        self.assertEqual(r["after"], {"on": True, "brightness": 40, "kelvin": 2200})

    def test_a_provisional_hold_protects_the_light_but_leaves_no_record(self):
        c = PlanTests().make()
        c.manual(B, p.Scene(True, 120, 3000), 1005, update_normal=False)
        self.assertIn(B, c.holds)
        self.assertIsNone(c.last_correction)

    def test_the_sequence_survives_a_restart(self):
        tests = PlanTests()
        c = tests.make()
        c.gaming_sample("idle", 1000, 1000)
        tests.plan(c, scenes={})
        c.begin_correction(A, p.Scene(True, 40, 2200), "context", 1005, 1005)
        again = p.Controller.restore(c.dump())
        self.assertEqual(again.correction_seq, 1)
        self.assertEqual(again.correction_stream_id, c.correction_stream_id)
        self.assertIsNone(again.last_correction)


class SocketTests(unittest.TestCase):
    def test_a_socket_list_replaces_the_four_bulbs(self):
        lights = (A, "light.fixture_f3_room", C, D)
        c = p.Controller(lights)
        for e in lights:
            c.observe(e, p.Scene(True, 200, 3000), 1000)
        c.set_occupancy("on", 1000)
        c.set_enabled(True, 1000)
        c.manual(B, p.Scene(True, 1, 3000), 1000)  # the old bulb is no longer this den's
        self.assertNotIn(B, c.holds)
        self.assertEqual(c.warmest["light.fixture_f3_room"], p.DEFAULT_WARMEST)

    def test_restore_drops_a_light_that_left_and_loads_1_0_4_data(self):
        old = PlanTests().make(coordinator=False)
        old.manual(B, p.Scene(True, 50, 3000), 1000)
        data = old.dump()
        for key in ("coordinator_enabled", "correction_seq", "correction_stream_id", "last_correction"):
            data.pop(key)  # what 1.0.4 stored
        swapped = p.Controller.restore(data, (A, "light.fixture_f3_room", C, D))
        self.assertNotIn(B, swapped.holds)
        self.assertFalse(swapped.coordinator_enabled)
        self.assertEqual(swapped.correction_seq, 0)

    def test_status_shows_the_plan_and_the_last_correction(self):
        tests = PlanTests()
        c = tests.make()
        tests.plan(c, situation="task/night")
        status = c.status(1001)
        self.assertEqual((status["coordinator"]["plan_id"], status["coordinator"]["fresh"],
                          status["coordinator"]["entities"]), ("plan-1", True, [A]))
        self.assertIn("last_correction", status)


if __name__ == "__main__":
    unittest.main()

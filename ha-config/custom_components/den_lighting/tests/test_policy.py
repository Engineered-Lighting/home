"""Deterministic regressions for the real Den policy (no HA runtime)."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("den_policy", ROOT / "policy.py")
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)


class PolicyTests(unittest.TestCase):
    def make(self, gaming=True):
        c = p.Controller()
        for e in p.LIGHTS:
            c.observe(e, p.Scene(True, 254, 3000), 1000)
        c.set_occupancy("on", 1000)
        c.set_enabled(True, 1000)
        c.set_gaming_enabled(gaming, 1000)
        return c

    def play(self, c, at=1000):
        c.gaming_sample("playing", at, at)
        c.gaming_sample("playing", at + 10, at + 10)
        return c.reconcile(at + 10)

    def apply(self, c, commands, now):
        for cmd in commands:
            c.observe(cmd.entity_id, cmd.target, now)

    def test_gaming_dim_manual_brightness_does_not_rebound(self):
        c = self.make()
        cmds = self.play(c)
        self.assertEqual(len(cmds), 4)
        self.assertTrue(all(cmd.target.brightness == 38 for cmd in cmds))
        self.apply(c, cmds, 1011)
        c.manual(p.LIGHTS[0], p.Scene(True, 153, 2600), 1012)
        self.assertEqual(c.reconcile(1013), [])
        c.gaming_sample("playing", 1020, 1020)
        self.assertEqual(c.reconcile(1021), [])
        self.assertEqual(c.holds[p.LIGHTS[0]].scene.brightness, 153)

    def test_manual_off_and_small_adjustment_are_held(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.manual(p.LIGHTS[0], p.Scene(False, 38, 1000), 1012)
        c.manual(p.LIGHTS[1], p.Scene(True, 39, 1100), 1013)
        self.assertEqual(c.reconcile(1014), [])
        self.assertEqual(len(c.holds), 2)

    def test_existing_manual_hold_wins_gaming_entry(self):
        c = self.make()
        c.manual(p.LIGHTS[0], p.Scene(True, 100, 2700), 1000)
        self.assertNotIn(p.LIGHTS[0], [cmd.entity_id for cmd in self.play(c)])

    def test_confirmed_exit_restores_snapshot_over_five_seconds(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.manual(p.LIGHTS[0], p.Scene(True, 150, 2500), 1012)
        c.gaming_sample("idle", 1020, 1020)
        c.gaming_sample("idle", 1030, 1030)
        c.gaming_sample("idle", 1050, 1050)
        cmds = c.reconcile(1050)
        self.assertFalse(c.gaming)
        self.assertFalse(c.holds)
        self.assertEqual(len(cmds), 4)
        self.assertTrue(all(cmd.target == p.Scene(True, 254, 3000) and cmd.transition == 5 for cmd in cmds))

    def test_game_holds_survive_vacancy_and_return(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.manual(p.LIGHTS[0], p.Scene(True, 150, 2500), 1012)
        c.set_occupancy("off", 1020)
        c.gaming_sample("playing", 1140, 1140)
        cmds = c.reconcile(1140)
        self.assertEqual(len(cmds), 4)
        self.assertTrue(all(not cmd.target.on for cmd in cmds))
        self.apply(c, cmds, 1141)
        c.set_occupancy("on", 1142)
        cmds = c.reconcile(1142)
        self.assertEqual(next(x.target.brightness for x in cmds if x.entity_id == p.LIGHTS[0]), 150)
        self.assertIn(p.LIGHTS[0], c.holds)

    def test_generic_hold_requires_vacancy_after_latest_touch(self):
        c = self.make(False)
        c.set_occupancy("off", 1001)
        c.manual(p.LIGHTS[0], p.Scene(True, 100, 3000), 1100)
        c.reconcile(1121)
        self.assertIn(p.LIGHTS[0], c.holds)
        c.reconcile(1220)
        self.assertNotIn(p.LIGHTS[0], c.holds)

    def test_unknown_occupancy_breaks_vacancy_and_blocks_writes(self):
        c = self.make(False)
        c.manual(p.LIGHTS[0], p.Scene(True, 100, 3000), 1000)
        c.set_occupancy("off", 1001)
        c.set_occupancy("unknown", 1120)
        self.assertEqual(c.reconcile(1400), [])
        self.assertIn(p.LIGHTS[0], c.holds)
        c.set_occupancy("off", 1401)
        c.reconcile(1500)
        self.assertIn(p.LIGHTS[0], c.holds)

    def test_normal_arrival_restores_lights_without_fresh_gaming_signal(self):
        for signal in ("never_received", "unknown", "stale_idle"):
            with self.subTest(signal=signal):
                c = self.make()
                if signal == "stale_idle":
                    c.gaming_sample("idle", 1000, 1000)
                elif signal == "unknown":
                    c.gaming_sample("unknown", 1000, 1000)
                c.set_occupancy("off", 1001)
                off = c.reconcile(1121)
                self.assertEqual(len(off), 4)
                self.assertTrue(all(not cmd.target.on for cmd in off))
                self.apply(c, off, 1122)
                c.set_occupancy("on", 1602)
                self.assertFalse(c.gaming)
                self.assertFalse(c.fresh_game(1602))
                arrival = c.reconcile(1602)
                self.assertEqual(len(arrival), 4)
                self.assertTrue(all(cmd.target == p.Scene(True, 254, 3000) for cmd in arrival))

    def test_active_game_unknown_signal_keeps_arrival_frozen(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.manual(p.LIGHTS[0], p.Scene(True, 120, 3000), 1012)
        c.set_occupancy("off", 1020)
        self.apply(c, c.reconcile(1140), 1141)
        c.gaming_sample("unknown", 1142, 1142)
        c.set_occupancy("on", 1143)
        self.assertEqual(c.reconcile(1143), [])
        self.assertTrue(c.gaming)
        self.assertEqual(c.holds[p.LIGHTS[0]].scene, p.Scene(True, 120, 3000))

    def test_stale_or_unknown_game_does_not_clear_hold_or_brighten(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.manual(p.LIGHTS[0], p.Scene(True, 120, 3000), 1012)
        c.gaming_sample("unknown", 1020, 1020)
        self.assertEqual(c.reconcile(1200), [])
        self.assertTrue(c.gaming)
        self.assertIn(p.LIGHTS[0], c.holds)

    def test_duplicate_future_stale_samples_cannot_start_game(self):
        for status, ts, now in [("playing", 2000, 1000), ("playing", 900, 1000)]:
            c = self.make()
            c.gaming_sample(status, ts, now)
            c.gaming_sample(status, ts, now)
            self.assertFalse(c.gaming)
        c = self.make()
        c.gaming_sample("playing", 1000, 1000)
        c.gaming_sample("playing", 1000, 1001)
        self.assertFalse(c.gaming)

    def test_disconnected_idle_samples_do_not_end_game(self):
        c = self.make()
        self.play(c)
        c.gaming_sample("idle", 1020, 1020)
        c.gaming_sample("idle", 1100, 1100)
        self.assertTrue(c.gaming)

    def test_restore_requalifies_vacancy_and_preserves_holds(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.manual(p.LIGHTS[0], p.Scene(False, 38, 1000), 1012)
        restored = p.Controller.restore(c.dump())
        self.assertTrue(restored.gaming)
        self.assertIn(p.LIGHTS[0], restored.holds)
        self.assertEqual(restored.reconcile(2000), [])
        self.assertEqual(restored.occupancy, "unknown")

    def test_resume_only_selected_fixture(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        for e in p.LIGHTS[:2]:
            c.manual(e, p.Scene(True, 100, 2700), 1012)
        c.resume([p.LIGHTS[0]], 1013)
        cmds = c.reconcile(1013)
        self.assertEqual([x.entity_id for x in cmds], [p.LIGHTS[0]])
        self.assertIn(p.LIGHTS[1], c.holds)

    def test_disabled_controller_never_writes(self):
        c = self.make()
        self.play(c)
        c.set_enabled(False, 1011)
        c.set_occupancy("off", 1012)
        self.assertEqual(c.reconcile(1300), [])

    def test_game_exit_while_vacant_never_turns_room_on(self):
        c = self.make()
        self.apply(c, self.play(c), 1011)
        c.set_occupancy("off", 1012)
        self.apply(c, c.reconcile(1132), 1132)
        for t in (1140, 1150, 1170):
            c.gaming_sample("idle", t, t)
        self.assertTrue(all(not cmd.target.on for cmd in c.reconcile(1170)))
        c.set_occupancy("on", 1180)
        self.assertTrue(all(cmd.target.brightness == 254 for cmd in c.reconcile(1180)))

    def test_game_started_while_vacant_preserves_last_normal_levels(self):
        c = self.make()
        c.set_occupancy("off", 1000)
        self.apply(c, c.reconcile(1120), 1120)
        # HA drops brightness/CCT from an OFF state. That does not mean the
        # normal scene or its last-level arrival preference has disappeared.
        for e in p.LIGHTS:
            c.observe(e, p.Scene(False), 1121)
        self.play(c, 1130)
        for t in (1150, 1160, 1180):
            c.gaming_sample("idle", t, t)
        c.set_occupancy("on", 1190)
        cmds = c.reconcile(1190)
        self.assertEqual(len(cmds), 4)
        self.assertTrue(all(cmd.target.brightness == 254 and cmd.target.kelvin == 3000 for cmd in cmds))


if __name__ == "__main__":
    unittest.main()

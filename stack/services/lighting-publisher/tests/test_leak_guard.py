"""Leak guard: one hit per pattern on an adversarial packet, none on the goldens, no echo."""
import getpass
import json
import os
import tempfile
import unittest
from pathlib import Path

from lighting_beliefs import leak_guard as lg

FIXTURES = Path(__file__).parent / "fixtures"
USERNAME = "opsuser"
ROSTER = ("Alice Example", "Bobby")


def adversarial_packet():
    """One seeded leak per pattern, each under its own key so paths are checkable."""
    return {
        "schema": "lighting-beliefs-state/v1",
        "seeds": {
            "ha_entity_id": "toggle binary_sensor.living_room_sofa_occupancy went on",
            "container_name": "logs from hav-observer-1 show",
            "lan_ip": "camera at 192.168.7.44 stalled",
            "local_hostname": "nas.local unreachable",
            "jwt_prefix": "token eyJhbGciOiJIUzI1NiJ9.payload",
            "model_name": "captioned by qwen3-vl-30b at loopback",
            "rtsp_url": "stream rtsp://user:pw@cam.example:554/live",
            "digit_run": "event 1789697770",
            "digit_group": "phone 555-123-4567",
            "email": "mail someone@example.com now",
            "http_url": "see https://example.com/x",
            "iso_timestamp": "at 2026-09-17T19:25:00 pacific",
            "slashed_date": "overlay reads 09/17/2026",
            "time_of_day": "clock on the wall reads 19:25",
            "format_char": "zero\u200bwidth",
            "non_ascii": "caf\u00e9",
            "os_username": f"/home/{USERNAME}/vjepa-home",
            "roster_name": "alice example is on the sofa",
        },
        "numeric_epoch": 1789697770,
        "key_leak": {"light.sofa_lamp": "on"},
    }


class LeakGuardTest(unittest.TestCase):
    def test_zero_misses_one_hit_per_pattern(self):
        findings = lg.scan_packet(adversarial_packet(), USERNAME, roster=ROSTER)
        by_pattern = {}
        for f in findings:
            by_pattern.setdefault(f.pattern, set()).add(f.key_path)
        for name in lg.PATTERN_NAMES:
            self.assertIn(f"$.seeds.{name}", by_pattern.get(name, set()), f"pattern {name} missed its seed")
        self.assertIn("$.numeric_epoch", by_pattern["digit_run"])
        self.assertIn("$.key_leak.<key#0>", by_pattern["ha_entity_id"])
        for f in findings:
            self.assertEqual(set(f.as_dict()), {"pattern", "key_path", "match_len", "in_key"})

    def test_formatted_numbers_clocks_and_hosts(self):
        for text, expected in (
            ("555-123-4567", "digit_group"), ("1,234,567", "digit_group"), ("1 234 567", "digit_group"),
            ("overlay reads 09/17/2026 19:25:03", "slashed_date"), ("overlay reads 09/17/2026 19:25:03", "time_of_day"),
            ("clock on the wall reads 19:25", "time_of_day"), ("at 7:05 pm", "time_of_day"),
            ("nas.local unreachable", "local_hostname"), ("Printer.LAN down", "local_hostname"),
            ("router.home", "local_hostname"), ("db.internal", "local_hostname"),
            ("Light.Sofa_Lamp", "ha_entity_id"), ("BINARY_SENSOR.Door", "ha_entity_id"),
            ("light.\u200bsofa_lamp", "ha_entity_id"), ("light.\u200bsofa_lamp", "format_char"),
            ("l\u0456ght.sofa_lamp", "non_ascii"),
        ):
            with self.subTest(text=text, expected=expected):
                self.assertIn(expected, [name for name, _ in lg.scan_text(text)])
        for clean in ("3 people, 2 on the sofa, 1 standing", "age 3 4 5", "one person reclined", "score 12 to 9"):
            with self.subTest(text=clean):
                self.assertEqual(lg.scan_text(clean), [])

    def test_zero_false_positives_on_goldens(self):
        for name in ("tv_evening.json", "quiet_night.json"):
            packet = json.loads((FIXTURES / name).read_text())
            for user in (USERNAME, getpass.getuser()):
                self.assertEqual(lg.scan_packet(packet, user, roster=ROSTER), [], name)
                lg.assert_clean(packet, user, roster=ROSTER)

    def test_assert_clean_blocks_without_leaking_text(self):
        with self.assertRaises(lg.LeakError) as ctx:
            lg.assert_clean({"a": "rtsp://cam.example/live"}, USERNAME)
        self.assertIn("rtsp_url at $.a", str(ctx.exception))
        self.assertNotIn("cam.example", str(ctx.exception))
        self.assertFalse(hasattr(lg, "strip"))
        self.assertFalse(hasattr(lg, "redact"))

    def test_leaking_keys_get_placeholder_paths(self):
        packet = {"key_leak": {"light.sofa_lamp": {"nested": "rtsp://cam.example/live"}},
                  "cameras": {"alice example": {}}}
        findings = lg.scan_packet(packet, USERNAME, roster=ROSTER)
        with self.assertRaises(lg.LeakError) as ctx:
            lg.assert_clean(packet, USERNAME, roster=ROSTER)
        rendered = str(ctx.exception) + json.dumps([f.as_dict() for f in findings])
        for secret in ("sofa_lamp", "alice", "cam.example"):
            self.assertNotIn(secret, rendered)
        paths = {(f.pattern, f.key_path, f.in_key) for f in findings}
        self.assertIn(("ha_entity_id", "$.key_leak.<key#0>", True), paths)
        self.assertIn(("rtsp_url", "$.key_leak.<key#0>.nested", False), paths)
        self.assertIn(("roster_name", "$.cameras.<key#0>", True), paths)

    def test_username_must_be_supplied(self):
        with self.assertRaises(ValueError):
            lg.scan_packet({}, "")

    def test_roster_tokens_and_spacing(self):
        roster = ("Marisol Quintero", "Teo")
        for text in ("Quintero is asleep", "marisol dozed", "Marisol  Quintero", "Marisol\nQuintero",
                     "Marisol's blanket", "teo naps"):
            with self.subTest(text=text):
                findings = lg.scan_packet({"c": text}, USERNAME, roster=roster)
                self.assertEqual({f.pattern for f in findings}, {"roster_name"})
        for text in ("Teodoro asleep", "one person on the sofa"):
            with self.subTest(text=text):
                self.assertEqual(lg.scan_packet({"c": text}, USERNAME, roster=roster), [])
        patterns = [name for name, _ in lg._identifier_patterns(USERNAME, ("Ann Ann", "ann"))]
        self.assertEqual(patterns.count("roster_name"), 2)

    def test_roster_mode_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roster.txt"
            path.write_text("# household\nAlice Example\n\nBobby\n")
            os.chmod(path, 0o644)
            with self.assertRaises(lg.RosterError):
                lg.load_roster(path)
            os.chmod(path, 0o600)
            self.assertEqual(lg.load_roster(path), ROSTER)
            findings = lg.scan_packet({"c": "Bobby dozed off"}, USERNAME, roster_path=path)
            self.assertEqual([f.pattern for f in findings], ["roster_name"])
            with self.assertRaises(lg.RosterError):
                lg.load_roster(Path(tmp) / "missing.txt")


if __name__ == "__main__":
    unittest.main()

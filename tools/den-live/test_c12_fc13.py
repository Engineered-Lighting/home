"""Exact FC13 bytes and refusal cases for the transport-free encoder."""

import json
import struct
import unittest
from pathlib import Path

import c12_fc13 as wire


GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "c12-preview-golden.json").read_text(encoding="utf-8")
)


class EncodeTest(unittest.TestCase):
    def setUp(self):
        self.profile = GOLDEN["profile"]
        self.start = GOLDEN["start_joints_cdeg"]
        self.intent = GOLDEN["steps"][0]["intent"]

    def move(self, *, profile=None, start=None, **fields):
        return wire.encode(dict(self.intent, **fields), self.profile if profile is None else profile,
                           seq=7, current_joints=self.start if start is None else start)

    def test_golden_command_bytes(self):
        current = self.start
        token = None
        for step in GOLDEN["steps"]:
            intent, seq = step["intent"], step["seq"]
            with self.subTest(seq=seq):
                out = wire.encode(intent, self.profile, seq=seq, current_joints=current, token=token)
                self.assertEqual({
                    "endpoint": out["endpoint"], "cluster": out["cluster"], "command_id": out["command_id"],
                    "seq": seq, "payload_hex": out["payload"].hex(),
                }, step["frame"])
                if intent["action"] == "aim_move":
                    current = [out["effective"]["pan_cdeg"], out["effective"]["tilt_cdeg"]]
                    token = seq if out["effective"]["spot_level"] > 0 else None
                elif intent["action"] == "stop":
                    token = None

    def test_quantization_matches_payload(self):
        result = self.move(level=0.25, cct_k=4000)
        self.assertEqual(struct.unpack("<HBhhBHhhH", result["payload"]),
                         (7, 9, 4500, -3000, 64, 250, 0, 0, 0))
        self.assertEqual(result["effective"]["level"], 64 / 254)
        self.assertEqual(result["effective"]["cct_k"], 4000)

    def test_effective_style_and_unrepresented_controls(self):
        cases = [
            ({}, {"motion_style": "lit", "lit_during_move": False}, "dark"),
            ({}, {"motion_style": "dim", "level_during_move": 0.3}, "dark"),
            ({"motion_mode": 3}, {"motion_style": "lit"}, "dark"),
            ({"lit_motion": False}, {"motion_style": "lit"}, "dark"),
            ({}, {"level": 0.0}, "dark"),
        ]
        for profile, fields, motion_style in cases:
            with self.subTest(profile=profile, fields=fields):
                effective = self.move(profile=dict(self.profile, **profile), fade_s=0.7, **fields)["effective"]
                self.assertEqual(effective["motion_style"], motion_style)
                self.assertEqual(effective["flags"] & 1, 0)
                self.assertFalse(effective["fade_supported"])
                self.assertFalse(effective["dim_supported"])
        stationary = self.move(start=[4500, -3000])["effective"]
        self.assertEqual(stationary["motion_style"], "stationary")
        self.assertFalse(stationary["lit_during_move"])

    def test_signed_velocity_and_source_flags(self):
        profile = dict(self.profile, terminal_velocity=True, max_velocity_cdeg_s=[1500, 300])
        result = self.move(profile=profile, source="hand", velocity_cdeg_s=[-1200, 300])
        self.assertEqual(result["effective"]["flags"], 15)
        self.assertEqual(struct.unpack("<HBhhBHhhH", result["payload"])[6:8], (-1200, 300))
        with self.assertRaisesRegex(wire.Refusal, "terminal_velocity_out_of_range"):
            self.move(profile=profile, velocity_cdeg_s=[-1200, 301])
        with self.assertRaisesRegex(wire.Refusal, "terminal_velocity_unsupported"):
            self.move(velocity_cdeg_s=[-1200, 300])

    def test_deadline_shortens_default_without_using_zero_for_expiry(self):
        result = wire.encode(dict(self.intent, valid_for_ms=1500), self.profile, seq=7,
                             current_joints=self.start, remaining_ms=650)
        self.assertEqual(result["effective"]["valid_ms"], 650)
        self.assertEqual(result["payload"][-2:], b"\x8a\x02")
        result = wire.encode(self.intent, self.profile, seq=7, current_joints=self.start, remaining_ms=1100)
        self.assertEqual(result["effective"]["valid_ms"], 0)
        with self.assertRaisesRegex(wire.Refusal, "command_expired"):
            wire.encode(self.intent, self.profile, seq=7, current_joints=self.start, remaining_ms=650)

    def test_refusals_for_incomplete_or_impossible_intent(self):
        cases = [
            ({}, {"decision_id": ""}, "missing_decision_id"),
            ({}, {"pan_cdeg": 4500.1}, "invalid_pan_cdeg"),
            ({}, {"pan_cdeg": 17501}, "invalid_pan_cdeg"),
            ({}, {"tilt_cdeg": -9001}, "invalid_tilt_cdeg"),
            ({}, {"frame": "compile_target"}, "unsupported_joint_frame"),
            ({}, {"profile_sha256": "0" * 64}, "fixture_profile_mismatch"),
            ({}, {"cct_k": 10000}, "invalid_spot_mired"),
            ({}, {"level": float("nan")}, "invalid_level"),
            ({}, {"valid_for_ms": 65536}, "invalid_valid_for_ms"),
            ({"motion_mode": 0}, {}, "lights_only_profile"),
            ({"fixture_id": "F2"}, {}, "fixture_profile_mismatch"),
        ]
        for profile, fields, reason in cases:
            with self.subTest(profile=profile, fields=fields):
                with self.assertRaisesRegex(wire.Refusal, reason):
                    self.move(profile=dict(self.profile, **profile), **fields)
        with self.assertRaisesRegex(wire.Refusal, "travel_exceeds_180_degrees"):
            self.move(start=[17000, 0], pan_cdeg=-17000)
        self.assertEqual(self.move(start=[1000, 0], pan_cdeg=-17000)["effective"]["pan_cdeg"], -17000)
        with self.assertRaisesRegex(wire.Refusal, "unknown_start_joints"):
            wire.encode(self.intent, self.profile, seq=7)

    def test_control_packets_allow_sequence_wrap_without_pose(self):
        stop = {"action": "stop", "fixture_id": "F3"}
        resume = {"action": "resume", "fixture_id": "F3"}
        self.assertEqual(wire.encode(stop, self.profile, seq=65535)["payload"], b"\xff\xff")
        self.assertEqual(wire.encode(resume, self.profile, seq=0)["payload"], b"\x00\x00")
        keep = GOLDEN["steps"][1]["intent"]
        self.assertEqual(wire.encode(keep, self.profile, seq=0, token=0)["payload"], b"\x00" * 4)
        with self.assertRaisesRegex(wire.Refusal, "invalid_token"):
            wire.encode(keep, self.profile, seq=1)
        with self.assertRaisesRegex(wire.Refusal, "keep_lit_requires_coordinator"):
            wire.encode(dict(keep, source="hand"), self.profile, seq=1, token=0)
        with self.assertRaisesRegex(wire.Refusal, "invalid_seq"):
            wire.encode(stop, self.profile, seq=True)


if __name__ == "__main__":
    unittest.main()

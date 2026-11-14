"""Offline fixture vectors. Set DEN_C12_CODEC_ZIP to an alternate local copy of the pinned bundle."""

import hashlib
import json
import os
import struct
import sys
import types
import unittest
import zipfile
from pathlib import Path

import preview_trace as trace


HERE = Path(__file__).resolve().parent
F3 = json.loads((HERE / "fixtures" / "preview-f3.json").read_text(encoding="utf-8"))
BUNDLE = Path("artifacts/human-context-architecture-v2-20261027/review/end-to-end-20261111/firmware-bundle/") / (
    "c12-firmware-for-codex-20261112.zip"
)
CODEC_HASH = "0f534b0fd739c756d91fd3870df569d171fe030277319641fb1fdb718c702361"


def f3_trace():
    rows = []
    writer = trace.PreviewTrace(F3["epoch"], rows.append, initial_sequence=F3["initial_sequence"],
                                binding=F3["snapshot"]["binding"], profiles={F3["fixture_id"]: F3["profile"]})
    for expected in F3["frames"]:
        writer.emit(F3["fixture_id"], expected["action"], decision_id=F3["decision_id"],
                    move_token=expected.get("move_token"), move=F3["move"] if expected["action"] == "aim_move" else None)
    return rows


class TraceTest(unittest.TestCase):
    def test_shared_f3_trace_matches_snapshot(self):
        rows = f3_trace()
        self.assertEqual(len(rows), len(F3["frames"]))
        for row, expected in zip(rows, F3["frames"]):
            self.assertEqual({key: row[key] for key in expected}, expected)
            self.assertEqual((row["outcome"], row["sent"], row["endpoint"], row["cluster"]),
                             ("encoded_only", False, 18, 0xFC13))
            self.assertEqual((row["schema"], row["run_id"], row["registration_revision"]),
                             ("c12-fixture-trace-3", F3["epoch"], F3["snapshot"]["binding"]["scan_revision"]))
            self.assertRegex(row["profile_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(row["frame"], {"endpoint": 18, "cluster": 0xFC13, "command_id": expected["command"],
                                            "seq": expected["sequence"], "payload_hex": expected["payload_hex"]})
        fixture = F3["snapshot"]["fixtures"][F3["fixture_id"]]
        move, keep, stop, resume = rows
        self.assertEqual((fixture["move_sequence"], fixture["keep_lit_token"]), (move["sequence"], keep["move_token"]))
        self.assertEqual((keep["move_seq"], keep["move_decision_id"]), (move["sequence"], move["decision_id"]))
        self.assertEqual((fixture["last_move_trace_id"], fixture["move_decision_id"]), (move["trace_id"], move["decision_id"]))
        self.assertEqual((fixture["pan_deg"], fixture["tilt_deg"]), tuple(-axis / 100 for axis in move["effective"]["target_cdeg"]))
        self.assertEqual((fixture["level"], fixture["cct_k"]), (move["effective"]["level"], move["effective"]["display_cct_k"]))
        self.assertEqual(move["effective"]["spot_mired"], 370)
        self.assertEqual(move["effective"]["cct_k"], 1_000_000 / 370)
        stopped_fixture = F3["stopped_snapshot"]["fixtures"][F3["fixture_id"]]
        self.assertEqual(stopped_fixture["last_move_trace_id"], move["trace_id"])
        self.assertIsNone(stopped_fixture["keep_lit_token"])
        self.assertFalse(stopped_fixture["on"])
        self.assertGreater(resume["sequence"], stop["sequence"])

    def test_route_inset_and_boundary(self):
        rejected = F3["route_refusal"]
        rows = []
        writer = trace.PreviewTrace("route", rows.append)
        invalid = dict(F3["move"], start_cdeg=rejected["start_cdeg"], target_cdeg=rejected["target_cdeg"])
        refused = writer.emit("F3", "aim_move", decision_id="route", move=invalid)
        self.assertEqual(refused["reason"], rejected["reason"])
        self.assertIsNone(refused["sequence"])
        self.assertNotIn("payload_hex", refused)
        exactly_180 = dict(F3["move"], start_cdeg=[1000, -3000], target_cdeg=[-17000, -3000])
        self.assertEqual(writer.emit("F3", "aim_move", decision_id="route", move=exactly_180)["sequence"], 0)
        outside = dict(F3["move"], target_cdeg=[-17001, -3000])
        self.assertEqual(writer.emit("F3", "aim_move", decision_id="route", move=outside)["reason"], "invalid_pan_cdeg")

    def test_dim_fallback_and_wire_only_fields(self):
        move = dict(F3["move"], travel="dim", fade_s=1.75, valid_ms=1500, velocity_cdeg_s=[-120, 0],
                    terminal_velocity=True, max_velocity_cdeg_s=[500, 500])
        row = trace.PreviewTrace("dim", lambda _row: None).emit("F3", "aim_move", decision_id="dim", move=move)
        self.assertEqual((row["requested"]["travel"], row["requested"]["fade_s"]), ("dim", 1.75))
        self.assertEqual((row["effective"]["travel"], row["effective"]["fade_s"]), ("dark", None))
        self.assertEqual(struct.unpack("<HBhhBHhhH", bytes.fromhex(row["payload_hex"])),
                         (0, 0x0C, 4500, -3000, 200, 370, -120, 0, 1500))

    def test_deadline_shortens_wire_validity_and_never_uses_default_for_expiry(self):
        writer = trace.PreviewTrace("deadline", lambda _row: None)
        capped = writer.emit("F3", "aim_move", decision_id="first", move=dict(F3["move"], valid_ms=1501))
        self.assertEqual(capped["reason"], "invalid_valid_for_ms")
        explicit = dict(F3["move"], valid_ms=1500)
        limited = writer.emit("F3", "aim_move", decision_id="first", move=dict(explicit, remaining_ms=300))
        self.assertEqual(limited["effective"]["valid_ms"], 300)
        self.assertEqual(limited["payload_hex"][-4:], "2c01")
        expired = writer.emit("F3", "aim_move", decision_id="first", move=dict(explicit, remaining_ms=0))
        self.assertEqual(expired["reason"], "command_expired")

    def test_wrap_and_sequence_isolation(self):
        writer = trace.PreviewTrace("wrap", lambda _row: None, initial_sequence=65535)
        one = writer.emit("F3", "aim_move", decision_id="first", move=F3["move"])
        unrelated = writer.emit("F2", "stop", decision_id="different")
        keep = writer.emit("F3", "keep_lit", decision_id="first", move_token=one["sequence"])
        self.assertEqual((one["sequence"], unrelated["sequence"], keep["sequence"]), (65535, 65535, 0))
        self.assertEqual((keep["payload_hex"], keep["decision_id"]), ("0000ffff", "first"))
        self.assertEqual(len({row["trace_id"] for row in (one, unrelated, keep)}), 3)
        self.assertEqual(writer.emit("F3", "keep_lit", decision_id="first")["reason"], "invalid_token")

    def test_failed_append_does_not_reuse_trace_or_sequence(self):
        rows = []

        def append(row):
            rows.append(row)
            if len(rows) == 1:
                raise OSError("append failed")

        writer = trace.PreviewTrace("io", append)
        with self.assertRaises(OSError):
            writer.emit("F3", "aim_move", decision_id="first", move=F3["move"])
        stop = writer.emit("F3", "stop")
        self.assertEqual((stop["sequence"], stop["trace_id"]), (1, "io/F3/trace-2"))

    def test_pin_independently_encodes_the_same_frames(self):
        provided = os.getenv("DEN_C12_CODEC_ZIP")
        bundle = Path(provided) if provided else next((p / BUNDLE for p in HERE.parents if (p / BUNDLE).is_file()), None)
        if bundle is None:
            self.skipTest("private C12 firmware bundle absent")
        with zipfile.ZipFile(bundle) as archive:
            entry = next(name for name in archive.namelist() if name.endswith("/tools/c12v2/fc13.py"))
            source = archive.read(entry)
        self.assertEqual(hashlib.sha256(source).hexdigest(), CODEC_HASH)
        codec = types.ModuleType("_preview_trace_pinned_fc13")
        sys.modules[codec.__name__] = codec
        try:
            exec(compile(source, entry, "exec"), codec.__dict__)
        finally:
            del sys.modules[codec.__name__]
        move, keep, stop, resume = f3_trace()
        target = move["effective"]
        expected_move = codec.Move(7, codec.FLAG_LIT_DURING_MOVE | codec.FLAG_SET_SPOT, 4500, -3000, 200, 370)
        payload = bytes.fromhex(move["payload_hex"])
        self.assertEqual(payload, codec.encode_move(expected_move))
        self.assertEqual(codec.parse_move(payload), expected_move)
        self.assertEqual(target["effective_valid_ms"], expected_move.effective_valid_ms)
        self.assertEqual(bytes.fromhex(keep["payload_hex"]), codec.encode_keep_lit(8, 7))
        self.assertEqual(bytes.fromhex(stop["payload_hex"]), codec.encode_seq_only(9))
        self.assertEqual(bytes.fromhex(resume["payload_hex"]), codec.encode_seq_only(10))


if __name__ == "__main__":
    unittest.main()

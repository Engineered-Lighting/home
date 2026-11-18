import json
import tempfile
import unittest
from pathlib import Path

from c12_computed import ComputedTrace, wire_matches


class ComputedTraceTests(unittest.TestCase):
    def test_firmware_cf1c325_lit_move_vector(self):
        fields = (7, 9, 4500, -3000, 200, 370, 0, 0, 0)
        expected = bytes.fromhex("070009941148f4c87201000000000000")
        self.assertTrue(wire_matches(0, expected, fields))
        self.assertFalse(wire_matches(0, expected[:-1], fields))
        with tempfile.TemporaryDirectory() as tmp:
            sink = ComputedTrace(Path(tmp), "test-epoch")
            sink.sequence["F2"] = 6
            trace = sink.record({"action": "aim_move", "fixture_id": "F2", "decision_id": "decision-1",
                                 "lit_during_move": True, "pan_cdeg": 4500, "tilt_cdeg": -3000,
                                 "level": 200 / 254, "cct_k": 2703, "valid_for_ms": 0}, "2026-11-12")
            line = json.loads((Path(tmp) / "2026-11-12/coordinator-c12-computed.jsonl").read_text())
            self.assertEqual(line["trace_id"], trace)
            self.assertEqual(line["payload_hex"], expected.hex())
            self.assertFalse(line["sent"])

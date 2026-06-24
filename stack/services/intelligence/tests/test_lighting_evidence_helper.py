from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HA_CONFIG = Path(__file__).resolve().parents[4] / "ha-config" / "extended_openai_conversation"
sys.path.insert(0, str(HA_CONFIG))

from lighting_evidence import export_source_lines, source_status  # noqa: E402


class LightingEvidenceHelperTests(unittest.TestCase):
    def test_export_returns_complete_lines_and_keeps_partial_cursor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lighting-evidence-") as tmp:
            path = Path(tmp) / "evidence.jsonl"
            first = json.dumps({"kind": "override_event", "ts": "2026-05-26T20:00:00-07:00"})
            second = json.dumps({"kind": "pending_preference", "ts": "2026-05-26T20:01:00-07:00"})
            path.write_text(first + "\n" + second, encoding="utf-8")

            exported = export_source_lines("preferences", path=path, offset=0, max_bytes=4096)
            self.assertEqual(len(exported["lines"]), 1)
            self.assertFalse(exported["eof"])
            self.assertEqual(exported["lines"][0]["raw"], first)
            self.assertLess(exported["offset_out"], exported["file"]["size"])

            status = source_status("preferences", path=path)
            self.assertTrue(status["exists"])
            self.assertEqual(status["latest"]["kind"], "override_event")

    def test_export_signals_reset_when_offset_is_past_end(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lighting-evidence-") as tmp:
            path = Path(tmp) / "evidence.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            exported = export_source_lines("preferences", path=path, offset=999, max_bytes=64)
            self.assertTrue(exported["reset_required"])
            self.assertEqual(exported["offset_out"], 0)


if __name__ == "__main__":
    unittest.main()

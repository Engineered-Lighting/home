"""Bounded runner command text only: nothing is executed, installed or started."""
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_bounded


class BoundedRunnerTests(unittest.TestCase):
    def test_systemd_properties_are_known_keys_and_keep_oom_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            if any(c.isspace() for c in str(root)):
                self.skipTest("runner refuses whitespace paths by design")
            config = {"profile_id": "vjepa-lm-qwen3-8b", "io_device": "259:0",
                      **{key: str(root / key) for key in ("snapshot_root", "receipt_root", "frame_root", "cache_root",
                                                          "execution_lease_file", "residency_file")}}
            # A fake regular file that is also the configured block device.
            fake = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_rdev=0)
            with patch.object(run_bounded, "operator_config", return_value=config), \
                    patch.object(Path, "stat", lambda self, **kwargs: fake), \
                    patch.object(run_bounded.stat, "S_ISBLK", return_value=True), \
                    patch.object(os, "major", create=True, return_value=259), \
                    patch.object(os, "minor", create=True, return_value=0):
                command = run_bounded.command(root / "operator.json", root / "python3", root / "nvme")
        properties = [item.split("=", 1)[1] for item in command if item.startswith("--property=")]
        self.assertIn("OOMPolicy=kill", properties)
        self.assertIn("KillMode=control-group", properties)
        self.assertFalse([item for item in properties if item.startswith("MemoryOOMGroup")])


if __name__ == "__main__":
    unittest.main()

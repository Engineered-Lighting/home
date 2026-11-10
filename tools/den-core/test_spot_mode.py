"""Tests for the coordinator's spot mode, test 13 of the den simulation's plan. From the worktree root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Everything is synthetic: den-core's own evenings and invented geometry. Nothing here is a real room or reaches a host.
"""
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import incumbent  # noqa: E402
import run  # noqa: E402

HAVE_POLICY = incumbent.find_policy() is not None
# The golden Hue Decision log, taken from the unedited coordinator at 03721630, before spot mode: decisions.jsonl's sha256 for
# run.py --synthetic evening with these flags and --policies need incumbent coordinator. The header is left out, because it pins
# coordinator.py's own hash; each Decision still logs config_sha256, so these move if PINS or _defaults do.
HUE_BEFORE_SPOT_MODE = {
    (): "8cc52378729ee17d2d0dc772378bf6deeec2d4709886bc9517e62dc3ca2440e4",
    ("--touch",): "88f0e28311c32eded49baa4149526dfdfba2cbbf8cfc2501a3fec6310cea9243",
    ("--game",): "7a4e3cd81f53db544a62b9651e8381d50d76d05c541cd093023f4be0ef6ebc6b",
    ("--touch", "--game"): "100824e7a7102ff021c974306b1fab0ea8c21fbf2a3c27466c5171cfcaf965a8",
}


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class HueGoldenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_hue_decisions_equal_the_golden_ones_taken_before_spot_mode(self):
        for n, (flags, sha) in enumerate(HUE_BEFORE_SPOT_MODE.items()):
            with self.subTest(flags=flags), mock.patch("builtins.print"):
                out = self.tmp / str(n)
                run.main(["--synthetic", "evening", *flags, "--policies", "need", "incumbent", "coordinator", "--out", str(out)])
                self.assertEqual(hashlib.sha256((out / "decisions.jsonl").read_bytes()).hexdigest(), sha)


if __name__ == "__main__":
    unittest.main()

"""Public, synthetic-data gate for L3: the tool reproduces lighting spike 1.

No Den data: the room, albedo, emitters and capture camera are procedural.
Run from tools/spatial-pipeline:  python -m unittest discover -s lightbake/tests -t .
"""
import json
import os
import unittest

from lightbake.encode import bytes_per_receiver
from lightbake.spike1 import TARGETS, reproduce


class Spike1Reproduction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = reproduce(n=12000, n_patches=600)
        if os.environ.get("LIGHTBAKE_REPORT"):
            print(json.dumps(cls.r, indent=1))

    def test_exact_case_logrmse(self):
        # Spike 1, exact case: 0.16 / 0.16 / 0.17. Gate: <= 0.2 on every target.
        for t in TARGETS:
            self.assertLessEqual(self.r["targets"][t]["logrmse_exact"], 0.2, t)

    def test_albedo_error_matches_spike(self):
        # Spike 1 with the measured-radiance gather: 5 % median albedo error.
        self.assertLess(self.r["albedo_err_median"], 0.08)

    def test_encoded_layer_keeps_broad_targets(self):
        # The 40 B format must not move broad states (spike 2: 0.8 % median error).
        for t in ("night_ambient", "all_on_warm"):
            self.assertLessEqual(self.r["targets"][t]["logrmse_encoded"], 0.2, t)
        # A single spot loses chroma to the shared bounce tint (spike 2: p90 14 %).
        # Bound it so a regression is caught; the value is reported in the PR.
        self.assertLessEqual(self.r["targets"]["one_spot"]["logrmse_encoded"], 0.35)

    def test_capture_rerender_self_consistency(self):
        # Re-rendering the capture state from the encoded layer matches the
        # shipped colours within 0.25 stop median.
        self.assertLessEqual(self.r["capture_rerender_median_stop"], 0.25)

    def test_record_size(self):
        self.assertEqual(self.r["bytes_per_receiver"], bytes_per_receiver(34))


if __name__ == "__main__":
    unittest.main()

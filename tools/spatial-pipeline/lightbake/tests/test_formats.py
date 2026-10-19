import os
import tempfile
import unittest

import numpy as np

from lightbake import binding, ktx2


class Ktx2(unittest.TestCase):
    def test_round_trip_formats(self):
        rng = np.random.default_rng(0)
        cases = {"r8": rng.integers(0, 256, (3, 16, 8, 1), dtype=np.uint8),
                 "rgba8_srgb": rng.integers(0, 256, (16, 8, 4), dtype=np.uint8),
                 "r16f": rng.random((16, 8, 1)).astype(np.float16)}
        with tempfile.TemporaryDirectory() as d:
            for fmt, a in cases.items():
                p = os.path.join(d, fmt + ".ktx2")
                n = ktx2.write(p, a, fmt)
                self.assertEqual(n, os.path.getsize(p))
                b, info = ktx2.read(p)
                np.testing.assert_array_equal(b, a)
                self.assertEqual(info["levels"], 1)
                self.assertEqual(info["supercompression"], 0)
                raw = open(p, "rb").read()
                off = int.from_bytes(raw[80:88], "little")
                self.assertEqual(off % 4, 0)


class CanonicalJson(unittest.TestCase):
    def test_matches_js_canonical_form(self):
        v = {"b": [1, 2.5, True, None], "a": {"z": "é", "y": 1e-7, "x": 3.0}, "c": 1e21}
        self.assertEqual(binding.canonical_json(v),
                         '{"a":{"x":3,"y":1e-7,"z":"é"},"b":[1,2.5,true,null],"c":1e+21}')

    def test_refuses_output_inside_pack(self):
        with tempfile.TemporaryDirectory() as d:
            pack = os.path.join(d, "rev")
            os.makedirs(pack)
            with self.assertRaises(ValueError):
                binding.assert_outside(os.path.join(pack, "x.lighting"), pack)
            binding.assert_outside(os.path.join(d, "rev.lighting"), pack)
            self.assertTrue(str(binding.default_layer_dir(pack)).endswith("rev.lighting"))


if __name__ == "__main__":
    unittest.main()

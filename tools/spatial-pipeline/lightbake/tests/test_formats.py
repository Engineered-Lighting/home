import os
import tempfile
import unittest

import numpy as np

from lightbake import binding


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

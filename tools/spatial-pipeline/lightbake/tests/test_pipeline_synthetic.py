"""End-to-end public test of steps 90 and 91 on a procedural pack (no Den data).

Checks the L3 gates that do not need the real scan: pack bytes unchanged,
40 B/receiver, lightmaps <= 64 MB, provenance "modeled", SHA-256 load rule,
and capture-state self-consistency <= 0.25 stop median.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import warp  # noqa: F401
    HAVE_WARP = True
except ImportError:  # pragma: no cover
    HAVE_WARP = False


@unittest.skipUnless(HAVE_WARP, "warp-lang not installed")
class PipelineSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lightbake import bake, binding, cli, delight, synthetic_pack

        cls.tmp = Path(tempfile.mkdtemp())
        site, cls.pack, eq = synthetic_pack.make(cls.tmp, spp=128)
        cls.before = binding.snapshot(cls.pack)
        common = ["--site-dir", str(site), "--equipment", str(eq)]
        delight.run(cli.parse(90, common + ["--capture-spp", "128",
                                           "--wall-bearings", "wall-d=0,wall-a=270,wall-b=90,wall-c=180",
                                           "--sun", "probe=0/30"]))
        cls.m = bake.run(cli.parse(91, common + ["--spp", "32", "--mesh-spp", "32"]))
        cls.layer = binding.default_layer_dir(cls.pack)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pack_bytes_unchanged(self):
        from lightbake import binding
        self.assertEqual(binding.snapshot(self.pack), self.before)
        self.assertTrue(self.m["pack_integrity"]["identical"])
        self.assertTrue(self.m["pack_integrity"]["step90_identical"])

    def test_layer_lives_beside_the_pack(self):
        self.assertEqual(self.layer.parent, self.pack.parent)
        self.assertFalse(any(p.name.startswith("splat-") for p in self.pack.iterdir()))
        self.assertFalse((self.layer / "_work").exists())

    def test_budget_and_provenance(self):
        b = self.m["budget"]
        self.assertTrue(b["ok"])
        self.assertTrue(all(v == 40 for v in b["receiver_bytes"].values()))
        self.assertLessEqual(b["mesh_bytes"], 64 * 1024 * 1024)
        self.assertEqual(b["mesh_bytes_per_vertex"], 43)
        self.assertEqual(self.m["provenance"]["status"], "modeled")
        self.assertFalse(self.m["provenance"]["measured"])
        self.assertEqual(len(self.m["columns"]), 29)
        self.assertEqual([c["kind"] for c in self.m["columns"]].count("downward"), 4)
        self.assertEqual([c["kind"] for c in self.m["columns"]].count("radial"), 24)

    def test_capture_self_consistency(self):
        for name in ("splat", "points", "mesh"):
            self.assertLessEqual(self.m["metrics"][name]["capture_rerender_median_stop"], 0.25, name)

    def test_recovered_albedo(self):
        from lightbake import encode, synthetic_pack
        r = self.m["step90"]["results"]
        truth = float(encode.lum(synthetic_pack.ALBEDO))
        for name in ("splat", "points", "mesh"):
            self.assertAlmostEqual(r[name]["albedo_luminance_median"] / truth, 1.0, delta=0.12, msg=name)

    def test_window_detected_from_exterior_splats(self):
        ap = self.m["step90"]["apertures"]
        self.assertEqual(len(ap), 1)
        self.assertEqual(ap[0]["wall_id"], "wall-d")
        self.assertAlmostEqual(ap[0]["area_m2"], 1.0, delta=0.35)

    def test_capture_state_sensitivity(self):
        s = self.m["step90"]["sensitivity"]
        self.assertEqual(set(s["bearing_consistency_deg"].values()), {0.0})
        sun = next(v for k, v in s.items() if k.startswith("sun_probe"))
        self.assertGreater(sun["receivers_in_sun_fraction"], 0.01)
        lamp = next(v for k, v in s.items() if k.startswith("ceiling_lamp_on"))
        self.assertGreater(lamp["median_abs_albedo_change_stop"], 0.1)
        q = self.m["provenance"]["capture_state"]
        self.assertEqual(q["Q1_room_lamps"]["assumed"], "off")

    def test_load_rule_refuses_mismatch(self):
        from lightbake import binding
        binding.verify_layer(self.layer, self.pack)
        victim = self.layer / "splat-flags.bin"
        raw = victim.read_bytes()
        try:
            victim.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            with self.assertRaisesRegex(ValueError, "file_mismatch"):
                binding.verify_layer(self.layer, self.pack)
        finally:
            victim.write_bytes(raw)
        m = json.loads((self.layer / "lighting-manifest.json").read_text())
        m["binding"]["registration_version"] += 1
        alt = self.tmp / "alt.lighting"
        shutil.copytree(self.layer, alt)
        (alt / "lighting-manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "binding_mismatch"):
            binding.verify_layer(alt, self.pack)

    def test_files_decode(self):
        nv = self.m["mesh"]["vertices"]
        self.assertEqual(np.fromfile(self.layer / "mesh-basis-u8.bin", np.uint8).size, nv * 29)
        self.assertEqual(np.fromfile(self.layer / "mesh-capture-irradiance.bin", "<f2").size, nv * 3)
        n = self.m["receivers"]["splat"]["count"]
        u8 = np.fromfile(self.layer / "splat-basis-u8.bin", np.uint8)
        self.assertEqual(u8.size, n * 29)


if __name__ == "__main__":
    unittest.main()

import unittest

import numpy as np

from lightbake import encode as E
from lightbake.estimator import FLAGS, recover_albedo


class Encoding(unittest.TestCase):
    def test_record_is_40_bytes_for_29_columns(self):
        self.assertEqual(E.bytes_per_receiver(29), 40)
        rng = np.random.default_rng(1)
        n = 500
        rec = E.encode_receivers(rng.random((n, 3)) * 0.9, rng.normal(size=(n, 3)), rng.random((n, 29)),
                                 rng.random((n, 29, 3)), np.zeros(n, np.uint8), np.ones(29))
        self.assertEqual(sum(v.nbytes for v in rec.values()), 40 * n)

    def test_oct_normals_round_trip(self):
        v = np.random.default_rng(2).normal(size=(4000, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        d = E.decode_normal_oct(E.encode_normal_oct(v))
        ang = np.degrees(np.arccos(np.clip((d * v).sum(1), -1, 1)))
        self.assertLess(ang.max(), 1.6)

    def test_sqrt_basis_never_overshoots_and_is_accurate(self):
        rng = np.random.default_rng(3)
        # Rows span 4 decades around the column scale (fp16 normal range).
        B = rng.random((2000, 29)) ** 3 * rng.choice([1e-2, 1, 30], (2000, 1))
        cs = E.column_scales(B)
        u8, s = E.encode_basis(B, cs)
        D = E.decode_basis(u8, s, cs)
        row = B.max(1)
        # Codes never exceed the stored per-receiver scale; rounding is <= half a code plus the fp16 round-up.
        self.assertTrue(np.all((D / cs).max(1) <= s.astype(np.float64) * (1 + 1e-12)))
        self.assertTrue(np.all(D.max(1) <= row * (1 + 1 / 255 + 2 ** -10) + 1e-12))
        big = B > 0.05 * row[:, None]
        rel = np.abs(D[big] - B[big]) / B[big]
        self.assertLess(np.median(rel), 0.01)
        self.assertLess(np.percentile(rel, 99), 0.06)

    def test_tint_is_luminance_neutral(self):
        t = E.decode_tint(E.encode_tint(np.array([[1.3, 0.95, 0.6], [0, 0, 0], [0.2, 0.2, 0.2]])))
        np.testing.assert_allclose(E.lum(t), 1.0, atol=1e-9)

    def test_albedo_round_trip(self):
        rho = np.linspace(0, 0.95, 300)[:, None].repeat(3, 1)
        d = E.decode_albedo(E.encode_albedo(rho))
        # Half an 8-bit sRGB step: absolute 0.005 near white, much finer in darks.
        self.assertLess(np.abs(d - rho).max(), 0.005)
        self.assertLess(np.abs(d - rho)[rho[:, 0] < 0.1].max(), 0.001)


class Estimator(unittest.TestCase):
    def test_clamp_and_flags(self):
        E_cap = np.array([[1.0, 1, 1], [1, 1, 1], [1e-6, 1e-6, 1e-6]] * 10)
        R = np.array([[0.1, 0.1, 0.1], [0.5, 0.5, 0.5], [0.0, 0, 0]] * 10)
        rho, flags, st = recover_albedo(R, E_cap, rho_max=0.95)
        np.testing.assert_allclose(rho[0], np.pi * 0.1)
        self.assertTrue(np.all(rho[1] == 0.95))
        self.assertTrue(flags[1] & FLAGS["unexplained_light"])
        self.assertTrue(flags[2] & FLAGS["low_irradiance"])
        self.assertAlmostEqual(st["clamp_hit_fraction"], 1 / 3)


if __name__ == "__main__":
    unittest.main()

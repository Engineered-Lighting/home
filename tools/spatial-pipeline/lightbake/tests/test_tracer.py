"""Analytic checks of the Warp tracer on a synthetic box room (skipped if warp is absent)."""
import unittest

import numpy as np

try:
    import warp  # noqa: F401
    from lightbake import tracer
    HAVE_WARP = True
except ImportError:  # pragma: no cover
    HAVE_WARP = False


def box_room(X=4.0, Y=3.0, Z=2.5, n=8):
    """Closed box, faces wound to point into the room, gridded n x n per wall, planar UVs per wall chart."""
    pos, faces, uv = [], [], []
    walls = [  # origin, u-axis, v-axis (faces wound v x u: into the room)
        ([0, 0, 0], [0, Y, 0], [X, 0, 0]),       # floor, normal +z
        ([0, 0, Z], [X, 0, 0], [0, Y, 0]),       # ceiling, normal -z
        ([0, 0, 0], [X, 0, 0], [0, 0, Z]),       # y=0 wall, normal +y
        ([0, Y, 0], [0, 0, Z], [X, 0, 0]),       # y=Y wall, normal -y
        ([0, 0, 0], [0, 0, Z], [0, Y, 0]),       # x=0 wall, normal +x
        ([X, 0, 0], [0, Y, 0], [0, 0, Z]),       # x=X wall, normal -x
    ]
    for w, (o, u, v) in enumerate(walls):
        o, u, v = map(np.array, (o, u, v))
        base = len(pos)
        for j in range(n + 1):
            for i in range(n + 1):
                pos.append(o + u * i / n + v * j / n)
                uv.append([(w % 3 + i / n * 0.98) / 3, (w // 3 + j / n * 0.98) / 2])
        for j in range(n):
            for i in range(n):
                a = base + j * (n + 1) + i
                faces += [[a, a + n + 2, a + 1], [a, a + n + 1, a + n + 2]]
    return np.array(pos, np.float32), np.array(faces, np.int32), np.array(uv, np.float32)


@unittest.skipUnless(HAVE_WARP, "warp-lang not installed")
class TracerAnalytic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lightbake.scan import mesh_normals
        cls.device = tracer.init()
        pos, faces, uv = box_room()
        fn, vn, flipped = mesh_normals(pos, faces)
        cls.flipped = flipped
        cls.fn = fn
        # uniform captured colour: sRGB code 128 everywhere
        tex = np.full((64, 64, 3), 128, np.uint8)
        cls.L = tracer.srgb_lut()[128]
        cls.pos, cls.faces = pos, faces
        cls.scene = tracer.Scene(cls.device, pos, faces, uv, vn, fn, np.zeros(len(faces), np.int32), tex)

    def test_normals_point_into_room(self):
        self.assertFalse(self.flipped)
        c = self.pos[self.faces].mean(1)
        inward = (np.array([2.0, 1.5, 1.25]) - c)
        self.assertTrue(np.all((inward * self.fn).sum(1) > 0))

    def test_uniform_radiance_gather_is_pi_L(self):
        P = np.array([[2.0, 1.5, 0.0], [1.0, 1.0, 2.5], [0.0, 1.5, 1.0]], np.float32)
        N = np.array([[0, 0, 1], [0, 0, -1], [1, 0, 0]], np.float32)
        Eb, Ew, esc, back = self.scene.gather_capture(P, N, spp=4096, batch=512)
        np.testing.assert_allclose(Eb[:, 1], np.pi * self.L, rtol=0.03)
        self.assertTrue(np.all(esc == 0))
        self.assertTrue(np.all(back == 0))

    def test_point_emitter_inverse_square(self):
        from lightbake.scan import lobe_peak_per_lumen
        col = {"position": np.array([2.0, 1.5, 2.0]), "axis": np.array([0, 0, -1.0]), "hwhm_deg": 30.0,
               "max_theta_deg": 90.0, "peak_cd_per_lm": lobe_peak_per_lumen(30.0, 90.0)}
        self.scene.set_fixtures([col])
        P = np.array([[2.0, 1.5, 0.0]], np.float32)
        N = np.array([[0, 0, 1.0]], np.float32)
        self.scene.set_albedo_constant([0, 0, 0])  # no bounce
        B = self.scene.basis(P, N, spp=8, bounces=2, batch=8)
        h = 2.0 - self.scene.eps
        np.testing.assert_allclose(B[0, 0, 0], col["peak_cd_per_lm"] / h ** 2, rtol=1e-3)
        self.scene.set_fixtures([])

    def test_albedo_ratio_model_closed_form(self):
        # rho = pi * L / E_vertex: uniform L in a closed box with E = pi*L/(1-rho) at steady state;
        # here we only check the ratio and clamp: E_vertex = pi*L/0.5 -> rho 0.5, E tiny -> rho_max.
        nv = len(self.pos)
        self.scene.set_albedo_ratio(np.full((nv, 3), np.pi * self.L / 0.5, np.float32), 1e-6, 0.9)
        col = {"position": np.array([2.0, 1.5, 2.0]), "axis": np.array([0, 0, -1.0]), "hwhm_deg": 60.0,
               "max_theta_deg": 180.0, "peak_cd_per_lm": 1.0}
        self.scene.set_fixtures([col])
        P = np.array([[2.0, 1.5, 0.0]], np.float32)
        N = np.array([[0, 0, 1.0]], np.float32)
        B1 = self.scene.basis(P, N, spp=2048, bounces=1, batch=256)[0, 0, 0]
        B0 = self.scene.basis(P, N, spp=8, bounces=0, batch=8)[0, 0, 0]
        self.scene.set_albedo_constant([0.5, 0.5, 0.5])
        Bc = self.scene.basis(P, N, spp=2048, bounces=1, batch=256)[0, 0, 0]
        self.assertGreater(B1, B0)
        np.testing.assert_allclose(B1 - B0, Bc - B0, rtol=0.08)
        self.scene.set_fixtures([])

    def test_closest_point_and_barycentric_convention(self):
        P = np.array([[1.3, 0.7, 0.05], [3.9, 2.2, 1.0]], np.float32)
        face, dist, nrm, uvo, cp = self.scene.closest(P)
        np.testing.assert_allclose(dist, [0.05, 0.1], atol=1e-4)
        np.testing.assert_allclose(cp, [[1.3, 0.7, 0.0], [4.0, 2.2, 1.0]], atol=1e-4)
        np.testing.assert_allclose(nrm[0], [0, 0, 1], atol=1e-4)
        np.testing.assert_allclose(nrm[1], [-1, 0, 0], atol=1e-4)
        # floor chart: u follows y, v follows x (pins Warp's barycentric order for UV lookups)
        np.testing.assert_allclose(uvo[0], [0.7 / 3.0 * 0.98 / 3, 1.3 / 4.0 * 0.98 / 2], atol=1e-5)


if __name__ == "__main__":
    unittest.main()

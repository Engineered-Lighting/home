"""NVIDIA Warp ray tracing over the scan mesh BVH (RTX 4090 on the workstation;
Warp's CPU backend runs the same kernels for the small public tests).

Two estimators share one scene:

- capture gather: E_cap at a receiver = gather of the *captured* radiance of
  every scan surface the hemisphere sees (spike 1's measured-radiance gather,
  all bounce orders at once), plus next-event estimation of the window area
  emitters. Window glass faces contribute nothing to the surface gather; they
  are emitters, represented by the window quads.
- unit basis: per receiver and emitter column, irradiance from unit-flux
  fixtures (28 point emitters with assumed Gaussian lobes) and the window,
  with direct light, shadows and multi-bounce GI through the de-lit albedo.

Windows TDR: launches are chunked so no kernel runs for long.
"""
from __future__ import annotations

import math
import os

import numpy as np
import warp as wp

wp.config.quiet = True
LN2 = 0.6931471805599453


def init(device=None):
    wp.init()
    device = device or os.environ.get("LIGHTBAKE_DEVICE")
    if device is None:
        device = "cuda:0" if wp.get_cuda_device_count() > 0 else "cpu"
    return device


def srgb_lut():
    v = np.arange(256) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


# ------------------------------------------------------------------ device helpers
@wp.func
def cosine_dir(n: wp.vec3, r1: float, r2: float):
    a = wp.vec3(1.0, 0.0, 0.0)
    if wp.abs(n[0]) > 0.9:
        a = wp.vec3(0.0, 1.0, 0.0)
    t = wp.normalize(wp.cross(n, a))
    b = wp.cross(n, t)
    phi = 6.283185307179586 * r1
    s = wp.sqrt(r2)
    return wp.normalize(t * (s * wp.cos(phi)) + b * (s * wp.sin(phi)) + n * wp.sqrt(wp.max(1.0 - r2, 0.0)))


@wp.func
def bary_uv(tri: wp.array(dtype=wp.int32), uv: wp.array(dtype=wp.vec2), f: int, u: float, v: float):
    # Warp's barycentrics: p = u*v0 + v*v1 + (1-u-v)*v2 (checked in tests against mesh_eval_position)
    return uv[tri[f * 3]] * u + uv[tri[f * 3 + 1]] * v + uv[tri[f * 3 + 2]] * (1.0 - u - v)


@wp.func
def tex_fetch(tex: wp.array(dtype=wp.uint8), w: int, h: int, lut: wp.array(dtype=float), t: wp.vec2):
    x = wp.clamp(int(t[0] * float(w)), 0, w - 1)
    y = wp.clamp(int(t[1] * float(h)), 0, h - 1)
    i = (y * w + x) * 3
    return wp.vec3(lut[int(tex[i])], lut[int(tex[i + 1])], lut[int(tex[i + 2])])


@wp.func
def occluded(room: wp.uint64, o: wp.vec3, d: wp.vec3, tmax: float):
    q = wp.mesh_query_ray(room, o, d, tmax)
    return q.result


@wp.func
def fixture_E(room: wp.uint64, k: int, x: wp.vec3, n: wp.vec3, epos: wp.array(dtype=wp.vec3),
              eaxis: wp.array(dtype=wp.vec3), ehw: wp.array(dtype=float), emaxcos: wp.array(dtype=float),
              epeak: wp.array(dtype=float)):
    v = epos[k] - x
    d2 = wp.dot(v, v)
    dist = wp.sqrt(d2)
    l = v / dist
    cr = wp.dot(n, l)
    if cr <= 0.0:
        return 0.0
    ca = wp.clamp(wp.dot(-l, eaxis[k]), -1.0, 1.0)
    if ca < emaxcos[k]:
        return 0.0
    ang = wp.acos(ca) / ehw[k]
    I = epeak[k] * wp.exp(-LN2 * ang * ang)
    if occluded(room, x, l, dist - 0.02):
        return 0.0
    return I * cr / d2


@wp.func
def window_E(room: wp.uint64, x: wp.vec3, n: wp.vec3, r0: float, r1: float, r2: float,
             w0: wp.array(dtype=wp.vec3), w1: wp.array(dtype=wp.vec3), w2: wp.array(dtype=wp.vec3),
             wn: wp.array(dtype=wp.vec3), wL: wp.array(dtype=wp.vec3), wcdf: wp.array(dtype=float),
             wpdf: wp.array(dtype=float)):
    nt = wcdf.shape[0]
    lo = int(0)
    hi = nt - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if wcdf[mid] < r0:
            lo = mid + 1
        else:
            hi = mid
    t = lo
    su = wp.sqrt(r1)
    y = w0[t] * (1.0 - su) + w1[t] * (su * (1.0 - r2)) + w2[t] * (su * r2)
    v = y - x
    d2 = wp.dot(v, v)
    dist = wp.sqrt(d2)
    l = v / dist
    cr = wp.dot(n, l)
    ce = -wp.dot(wn[t], l)
    if cr <= 0.0 or ce <= 0.0:
        return wp.vec3(0.0, 0.0, 0.0)
    if occluded(room, x, l, dist - 0.01):
        return wp.vec3(0.0, 0.0, 0.0)
    return wL[t] * (cr * ce / (d2 * wpdf[t]))


# ------------------------------------------------------------------ kernels
@wp.kernel
def k_closest(mesh: wp.uint64, P: wp.array(dtype=wp.vec3), maxd: float, tri: wp.array(dtype=wp.int32),
              vn: wp.array(dtype=wp.vec3), uv: wp.array(dtype=wp.vec2),
              face: wp.array(dtype=wp.int32), dist: wp.array(dtype=float), nrm: wp.array(dtype=wp.vec3),
              uvo: wp.array(dtype=wp.vec2)):
    i = wp.tid()
    q = wp.mesh_query_point_no_sign(mesh, P[i], maxd)
    if q.result:
        f = q.face
        cp = wp.mesh_eval_position(mesh, f, q.u, q.v)
        face[i] = f
        dist[i] = wp.length(cp - P[i])
        nn = vn[tri[f * 3]] * q.u + vn[tri[f * 3 + 1]] * q.v + vn[tri[f * 3 + 2]] * (1.0 - q.u - q.v)
        nrm[i] = wp.normalize(nn)
        uvo[i] = bary_uv(tri, uv, f, q.u, q.v)
    else:
        face[i] = -1
        dist[i] = 1.0e9


@wp.kernel
def k_gather(room: wp.uint64, win: wp.uint64, has_win: int, tri: wp.array(dtype=wp.int32),
             uv: wp.array(dtype=wp.vec2), fn: wp.array(dtype=wp.vec3), glass: wp.array(dtype=wp.int32),
             tex: wp.array(dtype=wp.uint8), tw: int, th: int, lut: wp.array(dtype=float),
             w0: wp.array(dtype=wp.vec3), w1: wp.array(dtype=wp.vec3), w2: wp.array(dtype=wp.vec3),
             wn: wp.array(dtype=wp.vec3), wL: wp.array(dtype=wp.vec3), wcdf: wp.array(dtype=float),
             wpdf: wp.array(dtype=float),
             P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), spp: int, seed: int, eps: float,
             Eb: wp.array(dtype=wp.vec3), Ew: wp.array(dtype=wp.vec3), esc: wp.array(dtype=float)):
    i = wp.tid()
    n = N[i]
    o = P[i] + n * eps
    st = wp.rand_init(seed, i)
    sb = wp.vec3(0.0, 0.0, 0.0)
    sw = wp.vec3(0.0, 0.0, 0.0)
    se = float(0.0)
    for s in range(spp):
        d = cosine_dir(n, wp.randf(st), wp.randf(st))
        q = wp.mesh_query_ray(room, o, d, 1.0e3)
        tr = float(1.0e9)
        if q.result:
            tr = q.t
        blocked_by_window = False
        if has_win == 1:
            qw = wp.mesh_query_ray(win, o, d, tr)
            if qw.result:
                blocked_by_window = True
        if not blocked_by_window:
            if q.result:
                f = q.face
                if wp.dot(d, fn[f]) < 0.0 and glass[f] == 0:
                    sb = sb + tex_fetch(tex, tw, th, lut, bary_uv(tri, uv, f, q.u, q.v))
            else:
                se = se + 1.0
        if has_win == 1:
            sw = sw + window_E(room, o, n, wp.randf(st), wp.randf(st), wp.randf(st), w0, w1, w2, wn, wL, wcdf, wpdf)
    Eb[i] = Eb[i] + sb * (3.141592653589793 / float(spp))
    Ew[i] = Ew[i] + sw / float(spp)
    esc[i] = esc[i] + se / float(spp)


@wp.kernel
def k_direct_fixtures(room: wp.uint64, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), eps: float,
                      nk: int, epos: wp.array(dtype=wp.vec3), eaxis: wp.array(dtype=wp.vec3),
                      ehw: wp.array(dtype=float), emaxcos: wp.array(dtype=float), epeak: wp.array(dtype=float),
                      acc: wp.array2d(dtype=float)):
    i = wp.tid()
    n = N[i]
    x = P[i] + n * eps
    for k in range(nk):
        e = fixture_E(room, k, x, n, epos, eaxis, ehw, emaxcos, epeak)
        acc[i, k * 3] = acc[i, k * 3] + e
        acc[i, k * 3 + 1] = acc[i, k * 3 + 1] + e
        acc[i, k * 3 + 2] = acc[i, k * 3 + 2] + e


@wp.kernel
def k_paths(room: wp.uint64, win: wp.uint64, has_win: int, tri: wp.array(dtype=wp.int32),
            uv: wp.array(dtype=wp.vec2), fn: wp.array(dtype=wp.vec3), glass: wp.array(dtype=wp.int32),
            alb: wp.array(dtype=wp.uint8), aw: int, ah: int, lut: wp.array(dtype=float),
            nk: int, epos: wp.array(dtype=wp.vec3), eaxis: wp.array(dtype=wp.vec3),
            ehw: wp.array(dtype=float), emaxcos: wp.array(dtype=float), epeak: wp.array(dtype=float),
            w0: wp.array(dtype=wp.vec3), w1: wp.array(dtype=wp.vec3), w2: wp.array(dtype=wp.vec3),
            wn: wp.array(dtype=wp.vec3), wL: wp.array(dtype=wp.vec3), wcdf: wp.array(dtype=float),
            wpdf: wp.array(dtype=float),
            P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), spp: int, bounces: int, seed: int,
            eps: float, inv_total_spp: float, acc: wp.array2d(dtype=float)):
    """Window direct at the receiver (MC) + all indirect orders, NEE to every column at each vertex."""
    i = wp.tid()
    st = wp.rand_init(seed, i)
    n0 = N[i]
    x0 = P[i] + n0 * eps
    for s in range(spp):
        if has_win == 1:
            ew = window_E(room, x0, n0, wp.randf(st), wp.randf(st), wp.randf(st), w0, w1, w2, wn, wL, wcdf, wpdf)
            acc[i, nk * 3] = acc[i, nk * 3] + ew[0] * inv_total_spp
            acc[i, nk * 3 + 1] = acc[i, nk * 3 + 1] + ew[1] * inv_total_spp
            acc[i, nk * 3 + 2] = acc[i, nk * 3 + 2] + ew[2] * inv_total_spp
        x = x0
        n = n0
        thr = wp.vec3(inv_total_spp, inv_total_spp, inv_total_spp)
        for b in range(bounces):
            d = cosine_dir(n, wp.randf(st), wp.randf(st))
            q = wp.mesh_query_ray(room, x, d, 1.0e3)
            if not q.result:
                break
            if has_win == 1:
                qw = wp.mesh_query_ray(win, x, d, q.t)
                if qw.result:
                    break
            f = q.face
            nh = fn[f]
            if wp.dot(d, nh) >= 0.0 or glass[f] != 0:
                break
            rho = tex_fetch(alb, aw, ah, lut, bary_uv(tri, uv, f, q.u, q.v))
            thr = wp.cw_mul(thr, rho)
            x = x + d * q.t + nh * eps
            n = nh
            for k in range(nk):
                e = fixture_E(room, k, x, n, epos, eaxis, ehw, emaxcos, epeak)
                if e > 0.0:
                    acc[i, k * 3] = acc[i, k * 3] + thr[0] * e
                    acc[i, k * 3 + 1] = acc[i, k * 3 + 1] + thr[1] * e
                    acc[i, k * 3 + 2] = acc[i, k * 3 + 2] + thr[2] * e
            if has_win == 1:
                ew = window_E(room, x, n, wp.randf(st), wp.randf(st), wp.randf(st), w0, w1, w2, wn, wL, wcdf, wpdf)
                acc[i, nk * 3] = acc[i, nk * 3] + thr[0] * ew[0]
                acc[i, nk * 3 + 1] = acc[i, nk * 3 + 1] + thr[1] * ew[1]
                acc[i, nk * 3 + 2] = acc[i, nk * 3 + 2] + thr[2] * ew[2]


@wp.kernel
def k_point_lamps(room: wp.uint64, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), eps: float,
                  lpos: wp.array(dtype=wp.vec3), lI: wp.array(dtype=wp.vec3), out: wp.array(dtype=wp.vec3)):
    """Direct irradiance from isotropic point lamps (capture-state room lamps, Q1)."""
    i = wp.tid()
    n = N[i]
    x = P[i] + n * eps
    acc = wp.vec3(0.0, 0.0, 0.0)
    for k in range(lpos.shape[0]):
        v = lpos[k] - x
        d2 = wp.dot(v, v)
        dist = wp.sqrt(d2)
        l = v / dist
        c = wp.dot(n, l)
        if c > 0.0:
            if not occluded(room, x, l, dist - 0.02):
                acc = acc + lI[k] * (c / d2)
    out[i] = acc


@wp.kernel
def k_sun(room: wp.uint64, win: wp.uint64, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), eps: float,
          to_sun: wp.vec3, out: wp.array(dtype=float)):
    """cos-weighted visibility of a directional sun that must pass through a window quad."""
    i = wp.tid()
    n = N[i]
    x = P[i] + n * eps
    c = wp.dot(n, to_sun)
    out[i] = 0.0
    if c > 0.0:
        qw = wp.mesh_query_ray(win, x, to_sun, 50.0)
        if qw.result:
            if not occluded(room, x, to_sun, qw.t):
                out[i] = c


@wp.kernel
def k_raster(tri: wp.array(dtype=wp.int32), uv: wp.array(dtype=wp.vec2), pos: wp.array(dtype=wp.vec3),
             vn: wp.array(dtype=wp.vec3), W: int, H: int,
             tface: wp.array(dtype=wp.int32), tpos: wp.array(dtype=wp.vec3), tnrm: wp.array(dtype=wp.vec3)):
    """Rasterise each face into the UV atlas at W x H (texel centres; glTF v points down the image)."""
    f = wp.tid()
    a = uv[tri[f * 3]]
    b = uv[tri[f * 3 + 1]]
    c = uv[tri[f * 3 + 2]]
    ax = a[0] * float(W)
    ay = a[1] * float(H)
    bx = b[0] * float(W)
    by = b[1] * float(H)
    cx = c[0] * float(W)
    cy = c[1] * float(H)
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if wp.abs(den) < 1.0e-12:
        return
    x0 = wp.max(int(wp.floor(wp.min(ax, wp.min(bx, cx)))), 0)
    x1 = wp.min(int(wp.ceil(wp.max(ax, wp.max(bx, cx)))), W - 1)
    y0 = wp.max(int(wp.floor(wp.min(ay, wp.min(by, cy)))), 0)
    y1 = wp.min(int(wp.ceil(wp.max(ay, wp.max(by, cy)))), H - 1)
    if (x1 - x0) * (y1 - y0) > 65536:
        return
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            px = float(x) + 0.5
            py = float(y) + 0.5
            l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
            l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
            l2 = 1.0 - l0 - l1
            if l0 >= -1.0e-4 and l1 >= -1.0e-4 and l2 >= -1.0e-4:
                t = y * W + x
                tface[t] = f
                tpos[t] = pos[tri[f * 3]] * l0 + pos[tri[f * 3 + 1]] * l1 + pos[tri[f * 3 + 2]] * l2
                tnrm[t] = wp.normalize(vn[tri[f * 3]] * l0 + vn[tri[f * 3 + 1]] * l1 + vn[tri[f * 3 + 2]] * l2)


# ------------------------------------------------------------------ host-side scene
class Scene:
    """Room mesh + window emitter quads + fixture columns, resident on one device."""

    def __init__(self, device, positions, faces, uv, vertex_normals, face_normals, glass_faces,
                 capture_texture, window=None, fixtures=(), eps=0.01):
        self.device = device
        self.eps = eps
        self.nf = len(faces)
        self.mesh = wp.Mesh(points=wp.array(positions.astype(np.float32), dtype=wp.vec3, device=device),
                            indices=wp.array(faces.astype(np.int32).ravel(), dtype=wp.int32, device=device))
        self.tri = wp.array(faces.astype(np.int32).ravel(), dtype=wp.int32, device=device)
        self.uv = wp.array(uv.astype(np.float32), dtype=wp.vec2, device=device)
        self.vn = wp.array(vertex_normals.astype(np.float32), dtype=wp.vec3, device=device)
        self.fn = wp.array(face_normals.astype(np.float32), dtype=wp.vec3, device=device)
        self.glass = wp.array(np.asarray(glass_faces, np.int32), dtype=wp.int32, device=device)
        self.lut = wp.array(srgb_lut(), dtype=float, device=device)
        self.set_texture("capture", capture_texture)
        self.set_window(window)
        self.set_fixtures(fixtures)

    def set_texture(self, name, img):
        img = np.ascontiguousarray(img, np.uint8)
        setattr(self, name, (wp.array(img.ravel(), dtype=wp.uint8, device=self.device), img.shape[1], img.shape[0]))

    def set_window(self, window):
        d = self.device
        if window is None or len(window["v0"]) == 0:
            self.has_win = 0
            z3 = np.zeros((1, 3), np.float32)
            self.win = wp.Mesh(points=wp.array(np.array([[0, 0, -1e4], [1, 0, -1e4], [0, 1, -1e4]], np.float32),
                                               dtype=wp.vec3, device=d),
                               indices=wp.array(np.array([0, 1, 2], np.int32), dtype=wp.int32, device=d))
            self.w = [wp.array(z3, dtype=wp.vec3, device=d) for _ in range(5)] + \
                     [wp.array(np.ones(1, np.float32), dtype=float, device=d) for _ in range(2)]
            return
        self.has_win = 1
        v0, v1, v2 = (window[k].astype(np.float32) for k in ("v0", "v1", "v2"))
        pts = np.concatenate([v0, v1, v2])
        m = len(v0)
        idx = np.stack([np.arange(m), np.arange(m) + m, np.arange(m) + 2 * m], 1).astype(np.int32).ravel()
        self.win = wp.Mesh(points=wp.array(pts, dtype=wp.vec3, device=d), indices=wp.array(idx, dtype=wp.int32, device=d))
        area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
        L = window["L"].astype(np.float64)
        wgt = area * np.maximum(L @ [0.2126, 0.7152, 0.0722], 1e-9)
        p_tri = wgt / wgt.sum()
        cdf = np.cumsum(p_tri)
        cdf[-1] = 1.0
        self.w = [wp.array(a, dtype=wp.vec3, device=d) for a in (v0, v1, v2, window["n"].astype(np.float32),
                                                                 L.astype(np.float32))]
        self.w += [wp.array(cdf.astype(np.float32), dtype=float, device=d),
                   wp.array((p_tri / area).astype(np.float32), dtype=float, device=d)]

    def set_fixtures(self, cols):
        d = self.device
        cols = list(cols)
        self.nk = len(cols)
        if not cols:
            cols = [{"position": np.zeros(3), "axis": np.array([0, 0, -1.0]), "hwhm_deg": 1.0, "max_theta_deg": 0.0,
                     "peak_cd_per_lm": 0.0}]
        self.e = [wp.array(np.array([c["position"] for c in cols], np.float32), dtype=wp.vec3, device=d),
                  wp.array(np.array([c["axis"] for c in cols], np.float32), dtype=wp.vec3, device=d),
                  wp.array(np.radians([c["hwhm_deg"] for c in cols]).astype(np.float32), dtype=float, device=d),
                  wp.array(np.cos(np.radians([c["max_theta_deg"] for c in cols])).astype(np.float32), dtype=float, device=d),
                  wp.array(np.array([c["peak_cd_per_lm"] for c in cols], np.float32), dtype=float, device=d)]

    # -------------------------------------------------------------- queries
    def _v3(self, a):
        return wp.array(np.ascontiguousarray(a, np.float32), dtype=wp.vec3, device=self.device)

    def closest(self, points, max_dist=1.0, chunk=1 << 20):
        n = len(points)
        face = np.empty(n, np.int32)
        dist = np.empty(n, np.float32)
        nrm = np.zeros((n, 3), np.float32)
        uvo = np.zeros((n, 2), np.float32)
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            m = b - a
            f_ = wp.zeros(m, dtype=wp.int32, device=self.device)
            d_ = wp.zeros(m, dtype=float, device=self.device)
            n_ = wp.zeros(m, dtype=wp.vec3, device=self.device)
            u_ = wp.zeros(m, dtype=wp.vec2, device=self.device)
            wp.launch(k_closest, m, [self.mesh.id, self._v3(points[a:b]), max_dist, self.tri, self.vn, self.uv,
                                     f_, d_, n_, u_], device=self.device)
            face[a:b], dist[a:b], nrm[a:b], uvo[a:b] = f_.numpy(), d_.numpy(), n_.numpy(), u_.numpy()
        return face, dist, nrm, uvo

    def gather_capture(self, P, N, spp=256, batch=32, chunk=1 << 18, seed=1):
        """Capture-state irradiance: (E_bounce (n,3), E_window (n,3), escape fraction (n,))."""
        n = len(P)
        Eb = np.zeros((n, 3))
        Ew = np.zeros((n, 3))
        es = np.zeros(n)
        tex, tw, th = self.capture
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            m = b - a
            p_, n_ = self._v3(P[a:b]), self._v3(N[a:b])
            eb = wp.zeros(m, dtype=wp.vec3, device=self.device)
            ew = wp.zeros(m, dtype=wp.vec3, device=self.device)
            ee = wp.zeros(m, dtype=float, device=self.device)
            passes = max(1, spp // batch)
            for s in range(passes):
                wp.launch(k_gather, m, [self.mesh.id, self.win.id, self.has_win, self.tri, self.uv, self.fn, self.glass,
                                        tex, tw, th, self.lut, *self.w, p_, n_, batch, seed * 7919 + s * 104729 + a,
                                        self.eps, eb, ew, ee], device=self.device)
            Eb[a:b] = eb.numpy() / passes
            Ew[a:b] = ew.numpy() / passes
            es[a:b] = ee.numpy() / passes
        return Eb, Ew, es

    def basis(self, P, N, albedo_texture, spp=64, bounces=5, batch=4, chunk=1 << 17, seed=2):
        """Unit basis per receiver: (n, nk+1, 3) RGB irradiance; last column = window."""
        n = len(P)
        K = self.nk + 1
        out = np.zeros((n, K, 3))
        alb = wp.array(np.ascontiguousarray(albedo_texture, np.uint8).ravel(), dtype=wp.uint8, device=self.device)
        ah, aw = albedo_texture.shape[:2]
        passes = max(1, spp // batch)
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            m = b - a
            p_, n_ = self._v3(P[a:b]), self._v3(N[a:b])
            acc = wp.zeros((m, K * 3), dtype=float, device=self.device)
            if self.nk:
                wp.launch(k_direct_fixtures, m, [self.mesh.id, p_, n_, self.eps, self.nk, *self.e, acc], device=self.device)
            for s in range(passes):
                wp.launch(k_paths, m, [self.mesh.id, self.win.id, self.has_win, self.tri, self.uv, self.fn, self.glass,
                                       alb, aw, ah, self.lut, self.nk, *self.e, *self.w, p_, n_, batch, bounces,
                                       seed * 7919 + s * 104729 + a, self.eps, 1.0 / (passes * batch), acc],
                          device=self.device)
            out[a:b] = acc.numpy().reshape(m, K, 3)
        return out

    def point_lamps(self, P, N, lamps):
        if not lamps:
            return np.zeros((len(P), 3))
        out = wp.zeros(len(P), dtype=wp.vec3, device=self.device)
        wp.launch(k_point_lamps, len(P), [self.mesh.id, self._v3(P), self._v3(N), self.eps,
                                          self._v3([l["position"] for l in lamps]),
                                          self._v3([l["intensity_rgb"] for l in lamps]), out], device=self.device)
        return out.numpy().astype(np.float64)

    def sun_cos(self, P, N, to_sun):
        out = wp.zeros(len(P), dtype=float, device=self.device)
        if not self.has_win:
            return np.zeros(len(P))
        wp.launch(k_sun, len(P), [self.mesh.id, self.win.id, self._v3(P), self._v3(N), self.eps,
                                  wp.vec3(*map(float, to_sun)), out], device=self.device)
        return out.numpy().astype(np.float64)

    def raster(self, positions, size):
        W = H = int(size)
        tface = wp.full(W * H, -1, dtype=wp.int32, device=self.device)
        tpos = wp.zeros(W * H, dtype=wp.vec3, device=self.device)
        tnrm = wp.zeros(W * H, dtype=wp.vec3, device=self.device)
        wp.launch(k_raster, self.nf, [self.tri, self.uv, self._v3(positions), self.vn, W, H, tface, tpos, tnrm],
                  device=self.device)
        return tface.numpy().reshape(H, W), tpos.numpy().reshape(H, W, 3), tnrm.numpy().reshape(H, W, 3)

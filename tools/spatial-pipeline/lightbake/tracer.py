"""NVIDIA Warp ray tracing over the scan mesh BVH (RTX 4090 on the workstation;
Warp's CPU backend runs the same kernels for the small public tests).

Two estimators share one scene:

- capture gather: E_cap at a receiver = gather of the *captured* radiance of
  every scan surface the hemisphere sees (spike 1's measured-radiance gather,
  all bounce orders at once), plus next-event estimation of the window area
  emitters. Window glass faces contribute nothing to the surface gather; they
  are emitters, represented by the window quads.
- unit basis: per receiver and emitter column, irradiance from unit-flux
  fixtures (point emitters with assumed Gaussian lobes) and the window, with
  direct light, shadows and multi-bounce GI through the de-lit albedo.

Surfaces: faces [0, n_main) are the scan mesh, textured by the capture atlas;
faces [n_main, nf) are auxiliary geometry the scan lacks (a ceiling cap built
from ceiling splats) with their own radiance and albedo textures. Scan albedo
at a hit is rho = clamp(pi * atlas / max(E_cap, floor)), with E_cap
interpolated from per-vertex capture irradiance; that is the same formula the
runtime shader evaluates.

Windows TDR: launches are chunked so no kernel runs for long.
"""
from __future__ import annotations

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


@wp.struct
class Surf:
    mesh: wp.uint64
    nmain: int
    tri: wp.array(dtype=wp.int32)
    uv: wp.array(dtype=wp.vec2)
    fn: wp.array(dtype=wp.vec3)
    glass: wp.array(dtype=wp.int32)
    lut: wp.array(dtype=float)
    tex: wp.array(dtype=wp.uint8)
    tw: int
    th: int
    aux_rad: wp.array(dtype=wp.uint8)
    aux_alb: wp.array(dtype=wp.uint8)
    aw: int
    ah: int
    aux_scale: float     # aux radiance = lut(u8) * aux_scale (HDR modeled ceiling)
    vE: wp.array(dtype=wp.vec3)
    floor: float
    rmax: float
    mode: int            # 0: constant albedo (tests), 1: atlas / per-vertex E_cap
    alb_const: wp.vec3


@wp.struct
class Win:
    mesh: wp.uint64
    has: int
    v0: wp.array(dtype=wp.vec3)
    v1: wp.array(dtype=wp.vec3)
    v2: wp.array(dtype=wp.vec3)
    n: wp.array(dtype=wp.vec3)
    L: wp.array(dtype=wp.vec3)
    cdf: wp.array(dtype=float)
    pdf: wp.array(dtype=float)


@wp.struct
class Fix:
    n: int
    pos: wp.array(dtype=wp.vec3)
    axis: wp.array(dtype=wp.vec3)
    hw: wp.array(dtype=float)
    maxcos: wp.array(dtype=float)
    peak: wp.array(dtype=float)


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
def bary_uv(s: Surf, f: int, u: float, v: float):
    # Warp's barycentrics: p = u*v0 + v*v1 + (1-u-v)*v2 (checked in tests)
    return s.uv[s.tri[f * 3]] * u + s.uv[s.tri[f * 3 + 1]] * v + s.uv[s.tri[f * 3 + 2]] * (1.0 - u - v)


@wp.func
def fetch(tex: wp.array(dtype=wp.uint8), w: int, h: int, lut: wp.array(dtype=float), t: wp.vec2):
    x = wp.clamp(int(t[0] * float(w)), 0, w - 1)
    y = wp.clamp(int(t[1] * float(h)), 0, h - 1)
    i = (y * w + x) * 3
    return wp.vec3(lut[int(tex[i])], lut[int(tex[i + 1])], lut[int(tex[i + 2])])


@wp.func
def radiance(s: Surf, f: int, u: float, v: float):
    t = bary_uv(s, f, u, v)
    if f < s.nmain:
        return fetch(s.tex, s.tw, s.th, s.lut, t)
    return fetch(s.aux_rad, s.aw, s.ah, s.lut, t) * s.aux_scale


@wp.func
def albedo(s: Surf, f: int, u: float, v: float):
    if s.mode == 0:
        return s.alb_const
    t = bary_uv(s, f, u, v)
    if f >= s.nmain:
        return fetch(s.aux_alb, s.aw, s.ah, s.lut, t)
    R = fetch(s.tex, s.tw, s.th, s.lut, t)
    E = s.vE[s.tri[f * 3]] * u + s.vE[s.tri[f * 3 + 1]] * v + s.vE[s.tri[f * 3 + 2]] * (1.0 - u - v)
    r = wp.vec3(0.0, 0.0, 0.0)
    for c in range(3):
        r[c] = wp.clamp(3.141592653589793 * R[c] / wp.max(E[c], s.floor), 0.0, s.rmax)
    return r


@wp.func
def occluded(mesh: wp.uint64, o: wp.vec3, d: wp.vec3, tmax: float):
    q = wp.mesh_query_ray(mesh, o, d, tmax)
    return q.result


@wp.func
def fixture_E(mesh: wp.uint64, fx: Fix, k: int, x: wp.vec3, n: wp.vec3):
    v = fx.pos[k] - x
    d2 = wp.dot(v, v)
    dist = wp.sqrt(d2)
    l = v / dist
    cr = wp.dot(n, l)
    if cr <= 0.0:
        return 0.0
    ca = wp.clamp(wp.dot(-l, fx.axis[k]), -1.0, 1.0)
    if ca < fx.maxcos[k]:
        return 0.0
    ang = wp.acos(ca) / fx.hw[k]
    I = fx.peak[k] * wp.exp(-LN2 * ang * ang)
    if occluded(mesh, x, l, dist - 0.02):
        return 0.0
    return I * cr / d2


@wp.func
def window_E(mesh: wp.uint64, w: Win, x: wp.vec3, n: wp.vec3, r0: float, r1: float, r2: float):
    nt = w.cdf.shape[0]
    lo = int(0)
    hi = nt - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if w.cdf[mid] < r0:
            lo = mid + 1
        else:
            hi = mid
    t = lo
    su = wp.sqrt(r1)
    y = w.v0[t] * (1.0 - su) + w.v1[t] * (su * (1.0 - r2)) + w.v2[t] * (su * r2)
    v = y - x
    d2 = wp.dot(v, v)
    dist = wp.sqrt(d2)
    l = v / dist
    cr = wp.dot(n, l)
    ce = -wp.dot(w.n[t], l)
    if cr <= 0.0 or ce <= 0.0:
        return wp.vec3(0.0, 0.0, 0.0)
    if occluded(mesh, x, l, dist - 0.01):
        return wp.vec3(0.0, 0.0, 0.0)
    return w.L[t] * (cr * ce / (d2 * w.pdf[t]))


# ------------------------------------------------------------------ kernels
@wp.kernel
def k_closest(s: Surf, vn: wp.array(dtype=wp.vec3), P: wp.array(dtype=wp.vec3), maxd: float,
              face: wp.array(dtype=wp.int32), dist: wp.array(dtype=float), nrm: wp.array(dtype=wp.vec3),
              uvo: wp.array(dtype=wp.vec2), cp: wp.array(dtype=wp.vec3)):
    i = wp.tid()
    q = wp.mesh_query_point_no_sign(s.mesh, P[i], maxd)
    if q.result:
        f = q.face
        p = wp.mesh_eval_position(s.mesh, f, q.u, q.v)
        face[i] = f
        dist[i] = wp.length(p - P[i])
        cp[i] = p
        nn = vn[s.tri[f * 3]] * q.u + vn[s.tri[f * 3 + 1]] * q.v + vn[s.tri[f * 3 + 2]] * (1.0 - q.u - q.v)
        nrm[i] = wp.normalize(nn)
        uvo[i] = bary_uv(s, f, q.u, q.v)
    else:
        face[i] = -1
        dist[i] = 1.0e9
        cp[i] = P[i]


@wp.kernel
def k_gather(s: Surf, w: Win, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), spp: int, seed: int,
             eps: float, Eb: wp.array(dtype=wp.vec3), Ew: wp.array(dtype=wp.vec3), esc: wp.array(dtype=float),
             back: wp.array(dtype=float)):
    i = wp.tid()
    n = N[i]
    o = P[i] + n * eps
    st = wp.rand_init(seed, i)
    sb = wp.vec3(0.0, 0.0, 0.0)
    sw = wp.vec3(0.0, 0.0, 0.0)
    se = float(0.0)
    sk = float(0.0)
    for k in range(spp):
        d = cosine_dir(n, wp.randf(st), wp.randf(st))
        q = wp.mesh_query_ray(s.mesh, o, d, 1.0e3)
        tr = float(1.0e9)
        if q.result:
            tr = q.t
        hit_window = False
        if w.has == 1:
            qw = wp.mesh_query_ray(w.mesh, o, d, tr)
            if qw.result:
                hit_window = True
        if not hit_window:
            if q.result:
                f = q.face
                if wp.dot(d, s.fn[f]) < 0.0:
                    if s.glass[f] == 0:
                        sb = sb + radiance(s, f, q.u, q.v)
                else:
                    sk = sk + 1.0
            else:
                se = se + 1.0
        if w.has == 1:
            sw = sw + window_E(s.mesh, w, o, n, wp.randf(st), wp.randf(st), wp.randf(st))
    Eb[i] = Eb[i] + sb * (3.141592653589793 / float(spp))
    Ew[i] = Ew[i] + sw / float(spp)
    esc[i] = esc[i] + se / float(spp)
    back[i] = back[i] + sk / float(spp)


@wp.kernel
def k_direct_fixtures(s: Surf, fx: Fix, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), eps: float,
                      acc: wp.array2d(dtype=float)):
    i = wp.tid()
    n = N[i]
    x = P[i] + n * eps
    for k in range(fx.n):
        e = fixture_E(s.mesh, fx, k, x, n)
        acc[i, k * 3] = acc[i, k * 3] + e
        acc[i, k * 3 + 1] = acc[i, k * 3 + 1] + e
        acc[i, k * 3 + 2] = acc[i, k * 3 + 2] + e


@wp.kernel
def k_paths(s: Surf, w: Win, fx: Fix, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), spp: int,
            bounces: int, seed: int, eps: float, inv_total_spp: float, acc: wp.array2d(dtype=float)):
    """Window direct at the receiver (MC) + all indirect orders, NEE to every column at each vertex."""
    i = wp.tid()
    st = wp.rand_init(seed, i)
    n0 = N[i]
    x0 = P[i] + n0 * eps
    nk = fx.n
    for k in range(spp):
        if w.has == 1:
            ew = window_E(s.mesh, w, x0, n0, wp.randf(st), wp.randf(st), wp.randf(st))
            acc[i, nk * 3] = acc[i, nk * 3] + ew[0] * inv_total_spp
            acc[i, nk * 3 + 1] = acc[i, nk * 3 + 1] + ew[1] * inv_total_spp
            acc[i, nk * 3 + 2] = acc[i, nk * 3 + 2] + ew[2] * inv_total_spp
        x = x0
        n = n0
        thr = wp.vec3(inv_total_spp, inv_total_spp, inv_total_spp)
        for b in range(bounces):
            d = cosine_dir(n, wp.randf(st), wp.randf(st))
            q = wp.mesh_query_ray(s.mesh, x, d, 1.0e3)
            if not q.result:
                break
            if w.has == 1:
                qw = wp.mesh_query_ray(w.mesh, x, d, q.t)
                if qw.result:
                    break
            f = q.face
            nh = s.fn[f]
            if wp.dot(d, nh) >= 0.0 or s.glass[f] != 0:
                break
            thr = wp.cw_mul(thr, albedo(s, f, q.u, q.v))
            x = x + d * q.t + nh * eps
            n = nh
            for j in range(nk):
                e = fixture_E(s.mesh, fx, j, x, n)
                if e > 0.0:
                    acc[i, j * 3] = acc[i, j * 3] + thr[0] * e
                    acc[i, j * 3 + 1] = acc[i, j * 3 + 1] + thr[1] * e
                    acc[i, j * 3 + 2] = acc[i, j * 3 + 2] + thr[2] * e
            if w.has == 1:
                ew = window_E(s.mesh, w, x, n, wp.randf(st), wp.randf(st), wp.randf(st))
                acc[i, nk * 3] = acc[i, nk * 3] + thr[0] * ew[0]
                acc[i, nk * 3 + 1] = acc[i, nk * 3 + 1] + thr[1] * ew[1]
                acc[i, nk * 3 + 2] = acc[i, nk * 3 + 2] + thr[2] * ew[2]


@wp.kernel
def k_point_lamps(s: Surf, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), eps: float,
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
            if not occluded(s.mesh, x, l, dist - 0.02):
                acc = acc + lI[k] * (c / d2)
    out[i] = acc


@wp.kernel
def k_sun(s: Surf, w: Win, P: wp.array(dtype=wp.vec3), N: wp.array(dtype=wp.vec3), eps: float,
          to_sun: wp.vec3, out: wp.array(dtype=float)):
    """cos-weighted visibility of a directional sun that must pass through a window quad."""
    i = wp.tid()
    n = N[i]
    x = P[i] + n * eps
    c = wp.dot(n, to_sun)
    out[i] = 0.0
    if c > 0.0:
        qw = wp.mesh_query_ray(w.mesh, x, to_sun, 50.0)
        if qw.result:
            if not occluded(s.mesh, x, to_sun, qw.t):
                out[i] = c


# ------------------------------------------------------------------ host-side scene
class Scene:
    """Scan mesh (+ auxiliary faces) + window emitter quads + fixture columns, on one device."""

    def __init__(self, device, positions, faces, uv, vertex_normals, face_normals, glass_faces,
                 capture_texture, window=None, fixtures=(), eps=0.01, n_main=None,
                 aux_radiance=None, aux_albedo=None):
        """aux_radiance: linear (H,W,3) radiance of faces >= n_main; aux_albedo: sRGB u8 (H,W,3)."""
        self.device = device
        self.eps = eps
        d = device
        self.nf = len(faces)
        self._keep = []
        self.mesh = wp.Mesh(points=self._a(positions, wp.vec3), indices=self._a(np.asarray(faces).ravel(), wp.int32))
        s = Surf()
        s.mesh = self.mesh.id
        s.nmain = int(self.nf if n_main is None else n_main)
        s.tri = self._a(np.asarray(faces).ravel(), wp.int32)
        s.uv = self._a(uv, wp.vec2)
        s.fn = self._a(face_normals, wp.vec3)
        s.glass = self._a(np.asarray(glass_faces, np.int32), wp.int32)
        s.lut = self._a(srgb_lut(), float)
        img = np.ascontiguousarray(capture_texture, np.uint8)
        s.tex, s.tw, s.th = self._a(img.ravel(), wp.uint8), img.shape[1], img.shape[0]
        s.vE = self._a(np.ones((len(positions), 3), np.float32), wp.vec3)
        s.floor, s.rmax, s.mode, s.alb_const = 1e-6, 0.95, 0, wp.vec3(0.0, 0.0, 0.0)
        self.s = s
        ar = np.zeros((1, 1, 3)) if aux_radiance is None else np.asarray(aux_radiance, np.float64)
        aa = np.zeros(ar.shape, np.uint8) if aux_albedo is None else aux_albedo
        self.set_aux(ar, aa)
        self.vn = self._a(vertex_normals, wp.vec3)
        self.set_window(window)
        self.set_fixtures(fixtures)

    def _a(self, a, dtype):
        arr = wp.array(np.ascontiguousarray(a, np.uint8 if dtype is wp.uint8 else
                                            np.int32 if dtype is wp.int32 else np.float32), dtype=dtype, device=self.device)
        self._keep.append(arr)
        return arr

    def set_aux(self, radiance, albedo_u8):
        """Auxiliary-face (ceiling cap) textures: linear HDR radiance (H,W,3) and sRGB u8 albedo (same size)."""
        rad = np.clip(np.asarray(radiance, np.float64), 0, None)
        scale = max(float(rad.max()), 1e-6)
        x = rad / scale
        enc = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
        ar = np.round(np.clip(enc, 0, 1) * 255).astype(np.uint8)
        aa = np.ascontiguousarray(albedo_u8, np.uint8)
        self.s.aux_rad, self.s.aux_alb = self._a(ar.ravel(), wp.uint8), self._a(aa.ravel(), wp.uint8)
        self.s.aw, self.s.ah, self.s.aux_scale = ar.shape[1], ar.shape[0], scale

    def set_glass(self, glass):
        self.s.glass = self._a(np.asarray(glass, np.int32), wp.int32)

    def set_albedo_constant(self, rgb):
        self.s.mode, self.s.alb_const = 0, wp.vec3(*map(float, rgb))

    def set_albedo_ratio(self, vertex_E, floor, rho_max, aux_albedo=None):
        """Scan albedo = clamp(pi * atlas / max(E_vertex, floor)); aux faces use aux_albedo."""
        self.s.vE = self._a(vertex_E, wp.vec3)
        self.s.floor, self.s.rmax, self.s.mode = float(floor), float(rho_max), 1
        if aux_albedo is not None:
            aa = np.ascontiguousarray(aux_albedo, np.uint8)
            self.s.aux_alb = self._a(aa.ravel(), wp.uint8)

    def set_window(self, window):
        w = Win()
        if window is None or len(window["v0"]) == 0:
            w.has = 0
            pts = np.array([[0, 0, -1e4], [1, 0, -1e4], [0, 1, -1e4]], np.float32)
            self.win = wp.Mesh(points=self._a(pts, wp.vec3), indices=self._a(np.array([0, 1, 2]), wp.int32))
            z3 = np.zeros((1, 3), np.float32)
            w.v0 = w.v1 = w.v2 = w.n = w.L = self._a(z3, wp.vec3)
            w.cdf = w.pdf = self._a(np.ones(1), float)
        else:
            w.has = 1
            v0, v1, v2 = (np.asarray(window[k], np.float32) for k in ("v0", "v1", "v2"))
            m = len(v0)
            idx = np.stack([np.arange(m), np.arange(m) + m, np.arange(m) + 2 * m], 1).ravel()
            self.win = wp.Mesh(points=self._a(np.concatenate([v0, v1, v2]), wp.vec3), indices=self._a(idx, wp.int32))
            area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
            L = np.asarray(window["L"], np.float64)
            wgt = area * np.maximum(L @ [0.2126, 0.7152, 0.0722], 1e-9)
            p_tri = wgt / wgt.sum()
            cdf = np.cumsum(p_tri)
            cdf[-1] = 1.0
            w.v0, w.v1, w.v2 = self._a(v0, wp.vec3), self._a(v1, wp.vec3), self._a(v2, wp.vec3)
            w.n, w.L = self._a(window["n"], wp.vec3), self._a(L, wp.vec3)
            w.cdf, w.pdf = self._a(cdf, float), self._a(p_tri / area, float)
        w.mesh = self.win.id
        self.w = w

    def set_fixtures(self, cols):
        cols = list(cols)
        f = Fix()
        f.n = len(cols)
        if not cols:
            cols = [{"position": np.zeros(3), "axis": np.array([0, 0, -1.0]), "hwhm_deg": 1.0, "max_theta_deg": 0.0,
                     "peak_cd_per_lm": 0.0}]
        f.pos = self._a([c["position"] for c in cols], wp.vec3)
        f.axis = self._a([c["axis"] for c in cols], wp.vec3)
        f.hw = self._a(np.radians([c["hwhm_deg"] for c in cols]), float)
        f.maxcos = self._a(np.cos(np.radians([c["max_theta_deg"] for c in cols])), float)
        f.peak = self._a([c["peak_cd_per_lm"] for c in cols], float)
        self.fx = f

    # -------------------------------------------------------------- queries
    def _v3(self, a):
        return wp.array(np.ascontiguousarray(a, np.float32), dtype=wp.vec3, device=self.device)

    def closest(self, points, max_dist=1.0, chunk=1 << 20):
        """Closest scan point: (face, distance, interpolated normal, uv, closest point)."""
        n = len(points)
        face = np.empty(n, np.int32)
        dist = np.empty(n, np.float32)
        nrm = np.zeros((n, 3), np.float32)
        uvo = np.zeros((n, 2), np.float32)
        cp = np.zeros((n, 3), np.float32)
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            m = b - a
            outs = [wp.zeros(m, dtype=t, device=self.device) for t in (wp.int32, float, wp.vec3, wp.vec2, wp.vec3)]
            wp.launch(k_closest, m, [self.s, self.vn, self._v3(points[a:b]), max_dist, *outs], device=self.device)
            face[a:b], dist[a:b], nrm[a:b], uvo[a:b], cp[a:b] = (o.numpy() for o in outs)
        return face, dist, nrm, uvo, cp

    def gather_capture(self, P, N, spp=256, batch=32, chunk=1 << 18, seed=1):
        """Capture-state irradiance: (E_bounce, E_window, escape fraction, back-face fraction)."""
        n = len(P)
        Eb, Ew, es, bk = np.zeros((n, 3)), np.zeros((n, 3)), np.zeros(n), np.zeros(n)
        passes = max(1, spp // batch)
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            m = b - a
            p_, n_ = self._v3(P[a:b]), self._v3(N[a:b])
            outs = [wp.zeros(m, dtype=t, device=self.device) for t in (wp.vec3, wp.vec3, float, float)]
            for s in range(passes):
                wp.launch(k_gather, m, [self.s, self.w, p_, n_, batch, seed * 7919 + s * 104729 + a, self.eps, *outs],
                          device=self.device)
            Eb[a:b], Ew[a:b], es[a:b], bk[a:b] = (o.numpy() / passes for o in outs)
        return Eb, Ew, es, bk

    def basis(self, P, N, spp=64, bounces=5, batch=4, chunk=1 << 17, seed=2):
        """Unit basis per receiver: (n, nk+1, 3) RGB irradiance; last column = window."""
        n = len(P)
        K = self.fx.n + 1
        out = np.zeros((n, K, 3))
        passes = max(1, spp // batch)
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            m = b - a
            p_, n_ = self._v3(P[a:b]), self._v3(N[a:b])
            acc = wp.zeros((m, K * 3), dtype=float, device=self.device)
            if self.fx.n:
                wp.launch(k_direct_fixtures, m, [self.s, self.fx, p_, n_, self.eps, acc], device=self.device)
            for s in range(passes):
                wp.launch(k_paths, m, [self.s, self.w, self.fx, p_, n_, batch, bounces, seed * 7919 + s * 104729 + a,
                                       self.eps, 1.0 / (passes * batch), acc], device=self.device)
            out[a:b] = acc.numpy().reshape(m, K, 3)
        return out

    def point_lamps(self, P, N, lamps):
        if not lamps:
            return np.zeros((len(P), 3))
        out = wp.zeros(len(P), dtype=wp.vec3, device=self.device)
        wp.launch(k_point_lamps, len(P), [self.s, self._v3(P), self._v3(N), self.eps,
                                          self._v3([l["position"] for l in lamps]),
                                          self._v3([l["intensity_rgb"] for l in lamps]), out], device=self.device)
        return out.numpy().astype(np.float64)

    def sun_cos(self, P, N, to_sun):
        if not self.w.has:
            return np.zeros(len(P))
        out = wp.zeros(len(P), dtype=float, device=self.device)
        wp.launch(k_sun, len(P), [self.s, self.w, self._v3(P), self._v3(N), self.eps,
                                  wp.vec3(*map(float, to_sun)), out], device=self.device)
        return out.numpy().astype(np.float64)

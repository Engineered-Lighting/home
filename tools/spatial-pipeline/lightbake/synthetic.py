"""Synthetic room for the public L3 test: a port of lighting spike 1's scene.

Everything here is procedural: an 8 x 6 x 2.7 m closed room (Y up, metres),
16 furniture boxes, a window (sky + sun) on the z=0 wall, 4 ceiling fixtures x
(spot, down, 6 radial zones) = 32 cone emitters, patch radiosity with box
visibility. No Den data is used. Ported from the 2026-10-19 research spike
(lighting-spikes/common/scene.py) with the colour helpers moved to encode.py.

Lighting is linear in emitter weights, so it is expressed as a per-emitter
basis  E(point) = sum_k w_k * c_k (.) (D_k + B_k)  (RGB). Columns: 0..31
emitters, 32 = window sky, 33 = sun through the window.
"""
import numpy as np

from .encode import lum, srgb_eotf, srgb_oetf  # noqa: F401  (re-exported for the test)

ROOM = np.array([8.0, 2.7, 6.0])
WIN = dict(x0=2.5, x1=5.5, y0=0.9, y1=2.3)  # on wall z=0, normal +z into room
SUN_DIR = np.array([0.30, -0.55, 0.78]); SUN_DIR /= np.linalg.norm(SUN_DIR)  # travel direction
FIX_XZ = np.array([[2.0, 1.5], [6.0, 1.5], [2.0, 4.5], [6.0, 4.5]])
FIX_Y = 2.55
NL = 34  # 32 emitters + sky + sun
SKY, SUN = 32, 33


def hash1(i):
    return np.modf(np.abs(np.sin(i * 12.9898 + 78.233) * 43758.5453))[0]


def cct_rgb(T):
    """Planckian locus (Kim et al. 2002 cubic approx) -> linear sRGB, luminance normalised to 1."""
    T = float(T)
    if T <= 4000:
        x = -0.2661239e9 / T**3 - 0.2343589e6 / T**2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T**3 + 2.1070379e6 / T**2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    X, Y, Z = x / y, 1.0, (1 - x - y) / y
    M = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]])
    rgb = np.maximum(M @ np.array([X, Y, Z]), 0)
    return rgb / lum(rgb)


# ---------------------------------------------------------------- geometry
class Scene:
    def __init__(self, seed=7, n_boxes=16):
        r = np.random.default_rng(seed)
        F = []  # (o, u, v, n, base, pattern, box)

        def add(o, u, v, n, base, pat, box=-1):
            F.append((np.array(o, float), np.array(u, float), np.array(v, float), np.array(n, float), np.array(base, float), pat, box))
        X, H, Z = ROOM
        add([0, 0, 0], [X, 0, 0], [0, 0, Z], [0, 1, 0], [0.42, 0.30, 0.20], 1)        # wood floor
        add([0, H, 0], [X, 0, 0], [0, 0, Z], [0, -1, 0], [0.85, 0.85, 0.83], 0)       # ceiling
        add([0, 0, 0], [X, 0, 0], [0, H, 0], [0, 0, 1], [0.80, 0.78, 0.72], 0)        # window wall
        add([0, 0, Z], [X, 0, 0], [0, H, 0], [0, 0, -1], [0.62, 0.70, 0.66], 0)       # sage wall
        add([0, 0, 0], [0, 0, Z], [0, H, 0], [1, 0, 0], [0.78, 0.76, 0.80], 0)        # wall x=0
        add([X, 0, 0], [0, 0, Z], [0, H, 0], [-1, 0, 0], [0.82, 0.76, 0.70], 5)      # wall x=8 with poster
        lo, hi = [], []
        pats = [2, 3, 4, 2, 0, 3, 4, 2]
        for i in range(n_boxes):
            for _ in range(50):
                sx, sz, sy = 0.4 + r.random() * 1.4, 0.4 + r.random() * 1.0, 0.4 + r.random() * 1.1
                x, z = 0.3 + r.random() * (X - sx - 0.6), 0.3 + r.random() * (Z - sz - 0.6)
                # keep a clear zone in front of the window and around the centre so the sun patch exists
                if x < WIN['x1'] and x + sx > WIN['x0'] and z < 1.4:
                    continue
                break
            pat = pats[i % len(pats)]
            c = np.array([0.15 + r.random() * 0.7, 0.15 + r.random() * 0.6, 0.15 + r.random() * 0.6])
            if pat == 4:
                c = c * 0.08  # near-black furniture
            lo.append([x, 0, z]); hi.append([x + sx, sy, z + sz])
            add([x, sy, z], [sx, 0, 0], [0, 0, sz], [0, 1, 0], c, pat, i)
            add([x, 0, z], [sx, 0, 0], [0, sy, 0], [0, 0, -1], c, pat, i)
            add([x, 0, z + sz], [sx, 0, 0], [0, sy, 0], [0, 0, 1], c, pat, i)
            add([x, 0, z], [0, 0, sz], [0, sy, 0], [-1, 0, 0], c, pat, i)
            add([x + sx, 0, z], [0, 0, sz], [0, sy, 0], [1, 0, 0], c, pat, i)
        self.lo, self.hi = np.array(lo), np.array(hi)
        self.fo = np.array([f[0] for f in F]); self.fu = np.array([f[1] for f in F]); self.fv = np.array([f[2] for f in F])
        self.fn = np.array([f[3] for f in F]); self.fbase = np.array([f[4] for f in F])
        self.fpat = np.array([f[5] for f in F]); self.fbox = np.array([f[6] for f in F])
        self.farea = np.linalg.norm(self.fu, axis=1) * np.linalg.norm(self.fv, axis=1)
        self.lights = self.default_lights()

    # ---- albedo texture (procedural, function of face + local coords)
    def albedo(self, fid, p):
        o, u, v = self.fo[fid], self.fu[fid], self.fv[fid]
        lu, lv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
        a = np.einsum('ij,ij->i', p - o, u) / lu  # metres along u
        b = np.einsum('ij,ij->i', p - o, v) / lv
        base = self.fbase[fid].copy()
        pat = self.fpat[fid]
        f = np.ones(len(fid))
        m = pat == 0
        f[m] = 1 + 0.05 * np.sin(a[m] * 7.1) * np.sin(b[m] * 5.3)
        m = pat == 1
        plank = np.floor(b[m] / 0.18)
        f[m] = (0.72 + 0.45 * hash1(plank)) * (1 + 0.08 * np.sin(a[m] * 31 + plank * 3.1))
        m = pat == 2
        chk = (np.floor(a[m] / 0.08) + np.floor(b[m] / 0.08)) % 2
        f[m] = np.where(chk > 0, 1.0, 0.55)
        m = pat == 4
        f[m] = 1 + 0.15 * np.sin(a[m] * 20)
        alb = base * f[:, None]
        m = pat == 3  # two-colour stripes
        if m.any():
            stripe = (np.floor(a[m] / 0.12) % 2) > 0
            comp = 0.85 - base[m]
            alb[m] = np.where(stripe[:, None], base[m], comp)
        m = pat == 5  # wall with a high-contrast poster (a in [1.5,3.5] along z, b in [1.0,2.0] height)
        if m.any():
            inp = (a[m] > 1.5) & (a[m] < 3.5) & (b[m] > 1.0) & (b[m] < 2.0)
            blk = (np.floor(a[m] / 0.25) + 3 * np.floor(b[m] / 0.25)).astype(int) % 5
            pal = np.array([[0.75, 0.12, 0.08], [0.05, 0.25, 0.65], [0.85, 0.75, 0.10], [0.04, 0.04, 0.04], [0.9, 0.9, 0.88]])
            sub = alb[m]
            sub[inp] = pal[blk[inp]]
            alb[m] = sub
        return np.clip(alb, 0.01, 0.95)

    # ---- surface samples ("splats")
    def sample(self, n, seed=1):
        r = np.random.default_rng(seed)
        cdf = np.cumsum(self.farea) / self.farea.sum()
        out_p, out_f = [], []
        need = n
        while need > 0:
            m = int(need * 1.35) + 100
            fid = np.minimum(np.searchsorted(cdf, r.random(m)), len(cdf) - 1)
            a, b = r.random(m), r.random(m)
            p = self.fo[fid] + self.fu[fid] * a[:, None] + self.fv[fid] * b[:, None]
            ok = np.ones(m, bool)
            for bi in range(len(self.lo)):
                inside = np.all((p > self.lo[bi] - 1e-6) & (p < self.hi[bi] + 1e-6), axis=1) & (self.fbox[fid] != bi)
                ok &= ~inside
            win = (fid == 2) & (p[:, 0] > WIN['x0']) & (p[:, 0] < WIN['x1']) & (p[:, 1] > WIN['y0']) & (p[:, 1] < WIN['y1'])
            ok &= ~win
            out_p.append(p[ok]); out_f.append(fid[ok]); need -= ok.sum()
        p = np.concatenate(out_p)[:n]; fid = np.concatenate(out_f)[:n]
        total_area = self.farea.sum() - (WIN['x1'] - WIN['x0']) * (WIN['y1'] - WIN['y0'])
        return dict(p=p, n=self.fn[fid].copy(), fid=fid, alb=self.albedo(fid, p), area=total_area / n)

    # ---- emitters
    def default_lights(self):
        L = []  # dict(pos, dir, cin, cout, kind, fixture)
        r = np.random.default_rng(3)
        spot_targets = [[1.2, 0.0, 0.8], [6.8, 0.0, 0.7], [3.0, 0.0, 3.6], [6.5, 0.0, 3.9]]
        for f, (x, z) in enumerate(FIX_XZ):
            pos = np.array([x, FIX_Y, z])
            t = np.array(spot_targets[f]); t[1] = 0.6
            d = t - pos; d /= np.linalg.norm(d)
            L.append(dict(pos=pos, dir=d, cin=np.cos(np.radians(9)), cout=np.cos(np.radians(14)), kind='spot', fixture=f))
            L.append(dict(pos=pos, dir=np.array([0, -1.0, 0]), cin=np.cos(np.radians(28)), cout=np.cos(np.radians(50)), kind='down', fixture=f))
            for k in range(6):
                az = np.radians(60 * k + 15 * f); tilt = np.radians(60)
                d = np.array([np.sin(tilt) * np.cos(az), -np.cos(tilt), np.sin(tilt) * np.sin(az)])
                L.append(dict(pos=pos, dir=d, cin=np.cos(np.radians(18)), cout=np.cos(np.radians(34)), kind='radial', fixture=f))
        return L

def rot_small(d, deg, r):
    """rotate unit vector d by `deg` degrees in a random direction"""
    a = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 0, 1.0])
    t1 = np.cross(d, a); t1 /= np.linalg.norm(t1); t2 = np.cross(d, t1)
    phi = r.random() * 2 * np.pi; th = np.radians(deg)
    out = np.cos(th) * d + np.sin(th) * (np.cos(phi) * t1 + np.sin(phi) * t2)
    return out / np.linalg.norm(out)


# ---------------------------------------------------------------- visibility & transport
def seg_visible(P, Q, lo, hi):
    """P,Q (M,3). True where segment P->Q is not blocked by any box."""
    d = Q - P
    d = np.where(np.abs(d) < 1e-9, np.float32(1e-9), d)
    inv = 1.0 / d
    vis = np.ones(len(P), bool)
    for b in range(len(lo)):
        t0 = (lo[b] - P) * inv; t1 = (hi[b] - P) * inv
        tmin = np.minimum(t0, t1).max(1); tmax = np.maximum(t0, t1).min(1)
        hit = (tmax >= np.maximum(tmin, 1e-5)) & (tmin <= 1 - 1e-5)
        vis &= ~hit
    return vis


def direct_basis(sc, P, N, lights, chunk=60000):
    """D (M, 34) float32: geometric direct term per emitter + sky + sun (no intensity/colour)."""
    M = len(P)
    D = np.zeros((M, NL), np.float32)
    Po = P + N * 1e-3
    for k, L in enumerate(lights):
        v = L['pos'] - Po
        d2 = (v * v).sum(1); d = np.sqrt(d2); l = v / d[:, None]
        cosn = np.maximum((N * l).sum(1), 0)
        ca = -(l @ L['dir'])
        cone = np.clip((ca - L['cout']) / (L['cin'] - L['cout']), 0, 1); cone = cone * cone * (3 - 2 * cone)
        g = cosn * cone / d2
        idx = np.nonzero(g > 0)[0]
        if len(idx):
            vis = seg_visible(Po[idx], np.broadcast_to(L['pos'], (len(idx), 3)), sc.lo, sc.hi)
            D[idx, k] = g[idx] * vis
    # sky: stratified window samples, Lambertian emitter with normal +z
    nx, ny = 8, 6
    xs = WIN['x0'] + (np.arange(nx) + 0.5) / nx * (WIN['x1'] - WIN['x0'])
    ys = WIN['y0'] + (np.arange(ny) + 0.5) / ny * (WIN['y1'] - WIN['y0'])
    As = (WIN['x1'] - WIN['x0']) * (WIN['y1'] - WIN['y0']) / (nx * ny)
    for wx in xs:
        for wy in ys:
            w = np.array([wx, wy, 0.0])
            v = w - Po; d2 = (v * v).sum(1); d = np.sqrt(d2); l = v / d[:, None]
            g = np.maximum((N * l).sum(1), 0) * np.maximum(-l[:, 2], 0) * As / (d2 + As / np.pi)
            idx = np.nonzero(g > 0)[0]
            if len(idx):
                D[idx, SKY] += g[idx] * seg_visible(Po[idx], np.broadcast_to(w, (len(idx), 3)), sc.lo, sc.hi)
    # sun: ray toward sun must exit through the window rectangle
    s = SUN_DIR
    t = Po[:, 2] / s[2]
    q = Po - t[:, None] * s
    inwin = (t > 0) & (q[:, 0] > WIN['x0']) & (q[:, 0] < WIN['x1']) & (q[:, 1] > WIN['y0']) & (q[:, 1] < WIN['y1'])
    g = np.maximum(-(N @ s), 0) * inwin
    idx = np.nonzero(g > 0)[0]
    if len(idx):
        D[idx, SUN] = g[idx] * seg_visible(Po[idx], q[idx], sc.lo, sc.hi)
    return D


def patch_transport(sc, Pp, Np_, area):
    """T (Np,Np): irradiance at patch i per unit radiance of patch j, with box visibility."""
    n = len(Pp)
    T = np.zeros((n, n), np.float32)
    Po = Pp + Np_ * 1e-3
    for i0 in range(0, n, 200):
        i1 = min(n, i0 + 200)
        Pi = np.repeat(Po[i0:i1], n, 0); Ni = np.repeat(Np_[i0:i1], n, 0)
        Pj = np.tile(Po, (i1 - i0, 1)); Nj = np.tile(Np_, (i1 - i0, 1))
        v = Pj - Pi; d2 = (v * v).sum(1) + 1e-12; d = np.sqrt(d2); l = v / d[:, None]
        g = np.maximum((Ni * l).sum(1), 0) * np.maximum(-(Nj * l).sum(1), 0) * area / (d2 + area / np.pi)
        idx = np.nonzero(g > 0)[0]
        vis = seg_visible(Pi[idx], Pj[idx], sc.lo, sc.hi)
        gg = np.zeros_like(g); gg[idx] = g[idx] * vis
        T[i0:i1] = gg.reshape(i1 - i0, n)
    np.fill_diagonal(T, 0)
    return T


def gather_vis(sc, P, N, Pp, Np_, chunk=1000):
    """Visibility (M, Np) bool between points and patches (boxes only). Depends on positions only."""
    V = np.zeros((len(P), len(Pp)), bool)
    Po = (P + N * 1e-3).astype(np.float32)
    Ppo = (Pp + Np_ * 1e-3).astype(np.float32)
    lo, hi = sc.lo.astype(np.float32), sc.hi.astype(np.float32)
    for i0 in range(0, len(P), chunk):
        i1 = min(len(P), i0 + chunk)
        A = np.repeat(Po[i0:i1], len(Pp), 0); B = np.tile(Ppo, (i1 - i0, 1))
        V[i0:i1] = seg_visible(A, B, lo, hi).reshape(i1 - i0, len(Pp))
    return V


def gather(P, N, Pp, Np_, area, Lp, V=None, chunk=4000):
    """Irradiance at points from patch radiance Lp (Np, K). V: optional (M,Np) visibility."""
    out = np.zeros((len(P), Lp.shape[1]), np.float32)
    for i0 in range(0, len(P), chunk):
        i1 = min(len(P), i0 + chunk)
        v = (Pp[None, :, :] - P[i0:i1, None, :]).astype(np.float32)
        d2 = (v * v).sum(2) + 1e-12; d = np.sqrt(d2)
        ci = np.maximum((N[i0:i1, None, :].astype(np.float32) * v).sum(2) / d, 0)
        cj = np.maximum(-(Np_[None, :, :].astype(np.float32) * v).sum(2) / d, 0)
        G = (ci * cj * np.float32(area) / (d2 + np.float32(area / np.pi))).astype(np.float32)
        if V is not None:
            G *= V[i0:i1]
        out[i0:i1] = G @ Lp
    return out


def bounce_basis(T, Dp, rho_p, bounces=3):
    """Per-channel patch radiance after multi-bounce. Dp (Np,K) direct at patches, rho_p (Np,3).
    Returns Lp (Np, K, 3) outgoing radiance of patches = rho/pi * (direct + indirect)."""
    Lp = np.zeros(Dp.shape + (3,), np.float32)
    for c in range(3):
        rp = (rho_p[:, c] / np.pi).astype(np.float32)[:, None]
        E = Dp.copy(); Eb = Dp.copy()
        for _ in range(bounces):
            Eb = T @ (rp * Eb)
            E = E + Eb
        Lp[:, :, c] = rp * E
    return Lp


def gather_matrix(P, N, Pp, Np_, area, V=None, chunk=4000):
    G = np.zeros((len(P), len(Pp)), np.float32)
    for i0 in range(0, len(P), chunk):
        i1 = min(len(P), i0 + chunk)
        v = (Pp[None, :, :] - P[i0:i1, None, :]).astype(np.float32)
        d2 = (v * v).sum(2) + 1e-12; d = np.sqrt(d2)
        ci = np.maximum((N[i0:i1, None, :].astype(np.float32) * v).sum(2) / d, 0)
        cj = np.maximum(-(Np_[None, :, :].astype(np.float32) * v).sum(2) / d, 0)
        G[i0:i1] = ci * cj * np.float32(area) / (d2 + np.float32(area / np.pi))
        if V is not None:
            G[i0:i1] *= V[i0:i1]
    return G


def gather_vis_apply(G, Rp):
    return (G @ Rp.astype(np.float32)).astype(np.float32)


def full_basis(P, N, D, Pp, Np_, parea, T, Dp, rho_p, bounces=3, with_bounce=True, V=None, G=None):
    """Return basis (M, 34, 3): direct + indirect per emitter per channel."""
    Bas = np.repeat(D[:, :, None], 3, 2).astype(np.float32)
    if with_bounce and bounces > 0:
        Lp = bounce_basis(T, Dp, rho_p, bounces)
        K = Dp.shape[1]
        if G is not None:
            Ei = (G @ Lp.reshape(len(Pp), K * 3)).reshape(len(P), K, 3)
        else:
            Ei = gather(P, N, Pp, Np_, parea, Lp.reshape(len(Pp), K * 3), V).reshape(len(P), K, 3)
        Bas += Ei
    return Bas


def irradiance(Bas, w, c):
    """Bas (M,34,3), w (34,), c (34,3) -> E (M,3)"""
    return np.einsum('mkc,kc->mc', Bas, (w[:, None] * c).astype(np.float32))

# ---------------------------------------------------------------- lighting states
def state(name, lights):
    """weights w (34,) [emitter intensity units] and colours c (34,3)."""
    I = {'spot': 80.0, 'down': 40.0, 'radial': 18.0}
    w = np.zeros(NL); c = np.zeros((NL, 3))
    c[:] = cct_rgb(2700)
    c[SKY] = cct_rgb(9000); c[SUN] = cct_rgb(5300)
    SKY_L, SUN_E = 8.0, 80.0
    def on(f, kind, frac, T):
        for k, L in enumerate(lights):
            if L['fixture'] == f and L['kind'] == kind:
                w[k] = I[kind] * frac; c[k] = cct_rgb(T)
    if name == 'capture':
        on(0, 'down', 1.0, 3000); on(0, 'radial', 0.6, 3000); on(1, 'down', 1.0, 3000); on(2, 'spot', 1.0, 4000)
        w[SKY] = SKY_L; w[SUN] = SUN_E
    elif name == 'one_spot':
        on(3, 'spot', 1.0, 2700)
    elif name == 'night_ambient':
        w[SKY] = SKY_L * 0.004; c[SKY] = cct_rgb(7500)
    elif name == 'all_on_warm':
        for f in range(4):
            for kind in ('spot', 'down', 'radial'):
                on(f, kind, 1.0, 2700)
    elif name == 'all_on_dim30':
        for f in range(4):
            for kind in ('spot', 'down', 'radial'):
                on(f, kind, 0.3, 2700)
    elif name == 'spot_5pct':
        on(3, 'spot', 0.05, 2700)
    elif name == 'one_fixture_radials20':
        on(1, 'radial', 0.2, 2700)
    elif name == 'off_moonlight':
        w[SKY] = SKY_L * 0.0015; c[SKY] = cct_rgb(7500)
    elif name == 'day_window_only':
        w[SKY] = SKY_L; w[SUN] = SUN_E
    else:
        raise ValueError(name)
    return w, c

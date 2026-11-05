#!/usr/bin/env python3
"""Probe: recompute den-register's rigid fit and gate independently.

1. Rigid fit: rigid.kabsch (Horn quaternion) vs an independent stdlib fit (polar
   decomposition of the cross-covariance by Newton iteration, det checked), on
   constructed control points with a known pose plus noise; also the Umeyama fit WITH
   scale, to report the scale the data imply and how much the no-scale residuals grow.
2. 5 cm rule at the boundary, through register.evaluate with exact camera points
   (depth sampling patched out): a check at 4.99 cm and 5.01 cm; a fit residual
   bisected to the flip point.
3. Collinearity: rigid.spread vs a closed form on three points; refusal at
   min_spread_m (proposed 0.25 m) +- 1 mm; a near-collinear set that passes the
   spread rule with three fit points.
4. transform_id format: what the tool writes and resolves.

Run:  python3 probe_fit-gate-recompute.py <rung-1 checkout root>
"""
import math
import random
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-register"
sys.path.insert(0, str(tool))
import method, records, register, rigid, scene, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
fails = 0


def mat_t(a):
    return [list(r) for r in zip(*a)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def inv3(a):
    (p, q, r), (s, t, u), (v, w, x) = a
    det = p * (t * x - u * w) - q * (s * x - u * v) + r * (s * w - t * v)
    adj = [[t * x - u * w, r * w - q * x, q * u - r * t], [u * v - s * x, p * x - r * v, r * s - p * u],
           [s * w - t * v, q * v - p * w, p * t - q * s]]
    return [[adj[i][j] / det for j in range(3)] for i in range(3)], det


def svd3(m):
    """One-sided Jacobi (Hestenes) SVD of a 3x3 matrix: m = U diag(sig) V^T."""
    a = [row[:] for row in m]
    v = [[float(i == j) for j in range(3)] for i in range(3)]
    for _ in range(100):
        rotated = False
        for p in range(2):
            for q in range(p + 1, 3):
                alpha = sum(a[k][p] ** 2 for k in range(3))
                beta = sum(a[k][q] ** 2 for k in range(3))
                gamma = sum(a[k][p] * a[k][q] for k in range(3))
                if abs(gamma) <= 1e-18 * math.sqrt(alpha * beta) or gamma == 0:
                    continue
                rotated = True
                zeta = (beta - alpha) / (2 * gamma)
                t = math.copysign(1.0, zeta) / (abs(zeta) + math.sqrt(1 + zeta * zeta))
                c = 1 / math.sqrt(1 + t * t)
                s_ = c * t
                for k in range(3):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s_ * akq, s_ * akp + c * akq
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s_ * vkq, s_ * vkp + c * vkq
        if not rotated:
            break
    sig = [math.sqrt(sum(a[k][j] ** 2 for k in range(3))) for j in range(3)]
    order = sorted(range(3), key=lambda j: -sig[j])
    sig = [sig[j] for j in order]
    a = [[a[k][j] for j in order] for k in range(3)]
    v = [[v[k][j] for j in order] for k in range(3)]
    u = [[0.0] * 3 for _ in range(3)]
    for j in range(2):
        for k in range(3):
            u[k][j] = a[k][j] / sig[j]
    c0, c1 = [u[k][0] for k in range(3)], [u[k][1] for k in range(3)]
    c2 = [c0[1] * c1[2] - c0[2] * c1[1], c0[2] * c1[0] - c0[0] * c1[2], c0[0] * c1[1] - c0[1] * c1[0]]
    if sig[2] > 1e-12 * sig[0]:
        c2a = [a[k][2] / sig[2] for k in range(3)]
        if sum(x * y for x, y in zip(c2a, c2)) < 0:
            sig[2] = -sig[2]
    for k in range(3):
        u[k][2] = c2[k]
    return u, sig, v


def det3(a):
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def polar_fit(src, dst, with_scale=False):
    """Independent Kabsch/Umeyama: SVD of sum (q - cq)(p - cp)^T, R = U diag(1, 1, d) V^T."""
    n = len(src)
    cs = [sum(p[i] for p in src) / n for i in range(3)]
    cd = [sum(p[i] for p in dst) / n for i in range(3)]
    m = [[sum((dst[k][i] - cd[i]) * (src[k][j] - cs[j]) for k in range(n)) for j in range(3)] for i in range(3)]
    u, sig, v = svd3(m)
    # u's third column is the cross product of the first two (det u = +1), sig[2] signed accordingly;
    # make det V = +1 too, flipping the third singular direction if needed (Kabsch's reflection fix).
    if det3(v) < 0:
        for k in range(3):
            v[k][2] = -v[k][2]
        sig[2] = -sig[2]
    x = [[sum(u[i][k] * v[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    s = 1.0
    if with_scale:
        var = sum((p[i] - cs[i]) ** 2 for p in src for i in range(3))
        s = (sig[0] + sig[1] + sig[2]) / var
    t = [cd[i] - s * sum(x[i][j] * cs[j] for j in range(3)) for i in range(3)]
    return x, t, s


# ---- 1. rigid fit recomputed -------------------------------------------------------
random.seed(7)
worst_r = worst_t = worst_res = 0.0
for trial in range(200):
    r_true = synthetic.rotation_xyz(random.uniform(-3, 3), random.uniform(-1.5, 1.5), random.uniform(-3, 3))
    t_true = [random.uniform(-1, 5) for _ in range(3)]
    n = random.choice([3, 4, 5, 6])
    cam = [[random.uniform(-1.5, 1.5), random.uniform(-1.0, 1.0), random.uniform(1.0, 4.5)] for _ in range(n)]
    room = [[c + random.gauss(0, 0.01) for c in rigid.apply(r_true, t_true, p)] for p in cam]
    r1, t1 = rigid.kabsch(cam, room)
    r2, t2, _ = polar_fit(cam, room)
    worst_r = max(worst_r, max(abs(r1[i][j] - r2[i][j]) for i in range(3) for j in range(3)))
    worst_t = max(worst_t, math.dist(t1, t2))
    res1 = [math.dist(rigid.apply(r1, t1, p), q) for p, q in zip(cam, room)]
    res2 = [math.dist(rigid.apply(r2, t2, p), q) for p, q in zip(cam, room)]
    worst_res = max(worst_res, abs(math.sqrt(sum(a * a for a in res1)) - math.sqrt(sum(b * b for b in res2))))
ok = worst_r < 1e-8 and worst_t < 1e-8 and worst_res < 1e-9
fails += not ok
print(f"{'PASS' if ok else 'FAIL'} kabsch vs polar decomposition, 200 noisy poses (3-6 pairs, 1 cm noise): "
      f"max |dR| {worst_r:.2e}, max |dt| {worst_t:.2e} m, max |d rss| {worst_res:.2e} m")

# with scale: a 2% depth-scale bias (stereo) on the synthetic scene
r_true, t_true = synthetic.camera_pose()
pts = [synthetic.POINTS[k] for k in ("p1", "p2", "p3", "p4")]
cam = [[1.02 * c for c in synthetic.to_camera(r_true, t_true, p)] for p in pts]
fit = rigid.fit(cam, pts)
rs, ts, s = polar_fit(cam, pts, with_scale=True)
res_s = max(math.dist([s * v for v in rigid.apply(rs, [0, 0, 0], p)], [q[i] - ts[i] for i in range(3)])
            for p, q in zip(cam, pts))
print(f"INFO 2% depth-scale bias, synthetic 4 fit points: no-scale max residual {fit['max_m'] * 100:.2f} cm "
      f"(tool), with-scale fit gives scale {s:.4f} and max residual {res_s * 100:.2f} cm")

# ---- 2. the 5 cm rule at the boundary, through register.evaluate --------------------
EXACT = {}


def fake_depth_at(bundle, u, v, *, window_px, min_valid_fraction):
    return {"z_m": EXACT[(u, v)][2], "valid": 49, "pixels": 49, "spread_mm": 0}


def fake_back_project(k, u, v, z_m):
    return list(EXACT[(u, v)])


register.scene.depth_at = fake_depth_at
register.scene.back_project = fake_back_project
BUNDLE = {"left_frame": {"sequence": 1, "device_timestamp_seconds": 1.0},
          "depth_frame": {"sequence": 1, "device_timestamp_seconds": 1.0},
          "intrinsics": {"fx": 450.0, "fy": 450.0, "cx": 320.0, "cy": 200.0},
          "depth": [0] * (scene.WIDTH * scene.HEIGHT)}
ROW = {"n": 1, "file": "frames/000001-100.npz", "sha256": "f" * 64}
TAKE = {"take": "t1", "ended_unix": 1_800_000_100.0}


def verdict(fit_ids, check_ids, offsets):
    EXACT.clear()
    points, clicks = [], {}
    for i, pid in enumerate(list(fit_ids) + list(check_ids)):
        room = synthetic.POINTS[pid]
        EXACT[(float(i), float(i))] = synthetic.to_camera(r_true, t_true, room)
        measured = [room[k] + offsets.get(pid, [0, 0, 0])[k] for k in range(3)]
        points.append({"id": pid, "role": "fit" if pid in fit_ids else "check", "label": "",
                       "room_xyz_m": measured, "scan_xyz_m": None})
        clicks[pid] = {"u": float(i), "v": float(i)}
    sheet = {"room_id": "den", "camera_id": "den-oak", "measured_by": "A", "points": points, "sha256": "s" * 64}
    return register.evaluate(sheet, {"frame": {"sha256": ROW["sha256"]}, "clicks": clicks}, TAKE, ROW, BUNDLE,
                             M, [], [], now=1_800_000_500.0)


for off, expect in ((0.0499, True), (0.0501, False)):
    v = verdict(("p1", "p2", "p3", "p4"), ("c1", "c2"), {"c1": [0, 0, off]})
    err = next(p["error_m"] for p in v["pairs"] if p["id"] == "c1")
    got = not v["reasons"]
    ok = got == expect
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} check at {off * 100:.2f} cm: error {err * 100:.4f} cm -> "
          f"{'registered' if got else [r['reason'] for r in v['reasons']]}")

pattern = {"p1": [0.3, -0.2, 0.1], "p2": [-0.1, 0.3, -0.2], "p3": [0.2, 0.1, 0.3], "p4": [-0.3, -0.1, 0.0]}
lo, hi = 0.0, 1.0
for _ in range(60):
    mid = (lo + hi) / 2
    v = verdict(("p1", "p2", "p3", "p4"), ("c1", "c2"), {k: [mid * x for x in d] for k, d in pattern.items()})
    if any(r["reason"] == "fit_residual_over_gate" for r in v["reasons"]):
        hi = mid
    else:
        lo = mid
vl = verdict(("p1", "p2", "p3", "p4"), ("c1", "c2"), {k: [lo * x for x in d] for k, d in pattern.items()})
vh = verdict(("p1", "p2", "p3", "p4"), ("c1", "c2"), {k: [hi * x for x in d] for k, d in pattern.items()})
max_lo = max(p["residual_m"] for p in vl["pairs"] if "residual_m" in p)
max_hi = max(p["residual_m"] for p in vh["pairs"] if "residual_m" in p)
ok = max_lo <= 0.05 < max_hi and abs(max_lo - 0.05) < 1e-9
fails += not ok
print(f"{'PASS' if ok else 'FAIL'} fit-residual gate flips at max residual {max_lo:.12f} m (passes) / "
      f"{max_hi:.12f} m (refused)")
v = verdict(("p1", "p2", "p3", "p4"), ("c1", "c2"), {k: [lo * 1.0 * x for x in d] for k, d in pattern.items()})
loo = v.get("fit", {}).get("loo_max_m")
print(f"INFO at that flip point the leave-one-out max is {loo * 100:.1f} cm and error_bound_m "
      f"{v['fit']['error_bound_m'] * 100:.1f} cm, yet the verdict is "
      f"{'registered' if not v['reasons'] else v['reasons']}")

# ---- 3. collinearity -------------------------------------------------------------------
def closed_form_spread(h, base=3.0):
    # points (0,0,0), (base,0,0), (base/2,h,0): second principal RMS spread
    ys = [0.0, 0.0, h]
    my = sum(ys) / 3
    xs = [0.0, base, base / 2]
    mx = sum(xs) / 3
    sxx = sum((x - mx) ** 2 for x in xs) / 3
    syy = sum((y - my) ** 2 for y in ys) / 3
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / 3
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    lam2 = tr / 2 - math.sqrt(max(0.0, tr * tr / 4 - det))
    return math.sqrt(lam2)


worst = max(abs(rigid.spread([[0, 0, 0], [3, 0, 0], [1.5, h, 0]]) - closed_form_spread(h)) for h in
            (0.01, 0.1, 0.3, 0.53, 0.6, 1.0, 2.0))
ok = worst < 1e-9
fails += not ok
print(f"{'PASS' if ok else 'FAIL'} rigid.spread vs closed form: max diff {worst:.2e} m")
h_at = 0.25 * math.sqrt(4.5)  # syy = 2 h^2 / 9 = 0.0625 -> h = 0.5303 m
for h, expect in ((h_at * 0.996, False), (h_at * 1.004, True)):
    s_ = rigid.spread([[0, 0, 0], [3, 0, 0], [1.5, h, 0]])
    ok = (s_ >= M["min_spread_m"]) == expect
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} three fit points 3 m apart with a {h * 100:.1f} cm apex: spread "
          f"{s_ * 100:.2f} cm -> {'passes' if s_ >= M['min_spread_m'] else 'collinear'}")

# ---- 4. transform_id ------------------------------------------------------------------
v = verdict(("p1", "p2", "p3", "p4"), ("c1", "c2"), {})
tid = v["write"]["transform_id"]
rev = v["write"]["revision"]
ok = re.fullmatch(r"den-oak@[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+", tid) is not None
ok &= records.resolve([rev], tid)["entry"]["version"] == tid.split("@")[1]
ok &= records.parse_transform_id("den-oak@2026-10-18") == ("den-oak", "2026-10-18")
for bad in ("den-oak", "den-oak@", "@x", "a@b@c", "Den-Oak@2026-11-10.1", "den-oak@2026-11-10 1"):
    try:
        records.parse_transform_id(bad)
        ok = False
        print("FAIL accepted", bad)
    except scene.Refused:
        pass
fails += not ok
print(f"{'PASS' if ok else 'FAIL'} transform_id written {tid}; resolves; schema example parses; bad forms refused")
print(f"RESULT {'FAIL' if fails else 'PASS'} ({fails} failures)")

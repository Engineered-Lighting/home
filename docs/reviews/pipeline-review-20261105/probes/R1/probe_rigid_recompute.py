#!/usr/bin/env python3
"""Independent recompute of den-register's rigid fit, residuals, spread and 5 cm gate.

Checks, against an independent standard-library implementation (SVD-based
Kabsch/Umeyama via a one-sided Jacobi SVD written here, not the tool's Horn
quaternion eigenproblem):
  1. rigid.kabsch R, t on constructed points with a known pose, with and without
     noise, 3..10 pairs, coplanar points, mirrored input and near-180 deg turns;
  2. rigid.fit residuals, rms, max and leave-one-out errors;
  3. Umeyama with scale on the tool's own synthetic scene (scale must be ~1);
  4. rigid.spread (second principal RMS spread) vs a closed-form 3x3 eigen solve,
     including near-collinear sets either side of the proposed 0.25 m floor;
  5. the 5 cm rule end to end through register.run_fit: a check error just
     under / at / just over 5 cm (4.99 / 5.01 cm) flips the verdict exactly at
     error <= gate_m;
  6. transform_id format <camera_id>@YYYY-MM-DD.N and that it resolves to one entry.

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B probe_rigid_recompute.py <root of the rung-1-register checkout>
"""
import io
import json
import math
import random
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import rigid  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import method  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)


# ---------------- independent linear algebra (stdlib) ----------------

def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def transpose(a):
    return [list(r) for r in zip(*a)]


def det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def svd3(m):
    """One-sided (Hestenes) Jacobi SVD of a 3x3 matrix: U, s (desc), V with m = U diag(s) V^T."""
    a = [list(map(float, r)) for r in m]
    v = [[float(i == j) for j in range(3)] for i in range(3)]
    for _ in range(60):
        rotated = False
        for i in range(2):
            for j in range(i + 1, 3):
                alpha = sum(a[k][i] ** 2 for k in range(3))
                beta = sum(a[k][j] ** 2 for k in range(3))
                gamma = sum(a[k][i] * a[k][j] for k in range(3))
                if abs(gamma) <= 1e-15 * math.sqrt(alpha * beta) or gamma == 0.0:
                    continue
                rotated = True
                zeta = (beta - alpha) / (2 * gamma)
                t = math.copysign(1.0, zeta) / (abs(zeta) + math.sqrt(1 + zeta * zeta))
                c = 1 / math.sqrt(1 + t * t)
                s = c * t
                for k in range(3):
                    aki, akj = a[k][i], a[k][j]
                    a[k][i], a[k][j] = c * aki - s * akj, s * aki + c * akj
                    vki, vkj = v[k][i], v[k][j]
                    v[k][i], v[k][j] = c * vki - s * vkj, s * vki + c * vkj
        if not rotated:
            break
    norms = [math.sqrt(sum(a[k][i] ** 2 for k in range(3))) for i in range(3)]
    order = sorted(range(3), key=lambda i: -norms[i])
    s = [norms[i] for i in order]
    vcols = [[v[k][i] for k in range(3)] for i in order]
    ucols = []
    for idx, i in enumerate(order):
        if norms[i] > 1e-12 * max(1.0, s[0]):
            ucols.append([a[k][i] / norms[i] for k in range(3)])
        else:
            ucols.append(None)
    # complete U for rank-deficient input
    if ucols[2] is None and ucols[1] is not None:
        ucols[2] = cross(ucols[0], ucols[1])
    if ucols[1] is None:
        raise ValueError("rank < 2: rotation not determined")
    U = transpose(ucols)
    V = transpose(vcols)
    return U, s, V


def my_umeyama(src, dst, with_scale=False):
    n = len(src)
    cs = [sum(p[i] for p in src) / n for i in range(3)]
    cd = [sum(p[i] for p in dst) / n for i in range(3)]
    # covariance  Sigma = 1/n sum (d - cd)(s - cs)^T   (Umeyama 1991, eq. 38)
    sig = [[sum((q[i] - cd[i]) * (p[j] - cs[j]) for p, q in zip(src, dst)) / n for j in range(3)] for i in range(3)]
    U, s, V = svd3(sig)
    d = 1.0 if det3(U) * det3(V) > 0 else -1.0
    D = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, d]]
    R = matmul(matmul(U, D), transpose(V))
    var_s = sum(sum((p[i] - cs[i]) ** 2 for i in range(3)) for p in src) / n
    c = (s[0] + s[1] + d * s[2]) / var_s if with_scale else 1.0
    t = [cd[i] - c * sum(R[i][j] * cs[j] for j in range(3)) for i in range(3)]
    return R, t, c


def apply(R, t, p, c=1.0):
    return [c * sum(R[i][j] * p[j] for j in range(3)) + t[i] for i in range(3)]


def my_fit(src, dst):
    R, t, _ = my_umeyama(src, dst)
    res = [math.dist(apply(R, t, p), q) for p, q in zip(src, dst)]
    loo = None
    if len(src) >= 4:
        loo = []
        for k in range(len(src)):
            keep = [i for i in range(len(src)) if i != k]
            r2, t2, _ = my_umeyama([src[i] for i in keep], [dst[i] for i in keep])
            loo.append(math.dist(apply(r2, t2, src[k]), dst[k]))
    return R, t, res, loo


def eig3_sym(a):
    """Closed-form (trigonometric) eigenvalues of a symmetric 3x3, descending."""
    p1 = a[0][1] ** 2 + a[0][2] ** 2 + a[1][2] ** 2
    q = (a[0][0] + a[1][1] + a[2][2]) / 3
    if p1 == 0:
        return sorted([a[0][0], a[1][1], a[2][2]], reverse=True)
    p2 = (a[0][0] - q) ** 2 + (a[1][1] - q) ** 2 + (a[2][2] - q) ** 2 + 2 * p1
    p = math.sqrt(p2 / 6)
    b = [[(a[i][j] - (q if i == j else 0)) / p for j in range(3)] for i in range(3)]
    r = max(-1.0, min(1.0, det3(b) / 2))
    phi = math.acos(r) / 3
    e1 = q + 2 * p * math.cos(phi)
    e3 = q + 2 * p * math.cos(phi + 2 * math.pi / 3)
    e2 = 3 * q - e1 - e3
    return sorted([e1, e2, e3], reverse=True)


def my_spread(points):
    n = len(points)
    c = [sum(p[i] for p in points) / n for i in range(3)]
    cov = [[sum((p[i] - c[i]) * (p[j] - c[j]) for p in points) / n for j in range(3)] for i in range(3)]
    return math.sqrt(max(0.0, eig3_sym(cov)[1]))


def rot_axis_angle(axis, angle):
    x, y, z = axis
    nrm = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / nrm, y / nrm, z / nrm
    c, s, C = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]


def maxdiff(a, b):
    fa = [v for r in a for v in (r if isinstance(r, list) else [r])]
    fb = [v for r in b for v in (r if isinstance(r, list) else [r])]
    return max(abs(x - y) for x, y in zip(fa, fb))


# ---------------- 1-2: kabsch, residuals, LOO ----------------
rng = random.Random(20261105)
worst = {"R": 0.0, "t": 0.0, "res": 0.0, "loo": 0.0, "pose_err": 0.0}
cases = 0
for trial in range(300):
    n = [3, 4, 5, 6, 10][trial % 5]
    axis = [rng.uniform(-1, 1) for _ in range(3)]
    angle = rng.choice([rng.uniform(-math.pi, math.pi), math.pi - 1e-6, math.pi, 1e-9])
    Rk = rot_axis_angle(axis, angle)
    tk = [rng.uniform(-3, 3) for _ in range(3)]
    coplanar = trial % 7 == 0
    src = [[rng.uniform(-1.5, 1.5), rng.uniform(-1, 1), 0.0 if coplanar else rng.uniform(1, 4)] for _ in range(n)]
    noise = [0.0, 0.002, 0.02][trial % 3]
    dst = [[v + rng.gauss(0, noise) for v in apply(Rk, tk, p)] for p in src]
    if my_spread(dst) < 0.05:
        continue
    cases += 1
    tr, tt = rigid.kabsch(src, dst)
    mr, mt, mres, mloo = my_fit(src, dst)
    tf = rigid.fit(src, dst)
    worst["R"] = max(worst["R"], maxdiff(tr, mr))
    worst["t"] = max(worst["t"], maxdiff([tt], [mt]))
    worst["res"] = max(worst["res"], max(abs(a - b) for a, b in zip(tf["residuals_m"], mres)))
    if mloo is not None:
        worst["loo"] = max(worst["loo"], max(abs(a - b) for a, b in zip(tf["loo_m"], mloo)))
    else:
        check(f"loo is None with {n} pairs", tf["loo_m"] is None)
    if noise == 0.0:
        worst["pose_err"] = max(worst["pose_err"], maxdiff(tr, Rk), maxdiff([tt], [tk]))
    rms = math.sqrt(sum(r * r for r in mres) / len(mres))
    if abs(rms - tf["rms_m"]) > 1e-9 or abs(max(mres) - tf["max_m"]) > 1e-9:
        check(f"rms/max trial {trial}", False, f"{rms} vs {tf['rms_m']}")
print(f"RESULT kabsch over {cases} cases: max |R diff| {worst['R']:.2e}, |t diff| {worst['t']:.2e}, "
      f"residual diff {worst['res']:.2e}, loo diff {worst['loo']:.2e}, noiseless pose error {worst['pose_err']:.2e}")
check("kabsch R/t equal the independent SVD-Kabsch (<1e-8)", worst["R"] < 1e-8 and worst["t"] < 1e-8)
check("residuals and LOO equal the independent recompute (<1e-8)", worst["res"] < 1e-8 and worst["loo"] < 1e-8)
check("noiseless known pose recovered (<1e-8)", worst["pose_err"] < 1e-8)

# mirrored input: tool must return a proper rotation equal to the reflection-corrected optimum
src = [[0, 0, 1], [1, 0, 1], [0, 1, 1], [0, 0, 2], [0.7, 0.3, 2.5]]
dst = [[x, -y, z] for x, y, z in src]
tr, tt = rigid.kabsch(src, dst)
mr, mt, _ = my_umeyama(src, dst)
check("mirrored input: proper rotation, det +1", abs(det3(tr) - 1) < 1e-9)
cost = lambda R, t: sum(math.dist(apply(R, t, p), q) ** 2 for p, q in zip(src, dst))
check("mirrored input: same optimal cost as reflection-corrected SVD", abs(cost(tr, tt) - cost(mr, mt)) < 1e-9,
      f"{cost(tr, tt):.9f} vs {cost(mr, mt):.9f}")

# ---------------- 3: Umeyama with scale on the tool's own synthetic scene ----------------
Rs, ts = synthetic.camera_pose()
cam = {k: synthetic.to_camera(Rs, ts, v) for k, v in synthetic.POINTS.items()}
ids = list(synthetic.POINTS)
R1, t1, c1 = my_umeyama([cam[k] for k in ids], [synthetic.POINTS[k] for k in ids], with_scale=True)
check("Umeyama scale on the synthetic scene is 1 (metric depth)", abs(c1 - 1) < 1e-9, f"scale {c1:.12f}")
# and with a 2% depth scale error the scaled fit sees it while the rigid fit's residuals grow
cam2 = {k: [1.02 * v for v in p] for k, p in cam.items()}
_, _, c2 = my_umeyama([cam2[k] for k in ids], [synthetic.POINTS[k] for k in ids], with_scale=True)
rf = rigid.fit([cam2[k] for k in ids], [synthetic.POINTS[k] for k in ids])
print(f"RESULT 2% depth-scale error: Umeyama scale {c2:.5f}; rigid max residual {rf['max_m']*100:.2f} cm")
check("Umeyama recovers a 2% depth-scale error", abs(c2 - 1 / 1.02) < 1e-9)

# ---------------- 4: spread and near-collinear detection ----------------
spread_worst = 0.0
for trial in range(200):
    n = rng.choice([3, 4, 5, 6])
    pts = [[rng.uniform(0, 4), rng.uniform(0, 3.7), rng.uniform(0, 2.4)] for _ in range(n)]
    spread_worst = max(spread_worst, abs(rigid.spread(pts) - my_spread(pts)))
check("spread equals closed-form 3x3 eigen recompute (<1e-9)", spread_worst < 1e-9, f"max diff {spread_worst:.2e}")


def line_with_offset(h):
    """Four points on a 2 m line plus offsets whose second-principal RMS spread is exactly h."""
    # points at x = -1, -1/3, 1/3, 1 along a line; offsets +-a in y with zero mean and orthogonal to x
    xs = [-1.0, -1 / 3, 1 / 3, 1.0]
    ys = [1.0, -1.0, -1.0, 1.0]  # orthogonal to xs, mean 0 -> covariance is diagonal
    a = h  # RMS of ys*a is a
    return [[2.0 + x, 1.5 + a * y, 0.3] for x, y in zip(xs, ys)]


for h in (0.0, 0.001, 0.2499, 0.25, 0.2501):
    pts = line_with_offset(h)
    got = rigid.spread(pts)
    mine = my_spread(pts)
    check(f"near-collinear spread h={h}: tool {got:.6f} vs independent {mine:.6f} vs constructed {h}",
          abs(got - h) < 1e-7 and abs(mine - h) < 1e-7)

# end to end: the collinear refusal at the 0.25 m floor uses '<' (0.25 exactly passes)
print("RESULT collinear rule is 'spread < min_spread_m' (register.py evaluate): 0.2499 refused, 0.25 and 0.2501 pass")


# ---------------- 5: the 5 cm rule end to end ----------------
def closed_method():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for a probe",
                  text, flags=re.M)
    return method.parse(text)


def run_case(tmp, name, offsets, fit=synthetic.FIT, checks=synthetic.CHECKS):
    folder = Path(tmp) / name
    folder.mkdir()
    depth, left, seen = synthetic.render(synthetic.POINTS)
    data = synthetic.bundle(synthetic.metadata(), depth, left)
    take = synthetic.write_take(folder / "take", [data] * 3)
    work = folder / "work"
    work.mkdir()
    sheet = synthetic.worksheet(synthetic.POINTS, fit, checks, offsets)
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    m = closed_method()
    sh = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sh, m)
    for pid, (u, v, _) in seen.items():
        session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    session.done()
    code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
    return code, report, work


with tempfile.TemporaryDirectory() as tmp:
    # c1's error is offset plus a fixed depth-quantisation term; solve for offsets that put the
    # error at 4.99 / 4.999 / 5.001 / 5.01 cm (secant on the tool's own reported error), then check
    # that the verdict flips exactly where error_m crosses gate_m (<= passes).
    def err_at(off, tag):
        code, report, _ = run_case(tmp, tag, {"c1": [0, 0, off]})
        err = next(p["error_m"] for p in report["pairs"] if p["id"] == "c1")
        others = [p.get("error_m", p.get("residual_m", 0)) for p in report["pairs"] if p["id"] != "c1"]
        return err, report["result"], max(others)
    count = [0]
    def solve(target):
        a, b = 0.049, 0.052
        ea, eb = err_at(a, f"s{count[0]}a")[0], err_at(b, f"s{count[0]}b")[0]
        for _ in range(8):
            count[0] += 1
            c = a + (target - ea) * (b - a) / (eb - ea)
            ec = err_at(c, f"s{count[0]}c")[0]
            a, ea, b, eb = b, eb, c, ec
            if abs(ec - target) < 2e-7:
                break
        return b
    rows = []
    for target in (0.0499, 0.04999, 0.05001, 0.0501):
        off = solve(target)
        count[0] += 1
        err, verdict, others = err_at(off, f"t{count[0]}")
        expected = "registered" if max(err, others) <= 0.05 else "refused"
        rows.append((round(off, 7), round(err, 7), verdict))
        check(f"check error {err*100:.4f} cm (target {target*100:.3f}) -> {verdict}", verdict == expected)
    print("RESULT boundary rows (c1 offset_m, c1 error_m, verdict):", rows)
    check("4.99 cm registers and 5.01 cm is refused", rows[0][2] == "registered" and rows[-1][2] == "refused")

    # ---------------- 6: transform_id ----------------
    code, report, work = run_case(tmp, "tid", {})
    tid = report["transform_id"]
    check("transform_id is <camera_id>@YYYY-MM-DD.N", bool(re.fullmatch(r"den-oak@\d{4}-\d{2}-\d{2}\.\d+", tid or "")),
          str(tid))
    models = records.read_jsonl(work / "room-model.jsonl", "x")
    found = records.resolve(models, tid)
    check("transform_id resolves to the one written entry",
          found["entry"]["version"] == tid.split("@")[1] and found["invalidated"] is None)
    example = "den-oak@2026-10-18"
    check("schema example transform_id parses", records.parse_transform_id(example) == ("den-oak", "2026-10-18"))

print("RESULT", "FAIL" if FAILS else "PASS", f"({len(FAILS)} failing checks)")
sys.exit(1 if FAILS else 0)

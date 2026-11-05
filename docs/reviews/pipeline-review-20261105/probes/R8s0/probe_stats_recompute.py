#!/usr/bin/env python3
"""Independent recomputation of rung 8 Stage 0's key statistics (den-light).

    python3 probe_stats_recompute.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. The probe imports meter.py and
sweep.py from <root>/tools/den-light and compares their functions against this
file's own implementations on small constructed cases (synthetic data only,
no network, temp dirs only):

  1. k_L round trip (k_L = rho*E/(pi*C)): hand only; Stage 0 has no k_L code.
  2. relative error median / p90 (linear-interpolation percentiles): hand only;
     Stage 1 (model.py) is not built.
  3. per-channel linear basis (bulb alone minus all-off) superposition: hand
     only; Stage 1 is not built.
  4. NV12 BT.601 limited-range decode vs a float reference.
  5. meter-norm/v1 value and its parameters sha256.
  6. residual RMS of F(X*E) - z.
  7. region-test least-squares scale s = sum q / sum q^2 and max |s q - 1|.
  8. the response fit on noise-free data from a known curve.
  9. the exposure plan (11 steps 1/16..2x, time capped, ISO takes over).
Prints one RESULT line per statistic and PASS/FAIL per comparison.
"""
import hashlib
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
sys.path.insert(0, str(ROOT / "tools/den-light"))
import meter  # noqa: E402
import sweep  # noqa: E402

FAILS = []


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name + ": " + detail)
    if not ok:
        FAILS.append(name)


# 1. k_L round trip ------------------------------------------------------------------
C, rho, E = 100.0, 0.18, 174.533
k_L = rho * E / (math.pi * C)
L = k_L * C
E_back = math.pi * L / rho
print(f"RESULT k_L={k_L:.6f} (cd/m2)/nc, L={L:.4f} cd/m2, E_back={E_back:.3f} lx  (tool: none in Stage 0)")
check("k_L round trip", abs(k_L - 0.100) < 5e-6 and abs(E_back - 174.533) < 1e-9 and abs(L - 10.0) < 5e-5,
      f"k_L {k_L:.7f}, L {L:.5f}, E back {E_back:.6f}")


# 2. relative error median / p90 ---------------------------------------------------------
def pct(values, p):
    v = sorted(values)
    pos = (len(v) - 1) * p / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


# sorted errors 0.01 0.025 0.04 0.04 0.05 0.1 0.1 0.1 0.2 0.25: median at 4.5 -> 0.075;
# p90 at 8.1 -> 0.2 + 0.1 * 0.05 = 0.205
E_obs = [10.0, 20.0, 40.0, 5.0, 8.0, 12.0, 30.0, 25.0, 50.0, 2.0]
E_hat = [10.5, 18.0, 44.0, 5.2, 9.6, 12.3, 27.0, 26.0, 50.5, 2.5]
rel = [abs(h - e) / e for h, e in zip(E_hat, E_obs)]
med, p90 = pct(rel, 50), pct(rel, 90)
print(f"RESULT relative errors {[round(r, 4) for r in rel]}: median {med:.4f}, p90 {p90:.4f}; "
      f"gate (median<=0.08 and p90<=0.19) -> {'pass' if med <= 0.08 and p90 <= 0.19 else 'fail'}  (tool: none)")
check("median/p90 hand values", abs(med - 0.075) < 1e-12 and abs(p90 - 0.205) < 1e-12, f"{med:.6f}, {p90:.6f}")

# 3. per-channel linear basis ----------------------------------------------------------------
all_off = [3.0, 1.0, 0.5]
alone = {"c1": [13.0, 6.0, 2.5], "c2": [7.0, 9.0, 4.5]}
basis = {c: [a - o for a, o in zip(v, all_off)] for c, v in alone.items()}
g = {"c1": 0.5, "c2": 0.25}
pred = [sum(g[c] * basis[c][b] for c in basis) for b in range(3)]
mixed = [o + p for o, p in zip(all_off, pred)]  # a perfectly additive scene
check("basis superposition", all(abs((m - o) - p) < 1e-12 for m, o, p in zip(mixed, all_off, pred)),
      f"basis {basis}, predicted E-hat {pred}  (tool: none, Stage 1 not built)")


# 4. NV12 decode --------------------------------------------------------------------------------
def ref_decode(Y, U, V):
    y, u, v = 1.164383 * (Y - 16), U - 128, V - 128
    return tuple(min(255, max(0, round(c))) for c in (y + 1.596027 * v, y - 0.391762 * u - 0.812968 * v,
                                                    y + 2.017232 * u))


rng = random.Random(3)
nv = bytearray(meter.NV12_BYTES)
expect = []
step = 8
for y in range(0, 360, step):
    for x in range(0, 640, step):
        Y, U, V = rng.randrange(16, 236), rng.randrange(16, 241), rng.randrange(16, 241)
        nv[y * 640 + x] = Y
        at = 640 * 360 + (y // 2) * 640 + (x // 2) * 2
        nv[at], nv[at + 1] = U, V
        expect.append(ref_decode(Y, U, V))
r, gg, b = meter.decode(bytes(nv), step)
worst = max(max(abs(r[i] - e[0]), abs(gg[i] - e[1]), abs(b[i] - e[2])) for i, e in enumerate(expect))
check("NV12 decode vs float BT.601", worst <= 1, f"largest difference {worst} code over {len(expect)} pixels")

# 5. meter-norm/v1 value and parameters sha256 -----------------------------------------------------
method = meter.load_method()
curves = {c: [(z / 128) ** (1.0 + 0.1 * i) for z in range(256)] for i, c in enumerate("rgb")}
params, digest = meter.meter_norm_parameters(method, curves)
mine = {"version": "meter-norm/v1", "white_balance_kelvin": method["white_balance_kelvin"],
        "clip_limits": [method["clip_limit_r"], method["clip_limit_g"], method["clip_limit_b"]],
        "weights": [method["weight_r"], method["weight_g"], method["weight_b"]],
        "normalise_code": method["normalise_code"], "decode": "nv12_bt601_limited_range_server_integer",
        "curves": {c: [round(v, 9) for v in curves[c]] for c in "rgb"}}
my_digest = hashlib.sha256(json.dumps(mine, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
check("meter-norm parameters sha256", my_digest == digest, digest[:16])
for (R, G, B, t, gain) in [(128, 128, 128, 10.0, 1.0), (200, 60, 30, 4.0, 2.0), (16, 249, 100, 33.0, 16.0)]:
    want = (0.2126 * curves["r"][R] + 0.7152 * curves["g"][G] + 0.0722 * curves["b"][B]) / (t * gain)
    got = meter.meter_norm_value(R, G, B, t, gain, params)
    check(f"meter-norm value {R, G, B, t, gain}", got is not None and abs(got - want) < 1e-12, f"{got} vs {want}")
check("meter-norm flags at the limit", meter.meter_norm_value(100, 250, 100, 1, 1, params) is None, "G = 250")

# 6. residual RMS -----------------------------------------------------------------------------------
curve = [(z / 128) ** 2.0 for z in range(256)]
samples = meter.Samples(3)
data = [(0, 1.0, 100), (0, 2.0, 140), (1, 0.5, 60), (2, 4.0, 220), (2, 1.0, 120)]
for p, x, z in data:
    samples.pix.append(p)
    samples.xs.append(x)
    for c in range(3):
        samples.zs[c].append(z)
energy = [0.6, 0.4, 0.2]


def my_inverse(y):  # code where (z/128)^2 = y, clamped; exact for this curve
    z = 128 * math.sqrt(max(y, 0.0))
    return min(255.0, max(0.0, z))


errs = [my_inverse(x * energy[p]) - z for p, x, z in data]
want = math.sqrt(sum(e * e for e in errs) / len(errs))
got = meter.residual_rms(meter.Fit(curve, energy, [0] * 256), samples, 0)
print(f"RESULT residual RMS hand {want:.4f} codes, tool {got:.4f} codes")
check("residual RMS", abs(got - want) < 0.02, f"{got:.4f} vs {want:.4f} (piecewise-linear inverse vs exact sqrt)")

# 7. region scale -------------------------------------------------------------------------------------
glob = [0.0] + [z / 128 for z in range(1, 256)]
region_curve = [v * (1.3 + 0.0005 * z) for z, v in enumerate(glob)]
fit = meter.Fit(region_curve, [], [50] * 256)
q = [region_curve[z] / glob[z] for z in range(16, 236)]
s = sum(q) / sum(v * v for v in q)
dev = max(abs(s * v - 1) for v in q)
codes, scale, deviation = meter.compare_region(fit, glob, method)
print(f"RESULT region scale hand {s:.6f}, max dev {dev:.6f}; tool {scale:.6f}, {deviation:.6f}, codes {codes}")
check("region scale and deviation", codes == 220 and abs(scale - s) < 1e-12 and abs(deviation - dev) < 1e-12,
      f"s {scale:.6f}, dev {100 * deviation:.3f}%")

# 8. response fit, noise free ----------------------------------------------------------------------------
m = meter.Method(dict(method.values, fit_iterations=30), (), "", "")
n = 3000
rng = random.Random(9)
E_true = [math.exp(rng.uniform(math.log(0.02), math.log(12))) for _ in range(n)]
gamma = 1 / 2.2
F = lambda y: min(255, max(0, round(255 * min(1.0, y) ** gamma)))  # camera: code = 255 * y^(1/2.2)
samples = meter.Samples(n)
for k in range(11):
    x = 2 ** (-4 + 0.5 * k)
    codes_ = [F(x * e / 8) for e in E_true]
    samples.add_frame((codes_, codes_, codes_), x, (250, 250, 250))
fitted = meter.fit_response(samples, 0, m)
truth = lambda z: (z / 255) ** 2.2 / (128 / 255) ** 2.2
oracle_sum, oracle_n = [0.0] * 256, [0] * 256
for p, x, z in zip(samples.pix, samples.xs, samples.zs[0]):
    oracle_sum[z] += x * E_true[p]
    oracle_n[z] += 1
oracle = [oracle_sum[z] / oracle_n[z] if oracle_n[z] else None for z in range(256)]
o128 = oracle[128]
supported = [z for z in range(16, 236) if fitted.counts[z] >= 20]
err_truth = max(abs(fitted.curve[z] / truth(z) - 1) for z in supported)
err_oracle = max(abs(fitted.curve[z] / (oracle[z] / o128) - 1) for z in supported if oracle[z])
print(f"RESULT response fit: {len(supported)} supported codes, max rel. error vs truth {100 * err_truth:.2f}%, "
      f"vs per-code oracle {100 * err_oracle:.2f}%")
check("response fit vs truth (codes 16..235 with >= 20 samples)", err_truth < 0.03,
      f"{100 * err_truth:.2f}% (8-bit quantisation alone gives a few % at low codes)")
check("response fit vs oracle", err_oracle < 0.03, f"{100 * err_oracle:.2f}%")

# 9. exposure plan --------------------------------------------------------------------------------------
for t_pick, iso in [(8000, 200), (30000, 100), (33000, 800)]:
    plan = sweep.exposure_plan(t_pick, iso, method)
    mine = []
    for k in range(11):
        t = t_pick * (1 / 16) * 32 ** (k / 10)
        if round(t) > 33000:
            mine.append({"exposure_us": 33000, "iso": round(iso * t / 33000), "white_balance_kelvin": 4000})
        else:
            mine.append({"exposure_us": round(t), "iso": iso, "white_balance_kelvin": 4000})
    mine.append(dict(mine[0]))
    check(f"exposure plan t_pick={t_pick} iso={iso}", plan == mine,
          f"steps {[p['exposure_us'] for p in plan]} iso {[p['iso'] for p in plan]}")

print("\nRESULT " + ("all comparisons match" if not FAILS else "MISMATCHES: " + ", ".join(FAILS)))

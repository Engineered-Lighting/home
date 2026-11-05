#!/usr/bin/env python3
"""(validate lens) Independent recomputation of rung 7's key statistics against den-photometry's functions.

What it checks (own implementation, not the tool's code; synthetic data only, no network):
  1. the known-card round trip: k_L = rho*E/(pi*C) and E = pi*k_L*C/rho, with Decimal arithmetic,
     against card.fit_k_L / luminance / illuminance / round_trip;
  2. E_hat(s) = pi*k_L*C(s)/rho_s on constructed surfaces, against card.illuminance and
     card.light_state_record (anchored lux, unanchored relative);
  3. exposure invariance on constructed NV12 frames (own BT.601 limited-range decoder, a
     non-linear test curve, two-level checkerboard boxes, frame-to-frame noise):
     C_h, sem_h, C, u_C, d, u_d against card.box_hold + card.box_reading; the 3% boundary;
  4. the held-out check in lux at the tolerance boundary (|e| = tol, tol +/- small), and
     u(e_j), against card.held_out_check;
  5. meter reading mean / drift boundary / u_rel and the reflectance inverse-variance mean.

Run:
    python3 probe_stats_recompute_validate.py <root of the branch checkout>
Prints one PASS/FAIL line per check and a RESULT line.
"""
import math
import statistics
import sys
from decimal import Decimal, getcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402

getcontext().prec = 50
PI = Decimal("3.14159265358979323846264338327950288419716939937510")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)


def method(**changes):
    values = dict(commissioning_option="card-and-meter", k_L_scope="single", meter_tolerance_lx=10,
                  meter_class="test-meter")
    values.update(changes)
    return card.load_method(ROOT / "tools" / "den-photometry" / "METHOD-V1.md").closed(**values)


M = method()

# ---- 1. round trip, exact ------------------------------------------------------------------
C, rho, E = Decimal(100), Decimal("0.18"), Decimal("174.533")
k_exact = rho * E / (PI * C)
L_exact = k_exact * C
E_back_exact = PI * L_exact / rho
k_tool = card.fit_k_L(100, 0.18, 174.533)
L_tool = card.luminance(k_tool, 100)
E_tool = card.illuminance(k_tool, 100, 0.18)
check("round trip k_L matches exact value to 1e-15", abs(Decimal(repr(k_tool)) - k_exact) < Decimal("1e-15"),
      f"tool {k_tool!r} exact {k_exact:.18f}")
check("round trip k_L is 0.100 at 3 decimals", round(float(k_exact), 3) == 0.100 and round(k_tool, 3) == 0.100)
check("round trip L is 10.0 at 1 decimal", round(float(L_exact), 1) == 10.0 and round(L_tool, 1) == 10.0,
      f"tool {L_tool!r} exact {L_exact:.12f}")
check("round trip E back is 174.533 to 1e-9", abs(Decimal(repr(E_tool)) - E_back_exact) < Decimal("1e-9")
      and abs(E_back_exact - E) < Decimal("1e-30"), f"tool {E_tool!r}")
trip = card.round_trip(M)
check("card.round_trip result pass, versions match", trip["result"] == "pass" and trip["normalisation_version_matches"]
      and trip["normalisation_version"] == "meter-norm/v1")
check("card.round_trip fails on a frame version other than meter-norm/v1",
      card.round_trip(M, ["meter-norm/v2"])["result"] == "fail")

# ---- 2. E_hat on constructed surfaces -------------------------------------------------------
cases = [(0.1, 100, 0.18), (10.0, 5.241, 0.35), (0.0731, 42.7, 0.72), (0.25, 0.9, 0.05), (1.3e-3, 2.4e4, 0.9)]
worst = 0.0
for k, c, r in cases:
    exact = PI * Decimal(repr(k)) * Decimal(repr(c)) / Decimal(repr(r))
    tool = card.illuminance(k, c, r)
    worst = max(worst, float(abs(Decimal(repr(tool)) - exact) / exact))
check("E_hat = pi*k_L*C/rho on 5 constructed surfaces (rel err < 1e-14)", worst < 1e-14, f"worst rel {worst:.2e}")
cap = {"ts": 1793000000.0, "point": "desk", "blinds": "closed", "exposure": {"time_ms": 10.0, "gain": 1.0},
       "card": {"C_nc": 50.0, "clipped_frac": 0.0}}
state = card.light_state_record(cap, 0.1, True, M, {"version": "meter-norm/v1", "sha256": "x"}, "den-v4-p1")
est = state["surface_estimates"][0]
check("anchored LightState value is pi*k_L*C/rho_card in lx",
      est["unit"] == "lx" and abs(est["value"] - math.pi * 0.1 * 50 / 0.18) < 1e-6, f"{est}")
state = card.light_state_record(cap, 0.1, False, M, {"version": "meter-norm/v1", "sha256": "x"}, "den-v4-p1")
est = state["surface_estimates"][0]
check("unanchored LightState value is pi*C/rho_card, unit relative",
      est["unit"] == "relative" and abs(est["value"] - math.pi * 50 / 0.18) < 1e-6, f"{est}")

# ---- 3. exposure invariance on constructed frames ------------------------------------------------
W, H = 640, 360


def my_decode_grey(Y):
    """BT.601 limited range, integer form, with U = V = 128: R = G = B = (298*(Y-16) + 128) >> 8, clamped."""
    v = (298 * (Y - 16) + 128) >> 8
    return min(255, max(0, v))


def my_nv12(y_of_xy):
    out = bytearray(W * H)
    for y in range(H):
        for x in range(W):
            out[y * W + x] = y_of_xy(x, y)
    out += bytes([128]) * (W * H // 2)
    return bytes(out)


CURVE = [((z / 128.0) ** 2.2) if z else 0.0 for z in range(256)]   # a non-linear inverse response
WEIGHTS = [0.2126, 0.7152, 0.0722]
PARAMS = {"clip_limits": [250, 250, 250], "curves": {c: CURVE for c in "rgb"}, "weights": WEIGHTS}
BOX = (300, 150, 339, 189)


def frame_of(y_card_a, y_card_b, exposure_us, iso):
    """Card box pixels alternate between two Y values (a checkerboard); background Y 40."""
    def y_of(x, y):
        if BOX[0] <= x <= BOX[2] and BOX[1] <= y <= BOX[3]:
            return y_card_a if (x + y) % 2 == 0 else y_card_b
        return 40
    nv12 = my_nv12(y_of)
    rgb = card.meter.decode(nv12, 1)
    return {"report": {"exposure_us": float(exposure_us), "iso": iso, "kelvin": 4000}, "rgb": rgb}


def my_frame_value(ya, yb, exposure_us, iso):
    X = exposure_us / 1000.0 * iso / 100.0
    n = (BOX[2] - BOX[0] + 1) * (BOX[3] - BOX[1] + 1)
    total = 0.0
    for x in range(BOX[0], BOX[2] + 1):
        for y in range(BOX[1], BOX[3] + 1):
            z = my_decode_grey(ya if (x + y) % 2 == 0 else yb)
            total += sum(w * CURVE[z] for w in WEIGHTS) / X
    return total / n


h1_specs = [(150, 160, 10, 100), (151, 161, 10, 100)]
h2_specs = [(120, 128, 5, 100), (121, 127, 5, 100)]
h1_frames = [frame_of(a, b, t, i) for a, b, t, i in h1_specs]
h2_frames = [frame_of(a, b, t, i) for a, b, t, i in h2_specs]
c1s = [my_frame_value(a, b, t, i) for a, b, t, i in h1_specs]
c2s = [my_frame_value(a, b, t, i) for a, b, t, i in h2_specs]
C1, C2 = statistics.fmean(c1s), statistics.fmean(c2s)
s1, s2 = statistics.stdev(c1s) / math.sqrt(2), statistics.stdev(c2s) / math.sqrt(2)
Cm = (C1 + C2) / 2
uC = math.sqrt((s1 ** 2 + s2 ** 2) / 4 + (C1 - C2) ** 2 / 4)
d = 100 * abs(C1 - C2) / Cm
ud = 100 * math.sqrt(s1 ** 2 + s2 ** 2) / Cm
tool1 = card.box_hold(h1_frames, BOX, PARAMS)
tool2 = card.box_hold(h2_frames, BOX, PARAMS)
check("C_h (hold 1, hold 2) = mean of per-frame mean meter-norm values",
      abs(tool1["C_nc"] - C1) < 1e-9 * C1 and abs(tool2["C_nc"] - C2) < 1e-9 * C2,
      f"tool {tool1['C_nc']:.9f},{tool2['C_nc']:.9f} hand {C1:.9f},{C2:.9f}")
check("sem_h = stdev(c_j)/sqrt(n)", abs(tool1["sem_nc"] - s1) < 1e-9 * C1 and abs(tool2["sem_nc"] - s2) < 1e-9 * C2,
      f"tool {tool1['sem_nc']:.6g},{tool2['sem_nc']:.6g} hand {s1:.6g},{s2:.6g}")
reading = card.box_reading(tool1, tool2, True, M.replace(box_code_low=0, box_code_high=255))
check("box usable", reading["usable"], str(reading.get("problem")))
if reading["usable"]:
    check("C = (C1+C2)/2", abs(reading["C_nc"] - Cm) < 1e-9 * Cm, f"tool {reading['C_nc']:.9f} hand {Cm:.9f}")
    check("u_C = sqrt((s1^2+s2^2)/4 + (C1-C2)^2/4)", abs(reading["u_C_nc"] - uC) < 1e-9 * Cm,
          f"tool {reading['u_C_nc']:.9f} hand {uC:.9f}")
    check("d = 100|C1-C2|/C", abs(reading["invariance_pct"] - d) < 1e-9, f"tool {reading['invariance_pct']:.6f} hand {d:.6f}")
    check("u_d = 100 sqrt(s1^2+s2^2)/C", abs(reading["u_invariance_pct"] - ud) < 1e-9,
          f"tool {reading['u_invariance_pct']:.6f} hand {ud:.6f}")
    check("invariance verdict = (d <= 3%)", reading["invariance_pass"] == (d <= 3), f"d {d:.3f}%")


def hold(Cv, sem=0.0, codes=(100, 100, 100)):
    return {"C_nc": Cv, "sem_nc": sem, "frames": 2, "frames_with_value": 2, "pixels": 900, "flagged": 0,
            "pixel_frames": 1800, "mean_codes": list(codes)}


at = card.box_reading(hold(101.5), hold(98.5), True, M)          # d = 3.000 % exactly
over = card.box_reading(hold(101.5 + 1e-6), hold(98.5 - 1e-6), True, M)
check("invariance boundary: d = 3.000% passes, d = 3.000002% fails", at["invariance_pass"] and not over["invariance_pass"],
      f"at {at['invariance_pct']!r} over {over['invariance_pct']!r}")

# ---- 4. held-out lux at the boundary ---------------------------------------------------------------
fit_card = {"C_nc": 100.0, "u_C_nc": 1.0}
fit_reading = card.meter_reading(174.533, 174.533, M)
k = card.k_L_fit(fit_card, fit_reading, M)["k_L"]
held_card = {"C_nc": 50.0, "u_C_nc": 0.5}
E_hat = float(PI * Decimal(repr(k)) * Decimal(50) / Decimal("0.18"))
for label, E_j, expect in [("|e| = tol - 0.001", E_hat - 9.999, True), ("|e| = tol (meter low)", E_hat - 10.0, True),
                           ("|e| = tol (meter high)", E_hat + 10.0, True),
                           ("|e| = tol + 0.001", E_hat + 10.001, False), ("|e| = tol + 1e-6", E_hat - 10.000001, False)]:
    r_ = card.meter_reading(E_j, E_j, M)
    out = card.held_out_check(fit_card, fit_reading, k, held_card, r_, M)
    e = E_hat - E_j
    r = math.sqrt(fit_reading["u_rel"] ** 2 + (1.0 / 100) ** 2 + (0.5 / 50) ** 2)
    ue = math.sqrt((E_hat * r) ** 2 + (E_j * fit_reading["u_rel"]) ** 2)
    ok = (out["pass"] is expect and abs(out["error_lx"] - e) < 1e-9 and abs(out["E_hat_lx"] - E_hat) < 1e-9
          and abs(out["u_error_lx"] - ue) < 1e-9)
    check(f"held-out {label}: pass={expect}, e and u(e) recomputed", ok,
          f"tool e {out['error_lx']:.7f} pass {out['pass']} u {out['u_error_lx']:.6f}; hand e {e:.7f} u {ue:.6f}")
check("held-out E_hat = E_fit*C_j/C_fit (rho_card cancels)", abs(E_hat - 174.533 * 50 / 100) < 1e-9)

# ---- 5. meter reading and reflectance ------------------------------------------------------------
mr = card.meter_reading(100.0, 104.0, M)
check("meter E = mean, u_rel = sqrt(0.05^2 + ((b-a)/(2E))^2), drift 3.92% used",
      abs(mr["E_lx"] - 102) < 1e-12 and abs(mr["u_rel"] - math.hypot(0.05, 4 / 204)) < 1e-12 and mr["used"])
check("meter drift at 5% of E is used, just above is not",
      card.meter_reading(97.5, 102.5, M)["used"] and not card.meter_reading(97.5, 102.5000001, M)["used"])
pairs = [({"C_nc": 194.4444, "u_C_nc": 2.0}, {"C_nc": 100.0, "u_C_nc": 1.0}),
         ({"C_nc": 70.0, "u_C_nc": 1.0}, {"C_nc": 40.0, "u_C_nc": 0.4})]
qs = [Fraction(s["C_nc"]) / Fraction(c["C_nc"]) for s, c in pairs]
us = [float(q) * math.hypot(s["u_C_nc"] / s["C_nc"], c["u_C_nc"] / c["C_nc"]) for q, (s, c) in zip(qs, pairs)]
w = [1 / u ** 2 for u in us]
qbar = sum(wi * float(q) for wi, q in zip(w, qs)) / sum(w)
uq = 1 / math.sqrt(sum(w))
rho_s = 0.18 * qbar
rel = math.hypot(0.01 / 0.18, uq / qbar)
tool = card.reflectance(pairs, M)
check("reflectance inverse-variance q_bar, rho_s and u_rel",
      abs(tool["q"] - qbar) < 1e-12 and abs(tool["rho"] - rho_s) < 1e-12 and abs(tool["u_rel"] - rel) < 1e-12,
      f"tool rho {tool['rho']:.9f} hand {rho_s:.9f}")

print("RESULT: " + ("PASS (all statistics match)" if not FAILS else "FAIL: " + "; ".join(FAILS)))
sys.exit(1 if FAILS else 0)

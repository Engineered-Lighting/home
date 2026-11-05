#!/usr/bin/env python3
"""probe_stats_recompute: recompute rung 7's numbers by hand and compare with den-photometry's card.py.

  1. the known-card round trip: k_L = rho*E/(pi*C), L = k_L*C, E_back = pi*k_L*C/rho, on the gate's
     numbers (100 nc, 0.18, 174.533 lx), and card.round_trip's verdict;
  2. E_hat(s) = pi*k_L*C(s)/rho_s on constructed surfaces (A9's chain 5.241 nc -> 52.41 cd/m2 -> 470 lx
     at rho 0.35 with k_L 10, and others);
  3. exposure invariance on constructed frames: synthetic NV12 frames of a 100 nc card under the linear
     test curves, with a black-level pedestal of p codes added at both holds; by hand
     C1 = C + p/(128*X1), C2 = C + p/(128*X2), d = 100*|C1-C2|/((C1+C2)/2); compared with
     card.box_hold + card.box_reading through meter.decode and meter.meter_norm_value;
  4. the held-out check in lux at the boundary of a 10 lx tolerance;
  5. reflectance rho_s = rho_card * C_s/C_card.

    python3 -B probe_stats_recompute.py <branch-root>

Synthetic only; no network; nothing written.
"""
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import card  # noqa: E402
import synthetic as syn  # noqa: E402

bad = 0


def check(name, tool, hand, tol=1e-9):
    global bad
    ok = abs(tool - hand) <= tol
    bad += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} {name}: tool={tool!r} hand={hand!r}")


m = syn.photometry_method()
# 1. round trip
C, rho, E = 100.0, 0.18, 174.533
k_hand = rho * E / (math.pi * C)
check("k_L", card.fit_k_L(C, rho, E), k_hand)
check("L_card", card.luminance(k_hand, C), k_hand * C)
check("E_back", card.illuminance(k_hand, C, rho), math.pi * k_hand * C / rho)
trip = card.round_trip(m)
print(f"RESULT round_trip result={trip['result']} k_L={trip['k_L']} L={trip['L_card_cd_m2']} E_back={trip['E_back_lx']} "
      f"| hand k_L={k_hand:.9f} (|k-0.100|={abs(k_hand-0.1):.2e}) E_back={math.pi*k_hand*C/rho:.9f}")
bad += 0 if trip["result"] == "pass" else 1
trip_bad = card.round_trip(m, frame_versions=("meter-norm/v2",))
print(f"{'PASS' if trip_bad['result'] == 'fail' else 'FAIL'} a frame under another normalisation fails the round trip "
      f"({trip_bad['result']})")
bad += 0 if trip_bad["result"] == "fail" else 1

# 2. E_hat chain
for k, Cs, rs, hand in [(10.0, 5.241, 0.35, math.pi * 52.41 / 0.35), (0.1, 50.0, 0.18, math.pi * 5.0 / 0.18),
                        (0.1, 194.4444, 0.35, math.pi * 19.44444 / 0.35)]:
    check(f"E_hat(k={k}, C={Cs}, rho={rs})", card.illuminance(k, Cs, rs), hand, 1e-6)

# 3. exposure invariance on constructed frames (card 100 nc, hold 1 X=0.01, hold 2 X=0.005)
box = (300, 150, 339, 189)
inner = (305, 155, 334, 184)
params, _ = syn.linear_parameters()
for p in (0, 2, 4, 6):
    h1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}
    h2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}
    sc = {"background": 20.0, "rects": [(box, 100.0)]}
    f1, f2 = syn.held_frames(sc, h1, 2, pedestal=p), syn.held_frames(sc, h2, 2, pedestal=p)
    r = card.box_reading(card.box_hold(f1, inner, params), card.box_hold(f2, inner, params), True, m)
    # by hand, through the BT.601 round trip of grey codes as synthetic.nv12 builds them
    z1, z2 = syn.code(100.0, 0.01, p), syn.code(100.0, 0.005, p)
    def decoded(z):
        y = syn.grey_y(z)
        return min(255, max(0, (298 * (y - 16) + 128) >> 8))
    c1, c2 = decoded(z1) / 128 / 0.01, decoded(z2) / 128 / 0.005
    d_hand = 100 * abs(c1 - c2) / ((c1 + c2) / 2)
    check(f"invariance d% pedestal={p} (codes {decoded(z1)},{decoded(z2)})", r["invariance_pct"], d_hand, 1e-9)
    print(f"RESULT pedestal={p}: C={r['C_nc']:.4f} nc (true 100), d={r['invariance_pct']:.4f}% pass={r['invariance_pass']} "
          f"-> k_L bias if fitted here {100*(100/r['C_nc']-1):+.2f}%")

# 4. held-out boundary (tolerance 10 lx; fit 100 nc at 174.533 lx -> k_L 0.1; held-out card 50 nc)
fit_card = {"C_nc": 100.0, "u_C_nc": 0.0}
fit_read = card.meter_reading(174.533, 174.533, m)
kL = card.k_L_fit(fit_card, fit_read, m)["k_L"]
held_card = {"C_nc": 50.0, "u_C_nc": 0.0}
E_hat = math.pi * kL * 50.0 / 0.18
for delta, expect in [(10.0, True), (10.0 - 1e-7, True), (10.0 + 1e-6, False), (-10.0, True), (-10.0 - 1e-6, False)]:
    reading = card.meter_reading(E_hat - delta, E_hat - delta, m)
    out = card.held_out_check(fit_card, fit_read, kL, held_card, reading, m)
    ok = out["pass"] is expect
    bad += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} held-out error {out['error_lx']:+.7f} lx vs tol 10 -> pass={out['pass']} (expected {expect})")
check("held-out E_hat", out["E_hat_lx"], E_hat, 1e-9)
u_rel_E = math.sqrt((5 / 100) ** 2)
check("held-out u(e)", out["u_error_lx"], math.sqrt((E_hat * u_rel_E) ** 2 + (out["E_meter_lx"] * u_rel_E) ** 2), 1e-9)

# 5. reflectance
rr = card.reflectance([({"C_nc": 194.4444, "u_C_nc": 0.0}, {"C_nc": 100.0, "u_C_nc": 0.0})], m)
check("rho_s = rho_card*Cs/Ccard", rr["rho"], 0.18 * 1.944444, 1e-9)

print("RESULT", "FAIL" if bad else "PASS", f"({bad} mismatches)")
sys.exit(1 if bad else 0)

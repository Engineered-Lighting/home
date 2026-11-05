#!/usr/bin/env python3
"""(R7 reproduce lens) Prints the margins the den-photometry tests use around three gate limits, and shows the
behaviour a 1.5x-looser limit would let through, using the unmutated card.py.

  held-out tolerance (meter_tolerance_lx 10): the |e| values the tests assert as out of tolerance, and a
      14 lx error that a 1.5x gate (15 lx) would pass.
  exposure invariance (3%): the d of BoxTests' pedestal-8 box (the only box_reading offset test), and a box
      at about 4.4% that a 1.5x gate (4.5%) would pass.
  meter drift (5%): the drifts the tests assert as not used, and a 7% drift a 1.5x gate (7.5%) would use.

Synthetic frames from the suite's own synthetic.py; no network.
Run:
    python3 verify_boundaries.py <root of the branch checkout>
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(ROOT / "tools" / "den-photometry"), str(ROOT / "tools" / "den-light")]
import card  # noqa: E402
import synthetic as syn  # noqa: E402

m = syn.photometry_method()
PARAMETERS, _ = syn.linear_parameters()
H1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}
H2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}


def read(value, pedestal=0):
    scene = {"background": 20.0, "rects": [((300, 150, 339, 189), value)]}
    b = card.parse_box("305 155 334 184")
    h1 = card.box_hold(syn.held_frames(scene, H1, pedestal=pedestal), b, PARAMETERS)
    h2 = card.box_hold(syn.held_frames(scene, H2, pedestal=pedestal), b, PARAMETERS)
    return card.box_reading(h1, h2, True, m)


# held out
fit_card = {"C_nc": 100.0, "u_C_nc": 0.0}
fit_reading = card.meter_reading(174.533, 174.533, m)
k = card.k_L_fit(fit_card, fit_reading, m)["k_L"]
for E, where in ((110.0, "test_card BoxTests far (box 50 nc)"), (70.0, "SelectionTests / ScoreTests desk 70 lx")):
    chk = card.held_out_check(fit_card, fit_reading, k, {"C_nc": 50.0, "u_C_nc": 0.0},
                              card.meter_reading(E, E, m), m)
    print(f"held-out test value: {where}: |e| {abs(chk['error_lx']):.2f} lx (tol 10, 1.5x tol 15) "
          f"pass={chk['pass']}")
chk = card.held_out_check(fit_card, fit_reading, k, {"C_nc": 50.0, "u_C_nc": 0.0},
                          card.meter_reading(87.2665 + 14, 87.2665 + 14, m), m)
print(f"held-out |e| 14 lx: pass={chk['pass']} (a 1.5x gate would pass it)")

# invariance
for ped in (8, 6, 5, 4):
    r = read(100.0, pedestal=ped)
    print(f"invariance: pedestal {ped}: usable {r['usable']}, d {r.get('invariance_pct', float('nan')):.2f}% "
          f"pass={r.get('invariance_pass')} (tol 3%, 1.5x tol 4.5%)")

# drift
for before, after, where in ((100.0, 110.0, "test_card test_meter_reading"), (40.0, 48.0, "ScoreTests unused row")):
    r = card.meter_reading(before, after, m)
    print(f"drift test value: {where}: {r['drift_pct']:.2f}% used={r['used']} (limit 5%, 1.5x 7.5%)")
r = card.meter_reading(100.0, 107.0, m)
print(f"drift 100 -> 107 lx: {r['drift_pct']:.2f}% used={r['used']} (a 1.5x gate would use it)")

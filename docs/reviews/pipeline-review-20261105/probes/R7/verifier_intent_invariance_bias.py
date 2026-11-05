#!/usr/bin/env python3
"""verifier_intent_invariance_bias (verifier, intent lens): for finding invariance-bias-understated, takes the
finding's own case (a 100 nc card, a 4-code pedestal, hold 1 at X=0.01 and hold 2 at X=0.005, through the
branch's synthetic frames, meter.decode and card.box_hold/box_reading) and asks what the k_L record's
confidence_pct_1sigma and a production lux estimate actually do:

  1. u_k/k as card.k_L_fit computes it (with the method's rho_card_sigma and meter_sigma_percent), against
     the same u_k/k with an offset term of 1.5*d in place of the tool's |C1-C2|/2 term;
  2. the production lux error when k_L fitted from C=(C1+C2)/2 is applied to a production frame at auto's
     exposure (hold-1 conditions, same pedestal), against u_C/C.

    python3 -B verifier_intent_invariance_bias.py <branch-root>

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

m = syn.photometry_method()
params, _ = syn.linear_parameters()
box, inner = (300, 150, 339, 189), (305, 155, 334, 184)
p = 4
h1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}
h2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}
sc = {"background": 20.0, "rects": [(box, 100.0)]}
b1 = card.box_hold(syn.held_frames(sc, h1, 2, pedestal=p), inner, params)
b2 = card.box_hold(syn.held_frames(sc, h2, 2, pedestal=p), inner, params)
r = card.box_reading(b1, b2, True, m)
C1, C2, C, uC, d = b1["C_nc"], b2["C_nc"], r["C_nc"], r["u_C_nc"], r["invariance_pct"]
print(f"RESULT C1={C1:.4f} C2={C2:.4f} C={C:.4f} u_C={uC:.4f} ({100*uC/C:.2f}%) d={d:.4f}% pass={r['invariance_pass']}")

E_true = math.pi * 0.1 * 100.0 / m["rho_card"]          # the lux a true 100 nc card reads at k_L 0.1
reading = card.meter_reading(E_true, E_true, m)
fit = card.k_L_fit(r, reading, m)
print(f"RESULT k_L={fit['k_L']:.6f} (offset-free 0.100000, bias {100*(fit['k_L']/0.1-1):+.2f}%) "
      f"confidence_pct_1sigma (tool)={100*fit['u_rel']:.2f}%")
offset_term = 1.5 * d / 100
u_alt = math.sqrt((m["rho_card_sigma"] / m["rho_card"]) ** 2 + reading["u_rel"] ** 2
                  + (math.sqrt((b1["sem_nc"] ** 2 + b2["sem_nc"] ** 2) / 4) / C) ** 2 + offset_term ** 2)
print(f"RESULT confidence_pct_1sigma with a 1.5*d offset term instead: {100*u_alt:.2f}% "
      f"(ratio {u_alt/fit['u_rel']:.2f}x, not ~3x)")

# Production: a frame at auto's exposure (hold-1 conditions) of the same scene, same pedestal.
prod = card.box_hold(syn.held_frames(sc, h1, 1, pedestal=p), inner, params)
E_hat = card.illuminance(fit["k_L"], prod["C_nc"], m["rho_card"])
err = 100 * (E_hat / E_true - 1)
print(f"RESULT production C={prod['C_nc']:.4f} nc -> E_hat={E_hat:.3f} lx vs true {E_true:.3f} lx: error {err:+.2f}% "
      f"(u_C/C = {100*uC/C:.2f}%)")

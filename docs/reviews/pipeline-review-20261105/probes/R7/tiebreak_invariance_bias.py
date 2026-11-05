#!/usr/bin/env python3
"""tiebreak_invariance_bias (tiebreak verifier) for finding invariance-bias-understated.

Checks, with the branch's own code (synthetic.held_frames -> card.box_hold -> card.box_reading ->
card.meter_reading -> card.k_L_fit -> card.held_out_check -> card.illuminance), for a grey card whose
offset-free reading is 100 nc and a code pedestal p (an offset meter-norm/v1 does not remove):

  1. the arithmetic: C = (C1+C2)/2 against the offset-free 100 nc, |C - 100| / u_C, and whether the
     3% exposure-invariance check passes;
  2. the reported numbers: k_L's confidence_pct_1sigma (u_k/k) as the tool computes it, against the same
     with the full offset bias of C added as a systematic term (is the understatement ~3x?);
  3. what the offset does where k_L is used: a production frame at auto's exposure (hold-1 conditions)
     of the same card, and a held-out card at another point (same pedestal) checked by
     card.held_out_check (is |e_j| within the reported u(e_j)?).

Synthetic data only, no network, nothing written.

    python3 -B tiebreak_invariance_bias.py <branch-root>
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
outer, inner = (300, 150, 339, 189), (305, 155, 334, 184)
H1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}   # auto's pick, x = 0.01
H2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}    # invariance_ratio 0.5


def reading(true_nc, pedestal, h1=H1, h2=H2):
    scene = {"background": 20.0, "rects": [(outer, true_nc)]}
    b1 = card.box_hold(syn.held_frames(scene, h1, 2, pedestal=pedestal), inner, params)
    b2 = card.box_hold(syn.held_frames(scene, h2, 2, pedestal=pedestal), inner, params)
    return card.box_reading(b1, b2, True, m), b1, b2


k_true = 0.1
rho = m["rho_card"]
sig_rho = m["rho_card_sigma"] / rho
for p in (0, 2, 4):
    r, b1, b2 = reading(100.0, p)
    C, uC, d = r["C_nc"], r["u_C_nc"], r["invariance_pct"]
    bias = C - 100.0
    ratio = (abs(bias) / uC) if uC else float("nan")
    print(f"RESULT p={p}: C1={b1['C_nc']:.4f} C2={b2['C_nc']:.4f} C={C:.4f} (offset-free 100) u_C={uC:.4f} "
          f"({100 * uC / C:.2f}%) d={d:.3f}% invariance_pass={r['invariance_pass']} |bias|/u_C={ratio:.2f}")
    E_fit = math.pi * k_true * 100.0 / rho              # what the meter reads at the fit card
    fit_reading = card.meter_reading(E_fit, E_fit, m)
    fit = card.k_L_fit(r, fit_reading, m)
    u_tool = fit["u_rel"]
    u_with_bias = math.sqrt(u_tool ** 2 + (bias / C) ** 2)
    print(f"RESULT p={p}: k_L={fit['k_L']:.6f} ({100 * (fit['k_L'] / k_true - 1):+.2f}% vs offset-free 0.1) "
          f"confidence_pct_1sigma tool={100 * u_tool:.2f}% with full bias added={100 * u_with_bias:.2f}% "
          f"(ratio {u_with_bias / u_tool:.2f}x)")
    # production: the same card at auto's exposure (hold-1 conditions), same pedestal
    prod = card.box_hold(syn.held_frames({"background": 20.0, "rects": [(outer, 100.0)]}, H1, 1, pedestal=p),
                         inner, params)
    E_prod = card.illuminance(fit["k_L"], prod["C_nc"], rho)
    print(f"RESULT p={p}: production frame at auto's exposure: E_hat={E_prod:.3f} lx vs meter {E_fit:.3f} lx "
          f"-> {100 * (E_prod / E_fit - 1):+.2f}% (u_C/C={100 * uC / C:.2f}%)")
    # held out: another point at 60 nc offset-free, auto picks a longer exposure so the codes are similar
    h1b = {"exposure_us": 17, "iso": 100, "white_balance_kelvin": 4000}
    h2b = {"exposure_us": round(17 * 0.5), "iso": 100, "white_balance_kelvin": 4000}
    rj, _, _ = reading(60.0, p, h1b, h2b)
    E_j = math.pi * k_true * 60.0 / rho
    held_reading = card.meter_reading(E_j, E_j, m)
    chk = card.held_out_check(r, fit_reading, fit["k_L"], rj, held_reading, m)
    keys = {k: chk[k] for k in chk if k in ("E_hat_lx", "error_lx", "u_error_lx")}
    print(f"RESULT p={p}: held-out 60 nc point (auto ~similar codes, d={rj['invariance_pct']:.2f}%): "
          + " ".join(f"{k}={v:.3f}" for k, v in keys.items()) + f" (meter {E_j:.3f} lx)")

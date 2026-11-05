#!/usr/bin/env python3
"""verify_invariance-bias-understated: independent re-check of finding invariance-bias-understated.

With a constant code offset (pedestal p) added to every pixel, hold 2 (half the exposure) carries twice
hold 1's relative bias. card.box_reading takes C = (C1 + C2) / 2 and u_C with an exposure-disagreement
term of |C1 - C2| / 2. This probe builds synthetic NV12 frames of a 100 nc card (the branch's own
synthetic.held_frames, linear test curves), runs card.box_hold + card.box_reading, and reports:
  - C, its bias against the true 100 nc, u_C, and the ratio bias / u_C;
  - whether the invariance check passes;
  - k_L's total relative 1-sigma (card.k_L_fit) with the tool's u_C versus with the bias added as a
    systematic term, to size the effect on the printed k_L +/-.

    python3 -B verify_invariance-bias-understated.py <branch-root>

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
worst_ratio = 0.0
for p in (0, 2, 3, 4):
    h1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}
    h2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}
    scene = {"background": 20.0, "rects": [(box, 100.0)]}
    f1, f2 = syn.held_frames(scene, h1, 2, pedestal=p), syn.held_frames(scene, h2, 2, pedestal=p)
    r = card.box_reading(card.box_hold(f1, inner, params), card.box_hold(f2, inner, params), True, m)
    c1, c2 = r["hold1"]["C_nc"], r["hold2"]["C_nc"]
    bias = r["C_nc"] - 100.0
    reading = card.meter_reading(174.533, 174.533, m)
    fit = card.k_L_fit({"C_nc": r["C_nc"], "u_C_nc": r["u_C_nc"]}, reading, m)
    rel_sys = math.sqrt(fit["u_rel"] ** 2 + (bias / r["C_nc"]) ** 2 - (r["u_C_nc"] / r["C_nc"]) ** 2)
    ratio = abs(bias) / r["u_C_nc"] if r["u_C_nc"] else float("inf")
    if r["invariance_pass"] and p:
        worst_ratio = max(worst_ratio, ratio)
    print(f"RESULT pedestal={p}: C1={c1:.4f} C2={c2:.4f} C={r['C_nc']:.4f} (true 100, bias {100*bias/100:+.3f}%) "
          f"u_C={r['u_C_nc']:.4f} ({100*r['u_C_nc']/r['C_nc']:.3f}%) bias/u_C={ratio:.2f} "
          f"d={r['invariance_pct']:.4f}% pass={r['invariance_pass']} | k_L u_rel tool={100*fit['u_rel']:.2f}% "
          f"with bias as systematic={100*rel_sys:.2f}%")
print(f"RESULT worst bias/u_C among passing offsets: {worst_ratio:.2f}")
print("FAIL u_C understates an offset bias that passes invariance" if worst_ratio > 2 else
      "PASS u_C covers the offset bias")
sys.exit(1 if worst_ratio > 2 else 0)

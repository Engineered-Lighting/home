#!/usr/bin/env python3
"""Rung 7's unanchored LightState value is pi*C/rho_card labelled "relative"; rung 8's light model calls its
normalised counts (C, nc) "relative" and rung 10 compares the two as "the same camera units".

The probe builds an unanchored LightState with card.light_state_record for a card reading C nc and prints the
ratio value/C (pi/rho_card, about 17.45 at rho_card 0.18). If a rung 10 checkout is given as the second
argument, it also runs rung 10's own within() on a measured change taken from two rung-7 LightStates against
the same change predicted in normalised counts (as rung 8's predict() returns them).

Synthetic values only; no network.
Run:
    python3 probe_relative_unit_scale.py <root of the rung-7 checkout> [<root of the rung-10 checkout>]
"""
import math
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402

m = card.load_method(ROOT / "tools" / "den-photometry" / "METHOD-V1.md").closed(
    commissioning_option="card-and-meter", k_L_scope="single", meter_tolerance_lx=10, meter_class="x")
norm = {"version": "meter-norm/v1", "sha256": "0" * 64}


def state(C):
    cap = {"ts": 1793000000.0, "point": "desk", "blinds": "closed", "exposure": {"time_ms": 10.0, "gain": 1.0},
           "card": {"C_nc": C, "clipped_frac": 0.0}}
    return card.light_state_record(cap, None, False, m, norm, "den-v4-p1")["surface_estimates"][0]


before, after = state(40.0), state(50.0)
ratio = after["value"] / 50.0
print(f"unanchored LightState: C 50 nc -> value {after['value']:.3f} unit {after['unit']!r}; value/C = {ratio:.4f}"
      f" (pi/rho_card = {math.pi / 0.18:.4f})")
mismatch = after["unit"] == "relative" and abs(ratio - 1) > 1e-6
if len(sys.argv) > 2:
    sys.path.insert(0, str(Path(sys.argv[2]).resolve() / "tools" / "den-verify"))
    import score as verify  # noqa: E402  rung 10's scorer
    measured_change = after["value"] - before["value"]     # rung 7 LightStates, "relative"
    predicted_change = 50.0 - 40.0                         # rung 8 model, "relative" = normalised counts
    ok = verify.within(measured_change, predicted_change, 0.15)
    print(f"rung 10 within(measured {measured_change:.2f}, predicted {predicted_change:.2f}, 0.15) -> {ok} "
          "(the true change is identical: 10 nc)")
    mismatch = mismatch and not ok
print("FAIL: rung 7's 'relative' is pi/rho_card times the normalised counts other rungs call 'relative'"
      if mismatch else "PASS: same scale")
print("RESULT: " + ("DEFECT REPRODUCED" if mismatch else "not reproduced"))
sys.exit(1 if mismatch else 0)

#!/usr/bin/env python3
"""Verifier probe (reproduce lens) for finding reflectance-surfaces-not-open (rung 7 METHOD-V1.md:42).

Checks at the given checkout:
  1. METHOD-V1's parsed block: reflectance_surfaces' value and whether it is marked open (an open line makes
     every scored run refuse until the owner confirms it).
  2. card.score with the README's flow (surface box at counter-1 only, held-out coverage complete): the gate
     verdict and reflectance_missing, i.e. whether a PASS is printed while another coordinator target
     (armchair-book in the contracts' Decision example) has no reflectance capture.
  3. Whether a later reflectance capture at armchair-book is still measured under the SAME block (so adding a
     region needs no METHOD-V2 to be measured, only to be required).

Synthetic capture dicts only; no network; nothing written.
Run:
    python3 -B verify2_reflectance-surfaces-not-open.py <root of the branch checkout>
"""
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402
import synthetic as syn  # noqa: E402

real = card.load_method()
print("METHOD-V1 reflectance_surfaces =", real["reflectance_surfaces"],
      "| marked open:", "reflectance_surfaces" in real.open)
m = syn.photometry_method()


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True, "hold1": {}, "hold2": {}}


def cap(n, day, point, C, illuminant="bulbs", surface=None, reading=True):
    E = math.pi * 0.1 * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "x", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface else None,
            "reading": card.meter_reading(E, E, m) if reading else None, "note": ""}


caps = [cap(1, "20261110", "counter-1", 100, surface=194.4444), cap(2, "20261110", "desk", 50),
        cap(3, "20261110", "sofa", 30), cap(4, "20261111", "desk", 80, "daylight"),
        cap(5, "20261111", "sofa", 40, "daylight")]
r = card.score(caps, m, ["meter-norm/v1"])
print("README flow (counter-1 only): gate =", r["gate"], "| reflectance measured:", sorted(r["reflectance"]),
      "| reflectance_missing:", r["reflectance_missing"])
r2 = card.score(caps + [cap(6, "20261111", "armchair-book", 60, surface=90.0, reading=False)], m, ["meter-norm/v1"])
print("same block + armchair-book reflectance capture: gate =", r2["gate"], "| reflectance measured:",
      {k: round(v["rho"], 4) for k, v in r2["reflectance"].items()})
print("RESULT: not open =", "reflectance_surfaces" not in real.open, "| PASS without armchair-book =",
      r["gate"] == "pass", "| armchair-book measurable under the same block =", "armchair-book" in r2["reflectance"])

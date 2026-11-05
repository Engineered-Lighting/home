#!/usr/bin/env python3
"""verify_reflectance-surfaces-not-open: independent re-check of finding reflectance-surfaces-not-open.

Ladder rung 7 'tested' asks for "a grey-card reflectance pass at every working region the coordinator
will target, counter-1 at least (A12)". This probe checks, at the given checkout:
  1. METHOD-V1's parsed block: is reflectance_surfaces marked open (so a scored run refuses until the
     owner lists the regions)? What value does it hold?
  2. card.score on synthetic captures where only counter-1 has a reflectance capture, while the
     coordinator's other target region in the contracts' examples (armchair-book, B's reading region in
     schemas.json Decision/LightState examples) has none: does the gate print PASS and report no
     reflectance missing?

    python3 -B verify_reflectance-surfaces-not-open.py <branch-root>

Synthetic dicts only; no network; nothing written.
"""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import card  # noqa: E402
import synthetic as syn  # noqa: E402

real = card.load_method()
print(f"RESULT METHOD-V1 reflectance_surfaces={real['reflectance_surfaces']} open={'reflectance_surfaces' in real.open}")
print(f"RESULT METHOD-V1 open lines: {list(real.open)}")
schemas = json.loads((ROOT / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
text = json.dumps(schemas)
print(f"RESULT schemas.json mentions armchair-book {text.count('armchair-book')} times (as aim / target / surface)")

m = syn.photometry_method()


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True, "hold1": {}, "hold2": {}}


def cap(n, day, point, C, illuminant, surface=None):
    E = 3.141592653589793 * 0.1 * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "x", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface is not None else None,
            "reading": card.meter_reading(E, E, m), "note": ""}


caps = [cap(1, "20261110", "counter-1", 100.0, "bulbs", surface=194.44),
        cap(2, "20261110", "desk", 50.0, "bulbs"), cap(3, "20261110", "sofa", 25.0, "bulbs"),
        cap(4, "20261111", "desk", 80.0, "daylight"), cap(5, "20261111", "sofa", 40.0, "daylight"),
        cap(6, "20261111", "armchair-book", 30.0, "daylight")]   # metered, but no surface box there
result = card.score(caps, m, ["meter-norm/v1"])
print(f"RESULT gate={result['gate']} reflectance measured={sorted(result['reflectance'])} "
      f"reflectance_missing={result['reflectance_missing']}")
bad = result["gate"] == "pass" and "reflectance_surfaces" not in real.open and \
    sorted(result["reflectance"]) == ["counter-1"]
print("FAIL gate PASS with counter-1 the only measured reflectance and no open slot for the region list" if bad
      else "PASS")
sys.exit(1 if bad else 0)

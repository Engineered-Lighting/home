#!/usr/bin/env python3
"""verifier_intent_per_class_readme_flow (verifier, intent lens): for finding per-class-pooled-coverage, scores the
README's own minimal flow (step 8: day 1 bulbs at counter-1 then desk and sofa; one more day, lit by window
daylight, capturing desk, sofa and counter-1 again) under k_L_scope = per-illuminant-class, with every reading
agreeing exactly with its own class's scale, and reports the gate and each group's held-out days. Then the
same data with the daylight day's scale 20 % off the bulbs scale under single scope as a control.

METHOD-V1 'Coverage' says the held-out checks are complete when "a group's fit and held-out captures span at
least min_days days".

    python3 -B verifier_intent_per_class_readme_flow.py <branch-root>

Synthetic dicts only (the shapes test_card.py uses); nothing written; no network.
"""
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import card  # noqa: E402
import synthetic as syn  # noqa: E402


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True}


def cap(n, day, point, C, k, m, ill, surface=None):
    E = math.pi * k * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1000.0 + n, n), "point": point, "illuminant": ill,
            "blinds": "open", "light": "x", "ts": 1793000000.0 + n, "exposure": {"time_ms": 10.0, "gain": 1.0},
            "card": box(C), "surface": box(surface) if surface else None,
            "reading": card.meter_reading(E, E, m), "note": ""}


for scope in ("per-illuminant-class", "single"):
    m = syn.photometry_method(k_L_scope=scope)
    caps = [cap(1, "20261110", "counter-1", 100.0, 0.10, m, "bulbs", surface=194.44),
            cap(2, "20261110", "desk", 50.0, 0.10, m, "bulbs"),
            cap(3, "20261110", "sofa", 25.0, 0.10, m, "bulbs"),
            cap(4, "20261111", "desk", 80.0, 0.12, m, "daylight"),
            cap(5, "20261111", "sofa", 40.0, 0.12, m, "daylight"),
            cap(6, "20261111", "counter-1", 90.0, 0.12, m, "daylight")]
    r = card.score(caps, m, ["meter-norm/v1"])
    print(f"RESULT scope={scope} gate={r['gate']} held_out={r['held_out']['result']} missing={r['held_out']['missing']}")
    for key, g in sorted(r["groups"].items()):
        days = sorted({p["day"] for p in g["points"]} | ({g["fit"].split("-")[0]} if g["fit"] else set()))
        print(f"RESULT   group={key} fit={g['fit']} k_L={g['k_L']:.4f} fit+held-out days={days} "
              f"(METHOD: span at least {m['min_days']})")

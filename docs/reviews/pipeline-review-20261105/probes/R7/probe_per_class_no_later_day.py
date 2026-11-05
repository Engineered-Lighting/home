#!/usr/bin/env python3
"""Per-illuminant-class scope: rung 7's gate passes although no k_L is checked on any day but its own fit day.

The ladder (rung 7) fits k_L on day 1 and holds "the other meter points and later days" back as checks.
card.coverage() counts min_days over the union of all groups' fit and held-out days, so with
k_L_scope = per-illuminant-class a natural plan (README step 8: day 1 under the bulbs, a second day lit by
window daylight) gives the bulbs k_L only same-day checks and the daylight k_L (fitted on day 2, its own
"first day") only same-day checks, yet coverage is complete and the gate is PASS.

The probe builds the same physical captures twice: the camera's true scale drops 30% from day 1 to day 2
(drift), and the meter is right. Single scope catches it (FAIL); per-class scope reports PASS with anchored
k_L values whose day-to-day stability was never tested.

Scenario B: drop day 1's desk and sofa, so the bulbs k_L has no held-out capture at all; coverage counts
desk and sofa from the daylight group, and the gate is still PASS with the bulbs k_L anchored.

Synthetic capture dicts only (the shape card.select documents); no frames, no network.
Run:
    python3 probe_per_class_no_later_day.py <root of the branch checkout>
"""
import math
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402


def method(**changes):
    values = dict(commissioning_option="card-and-meter", k_L_scope="single", meter_tolerance_lx=10,
                  meter_class="test-meter")
    values.update(changes)
    return card.load_method(ROOT / "tools" / "den-photometry" / "METHOD-V1.md").closed(**values)


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True}


def cap(n, day, point, C, E, illuminant, m, surface=None):
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1793000000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "open" if illuminant == "daylight" else "closed",
            "light": "x", "ts": 1793000000.0 + n, "exposure": {"time_ms": 10.0, "gain": 1.0},
            "card": box(C), "surface": box(surface) if surface else None,
            "reading": card.meter_reading(E, E, m), "note": ""}


def plan(m):
    # true scale: day 1 k = 0.100; day 2 the camera reads 30% higher per lux (k = 0.0769): drift.
    lux = lambda k, C: math.pi * k * C / 0.18  # noqa: E731  the meter's (true) reading for card C
    k1, k2 = 0.100, 0.100 / 1.3
    return [cap(1, "20261110", "counter-1", 100, lux(k1, 100), "bulbs", m, surface=194.4444),
            cap(2, "20261110", "desk", 50, lux(k1, 50), "bulbs", m),
            cap(3, "20261110", "sofa", 30, lux(k1, 30), "bulbs", m),
            cap(4, "20261111", "counter-1", 100, lux(k2, 100), "daylight", m),
            cap(5, "20261111", "desk", 50, lux(k2, 50), "daylight", m),
            cap(6, "20261111", "sofa", 30, lux(k2, 30), "daylight", m)]


single = method(k_L_scope="single")
r1 = card.score(plan(single), single, ["meter-norm/v1"])
per = method(k_L_scope="per-illuminant-class")
r2 = card.score(plan(per), per, ["meter-norm/v1"])

print(f"single scope:    gate {r1['gate'].upper()}, held_out {r1['held_out']['result']}, "
      f"worst |e| {max(abs(p['error_lx']) for g in r1['groups'].values() for p in g['points']):.1f} lx")
print(f"per-class scope: gate {r2['gate'].upper()}, held_out {r2['held_out']['result']}, anchored {r2['anchored']}")
same_day_only = True
for key, g in sorted(r2["groups"].items()):
    fit_day = g["first_day"]
    days = sorted({p["day"] for p in g["points"]})
    print(f"  group {key}: fit {g['fit']} (day {fit_day}), k_L {g['k_L']:.5f}, held-out days {days}")
    same_day_only &= days == [fit_day]
# scenario B: the bulbs k_L has no held-out capture at all (day 1: only the fit at counter-1)
caps_b = [c for c in plan(per) if c["id"] not in ("20261110-2", "20261110-3")]
r3 = card.score(caps_b, per, ["meter-norm/v1"])
bulbs = r3["groups"]["bulbs"]
print(f"scenario B (per-class, bulbs fit only): gate {r3['gate'].upper()}, anchored {r3['anchored']}, "
      f"bulbs k_L {bulbs['k_L']:.5f} with {len(bulbs['points'])} held-out checks")
no_check = r3["gate"] == "pass" and r3["anchored"] and len(bulbs["points"]) == 0
hole = r1["gate"] == "fail" and r2["gate"] == "pass" and same_day_only and no_check
print("FAIL: per-class gate PASS with every k_L checked only on its own fit day (the 30% day-to-day drift is "
      "never tested; single scope on the same data FAILS), and PASS with a k_L that has no held-out check at all"
      if hole else
      "PASS: per-class scope requires a later held-out day per k_L")
print("RESULT: " + ("DEFECT REPRODUCED" if hole else "not reproduced"))
sys.exit(1 if hole else 0)

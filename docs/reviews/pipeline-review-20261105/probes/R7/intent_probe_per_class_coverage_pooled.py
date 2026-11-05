#!/usr/bin/env python3
"""(R7 verifier, intent-and-spec lens) Finding per-class-coverage-pooled.

What it checks, with k_L_scope = per-illuminant-class (one of the two documented owner choices):
  A. The README step 8 plan done the natural way (day 1 under the bulbs: counter-1 fit, desk, sofa; day 2
     under window daylight: counter-1, desk, sofa), meter and camera agreeing within 1 lx everywhere.
     METHOD-V1 "Coverage" asks that "a group's fit and held-out captures span at least min_days days".
     The probe prints, per group, the days its own fit and held-out captures span, and whether ANY group
     spans min_days; then card.score's held-out result and gate.
  B. Same, but day 1 holds only the bulbs fit (its desk and sofa readings drifted and were not used, which
     is modelled by a before/after pair 12% apart, so card.meter_reading itself rejects them). The bulbs
     k_L has no held-out check at all. The probe then builds the records the score writes
     (card.k_L_records, card.room_model_record, card.light_state_record, exactly as photometry.score_root
     calls them) and prints the bulbs CalibrationRecord's checks.held_out and the bulbs LightStates.

Synthetic capture dicts in the shape card.select documents; no frames, no camera, no network, no files.
Run:
    python3 intent_probe_per_class_coverage_pooled.py <root of the claude/rung-7-photometry checkout>
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402

SCHEMAS = ROOT / "docs/architecture/live-v1/schemas.json"
NORM = {"version": "meter-norm/v1", "sha256": "0" * 64}


def method(scope):
    return card.load_method(ROOT / "tools/den-photometry/METHOD-V1.md").closed(
        commissioning_option="card-and-meter", k_L_scope=scope, meter_tolerance_lx=10, meter_class="test-meter")


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.2, "clipped_frac": 0.0,
            "invariance_pct": 0.4, "u_invariance_pct": 0.1, "invariance_pass": True}


def cap(n, day, point, C, before, after, illuminant, m, surface=None):
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1793000000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "open" if illuminant == "daylight" else "closed",
            "light": "x", "ts": 1793000000.0 + n, "exposure": {"time_ms": 10.0, "gain": 1.0},
            "card": box(C), "surface": box(surface) if surface else None,
            "reading": card.meter_reading(before, after, m), "note": ""}


def lux(k, C):
    return math.pi * k * C / 0.18


def plan(m, drop_day1_held=False):
    k1, k2 = 0.100, 0.100 / 1.3   # the camera's true scale on day 1 and on day 2 (a 30% day-to-day change)
    d1 = [cap(1, "20261110", "counter-1", 100, lux(k1, 100), lux(k1, 100), "bulbs", m, surface=194.4444)]
    if drop_day1_held:
        d1 += [cap(2, "20261110", "desk", 50, lux(k1, 50), 1.12 * lux(k1, 50), "bulbs", m),
               cap(3, "20261110", "sofa", 30, lux(k1, 30), 1.12 * lux(k1, 30), "bulbs", m)]
    else:
        d1 += [cap(2, "20261110", "desk", 50, lux(k1, 50) + 0.5, lux(k1, 50) + 0.5, "bulbs", m),
               cap(3, "20261110", "sofa", 30, lux(k1, 30) - 0.5, lux(k1, 30) - 0.5, "bulbs", m)]
    d2 = [cap(4, "20261111", "counter-1", 100, lux(k2, 100), lux(k2, 100), "daylight", m),
          cap(5, "20261111", "desk", 50, lux(k2, 50) + 0.5, lux(k2, 50) + 0.5, "daylight", m),
          cap(6, "20261111", "sofa", 30, lux(k2, 30) - 0.5, lux(k2, 30) - 0.5, "daylight", m)]
    return d1 + d2


def per_group_days(result, m):
    out = {}
    for key, g in result["selection"]["groups"].items():
        caps = g["held_out"] + ([g["fit"]] if g["fit"] else [])
        out[key] = sorted({c["day"] for c in caps})
    return out


bad = []
single, per = method("single"), method("per-illuminant-class")
rs = card.score(plan(single), single, ["meter-norm/v1"])
print(f"A single scope  : gate {rs['gate'].upper()}, held_out {rs['held_out']['result']}")
ra = card.score(plan(per), per, ["meter-norm/v1"])
spans = per_group_days(ra, per)
any_group_spans = any(len(d) >= per["min_days"] for d in spans.values())
print(f"A per-class     : gate {ra['gate'].upper()}, held_out {ra['held_out']['result']}, anchored {ra['anchored']}, "
      f"missing {ra['held_out']['missing']}")
for key, days in sorted(spans.items()):
    g = ra["groups"][key]
    print(f"    group {key:8}: own fit+held-out days {days}; k_L {g['k_L']:.5f}; "
          f"held-out points {[(p['day'], p['point']) for p in g['points']]}")
print(f"    any group spanning min_days={per['min_days']}: {any_group_spans}")
if ra["gate"] == "pass" and not any_group_spans:
    bad.append("A: gate PASS although no group's fit and held-out captures span min_days (METHOD-V1 Coverage)")

rb_caps = plan(per, drop_day1_held=True)
rb = card.score(rb_caps, per, ["meter-norm/v1"])
unused = {c["id"]: why for c, why in rb["selection"]["unused"]}
bulbs = rb["groups"]["bulbs"]
print(f"B per-class     : gate {rb['gate'].upper()}, held_out {rb['held_out']['result']}, anchored {rb['anchored']}; "
      f"bulbs k_L {bulbs['k_L']:.5f} with {len(bulbs['points'])} held-out checks; unused {unused}")
by_id = {c["id"]: c for c in rb_caps}
k_records = card.k_L_records(rb, by_id, per, NORM, 1)
rho_records = card.reflectance_records(rb, by_id, per, NORM, 1)
room_in = next(r for r in json.loads(SCHEMAS.read_text(encoding="utf-8"))["records"] if r["id"] == "room_model")["example"]
room = card.room_model_record(json.loads(json.dumps(room_in)), rb, k_records, rho_records, per, NORM, 1)
kb = next(r for r in k_records if r["version"].endswith("-bulbs"))
h = kb["checks"]["held_out"]
print(f"    bulbs CalibrationRecord {kb['version']}: value {kb['value']}, held_out.result {h['result']!r}, "
      f"n_points {h['n_points']}, own points {len(h['points'])}, group {h.get('group')}")
print(f"    RoomModel photometric: {json.dumps(room['cameras'][0]['photometric'], ensure_ascii=False)}")
used_bulbs = [by_id["20261110-1"]]
for c in used_bulbs:  # as photometry.score_root builds each LightState
    g = rb["groups"].get(card.group_of(c, per), {})
    st = card.light_state_record(c, g.get("k_L"), rb["anchored"] and g.get("k_L") is not None, per, NORM,
                                 room["version"])
    e = st["surface_estimates"][0]
    print(f"    LightState {c['id']} ({c['illuminant']}): anchored {st['anchored']}, {e['value']:.3f} {e['unit']}")
if rb["gate"] == "pass" and rb["anchored"] and not bulbs["points"] and h["result"] == "pass":
    bad.append("B: gate PASS and the bulbs k_L published anchored with zero held-out checks; its record says "
               "held_out pass")
for b in bad:
    print("FAIL: " + b)
print("RESULT: " + ("DEFECT REPRODUCED" if bad else "not reproduced"))
sys.exit(1 if bad else 0)

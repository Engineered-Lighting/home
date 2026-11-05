#!/usr/bin/env python3
"""(R7 verifier, intent-and-spec lens) Findings per-class-status-measured-k_L-null and relative-unit-scale-mismatch.

Part 1 (status): a passing per-illuminant-class score (two days, each class with a later-day check, so the
coverage hole of the other finding is not involved) -> card.room_model_record's photometric entry. Prints
k_L, calibration_id and status, and the METHOD-V1 sentence on status.

Part 2 (relative): an unanchored rung-7 LightState (card.light_state_record) -> value / C. Then the seam:
rung 8 Stage 1's predict.py docstring for its "relative" unit, rung 8's point ids (model.point_id), and how
rung 10's score.Frame takes a LightState's unit (from `anchored` alone) and whether it finds a rung-7 point
under a rung-8 point id (the counterpoint: rung 7's own LightStates are keyed by meter points).

Synthetic values only; no network, no files written.
Run:
    python3 intent_probe_relative_and_status.py <rung-7 checkout> <rung-8-stage1 checkout> <rung-10-verify checkout>
"""
import json
import math
import sys
from pathlib import Path

R7, R8, R10 = (Path(a).resolve() for a in sys.argv[1:4])
sys.path.insert(0, str(R7 / "tools/den-photometry"))
import card  # noqa: E402

NORM = {"version": "meter-norm/v1", "sha256": "0" * 64}
m = card.load_method(R7 / "tools/den-photometry/METHOD-V1.md").closed(
    commissioning_option="card-and-meter", k_L_scope="per-illuminant-class", meter_tolerance_lx=10, meter_class="x")


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.1, "clipped_frac": 0.0,
            "invariance_pct": 0.4, "u_invariance_pct": 0.1, "invariance_pass": True}


def cap(n, day, point, C, illuminant, k, surface=None):
    E = math.pi * k * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1793000000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "x", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface else None, "reading": card.meter_reading(E, E, m), "note": ""}


caps = [cap(1, "20261110", "counter-1", 100, "bulbs", 0.10, surface=194.4444),
        cap(2, "20261110", "desk", 50, "bulbs", 0.10), cap(3, "20261110", "sofa", 30, "bulbs", 0.10),
        cap(4, "20261111", "counter-1", 100, "daylight", 0.11), cap(5, "20261111", "desk", 50, "daylight", 0.11),
        cap(6, "20261111", "sofa", 30, "daylight", 0.11), cap(7, "20261111", "desk", 50, "bulbs", 0.10),
        cap(8, "20261112", "sofa", 30, "daylight", 0.11)]
res = card.score(caps, m, ["meter-norm/v1"])
by_id = {c["id"]: c for c in caps}
k_rec = card.k_L_records(res, by_id, m, NORM, 1)
schemas = json.loads((R7 / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
room_in = next(r for r in schemas["records"] if r["id"] == "room_model")["example"]
room = card.room_model_record(json.loads(json.dumps(room_in)), res, k_rec, [], m, NORM, 1)
ph = room["cameras"][0]["photometric"]
print(f"1 per-class gate {res['gate'].upper()}, anchored {res['anchored']}, group days "
      f"{ {k: sorted({p['day'] for p in g['points']}) for k, g in res['groups'].items()} }")
print(f"1 photometric k_L {ph['k_L']!r}, calibration_id {ph['calibration_id']!r}, status {ph['status']!r}, "
      f"k_L_by_illuminant {ph.get('k_L_by_illuminant')}")
method_text = (R7 / "tools/den-photometry/METHOD-V1.md").read_text(encoding="utf-8")
i = method_text.index("`status` is `measured`")
print("1 METHOD-V1 says: " + " ".join(method_text[i:i + 330].split()))
status_bad = ph["status"] == "measured" and ph["k_L"] is None and ph["calibration_id"] is None
print("1 " + ("FAIL: status 'measured' while k_L and calibration_id are null" if status_bad else "PASS"))

# ---- part 2 ----
ms = m.closed(k_L_scope="single")
cap_u = {"ts": 1793000000.0, "point": "desk", "blinds": "closed", "exposure": {"time_ms": 10.0, "gain": 1.0},
         "card": {"C_nc": 50.0, "clipped_frac": 0.0}}
st = card.light_state_record(cap_u, None, False, ms, NORM, "den-v4-p1")
e = st["surface_estimates"][0]
print(f"2 rung 7 unanchored: C 50 nc -> value {e['value']:.4f} unit {e['unit']!r}; value/C {e['value'] / 50:.4f} "
      f"(pi/rho_card {math.pi / ms['rho_card']:.4f})")
pred_doc = next(l for l in (R8 / "tools/den-light/predict.py").read_text(encoding="utf-8").splitlines()
                if '"unit"' in l and "relative" in l)
print("2 rung 8 predict.py: " + pred_doc.strip())
sys.path.insert(0, str(R8 / "tools/den-light"))
try:
    import model as r8model  # noqa: E402
    print(f"2 rung 8 point ids look like: {r8model.point_id(0, 32)!r}, {r8model.point_id(76, 32)!r}")
except Exception as exc:  # noqa: BLE001
    print(f"2 rung 8 model import failed: {exc!r}")
sys.path.insert(0, str(R10 / "tools/den-verify"))
import score as verify  # noqa: E402


class Clock:
    def hhmmss(self, ts):
        return "000000"


frame = verify.Frame(dict(st, unit_ignored=True), Clock())
st_lx = dict(st, anchored=True)
st_lx["surface_estimates"] = [dict(e, unit="weird-unit")]
print(f"2 rung 10 Frame.unit for an unanchored frame: {frame.unit!r}; for anchored with estimate unit "
      f"'weird-unit': {verify.Frame(st_lx, Clock()).unit!r} (taken from `anchored` only)")
print(f"2 rung 10 usable('r02c12') on a rung-7 LightState: {frame.usable('r02c12', {'v_min': 0.5})}; "
      f"usable('desk'): {frame.usable('desk', {'v_min': 0.5})}")
print(f"2 rung 10 within(measured {e['value'] - 40 * math.pi / 0.18:.2f}, predicted 10.0 nc, 0.15) -> "
      f"{verify.within(e['value'] - 40 * math.pi / 0.18, 10.0, 0.15)}")
rel_bad = e["unit"] == "relative" and abs(e["value"] / 50 - 1) > 1e-6
print("2 " + ("FAIL: rung 7 'relative' = pi/rho_card x the normalised counts rung 8 calls 'relative'" if rel_bad
              else "PASS"))
print("RESULT: status " + ("REPRODUCED" if status_bad else "not reproduced") + "; relative scale "
      + ("REPRODUCED" if rel_bad else "not reproduced"))

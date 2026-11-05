#!/usr/bin/env python3
"""Per-illuminant-class scope writes RoomModel.cameras[].photometric with status "measured" but k_L null and
calibration_id null.

schemas.json (room_model.cameras[].photometric) is {k_L, unit "(cd/m²)/nc", normalisation_version,
calibration_id, status: uncalibrated | measured}: K11 reads k_L through it, L = k_L·C. card.room_model_record
sets status "measured" for a passing per-class score while leaving k_L and calibration_id None and putting the
scales under an additive k_L_by_illuminant key. A consumer that trusts status ("measured") and multiplies k_L
by C fails (None) or, in JavaScript, gets NaN.

Synthetic capture dicts and the contract's RoomModel example only; no network.
Run:
    python3 probe_per_class_status_measured_k_L_null.py <root of the branch checkout>
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402

m = card.load_method(ROOT / "tools" / "den-photometry" / "METHOD-V1.md").closed(
    commissioning_option="card-and-meter", k_L_scope="per-illuminant-class", meter_tolerance_lx=10,
    meter_class="test-meter")


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True}


def cap(n, day, point, C, illuminant, k, surface=None):
    E = math.pi * k * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1793000000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "x", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface else None, "reading": card.meter_reading(E, E, m), "note": ""}


caps = [cap(1, "20261110", "counter-1", 100, "bulbs", 0.1, surface=194.4444),
        cap(2, "20261110", "desk", 50, "bulbs", 0.1), cap(3, "20261111", "sofa", 30, "bulbs", 0.1),
        cap(4, "20261110", "counter-1", 90, "daylight", 0.11), cap(5, "20261111", "desk", 50, "daylight", 0.11),
        cap(6, "20261111", "sofa", 30, "daylight", 0.11)]
result = card.score(caps, m, ["meter-norm/v1"])
by_id = {c["id"]: c for c in caps}
norm = {"version": "meter-norm/v1", "sha256": "0" * 64}
k_records = card.k_L_records(result, by_id, m, norm, 1)
rho_records = card.reflectance_records(result, by_id, m, norm, 1)
schemas = json.loads((ROOT / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
room = next(r for r in schemas["records"] if r["id"] == "room_model")["example"]
out = card.room_model_record(json.loads(json.dumps(room)), result, k_records, rho_records, m, norm, 1)
photometric = out["cameras"][0]["photometric"]
print("gate:", result["gate"], " anchored:", result["anchored"])
print("photometric:", json.dumps(photometric, ensure_ascii=False))
bad = photometric["status"] == "measured" and photometric["k_L"] is None and photometric["calibration_id"] is None
try:
    L = photometric["k_L"] * 50.0 if photometric["status"] == "measured" else None
    print("consumer L = k_L*C:", L)
except TypeError as exc:
    print("consumer L = k_L*C raises:", type(exc).__name__, exc)
print(("FAIL: status 'measured' with k_L null and calibration_id null" if bad else "PASS: status agrees with k_L"))
print("RESULT: " + ("DEFECT REPRODUCED" if bad else "not reproduced"))
sys.exit(1 if bad else 0)

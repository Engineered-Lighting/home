#!/usr/bin/env python3
"""A surface box that reads brighter than the card by more than 1/rho_card (a gloss highlight on the counter,
or the box drawn over a lit edge) gives rho_s > 1, which card.score accepts and card.room_model_record writes
into RoomModel.surfaces[].reflectance with status "measured". A matte reflectance cannot exceed 1.

Synthetic capture dicts and the contract's RoomModel example only; no network.
Run:
    python3 probe_reflectance_above_one.py <root of the branch checkout>
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))
import card  # noqa: E402

m = card.load_method(ROOT / "tools" / "den-photometry" / "METHOD-V1.md").closed(
    commissioning_option="card-and-meter", k_L_scope="single", meter_tolerance_lx=10, meter_class="x")


def box(C):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": 0.5, "u_invariance_pct": 0.1, "invariance_pass": True}


def cap(n, day, point, C, illuminant="bulbs", surface=None):
    E = math.pi * 0.1 * C / 0.18
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1793000000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "x", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface else None, "reading": card.meter_reading(E, E, m), "note": ""}


caps = [cap(1, "20261110", "counter-1", 100, surface=650.0),   # surface box reads 6.5x the card
        cap(2, "20261110", "desk", 50), cap(3, "20261111", "sofa", 30, "daylight"),
        cap(4, "20261111", "desk", 60, "daylight")]
result = card.score(caps, m, ["meter-norm/v1"])
by_id = {c["id"]: c for c in caps}
norm = {"version": "meter-norm/v1", "sha256": "0" * 64}
rho = card.reflectance_records(result, by_id, m, norm, 1)
schemas = json.loads((ROOT / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
room = next(r for r in schemas["records"] if r["id"] == "room_model")["example"]
out = card.room_model_record(json.loads(json.dumps(room)), result, card.k_L_records(result, by_id, m, norm, 1),
                             rho, m, norm, 1)
counter = next(s for s in out["surfaces"] if s["surface_id"] == "counter-1")["reflectance"]
print("gate:", result["gate"], " counter-1 reflectance written:", counter)
bad = counter["status"] == "measured" and counter["value"] > 1
print("FAIL: a reflectance above 1 is written as measured" if bad else "PASS: rho_s > 1 refused or flagged")
print("RESULT: " + ("DEFECT REPRODUCED" if bad else "not reproduced"))
sys.exit(1 if bad else 0)

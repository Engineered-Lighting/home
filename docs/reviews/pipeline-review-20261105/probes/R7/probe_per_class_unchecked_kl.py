#!/usr/bin/env python3
"""probe_per_class_unchecked_kl: with k_L_scope = per-illuminant-class, does the gate pass while a
class's k_L has no independent held-out check at all (scenario 1), or while no k_L is ever checked on
a day other than its own fit day (scenario 2)?

The ladder's rung 7 gate: "held-out days and meter points not used to fit k_L stay within a meter
tolerance in lux". card.coverage() pools the held-out captures of every group, so one group's
held-out points and days satisfy coverage for another group.

Synthetic box/reading dicts only (the same shapes test_card.py uses); no camera, no network, no files
written outside a temp dir.

    python3 -B probe_per_class_unchecked_kl.py <branch-root>

Prints PASS when the tool refuses to qualify an unchecked k_L, FAIL when it qualifies one.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import card  # noqa: E402
import synthetic as syn  # noqa: E402


def box(C, d=0.5):
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": 0.0, "clipped_frac": 0.0,
            "invariance_pct": d, "u_invariance_pct": 0.1, "invariance_pass": d <= 3, "hold1": {}, "hold2": {}}


def cap(n, day, point, C, E, m, illuminant, surface=None):
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "x", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface is not None else None,
            "reading": card.meter_reading(E, E, m) if E is not None else None, "note": ""}


def E_of(C, k=0.1, rho=0.18):
    """The lux a card of C nc reads under scale k (so a capture agrees exactly with that k)."""
    return 3.141592653589793 * k * C / rho


def run(name, captures, m, room):
    result = card.score(captures, m, ["meter-norm/v1"])
    norm = {"version": "meter-norm/v1", "sha256": "0" * 64}
    by_id = {c["id"]: c for c in captures}
    k_records = card.k_L_records(result, by_id, m, norm, 1)
    rho_records = card.reflectance_records(result, by_id, m, norm, 1)
    room_out = card.room_model_record(room, result, k_records, rho_records, m, norm, 1)
    print(f"--- {name}")
    print(f"RESULT gate={result['gate']} held_out={result['held_out']['result']} "
          f"missing={result['held_out']['missing']}")
    for key, g in sorted(result["groups"].items()):
        days = sorted({p['day'] for p in g['points']})
        print(f"RESULT group={key} fit={g['fit']} k_L={g['k_L']:.6f} held_out_points={len(g['points'])} "
              f"held_out_days={days}")
    phot = room_out["cameras"][0]["photometric"]
    print(f"RESULT room photometric status={phot['status']} k_L_by_illuminant={phot.get('k_L_by_illuminant')}")
    for rec in k_records:
        h = rec["checks"]["held_out"]
        print(f"RESULT record {rec['version']}: checks.held_out.result={h['result']} n_points(global)={h['n_points']} "
              f"points_in_this_record={len(h['points'])}")
    return result, k_records, room_out


def main():
    m = syn.photometry_method(k_L_scope="per-illuminant-class")
    room = {"room_id": "den", "version": "den-v4",
            "surfaces": [{"surface_id": "counter-1", "kind": "work surface",
                          "reflectance": {"value": 0.35, "status": "assumed", "calibration_id": None}}],
            "cameras": [{"camera_id": "den-oak", "photometric": None}]}
    failures = 0

    # Scenario 1: bulbs and daylight are fully checked; 'mixed' has one capture, its own fit, nothing held out.
    # The mixed capture's meter reading is 30 % off the bulbs scale: nothing ever compares it with anything.
    caps = [
        cap(1, "20261110", "counter-1", 100.0, E_of(100.0), m, "bulbs", surface=194.44),
        cap(2, "20261110", "desk", 50.0, E_of(50.0), m, "bulbs"),
        cap(3, "20261110", "sofa", 25.0, E_of(25.0), m, "bulbs"),
        cap(4, "20261111", "counter-1", 100.0, E_of(100.0), m, "bulbs"),
        cap(5, "20261111", "counter-1", 80.0, E_of(80.0, 0.12), m, "daylight"),
        cap(6, "20261111", "desk", 40.0, E_of(40.0, 0.12), m, "daylight"),
        cap(7, "20261112", "counter-1", 60.0, 1.3 * E_of(60.0), m, "mixed"),
    ]
    result, k_records, room_out = run("scenario 1: a class with a fit and no held-out check", caps, m, room)
    mixed = result["groups"]["mixed"]
    qualified = (result["gate"] == "pass" and not mixed["points"]
                 and (room_out["cameras"][0]["photometric"].get("k_L_by_illuminant") or {}).get("mixed"))
    states = [card.light_state_record(c, result["groups"][c["illuminant"]]["k_L"], result["anchored"], m,
                                      {"version": "meter-norm/v1", "sha256": "0" * 64}, "den-v4-p1")
              for c in caps if c["illuminant"] == "mixed"]
    print(f"RESULT mixed LightState anchored={states[0]['anchored']} value={states[0]['surface_estimates'][0]['value']:.3f} "
          f"{states[0]['surface_estimates'][0]['unit']}")
    if qualified:
        failures += 1
        print("FAIL scenario 1: gate PASS and k_L[mixed] written as measured with zero held-out points; its "
              "LightState is anchored in lux")
    else:
        print("PASS scenario 1: an unchecked class k_L is not qualified")

    # Scenario 2: every class has same-day held-out points only; no k_L is checked on a later day.
    caps2 = [
        cap(1, "20261110", "counter-1", 100.0, E_of(100.0), m, "bulbs", surface=194.44),
        cap(2, "20261110", "desk", 50.0, E_of(50.0), m, "bulbs"),
        cap(3, "20261110", "sofa", 25.0, E_of(25.0), m, "bulbs"),
        cap(4, "20261111", "counter-1", 80.0, E_of(80.0, 0.12), m, "daylight"),
        cap(5, "20261111", "desk", 40.0, E_of(40.0, 0.12), m, "daylight"),
    ]
    result2, _, _ = run("scenario 2: no class has a held-out day other than its fit day", caps2, m, room)
    cross_day = any(p["day"] != g["first_day"] for g in result2["groups"].values() for p in g["points"])
    if result2["gate"] == "pass" and not cross_day:
        failures += 1
        print("FAIL scenario 2: gate PASS although no k_L was checked on any day but its own fit day")
    else:
        print("PASS scenario 2: held-out days are required per k_L")

    # Control: the same scenario-2 data under single scope (one k_L) is checked across days.
    result3 = card.score(caps2, syn.photometry_method(k_L_scope="single"), ["meter-norm/v1"])
    print(f"RESULT control single scope: gate={result3['gate']} (daylight points now test the day-1 scale)")
    print("RESULT", "FAIL" if failures else "PASS", f"({failures} scenario(s) qualified an unchecked k_L)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

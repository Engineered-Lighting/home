#!/usr/bin/env python3
"""(R7 reproduce lens) End-to-end check of finding per-class-coverage-pooled through photometry.score_root.

Drives the owner flow (--new-day, --capture against the suite's own fake camera on 127.0.0.1, worksheet rows
typed, then --score's score_root) with k_L_scope = per-illuminant-class:

  scenario B: day 1 (bulbs): counter-1 fit reading + counter-1 reflectance (no desk/sofa reading used);
              day 2 (daylight): counter-1, desk, sofa with readings.
  -> does the gate PASS with the bulbs k_L anchored although it has zero held-out checks?
  scenario A: day 1 (bulbs) counter-1, desk, sofa; day 2 (daylight) counter-1, desk, sofa, where the camera
              reads 30% more counts per lux on day 2 (a scale drift). Same frames scored single and per-class.

Synthetic frames only, temp folders only, fake camera bound to 127.0.0.1 only; no real host is contacted.
Run:
    python3 verify_per-class-coverage-pooled.py <root of the branch checkout>
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-photometry"
sys.path[:0] = [str(TOOL), str(ROOT / "tools" / "den-light")]
import synthetic as syn  # noqa: E402
import test_photometry as tp  # noqa: E402  the suite's Workspace, scene, type_row helpers (no tests run)


def run(method, plan):
    fake = syn.FakeCamera(tp.scene(100.0))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = tp.Workspace(tmp, method=method)
            days = {}
            for date, point, value, surface, auto, illuminant, reading in plan:
                if date not in days:
                    days[date] = ws.new_day(date)
                day = days[date]
                fake.scene, fake.auto = tp.scene(value, surface), auto
                folder, record, _ = ws.capture(fake, day, point, illuminant,
                                               blinds="open" if illuminant == "daylight" else "closed",
                                               light="daylight only" if illuminant == "daylight" else "bulbs")
                assert record["outcome"] == "kept", record
                cells = {"card_box": tp.CARD_BOX}
                if reading:
                    cells.update(E_before_lx=reading[0], E_after_lx=reading[1])
                if surface:
                    cells["surface_box"] = tp.SURFACE_BOX
                tp.type_row(day, record["capture"], **cells)
            folder, report = ws.score()
            krecs = [json.loads(l) for l in (folder / "calibration_record.jsonl").read_text().splitlines()]
            states = [json.loads(l) for l in (folder / "light_state.jsonl").read_text().splitlines()]
            room = json.loads((folder / "room_model.jsonl").read_text())
            hosts = {h for h in []}
            return report, krecs, states, room
    finally:
        fake.close()


per = syn.photometry_method(k_L_scope="per-illuminant-class")
single = syn.photometry_method(k_L_scope="single")

# scenario B --------------------------------------------------------------------------------------------
plan_b = [("20261110", "counter-1", 100.0, None, (10, 100), "bulbs", ("174.533", "174.533")),
          ("20261110", "counter-1", 100.0, 194.4444, (6, 100), "bulbs", None),
          ("20261111", "counter-1", 100.0, None, (10, 100), "daylight", ("174.533", "174.533")),
          ("20261111", "desk", 80.0, None, (16, 100), "daylight", ("139.6", "139.7")),
          ("20261111", "sofa", 40.0, None, (32, 100), "daylight", ("69.8", "69.8"))]
report, krecs, states, room = run(per, plan_b)
groups = report["result"]["groups"]
print(f"B per-class: gate {report['gate'].upper()}, anchored {report['result']['anchored']}, "
      f"held_out {report['result']['held_out']['result']} n_points {report['result']['held_out']['n_points']}")
for key, g in sorted(groups.items()):
    print(f"   group {key}: fit {g['fit']} k_L {g['k_L']} held-out points {len(g['points'])}")
bulbs_rec = next(r for r in krecs if r.get("version", "").endswith("-bulbs"))
h = bulbs_rec["checks"]["held_out"]
print(f"   bulbs CalibrationRecord {bulbs_rec['version']}: value {bulbs_rec['value']}, held_out.result {h['result']}, "
      f"n_points {h['n_points']}, own points {len(h['points'])}")
bulbs_states = [s for s in states if s["blinds"] == "closed"]
print("   bulbs LightStates:", [(s["anchored"], s["surface_estimates"][0]["unit"],
                                round(s["surface_estimates"][0]["value"], 2)) for s in bulbs_states])
b_bad = (report["gate"] == "pass" and len(groups["bulbs"]["points"]) == 0 and bulbs_states
         and all(s["anchored"] and s["surface_estimates"][0]["unit"] == "lx" for s in bulbs_states))

# scenario A --------------------------------------------------------------------------------------------
# day 2 camera reads 30% more counts per lux; meter values are the true lux (k = 0.1 on day 1)
lux = lambda C, k: f"{3.141592653589793 * k * C / 0.18:.3f}"  # noqa: E731
k2 = 0.1 / 1.3
plan_a = [("20261110", "counter-1", 100.0, None, (10, 100), "bulbs", (lux(100, .1),) * 2),
          ("20261110", "counter-1", 100.0, 194.4444, (6, 100), "bulbs", None),
          ("20261110", "desk", 50.0, None, (20, 100), "bulbs", (lux(50, .1),) * 2),
          ("20261110", "sofa", 25.0, None, (40, 100), "bulbs", (lux(25, .1),) * 2),
          ("20261111", "counter-1", 100.0, None, (10, 100), "daylight", (lux(100, k2),) * 2),
          ("20261111", "desk", 80.0, None, (16, 100), "daylight", (lux(80, k2),) * 2),
          ("20261111", "sofa", 40.0, None, (32, 100), "daylight", (lux(40, k2),) * 2)]
ra, *_ = run(single, plan_a)
rp, *_ = run(per, plan_a)
worst = max(abs(p["error_lx"]) for g in ra["result"]["groups"].values() for p in g["points"])
print(f"A single:    gate {ra['gate'].upper()}, held_out {ra['result']['held_out']['result']}, worst |e| {worst:.1f} lx")
print(f"A per-class: gate {rp['gate'].upper()}, held_out {rp['result']['held_out']['result']}")
for key, g in sorted(rp["result"]["groups"].items()):
    print(f"   group {key}: fit day {g['first_day']}, held-out days {sorted({p['day'] for p in g['points']})}")
a_bad = ra["gate"] == "fail" and rp["gate"] == "pass" and all(
    {p["day"] for p in g["points"]} <= {g["first_day"]} for g in rp["result"]["groups"].values())

print(f"scenario B (k_L with 0 held-out checks anchored, gate PASS): {'REPRODUCED' if b_bad else 'not reproduced'}")
print(f"scenario A (per-class PASS with only same-day checks; single FAIL): {'REPRODUCED' if a_bad else 'not reproduced'}")
print("RESULT: " + ("DEFECT REPRODUCED" if (b_bad or a_bad) else "not reproduced"))

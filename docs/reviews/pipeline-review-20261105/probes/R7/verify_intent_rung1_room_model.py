#!/usr/bin/env python3
"""(R7 verifier, intent lens) Finding rung1-room-model-has-no-surfaces: README step 9 (--score --room-model)
fed the RoomModel that rung 1 (den-register) writes.

Builds the README's two-day flow with den-photometry's own test fixtures (its FakeCamera on 127.0.0.1 and its
synthetic Stage 0 response, in a temp folder), then scores it twice:
  1. control: the contract's example RoomModel (schemas.json), which lists counter-1 under surfaces;
  2. a RoomModel built by rung 1's own code, records.new_room_model("den") + records.with_camera(...), i.e.
     surfaces [] and one den-oak camera entry, which is what rung 1's room-model.jsonl holds.
Rung 1's checkout is only imported (read-only); nothing in either checkout is written (run with -B).

Run:
    python3 -B verify_intent_rung1_room_model.py <rung-7 checkout root> <rung-1-register checkout root>
"""
import json
import sys
from pathlib import Path

R7 = Path(sys.argv[1]).resolve()
R1 = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(R7 / "tools" / "den-photometry"))
import test_photometry as tp  # noqa: E402  (imports card, photometry, sweep, synthetic)
import sweep  # noqa: E402

sys.path.append(str(R1 / "tools" / "den-register"))
import records as r1  # noqa: E402

case = tp.ScoreCase()
case.setUp()
try:
    with tp.workspace() as ws:
        case.two_days(ws)
        _, report = ws.score()
        print("control (contract example RoomModel): gate", report["gate"])
        entry = {"camera_id": "den-oak", "intrinsics": "EEPROM (nominal)", "pose": {"R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                 "t_m": [0, 0, 0]}, "method": "fit", "version": "2026-11-10.1", "error_bound_m": 0.02,
                 "photometric": dict(r1.PHOTOMETRIC_UNCALIBRATED)}
        rung1_room = r1.with_camera(r1.new_room_model("den"), entry, {"method": "fit", "ts": 0, "confidence": None})
        print("rung-1 RoomModel: version", rung1_room["version"], "surfaces", rung1_room["surfaces"],
              "cameras", [c["camera_id"] for c in rung1_room["cameras"]])
        ws.room.write_text(json.dumps(rung1_room) + "\n", encoding="utf-8")
        before = sorted(p.name for p in ws.root.iterdir())
        try:
            ws.score()
            refused, why = False, "score ran"
        except sweep.Refused as exc:
            refused, why = True, str(exc)
        after = sorted(p.name for p in ws.root.iterdir())
        print("rung-1 RoomModel score:", ("REFUSED: " + why) if refused else why,
              "| new folders:", sorted(set(after) - set(before)))
finally:
    case.doCleanups()
ok = report["gate"] == "pass" and refused and "no surface counter-1" in why
print(("FAIL: a complete, passing run refuses to score with the RoomModel rung 1 writes" if ok
       else "PASS: rung 1's RoomModel is accepted"))
print("RESULT: " + ("DEFECT REPRODUCED" if ok else "not reproduced"))
sys.exit(1 if ok else 0)

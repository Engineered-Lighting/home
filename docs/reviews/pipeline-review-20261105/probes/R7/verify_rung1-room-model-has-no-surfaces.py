#!/usr/bin/env python3
"""Verifier probe for finding rung1-room-model-has-no-surfaces (rung 7 photometry.py:613-616 vs rung 1).

What it checks: README step 9 passes "the den RoomModel" to --score. The only tool that writes a den RoomModel
is rung 1's den-register (records.new_room_model starts with surfaces [], records.with_camera only replaces
the camera entry). The probe builds that RoomModel with rung 1's own functions (camera den-oak added), takes
one kept capture at counter-1 against den-photometry's FakeCamera on 127.0.0.1 with a card box AND a surface
box typed into the worksheet (the README's reflectance pass), and runs photometry.score_root with it.
Control: the same folder scored with the contract's RoomModel example (which lists counter-1).

Synthetic data, temp dirs and 127.0.0.1 only; nothing in either checkout is written (run with python3 -B).
Run:
    python3 -B verify_rung1-room-model-has-no-surfaces.py <rung-7 checkout root> <rung-1 checkout root>
"""
import csv
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
R1 = Path(sys.argv[2]).resolve()
TOOL = ROOT / "tools" / "den-photometry"
sys.path.insert(0, str(TOOL))
import card  # noqa: E402,F401
import photometry  # noqa: E402
import sweep  # noqa: E402
import synthetic as syn  # noqa: E402

sys.path.append(str(R1 / "tools" / "den-register"))
spec = importlib.util.spec_from_file_location("r1records", R1 / "tools" / "den-register" / "records.py")
r1records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r1records)

with tempfile.TemporaryDirectory(prefix="r7verify-room-") as tmp:
    tmp = Path(tmp)
    root = tmp / "den-photometry"
    root.mkdir()
    token = tmp / "private" / "camera-token.txt"
    token.parent.mkdir()
    token.write_text(syn.TOKEN)
    stage0 = tmp / "den-light" / "stage0-20261108" / "sweep-1"
    stage0.mkdir(parents=True)
    den, m = syn.den_method(), syn.photometry_method()
    (stage0 / "response.json").write_text(json.dumps(syn.stage0_response(den)), encoding="utf-8")
    # rung 1's RoomModel: new_room_model + with_camera (the den camera's registration)
    base = r1records.new_room_model("den")
    rung1_room = r1records.with_camera(base, {"camera_id": m["camera_id"], "version": "2026-11-01.1"},
                                       {"method": "synthetic"})
    room1 = tmp / "room-model.jsonl"
    room1.write_text(json.dumps(rung1_room) + "\n", encoding="utf-8")
    print("rung-1 RoomModel surfaces:", rung1_room["surfaces"], "cameras:",
          [c["camera_id"] for c in rung1_room["cameras"]])
    schemas = json.loads((ROOT / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
    example = next(r for r in schemas["records"] if r["id"] == "room_model")["example"]
    room_ex = tmp / "room_model_example.jsonl"
    room_ex.write_text(json.dumps(example) + "\n", encoding="utf-8")
    day = photometry.new_day(root, stage0, token, m, den, "20261110")
    fake = syn.FakeCamera({"background": 20.0, "rects": [((300, 150, 339, 189), 100.0),
                                                          ((400, 150, 439, 189), 150.0)]})
    try:
        _, rec, _ = photometry.capture(day, sweep.Camera(fake.url, syn.TOKEN), token, m, den, point="counter-1",
                                       illuminant="bulbs", blinds="closed", light="four bulbs", say=lambda *a: None)
        print("capture 1:", rec["outcome"])
    finally:
        fake.close()
    table = list(csv.reader((day / "worksheet.csv").read_text(encoding="utf-8").splitlines()))
    table[1][6:10] = ["174.533", "174.533", "305 155 334 184", "405 155 434 184"]
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(table)
    (day / "worksheet.csv").write_bytes(out.getvalue().encode())
    try:
        _, report = photometry.score_root(root, room_ex, token, m, den, say=lambda *a: None)
        print("control (contract example RoomModel): score ran, gate", report["gate"],
              "reflectance", {k: round(v["rho"], 4) for k, v in report["result"]["reflectance"].items()})
    except sweep.Refused as exc:
        print("control (contract example RoomModel): REFUSED:", exc)
    try:
        photometry.score_root(root, room1, token, m, den, say=lambda *a: None)
        blocked, why = False, "score ran"
    except sweep.Refused as exc:
        blocked, why = True, str(exc)
    print("rung-1 RoomModel:", ("REFUSED: " + why) if blocked else why)
    print("score folders written:", sorted(p.name for p in root.iterdir() if p.name.startswith("score-")))
    print("RESULT: " + ("DEFECT REPRODUCED (rung 1's RoomModel blocks the score)" if blocked else "not reproduced"))

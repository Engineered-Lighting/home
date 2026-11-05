#!/usr/bin/env python3
"""probe_rung1_entry_refused: can den-observe read the RoomModel camera entry rung 1 writes?

What it checks
    Rung 1 (tools/den-register, branch claude/rung-1-register) writes
    room-model.jsonl -- every RoomModel revision, one per line, the last one
    current (records.py:5) -- and its camera entry as register.py:338-350
    builds it: intrinsics {source, frame, width, height, fx, fy, cx, cy},
    pose {room_from_camera, camera_frame: records.CAMERA_FRAME, ...}.
    The probe writes, with synthetic numbers and that shape:
      A  a two-line room-model.jsonl (an empty revision, then one with the camera)
      B  only its last line, as a single JSON document
    and asks den-observe to resolve den-oak's newest transform_id and build its
    Camera (roommodel.load / current_transform_id / Camera), printing what happens.
    The first arg is the den-observe checkout; an optional second arg is the
    rung-1 checkout, used only to read its CAMERA_FRAME string (read-only).

How to run
    python3 probe_rung1_entry_refused.py <rung-2 checkout root> [<rung-1 checkout root>]

Synthetic data only, in a temporary folder that is removed afterwards.
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import roommodel  # noqa: E402

camera_frame = "rectified_left: +x right, +y down, +z forward, metres"
if len(sys.argv) > 2:
    text = (Path(sys.argv[2]) / "tools" / "den-register" / "records.py").read_text(encoding="utf-8")
    camera_frame = re.search(r'^CAMERA_FRAME = "([^"]*)"', text, re.M)[1]

entry = {"camera_id": "den-oak",
         "intrinsics": {"source": "EEPROM, as the bundle's depth frame carries it", "frame": "rectified_left",
                        "width": 640, "height": 400, "fx": 450.0, "fy": 450.0, "cx": 320.0, "cy": 200.0},
         "pose": {"room_from_camera": [[1, 0, 0, 1.0], [0, 0, 1, 0.2], [0, -1, 0, 2.2], [0, 0, 0, 1]],
                  "camera_frame": camera_frame, "room_frame": "z_up_metric_floor0", "unit": "m",
                  "position_m": [1.0, 0.2, 2.2]},
         "method": "register-v1", "version": "2026-11-12", "error_bound_m": 0.02,
         "photometric": {"k_L": None, "unit": "(cd/m²)/nc", "normalisation_version": None,
                         "calibration_id": None, "status": "uncalibrated"},
         "calibration_id": "cal-den-oak-pose-2026-11-12"}
first = {"room_id": "den", "version": "den-r1", "frame": {"name": "z_up_metric_floor0", "unit": "m"},
         "surfaces": [], "cameras": [], "fixtures": [], "landmarks": [], "provenance": {}}
second = dict(first, version="den-r2", cameras=[entry])

work = Path(tempfile.mkdtemp(prefix="probe-rung1-entry-"))
refused = 0
try:
    jsonl = work / "room-model.jsonl"
    jsonl.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
    single = work / "room-model.json"
    single.write_text(json.dumps(second), encoding="utf-8")
    for label, path in (("A room-model.jsonl as rung 1 writes it", jsonl), ("B its last line alone", single)):
        try:
            model = roommodel.load(path)
            tid = roommodel.current_transform_id(model, "den-oak")
            roommodel.Camera(model, tid, max_error_bound_m=0.05)
            print(f"PASS {label}: resolved {tid}")
        except roommodel.RegistrationRefused as exc:
            refused += 1
            print(f"FAIL {label}: refused -- {exc}")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if refused else 'PASS'}: {refused} of 2 rung-1-shaped RoomModels refused by den-observe")

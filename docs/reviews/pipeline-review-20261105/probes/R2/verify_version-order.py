#!/usr/bin/env python3
"""verify_version-order: does den-observe's string-max version pick go wrong on what rung 1 really writes?

A. The reported case: one RoomModel object holding both den-oak@D.9 and den-oak@D.10
   (hand-made). Shows the code defect in isolation: max() by string picks .9.
B. Rung 1's real shape: ten registrations built with rung 1's own records.next_version and
   records.with_camera (tools/den-register, the 9th and 10th on one date), appended to
   room-model.jsonl with records.append_jsonl, exactly as register.build_records does. Then:
   B1 den-observe reads the whole room-model.jsonl;
   B2 den-observe reads the current revision (the last line) saved as a .json file;
   B3 (hypothetical) every revision's den-oak entries flattened into one RoomModel.
RESULT lines say which version den-observe treats as current in each case.

    python3 -B verify_version-order.py <rung-2-observe checkout root> [<rung-1-register checkout root>]

Synthetic data only, temp dirs only, no network. Neither checkout is modified (run with -B).
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

r2 = Path(sys.argv[1]).resolve()
r1 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else r2.parent / "rung-1-register"
sys.path.insert(0, str(r2 / "tools" / "den-observe"))
import roommodel  # noqa: E402
import synth  # noqa: E402

# A
model = synth.room_model([synth.camera_entry(version="2026-12-01.9"), synth.camera_entry(version="2026-12-01.10")])
print(f"RESULT A hand-made RoomModel with .9 and .10: current -> {roommodel.current_transform_id(model, 'den-oak')}")

# B
sys.path.insert(0, str(r1 / "tools" / "den-register"))
import records  # noqa: E402  (rung 1)

work = Path(tempfile.mkdtemp(prefix="verify-version-order-"))
try:
    path = work / "room-model.jsonl"
    dates = ["2026-11-%02d" % (10 + i) for i in range(8)] + ["2026-12-01", "2026-12-01"]
    for date in dates:
        room_models = records.read_jsonl(path, "room_model_unreadable")
        base = records.current(room_models, "den")
        version = records.next_version(room_models, "den-oak", date)
        entry = synth.camera_entry(version=version)
        records.append_jsonl(path, records.with_camera(base, entry, {"method": "synthetic"}))
    revisions = records.read_jsonl(path, "room_model_unreadable")
    last = revisions[-1]
    oak = [c["version"] for c in last["cameras"] if c["camera_id"] == "den-oak"]
    print(f"RESULT B rung 1 wrote {len(revisions)} revisions; versions over the file: "
          f"{[c['version'] for _, c in records.entries(revisions, 'den-oak')][-3:]}; "
          f"den-oak entries in the current revision: {oak}")
    try:
        roommodel.load(path)
        print("RESULT B1 whole room-model.jsonl: loaded")
    except roommodel.RegistrationRefused as exc:
        print(f"RESULT B1 whole room-model.jsonl: refused before any version compare: {exc}".replace(str(work), "<tmp>"))
    single = work / "room-model.json"
    single.write_text(json.dumps(last), encoding="utf-8")
    current = roommodel.current_transform_id(roommodel.load(single), "den-oak")
    print(f"RESULT B2 current revision as .json: current -> {current}")
    flat = synth.room_model([c for _, c in records.entries(revisions, "den-oak")])
    print(f"RESULT B3 (hypothetical) all revisions flattened: current -> "
          f"{roommodel.current_transform_id(flat, 'den-oak')}")
finally:
    shutil.rmtree(work, ignore_errors=True)

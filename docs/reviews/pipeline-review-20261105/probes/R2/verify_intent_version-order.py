#!/usr/bin/env python3
"""verify_intent_version-order: can rung 1's real output ever put .9 and .10 of one camera in one RoomModel?

Builds ten same-day den-oak registrations with rung 1's own records.next_version and
records.with_camera (synthetic entries, temp dir only), then:
  1. checks every RoomModel revision holds exactly one den-oak entry;
  2. hands the newest revision (a single JSON object, the only form den-observe's
     roommodel.load can read) to den-observe and prints the transform_id it picks;
  3. hands den-observe the whole multi-line room-model.jsonl and prints what happens.

    python3 -B verify_intent_version-order.py <rung-2-observe root> <rung-1-register root>
"""
import json
import sys
import tempfile
from pathlib import Path

r2, r1 = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(r1 / "tools" / "den-register"))
import records  # rung 1 (read-only import)
sys.path.insert(0, str(r2 / "tools" / "den-observe"))
import roommodel  # rung 2 (read-only import)

work = Path(tempfile.mkdtemp(prefix="vi-version-order-"))
revisions = []
base = records.new_room_model("den")
for i in range(10):
    version = records.next_version(revisions, "den-oak", "2026-12-01")
    entry = {"camera_id": "den-oak", "version": version, "error_bound_m": 0.03}
    base = records.with_camera(base, entry, {"method": "synthetic"})
    revisions.append(base)
counts = [sum(1 for c in rm["cameras"] if c.get("camera_id") == "den-oak") for rm in revisions]
print("RESULT den-oak entries per revision:", counts)
print("RESULT versions written:", [rm["cameras"][-1]["version"] for rm in revisions])
newest = work / "room-model.json"
newest.write_text(json.dumps(revisions[-1]), encoding="utf-8")
print("RESULT den-observe current_transform_id on the newest revision ->",
      roommodel.current_transform_id(roommodel.load(newest), "den-oak"))
whole = work / "room-model.jsonl"
whole.write_text("".join(json.dumps(rm) + "\n" for rm in revisions), encoding="utf-8")
try:
    roommodel.load(whole)
    print("RESULT whole room-model.jsonl: loaded")
except roommodel.RegistrationRefused as exc:
    print("RESULT whole room-model.jsonl: refused:", exc)
ok = all(c == 1 for c in counts)
print("PASS rung 1 never puts two entries of one camera in a revision; .9/.10 cannot meet in den-observe's max()"
      if ok else "FAIL a revision holds two entries of one camera")

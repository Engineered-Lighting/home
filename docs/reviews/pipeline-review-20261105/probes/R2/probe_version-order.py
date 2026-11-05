#!/usr/bin/env python3
"""probe_version-order: does den-observe pick the newest registration under rung 1's version format?

den-observe takes the newest camera entry as max(version) by string order
("versions are ISO dates"). Rung 1 (tools/den-register records.next_version)
writes `YYYY-MM-DD.N`, with N counting ALL of the camera's registrations so far.
After nine registrations, two on the same day give `...-01.9` then `...-01.10`.
PASS = the newest (.10) is chosen and .9 is refused as stale; FAIL = .9 is
chosen as current and the real newest (.10) is refused as "stale".

    python3 probe_version-order.py <rung-2-observe checkout root>

No files, no network.
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import roommodel  # noqa: E402
import synth  # noqa: E402

model = synth.room_model([synth.camera_entry(version="2026-12-01.9"), synth.camera_entry(version="2026-12-01.10")])
current = roommodel.current_transform_id(model, "den-oak")
print(f"RESULT current_transform_id -> {current}")
try:
    roommodel.Camera(model, "den-oak@2026-12-01.10", max_error_bound_m=0.05)
    print("RESULT den-oak@2026-12-01.10 accepted")
    newest_ok = True
except roommodel.RegistrationRefused as exc:
    print(f"RESULT den-oak@2026-12-01.10 refused: {exc}")
    newest_ok = False
if current != "den-oak@2026-12-01.10" or not newest_ok:
    print("FAIL: string max picks the older .9 registration; the newer .10 is refused as stale")
    sys.exit(1)
print("PASS")

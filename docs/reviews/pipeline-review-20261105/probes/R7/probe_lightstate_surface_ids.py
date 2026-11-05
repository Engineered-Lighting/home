#!/usr/bin/env python3
"""probe_lightstate_surface_ids: (1) does --score write LightState.surface_estimates[].surface_id values
that the RoomModel it was given has no surface for (the free-form --point names, e.g. desk and sofa)?
(2) does one interrupted capture folder (frames but no capture.json, as a closed window or a power cut
leaves it) block every later --score?

End to end through photometry.capture / photometry.score_root with the branch's FakeCamera on 127.0.0.1,
synthetic frames and the schemas.json RoomModel example, in a temp folder.

    python3 -B probe_lightstate_surface_ids.py <branch-root>
"""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import test_photometry as tp  # noqa: E402
import synthetic as syn  # noqa: E402
import photometry  # noqa: E402

bad = 0
fake = syn.FakeCamera(tp.scene(100.0))
case = tp.ScoreCase("run")
case.fake = fake
try:
    with tp.workspace() as ws:
        case.two_days(ws)
        folder, report = ws.score()
        room_in = json.loads(ws.room.read_text().splitlines()[-1])
        surfaces = {s["surface_id"] for s in room_in["surfaces"]}
        states = [json.loads(l) for l in (folder / "light_state.jsonl").read_text().splitlines()]
        ids = sorted({e["surface_id"] for s in states for e in s["surface_estimates"]})
        unresolved = [i for i in ids if i not in surfaces]
        print(f"RESULT gate={report['gate']} RoomModel surfaces={sorted(surfaces)} LightState surface_ids={ids}")
        if unresolved:
            bad += 1
            print(f"FAIL LightState surface_ids with no RoomModel surface, written without a warning: {unresolved} "
                  f"(anchored={[s['anchored'] for s in states][:1]}, unit={states[0]['surface_estimates'][0]['unit']})")
        else:
            print("PASS every LightState surface_id resolves in the RoomModel")
        # (2) an interrupted capture: frames written, no capture.json
        day = sorted(p for p in ws.root.iterdir() if p.name.startswith("day-"))[-1]
        orphan = day / "capture-99"
        (orphan / "frames").mkdir(parents=True)
        (orphan / "frames.jsonl").write_text("")
        try:
            ws.score()
            print("PASS an interrupted capture folder does not block the score")
        except photometry.Refused as exc:
            print(f"RESULT --score after one interrupted capture: Refused: {exc}")
            readme = (ROOT / "tools/den-photometry/README.md").read_text(encoding="utf-8")
            hint = "capture.json" in readme and ("delete" in readme.lower())
            print(f"RESULT README tells the owner what to do with such a folder: {hint}")
            bad += 1
            print("FAIL one interrupted capture blocks every later score until the folder is removed by hand")
finally:
    fake.close()
print("RESULT", "FAIL" if bad else "PASS")
sys.exit(1 if bad else 0)

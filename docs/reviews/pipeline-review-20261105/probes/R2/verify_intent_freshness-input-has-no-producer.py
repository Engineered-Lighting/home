#!/usr/bin/env python3
"""verify_intent_freshness-input-has-no-producer: can rows on the live loop's Unix clock feed a take replay?

den-detections-1 names two clocks: host_monotonic (a take's t_host) and unix ("the live
loop's observed_at"). This builds a synthetic take with a PASS slate check, reads the
take's own offset (unix = t_host + offset_s), and writes fake-detector rows on the unix
clock whose capture_ts is exactly t_host + offset_s and available_ts 0.3 s later. It runs
replay unscored and prints whether the rows are accepted. It also greps the checkout for
any caller of detections.from_perception_lab outside the tests.

    python3 -B verify_intent_freshness-input-has-no-producer.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; no network.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
here = root / "tools" / "den-observe"
sys.path.insert(0, str(here))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402
import takes  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="vi-fresh-"))
try:
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(3)]
    folder = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(folder)
    clock = takes.Take(folder).clock()
    print(f"RESULT take clock source {clock['source']}, offset_s {clock['offset_s']}")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = []
    for i, (t, _) in enumerate(pairs):
        row = fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels)
        row["frame"].update(clock="unix", capture_ts=t + clock["offset_s"])
        row["available_ts"] = t + clock["offset_s"] + 0.3
        rows.append(row)
    path = work / "rows-unix.jsonl"
    detections.write_rows(path, rows)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    try:
        replay.replay(folder, room, work / "out", detections_path=path, log=lambda *a: None)
        print("RESULT unix rows aligned through the take's own offset: accepted")
    except replay.Refused as exc:
        print(f"RESULT unix rows aligned through the take's own offset: refused: {exc}")
finally:
    shutil.rmtree(work, ignore_errors=True)
callers = [str(p.relative_to(root)) for p in root.rglob("*.py")
           if "__pycache__" not in p.parts and not p.name.startswith("test_")
           and "from_perception_lab(" in p.read_text(encoding="utf-8", errors="replace")
           and p.name != "detections.py"]
print(f"RESULT non-test callers of from_perception_lab: {callers or 'none'}")

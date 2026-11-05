#!/usr/bin/env python3
"""probe_freshness-counts-observations: is the expired fraction per detector result, as R5's baseline is?

R5's baseline is 115 of 228 detector RESULTS expired. replay.gate counts
expired OBSERVATIONS: a row with no detections contributes nothing, a row with
5 detections counts 5 times. This probe runs a scored replay (every slot closed
at its proposed value: max age 1.0 s, max expired fraction 0.25) on 4 rows:
two late rows (1.5 s) with an empty frame, two on-time rows (0.3 s) with a
person. Per result 2 of 4 expired (0.5, above 0.25); per observation 0 of 2.
PASS = the freshness gate fails; FAIL = scored PASS while half the results expired.

    python3 probe_freshness-counts-observations.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; connect is allowed to 127.0.0.1 only.
"""
import json
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
here = root / "tools" / "den-observe"
sys.path.insert(0, str(here))
_real = socket.socket.connect
socket.socket.connect = lambda s, a, *k, **kw: (_real(s, a, *k, **kw) if a[0] == "127.0.0.1"
                                                else (_ for _ in ()).throw(AssertionError(a)))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-fresh-"))
try:
    text = (here / "METHOD.md").read_text(encoding="utf-8")
    method = work / "METHOD.md"
    method.write_text(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}", text,
                             flags=re.M), encoding="utf-8")
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(4)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = []
    for i, (t, _) in enumerate(pairs):
        late = i % 2 == 0
        row = fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, latency_s=1.5 if late else 0.3)
        if late:
            row = dict(row, detections=[])  # an empty frame, completed late
        rows.append(row)
    path = work / "rows.jsonl"
    detections.write_rows(path, rows)
    run = replay.replay(take, room, work / "out", detections_path=path, scored=True, method_path=method,
                        log=lambda *a: None)
    f = run["gate"]["freshness"]
    per_result = sum(1 for r in rows if r["available_ts"] - r["frame"]["capture_ts"] > 1.0) / len(rows)
    print(f"RESULT per-result expired fraction {per_result:.2f}; gate expired_fraction {f['expired_fraction']} "
          f"({f['expired']} of {f['judged']}), limit {f['max_expired_fraction']}; gate pass {run['gate']['pass']} scored {run['scored']}")
finally:
    shutil.rmtree(work, ignore_errors=True)
if run["gate"]["pass"]:
    print("FAIL: the freshness gate passes on observations while half of the detector results expired")
    sys.exit(1)
print("PASS")

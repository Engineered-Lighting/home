#!/usr/bin/env python3
"""probe_freshness_per_result: the scored freshness gate counts Observations, not detector results.

What it checks
    Ladder rung 2: "Detector freshness: ... set one before the run, with R5's
    expiry rate as the baseline." notes.json R5: "115 of 228 detector results
    expired" -- a rate per detector result (one result per frame the detector
    ran on). den-observe's gate (replay.gate) divides expired Observations by
    judged Observations and compares that with max_expired_fraction and with
    R5's 115/228.
    The probe builds a synthetic take of 8 frames with closed slots (every open
    slot set to its proposed value: detector_max_age_s 1.0,
    max_expired_fraction 0.25) and a PASS check, and 8 detector rows:
      4 rows: one person, result complete 0.3 s after capture (fresh)
      4 rows: no detections, result complete 2.0 s after capture (expired)
    Per detector result 4 of 8 expired (0.5, the R5 rate); the probe runs a
    scored replay and prints the gate's expired_fraction and verdict.
    A second variant puts 3 detections (person + 2 objects) in each of 2 fresh
    rows and 1 person in each of 2 expired rows: per result 2/4 = 0.5 expired,
    per Observation 2/8 = 0.25.

How to run
    python3 probe_freshness_per_result.py <branch checkout root>

Synthetic data only, in a temporary folder that is removed afterwards.
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-observe"
sys.path.insert(0, str(tool))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-fresh-"))
fails = 0
try:
    text = (tool / "METHOD.md").read_text(encoding="utf-8")
    closed = work / "METHOD.md"
    closed.write_text(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2", text, flags=re.M),
                      encoding="utf-8")
    scene = synth.Scene()
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    depth = scene.depth_mm()

    def scored_run(name, rows, pairs):
        folder = work / name
        take = synth.make_take(folder / "take", pairs, depth)
        synth.write_check(take)
        room = folder / "room.json"
        room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
        detections.write_rows(folder / "rows.jsonl", rows)
        code = replay.main(["--take", str(take), "--room-model", str(room), "--detections", str(folder / "rows.jsonl"),
                            "--out", str(folder / "out"), "--scored", "--method", str(closed)])
        return code, json.loads((folder / "out" / "run.json").read_text(encoding="utf-8"))["gate"]

    # variant 1: empty-result rows expire, rows with a person are fresh
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(8)]
    rows = []
    for i, (t, _) in enumerate(pairs):
        r = fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, latency_s=0.3 if i % 2 == 0 else 2.0)
        if i % 2:
            r["detections"] = []
        rows.append(r)
    import contextlib, io  # noqa: E401
    with contextlib.redirect_stdout(io.StringIO()):
        code, gate = scored_run("v1", rows, pairs)
    per_result = sum(1 for r in rows if r["available_ts"] - r["frame"]["capture_ts"] > 1.0) / len(rows)
    f = gate["freshness"]
    print(f"INFO variant 1: per detector result {per_result:.3f} expired (R5 measured 0.504); gate judged "
          f"{f['judged']} Observations, expired_fraction {f['expired_fraction']}, limit {f['max_expired_fraction']}, "
          f"gate pass {gate['pass']}, exit code {code}")
    bad = gate["pass"] and per_result > f["max_expired_fraction"]
    fails += bad
    print(f"{'FAIL' if bad else 'PASS'} variant 1: a scored run where half the detector results expired "
          f"{'PASSES' if gate['pass'] else 'fails'} the freshness gate")

    # variant 2: crowded fresh rows dilute expired ones
    pairs = [(6000.0 + 0.25 * i, 6000.0 + 0.25 * i - 0.004) for i in range(4)]
    rows = []
    for i, (t, _) in enumerate(pairs):
        late = i >= 2
        r = fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, latency_s=2.0 if late else 0.3)
        if not late:
            x0, y0, x1, y1 = r["detections"][0]["box_xyxy"]
            r["detections"] += [{"id": f"{i}:{k}", "class": "cup", "score": 0.5, "keypoints": None,
                                 "box_xyxy": [x0 + 20, y0 + 60 + 10 * k, x1 - 20, y0 + 120 + 10 * k]} for k in (1, 2)]
            r["detector"] = dict(r["detector"])
        rows.append(r)
    with contextlib.redirect_stdout(io.StringIO()):
        code, gate = scored_run("v2", rows, pairs)
    f = gate["freshness"]
    per_result = 2 / 4
    print(f"INFO variant 2: per detector result {per_result:.3f} expired; gate expired_fraction "
          f"{f['expired_fraction']} over {f['judged']} Observations, gate pass {gate['pass']}, exit code {code}")
    bad = gate["pass"] and per_result > f["max_expired_fraction"]
    fails += bad
    print(f"{'FAIL' if bad else 'PASS'} variant 2: per-result 0.5 expired, gate {'PASS' if gate['pass'] else 'FAIL'}")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} variants where the gate passes while more than "
      f"max_expired_fraction of detector results expired")

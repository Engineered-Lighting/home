#!/usr/bin/env python3
"""probe_check-cuts-ignored: does den-observe honour the cuts in the take's PASS check?

den-take's check.py (rung R) writes check-N.json with `cuts` (kind gap, stale =
repeated RGB frame, second_person = owner-marked spans with another person in
view) and `usable.segments`; the frames stay on disk and only the check says
they are not part of the retained session. This probe writes a PASS check in
den-take's shape with a second_person cut over t_s 0.9-1.6 s and a stale cut
over 0.4-0.6 s, then replays with detector rows for every frame.
PASS = no Observation is written for a frame inside a cut (or the run refuses);
FAIL = valid Observations are written for cut frames.

    python3 probe_check-cuts-ignored.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; connect is allowed to 127.0.0.1 only.
"""
import json
import shutil
import socket
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
_real = socket.socket.connect
socket.socket.connect = lambda s, a, *k, **kw: (_real(s, a, *k, **kw) if a[0] == "127.0.0.1"
                                                else (_ for _ in ()).throw(AssertionError(a)))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-cuts-"))
try:
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(8)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    path = synth.write_check(take)
    check = json.loads(path.read_text())

    def cut(kind, a, b):
        return {"kind": kind, "start_t_s": a, "end_t_s": b, "start": "00:00", "end": "00:01", "seconds": b - a}
    cuts = [cut("stale", 0.4, 0.6), cut("second_person", 0.9, 1.6)]
    check.update(cuts=cuts, cut_count=2, second_person={"asked": True, "typed": "00:01-00:01"},
                 usable={"start_t_s": 0.0, "end_t_s": 1.75, "segments": [
                     {"start_t_s": 0.0, "end_t_s": 0.4}, {"start_t_s": 0.6, "end_t_s": 0.9},
                     {"start_t_s": 1.6, "end_t_s": 1.75}], "minutes": 0.015})
    path.write_text(json.dumps(check), encoding="utf-8")
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)])
    run = replay.replay(take, room, work / "out", detections_path=rows, log=lambda *a: None)
    obs = [json.loads(l) for l in (work / "out" / "observations.jsonl").read_text().splitlines()]
    first = pairs[0][0] + run["clock"]["offset_s"]
    inside = [o for o in obs if any(c["start_t_s"] <= o["capture_ts"] - first < c["end_t_s"] for c in cuts)]
    valid_inside = [o for o in inside if o["validity"] == "valid"]
    print(f"RESULT {len(obs)} observations; {len(inside)} inside the check's cuts, {len(valid_inside)} of them valid; "
          f"kinds inside: {sorted({c['kind'] for c in cuts for o in inside if c['start_t_s'] <= o['capture_ts'] - first < c['end_t_s']})}")
    print(f"RESULT run.json mentions cuts: {'cuts' in json.dumps(run)}; gate pass {run['gate']['pass']}")
finally:
    shutil.rmtree(work, ignore_errors=True)
if valid_inside:
    print("FAIL: Observations are written as valid for frames the take's PASS check cut (stale and second_person)")
    sys.exit(1)
print("PASS")

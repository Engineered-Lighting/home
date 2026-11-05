#!/usr/bin/env python3
"""probe_contract-numbers-not-pinned: can a scored run pass with the contracts' own numbers changed?

METHOD.md's block holds numbers fixed by the contracts (max_depth_gap_s 0.067 =
schemas.json's one stereo frame; max_error_bound_m 0.05 = ladder rung 1's 5 cm)
next to the owner's open slots, and replay.py --method takes any METHOD.md.
This probe closes every open slot at its proposed value (as the tests do), then
also sets max_depth_gap_s = 0.5 and max_error_bound_m = 0.5, and runs a scored
replay on a take whose depth is 0.2 s older than its RGB with a 0.3 m camera
error bound. PASS = refused; FAIL = scored PASS with stale depth lifted as stereo.

    python3 probe_contract-numbers-not-pinned.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; connect is allowed to 127.0.0.1 only.
"""
import contextlib
import io
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

work = Path(tempfile.mkdtemp(prefix="probe-pinned-"))
try:
    text = (here / "METHOD.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}", text, flags=re.M)
    text = text.replace("max_depth_gap_s = 0.067", "max_depth_gap_s = 0.5").replace(
        "max_error_bound_m = 0.05", "max_error_bound_m = 0.5")
    method = work / "METHOD.md"
    method.write_text(text, encoding="utf-8")
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.2) for i in range(3)]  # depth 200 ms older than RGB
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model([synth.camera_entry(error_bound_m=0.3)])), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)])
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = replay.main(["--take", str(take), "--room-model", str(room), "--detections", str(rows),
                            "--out", str(work / "out"), "--scored", "--method", str(method)])
    print(f"RESULT exit {code}; last line: {printed.getvalue().strip().splitlines()[-1] if printed.getvalue() else ''}")
    if code == 2:
        print("PASS: refused")
        sys.exit(0)
    run = json.loads((work / "out" / "run.json").read_text())
    obs = [json.loads(l) for l in (work / "out" / "observations.jsonl").read_text().splitlines()]
    lifts = {e["lift"] for o in obs for e in o["joints_room"]}
    print(f"RESULT scored {run['scored']}, gate pass {run['gate']['pass']}, depth {run['gate']['counts']['depth']}, "
          f"depth_gap_s {obs[0]['freshness']['depth_gap_s']}, lifts {sorted(lifts)}, "
          f"values_used max_depth_gap_s {run['method']['values_used']['max_depth_gap_s']}, "
          f"max_error_bound_m {run['method']['values_used']['max_error_bound_m']}")
    if run["scored"] and run["gate"]["pass"]:
        print("FAIL: a scored run PASSes with the contract's 67 ms depth limit and 5 cm bound replaced; "
              "200 ms-old depth is lifted as stereo")
        sys.exit(1)
    print("PASS")
finally:
    shutil.rmtree(work, ignore_errors=True)

#!/usr/bin/env python3
"""verify_check-cuts-ignored: commissioning half of finding check-cuts-ignored.

CHECK-V1.md: "A retained session is a take whose newest check-N.json says PASS
and which is not a commissioning take. Only retained sessions go to N1 and N2."
check.py writes "commissioning" and "retained" into check-N.json. This probe
builds a synthetic commissioning take (take.json commissioning true) whose
newest check PASSes with retained false, closes every METHOD.md slot at its
proposed value, and runs replay.py --scored with detector rows. It also checks
whether a second_person cut on the only frames changes anything.

    python3 verify_check-cuts-ignored.py <rung-2-observe root>

PASS = the scored run is refused; FAIL = a scored run of a non-retained take is
accepted. Synthetic data only, temp dirs only; connect allowed to 127.0.0.1 only.
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

work = Path(tempfile.mkdtemp(prefix="verify-cuts-"))
code = 0
try:
    text = (here / "METHOD.md").read_text(encoding="utf-8")
    method = work / "METHOD.md"
    method.write_text(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}", text,
                             flags=re.M), encoding="utf-8")
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(3)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    record = json.loads((take / "take.json").read_text(encoding="utf-8"))
    record["commissioning"] = True
    (take / "take.json").write_text(json.dumps(record), encoding="utf-8")
    path = synth.write_check(take)
    check = json.loads(path.read_text())
    check.update(commissioning=True, retained=False,
                 cuts=[{"kind": "second_person", "start_t_s": 0.0, "end_t_s": 1.0, "start": "00:00", "end": "00:01",
                        "seconds": 1.0}], cut_count=1,
                 usable={"start_t_s": 0.0, "end_t_s": 0.5, "segments": [], "minutes": 0.0})
    path.write_text(json.dumps(check), encoding="utf-8")
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, latency_s=0.3)
                                 for i, (t, _) in enumerate(pairs)])
    try:
        run = replay.replay(take, room, work / "out", detections_path=rows, scored=True, method_path=method,
                            log=lambda *a: None)
    except replay.Refused as exc:
        print(f"RESULT scored run refused: {exc}")
        print("PASS")
    else:
        obs = [json.loads(x) for x in (work / "out" / "observations.jsonl").read_text().splitlines()]
        print(f"RESULT scored {run['scored']}, gate pass {run['gate']['pass']}, {len(obs)} obs "
              f"({sum(o['validity'] == 'valid' for o in obs)} valid) from a take with commissioning=true, "
              f"retained=false, usable segments [] and every frame inside a second_person cut")
        print(f"RESULT run.json take_check keys: {sorted(run['take_check'])}")
        print("FAIL: a non-retained (commissioning) take, entirely cut, is accepted for a scored run")
        code = 1
finally:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(code)

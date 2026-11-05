#!/usr/bin/env python3
"""verify_tiebreak_contract-numbers-not-pinned: what does overriding the contract lines in METHOD.md change?

Tiebreak probe for finding contract-numbers-not-pinned (rung 2, den-observe).
It runs four scored replays on synthetic takes, each with every open slot
closed at its proposed value (as the tests' closed_method does):

  A  committed contract lines (0.067 s, 0.05 m), depth 0.2 s older than RGB, bound 0.03 m
  B  as A, but max_depth_gap_s = 0.5 in a --method copy
  C  committed contract lines, camera bound 0.3 m
  D  as C, but max_error_bound_m = 0.5 in a --method copy

and reports for each: exit code, the console's last line, the gate verdict,
depth flags, the joint lifts, and what run.json's method record says (file
name, sha256_lf versus the committed METHOD.md's, values_used). It also checks
whether the console output names the method file or any changed value.

    python3 verify_tiebreak_contract-numbers-not-pinned.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; connect is allowed to 127.0.0.1 only.
Prints RESULT lines; it does not judge, the verdict is in the review.
"""
import contextlib
import hashlib
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
import method as method_file  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

committed = method_file.load()
print(f"RESULT committed METHOD.md sha256_lf {committed.sha256[:16]}..., open slots {len(committed.open)}")

work = Path(tempfile.mkdtemp(prefix="verify-pinned-"))


def closed(name, replace=()):
    text = (here / "METHOD.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}", text, flags=re.M)
    for old, new in replace:
        assert old in text, old
        text = text.replace(old, new)
    folder = work / name
    folder.mkdir()
    path = folder / "METHOD.md"
    path.write_text(text, encoding="utf-8")
    return path


def run(label, method_path, depth_lag_s, bound_m):
    case = work / f"case-{label}"
    case.mkdir()
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - depth_lag_s) for i in range(3)]
    take = synth.make_take(case / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = case / "room.json"
    room.write_text(json.dumps(synth.room_model([synth.camera_entry(error_bound_m=bound_m)])), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = case / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels)
                                 for i, (t, _) in enumerate(pairs)])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = replay.main(["--take", str(take), "--room-model", str(room), "--detections", str(rows),
                            "--out", str(case / "out"), "--scored", "--method", str(method_path)])
    lines = (out.getvalue() + err.getvalue()).strip().splitlines()
    print(f"RESULT {label}: exit {code}; last line: {lines[-1] if lines else ''}")
    console = out.getvalue() + err.getvalue()
    mentions = [w for w in ("METHOD", "0.5", "max_depth_gap_s", "max_error_bound_m", str(method_path)) if w in console]
    print(f"RESULT {label}: console mentions method file or changed values: {mentions or 'none'}")
    if code == 2:
        return
    rec = json.loads((case / "out" / "run.json").read_text(encoding="utf-8"))
    obs = [json.loads(l) for l in (case / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    lifts = sorted({e["lift"].split(":")[0] for o in obs for e in o["joints_room"]})
    m = rec["method"]
    print(f"RESULT {label}: gate pass {rec['gate']['pass']}, depth {rec['gate']['counts']['depth']}, "
          f"gap {obs[0]['freshness']['depth_gap_s']}, lifts {lifts}, validity {rec['gate']['counts']['validity']}")
    print(f"RESULT {label}: run.json method.file {m['file']!r}, sha256_lf same as committed "
          f"{m['sha256_lf'] == committed.sha256}, values_used max_depth_gap_s {m['values_used']['max_depth_gap_s']}, "
          f"max_error_bound_m {m['values_used']['max_error_bound_m']}")


try:
    contract = closed("contract")
    gap_override = closed("gap", [("max_depth_gap_s = 0.067", "max_depth_gap_s = 0.5")])
    bound_override = closed("bound", [("max_error_bound_m = 0.05", "max_error_bound_m = 0.5")])
    run("A-contract-stale-depth", contract, 0.2, 0.03)
    run("B-gap-override-stale-depth", gap_override, 0.2, 0.03)
    run("C-contract-bound-wide", contract, 0.004, 0.3)
    run("D-bound-override-bound-wide", bound_override, 0.004, 0.3)
finally:
    shutil.rmtree(work, ignore_errors=True)

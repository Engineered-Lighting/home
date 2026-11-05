#!/usr/bin/env python3
"""Verify finding mutant-round-trip (rung 1, register.build_records line 363).

The finding says the mutant `math.dist(back, fitted) > 1e-9` -> `> 1e9`
survives because no test makes the written transform_id resolve to a
different or invalidated transform. This probe checks whether ANY input can:

  1. survival: the mutant in a temp copy keeps the whole module green;
  2. JSON round trip: for 20,000 random rigid poses, a matrix written with
     records.line and read back gives exactly the same room point (dist == 0.0);
  3. next_version never hands out a version this camera already holds, so the
     new transform_id can only ever name the entry being written;
  4. end to end, with the original code: the finding's suggested test (a
     pre-existing entry with another pose, invalidated, already holding a
     version of that date) still registers, and every math.dist call made
     inside build_records returns exactly 0.0.
If 2-4 hold, the mutated condition is unreachable and the mutant is equivalent.

The checkout is never modified. Synthetic data only; temp dirs; contacts nothing.

    python3 -B verify_mutant-round-trip.py <root of the rung-1-register checkout>
"""
import io
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import rigid  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402

OLD = 'if resolved["invalidated"] or math.dist(back, fitted) > 1e-9:'
NEW = 'if resolved["invalidated"] or math.dist(back, fitted) > 1e9:'

# 1. survival ---------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as base:
    dest = Path(base)
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    path = dest / "tools" / "den-register" / "register.py"
    text = path.read_text(encoding="utf-8")
    assert text.count(OLD) == 1
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_den_register"],
                         cwd=dest / "tools" / "den-register", capture_output=True, text=True, timeout=600)
    print(f"RESULT 1 mutant M16 full module: exit {out.returncode}, "
          f"{[ln for ln in out.stderr.splitlines() if ln.strip()][-1]}")

# 2. JSON round trip of the matrix is exact -----------------------------------------------------------
rng = random.Random(7)
worst = 0.0
for _ in range(20000):
    q = [rng.gauss(0, 1) for _ in range(4)]
    n = math.sqrt(sum(x * x for x in q))
    w, x, y, z = (v / n for v in q)
    R = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    t = [rng.uniform(-10, 10) for _ in range(3)]
    p = [rng.uniform(-5, 5) for _ in range(3)]
    m4 = rigid.matrix4(R, t)
    reread = json.loads(records.line({"pose": {"room_from_camera": m4}}))
    back = records.to_room(reread, p)
    fitted = rigid.apply(*rigid.split4(m4), p)
    worst = max(worst, math.dist(back, fitted))
print(f"RESULT 2 largest round-trip distance over 20000 random poses: {worst!r}")

# 3. next_version never reuses a held version ---------------------------------------------------------
reused = 0
for held in ([], ["2027-01-15.1"], ["2027-01-15.2"], ["2027-01-15.1", "2027-01-15.2"], ["2027-01-15.3"],
             ["2027-01-14.1", "2027-01-15.2", "2027-01-15.4"]):
    rms = [{"room_id": "den", "cameras": [{"camera_id": "den-oak", "version": v}]} for v in held]
    got = records.next_version(rms, "den-oak", "2027-01-15")
    reused += got in held
    print(f"RESULT 3 held {held} -> next {got}")
print(f"RESULT 3 versions reused: {reused}")

# 4. end to end with the suggested pre-existing entry --------------------------------------------------
calls = []


class MathProxy(types.ModuleType):
    def __getattr__(self, name):
        return getattr(math, name)


proxy = MathProxy("math")


def dist(a, b):
    value = math.dist(a, b)
    if sys._getframe(1).f_code.co_name == "build_records":
        calls.append(value)
    return value


proxy.dist = dist
register.math = proxy


def closed():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    return method.parse(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$",
                               lambda g: f"{g[1]} = {g[2]}  # closed for a probe", text, flags=re.M))


m = closed()
date = time.strftime("%Y-%m-%d", time.localtime(1800000100.0))
other = rigid.matrix4([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], [9.0, 9.0, 9.0])
for held in ([f"{date}.1"], [f"{date}.2"], [f"{date}.1", f"{date}.2"]):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        depth, left, seen = synthetic.render()
        take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"
        work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
        for i, version in enumerate(held):
            rm = records.new_room_model("den")
            rm["version"] = f"den-v{i + 1}"
            rm["cameras"] = [{"camera_id": "den-oak", "version": version, "method": "tape + fit",
                              "calibration_id": f"cal-den-oak-pose-{version}",
                              "pose": {"room_from_camera": other, "position_m": [9.0, 9.0, 9.0]},
                              "error_bound_m": 0.01, "photometric": dict(records.PHOTOMETRIC_UNCALIBRATED),
                              "invalidated": {"reason": "mount_changed", "take": "old", "take_ended_unix": 0}}]
            records.append_jsonl(work / "room-model.jsonl", rm)
        sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
        session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
        for pid, (u, v, _) in seen.items():
            session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        session.done()
        before = len(calls)
        code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
        print(f"RESULT 4 pre-existing {held}: {report['result']} {report['transform_id']} "
              f"reasons {[r['reason'] for r in report['reasons']]}; build_records math.dist values {calls[before:]}")

equivalent = worst == 0.0 and reused == 0 and calls and max(calls) == 0.0
print(("RESULT mutant M16 is EQUIVALENT: the condition at register.py:363 cannot be true for a reachable input; "
       "REFUTED as a test gap") if equivalent else "RESULT mutant M16 is distinguishable; finding stands")

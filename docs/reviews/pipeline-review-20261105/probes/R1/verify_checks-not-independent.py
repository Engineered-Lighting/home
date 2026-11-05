#!/usr/bin/env python3
"""Verifier probe for finding checks-not-independent (rung 1, den-register).

The finding's case B lists the SAME crosses as fit points p1, p2 again as checks, with
the same (wrong) numbers copied. This probe asks whether the failure needs that exact
duplication, or also happens with honest checks: separate crosses placed a short
distance from p1 and p2 and measured correctly with the tape.

Scene: synthetic room, fit p1 p2 p3, p1 mis-measured by +10 cm in z (as in the finding).
  A  checks c1, c2 (the synthetic far checks)               -> expected refused
  B  checks d1, d2 = p1, p2 copied (same numbers, same pixel)  -> the finding's case
  N  checks n1, n2 = separate crosses at distance D from p1, p2, measured correctly,
     for D in 0.10 / 0.25 / 0.50 m
Synthetic data and temp folders only.

Run:  python3 -B verify_checks-not-independent.py <rung-1 checkout root>
"""
import io
import json
import math
import re
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-register"
sys.path.insert(0, str(tool))
import method, register, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
BAD = {"p1": [0.0, 0.0, 0.10]}


def run(points, checks, offsets):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        depth, left, seen = synthetic.render(points)
        take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"
        work.mkdir()
        sheet = synthetic.worksheet(points, ("p1", "p2", "p3"), checks, offsets)
        (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
        ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
        for pid in ("p1", "p2", "p3") + tuple(checks):
            u, v, _ = seen[pid]
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, rep = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        errs = {p["id"]: round(p["error_m"] * 100, 1) for p in rep.get("pairs", []) if "error_m" in p}
        return rep["result"], [r["reason"] for r in rep["reasons"]], errs


P = dict(synthetic.POINTS)
print("RESULT A independent far checks:", run(P, ("c1", "c2"), BAD))
Q = dict(P, d1=list(P["p1"]), d2=list(P["p2"]))
print("RESULT B same crosses copied as checks:", run(Q, ("d1", "d2"), dict(BAD, d1=BAD["p1"])))
for d in (0.10, 0.25, 0.50):
    # on the floor next to p1 (z = 0), and on p2's surface height next to p2
    R = dict(P, n1=[P["p1"][0] - d, P["p1"][1] - 0.0, P["p1"][2]], n2=[P["p2"][0] + d / math.sqrt(2),
                                                                         P["p2"][1] - d / math.sqrt(2), P["p2"][2]])
    print(f"RESULT N honest checks {d:.2f} m from p1/p2:", run(R, ("n1", "n2"), BAD))

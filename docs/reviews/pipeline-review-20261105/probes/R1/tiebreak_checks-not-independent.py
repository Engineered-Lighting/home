#!/usr/bin/env python3
"""Tiebreak probe for finding checks-not-independent (rung 1, den-register).

Question: does the "checks need not be independent of the fit" gap need the owner to
copy a fit point's numbers into a check row (the finding's case B), or does an owner
who measures honestly but places the two checks close to fit points also get a wrong
REGISTERED?

Synthetic room from tools/den-register/synthetic.py; p1 mis-measured by +10 cm in z.
Honest checks are separate crosses, measured correctly (true positions), placed at a
distance d from the WELL-fitted fit points (p2, p3; and p4 when four fit points are
used), on the same surface height. For each case it prints the verdict, the check
errors and how far the accepted transform is from the truth at c1 (the wall cross)
and at the room centre 1 m up.

Synthetic data and temp folders only; nothing contacts a host.
Run:  python3 -B tiebreak_checks-not-independent.py <rung-1 checkout root>
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
import method, register, rigid, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
TRUE_R, TRUE_T = synthetic.camera_pose()
CENTRE = [2.3, 1.9, 1.0]


def run(points, fit, checks, offsets):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        depth, left, seen = synthetic.render(points)
        missing = [p for p in list(fit) + list(checks) if p not in seen]
        if missing:
            return "not-in-view " + ",".join(missing), {}, None
        take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"
        work.mkdir()
        sheet = synthetic.worksheet(points, fit, checks, offsets)
        (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
        ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
        for pid in list(fit) + list(checks):
            u, v, _ = seen[pid]
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, rep = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        errs = {p["id"]: round(p.get("error_m", p.get("residual_m", float("nan"))) * 100, 1)
                for p in rep.get("pairs", [])}
        off = None
        if rep.get("matrix"):
            r, tr = rigid.split4(rep["matrix"])
            at = lambda q: math.dist(rigid.apply(r, tr, synthetic.to_camera(TRUE_R, TRUE_T, q)), q) * 100
            off = (round(at(synthetic.POINTS["c1"]), 1), round(at(CENTRE), 1))
        verdict = rep["result"] + (" " + ",".join(x["reason"] for x in rep["reasons"]) if rep["reasons"] else "")
        return verdict, errs, off


P = dict(synthetic.POINTS)
BAD = {"p1": [0.0, 0.0, 0.10]}
wrong_pass = 0
for fit in (("p1", "p2", "p3"), ("p1", "p2", "p3", "p4")):
    v, e, off = run(P, fit, ("c1", "c2"), BAD)
    print(f"RESULT fit {fit} independent far checks c1,c2: {v} errors(cm) {e} truth-offset c1/centre(cm) {off}")
    near = [q for q in fit if q != "p1"][:2]
    for d in (0.10, 0.25, 0.50):
        Q = dict(P)
        names = []
        for i, q in enumerate(near):
            x, y, z = P[q]
            ang = math.radians(45 + 90 * i)
            Q[f"n{i + 1}"] = [x + d * math.cos(ang), y - d * math.sin(ang), z]
            names.append(f"n{i + 1}")
        v, e, off = run(Q, fit, tuple(names), BAD)
        flag = ""
        if v.startswith("registered") and off and off[0] > 5.0:
            wrong_pass += 1
            flag = "  <-- REGISTERED with honest checks; transform off by more than the 5 cm gate"
        print(f"RESULT fit {fit} honest checks {d:.2f} m from {near}: {v} errors(cm) {e} "
              f"truth-offset c1/centre(cm) {off}{flag}")
print(f"RESULT wrong passes with honest, correctly measured checks near fit points: {wrong_pass}")

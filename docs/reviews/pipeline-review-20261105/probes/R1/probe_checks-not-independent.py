#!/usr/bin/env python3
"""Probe: are den-register's "two independent checks" really independent of the fit?

The worksheet only requires role `check` on a point; nothing stops a check from being
the same tape cross as a fit point (same room position, clicked at the same pixel).
Such a check passes whenever the fit residual at that mark passes, so the ladder's
"plus two independent checks" degenerates to "fit residuals within 5 cm".

Synthetic scene only. One fit mark is mis-measured by a few cm (a realistic tape
slip). Two worksheets with the same fit points are fitted:
  A. checks at two other marks (independent)        -> expected REFUSED check_over_gate
  B. checks that are copies of two fit marks        -> if REGISTERED, the gate is vacuous
It also reports how far the accepted transform B is from the true synthetic pose at a
point in the working region (room centre, 1.0 m up).

Run:  python3 probe_checks-not-independent.py <rung-1 checkout root>
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
import method, records, register, rigid, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
P = dict(synthetic.POINTS)
P["d1"], P["d2"] = list(P["p1"]), list(P["p2"])   # the same crosses as fit marks p1 and p2
DUP = {"p1": "d1", "p2": "d2"}


def run(fit, checks, offsets):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        depth, left, seen = synthetic.render(P)
        data = synthetic.bundle(synthetic.metadata(), depth, left)
        take = synthetic.write_take(tmp / "take1", [data] * 3)
        work = tmp / "work"
        work.mkdir()
        sheet = synthetic.worksheet(P, fit, checks, offsets)
        (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
        ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
        for pid in list(fit) + list(checks):
            u, v, _ = seen[pid]
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, report = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        return code, report


true_r, true_t = synthetic.camera_pose()
probe_room = [2.3, 1.9, 1.0]
probe_cam = synthetic.to_camera(true_r, true_t, probe_room)
fits = ("p1", "p2", "p3")
found, worst = None, -1.0
for axis in range(3):
    for size in (0.04, 0.06, 0.08, 0.10, 0.12, 0.14, -0.04, -0.06, -0.08, -0.10, -0.12, -0.14):
        for pid in fits:
            off = [0.0, 0.0, 0.0]
            off[axis] = size
            offsets = {pid: off}
            ca, ra = run(fits, ("c1", "c2"), offsets)
            if ra["result"] != "refused" or {r["reason"] for r in ra["reasons"]} != {"check_over_gate"}:
                continue
            # the owner lists the same two crosses again as checks: same numbers copied, same pixels
            dup_offsets = dict(offsets)
            if pid in DUP:
                dup_offsets[DUP[pid]] = off
            cb, rb = run(fits, ("d1", "d2"), dup_offsets)
            if rb["result"] == "registered":
                r_, t_ = rigid.split4(rb["matrix"])
                e = max(math.dist(rigid.apply(r_, t_, synthetic.to_camera(true_r, true_t, P[c])), P[c])
                        for c in ("c1", "c2"))
                if e > worst:
                    worst, found = e, (offsets, ra, rb)

if not found:
    print("RESULT PASS: no offset found where duplicated checks accept what independent checks refuse")
    sys.exit(0)
offsets, ra, rb = found
print(f"fit marks {fits}, mis-measured: {offsets}")
print("A (independent checks c1, c2):", ra["result"], [r["reason"] for r in ra["reasons"]],
      {p["id"]: round(p.get("error_m", p.get("residual_m", 0)) * 100, 1) for p in ra["pairs"]}, "(cm)")
print("B (checks d1, d2 = the p1, p2 crosses listed again):", rb["result"], rb["transform_id"],
      {p["id"]: round(p.get("error_m", p.get("residual_m", 0)) * 100, 1) for p in rb["pairs"]}, "(cm)")
r, t = rigid.split4(rb["matrix"])
err = math.dist(rigid.apply(r, t, probe_cam), probe_room)
err_c = [math.dist(rigid.apply(r, t, synthetic.to_camera(true_r, true_t, P[c])), P[c]) for c in ("c1", "c2")]
print(f"B's accepted transform is off by {err * 100:.1f} cm at the room centre 1.0 m up, "
      f"and by {err_c[0] * 100:.1f} / {err_c[1] * 100:.1f} cm at c1 / c2")
print("FAIL: duplicated check marks pass the 'two independent checks' gate and register a transform "
      "that independent checks refuse")
print("RESULT FAIL")

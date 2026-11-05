#!/usr/bin/env python3
"""Verifier for finding checks-near-fit-points-pass (rung 1, den-register).

Re-runs the reported scenario end to end (worksheet -> clicks -> register.run_fit)
and sweeps how far the two checks sit from the fit points, to judge realism:
  * 3 fit points (p1, p2, p3), p3 mismeasured by 8 cm along the normal of their
    plane; checks c1, c2 placed d metres from p1 (+x) and p2 (-y) for
    d = 0, 0.15, 0.30, 0.50, 0.80;
  * the same with 4 fit points (p1..p4), as README step 3 tells the owner to use;
  * the synthetic room's own check marks (not next to a fit point).
For each: verdict, check errors, the written error_bound_m, and the worst error of
independent room points carried through the written transform.
Synthetic data only; temp dirs only; contacts nothing.

    python3 -B verify_checks-near-fit-points-pass.py <root of the rung-1-register checkout>
"""
import io
import json
import math
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import synthetic  # noqa: E402
import worksheet as ws  # noqa: E402


def closed():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed", text, flags=re.M)
    return method.parse(text)


def run(points, fit, checks, offsets):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        depth, left, seen = synthetic.render(points)
        missing = [k for k in list(fit) + list(checks) if k not in seen]
        if missing:
            return None, f"not in view: {missing}"
        take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"
        work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet(points, fit, checks, offsets)),
                                             encoding="utf-8")
        m = closed()
        sheet = ws.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
        for pid in list(fit) + list(checks):
            u, v, _ = seen[pid]
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
        entry = records.read_jsonl(work / "room-model.jsonl", "x")[-1]["cameras"][-1] if code == 0 else None
        return report, entry


P = synthetic.POINTS
R, t = synthetic.camera_pose()
sub = lambda a, b: [a[i] - b[i] for i in range(3)]
cross = lambda a, b: [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
n = cross(sub(P["p2"], P["p1"]), sub(P["p3"], P["p1"]))
norm = math.sqrt(sum(v * v for v in n))
offsets = {"p3": [0.08 * v / norm for v in n]}
INDEPENDENT = [P["c1"], P["c2"], [2.0, 2.0, 0.0], [4.2, 3.4, 1.6], [3.0, 2.5, 0.0], [2.5, 3.6, 0.9]]


def worst_independent(entry):
    return max(math.dist(records.to_room(entry, synthetic.to_camera(R, t, X)), X) for X in INDEPENDENT)


def line(label, report, entry):
    if report is None:
        print(f"RESULT {label}: skipped ({entry})")
        return
    chk = [round(p["error_m"] * 100, 1) for p in report.get("pairs", []) if "error_m" in p]
    res = [round(p["residual_m"] * 100, 1) for p in report.get("pairs", []) if "residual_m" in p]
    tail = ""
    if entry:
        tail = f"; error_bound_m {entry['error_bound_m']}; worst independent point {worst_independent(entry)*100:.1f} cm"
    print(f"RESULT {label}: {report['result']} {[r['reason'] for r in report['reasons']]}; residuals {res} cm; "
          f"checks {chk} cm{tail}")


for fit in (("p1", "p2", "p3"), ("p1", "p2", "p3", "p4")):
    for d in (0.0, 0.15, 0.30, 0.50, 0.80):
        pts = {k: P[k] for k in fit}
        pts["c1"] = [P["p1"][0] + d, P["p1"][1], P["p1"][2]]
        pts["c2"] = [P["p2"][0], P["p2"][1] - d, P["p2"][2]]
        report, entry = run(pts, fit, ("c1", "c2"), offsets)
        line(f"{len(fit)} fit, checks {d:.2f} m from p1/p2", report, entry)
    pts = {k: P[k] for k in fit}
    pts.update(c1=P["c1"], c2=P["c2"])
    report, entry = run(pts, fit, ("c1", "c2"), offsets)
    line(f"{len(fit)} fit, the synthetic room's own checks (not next to a fit point)", report, entry)

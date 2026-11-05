#!/usr/bin/env python3
"""A REGISTERED verdict whose written error_bound_m is above the 5 cm gate.

den-register gates only the fit residuals and the check errors (<= gate_m), but
writes RoomModel.cameras[].error_bound_m = max(residuals, check errors,
leave-one-out errors). With four fit points the leave-one-out errors come from
3-point fits and are often several times the residuals, so a run prints
REGISTERED while the entry it writes says the transform's own uncertainty is
above 5 cm. Rung 2 (den-observe, max_error_bound_m = 0.05, "rung 1's gate")
refuses such an entry. This probe:
  1. runs register.run_fit end to end on a synthetic scene with tape errors of
     about 1.5 cm per axis (seeded) and prints the verdict and error_bound_m;
  2. Monte Carlo on the tool's rigid.fit: share of gate-passing registrations
     whose error_bound_m exceeds 0.05 m at 1, 1.5 and 2 cm per-axis tape noise.

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B probe_error_bound_over_gate.py <root of the rung-1-register checkout>
"""
import io
import json
import math
import random
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
import rigid  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402


def closed_method():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for a probe",
                  text, flags=re.M)
    return method.parse(text)


def run_case(tmp, offsets):
    folder = Path(tmp)
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(folder / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = folder / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet(synthetic.POINTS, offsets=offsets)),
                                         encoding="utf-8")
    m = closed_method()
    sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for pid, (u, v, _) in seen.items():
        session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    session.done()
    out = io.StringIO()
    code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=out)
    return code, report, work, out.getvalue()


# Seeded search on ideal camera points for a 1.5 cm/axis tape-error pattern that passes the gate
# but has a leave-one-out error above 5 cm; then confirm through the whole pipeline.
R, t = synthetic.camera_pose()
cam = {k: synthetic.to_camera(R, t, v) for k, v in synthetic.POINTS.items()}
rng = random.Random(11)
chosen = None
for _ in range(500):
    off = {k: [round(rng.gauss(0, 0.015), 4) for _ in range(3)] for k in synthetic.POINTS}
    room = {k: [synthetic.POINTS[k][i] + off[k][i] for i in range(3)] for k in synthetic.POINTS}
    res = rigid.fit([cam[k] for k in synthetic.FIT], [room[k] for k in synthetic.FIT])
    chk = [math.dist(rigid.apply(res["rotation"], res["translation"], cam[k]), room[k]) for k in synthetic.CHECKS]
    if res["max_m"] <= 0.04 and max(chk) <= 0.04 and res["loo_max_m"] > 0.06:
        chosen = off
        break
print("RESULT tape errors used (m):", json.dumps(chosen))
with tempfile.TemporaryDirectory() as tmp:
    code, report, work, text = run_case(tmp, chosen)
    print(text.rstrip())
    fit = report.get("fit") or {}
    entry = records.read_jsonl(work / "room-model.jsonl", "x")[-1]["cameras"][-1] if code == 0 else {}
    print(f"RESULT verdict {report['result']}; max residual {fit.get('max_m', 0)*100:.2f} cm; "
          f"max check {max(p.get('error_m', 0) for p in report['pairs'])*100:.2f} cm; "
          f"loo max {fit.get('loo_max_m', 0)*100:.2f} cm; written error_bound_m {entry.get('error_bound_m')}")
    bad = report["result"] == "registered" and (entry.get("error_bound_m") or 0) > 0.05
    print(("FAIL" if bad else "PASS") + " a REGISTERED entry must not carry error_bound_m above gate_m (0.05)")

# Monte Carlo on the tool's own fit: how often does a gate pass carry a bound above 5 cm?
for sigma in (0.01, 0.015, 0.02):
    rng = random.Random(7)
    passed = over = 0
    for _ in range(2000):
        off = {k: [rng.gauss(0, sigma) for _ in range(3)] for k in synthetic.POINTS}
        room = {k: [synthetic.POINTS[k][i] + off[k][i] for i in range(3)] for k in synthetic.POINTS}
        res = rigid.fit([cam[k] for k in synthetic.FIT], [room[k] for k in synthetic.FIT])
        chk = [math.dist(rigid.apply(res["rotation"], res["translation"], cam[k]), room[k]) for k in synthetic.CHECKS]
        if res["max_m"] <= 0.05 and max(chk) <= 0.05:
            passed += 1
            over += max([res["max_m"], res["loo_max_m"]] + chk) > 0.05
    print(f"RESULT tape noise {sigma*100:.1f} cm/axis: {passed}/2000 pass the gate; {over} of them "
          f"({over/max(passed, 1):.0%}) write error_bound_m > 0.05")
sys.exit(1 if bad else 0)

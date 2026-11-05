#!/usr/bin/env python3
"""Verifier for finding roles-swapped-after-refusal (rung 1, den-register).

Replays the reported scenario: check c1 mismeasured by 7 cm in y -> fit-1 REFUSED
(check_over_gate); the worksheet is edited so c1 is a fit point and p4 a check ->
fit-2. Reports fit-2's verdict, what the CalibrationRecord says about the earlier
attempt, the written error_bound_m against rung 2's max_error_bound_m (0.05, from
claude/rung-2-observe METHOD.md), and how far independent room points land from
their true positions through the written transform.
Synthetic data only; temp dirs only; contacts nothing.

    python3 -B verify_roles-swapped-after-refusal.py <root of the rung-1-register checkout>
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


def click_fit(work, take, seen, now):
    m = closed()
    sheet = ws.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for p in sheet["points"]:
        u, v, _ = seen[p["id"]]
        s.click({"id": p["id"], "u": round(u, 1), "v": round(v, 1)})
    s.done()
    return register.run_fit(work, take, m=m, now=lambda: now, out=io.StringIO())


P = synthetic.POINTS
R, t = synthetic.camera_pose()
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    depth, left, seen = synthetic.render(P)
    take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    sheet = synthetic.worksheet(P, synthetic.FIT, synthetic.CHECKS, {"c1": [0.0, 0.07, 0.0]})
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    code1, rep1 = click_fit(work, take, seen, 1_800_000_500.0)
    print("RESULT fit-1:", rep1["result"], [r["reason"] for r in rep1["reasons"]])
    for row in sheet["points"]:
        row["role"] = {"c1": "fit", "p4": "check"}.get(row["id"], row["role"])
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    code2, rep2 = click_fit(work, take, seen, 1_800_000_900.0)
    print("RESULT fit-2 after the swap:", rep2["result"], rep2.get("transform_id"))
    if code2 == 0:
        rec = records.read_jsonl(work / "calibration-records.jsonl", "x")[-1]
        entry = records.read_jsonl(work / "room-model.jsonl", "x")[-1]["cameras"][-1]
        print("RESULT record inputs keys:", sorted(rec["inputs"]))
        print("RESULT record mentions fit-1 / refused / earlier roles:",
              any(s in json.dumps(rec) for s in ("fit-1", "refused", "check_over_gate")))
        print("RESULT fit files kept in the work folder:", sorted(p.name for p in work.glob("fit-*.json")))
        print(f"RESULT written error_bound_m {entry['error_bound_m']}, loo_m_max {rec['loo_m_max']}; "
              f"rung 2 max_error_bound_m 0.05 -> {'refuses' if entry['error_bound_m'] > 0.05 else 'accepts'}")
        pts = [P["c1"], [2.0, 2.0, 0.0], [4.2, 3.4, 1.6], [3.0, 2.5, 0.0], [2.5, 3.6, 0.9], [1.0, 3.0, 1.5]]
        errs = [math.dist(records.to_room(entry, synthetic.to_camera(R, t, X)), X) for X in pts]
        print("RESULT independent points through fit-2 (cm):", [round(e * 100, 1) for e in errs])

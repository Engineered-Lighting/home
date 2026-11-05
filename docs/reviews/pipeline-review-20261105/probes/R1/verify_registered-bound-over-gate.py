#!/usr/bin/env python3
"""Verifier probe for finding registered-bound-over-gate (rung 1, den-register).

Scans a fixed pattern of tape error on the four fit points (checks measured exactly),
scaled up step by step, through den-register's own click session and run_fit, and
prints, for each scale: the verdict, the max fit residual, the worst check error, the
leave-one-out max, and the error_bound_m that a REGISTERED run writes into
room-model.jsonl. It reports the first registered run whose written bound exceeds the
5 cm gate and the largest written bound among registered runs (the "flip point" claim).

Synthetic data and temp folders only; nothing contacts any host.
Run:  python3 -B verify_registered-bound-over-gate.py <rung-1 checkout root>
"""
import io
import json
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
PATTERN = {"p1": [0.3, -0.2, 0.1], "p2": [-0.1, 0.3, -0.2], "p3": [0.2, 0.1, 0.3], "p4": [-0.3, -0.1, 0.0]}

first_over, largest = None, None
SCALES = [0.005 * step for step in range(1, 28)] + [0.136, 0.137, 0.138, 0.139, 0.1395, 0.14, 0.145]
for step, scale in enumerate(SCALES, 1):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        depth, left, seen = synthetic.render(synthetic.POINTS)
        take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"
        work.mkdir()
        sheet = synthetic.worksheet(offsets={k: [scale * x for x in d] for k, d in PATTERN.items()})
        (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
        ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
        for pid, (u, v, _) in seen.items():
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, rep = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        fit = rep.get("fit") or {}
        checks = [p["error_m"] for p in rep.get("pairs", []) if "error_m" in p]
        written = None
        if rep["result"] == "registered":
            entry = json.loads((work / "room-model.jsonl").read_text(encoding="utf-8").splitlines()[-1])["cameras"][-1]
            written = entry["error_bound_m"]
            if written > 0.05 and first_over is None:
                first_over = (scale, fit, max(checks), written)
            if largest is None or written > largest[3]:
                largest = (scale, fit, max(checks), written)
        if fit:
            print(f"scale {scale:.3f}: {rep['result']:10} residual max {fit['max_m'] * 100:5.1f} cm, worst check "
                  f"{max(checks) * 100:4.1f} cm, LOO max {fit['loo_max_m'] * 100:5.1f} cm, written bound {written}")
        if rep["result"] != "registered" and step > 10:
            break
if first_over:
    sc, fit, chk, wr = first_over
    print(f"RESULT FAIL first REGISTERED run with error_bound_m above 0.05: scale {sc:.3f}, residual max "
          f"{fit['max_m'] * 100:.1f} cm, worst check {chk * 100:.1f} cm, LOO {fit['loo_max_m'] * 100:.1f} cm, "
          f"written {wr}")
if largest:
    sc, fit, chk, wr = largest
    print(f"RESULT largest written bound on a REGISTERED run: {wr} at scale {sc:.3f} (residual max "
          f"{fit['max_m'] * 100:.1f} cm, LOO {fit['loo_max_m'] * 100:.1f} cm)")
if not first_over:
    print("RESULT PASS no REGISTERED run wrote error_bound_m above 0.05")

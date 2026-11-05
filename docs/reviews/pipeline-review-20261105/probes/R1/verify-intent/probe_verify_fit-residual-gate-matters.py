#!/usr/bin/env python3
"""Verifier probe for gate-mutants-survive: does the fit-residual gate ever decide a verdict alone?

If every fit with a residual over 5 cm also fails a check, the surviving 'residual > 2 * gate'
mutant would be harmless. This sweeps one mis-measured fit point (p1..p4, along x, y or z,
5 to 30 cm) on den-register's own synthetic scene with the method's proposed values closed,
and counts cases where the fit-residual gate is the ONLY reason for refusal while the worst
residual is between 5 and 10 cm (the cases the doubled gate would register).

Synthetic data and temp folders only; nothing contacts a host.
Run:  python3 -B probe_verify_fit-residual-gate-matters.py <rung-1 checkout root>
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

depth, left, seen = synthetic.render(synthetic.POINTS)
alone = []
total = 0
with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    for pid in ("p1", "p2", "p3", "p4"):
        for axis in range(3):
            for cm in (5, 8, 10, 12, 15, 20, 30):
                total += 1
                work = tmp / f"w-{pid}-{axis}-{cm}"
                work.mkdir()
                off = [0.0, 0.0, 0.0]
                off[axis] = cm / 100
                sheet = synthetic.worksheet(offsets={pid: off})
                (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
                ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
                s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
                for q, (u, v, _) in seen.items():
                    s.click({"id": q, "u": round(u, 1), "v": round(v, 1)})
                s.done()
                code, rep = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
                rs = sorted({r["reason"] for r in rep["reasons"]})
                worst_res = max((p.get("residual_m", 0) for p in rep.get("pairs", [])), default=0)
                worst_chk = max((p.get("error_m", 0) for p in rep.get("pairs", [])), default=0)
                if rs == ["fit_residual_over_gate"] and 0.05 < worst_res <= 0.10:
                    alone.append((pid, "xyz"[axis], cm, round(worst_res * 100, 1), round(worst_chk * 100, 1)))
for row in alone:
    print(f"fit-residual gate alone refuses: {row[0]} off {row[2]} cm in {row[1]}: worst residual {row[3]} cm, "
          f"worst check {row[4]} cm")
print(f"RESULT {'FAIL' if alone else 'PASS'}: {len(alone)} of {total} cases are refused only by the fit-residual "
      f"gate with a residual in (5, 10] cm, which the doubled-gate mutant would register")

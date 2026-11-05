#!/usr/bin/env python3
"""Verifier: is mutant M3 (`if residual > gate:` -> `if residual > 2 * gate:`) equivalent in
practice, or is there an input where ONLY the fit-residual gate refuses?

Scene: the synthetic room's four fit points p1..p4, each measured 6 cm too far from their
common centroid (a scale-like tape pattern a rigid fit cannot absorb), checks c1 and c2
measured exactly. The unmutated tool must refuse with fit_residual_over_gate alone; the
mutant (applied to a temp copy of tools/den-register) registers it.

Synthetic data and temp dirs only; contacts nothing; the checkout is not modified.

    python3 -B verify_mutant_m3_nonequivalent.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()

RUNNER = textwrap.dedent(r'''
import io, json, re, sys, tempfile
from pathlib import Path
TOOL = Path(sys.argv[1])
sys.path.insert(0, str(TOOL))
import method, register, synthetic, worksheet as ws
text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed", text, flags=re.M)
m = method.parse(text)
P = synthetic.POINTS
fit = ("p1", "p2", "p3", "p4")
c = [sum(P[k][i] for k in fit) / 4 for i in range(3)]
offsets = {}
for k in fit:
    d = [P[k][i] - c[i] for i in range(3)]
    n = sum(v * v for v in d) ** 0.5
    offsets[k] = [0.06 * v / n for v in d]
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    depth, left, seen = synthetic.render(P)
    take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"; work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet(P, fit, ("c1", "c2"), offsets)), encoding="utf-8")
    sheet = ws.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for pid in seen:
        u, v, _ = seen[pid]
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
    res = [round(p["residual_m"] * 100, 2) for p in report["pairs"] if "residual_m" in p]
    chk = [round(p["error_m"] * 100, 2) for p in report["pairs"] if "error_m" in p]
    print(json.dumps({"result": report["result"], "reasons": [r["reason"] for r in report["reasons"]],
                      "residuals_cm": res, "checks_cm": chk}))
''')

with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    runner = base / "runner.py"
    runner.write_text(RUNNER, encoding="utf-8")
    out = {}
    for label, mutate in (("original", False), ("mutant M3", True)):
        copy = base / label.replace(" ", "_")
        for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
            shutil.copytree(ROOT / rel, copy / rel, ignore=shutil.ignore_patterns("__pycache__"))
        if mutate:
            p = copy / "tools/den-register/register.py"
            t = p.read_text(encoding="utf-8")
            assert t.count("if residual > gate:") == 1
            p.write_text(t.replace("if residual > gate:", "if residual > 2 * gate:"), encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(runner), str(copy / "tools/den-register")],
                           capture_output=True, text=True, cwd=base, timeout=300)
        out[label] = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"error": r.stderr[-500:]}
        print(f"RESULT {label}: {out[label]}")
    o, mu = out["original"], out["mutant M3"]
    ok = (o.get("result") == "refused" and set(o.get("reasons", [])) == {"fit_residual_over_gate"}
          and mu.get("result") == "registered")
    print(("PASS" if ok else "FAIL") + " M3 is NOT equivalent: only the fit-residual gate refuses this scene, "
          "and the mutant registers it" if ok else "FAIL could not show non-equivalence")

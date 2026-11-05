#!/usr/bin/env python3
"""Verifier: do the reported surviving mutants (M3 fit-residual gate doubled, M5 collinear
floor halved, M9 error bound drops LOO and checks) also survive the WHOLE den-register suite
(unittest discover, every class), not only FitTests?

Copies tools/den-register, tools/den-take and docs/architecture/live-v1 into a temp folder
(git init'd, as the work-folder test needs), applies one exact text replacement per mutant,
runs `python -B -m unittest discover -s . -f` in the copy, prints caught/survived. Also runs
an unmutated baseline. The checkout is never modified; synthetic data only; contacts nothing.

    python3 -B verify_mutants_full_suite.py <root of the rung-1-register checkout>
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MUTANTS = [
    ("M3-fit-residual-gate-doubled", "register.py", "if residual > gate:", "if residual > 2 * gate:"),
    ("M3b-fit-residual-gate-disabled", "register.py", "if residual > gate:", "if residual > 100 * gate:"),
    ("M5-collinear-floor-halved", "register.py", 'if spread < m["min_spread_m"]:', 'if spread < m["min_spread_m"] / 2:'),
    ("M9-error-bound-drops-loo-and-checks", "register.py",
     'bound = max(result["residuals_m"] + [p["error_m"] for p in check_pairs] + (result["loo_m"] or []))',
     'bound = max(result["residuals_m"])'),
]


def make_copy(tmp):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)


def run(tmp):
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", ".", "-f"],
                         cwd=tmp / "tools" / "den-register", capture_output=True, text=True, timeout=600)
    lines = out.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "")
    return out.returncode, ran + " " + (lines[-1] if lines else ""), out.stderr


with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    clean = base / "clean"
    make_copy(clean)
    code, tail, _ = run(clean)
    print(f"RESULT baseline (unmutated copy, full suite): exit {code}, {tail}")
    for mid, rel, old, new in MUTANTS:
        tmp = base / mid
        make_copy(tmp)
        path = tmp / "tools" / "den-register" / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (mid, text.count(old))
        path.write_text(text.replace(old, new), encoding="utf-8")
        code, tail, err = run(tmp)
        failing = [l for l in err.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        print(f"RESULT {mid:<40} {'caught' if code else 'SURVIVED'}  {failing[0] if failing else tail}")

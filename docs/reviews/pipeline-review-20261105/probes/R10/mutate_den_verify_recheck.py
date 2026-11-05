#!/usr/bin/env python3
"""mutate_den_verify_recheck: verifier's re-run of mutants M05, M07 and M09 (findings
mutant-empty-input-pass, mutant-held-hit-rule, mutant-settle-time).

What it checks: copies tools/den-verify and docs/architecture/live-v1 of the given
checkout into a temp dir, applies each mutant alone, runs the WHOLE unittest suite
(no failfast, so every failing test is listed), and prints caught/survived with the
number of tests run and failed. Synthetic data only; nothing written outside temp dirs.

Run:
    python3 mutate_den_verify_recheck.py <root of the rung-10-verify checkout>
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUTANTS = [
    ("M05-empty-passes", "score.py",
     "\"verdict\": (\"insufficient\" if not n else",
     "\"verdict\": (\"pass\" if not n else"),
    ("M07-held-hits-also-excluded", "score.py",
     "row[\"excluded_held\"] = row[\"status\"] == \"outside\" and row[\"held_share\"] >= m[\"held_share_min\"]",
     "row[\"excluded_held\"] = row[\"status\"] in (\"within\", \"outside\") and row[\"held_share\"] >= m[\"held_share_min\"]"),
    ("M09-ignore-settle-time", "score.py",
     "            if f.ts < d.settled:\n                continue",
     "            if f.ts < d.ts:\n                continue"),
]


def suite(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tools/den-verify", "-p", "test_*.py"],
                       cwd=root, env=env, capture_output=True, text=True, timeout=600)
    err = p.stderr or ""
    ran = re.search(r"Ran (\d+) test", err)
    failing = re.findall(r"^(?:FAIL|ERROR): (\S+ \(\S+\))", err, re.M)
    return p.returncode, int(ran[1]) if ran else None, failing


src = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "base"
    (base / "tools").mkdir(parents=True)
    shutil.copytree(src / "tools" / "den-verify", base / "tools" / "den-verify",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(src / "docs" / "architecture" / "live-v1", base / "docs" / "architecture" / "live-v1")
    code, ran, failing = suite(base)
    print(f"RESULT baseline exit={code} ran={ran} failing={failing}")
    for mid, fname, old, new in MUTANTS:
        work = Path(tmp) / mid
        shutil.copytree(base, work)
        f = work / "tools" / "den-verify" / fname
        text = f.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"RESULT {mid}: NOT APPLIED (pattern count {text.count(old)})")
            continue
        f.write_text(text.replace(old, new), encoding="utf-8")
        code, ran, failing = suite(work)
        print(f"RESULT {mid}: {'caught' if code else 'SURVIVED'} exit={code} ran={ran} failing={failing}")

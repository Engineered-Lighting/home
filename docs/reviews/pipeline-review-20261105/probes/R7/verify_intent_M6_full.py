#!/usr/bin/env python3
"""(R7 verifier, intent lens) Re-run finding mutant-daylight-by-fit-survives against the WHOLE den-photometry suite.

The original runner's --full mode builds `python -m unittest -f discover -s ...`, which unittest rejects
("unrecognized arguments"), so its --full result is not a result. This runner applies the same M6 mutant
(card.py coverage(): the daylight requirement also accepts a fit capture) to a copy and runs
`python -B -m unittest discover -f -s tools/den-photometry` from the copy's root.

Run:
    TMPDIR=<scratch dir> python3 verify_intent_M6_full.py <root of the branch checkout> [--baseline]

--baseline runs the unmutated copy instead (to show the copy itself passes).
Only temp folders are written (prefix r7v-); the checkout is never modified. The suite's own synthetic
data and 127.0.0.1 fakes only.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
BASELINE = "--baseline" in sys.argv
OLD = 'if not any(c["illuminant"] == v["daylight_class"] for c in held):'
NEW = ('if not any(c["illuminant"] == v["daylight_class"] for c in held + '
       '[g["fit"] for g in selection["groups"].values() if g["fit"]]):')

with tempfile.TemporaryDirectory(prefix="r7v-") as tmp:
    copy = Path(tmp) / "co"
    for sub in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / sub, copy / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / ".git").mkdir()
    target = copy / "tools/den-photometry/card.py"
    text = target.read_text(encoding="utf-8")
    if not BASELINE:
        if text.count(OLD) != 1:
            print(f"RESULT: NOT RUN (pattern found {text.count(OLD)} times)")
            sys.exit(2)
        target.write_text(text.replace(OLD, NEW), encoding="utf-8")
    start = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-photometry"],
                          cwd=copy, capture_output=True, text=True, timeout=1500)
    took = time.time() - start
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    print("\n".join(tail[-6:]))
    first_fail = next((l for l in tail if l.startswith(("FAIL:", "ERROR:"))), "")
    label = "baseline" if BASELINE else "M6-daylight-by-fit (full suite)"
    print(f"RESULT: {label}: {'caught' if proc.returncode else 'survived'} in {took:.0f} s {first_fail}")

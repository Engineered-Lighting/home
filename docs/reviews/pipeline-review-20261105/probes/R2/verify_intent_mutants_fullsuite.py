#!/usr/bin/env python3
"""verify_intent_mutants_fullsuite: re-run the six reported surviving mutants against the FULL den-observe suite.

The original runner (mutate_den_observe.py) ran some mutants only against one test
class. This verifier applies each mutant to a temp copy of tools/den-observe,
tools/den-take and docs/architecture, and runs the whole suite exactly as the README
says (`python3 -B -m unittest discover -s tools/den-observe -f`), so "survived" means
all 50 tests pass with the mutant in place.

    python3 -B verify_intent_mutants_fullsuite.py <rung-2-observe checkout root>

Synthetic data only (the suite's own); temp dirs only; never touches the checkout.
Prints one RESULT line per mutant and a summary line.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
MUTANTS = [
    ("observe.py", "if abs(gap) > settings.max_gap_s:", "if gap > settings.max_gap_s:",
     "one-sided depth gap"),
    ("observe.py", 'if fresh and fresh.get("depth") != "used" and any(e.get("lift") == "stereo" for e in joints or []):',
     'if False:', "check_record stereo-from-unused-depth check disabled"),
    ("observe.py", "common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / n)",
     "common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / (n * n))", "torso common term / n^2"),
    ("observe.py", "if score < settings.score_min or not (0 <= u <= width and 0 <= v <= height):",
     "if score < settings.score_min:", "out-of-frame joints lifted"),
    ("observe.py", 'if reason is None and xyz[2] + 3 * math.sqrt(cov[2][2]) < 0:', 'if False:',
     "below_floor rejection removed"),
    ("replay.py", "if take.rows[index][\"t_host\"] != frame[\"capture_ts\"]:", "if False:",
     "row capture_ts vs take t_host check removed"),
]


def run(mutant):
    rel, old, new, what = mutant
    work = Path(tempfile.mkdtemp(prefix="vi-mut-den-observe-"))
    try:
        for part in ("tools/den-observe", "tools/den-take", "docs/architecture"):
            shutil.copytree(root / part, work / part, ignore=shutil.ignore_patterns("__pycache__"))
        path = work / "tools" / "den-observe" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return what, "not-run", f"pattern found {text.count(old)} times"
        path.write_text(text.replace(old, new), encoding="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-observe", "-f"],
                              cwd=work, capture_output=True, text=True, timeout=900)
        lines = proc.stderr.strip().splitlines()
        ran = next((l for l in lines if l.startswith("Ran ")), "?")
        failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
        return what, ("caught" if proc.returncode else "survived"), (failed[0] if failed else ran + " " + lines[-1])
    finally:
        shutil.rmtree(work, ignore_errors=True)


results = [run(m) for m in MUTANTS]
for what, status, detail in results:
    print(f"RESULT {status:9s} | {what:48s} | {detail}")
print(f"RESULT survived {sum(r[1] == 'survived' for r in results)} of {len(results)} against the full suite")

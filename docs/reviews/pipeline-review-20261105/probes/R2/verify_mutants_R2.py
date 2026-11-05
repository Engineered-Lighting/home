#!/usr/bin/env python3
"""verify_mutants_R2: independent re-run of the mutants cited by depth-gap-rule-untested,
gate-verdict-untested and record-checks-untested, against den-observe's WHOLE suite.

What it does
    Copies tools/den-observe, tools/den-take and docs/architecture/live-v1 from the
    given checkout into <mut root>/<mutant>/ (default mut root: the scratchpad's
    mut/R2-reproduce), applies one exact text replacement (refusing unless the
    original occurs exactly once), and runs `python -B -m unittest discover -f -s .`
    in the copied tool folder. A mutant 'survived' when the whole suite still passes.
    The unmutated copy must pass first. The checkout itself is never modified.

How to run
    python3 -B verify_mutants_R2.py <branch checkout root> [<mut root>] [mutant-id ...]

Synthetic data only (the suite's own fakes); nothing connects anywhere.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
default_mut = Path(__file__).resolve().parents[2] / "mut" / "R2-reproduce"
mut = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 and not sys.argv[2].startswith("V") else default_mut
only = {a for a in sys.argv[2:] if a.startswith("V")}

MUTANTS = [
    ("V01-gap-ge", "observe.py", "if abs(gap) > settings.max_gap_s:", "if abs(gap) >= settings.max_gap_s:"),
    ("V02-gap-no-abs", "observe.py", "if abs(gap) > settings.max_gap_s:", "if gap > settings.max_gap_s:"),
    ("V03-gap-0.0679", "METHOD.md", "max_depth_gap_s = 0.067 ", "max_depth_gap_s = 0.0679"),
    ("V08-expired-ge", "observe.py", '("expired" if age > settings.max_age_s else "fresh")',
     '("expired" if age >= settings.max_age_s else "fresh")'),
    ("V09-gate-fraction-lt", "replay.py", "fraction is not None and fraction <= limit",
     "fraction is not None and fraction < limit"),
    ("V10-gate-ignores-problems", "replay.py", '"pass": bad == 0 and freshness["pass"]}', '"pass": freshness["pass"]}'),
    ("V12-check-no-stereo-from-unused", "observe.py", 'problems.append("stereo lift from depth that was not used")',
     "pass"),
    ("V13-check-no-depth-ts", "observe.py", 'problems.append("keypoints without depth_ts")', "pass"),
    ("V14-no-below-floor", "observe.py", 'reason = "below_floor"', "pass"),
]


def copy_tree(dst):
    if dst.exists():
        shutil.rmtree(dst)
    for rel in ("tools/den-observe", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(root / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__"))


def run_all(tool):
    start = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "."], cwd=tool,
                          capture_output=True, text=True, timeout=900)
    lines = proc.stderr.splitlines()
    tail = " ".join(l for l in lines if l.startswith(("Ran ", "OK", "FAILED")))
    first = next((l for l in lines if l.startswith(("FAIL:", "ERROR:"))), "")
    return proc.returncode == 0, f"{tail} [{time.time() - start:.0f}s wall]", first


mut.mkdir(parents=True, exist_ok=True)
base = mut / "base"
copy_tree(base)
ok, tail, first = run_all(base / "tools" / "den-observe")
print(f"BASELINE whole suite: {'PASS' if ok else 'FAIL'} ({tail}) {first}", flush=True)
if not ok:
    sys.exit("baseline fails; mutants cannot be judged")
shutil.rmtree(base, ignore_errors=True)
rows = []
for mid, rel, old, new in MUTANTS:
    if only and mid not in only:
        continue
    work = mut / mid
    copy_tree(work)
    path = work / "tools" / "den-observe" / rel
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        rows.append((mid, rel, "not-run", f"original occurs {text.count(old)} times"))
        print(f"{mid}: NOT RUN", flush=True)
        continue
    line = text[:text.index(old)].count("\n") + 1
    path.write_text(text.replace(old, new), encoding="utf-8")
    ok, tail, first = run_all(work / "tools" / "den-observe")
    verdict = "SURVIVED" if ok else "CAUGHT"
    rows.append((mid, f"{rel}:{line}", verdict, tail if ok else first))
    print(f"{mid} {rel}:{line}: {verdict} -- {tail} {first}", flush=True)
    shutil.rmtree(work, ignore_errors=True)
print("\nRESULT table")
for r in rows:
    print(f"{r[0]:34} {r[1]:18} {r[2]:9} {r[3]}")
print(f"RESULT {sum(r[2] == 'CAUGHT' for r in rows)} caught, {sum(r[2] == 'SURVIVED' for r in rows)} survived")

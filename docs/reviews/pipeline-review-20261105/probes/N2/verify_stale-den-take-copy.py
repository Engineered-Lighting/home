#!/usr/bin/env python3
"""Verify finding stale-den-take-copy (unit N2, claude/den-n2 carries tools/den-take from den-label).

What it checks (synthetic data, temp dirs only; the den-take tests' fake camera binds 127.0.0.1 only):
  A. CHECK-V1.md in the den-n2 checkout differs from rung-r's (sha256), and den-n2's check.read_take refuses a
     synthetic take whose take.json records rung-r's CHECK-V1 sha256 ("recorded under a different CHECK file").
  B. rung-r's own regression test for commit f693b51a ("Survive a second Ctrl+C while take.py waits for the
     poller"), run against a temp copy of den-n2's tools/den-take, i.e. den-n2's take.py: does the second
     Ctrl+C escape take.run? Control: the same test against a temp copy of rung-r's tools/den-take.
  C. Whether den-n2's own owner path (tools/den-n2/score.py) uses any den-take code that these two
     differences touch (CHECK sha check, take.run's finally block).

Run:  python3 -B verify_stale-den-take-copy.py <root of the claude/den-n2 checkout> [<root of claude/rung-r checkout>]
Default rung-r root: <den-n2 root>/../rung-r
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

N2 = Path(sys.argv[1]).resolve()
RR = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else N2.parent / "rung-r"
TEST = "test_den_take.TakeScriptTests.test_second_ctrl_c_during_poller_join_still_saves_the_take"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---- A ----
n2_check, rr_check = N2 / "tools/den-take/CHECK-V1.md", RR / "tools/den-take/CHECK-V1.md"
print(f"RESULT CHECK-V1 sha256 den-n2={sha(n2_check)[:12]} rung-r={sha(rr_check)[:12]}")
tmp = Path(tempfile.mkdtemp(prefix="n2-verify-take-"))
try:
    code = (f"import sys, json, pathlib; sys.path.insert(0, {str(N2 / 'tools/den-take')!r}); import check\n"
            f"d = pathlib.Path({str(tmp / 'take')!r}); d.mkdir()\n"
            "(d / 'frames.csv').write_text('file,n\\n', encoding='utf-8')\n"
            "t = {'schema': check.TAKE_SCHEMA, 'slates': [], 'cues': [{'cue': 'begin', 'pc_mono_s': 1.0}],\n"
            f"     'method': {{'file': check.METHOD_FILE, 'sha256': {sha(rr_check)!r}}}}}\n"
            "(d / 'take.json').write_text(json.dumps(t), encoding='utf-8')\n"
            "try:\n    check.read_take(d, check.load_method())\n    print('NOT REFUSED')\n"
            "except check.Refused as e:\n    print('REFUSED:', e)\n")
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
    msg = (out.stdout + out.stderr).strip()
    print(f"RESULT den-n2 check.read_take on a take recorded under rung-r's CHECK-V1: {msg}")
    print(("PASS" if "different CHECK file" in msg else "FAIL") +
          " A: den-n2's check.py refuses a take recorded under rung-r's CHECK-V1")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- B ----
def run_test(label, take_dir_src):
    work = Path(tempfile.mkdtemp(prefix=f"n2-verify-ctrlc-{label}-"))
    try:
        dst = work / "den-take"
        shutil.copytree(take_dir_src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(RR / "tools/den-take/test_den_take.py", dst / "test_den_take.py")  # rung-r's test file
        r = subprocess.run([sys.executable, "-B", "-m", "unittest", "-v", TEST], cwd=dst,
                           capture_output=True, text=True, timeout=300)
        tail = (r.stdout + r.stderr).strip().splitlines()
        verdict = "ok" if r.returncode == 0 else "FAILED"
        detail = next((line for line in tail if "AssertionError" in line or "escaped" in line), tail[-1])
        print(f"RESULT {label}: take.py sha256={sha(take_dir_src / 'take.py')[:12]} test {verdict}; {detail}")
        return r.returncode == 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


n2_ok = run_test("den-n2", N2 / "tools/den-take")
rr_ok = run_test("rung-r-control", RR / "tools/den-take")
print(("PASS" if (not n2_ok and rr_ok) else "FAIL") +
      " B: a second Ctrl+C during poller.join escapes den-n2's take.run (rung-r's copy survives it)")

# ---- C ----
score = (N2 / "tools/den-n2/score.py").read_text(encoding="utf-8")
used = sorted(set(re.findall(r"n1\.den_take\.([a-z_]+)", score)))
uses_check = sorted(set(re.findall(r"\bcheck\.(read_take|load_method|METHOD_FILE)\b", score)))
print(f"RESULT den-n2 score.py uses den-take: take.{used}; check-method calls: {uses_check or 'none'}")
readme = (N2 / "tools/den-take/README.md").read_text(encoding="utf-8")
m = re.search(r"worktree add \S+ (claude/[a-z0-9-]+)", readme)
print(f"RESULT den-take README (as carried in den-n2) tells the owner to record from: {m.group(1) if m else '?'}")
n2_readme = (N2 / "tools/den-n2/README.md").read_text(encoding="utf-8")
print(f"RESULT den-n2 README owner commands mention take.py/check.py of den-take: "
      f"{bool(re.search(r'den-take.(take|check)\.py', n2_readme))}")

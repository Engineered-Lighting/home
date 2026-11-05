#!/usr/bin/env python3
"""Probe: label.py's second-person re-run command fails with den-label's stale CHECK-V1.md copy.

What it checks
  den-label (a28130a4) carries rung R's tools/den-take from rung-r commit b337a7e2.
  rung-r later revised CHECK-V1.md in place (8b1c945c), so the two copies hash
  differently, and R's check.py refuses any take recorded under a different
  CHECK file. The den-label README says to run every command from the den-label
  worktree, and after the second-person key label.py prints
  "Re-run: python -B tools\\den-take\\check.py <takes>\\<take>", a path relative
  to that worktree. This probe:
    1. hashes both CHECK-V1.md copies;
    2. builds a synthetic take with rung-r's own make_take (so take.json records
       rung-r's CHECK-V1 sha, as rung-r's take.py would);
    3. runs den-label's tools/den-take/check.py on it from the den-label root
       (the printed command) and expects a refusal, exit 2, and no check file;
    4. runs rung-r's check.py on the same take for contrast (it gets past the
       method check and stops only at the interactive second-person prompt);
    5. prints the exact re-run line label.py's after_pass tells the owner.

How to run
  python3 probe_stale-check-v1-recheck-refused.py <den-label checkout> [<rung-r checkout>]
  (the rung-r checkout defaults to the sibling folder "rung-r").
Synthetic data only, in a temp dir; no network, no camera.
"""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = Path(sys.argv[1]).resolve()
    rung_r = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root.parent / "rung-r"
    mine, theirs = root / "tools/den-take/CHECK-V1.md", rung_r / "tools/den-take/CHECK-V1.md"
    print(f"RESULT den-label CHECK-V1.md sha256 {sha(mine)}")
    print(f"RESULT rung-r    CHECK-V1.md sha256 {sha(theirs)}")
    print(f"RESULT check.py identical: {sha(root / 'tools/den-take/check.py') == sha(rung_r / 'tools/den-take/check.py')}")
    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-stale-check-"))
    # 2. a take written by rung-r's helpers (take.json carries rung-r's CHECK-V1 sha)
    build = ("import sys; sys.path.insert(0, sys.argv[1]); import test_den_take as R; "
             "print(R.make_take(sys.argv[2], activity=20.0, commissioning=False))")
    folder = Path(subprocess.run([sys.executable, "-B", "-c", build, str(rung_r / "tools/den-take"), str(tmp)],
                                 check=True, capture_output=True, text=True).stdout.strip())
    print(f"RESULT synthetic take built with rung-r's make_take: {folder.name}")
    # 3. the command label.py prints, run from the den-label worktree
    run = subprocess.run([sys.executable, "-B", "tools/den-take/check.py", str(folder)], cwd=root,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    checks = sorted(p.name for p in folder.glob("check-*.json"))
    print(f"RESULT den-label check.py exit {run.returncode}: {(run.stderr or run.stdout).strip()}")
    refused = run.returncode == 2 and "different CHECK file" in run.stderr and not checks
    # 4. contrast: rung-r's check.py passes the method check (stops only at the console prompt)
    other = subprocess.run([sys.executable, "-B", "tools/den-take/check.py", str(folder)], cwd=rung_r,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print(f"RESULT rung-r check.py exit {other.returncode}: {(other.stderr or other.stdout).strip()}")
    # 5. the line label.py prints after the second-person key
    sys.path.insert(0, str(root / "tools/den-label"))
    import label  # noqa: E402
    out = io.StringIO()
    p = types.SimpleNamespace(take=folder.name, state=types.SimpleNamespace(folder=folder),
                              allow_commissioning=True, refused=None)
    label.after_pass(types.SimpleNamespace(takes=folder.parent), p, "second_person", out)
    rerun = [l for l in out.getvalue().splitlines() if l.startswith("Re-run")]
    print(f"RESULT label.py tells the owner: {rerun[0] if rerun else out.getvalue()!r}")
    if refused and "different CHECK file" not in other.stderr:
        print("FAIL den-label's copy of check.py refuses a take recorded under rung-r's CHECK-V1.md; "
              "the printed re-run command cannot add the missed second person's cut")
        return 1
    print("PASS the re-run command works on a rung-r take")
    return 0


if __name__ == "__main__":
    sys.exit(main())

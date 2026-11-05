#!/usr/bin/env python3
"""Verify finding mutant-mount-thresholds (rung 1, den-register check-mount).

Checks two things for each of three mutants of scene.check_mount:
  1. survival: the mutant applied to a temp copy of tools/den-register (with
     tools/den-take and docs/architecture/live-v1, git-initialised as the
     work-folder test needs) keeps the WHOLE test module green
     (python -B -m unittest -f test_den_register), not just MountTests;
  2. non-equivalence: on a synthetic signature the original and the mutated
     check_mount give different statuses, so a test could have caught it.

The checkout is never modified (bytecode writing is off; mutants live in temp
copies). Synthetic data only; nothing contacts a host.

    python3 -B verify_mutant-mount-thresholds.py <root of the rung-1-register checkout>
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()

MUTANTS = {
    "M12-min-cells-ignored": ("if compared < minimum_cells:", "if compared < 1:"),
    "M13-relative-tolerance-dropped": (
        "changed += abs(value - reference) > max(tolerance_mm, tolerance_fraction * reference)",
        "changed += abs(value - reference) > tolerance_mm"),
    "M14-changed-fraction-0.99": ('status = "changed" if changed > changed_fraction * compared',
                                  'status = "changed" if changed > min(0.99, 2 * changed_fraction) * compared'),
}
KW = dict(tolerance_mm=50, tolerance_fraction=0.03, changed_fraction=0.5, minimum_cells=16)  # proposed values


def copy_tree(dest):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "-q", str(dest)], check=True)


def run_module(dest):
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_den_register"],
                         cwd=dest / "tools" / "den-register", capture_output=True, text=True, timeout=600)
    last = [ln for ln in out.stderr.splitlines() if ln.strip()]
    return out.returncode, (last[-1] if last else "")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sig(cells):
    return {"grid": [8, 8], "shape": [400, 640], "cells": cells}


def grid(fill):
    return [[fill(r, c) for c in range(8)] for r in range(8)]


# Distinguishing inputs, one per mutant: (reference signature, current signature)
CASES = {
    # only 10 comparable cells (the rest invalid in the current frame), none moved
    "M12-min-cells-ignored": (sig(grid(lambda r, c: 3500.0)),
                              sig(grid(lambda r, c: 3500.0 if r * 8 + c < 10 else None))),
    # every cell 80 mm nearer at 3.5 m: inside 3% (105 mm), outside 50 mm
    "M13-relative-tolerance-dropped": (sig(grid(lambda r, c: 3500.0)), sig(grid(lambda r, c: 3420.0))),
    # 40 of 64 cells (62.5%) moved by 700 mm, as a turned camera would
    "M14-changed-fraction-0.99": (sig(grid(lambda r, c: 3500.0)),
                                  sig(grid(lambda r, c: 2800.0 if c < 5 else 3500.0))),
}

with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    clean = base / "clean"
    copy_tree(clean)
    code, tail = run_module(clean)
    print(f"RESULT baseline full module: exit {code}, {tail}")
    if code != 0:
        print("FAIL baseline does not pass; cannot judge mutants")
        sys.exit(2)
    original = load(ROOT / "tools" / "den-register" / "scene.py", "scene_original")
    rows = []
    for mid, (old, new) in MUTANTS.items():
        dest = base / mid
        copy_tree(dest)
        path = dest / "tools" / "den-register" / "scene.py"
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            rows.append((mid, "not-run", f"pattern occurs {text.count(old)} times", "", ""))
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        code, tail = run_module(dest)
        mutated = load(path, "scene_" + mid.replace("-", "_").replace(".", "_"))
        ref, cur = CASES[mid]
        a = original.check_mount(ref, cur, **KW)
        b = mutated.check_mount(ref, cur, **KW)
        rows.append((mid, "survived" if code == 0 else "caught", tail,
                     f"{a['status']} ({a['changed_cells']}/{a['compared_cells']})",
                     f"{b['status']} ({b['changed_cells']}/{b['compared_cells']})"))

print()
print(f"{'mutant':<34} {'full module':<11} {'original on case':<24} mutant on case")
for mid, result, tail, a, b in rows:
    print(f"{mid:<34} {result:<11} {a:<24} {b}")
for mid, result, tail, a, b in rows:
    distinct = a.split()[0] != b.split()[0]
    verdict = "PASS(defect reproduced)" if result == "survived" and distinct else "FAIL(not reproduced)"
    print(f"RESULT {mid}: {result} the full module ({tail}); non-equivalent: {distinct} -> {verdict}")

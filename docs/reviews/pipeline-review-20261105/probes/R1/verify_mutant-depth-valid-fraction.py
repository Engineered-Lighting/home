#!/usr/bin/env python3
"""Verify finding mutant-depth-valid-fraction (rung 1, den-register depth_at).

  1. survival: `if not values or len(values) < min_valid_fraction * total:`
     changed to `if not values:` in a temp copy of tools/den-register keeps the
     whole test module green;
  2. non-equivalence: a click whose 7x7 window holds 1 (and 10) valid depth
     pixels of 49 is refused (depth_missing) by the original and accepted by
     the mutant, which returns that single pixel's depth.

The checkout is never modified. Synthetic data only; nothing contacts a host.

    python3 -B verify_mutant-depth-valid-fraction.py <root of the rung-1-register checkout>
"""
import array
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
OLD = "if not values or len(values) < min_valid_fraction * total:"
NEW = "if not values:"


def copy_tree(dest):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "-q", str(dest)], check=True)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def window_bundle(valid, u=300, v=200, value=2345):
    """A depth plane that is invalid (0) everywhere except `valid` pixels of the 7x7 window at (u, v)."""
    plane = array.array("H", [0]) * (640 * 400)
    spots = [(x, y) for y in range(v - 3, v + 4) for x in range(u - 3, u + 4)][:valid]
    for x, y in spots:
        plane[y * 640 + x] = value
    return {"depth": plane}


def outcome(module, bundle):
    try:
        got = module.depth_at(bundle, 300, 200, window_px=3, min_valid_fraction=0.5)
        return f"accepted z={got['z_m']} m from {got['valid']} of {got['pixels']} pixels"
    except module.Refused as exc:
        return f"refused {exc.reason}"


with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    dest = base / "mut"
    copy_tree(dest)
    path = dest / "tools" / "den-register" / "scene.py"
    text = path.read_text(encoding="utf-8")
    assert text.count(OLD) == 1, "pattern not unique"
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_den_register"],
                         cwd=dest / "tools" / "den-register", capture_output=True, text=True, timeout=600)
    tail = [ln for ln in out.stderr.splitlines() if ln.strip()][-1]
    print(f"RESULT mutant full module: exit {out.returncode}, {tail}")
    original = load(ROOT / "tools" / "den-register" / "scene.py", "scene_original")
    mutated = load(path, "scene_mutated")
    distinct = False
    for valid in (1, 10, 24, 25, 49):
        a, b = outcome(original, window_bundle(valid)), outcome(mutated, window_bundle(valid))
        distinct |= a.split()[0] != b.split()[0]
        print(f"RESULT {valid:>2} of 49 valid: original {a}; mutant {b}")
    survived = out.returncode == 0
    print(("PASS(defect reproduced)" if survived and distinct else "FAIL(not reproduced)")
          + f" mutant survives the full module: {survived}; non-equivalent: {distinct}")

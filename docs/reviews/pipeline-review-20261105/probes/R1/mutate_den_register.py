#!/usr/bin/env python3
"""Mutation runner for tools/den-register (rung 1) gate and verdict logic.

For each mutant: copy tools/den-register, tools/den-take and
docs/architecture/live-v1 from the checkout into a fresh temp folder (made a
git checkout with `git init`, as the work-folder test needs), apply one exact
text replacement, run `python -B -m unittest -f test_den_register` (or a
narrower class), and record caught / survived. Prints a table. The checkout is
never modified. Synthetic data only; nothing contacts a host.

    python3 -B mutate_den_register.py <root of the rung-1-register checkout> [mutant-id ...]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ONLY = set(sys.argv[2:])

# id, file (relative to tools/den-register), old, new, tests to run (module or module.Class)
MUTANTS = [
    ("M1-check-gate-loosened-3mm", "register.py", 'if p["error_m"] > gate:', 'if p["error_m"] > gate + 0.003:',
     "test_den_register.FitTests"),
    ("M2-check-gate-ge", "register.py", 'if p["error_m"] > gate:', 'if p["error_m"] >= gate:',
     "test_den_register.FitTests"),
    ("M3-fit-residual-gate-doubled", "register.py", "if residual > gate:", "if residual > 2 * gate:",
     "test_den_register.FitTests"),
    ("M4-collinear-disabled", "register.py", 'if spread < m["min_spread_m"]:', 'if spread < 0:',
     "test_den_register.FitTests"),
    ("M5-collinear-floor-halved", "register.py", 'if spread < m["min_spread_m"]:',
     'if spread < m["min_spread_m"] / 2:', "test_den_register.FitTests"),
    ("M6-min-checks-off-by-one", "register.py", 'if len(check_pairs) < m["min_checks"]:',
     'if len(check_pairs) < m["min_checks"] - 1:', "test_den_register.FitTests"),
    ("M7-min-fit-pairs-off-by-one", "register.py", 'if len(fit_pairs) < m["min_fit_pairs"]:',
     'if len(fit_pairs) < m["min_fit_pairs"] - 1:', "test_den_register.FitTests"),
    ("M8-slots-open-refusal-skipped", "register.py", "if m.scored() is None:\n            raise Refused(\"slots_open\", \"close these in REGISTER-V1.md before a scored fit: \"",
     "if False:\n            raise Refused(\"slots_open\", \"close these in REGISTER-V1.md before a scored fit: \"",
     "test_den_register"),
    ("M9-error-bound-drops-loo-and-checks", "register.py",
     'bound = max(result["residuals_m"] + [p["error_m"] for p in check_pairs] + (result["loo_m"] or []))',
     'bound = max(result["residuals_m"])', "test_den_register"),
    ("M10-camera-below-floor-disabled", "register.py", "if translation[2] <= 0:", "if translation[2] < -10:",
     "test_den_register.FitTests"),
    ("M11-depth-valid-fraction-ignored", "scene.py",
     "if not values or len(values) < min_valid_fraction * total:", "if not values:", "test_den_register"),
    ("M12-mount-min-cells-ignored", "scene.py", "if compared < minimum_cells:", "if compared < 1:",
     "test_den_register.MountTests"),
    ("M13-mount-relative-tolerance-dropped", "scene.py",
     "changed += abs(value - reference) > max(tolerance_mm, tolerance_fraction * reference)",
     "changed += abs(value - reference) > tolerance_mm", "test_den_register.MountTests"),
    ("M14-mount-changed-fraction-doubled", "scene.py", 'status = "changed" if changed > changed_fraction * compared',
     'status = "changed" if changed > min(0.99, 2 * changed_fraction) * compared', "test_den_register.MountTests"),
    ("M15-stale-take-after-invalidation-allowed", "register.py",
     'if gone and take.get("ended_unix", 0) <= gone.get("take_ended_unix", 0):',
     'if gone and take.get("ended_unix", 0) < gone.get("take_ended_unix", 0) - 1e6:', "test_den_register.MountTests"),
    ("M16-round-trip-check-disabled", "register.py", "if resolved[\"invalidated\"] or math.dist(back, fitted) > 1e-9:",
     "if resolved[\"invalidated\"] or math.dist(back, fitted) > 1e9:", "test_den_register"),
    ("M17-skew-and-instead-of-or", "scene.py", 'if left["sequence"] != depth["sequence"] or skew > max_skew_s:',
     'if left["sequence"] != depth["sequence"] and skew > max_skew_s:', "test_den_register"),
    ("M18-loo-needs-five", "rigid.py", "if len(source) >= 4:", "if len(source) >= 5:", "test_den_register.RigidTests"),
]


def make_copy(tmp):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__", "work"))
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)


def run(tmp, target):
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", target], cwd=tmp / "tools" / "den-register",
                         capture_output=True, text=True, timeout=600)
    tail = (out.stderr.strip().splitlines() or [""])[-1]
    return out.returncode, tail, out.stderr


results = []
with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    clean = base / "clean"
    make_copy(clean)
    code, tail, _ = run(clean, "test_den_register")
    print(f"baseline (unmutated copy): exit {code}, {tail}")
    if code != 0:
        print("FAIL baseline does not pass; mutants not run")
        sys.exit(2)
    for mid, rel, old, new, target in MUTANTS:
        if ONLY and mid not in ONLY:
            continue
        tmp = base / mid
        make_copy(tmp)
        path = tmp / "tools" / "den-register" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            results.append((mid, rel, "not-run", f"pattern found {text.count(old)} times"))
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        code, tail, err = run(tmp, target)
        caught = code != 0
        failing = [line for line in err.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
        results.append((mid, rel, "caught" if caught else "survived", (failing[0] if failing else tail)[:110]))
        shutil.rmtree(tmp)

print()
print(f"{'mutant':<44} {'file':<12} {'result':<9} by / note")
for mid, rel, result, note in results:
    print(f"{mid:<44} {rel:<12} {result:<9} {note}")
survived = [r for r in results if r[2] == "survived"]
print(f"RESULT {len(results)} mutants: {sum(r[2] == 'caught' for r in results)} caught, {len(survived)} survived, "
      f"{sum(r[2] == 'not-run' for r in results)} not run")

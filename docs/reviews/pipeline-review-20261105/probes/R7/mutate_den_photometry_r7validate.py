#!/usr/bin/env python3
"""(R7 validate lens) Mutation runner for tools/den-photometry (rung 7): applies each mutant to a temp copy,
runs the narrowest tests that should catch it (failfast), and prints a caught/survived table.

Run:
    python3 mutate_den_photometry_r7validate.py <root of the branch checkout> [--full] [mutant-id ...]

--full runs the whole den-photometry suite for each selected mutant (slow, ~2-4 min each) instead of the
narrow tests. The copy gets an empty .git folder so the suite's "inside a git checkout" test sees a checkout.
Only temp folders (prefix r7mut-) are written; the checkout is never modified. Synthetic data and 127.0.0.1
fakes only (the suite's own).

Result at 1931c83172c9 (narrow sets, run 2026-11-05; also in mutate_narrow.out):
  M1 open-proposals-ignored  survived | M2 held-out-tol-x1.5 survived | M3 held-out-no-abs caught
  M4 invariance-tol-x1.5     survived | M5 drift-x1.5        survived | M6 daylight-by-fit  survived
  M7 fit-latest caught | M8 no-fit-not-missing caught | M9 score-no-ready caught | M10 capture-no-ready caught
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
FULL = "--full" in sys.argv
PICK = [a for a in sys.argv[2:] if not a.startswith("--")]
CARD, PHOT = "tools/den-photometry/card.py", "tools/den-photometry/photometry.py"
FAST = ["test_card"]
SCORE = ["test_card", "test_photometry.ScoreTests", "test_photometry.WorksheetTests"]
REFUSE = ["test_card.MethodTests", "test_photometry.RefusalTests", "test_photometry.CaptureTests"]

MUTANTS = [
    ("M1-open-proposals-ignored", CARD, 144,
     "    if method.open:\n",
     "    if any(method[n] is None for n in method.open):  # MUTANT: refuse only empty open slots\n", REFUSE),
    ("M2-held-out-tol-x1.5", CARD, 336,
     "abs(error) <= tolerance + 1e-9}", "abs(error) <= 1.5 * tolerance + 1e-9}", SCORE),
    ("M3-held-out-no-abs", CARD, 336,
     "abs(error) <= tolerance + 1e-9}", "error <= tolerance + 1e-9}", SCORE),
    ("M4-invariance-tol-x1.5", CARD, 293,
     'd <= v["invariance_tolerance_percent"] + 1e-12', 'd <= 1.5 * v["invariance_tolerance_percent"] + 1e-12', SCORE),
    ("M5-drift-x1.5", CARD, 309,
     'drift <= v["meter_drift_max_percent"] + 1e-12', 'drift <= 1.5 * v["meter_drift_max_percent"] + 1e-12', SCORE),
    ("M6-daylight-by-fit", CARD, 423,
     'if not any(c["illuminant"] == v["daylight_class"] for c in held):',
     'if not any(c["illuminant"] == v["daylight_class"] for c in held + [g["fit"] for g in selection["groups"].values() if g["fit"]]):',
     SCORE),
    ("M7-fit-latest", CARD, 400,
     "fit = candidates[0] if candidates else None", "fit = candidates[-1] if candidates else None", FAST),
    ("M8-no-fit-not-missing", CARD, 426,
     'for k, g in sorted(selection["groups"].items()) if g["fit"] is None]',
     'for k, g in sorted(selection["groups"].items()) if False]', FAST),
    ("M9-score-no-ready", PHOT, 581,
     "    ready(method, den_method)\n    root = check_folder(root, token_file, \"photometry folder\")\n    days = day_folders(root)",
     "    root = check_folder(root, token_file, \"photometry folder\")\n    days = day_folders(root)", REFUSE),
    ("M10-capture-no-ready", PHOT, 373,
     "    ready(method, den_method)\n    check_labels(", "    check_labels(", REFUSE),
]


def run(mutant):
    mid, rel, line, old, new, tests = mutant
    with tempfile.TemporaryDirectory(prefix="r7mut-") as tmp:
        copy = Path(tmp) / "co"
        for sub in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
            shutil.copytree(ROOT / sub, copy / sub, ignore=shutil.ignore_patterns("__pycache__"))
        (copy / ".git").mkdir()
        target = copy / rel
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return mid, rel, line, "not-run", f"pattern found {text.count(old)} times", 0.0
        target.write_text(text.replace(old, new), encoding="utf-8")
        cmd = [sys.executable, "-B", "-m", "unittest", "-f"]
        cmd += ["discover", "-s", "tools/den-photometry"] if FULL else tests
        start = time.time()
        proc = subprocess.run(cmd, cwd=copy / "tools/den-photometry" if not FULL else copy,
                              capture_output=True, text=True, timeout=900)
        took = time.time() - start
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        first_fail = next((l for l in tail if l.startswith(("FAIL:", "ERROR:"))), "")
        return mid, rel, line, ("caught" if proc.returncode else "survived"), first_fail or tail[-1], took


if __name__ == "__main__":
    print(f"{'mutant':28} {'file:line':36} {'result':9} {'secs':>5}  caught by")
    for m in MUTANTS:
        if PICK and m[0] not in PICK:
            continue
        mid, rel, line, result, why, took = run(m)
        print(f"{mid:28} {rel + ':' + str(line):36} {result:9} {took:5.0f}  {why}")
        sys.stdout.flush()

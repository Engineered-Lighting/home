#!/usr/bin/env python3
"""mutate_den_observe_validate: targeted mutants on den-observe's gate logic, each run against its tests.

(VALIDATE lens's runner; a different reviewer's runner is mutate_den_observe.py.)

What it does
    For each mutant below it copies tools/den-observe, tools/den-take and
    docs/architecture/live-v1 from the given checkout into a fresh temporary
    folder, applies one exact text replacement (refusing if the original text
    does not occur exactly once), and runs the narrowest test class that
    should catch it with unittest's failfast flag. A mutant whose narrow class
    still passes is then run against the whole den-observe suite
    (`python -m unittest discover -f -s .`); it is 'survived' only if the
    whole suite passes too. Prints a caught/survived table. The checkout itself
    is never modified.

How to run
    python3 mutate_den_observe_validate.py <branch checkout root> [mutant-id ...]

Synthetic data only (the suite's own fakes); nothing connects anywhere.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
only = set(sys.argv[2:])

# id, file (under tools/den-observe), original text, mutated text, narrow test target
MUTANTS = [
    ("M01-gap-ge", "observe.py", "if abs(gap) > settings.max_gap_s:", "if abs(gap) >= settings.max_gap_s:",
     "test_observe.DepthTimingTests"),
    ("M02-gap-no-abs", "observe.py", "if abs(gap) > settings.max_gap_s:", "if gap > settings.max_gap_s:",
     "test_observe.DepthTimingTests"),
    ("M03-gap-0.0679", "METHOD.md", "max_depth_gap_s = 0.067 ", "max_depth_gap_s = 0.0679",
     "test_observe.DepthTimingTests"),
    ("M04-along-drop-z", "observe.py",
     "along = z * z * settings.disparity_sigma_px / (camera.f_left * camera.baseline_m)",
     "along = z * settings.disparity_sigma_px / (camera.f_left * camera.baseline_m)",
     "test_observe.GeometryTests"),
    ("M05-plane-no-obliquity", "observe.py", "along = sigma_h / abs(direction[2])", "along = sigma_h",
     "test_observe.DepthTimingTests"),
    ("M06-torso-indep-over-n", "observe.py",
     "independent = _scale(g.cov_add(*(p[1] for p in used)), 1.0 / (n * n))",
     "independent = _scale(g.cov_add(*(p[1] for p in used)), 1.0 / n)",
     "test_observe.GeometryTests"),
    ("M07-torso-no-cal", "observe.py",
     "return xyz, g.cov_add(independent, common, g.isotropic(camera.error_bound_m))",
     "return xyz, g.cov_add(independent, common)",
     "test_observe.GeometryTests"),
    ("M08-expired-ge", "observe.py",
     'status = "unknown" if age is None else ("expired" if age > settings.max_age_s else "fresh")',
     'status = "unknown" if age is None else ("expired" if age >= settings.max_age_s else "fresh")',
     "test_observe.FreshnessTests"),
    ("M09-gate-fraction-lt", "replay.py", "fraction is not None and fraction <= limit",
     "fraction is not None and fraction < limit", "test_observe.FreshnessTests"),
    ("M10-gate-ignores-problems", "replay.py", '"pass": bad == 0 and freshness["pass"]}',
     '"pass": freshness["pass"]}', "test_observe.FreshnessTests"),
    ("M11-scored-ignores-open", "method.py", "return dict(self.values) if not self.open else None",
     "return dict(self.values)", "test_observe.FreshnessTests"),
    ("M12-check-no-stereo-from-unused", "observe.py",
     'problems.append("stereo lift from depth that was not used")', "pass", "test_observe.ContractTests"),
    ("M13-check-no-depth-ts", "observe.py", 'problems.append("keypoints without depth_ts")', "pass",
     "test_observe.ContractTests"),
    ("M14-no-below-floor", "observe.py", 'reason = "below_floor"', "pass", "test_observe.GeometryTests"),
    ("M15-torso-all-joints", "observe.py",
     'used = [lifted[j] for j in TORSO if lifted.get(j)',
     'used = [lifted[j] for j in range(17) if lifted.get(j)', "test_observe.GeometryTests"),
    ("M16-scored-arrival-clock", "replay.py", 'if scored and clock["source"] != "slate":',
     "if False:", "test_observe.FreshnessTests"),
]


def copy_tree(dst):
    for rel in ("tools/den-observe", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(root / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__"))


def run_tests(tool, target):
    args = [sys.executable, "-B", "-m", "unittest"]
    args += ["discover", "-f", "-s", "."] if target == "ALL" else ["-f", target]
    proc = subprocess.run(args, cwd=tool, capture_output=True, text=True, timeout=600)
    lines = proc.stderr.splitlines()
    tail = [line for line in lines if line.startswith(("Ran ", "OK", "FAILED"))]
    first_fail = next((line for line in lines if line.startswith(("FAIL:", "ERROR:"))), "")
    if proc.returncode != 0 and not first_fail:
        first_fail = "no test ran: " + (lines[-1] if lines else f"exit {proc.returncode}")
    return proc.returncode == 0, " ".join(tail), first_fail


results = []
with tempfile.TemporaryDirectory(prefix="mutate-den-observe-validate-") as tmp:
    base = Path(tmp) / "base"
    copy_tree(base)
    targets = sorted({m[4] for m in MUTANTS if not only or m[0] in only}) + ["ALL"]
    for target in targets:  # the unmutated suite must pass on each target first
        ok, tail, failed = run_tests(base / "tools" / "den-observe", target)
        print(f"BASELINE {target}: {'PASS' if ok else 'FAIL'} ({tail}) {failed}", flush=True)
        if not ok:
            sys.exit("baseline fails; mutants cannot be judged")
    for mid, rel, old, new, target in MUTANTS:
        if only and mid not in only:
            continue
        work = Path(tmp) / mid
        copy_tree(work)
        path = work / "tools" / "den-observe" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            results.append((mid, rel, "not-run", f"original text occurs {text.count(old)} times"))
            print(f"{mid}: NOT RUN (original text occurs {text.count(old)} times)", flush=True)
            continue
        line = text[:text.index(old)].count("\n") + 1
        path.write_text(text.replace(old, new), encoding="utf-8")
        ok, tail, failed = run_tests(work / "tools" / "den-observe", target)
        if not ok:
            verdict, by = "caught", f"{target}: {failed}"
        else:
            ok_all, tail_all, failed_all = run_tests(work / "tools" / "den-observe", "ALL")
            verdict, by = ("survived", f"whole suite passes ({tail_all})") if ok_all else ("caught", f"ALL: {failed_all}")
        results.append((mid, f"{rel}:{line}", verdict, by))
        print(f"{mid} {rel}:{line}: {verdict.upper()} -- {by}", flush=True)
        shutil.rmtree(work, ignore_errors=True)

print("\nRESULT table")
print(f"{'mutant':34} {'where':22} {'result':9} by")
for mid, where, verdict, by in results:
    print(f"{mid:34} {where:22} {verdict:9} {by}")
print(f"RESULT {sum(r[2] == 'caught' for r in results)} caught, {sum(r[2] == 'survived' for r in results)} survived, "
      f"{sum(r[2] == 'not-run' for r in results)} not run")

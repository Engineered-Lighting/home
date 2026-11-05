#!/usr/bin/env python3
"""mutate_den_observe: apply one mutant at a time to a temp copy of den-observe and run its tests.

Each mutant is an exact string replacement in one file under tools/den-observe
that weakens rung 2's gate logic. For each, the tools/den-observe, tools/den-take
and docs/architecture trees are copied to a fresh temp dir, the mutant applied
(it must match exactly once), and `python3 -B -m unittest -f` run on the narrowest
test module/class expected to catch it. Prints a caught/survived table.

    python3 mutate_den_observe.py <rung-2-observe checkout root>

Synthetic data only (the suite's own); temp dirs only; never touches the checkout.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
MUTANTS = [
    ("observe.py", "if abs(gap) > settings.max_gap_s:", "if abs(gap) > 2 * settings.max_gap_s:",
     "depth staleness limit doubled (134 ms)", "test_observe.DepthTimingTests"),
    ("observe.py", "if abs(gap) > settings.max_gap_s:", "if gap > settings.max_gap_s:",
     "one-sided gap: depth >67 ms AFTER the RGB is used", "test_observe.DepthTimingTests"),
    ("observe.py", 'if fresh and fresh.get("depth") != "used" and any(e.get("lift") == "stereo" for e in joints or []):',
     'if False:', "gate check no longer flags stereo lift from unused depth", "test_observe.ContractTests"),
    ("observe.py", "common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / n)",
     "common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / (n * n))",
     "torso: common plane-height error divided by n^2 (too small)", "test_observe"),
    ("observe.py", "if score < settings.score_min or not (0 <= u <= width and 0 <= v <= height):",
     "if score < settings.score_min:", "joints outside the RGB frame are lifted", "test_observe"),
    ("observe.py", 'if reason is None and xyz[2] + 3 * math.sqrt(cov[2][2]) < 0:', 'if False:',
     "below-floor rejection removed", "test_observe"),
    ("replay.py", "\"pass\": fraction is not None and fraction <= limit and not counts[\"freshness\"].get(\"unknown\")}",
     "\"pass\": fraction is not None and fraction <= 1.0 and not counts[\"freshness\"].get(\"unknown\")}",
     "freshness gate ignores max_expired_fraction", "test_observe.FreshnessTests"),
    ("method.py", "return dict(self.values) if not self.open else None", "return dict(self.values)",
     "scored run allowed with open slots", "test_observe.FreshnessTests"),
    ("roommodel.py", "if current != transform_id:", "if False:",
     "superseded registration accepted", "test_observe.RegistrationTests"),
    ("roommodel.py", "if bound > max_error_bound_m:", "if bound > 10 * max_error_bound_m:",
     "registration error bound gate loosened 10x", "test_observe.RegistrationTests"),
    ("replay.py", "if take.rows[index][\"t_host\"] != frame[\"capture_ts\"]:", "if False:",
     "detector row whose capture_ts differs from the take frame accepted", "test_observe"),
]


def run(mutant):
    rel, old, new, what, target = mutant
    work = Path(tempfile.mkdtemp(prefix="mut-den-observe-"))
    try:
        for part in ("tools/den-observe", "tools/den-take", "docs/architecture"):
            shutil.copytree(root / part, work / part)
        path = work / "tools" / "den-observe" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return what, rel, target, "not-run", f"pattern found {text.count(old)} times"
        path.write_text(text.replace(old, new), encoding="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", target],
                              cwd=work / "tools" / "den-observe", capture_output=True, text=True, timeout=600)
        tail = (proc.stderr.strip().splitlines() or [""])[-1]
        failed = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        return what, rel, target, ("caught" if proc.returncode else "survived"), (failed[0] if failed else tail)
    finally:
        shutil.rmtree(work, ignore_errors=True)


rows = [run(m) for m in MUTANTS]
print(f"{'result':9s} | {'file':12s} | {'mutant':62s} | caught by")
for what, rel, target, result, detail in rows:
    print(f"{result:9s} | {rel:12s} | {what:62s} | {detail if result == 'caught' else target + ' (' + detail + ')'}")
print(f"RESULT caught {sum(r[3] == 'caught' for r in rows)}, survived {sum(r[3] == 'survived' for r in rows)}, "
      f"not-run {sum(r[3] == 'not-run' for r in rows)}")

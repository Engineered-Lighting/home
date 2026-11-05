#!/usr/bin/env python3
"""Mutation runner for tools/den-n2/score.py (N2's need-test scorer).

What it checks: whether den-n2's own test suite (test_den_n2.py) fails when the
gate / verdict / statistics logic in score.py is broken one targeted way at a
time. Each mutant is applied to a fresh temporary copy of the branch's
tools/den-n2, tools/den-label and tools/den-take (made a throwaway git repo,
because two tests assume a git checkout), then the suite runs with failfast.
A mutant is "caught" when the suite fails, "survived" when it still passes.

How to run (stdlib only, synthetic data only, temp dirs only):

    python3 -B mutate_score.py <root of the claude/den-n2 checkout> [mutant-id ...]

It never writes inside <root>. Prints one RESULT line per mutant and a table.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    # (id, description, old, new)
    ("M01-cut-fine-strict", "band_of: level >= cut.fine becomes > (a level of exactly 0.6 is no longer fine)",
     'return "fine" if level >= gate["cut.fine"]', 'return "fine" if level > gate["cut.fine"]'),
    ("M02-ece-9-bins", "ECE binning uses bins-1 equal-width bins instead of 10",
     "b = ece[name][min(int(conf * bins), bins - 1)]", "b = ece[name][min(int(conf * (bins - 1)), bins - 1)]"),
    ("M03-stale-is-a-hit", "fine recall counts a stale fine level as a hit (owner/ladder: stale is a miss)",
     'ratio["fine_recall"][0] += s.fresh and fine', 'ratio["fine_recall"][0] += fine'),
    ("M04-no-days-rule", "sample gates drop the >= 2 days condition",
     ' and len(days) >= gate["sample.days"]', ""),
    ("M05-units-off-by-one", "sample gate needs n > 30 instead of n >= 30",
     "met = n >= needed and", "met = n > needed and"),
    ("M06-bootstrap-90pct", "bootstrap interval uses 5%/95% tails (90%) instead of 2.5%/97.5%",
     "        return None\n    tail = (1 - level) / 2\n    return [percentile(values, tail)",
     "        return None\n    tail = (1 - level)\n    return [percentile(values, tail)"),
    ("M07-identity-resample", "bootstrap resamples every session once (no resampling): the interval collapses",
     "return [[rng.randrange(n) for _ in range(n)] for _ in range(rounds)]",
     "return [list(range(n)) for _ in range(rounds)]"),
    ("M08-no-data-passes", "a gated metric with no data no longer counts as straddled (vacuous pass)",
     'in ("straddled", "no_data")]', 'in ("straddled",)]'),
    ("M09-skip-fixture-verify", "held-out run verifies only the labels folder, not the fixture folder",
     "for folder in (labels_dir, fixture_dir):", "for folder in (labels_dir,):"),
    ("M10-skip-tick-standin", "held-out run no longer refuses a replay log whose tick differs from GATE-V1",
     'if entry["tick_s"] != gate["tick_s"]:', "if False:"),
    ("M11-data-gate-vacuous", "per-second data gates count every session, data or not",
     'with_data = {an.session.take for an, s in zip(analyses, stats) if s["ratio"][name][1]}',
     "with_data = {an.session.take for an, s in zip(analyses, stats)}"),
    ("M12-ctrl-open-slot", "control: held-out no longer refuses an empty slot",
     "    if empty:\n        raise Refused(\"GATE-V1 has empty slots", "    if False:\n        raise Refused(\"GATE-V1 has empty slots"),
    ("M13-ctrl-floor-boundary", "control: a floor met at lo == threshold becomes straddled",
     '"met" if lo >= threshold', '"met" if lo > threshold'),
]


def run_suite(copy):
    t = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-n2"],
                          cwd=copy, capture_output=True, text=True, timeout=900)
    tail = (proc.stderr.strip().splitlines() or [""])
    first_fail = next((line for line in tail if line.startswith(("FAIL:", "ERROR:"))), "")
    return proc.returncode, first_fail, tail[-1], time.time() - t


def main():
    root = Path(sys.argv[1]).resolve()
    want = set(sys.argv[2:])
    src = root / "tools" / "den-n2" / "score.py"
    if not src.is_file():
        sys.exit(f"FAIL: {src} not found")
    rows = []
    with tempfile.TemporaryDirectory(prefix="n2-mut-") as tmp:
        base = Path(tmp) / "base"
        for sub in ("tools/den-n2", "tools/den-label", "tools/den-take"):
            shutil.copytree(root / sub, base / sub, ignore=shutil.ignore_patterns("__pycache__"))
        git = ["git", "-c", "user.email=m@example.invalid", "-c", "user.name=m"]
        subprocess.run(git + ["init", "-q", str(base)], check=True)
        subprocess.run(git + ["-C", str(base), "add", "-A"], check=True)
        subprocess.run(git + ["-C", str(base), "commit", "-qm", "base"], check=True)
        code, first, last, secs = run_suite(base)
        print(f"RESULT baseline exit={code} {last} ({secs:.0f} s)")
        if code != 0:
            print("FAIL: the unmutated suite fails in the temp copy; mutants would be meaningless")
            return 1
        for mid, desc, old, new in MUTANTS:
            if want and mid not in want:
                continue
            copy = Path(tmp) / mid
            shutil.copytree(base, copy)
            target = copy / "tools" / "den-n2" / "score.py"
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"RESULT {mid} not-run: the old text occurs {text.count(old)} times")
                rows.append((mid, "not-run", "", desc))
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            code, first, last, secs = run_suite(copy)
            result = "caught" if code != 0 else "survived"
            print(f"RESULT {mid} {result} ({secs:.0f} s) {first or last}")
            rows.append((mid, result, first, desc))
            shutil.rmtree(copy, ignore_errors=True)
    print("\nmutant                     result    caught by / description")
    for mid, result, first, desc in rows:
        print(f"{mid:26} {result:9} {first or desc}")
    survived = [r for r in rows if r[1] == "survived"]
    print(f"\nRESULT {len(rows)} mutants, {len(survived)} survived: " + ", ".join(r[0] for r in survived))
    return 0


if __name__ == "__main__":
    sys.exit(main())

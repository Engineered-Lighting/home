#!/usr/bin/env python3
"""Mutation runner for tools/den-verify (rung 10).

What it checks: applies each targeted mutant of den-verify's gate, verdict,
settle and refusal logic to a temporary copy of the tool (keeping the repo
layout tools/den-verify + docs/architecture/live-v1/schemas.json, which
records.py reads), runs the tool's own unittest suite with failfast, and prints
a caught/survived table. Synthetic data only (the suite's own fixtures); no
network, nothing written outside a temp dir.

Run:
    python3 mutate_den_verify.py <root of the rung-10-verify checkout>
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUTANTS = [
    # (id, file, old, new, what)
    ("M01-within-strict", "score.py",
     "return abs(m - p) <= tol * abs(p) + Fraction(extra)",
     "return abs(m - p) < tol * abs(p) + Fraction(extra)",
     "tolerance compare <= -> < (boundary 15% becomes a miss)"),
    ("M02-gate-strict", "score.py",
     "\"pass\" if Fraction(hits, n) >= gate else \"fail\"",
     "\"pass\" if Fraction(hits, n) > gate else \"fail\"",
     "hit-rate gate >= -> > (exactly 85% fails)"),
    ("M03-relative-to-measurement", "score.py",
     "return abs(m - p) <= tol * abs(p) + Fraction(extra)",
     "return abs(m - p) <= tol * abs(m) + Fraction(extra)",
     "tolerance relative to the measurement instead of the prediction"),
    ("M04-residual-denominator", "score.py",
     "row[\"residual\"] = (measured - predicted) / predicted",
     "row[\"residual\"] = (measured - predicted) / measured",
     "residual denominator prediction -> measurement"),
    ("M05-empty-passes", "score.py",
     "\"verdict\": (\"insufficient\" if not n else",
     "\"verdict\": (\"pass\" if not n else",
     "no counted point (all excluded / no decisions) -> verdict pass"),
    ("M06-skip-open-slot-refusal", "verify.py",
     "if values is None and not args.preview:",
     "if False:",
     "scored run no longer refuses an open slot"),
    ("M07-held-hits-also-excluded", "score.py",
     "row[\"excluded_held\"] = row[\"status\"] == \"outside\" and row[\"held_share\"] >= m[\"held_share_min\"]",
     "row[\"excluded_held\"] = row[\"status\"] in (\"within\", \"outside\") and row[\"held_share\"] >= m[\"held_share_min\"]",
     "held points excluded whether within or outside (flips the held-hit rule)"),
    ("M08-held-share-boundary", "score.py",
     "row[\"excluded_held\"] = row[\"status\"] == \"outside\" and row[\"held_share\"] >= m[\"held_share_min\"]",
     "row[\"excluded_held\"] = row[\"status\"] == \"outside\" and row[\"held_share\"] > m[\"held_share_min\"]",
     "held-share exclusion >= -> >"),
    ("M09-ignore-settle-time", "score.py",
     "            if f.ts < d.settled:\n                continue",
     "            if f.ts < d.ts:\n                continue",
     "verify frame searched from the decision's ts, not its settle time"),
    ("M10-drift-ignores-occupancy", "score.py",
     "if (inc[\"status\"] == \"outside\" and not occ",
     "if (inc[\"status\"] == \"outside\"",
     "drift explanation no longer requires an unoccupied frame"),
    ("M11-interval-one-sided", "score.py",
     "a = (1 - m[\"interval_level\"]) / 2",
     "a = (1 - m[\"interval_level\"])",
     "bootstrap interval at 5%/95% instead of 2.5%/97.5%"),
    ("M12-interval-no-resample", "score.py",
     "pick = [sessions[rng.randrange(len(sessions))] for _ in sessions]",
     "pick = list(sessions)",
     "bootstrap never resamples (interval collapses to the point estimate)"),
    ("M13-not-admitted-ignored", "score.py",
     "admitted = not any(s.mid_transition for s in d.steps)",
     "admitted = True",
     "mid-transition decisions admitted into the hit rate"),
    ("M14-no-settle-margin", "settle.py",
     "end = sent + fade + float(m[\"settle_margin_s\"])",
     "end = sent + fade",
     "settle margin dropped"),
]


def run_suite(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tools/den-verify",
                        "-p", "test_*.py", "-f"], cwd=root, env=env, capture_output=True, text=True,
                       timeout=600)
    tail = (p.stderr or "").strip().splitlines()
    first_fail = next((l for l in tail if l.startswith(("FAIL:", "ERROR:"))), "")
    return p.returncode, first_fail, tail[-1] if tail else ""


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        (base / "tools").mkdir(parents=True)
        shutil.copytree(src / "tools" / "den-verify", base / "tools" / "den-verify")
        shutil.copytree(src / "docs" / "architecture" / "live-v1", base / "docs" / "architecture" / "live-v1")
        code, ff, last = run_suite(base)
        print(f"RESULT baseline exit={code} {last}")
        if code != 0:
            print("FAIL baseline suite does not pass; mutants are meaningless")
            return 1
        for mid, fname, old, new, what in MUTANTS:
            work = Path(tmp) / mid
            shutil.copytree(base, work)
            f = work / "tools" / "den-verify" / fname
            text = f.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((mid, fname, "not-applied", f"pattern found {text.count(old)} times", what))
                continue
            f.write_text(text.replace(old, new), encoding="utf-8")
            code, ff, last = run_suite(work)
            rows.append((mid, fname, "caught" if code != 0 else "survived", ff or last, what))
            shutil.rmtree(work)
    print()
    print(f"{'mutant':32} {'file':10} {'result':9} caught_by / what")
    for mid, fname, res, by, what in rows:
        print(f"{mid:32} {fname:10} {res:9} {by}  [{what}]")
    survived = [r for r in rows if r[2] == "survived"]
    print()
    print(f"RESULT {len(rows)} mutants: {sum(r[2] == 'caught' for r in rows)} caught, {len(survived)} survived, "
          f"{sum(r[2] == 'not-applied' for r in rows)} not applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

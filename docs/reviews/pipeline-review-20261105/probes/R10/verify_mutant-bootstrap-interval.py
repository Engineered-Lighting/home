#!/usr/bin/env python3
"""verify_mutant-bootstrap-interval: verifier's re-run of finding mutant-bootstrap-interval (rung 10).

What it checks (synthetic only, no network, nothing written outside a temp dir):
  1. copies tools/den-verify (without __pycache__, so no stale bytecode can mask
     a mutant) and docs/architecture/live-v1 of the given checkout into a temp
     dir and runs the WHOLE den-verify unittest suite (no failfast) as a baseline;
  2. applies each mutant alone to a fresh copy and runs the whole suite again:
       M11  a = (1 - interval_level) / 2  ->  a = (1 - interval_level)
            (a two-sided 90% interval, still labelled "two-sided 95%")
       M12  the bootstrap draws `sessions` itself every round (never resamples)
  3. in each copy, calls Run.hit_rate on
       (a) the suite's own case {17/20, 17/20}: two identical sessions, and
       (b) three unequal sessions {9/10, 7/10, 10/12}, whose exact bootstrap
           distribution is enumerated over all 27 equally likely resamples,
     so a surviving mutant is shown to be non-equivalent (it changes the
     reported interval) while the suite's only interval case cannot tell.
Prints RESULT lines and a caught/survived table; a FAIL line marks a mutant that
survives the suite while changing the interval (the reported defect).

Run:
    python3 verify_mutant-bootstrap-interval.py <root of the rung-10-verify checkout> [work dir parent]
"""
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUTANTS = [
    ("M11-interval-90pct", "score.py",
     "a = (1 - m[\"interval_level\"]) / 2",
     "a = (1 - m[\"interval_level\"])"),
    ("M12-interval-no-resample", "score.py",
     "pick = [sessions[rng.randrange(len(sessions))] for _ in sessions]",
     "pick = list(sessions)"),
]

CASES = {"suite_identical": {"1": [17, 20], "2": [17, 20]},
         "unequal_three": {"1": [9, 10], "2": [7, 10], "3": [10, 12]}}

PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import method
from score import Run
from pathlib import Path
text = method.lf_text(Path(sys.argv[1]) / "VERIFY-V1.md")
m = method.parse(method.closed_copy(text)).scored()
cases = json.loads(sys.argv[2])
out = {}
for name, sess in cases.items():
    hr = Run.hit_rate(type("R", (), {"m": m})(), {k: tuple(v) for k, v in sess.items()})
    out[name] = {"interval": hr["interval"], "value": hr["value"], "status": hr["interval_status"]}
print(json.dumps(out))
'''


def suite(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-verify",
                        "-p", "test_*.py"], cwd=root, env=env, capture_output=True, text=True, timeout=600)
    err = p.stderr or ""
    ran = re.search(r"Ran (\d+) test", err)
    failing = re.findall(r"^(?:FAIL|ERROR): (\S+)", err, re.M)
    return p.returncode, int(ran[1]) if ran else None, failing


def intervals(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-B", "-c", PROBE, str(root / "tools" / "den-verify"), json.dumps(CASES)],
                       cwd=root, env=env, capture_output=True, text=True, timeout=600)
    if p.returncode:
        raise SystemExit(f"FAIL interval probe crashed: {p.stderr}")
    return json.loads(p.stdout)


def exact_quantiles(sessions, lo_q=0.025, hi_q=0.975):
    vals = list(sessions.values())
    dist = sorted(sum(h for h, _ in pick) / sum(n for _, n in pick)
                  for pick in itertools.product(vals, repeat=len(vals)))
    cdf = lambda x: sum(v <= x + 1e-12 for v in dist) / len(dist)
    q = lambda p: min(v for v in dist if cdf(v) >= p)
    return round(q(lo_q), 4), round(q(hi_q), 4), dist


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    parent = sys.argv[2] if len(sys.argv) > 2 else None
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        base = Path(tmp) / "base"
        (base / "tools").mkdir(parents=True)
        shutil.copytree(src / "tools" / "den-verify", base / "tools" / "den-verify",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(src / "docs" / "architecture" / "live-v1", base / "docs" / "architecture" / "live-v1")
        code, ran, failing = suite(base)
        print(f"RESULT baseline suite exit={code} ran={ran} failing={failing}")
        if code:
            print("FAIL baseline suite does not pass; mutants are meaningless")
            return 1
        b = intervals(base)
        for name, r in b.items():
            print(f"RESULT baseline {name}: interval={r['interval']} value={r['value']} status={r['status']!r}")
        s3 = {k: tuple(v) for k, v in CASES["unequal_three"].items()}
        lo, hi, dist = exact_quantiles(s3)
        lo90, hi90, _ = exact_quantiles(s3, 0.05, 0.95)
        print(f"RESULT exact 27-resample distribution for unequal_three: {[round(x, 4) for x in dist]}")
        print(f"RESULT exact atoms: 2.5%={lo} 97.5%={hi} (95% two-sided); 5%={lo90} 95%={hi90} (90%)")
        rows = []
        for mid, fname, old, new in MUTANTS:
            work = Path(tmp) / mid
            shutil.copytree(base, work)
            f = work / "tools" / "den-verify" / fname
            text = f.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((mid, "not-applied", f"pattern count {text.count(old)}", None))
                continue
            f.write_text(text.replace(old, new), encoding="utf-8")
            code, ran, failing = suite(work)
            mi = intervals(work)
            res = "caught" if code else "survived"
            rows.append((mid, res, f"exit={code} ran={ran} failing={failing}", mi))
            for name, r in mi.items():
                same = r["interval"] == b[name]["interval"]
                print(f"RESULT {mid} {name}: interval={r['interval']} (baseline {b[name]['interval']}) "
                      f"{'SAME' if same else 'DIFFERENT'}; status={r['status']!r}")
        print()
        print(f"{'mutant':28} {'suite':10} detail")
        bad = 0
        for mid, res, detail, mi in rows:
            print(f"{mid:28} {res:10} {detail}")
            if res == "survived" and mi:
                changed = mi["unequal_three"]["interval"] != b["unequal_three"]["interval"]
                blind = mi["suite_identical"]["interval"] == b["suite_identical"]["interval"]
                if changed and blind:
                    bad += 1
                    print(f"FAIL {mid} survives the whole suite yet changes the 3-session interval "
                          f"{b['unequal_three']['interval']} -> {mi['unequal_three']['interval']}; "
                          f"the suite's identical-session case gives {mi['suite_identical']['interval']} either way")
        print(f"RESULT {bad} non-equivalent surviving interval mutant(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

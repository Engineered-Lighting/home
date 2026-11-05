#!/usr/bin/env python3
"""Verifier probe for finding mutant-bootstrap-interval (den-verify, rung 10).

What it checks: (1) the suite's only interval assertion uses two identical
sessions (17/20, 17/20), for which the real code, the 90% mutant (M11) and the
no-resample mutant (M12) all print [0.85, 0.85]; (2) on unequal sessions the
three disagree, so neither mutant is equivalent; (3) the real code's interval
sits on the exact 2.5%/97.5% atoms of the 27-resample bootstrap distribution
(the current code is right; the finding is a test gap only); (4) whether the
verdict ever reads the interval (it decides the gate only if it does).
Synthetic numbers only; no network; writes only to a temp dir.

Run:
    python3 verify_intent_mutant-bootstrap-interval.py <root of the rung-10-verify checkout>
"""
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VARIANTS = {
    "real": None,
    "M11 a=1-level": ("a = (1 - m[\"interval_level\"]) / 2", "a = (1 - m[\"interval_level\"])"),
    "M12 no resample": ("pick = [sessions[rng.randrange(len(sessions))] for _ in sessions]",
                        "pick = list(sessions)"),
}

CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import method
from score import Run
from pathlib import Path
m = method.parse(method.closed_copy(method.lf_text(Path(sys.argv[1]) / "VERIFY-V1.md"))).scored()
R = type("R", (), {"m": m})()
out = {}
for name, s in (("identical", {1: (17, 20), 2: (17, 20)}),
                ("unequal", {1: (9, 10), 2: (7, 10), 3: (10, 12)}),
                ("unequal-pass-low", {1: (20, 20), 2: (20, 20), 3: (11, 20)})):
    hr = Run.hit_rate(R, s)
    out[name] = {"interval": hr["interval"], "verdict": hr["verdict"], "value": hr["value"],
                 "status": hr.get("interval_status")}
print(json.dumps(out))
"""


def main():
    root = Path(sys.argv[1]).resolve()
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, mut in VARIANTS.items():
            work = Path(tmp) / name.split()[0]
            (work / "tools").mkdir(parents=True)
            shutil.copytree(root / "tools" / "den-verify", work / "tools" / "den-verify",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(root / "docs" / "architecture" / "live-v1", work / "docs" / "architecture" / "live-v1")
            f = work / "tools" / "den-verify" / "score.py"
            if mut:
                text = f.read_text(encoding="utf-8")
                assert text.count(mut[0]) == 1, name
                f.write_text(text.replace(mut[0], mut[1]), encoding="utf-8")
            p = subprocess.run([sys.executable, "-B", "-c", CHILD, str(work / "tools" / "den-verify")],
                               capture_output=True, text=True, timeout=600)
            if p.returncode:
                print(f"FAIL {name}: {p.stderr[-400:]}")
                return 1
            results[name] = json.loads(p.stdout)
    for case in ("identical", "unequal", "unequal-pass-low"):
        for name in VARIANTS:
            r = results[name][case]
            print(f"RESULT {case:17} {name:16} interval={r['interval']} value={r['value']} verdict={r['verdict']}")
    same = len({json.dumps(results[n]["identical"]["interval"]) for n in VARIANTS}) == 1
    print(f"{'PASS' if same else 'FAIL'} identical sessions cannot tell the three apart (the suite's only case)")
    differ = len({json.dumps(results[n]["unequal"]["interval"]) for n in VARIANTS}) == 3
    print(f"{'PASS' if differ else 'FAIL'} unequal sessions tell all three apart (mutants are not equivalent)")
    # exact bootstrap distribution for the unequal case, 27 equally likely resamples
    vals = [(9, 10), (7, 10), (10, 12)]
    dist = sorted(sum(h for h, _ in pk) / sum(n for _, n in pk) for pk in itertools.product(vals, repeat=3))
    cdf = lambda x: sum(v <= x + 1e-12 for v in dist) / 27
    q = lambda pr: min(v for v in dist if cdf(v) >= pr)
    lo, hi = q(0.025), q(0.975)
    lo_b = max([v for v in dist if v < lo], default=lo)
    hi_b = min([v for v in dist if v > hi], default=hi)
    iv = results["real"]["unequal"]["interval"]
    ok = (lo_b - 1e-4 <= iv[0] <= lo + 1e-4) and (hi - 1e-4 <= iv[1] <= hi_b + 1e-4)
    print(f"{'PASS' if ok else 'FAIL'} real interval {iv} brackets the exact 2.5%/97.5% atoms "
          f"[{lo_b:.4f}..{lo:.4f}] / [{hi:.4f}..{hi_b:.4f}]")
    v = {n: results[n]["unequal-pass-low"]["verdict"] for n in VARIANTS}
    print(f"RESULT verdict with sessions 20/20, 20/20, 11/20 (pooled 0.85): {v}; "
          f"interval real {results['real']['unequal-pass-low']['interval']} -> the verdict never reads the interval")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tiebreak probe for R8s1 finding mutant-gate-median-boundary-survives (tools/den-light/model.py:529-530).

    python3 -B probe_mutant-gate-median-boundary-survives.py <branch-checkout-root>

What it checks, for three variants of tools/den-light copied to a temp dir (the checkout is never touched):
  original      model.py as committed: median * 100 <= 8 and p90 * 100 <= 19 ("at most", METHOD-V1)
  median-strict median * 100 <  8   (the reported mutant)
  p90-strict    p90 * 100    <  19  (the same boundary mutant on the p90 half)

Part 1, exact ties through model.score()'s own arithmetic (internal_predict replaced by fixed predictions,
one block, D = 0, synthetic integer readings so the relative error is exactly 0.08 or 0.19 in float):
  M-tie    11 hold-outs, E = 25, prediction 27 -> every error 2/25 == 0.08: median 0.08, p90 0.08
  P-tie    9 hold-outs exact (error 0) + 2 with E = 100, prediction 119 -> median 0, p90 0.19 (rank 9.0)
Part 2, one ulp either side (model.percentile replaced so median/p90 take chosen floats).
Part 3, realism: the real synthetic pipeline (test_stage1.readings_for + model.fit + model.score on
synthetic.SyntheticDen) for a few configurations; prints median/p90 and whether the three variants agree.

Synthetic data only, no sockets, no hosts. Prints one RESULT line per (variant, case) and a summary.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VARIANTS = {
    "original": [],
    "median-strict": [('median * 100 <= method1["gate_median_percent"]',
                       'median * 100 < method1["gate_median_percent"]')],
    "p90-strict": [('p90 * 100 <= method1["gate_p90_percent"]', 'p90 * 100 < method1["gate_p90_percent"]')],
}

CHILD = r'''
import json, math, sys
sys.path.insert(0, sys.argv[1])
import guard, model
m = model.load_stage1().proposed()
TEMPS = {"warm": 2202, "middle": 3200, "cool": 6500}
out = {}

def case(pairs):
    """pairs: list of (E, prediction); one block, D = 0 brackets, no flags."""
    steps = [{"index": 0, "kind": "all_off", "t": 0.0, "reading": [0.0], "spread": [1e-4]}]
    preds = {}
    for i, (E, p) in enumerate(pairs, start=1):
        scene = {e: {"on": True, "brightness": 100, "kelvin": 3200} for e in guard.LIGHTS}
        steps.append({"index": i, "kind": "holdout", "t": float(i), "scene": scene, "label": f"h{i}",
                      "reading": [float(E)], "spread": [1e-4]})
        preds[i] = [float(p)]
    steps.append({"index": len(pairs) + 1, "kind": "all_off", "t": 1000.0, "reading": [0.0], "spread": [1e-4]})
    readings = {"blocks": 1, "steps": steps}
    fitted = {"blocks": 1, "excluded": {}, "noise": [1e-4], "flagged": [], "temperatures": TEMPS,
              "basis": {e: {str(k): [1.0] for k in TEMPS.values()} for e in guard.LIGHTS},
              "shared_curve": {}, "full_level": 254}
    order = iter(sorted(preds))
    saved = model.internal_predict
    model.internal_predict = lambda scene, fitted, _o=order: preds[next(_o)]
    try:
        return model.score(readings, fitted, m)["error"]
    finally:
        model.internal_predict = saved

out["M-tie"] = case([(25, 27)] * 11)
out["P-tie"] = case([(25, 25)] * 9 + [(100, 119)] * 2)

# Part 2: one ulp either side, through score()'s own comparison
real_percentile = model.percentile
def with_values(med, p90):
    model.percentile = lambda values, q: med if q == 50 else p90
    try:
        return case([(25, 26)] * 3)
    finally:
        model.percentile = real_percentile
up, dn = (lambda x: math.nextafter(x, 1.0)), (lambda x: math.nextafter(x, 0.0))
out["M-ulp-below"] = with_values(dn(0.08), 0.10)
out["M-ulp-above"] = with_values(up(0.08), 0.10)
out["P-ulp-below"] = with_values(0.05, dn(0.19))
out["P-ulp-above"] = with_values(0.05, up(0.19))

# Part 3: realistic synthetic pipeline
if len(sys.argv) > 2 and sys.argv[2] == "realistic":
    import test_stage1, synthetic
    cfgs = {"clean noise 2e-5": dict(den=dict(block_px=40, noise=2e-5)),
            "saturating 0.6": dict(den=dict(block_px=40, noise=1e-4, saturate=0.6)),
            "wrong response": dict(den=dict(block_px=40, noise=1e-4),
                                   kw=dict(response=lambda v: math.copysign(abs(v) ** 0.6, v)))}
    for name, cfg in cfgs.items():
        den = synthetic.SyntheticDen(**cfg["den"])
        mm = test_stage1.method1()
        r = test_stage1.readings_for(den, mm, **cfg.get("kw", {}))
        fitted = model.fit(r, r["temperatures"], mm)
        out["R " + name] = model.score(r, fitted, mm)["error"]
print(json.dumps({k: {x: v[x] for x in ("median", "p90", "count", "verdict")} for k, v in out.items()}))
'''


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = Path(argv[1]).resolve() / "tools" / "den-light"
    realistic = "--no-realistic" not in argv
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        child = Path(tmp) / "child.py"
        child.write_text(CHILD, encoding="utf-8")
        for name, changes in VARIANTS.items():
            dest = Path(tmp) / name
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "frames"))
            text = (dest / "model.py").read_text(encoding="utf-8")
            for old, new in changes:
                assert text.count(old) == 1, (name, old)
                text = text.replace(old, new)
            (dest / "model.py").write_text(text, encoding="utf-8")
            args = [sys.executable, "-B", "-I", str(child), str(dest)] + (["realistic"] if realistic else [])
            proc = subprocess.run(args, capture_output=True, text=True, timeout=900, cwd=str(dest))
            if proc.returncode:
                print(f"RESULT {name}: child failed: {proc.stderr.strip().splitlines()[-3:]}")
                return 1
            results[name] = json.loads(proc.stdout)
            for k, v in results[name].items():
                print(f"RESULT {name:14s} {k:22s} median={v['median']!r:24} p90={v['p90']!r:24} "
                      f"count={v['count']} verdict={v['verdict']}")
    print()
    cases = list(results["original"])
    for k in cases:
        verdicts = {n: results[n][k]["verdict"] for n in VARIANTS}
        same = len(set(verdicts.values())) == 1
        print(f"SUMMARY {k:22s} {'agree ' if same else 'DIFFER'} {verdicts}")
    ok = (results["original"]["M-tie"]["verdict"] == "pass" and results["median-strict"]["M-tie"]["verdict"] == "fail"
          and results["original"]["P-tie"]["verdict"] == "pass" and results["p90-strict"]["P-tie"]["verdict"] == "fail")
    print("PASS: committed code is inclusive at exactly 8% / 19% and each strict mutant flips only the exact tie"
          if ok else "FAIL: tie behaviour not as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

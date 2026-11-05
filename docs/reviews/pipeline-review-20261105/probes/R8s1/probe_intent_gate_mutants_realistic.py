#!/usr/bin/env python3
"""Intent-lens check for the R8s1 surviving gate mutants: does each mutant change a verdict on
hand-made, plausible hold-out errors (so it is not an equivalent mutant), and how does the
'<' boundary mutant behave away from an exact tie?

    python3 -B probe_intent_gate_mutants_realistic.py <branch-checkout-root>

For the original model.py and each mutant (applied to a temp copy of tools/den-light; the checkout is
never touched) a child process imports model, replaces internal_predict with fixed predictions, and
calls model.score() on hand-made readings/fitted dicts (synthetic numbers only, no network):
  A median-only:  10 hold-outs, every error 10%        (median 10% > 8%, p90 10% <= 19%)
  B stray-light:  D = E = 1.0, every error 15% of E    (|E^-E|/E = 15%, |E^-E|/reading = 7.5%)
  C flagged:      10 clean pairs at 5% + 3 pairs between disagreeing brackets at 50%
  T tie:          every error 8% as E*(1-0.08): float rounding lands just below 0.08
  X exact-tie:    D = 0, E = 25, prediction 27: |27-25|/25 is exactly 0.08 in float
  N near tie:     errors 7.9999%..8.0001% interleaved (median just above / below 8%)
Prints one RESULT line per (variant, case) with median, p90, count and verdict.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUTANTS = {
    "original": None,
    "gate-drop-median": ('passes = (median is not None and median * 100 <= method1["gate_median_percent"]\n'
                         '              and p90', 'passes = (median is not None\n              and p90'),
    "score-denominator-reading": ('step_errors.append(abs(predicted[b] - e) / e)',
                                  'step_errors.append(abs(predicted[b] - e) / v)'),
    "score-ignores-bracket-flags": ('if b in fitted["excluded"] or (step["index"], b) in flagged:',
                                    'if b in fitted["excluded"]:'),
    "gate-median-boundary": ('median * 100 <= method1["gate_median_percent"]',
                             'median * 100 < method1["gate_median_percent"]'),
}

CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import guard, model
m = model.load_stage1().proposed()
TEMPS = {"warm": 2202, "middle": 3200, "cool": 6500}

def case(errors, D=0.01, E=1.0, flagged_errors=(), pred=None):
    """One block; hold-out i reads E + D and is predicted E*(1-err_i). flagged pairs get their own errors."""
    steps = [{"index": 0, "kind": "all_off", "t": 0.0, "reading": [D], "spread": [1e-4]}]
    preds, flagged = {}, []
    all_errs = list(errors) + list(flagged_errors)
    for i, err in enumerate(all_errs, start=1):
        scene = {e: {"on": True, "brightness": 100, "kelvin": 3200} for e in guard.LIGHTS}
        steps.append({"index": i, "kind": "holdout", "t": float(i), "scene": scene, "label": f"h{i}",
                      "reading": [E + D], "spread": [1e-4]})
        preds[i] = [pred if pred is not None else E * (1 - err)]
        if i > len(errors):
            flagged.append([i, 0])
    steps.append({"index": len(all_errs) + 1, "kind": "all_off", "t": 1000.0, "reading": [D], "spread": [1e-4]})
    readings = {"blocks": 1, "steps": steps}
    fitted = {"blocks": 1, "excluded": {}, "noise": [1e-4], "flagged": flagged, "temperatures": TEMPS,
              "basis": {e: {str(k): [1.0] for k in TEMPS.values()} for e in guard.LIGHTS},
              "shared_curve": {}, "full_level": 254}
    order = iter(sorted(preds))
    model.internal_predict = lambda scene, fitted, _o=order: preds[next(_o)]
    return model.score(readings, fitted, m)["error"]

near = [0.079999, 0.080001] * 5 + [0.080001]
cases = {"A median-only": case([0.10] * 10),
         "B stray-light": case([0.15] * 10, D=1.0, E=1.0),
         "C flagged": case([0.05] * 10, flagged_errors=[0.5] * 3),
         "T tie": case([0.08] * 11),
         "X exact-tie": case([0] * 11, D=0.0, E=25.0, pred=27.0),
         "N near-tie": case(near)}
print(json.dumps({k: {x: v[x] for x in ("median", "p90", "count", "verdict")} for k, v in cases.items()}))
'''


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = Path(argv[1]).resolve() / "tools" / "den-light"
    with tempfile.TemporaryDirectory() as tmp:
        child = Path(tmp) / "child.py"
        child.write_text(CHILD, encoding="utf-8")
        for name, change in MUTANTS.items():
            dest = Path(tmp) / name
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "frames"))
            if change:
                text = (dest / "model.py").read_text(encoding="utf-8")
                assert text.count(change[0]) == 1, name
                (dest / "model.py").write_text(text.replace(*change), encoding="utf-8")
            out = subprocess.run([sys.executable, "-B", "-I", str(child), str(dest)], capture_output=True,
                                 text=True, timeout=120)
            if out.returncode:
                print(f"RESULT {name}: child failed: {out.stderr.strip().splitlines()[-1:]}")
                continue
            for k, v in json.loads(out.stdout).items():
                print(f"RESULT {name:28s} {k:14s} median={v['median']:.6f} p90={v['p90']:.6f} "
                      f"count={v['count']} verdict={v['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

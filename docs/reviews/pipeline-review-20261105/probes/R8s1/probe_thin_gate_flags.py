#!/usr/bin/env python3
"""probe_thin_gate_flags: one disturbed all-off capture among the hold-outs silently removes most hold-out
pairs, and the gate is then scored on what is left, with no minimum and no count of the removed pairs.

What it checks: noise_and_flags() flags every (step, block) between two all-off captures that disagree;
score() skips flagged pairs with `continue` (they are not in excluded_count), score.json and
light-model.json carry no count of them, and the verdict needs only one pair. Here the den model is 30%
wrong on hold-outs 1 to 12 (the gate should fail); a stray light during the all-off capture between
hold-out groups (car headlights, a hallway light) flags exactly those, and the verdict becomes pass on
hold-outs 13 to 16.

How to run (synthetic readings in memory, no I/O):
    python3 -B probe_thin_gate_flags.py <root of the claude/rung-8-stage1 checkout>
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import model  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


def main():
    m = T.method1()
    den = synthetic.SyntheticDen(block_px=40, noise=2e-5, drift=0.0)
    base = T.readings_for(den, m)
    steps = base["steps"]
    holdouts = [i for i, s in enumerate(steps) if s["kind"] == "holdout"]
    for i in holdouts[:12]:  # the model is 30% off on these scenes: the truth is brighter than superposition
        steps[i]["reading"] = [v * 1.3 for v in steps[i]["reading"]]
    wrong = copy.deepcopy(base)
    fitted = model.fit(wrong, wrong["temperatures"], m)
    honest = model.score(wrong, fitted, m)
    offs = [i for i, s in enumerate(steps) if s["kind"] == "all_off" and holdouts[0] < i < holdouts[-1]][:1]
    disturbed = copy.deepcopy(base)
    for i in offs:  # the all-off capture between hold-out groups 1 and 2 catches a stray light
        disturbed["steps"][i]["reading"] = [v * 3 for v in disturbed["steps"][i]["reading"]]
    fitted2 = model.fit(disturbed, disturbed["temperatures"], m)
    thin = model.score(disturbed, fitted2, m)
    used = [s["label"] for s in thin["information_only"]["steps"] if s["pairs"]]
    print("RESULT without the disturbance:", {k: honest["error"][k] for k in ("median", "p90", "count", "verdict")})
    print("RESULT with stray light in", len(offs), "all-off capture(s):",
          {k: thin["error"][k] for k in ("median", "p90", "count", "excluded_count", "verdict")})
    print("RESULT hold-outs that still have pairs:", used)
    print("RESULT flagged pairs in the fit:", len(fitted2["flagged"]),
          "| a flagged-pair count in score.json:", any("flag" in k for k in json.dumps(thin).split('"')))
    if honest["error"]["verdict"] == "fail" and thin["error"]["verdict"] == "pass":
        print("FAIL: a disturbance in an all-off capture turns a failing model into a pass scored on",
              len(used), "of", len(holdouts), "hold-outs; no minimum coverage and no count of the dropped pairs")
    else:
        print("PASS")


if __name__ == "__main__":
    main()

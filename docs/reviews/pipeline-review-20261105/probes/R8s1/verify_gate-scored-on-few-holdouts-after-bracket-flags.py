#!/usr/bin/env python3
"""verify_gate-scored-on-few-holdouts-after-bracket-flags: one all-off capture that reads brighter than its
neighbours (a steady stray light during that capture only) flags every block of the hold-outs on both sides
of it; score() skips those pairs without counting them, and the verdict needs no minimum coverage.

What it checks (synthetic readings in memory, no I/O, no network):
 1. The schedule positions of the all-off captures among the hold-outs (METHOD's proposed bracket_every).
 2. With the den model exact (no injected error), one disturbed all-off: score count, excluded_count,
    the hold-outs left with pairs, and what score.json carries about the dropped pairs
    (information_only.steps[].pairs is the only trace).
 3. Two disturbed all-offs: how few hold-outs remain, and whether a verdict is still issued.

How to run:
    python3 -B verify_gate-scored-on-few-holdouts-after-bracket-flags.py <root of the claude/rung-8-stage1 checkout>
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


def scored(readings, m):
    fitted = model.fit(readings, readings["temperatures"], m)
    s = model.score(readings, fitted, m)
    return fitted, s


def main():
    m = T.method1()
    den = synthetic.SyntheticDen(block_px=40, noise=2e-5, drift=0.0)
    base = T.readings_for(den, m)
    steps = base["steps"]
    hold = [i for i, s in enumerate(steps) if s["kind"] == "holdout"]
    offs = [i for i, s in enumerate(steps) if s["kind"] == "all_off" and i > hold[0] - 2]
    print("RESULT all-off step indexes around the hold-outs:", [steps[i]["index"] for i in offs],
          "| hold-out indexes:", [steps[i]["index"] for i in hold])
    _, clean = scored(copy.deepcopy(base), m)
    print("RESULT clean:", {k: clean["error"][k] for k in ("count", "excluded_count", "verdict")})
    for n_dist in (1, 2):
        r = copy.deepcopy(base)
        between = [i for i in offs if hold[0] < i < hold[-1]][:n_dist]
        for i in between:
            r["steps"][i]["reading"] = [v * 3 for v in r["steps"][i]["reading"]]
        fitted, s = scored(r, m)
        left = [x["label"] for x in s["information_only"]["steps"] if x["pairs"]]
        zero = [x["label"] for x in s["information_only"]["steps"] if not x["pairs"]]
        keys = set(json.dumps(s).replace("{", " ").replace(",", " ").split())
        print(f"RESULT {n_dist} disturbed all-off(s): count {s['error']['count']} of {clean['error']['count']}, "
              f"excluded_count {s['error']['excluded_count']}, verdict {s['error']['verdict']}, "
              f"flagged in fit {len(fitted['flagged'])}")
        print(f"RESULT   hold-outs with pairs ({len(left)}): {left}")
        print(f"RESULT   hold-outs with pairs 0 in information_only.steps ({len(zero)})")
        print("RESULT   a 'flag' key anywhere in score.json:", any("flag" in k for k in keys))
    print("FAIL: the verdict is issued on a fraction of the hold-outs; the dropped pairs are not in "
          "excluded_count and no minimum applies" if s["error"]["verdict"] in ("pass", "fail") else "PASS")


if __name__ == "__main__":
    main()

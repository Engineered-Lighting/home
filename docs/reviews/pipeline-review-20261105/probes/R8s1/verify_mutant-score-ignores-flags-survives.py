#!/usr/bin/env python3
"""Verifier for mutant-score-ignores-flags-survives (R8s1, reproduce lens).

    python3 -B verify_mutant-score-ignores-flags-survives.py <branch-checkout-root> [test|holdout] [factor]

Mode 'holdout' disturbs the first all-off capture whose neighbouring brackets enclose only hold-out steps;
factor (default 5, as the test) multiplies block 2 of that all-off capture.

Re-creates test_stage1.ReductionTests.test_disagreeing_brackets_flag_the_steps_between_them's scenario
(TinyDen, all-off capture 3 at 5x in block 2) and scores it twice: with the committed score(), and with
score() seeing no flagged pairs (what the mutant 'skip only excluded blocks' does). If the pair count or the
median/p90 differ, the mutant is not equivalent in that very scenario, so the existing test could catch it
by asserting score's count, and it does not. Synthetic data only, no host contacted.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import test_stage1 as T  # noqa: E402
import model  # noqa: E402

FACTOR = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0


def pick_bracket(case):
    """'test' -> the test's offs[3]; 'holdout' -> the first all-off whose neighbours enclose a hold-out."""
    if len(sys.argv) < 3 or sys.argv[2] != "holdout":
        return 3
    captures, _ = case.den.captures(case.m, None)
    kinds = [c["step"]["kind"] for c in captures]
    offs = [i for i, k in enumerate(kinds) if k == "all_off"]
    for k in range(1, len(offs) - 1):  # only hold-outs between its neighbours, so no basis step is flagged
        between = set(kinds[offs[k - 1] + 1:offs[k + 1]]) - {"all_off"}
        if between == {"holdout"}:
            return k
    raise SystemExit("RESULT no all-off capture encloses a hold-out")


def main():
    case = T.ReductionTests("test_disagreeing_brackets_flag_the_steps_between_them")
    case.setUp()
    k = pick_bracket(case)
    print(f"RESULT disturbing all-off capture number {k}")

    def disturb(captures):
        offs = [c for c in captures if c["step"]["kind"] == "all_off"]
        target = offs[k]
        target["frames"] = [([v * (FACTOR if b == 2 else 1) if v is not None else None
                              for v, b in zip(vals, case.den.owner)], f) for vals, f in target["frames"]]
        for c in captures:
            if c["step"]["kind"] == "all_off":
                v, f = c["frames"][1]
                c["frames"][1] = ([x * 1.0001 if x is not None else None for x in v], f)

    r, fitted, scored = case.run_model(edit=disturb)
    holdouts = {s["index"] for s in r["steps"] if s["kind"] == "holdout"}
    flagged_holdout_pairs = [p for p in fitted["flagged"] if tuple(p)[0] in holdouts]
    mutant = model.score(r, {**fitted, "flagged": []}, case.m)
    a, b = scored["error"], mutant["error"]
    print(f"RESULT flagged pairs {len(fitted['flagged'])}, of them on hold-out steps: {flagged_holdout_pairs}")
    print(f"RESULT committed score: count={a['count']} excluded_count={a['excluded_count']} median={a['median']} p90={a['p90']} verdict={a['verdict']}")
    print(f"RESULT flags ignored  : count={b['count']} excluded_count={b['excluded_count']} median={b['median']} p90={b['p90']} verdict={b['verdict']}")
    if (a["count"], a["excluded_count"], a["median"], a["p90"]) != (b["count"], b["excluded_count"], b["median"], b["p90"]):
        print("CONFIRMED the mutant (flags ignored) changes score in this scenario; no test scores a flagged "
              "hold-out pair (the existing bracket test asserts only fitted['flagged'])")
    else:
        print("RESULT no difference in this scenario")
    return 0


if __name__ == "__main__":
    sys.exit(main())

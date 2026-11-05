#!/usr/bin/env python3
"""verify_intent_bracket_abort_missing: end to end on fakes, a probe session whose all-off brackets disagree
(a stray light during the all-off capture between hold-out groups 1 and 2) completes instead of aborting,
and model.run_session then scores the gate on the remaining hold-outs only.

What it checks (verifier for finding gate-scored-on-few-holdouts-after-bracket-flags):
  - PLAN.md (docs/plans/rung-8-light-model, "Aborts happen on") lists "the camera guard: ... all-off
    brackets disagree" as an abort. probe.py has no such guard, so the session's outcome is "complete".
  - model.run_session flags the steps between the disagreeing brackets and scores what is left.
  - score.json: error.count, error.excluded_count, per-hold-out pair counts (information_only.steps),
    and whether any key names a flagged-pair count.

Synthetic data only: the fake HA and fake camera from tools/den-light/synthetic.py bind 127.0.0.1.
Temp dirs only (the test Rig's own TemporaryDirectory).

How to run:
    python3 -B verify_intent_bracket_abort_missing.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import model  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    steps = model.schedule(rig.m1, T.SNAPSHOT, T.RANGES, T.WARMEST)["steps"]
    holdouts = [s["index"] for s in steps if s["kind"] == "holdout"]
    offs = [s["index"] for s in steps if s["kind"] == "all_off" and holdouts[0] < s["index"] < holdouts[-1]]
    target = offs[0]
    print("RESULT hold-out step indexes:", holdouts)
    print("RESULT all-off steps among the hold-outs:", offs, "-> stray light during step", target)
    base_daylight = rig.den.daylight

    def hook(session, step):
        if step["index"] == target:
            rig.den.daylight = base_daylight * 3  # a steady stray light for this one all-off capture
        elif rig.den.daylight != base_daylight:
            rig.den.daylight = base_daylight

    try:
        outcome, lines, s = rig.run(hook=hook)
        print("RESULT session outcome:", outcome)
        record = json.loads((rig.folder / "session.json").read_text())
        print("RESULT session.json outcome / abort_reason:", record["outcome"], "/", record["abort_reason"])
        if outcome != "complete":
            print("PASS: the session aborted on the disagreeing brackets")
            return
        params, digest = synthetic.meter_norm_for(rig.m0)
        lm, scored = model.run_session(rig.folder, {"parameters": params, "parameters_sha256": digest}, rig.m1)
        e = scored["error"]
        print("RESULT score:", {k: e[k] for k in ("median", "p90", "count", "excluded_count", "verdict")})
        per = [(st["label"], st["pairs"]) for st in scored["information_only"]["steps"]]
        print("RESULT per-hold-out pairs (information_only.steps):", per)
        flag_keys = [k for k in json.dumps(scored).split('"') if "flag" in k.lower()]
        print("RESULT keys naming flags in score.json:", flag_keys)
        print("RESULT keys naming flags in light-model.json:",
              [k for k in json.dumps(lm).split('"') if "flag" in k.lower()])
        scored_holdouts = [lab for lab, n in per if n]
        print(f"FAIL: the session completed although its all-off brackets disagree (PLAN says abort); the gate "
              f"was scored on {len(scored_holdouts)} of {len(per)} hold-outs ({e['count']} pairs), with no "
              f"flagged-pair count")
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()

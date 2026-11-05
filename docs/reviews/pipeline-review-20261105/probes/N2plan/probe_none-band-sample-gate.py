"""probe_none-band-sample-gate: N2 plan's sample gate covers fine and casual only; the none band
(quiet / no-need units) gets no episode count, and N1's scripts cannot give 30 of them.

What it checks
  1. Counts, from N1's hard-a / hard-b round tables (claude/plan-n1 b1f1a9d2), the held-out units
     per band for the held-out split of N1's 8-take plan (2 hard-a + 2 hard-b), under both phone
     bands: fine and casual intervals, and none units (quiet / no-need spans with at least one
     second more than tau_off = 60 s after the latest labelled offset), plus timed onsets.
  2. Reads PLAN.md section 4.5 (the sample gate) and 4.4 (the 22 / 30 quiet-unit count).
  3. Reads the canon EvalRecord example (schemas.json, live-records) for `episodes_per_band`.
  Then applies the plan's gate (fine, casual >= 30) and the owner's per-band gate read over all
  three bands (owner-choices item 5: ">= 30 episodes per band from >= 3 sessions over >= 2 days").

Run
  python3 probe_none-band-sample-gate.py <root of claude/plan-n2> [<root of live-records checkout>]
  (the second root defaults to the sibling folder 'live-records')
Synthetic counts only; no network.
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n2_scripts import intervals_and_spans, scored_from  # noqa: E402


def take_counts(script, phone_band):
    ivs, spans = intervals_and_spans(script, phone_band)
    c = {"fine": 0, "casual": 0, "none": 0, "onsets": len(ivs)}
    for iv in ivs:
        c[iv["band"]] += 1
    for sp in spans:
        a = scored_from(sp["start"], ivs)
        if math.ceil(a) + 1 <= sp["end"]:  # at least one whole scored second
            c["none"] += 1
    return c


def main(root, records_root):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    for phrase in ("fine and casual: at least 30 units each from at least 3 sessions over at least 2 days",
                   "every per-second metric and every case: data in at least 3 held-out sessions over at least 2 days",
                   "22 per held-out set of two takes each, or 30 with the phone as `none`",
                   "too few, or no margin at all, for a 30-unit gate"):
        print(("PASS" if phrase in plan else "FAIL") + f": PLAN.md says {phrase!r}")
    schema = json.loads((Path(records_root) / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
    ev = next(r for r in schema["records"] if r["name"] == "EvalRecord")
    bands = ev["example"][0]["per_link"]["visual_demand"]["episodes_per_band"]
    print(f"RESULT canon EvalRecord example episodes_per_band = {bands} (none is a band with episodes)")
    for phone_band in ("casual", "none"):
        a, b = take_counts("hard-a", phone_band), take_counts("hard-b", phone_band)
        held = {k: 2 * a[k] + 2 * b[k] for k in a}
        print(f"RESULT phone band {phone_band}: per take hard-a {a}, hard-b {b}; held-out set {held}")
        plan_gate = held["fine"] >= 30 and held["casual"] >= 30
        owner_gate = all(held[k] >= 30 for k in ("fine", "casual", "none"))
        print(f"RESULT phone band {phone_band}: plan's gate {'met' if plan_gate else 'unmet'}; "
              f"owner's per-band gate over fine/casual/none {'met' if owner_gate else 'unmet'}"
              f" (none = {held['none']}, margin {held['none'] - 30})")
        if plan_gate and not owner_gate:
            print(f"FAIL: phone band {phone_band}: N2 can return feasible with {held['none']} none-band units, "
                  "where the per-band gate read over all three bands says insufficient")
        elif plan_gate and held["none"] - 30 <= 0:
            print(f"FAIL: phone band {phone_band}: the none band meets 30 only with zero margin "
                  "(one uncertain span or carried piece makes it insufficient)")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    rec = sys.argv[2] if len(sys.argv) > 2 else str(Path(root).resolve().parent / "live-records")
    main(root, rec)

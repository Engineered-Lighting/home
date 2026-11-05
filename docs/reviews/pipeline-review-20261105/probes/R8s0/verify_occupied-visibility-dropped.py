#!/usr/bin/env python3
"""verify_occupied-visibility-dropped.py: does the rung 8 plan keep the ladder's occupied-visibility qualification?

Text-level check (the plan is a document; Stage 1's outputs are checked where a Stage 1 checkout is given):
  1. ladder.json rung 8: does the gate/tested text require V_total qualified on occupied hold-outs?
  2. PLAN.md: does the verdict paragraph say "Nothing else enters the verdict", and does the score.json
     reporting list in that paragraph mention occupied visibility / V_total at all?
  3. PLAN.md: where occupied hold-outs are left out, is a rung or stage named for them (as it is for
     day-to-day stability -> rung 10, sample points -> rung 9)?
  4. Optional Stage 1 checkout: do model.py's score.json / light-model.json builders carry any occupied
     visibility line beside 'absolute', 'camera_scale' and 'day_to_day_stability'?

How to run:
  python3 -B verify_occupied-visibility-dropped.py <plan-rung-8 checkout> <live-records checkout> [<rung-8-stage1 checkout>]
Reads files only.
"""
import json
import re
import sys
from pathlib import Path

plan_root = Path(sys.argv[1]).resolve()
spec_root = Path(sys.argv[2]).resolve()
stage1 = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None

ladder = json.loads((spec_root / "docs/architecture/live-v1/ladder.json").read_text(encoding="utf-8"))
r8 = next(r for r in ladder["rungs"] if r["n"] == 8)
need = "qualified on occupied hold-outs" in r8["gate"] and "occupied hold-outs" in r8["tested"]
print(f"RESULT ladder rung 8 gate requires occupied-visibility qualification: {need}")

lines = (plan_root / "docs/plans/rung-8-light-model/PLAN.md").read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("**The gate passes when the pooled median"))
end = next(i for i in range(start, len(lines)) if not lines[i].strip())
para = " ".join(lines[start:end])
print(f"RESULT PLAN.md:{start + 1}-{end}: 'Nothing else enters the verdict' present: "
      f"{'Nothing else enters the verdict' in para}")
mentions = re.findall(r"occupied|visibility|V_total", para, re.I)
print(f"RESULT PLAN.md:{start + 1}-{end}: score.json list mentions occupied visibility: {bool(mentions)}; "
      f"lists absolute: {'absolute: not run' in para}; lists day-to-day stability: {'day-to-day' in para}")
left = next(i for i, l in enumerate(lines) if "Occupied hold-outs and the" in l)
item = " ".join(lines[left:left + 3])
named = re.search(r"rung (9|10|11)|Stage [12]|later rung", item)
print(f"RESULT PLAN.md:{left + 1}-{left + 3}: left-out item names a rung/stage for occupied hold-outs: {bool(named)}")
later = [i + 1 for i, l in enumerate(lines) if "occupied visibility term is a later" in l]
print(f"RESULT PLAN.md:{later}: 'The occupied visibility term is a later step' (no rung named)")

defect = need and not mentions and not named
if stage1:
    model = (stage1 / "tools/den-light/model.py").read_text(encoding="utf-8")
    keys = [k for k in ("\"absolute\"", "\"camera_scale\"", "\"day_to_day_stability\"", "\"glare_note\"") if k in model]
    vis = re.findall(r"occupied|visibility|V_total", model, re.I)
    print(f"RESULT stage1 model.py caveat keys present: {keys}; occupied-visibility mentions: {len(vis)}")
print(("FAIL (defect shown)" if defect else "PASS")
      + ": the ladder's occupied-visibility qualification is " + ("missing from" if defect else "kept in")
      + " the plan's verdict and score.json caveats")

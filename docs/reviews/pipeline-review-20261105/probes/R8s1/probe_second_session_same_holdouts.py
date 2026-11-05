#!/usr/bin/env python3
"""Would a second probe session (the plan's confirmation after tuning) get fresh hold-out scenes?

    python3 -I probe_second_session_same_holdouts.py <branch-checkout-root>

Builds the schedule twice with Stage 1's own model.schedule() and METHOD's numbers, as probe.py
does at each session start: once for session 1 and once for a later session after the owner has
changed a bulb's normal scene (so even the snapshot-derived hold-out differs). The plan runs a
second session when 'anything is tuned on its hold-outs', and ladder.json says that once a
diagnosis informs tuning, confirmation needs a fresh, independent held-out set. FAIL if the later
session's random and fixed hold-out scenes repeat session 1's. Pure computation, no network.
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import model  # noqa: E402
import synthetic  # noqa: E402

m = model.load_stage1().proposed()  # schedule_seed is a single METHOD slot, closed once before any probe
ranges = synthetic.KELVIN_RANGE
warmest = {e: lo for e, (lo, _) in ranges.items()}
snap1 = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
         "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
snap2 = dict(snap1, **{"light.den_front_left": guard.scene(True, 150, 3000)})


def holdouts(snapshot):
    plan = model.schedule(m, snapshot, ranges, warmest)
    return [(s["label"], json.dumps(s["scene"], sort_keys=True)) for s in plan["steps"] if s["kind"] == "holdout"]


h1, h2 = holdouts(snap1), holdouts(snap2)
same = [a for a, b in zip(h1, h2) if a == b]
print(f"RESULT schedule_seed={m['schedule_seed']}; session 1 hold-outs={len(h1)}, later session={len(h2)}")
print(f"RESULT identical hold-out scenes across the two sessions: {len(same)} of {len(h1)} "
      f"({', '.join(label for label, _ in same)})")
src = (ROOT / "tools" / "den-light" / "probe.py").read_text() + (ROOT / "tools" / "den-light" / "model.py").read_text()
print(f"RESULT any per-session seed or used-seed refusal in probe.py/model.py: "
      f"{'session_seed' in src or 'seed already' in src or 'used seed' in src}")
if len(same) >= len(h1) - 1:
    print("FAIL a later session repeats session 1's hold-out scenes; a confirmation after tuning on them is not "
          "a fresh held-out set")
    sys.exit(1)
print("PASS")

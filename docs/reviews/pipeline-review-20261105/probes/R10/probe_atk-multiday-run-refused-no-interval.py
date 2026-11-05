"""Probe: the session-bootstrap interval cannot be produced: multi-day runs abort, per-day runs have one session.

What it checks
  den-verify names a LightState with no `id` key "ls-HHMMSS" in local time
  (records.py light_state_id), with no date, and Run() refuses a file where two
  frames share a name ("two LightStates share the id"). It makes sessions out
  of local calendar days and computes the owner's two-sided 95% interval from
  10,000 session-bootstrap rounds only when one run holds >= 2 days
  (score.py hit_rate). Neither the LightState contract nor den-photometry's
  writer gives LightState an id, and Decision.inputs.light_state refers to a
  frame by that same dateless name.

  1. Two evenings, one decision each, frames at the same clock times on both
     days, in one run: ValueError, nothing scored.
  2. Each evening run on its own (what the README's per-session folder
     C:\\den-verify\\s1 suggests): interval None, "one session".
  Also: a frame at hh:mm:ss.996 and one at hh:mm:ss.000 get the same name.

Run
  python3 probe_multiday-run-refused-no-interval.py <den-verify branch checkout root>
Synthetic data only; no network; writes nothing.
"""
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock, light_state_id  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
MODEL = syn.hue_model({"desk-1": {B0: 400.0}})
LM = LightModel(MODEL)
CLOCK = Clock(syn.UTC_OFFSET_S)


def evening(day, shift=0.0):
    t = syn.T0 + 86400 * day + shift
    pb = LM.predict({B0: {"level": 0.2, "cct_k": 2700}})["desk-1"]
    pa = LM.predict({B0: {"level": 0.8, "cct_k": 2700}})["desk-1"]
    ref = syn.light_state(t, {"desk-1": pb}, anchored=False)
    ref_id = light_state_id(ref, CLOCK)
    dec = syn.hue_decision(f"d-day{day}", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [
        {"kind": "send", "sent_at": t + 0.6}]}}, ref_id, targets={"A": {"surface_id": "desk-1"}})
    after = syn.light_state(t + 5, {"desk-1": pa}, anchored=False)
    return [dec], [ref, after], ref_id


d1, f1, id1 = evening(0)
d2, f2, id2 = evening(1)
print(f"day 1 read frame {id1}, day 2 read frame {id2}")
try:
    Run(M, LM, d1 + d2, f1 + f2, clock=CLOCK).run()
    print("RESULT two-day run: completed")
    refused = False
except ValueError as e:
    refused = True
    print(f"RESULT two-day run: ValueError: {e}")

per_day = []
for d, f in ((d1, f1), (d2, f2)):
    _, _, rep = Run(M, LM, d, f, clock=CLOCK).run()
    hr = rep["hit_rate"]
    per_day.append(hr["interval"])
    print(f"RESULT one-day run: sessions={hr['sessions']} interval={hr['interval']} ({hr['interval_status']})")

a = syn.light_state(syn.T0, {"desk-1": 1.0}, anchored=False)
b = syn.light_state(syn.T0 + 0.996, {"desk-1": 1.0}, anchored=False)
print(f"RESULT names: ts+0.000 -> {light_state_id(a, CLOCK)}, ts+0.996 -> {light_state_id(b, CLOCK)}")

bad = refused and all(i is None for i in per_day)
print("FAIL: no run can produce the session-bootstrap interval (multi-day input is refused, one day has one session)"
      if bad else "PASS")

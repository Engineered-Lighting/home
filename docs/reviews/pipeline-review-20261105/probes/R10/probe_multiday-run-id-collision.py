#!/usr/bin/env python3
"""probe_multiday-run-id-collision: can one den-verify run hold two evenings, as its interval needs?

What it checks (synthetic only, no network). den-verify's session-bootstrap
interval (owner choice 6) is computed over the sessions inside ONE run, a
session being a local calendar day, so it needs frames from at least two days
in one run. LightStates carry no id in the contract (den-photometry writes
none), so den-verify names each ls-HHMMSS in local time, plus hundredths off
the whole second, with no date, and refuses a run where two names collide.

  1. two evenings, metering frames every 3 s on whole seconds (19:00-19:10);
  2. two evenings, frames every ~3 s with random sub-second jitter, 3 h each
     (fixed seed): how many names collide;
  3. one evening alone: what the interval says.

Run:
    python3 probe_multiday-run-id-collision.py <root of the rung-10-verify checkout>
"""
import random
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock, light_state_id  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
model = LightModel(syn.hue_model({"p": {B0: 100.0}}, kelvins=(2700,)))
clock = Clock(syn.UTC_OFFSET_S)
DAY = 86400.0
t1 = syn.T0                 # 19:03:29 local, evening 1
t2 = syn.T0 + DAY           # same local time, evening 2

# 1. whole-second frames
frames = [syn.light_state(t + 3 * i, {"p": 50.0}, anchored=False) for t in (t1, t2) for i in range(200)]
try:
    Run(M, model, [], frames, clock=clock)
    print("RESULT 1: two whole-second evenings load")
    print("PASS 1")
except ValueError as e:
    print(f"RESULT 1: Run() raised ValueError: {e}")
    print("FAIL 1: a two-evening run on whole-second frames is refused before scoring")

# 2. jittered frames, 3 h per evening
rng = random.Random(7)
names = {}
collisions = 0
for t in (t1, t2):
    x = t
    while x < t + 3 * 3600:
        x += 3.0 + rng.uniform(-0.4, 0.4)
        ts = round(x, 3)
        n = light_state_id({"ts": ts}, clock)
        collisions += n in names
        names[n] = ts
print(f"RESULT 2: {len(names)} distinct names, {collisions} collisions across two 3 h evenings with jittered frames")
if collisions:
    print("FAIL 2: a realistic two-evening run crashes on a duplicate LightState name")

# 3. one evening: the interval
ref = syn.light_state(t1, {"p": 0.0}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d", t1 + 0.5, {B0: 0.0}, {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t1 + 0.5}]}},
                       "ls-ref", targets={"A": {"surface_id": "p"}})
after = syn.light_state(t1 + 5, {"p": 100.0}, anchored=False)
hr = Run(M, model, [dec], [ref, after], clock=clock).run()[2]["hit_rate"]
print(f"RESULT 3: one evening -> verdict {hr['verdict']}, interval {hr['interval']} ({hr['interval_status']})")

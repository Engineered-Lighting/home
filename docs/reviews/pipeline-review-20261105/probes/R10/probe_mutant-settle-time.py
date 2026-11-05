#!/usr/bin/env python3
"""probe_mutant-settle-time: is mutant M09 (verify frame searched from the decision's ts, not its settle time) equivalent?

What it checks (synthetic only, no network): a decision logged at t+0.5 whose
only send goes out at t+2.0, with a metering frame at t+1.0 that still shows
the old light and a settled frame at t+6.0. The unmutated tool must pick t+6.0.
Run it on the clean checkout and on a copy with M09 applied (mutate_den_verify.py
lists M09): if the outputs differ, the mutant is not equivalent and its survival
is a test gap.

Run:
    python3 probe_mutant-settle-time.py <root of a rung-10-verify checkout or a mutated copy>
"""
import sys
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root / "tools" / "den-verify"))
import method, synthetic as syn
from light_model import LightModel
from records import Clock
from score import Run
B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(root/"tools"/"den-verify"/"VERIFY-V1.md"))).scored()
model = syn.hue_model({"p": {B0: 100.0}}, kelvins=(2700,))
t = syn.T0
ref = syn.light_state(t, {"p": 20.0}, anchored=False, ident="ls-ref")
# decision logged at t+0.5, its send goes out at t+2.0 (t_exec later); a frame at t+1.0 still shows the old light
dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 2.0}]}},
                       "ls-ref", targets={"A": {"surface_id": "p"}})
frames = [ref, syn.light_state(t + 1.0, {"p": 20.0}, anchored=False), syn.light_state(t + 6.0, {"p": 80.0}, anchored=False)]
d, _, r = Run(M, LightModel(model), [dec], frames, clock=Clock(syn.UTC_OFFSET_S)).run()
v = d[0]["verify"]
print(f"verify frame offset {v['ts'] - t:+.1f} s, status {v['points'][0]['status']}, residual {v['points'][0]['residual']}, verdict {r['hit_rate']['verdict']}")

#!/usr/bin/env python3
"""probe_dark-scene-false-misses: do perfectly executed OFF and dim-down scenes score as misses?

What it checks (synthetic only, no network): with the proposed background
`reference_frame`, den-verify predicts D + E(after) with D = measured_ref -
E(before). Any light-model error at the brighter before state lands in D and
is then judged against +/-15% of a much smaller after prediction, with no
floor. The light model here is 5% off at the before state (rung 8's own target
error is a median of 8%), and every bulb lands exactly where it was told:

  d-on1..d-on3: three ordinary scene changes, 6 points each, all within;
  d-off:  vacancy OFF of all four bulbs; the room goes dark (0.5 measured);
  d-dim:  a dim-down 0.8 -> 0.1 on the same points.

By hand, nothing failed, so the hit rate should be 100% (or the dark points
unverifiable). The probe prints what the tool says, point by point, and the
session verdict with and without the dark scenes.

Run:
    python3 probe_dark-scene-false-misses.py <root of the rung-10-verify checkout>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0, B1, B2, B3 = syn.HUE
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
names = [f"p{i}" for i in range(6)]
model = syn.hue_model({n: {B0: 300.0, B1: 100.0} for n in names}, kelvins=(2700,))
lm = LightModel(model)
err = {n: (1.05 if i % 2 == 0 else 0.95) for i, n in enumerate(names)}   # the model is 5% off at the before state
targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(names)}


def case(did, t, before, after_cmd, landed):
    """before/after_cmd/landed: {bulb: level}; the truth is model x err (5% off), no daylight."""
    truth = lambda lv: {n: err[n] * (300 * lv.get(B0, 0) + 100 * lv.get(B1, 0)) for n in names}
    ref = syn.light_state(t, truth(before), anchored=False, ident=f"ls-{did}")
    after_vals = truth(landed)
    after_vals = {n: max(v, 0.5) for n, v in after_vals.items()}     # camera floor in a dark room
    applied = {b: {"level": lv, "steps": [{"kind": "send", "sent_at": t + 0.5}]} for b, lv in after_cmd.items()}
    dec = syn.hue_decision(did, t + 0.5, before, applied, f"ls-{did}", targets=targets)
    return [dec], [ref, syn.light_state(t + 5, after_vals, anchored=False)]


t = syn.T0
cases = [case(f"d-on{i}", t + 100 * i, {B0: 0.3, B1: 0.5}, {B0: 0.8}, {B0: 0.8, B1: 0.5}) for i in range(3)]
off = case("d-off", t + 400, {B0: 0.8, B1: 0.5}, {B0: 0.0, B1: 0.0}, {B0: 0.0, B1: 0.0})
dim = case("d-dim", t + 500, {B0: 0.8, B1: 0.5}, {B0: 0.1, B1: 0.1}, {B0: 0.1, B1: 0.1})


def score(parts):
    d = [x for p in parts for x in p[0]]
    f = [x for p in parts for x in p[1]]
    return Run(M, lm, d, f, clock=Clock(syn.UTC_OFFSET_S)).run()


decs, _, rep = score(cases + [off, dim])
for dd in decs:
    if dd["decision_id"] in ("d-off", "d-dim"):
        pts = [(p["point_id"], p["status"], p["residual"]) for p in dd["verify"]["points"]]
        causes = [e["cause"] for e in dd["verify"]["explanation"] or []]
        print(f"RESULT {dd['decision_id']}: {pts} causes {causes}")
hr = rep["hit_rate"]
print(f"RESULT tool with dark scenes: hits {hr['hits']} misses {hr['misses']} value {hr['value']} -> {hr['verdict']}")
_, _, rep2 = score(cases)
hr2 = rep2["hit_rate"]
print(f"RESULT tool without them: hits {hr2['hits']} misses {hr2['misses']} value {hr2['value']} -> {hr2['verdict']}")
print("RESULT hand: every bulb landed as commanded; the only error is the model's 5% at the before state")
if hr["verdict"] == "fail" and hr2["verdict"] == "pass":
    print("FAIL perfectly executed OFF and dim-down scenes turn a passing session into a fail "
          "(before-state model error carried by the reference-frame background, judged at +/-15% of a dark prediction)")
else:
    print("PASS dark scenes do not flip the verdict")

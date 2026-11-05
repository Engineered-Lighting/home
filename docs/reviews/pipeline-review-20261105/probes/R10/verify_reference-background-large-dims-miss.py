"""Verifier probe (reproduce lens) for finding reference-background-large-dims-miss.

What it checks
  With background = "reference_frame" (VERIFY-V1.md's proposal), score.py
  evaluate() predicts D + E(after) with D = measured_ref - E(before)
  (score.py:271-275). A synthetic den whose bulb is off from the model by a
  uniform factor (1+e) lands every command exactly, with no daylight and no
  noise. For each e in {+5%, +8%, -5%, -8%} (rung 8's own gate: median 8%,
  p90 19%) and each command 0.8 -> {0.4, 0.27, 0.2, 0.1, 0.0}, plus a
  brightening 0.1 -> 0.8, it prints the point's status and residual under
  "reference_frame" and under "zero", next to the closed form
  e(1 - r)/(e r + 1), r = E(before)/E(after).

  Expected if the finding holds: every exact landing is "within" under
  "zero" (residual = e), while under "reference_frame" large dims and offs
  come out "outside" (or "unverifiable" when D <= 0).

Run
  python3 verify_reference-background-large-dims-miss.py <den-verify branch checkout root>
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
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
TEXT = method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")
MODEL = syn.hue_model({"desk-1": {B0: 400.0}})


def case(e, lv_before, lv_after, background):
    true = LightModel(syn.hue_model({"desk-1": {B0: 400.0 * (1 + e)}}))
    m = method.parse(method.closed_copy(TEXT, {"background": background})).scored()
    t = syn.T0
    st = lambda lv: {B0: {"level": lv, "cct_k": 2700}}  # noqa: E731
    ref = syn.light_state(t, {"desk-1": true.predict(st(lv_before))["desk-1"]}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"desk-1": true.predict(st(lv_after))["desk-1"]}, anchored=False)
    dec = syn.hue_decision("d", t + 0.5, {B0: lv_before}, {B0: {"level": lv_after, "steps": [
        {"kind": "send", "sent_at": t + 0.6}]}}, "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    d, _, rep = Run(m, LightModel(MODEL), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    p = d[0]["verify"]["points"][0]
    return p["status"], p["residual"], rep["hit_rate"]["verdict"]


false_miss = zero_ok = 0
for e in (0.05, 0.08, -0.05, -0.08):
    for lb, la in ((0.8, 0.4), (0.8, 0.27), (0.8, 0.2), (0.8, 0.1), (0.8, 0.0), (0.1, 0.8)):
        rf = case(e, lb, la, "reference_frame")
        z = case(e, lb, la, "zero")
        r = (lb / la) if la else float("inf")
        closed = (e * (1 - r) / (e * r + 1)) if la and e * r + 1 > 0 else None
        print(f"RESULT e={e:+.2f} {lb} -> {la}: reference_frame {rf[0]} ({rf[1]}), zero {z[0]} ({z[1]}), "
              f"closed form {None if closed is None else round(closed, 4)}")
        false_miss += rf[0] == "outside"
        zero_ok += z[0] == "within"
print(f"RESULT exact landings scored outside under reference_frame: {false_miss}; within under zero: {zero_ok}")
print("FAIL: exact landings on large dims / offs miss under the proposed background" if false_miss else "PASS")

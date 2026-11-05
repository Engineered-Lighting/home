"""Probe (attack lens): with the proposed background = reference_frame, a perfect landing on a big dim or an off is a miss.

What it checks
  VERIFY-V1.md predicts D(p) + E(p; after) with D(p) = measured_ref - E(p; before)
  (score.py evaluate()). D therefore carries the light model's absolute error at
  the brighter before-state, and the residual divides it by the much smaller
  after-state. A synthetic "true" den whose bulb is 5% brighter than the model
  (inside rung 8's own gate, median 8%) lands each command exactly; no daylight.

  Dims from level 0.8 to 0.4, 0.2, 0.1 and off are verified with
  background = reference_frame (proposed) and with background = zero.

Run
  python3 probe_atk-dim-step-perfect-landing-misses.py <den-verify branch checkout root>
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
MODEL = LightModel(syn.hue_model({"desk-1": {B0: 400.0}}))
TRUE = LightModel(syn.hue_model({"desk-1": {B0: 420.0}}))  # the room is 5% brighter than the model


def status(after_level, background):
    m = method.parse(method.closed_copy(TEXT, {"background": background})).scored()
    t = syn.T0
    st = lambda lv: {B0: {"level": lv, "cct_k": 2700}}  # noqa: E731
    ref = syn.light_state(t, {"desk-1": TRUE.predict(st(0.8))["desk-1"]}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"desk-1": TRUE.predict(st(after_level))["desk-1"]}, anchored=False)
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.8}, {B0: {"level": after_level, "steps": [
        {"kind": "send", "sent_at": t + 0.6}]}}, "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    d, _, rep = Run(m, MODEL, [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    p = d[0]["verify"]["points"][0]
    return p["status"], p["residual"], rep["hit_rate"]["verdict"]


bad = 0
for lv in (0.4, 0.2, 0.1, 0.0):
    rf, z = status(lv, "reference_frame"), status(lv, "zero")
    print(f"RESULT 0.8 -> {lv}: reference_frame {rf[0]} (residual {rf[1]}), zero {z[0]} (residual {z[1]})")
    bad += rf[0] != "within"
print("FAIL: perfect landings on large dims / offs are scored outside or unverifiable under the proposed background"
      if bad >= 2 else "PASS")

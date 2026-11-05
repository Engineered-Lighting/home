"""Verifier probe: does background = reference_frame score perfect landings on big dims as misses, and when?

What it checks
  The finding reference-background-large-dims-miss says the proposed
  background (D = measured_ref - E_model(before), score.py:271-275) folds the
  light model's error at the bright before-state into the prediction for the
  dim after-state. This probe builds a synthetic room that is exactly
  (1 + e) times the model plus a constant daylight term Dl at the point, lands
  every command exactly, and scores one decision per case with the tool's own
  Run (closed copy of VERIFY-V1.md, every other slot at its proposal):

    e = +5% and -5% (rung 8's gate allows a median 8%), Dl = 0 (after dark)
    and Dl = 25% of the before-state electric light (dusk), dims from 0.8 to
    0.4, 0.2, 0.1 and off.

  It also reports the increment status, which compares the measured change
  with the model's own change and so should agree with a perfect landing.

Run
  python3 vfy-intentspec_reference-background-large-dims-miss.py <den-verify branch checkout root>
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
LM = LightModel(MODEL)
st = lambda lv: {B0: {"level": lv, "cct_k": 2700}}  # noqa: E731


def one(after_level, e, dl_share, background):
    m = method.parse(method.closed_copy(TEXT, {"background": background})).scored()
    t = syn.T0
    eb = LM.predict(st(0.8))["desk-1"]
    dl = dl_share * eb
    true = lambda lv: (1 + e) * LM.predict(st(lv))["desk-1"] + dl  # noqa: E731
    ref = syn.light_state(t, {"desk-1": true(0.8)}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"desk-1": true(after_level)}, anchored=False)
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.8}, {B0: {"level": after_level, "steps": [
        {"kind": "send", "sent_at": t + 0.6}]}}, "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    d, _, rep = Run(m, LM, [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    v = d[0]["verify"]
    return v["points"][0]["status"], v["points"][0]["residual"], v["increments"][0]["status"], rep["hit_rate"]["verdict"]


false_miss = 0
for e in (0.05, -0.05):
    for dl_share in (0.0, 0.25):
        for lv in (0.4, 0.2, 0.1, 0.0):
            rf = one(lv, e, dl_share, "reference_frame")
            z = one(lv, e, dl_share, "zero")
            false_miss += rf[0] == "outside"
            print(f"RESULT e={e:+.2f} daylight={dl_share:.2f}*E_before 0.8->{lv}: reference_frame point {rf[0]} "
                  f"(residual {rf[1]}) increment {rf[2]} verdict {rf[3]} | zero point {z[0]} (residual {z[1]})")
print(f"RESULT {false_miss} perfect landings scored outside under the proposed reference_frame background")
print("FAIL: reference_frame turns perfect landings on big dims or offs into counted misses" if false_miss
      else "PASS")

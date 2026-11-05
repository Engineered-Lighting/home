#!/usr/bin/env python3
"""verify_intent_held-share-boundary: does the held-share boundary (>=) matter in practice?

What it checks (synthetic only, no network, no files written). Finding
mutant-held-share-boundary says `held_share >= held_share_min` (score.py:303)
can be flipped to `>` with no test noticing. This probe shows the two places
the boundary is not float trivia:
  1. held_share_min closed at 0 (an owner reading the ladder literally: "a held
     or superseded channel is excluded from misses"): with no held channel at
     all a point's held_share is 0.0, and 0.0 >= 0 excludes EVERY miss, so a
     decision whose only point lands 50% low reads hit rate 1.0 / pass? or
     insufficient. Same with a second, within point: verdict pass.
  2. An exact boundary: held share exactly held_share_min (0.25 with dyadic
     numbers, so the float division is exact).
Run:
    python3 verify_intent_held-share-boundary.py <root of the rung-10-verify checkout>
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
text = method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md")
t = syn.T0


def run(held_share_min):
    M = method.parse(method.closed_copy(text, {"held_share_min": held_share_min})).scored()
    # desk-1 lit by free B0 only; sofa-1 lit by free B1 only; nothing is held
    model = syn.hue_model({"desk-1": {B0: 400.0}, "sofa-1": {B1: 400.0}}, kelvins=(2700,))
    ref = syn.light_state(t, {"desk-1": 400 * 0.2, "sofa-1": 400 * 0.2}, anchored=False, ident="ls-ref")
    dec = syn.hue_decision("d-0", t + 0.5, {B0: 0.2, B1: 0.2},
                           {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]},
                            B1: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-ref",
                           targets={"A": {"surface_id": "desk-1"}, "B": {"surface_id": "sofa-1"}})
    after = syn.light_state(t + 5, {"desk-1": 400 * 0.8,          # within
                                    "sofa-1": 400 * 0.8 * 0.5},   # 50% low, no held channel anywhere
                            anchored=False)
    decs, states, rep = Run(M, LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    return decs[0]["verify"], rep


for hsm in (0.05, 0):
    v, rep = run(hsm)
    hr = rep["hit_rate"]
    print(f"RESULT held_share_min={hsm}: points {[(p['point_id'], p['status']) for p in v['points']]}; "
          f"explanation {v['explanation']}; hits {hr['hits']} misses {hr['misses']} value {hr['value']} "
          f"verdict {hr['verdict']}; excluded_held_or_superseded {rep['excluded_held_or_superseded']}")
v, rep = run(0)
if rep["hit_rate"]["verdict"] == "pass" and rep["hit_rate"]["misses"] == 0:
    print("FAIL held_share_min closed at 0 excludes a miss at a point no held channel lights: "
          "the 50%-low point vanishes from the gate and the verdict is pass (>= at the 0 boundary)")
else:
    print("PASS held_share_min=0 does not exclude unheld misses")

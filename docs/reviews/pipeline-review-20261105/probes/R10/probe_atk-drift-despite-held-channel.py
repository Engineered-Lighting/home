"""Probe: a miss at a point a held channel lights (share below held_share_min) is explained as drift.

What it checks
  VERIFY-V1.md's cause order says `drift` needs "the increment at the point
  also misses in the same direction, the frame is not occupied, and no held or
  superseded channel contributes", and ladder rung 10 says a held or superseded
  channel is "never flagged as drift". score.py explain() tests excluded_held
  (share >= held_share_min) first, and the drift test itself never checks for
  a held contribution. So a point where the held bulb gives ~3.6% (below the
  proposed 0.05) and the free bulb's change lands 30% low gets cause `drift`
  in Decision.verify.explanation and a `drift` flag (with no `held` flag) on
  its LightState residual.

Run
  python3 probe_drift-despite-held-channel.py <den-verify branch checkout root>
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

B0, B1 = syn.HUE[:2]
M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
MODEL = syn.hue_model({"desk-1": {B0: 400.0, B1: 40.0}})
LM = LightModel(MODEL)
t = syn.T0
before = {B0: {"level": 0.2, "cct_k": 2700}, B1: {"level": 0.3, "cct_k": 2700}}
after_state = {B0: {"level": 0.8, "cct_k": 2700}, B1: {"level": 0.3, "cct_k": 2700}}
pb, pa = LM.predict(before)["desk-1"], LM.predict(after_state)["desk-1"]
held_part = LM.predict({B1: after_state[B1]})["desk-1"]
ref = syn.light_state(t, {"desk-1": pb}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2, B1: 0.3},
                       {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]},
                        B1: {"status": "held", "level": 0.3}}, "ls-ref",
                       targets={"A": {"surface_id": "desk-1", "lx": 300}},
                       hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                                          "pinned": {"level_pct": 30, "cct_k": 2700}}]})
after = syn.light_state(t + 5, {"desk-1": pa * 0.7}, anchored=False, ident="ls-after")
decisions, states, rep = Run(M, LM, [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
expl = decisions[0]["verify"]["explanation"]
res = next(e for e in states[1]["residual"] if e["surface_id"] == "desk-1")
print(f"held channel share at desk-1: {held_part / pa:.4f} (held_share_min {M['held_share_min']})")
print(f"RESULT verify.explanation: {expl}")
print(f"RESULT LightState residual flags: {res['flags']}; misses_explained: "
      f"{ {k: rep['misses_explained'][k] for k in ('drift', 'unexplained')} }")
bad = expl and expl[0]["cause"] == "drift" and "drift" in res["flags"]
print("FAIL: a point lit in part by a held channel is explained and flagged as drift"
      if bad else "PASS: no drift where a held channel contributes")

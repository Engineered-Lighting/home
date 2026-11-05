#!/usr/bin/env python3
"""probe_drift-flag-rules: when does den-verify call a miss 'drift'?

What it checks (synthetic only, no network). VERIFY-V1.md's miss order says
`drift` needs: the increment also misses in the same direction, the frame is
not occupied, AND "no held or superseded channel contributes". Two cases:

  A. small held share: front_right is held (hold_set pinned at 30%) and gives
     about 2.4% of a point's light, below held_share_min (0.05). front_left,
     free, lands 30% low. By the method's rule 3 this miss is not drift
     (a held channel contributes): expected `unexplained`, never `drift`.
  B. no occupancy input: a person stands on the board and shadows it (the
     reading falls 50%), but the run is given no --occupancy file (the
     README calls it optional). Expected: the frame's occupancy is unknown,
     so no `drift` claim; the tool instead treats the frame as unoccupied.

Run:
    python3 probe_drift-flag-rules.py <root of the rung-10-verify checkout>
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
fails = 0

# A. small held share
model = syn.hue_model({"desk-1": {B0: 400.0, B1: 25.0}}, kelvins=(2700,))
t = syn.T0
ref = syn.light_state(t, {"desk-1": 400 * 0.2 + 25 * 0.3}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d-a", t + 0.5, {B0: 0.2, B1: 0.3},
                       {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]},
                        B1: {"status": "held", "level": 0.3}}, "ls-ref",
                       targets={"A": {"surface_id": "desk-1"}},
                       hold_set={"revision": 3, "held": [{"entity_id": B1, "channels": [B1],
                                                          "pinned": {"level_pct": 30, "cct_k": 2700,
                                                                     "source": "read-back"}}]})
pred = 400 * 0.8 + 25 * 0.3
after = syn.light_state(t + 5, {"desk-1": 0.7 * pred}, anchored=False)
decs, states, rep = Run(M, LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
share = 25 * 0.3 / pred
cause = decs[0]["verify"]["explanation"][0]["cause"]
flags = next(e for e in states[1]["residual"] if e["surface_id"] == "desk-1")["flags"]
print(f"RESULT A: held share {share:.4f} (< 0.05), residual {decs[0]['verify']['points'][0]['residual']}, "
      f"cause {cause}, LightState flags {flags}")
if cause == "drift" or "drift" in flags:
    fails += 1
    print("FAIL A: a miss at a point where a held channel contributes is explained and flagged as drift "
          "(VERIFY-V1 rule 3: drift only when no held or superseded channel contributes)")
else:
    print("PASS A")

# B. shadow with no occupancy input
model = syn.hue_model({"desk-1": {B0: 400.0}}, kelvins=(2700,))
ref = syn.light_state(t, {"desk-1": 150 + 400 * 0.2}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d-b", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                       "ls-ref", targets={"A": {"surface_id": "desk-1"}})
after = syn.light_state(t + 5, {"desk-1": 0.5 * (150 + 400 * 0.8)}, anchored=False)
occ = [{"start": t + 4, "end": t + 6, "people": [{"track_id": "A", "xy": [0.1, 0.0]}]}]
with_occ = Run(M, LightModel(model), [dec], [ref, after], occupancy=occ, clock=Clock(syn.UTC_OFFSET_S)).run()
without = Run(M, LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
c1 = with_occ[0][0]["verify"]["explanation"][0]["cause"]
c2 = without[0][0]["verify"]["explanation"][0]["cause"]
f2 = next(e for e in without[1][1]["residual"] if e["surface_id"] == "desk-1")["flags"]
print(f"RESULT B: with --occupancy the cause is {c1}; without it the cause is {c2}, LightState flags {f2}; "
      f"report frames.occupied = {without[2]['frames']['occupied']} (no 'unknown' anywhere)")
if c2 == "drift":
    fails += 1
    print("FAIL B: with no occupancy input every frame is taken as unoccupied and a shadow is written as drift")
else:
    print("PASS B")
print(f"RESULT {fails} failing case(s)")

"""Verifier probe (reproduce lens) for finding unknown-daylight-or-occupancy-reads-as-drift, occupancy half.

What it checks
  A person stands at desk-1 and shadows it: the free bulb's change lands but the
  point reads 40% low. Run once with an occupancy file placing the person 0.2 m
  from desk-1 (proposed shadow_radius_m 0.5) and once with no occupancy file
  (it is optional on the command line). Prints the miss's cause, the residual
  flags and the report's frames.occupied for each.

Run
  python3 -B verify_unknown-daylight-or-occupancy-reads-as-drift.py <den-verify checkout root>
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
M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
MODEL = syn.hue_model({"desk-1": {B0: 400.0}})
for p in MODEL["sample_points"]:
    p["xyz"] = [1.0, 1.0, 0.75]
LM = LightModel(MODEL)
t = syn.T0
pb = LM.predict({B0: {"level": 0.2, "cct_k": 2700}})["desk-1"]
pa = LM.predict({B0: {"level": 0.8, "cct_k": 2700}})["desk-1"]
ref = syn.light_state(t, {"desk-1": pb}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                       "ls-ref", targets={"A": {"surface_id": "desk-1"}})
after = syn.light_state(t + 5, {"desk-1": pa * 0.6}, anchored=False, ident="ls-after")
occ = [{"start": t - 10, "end": t + 60, "people": [{"track_id": "A", "xy": [1.2, 1.0]}]}]
for label, o in (("with occupancy", occ), ("no occupancy file", ())):
    decs, states, rep = Run(M, LM, [dec], [ref, after], occupancy=o, clock=Clock(syn.UTC_OFFSET_S)).run()
    cause = (decs[0]["verify"]["explanation"] or [{}])[0].get("cause")
    flags = next(e for e in states[1]["residual"] if e["surface_id"] == "desk-1")["flags"]
    print(f"RESULT {label}: cause={cause} flags={flags} frames.occupied={rep['frames']['occupied']} "
          f"share_explained_admitted={rep['misses_explained']['share_explained_admitted']}")

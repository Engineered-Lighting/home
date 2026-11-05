"""Verifier probe: Decision.verify.within_tolerance when held-share points miss.

What it checks
  Re-uses the held-hits probe's case 1 (one decision, 20 points, a held bulb
  gives ~12% at each point, 3 within and 17 landing 40% low) and prints the
  Decision record's verify.within_tolerance and its point statuses, to see
  whether the written record (not just the report) claims the decision landed.

Run
  python3 probe_verify-held-within-tolerance-flag.py <den-verify branch checkout root>
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

B0, B1, B2, B3 = syn.HUE
M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
NAMES = [f"p{i:02d}" for i in range(20)]
LM = LightModel(syn.hue_model({n: {B0: 400.0, B1: 150.0} for n in NAMES}))
t = syn.T0
before = {B0: {"level": 0.2, "cct_k": 2700}, B1: {"level": 0.3, "cct_k": 2700}}
after_state = {B0: {"level": 0.8, "cct_k": 2700}, B1: {"level": 0.3, "cct_k": 2700}}
pb, pa = LM.predict(before), LM.predict(after_state)
ref = syn.light_state(t, {n: pb[n] for n in NAMES}, anchored=False, ident="ls-ref")
applied = {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}, B1: {"status": "held", "level": 0.3}}
hold_set = {"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                     "pinned": {"level_pct": 30, "cct_k": 2700, "source": "read-back"}}]}
dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2, B1: 0.3}, applied, "ls-ref",
                       targets={f"T{j}": {"surface_id": n} for j, n in enumerate(NAMES)}, hold_set=hold_set)
after = syn.light_state(t + 5, {n: (pa[n] if j < 3 else pa[n] * 0.6) for j, n in enumerate(NAMES)},
                        anchored=False, ident="ls-after")
decisions, _, rep = Run(M, LM, [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
v = decisions[0]["verify"]
statuses = [p["status"] for p in v["points"]]
print(f"RESULT points within={statuses.count('within')} outside={statuses.count('outside')}; "
      f"Decision.verify.within_tolerance={v['within_tolerance']}; report verdict={rep['hit_rate']['verdict']}")

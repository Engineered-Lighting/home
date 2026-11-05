#!/usr/bin/env python3
"""verify_intent_relative-unit-seam: what den-verify does with two different 'relative' units.

What it checks (synthetic only, no network, no files written). Finding
relative-unit-seam says den-verify pairs any unanchored LightState with a
'relative' light model. This probe builds:
  - a model whose basis is in normalised counts C (rung 8 Stage 1's 'relative'),
  - LightStates in rung 7's unanchored convention, value = pi*C/rho_card
    (den-photometry card.py light_state_record), rho_card 0.18, unit 'relative',
and a decision that raises one bulb 0.2 -> 0.8 which lands exactly as modelled
(in C). It prints what den-verify scores, and whether it ever reads the
estimate's own unit or exposure.normalisation_version.
Run:
    python3 verify_intent_relative-unit-seam.py <root of the rung-10-verify checkout>
"""
import math
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
t = syn.T0
S = math.pi / 0.18
model = syn.hue_model({"desk-1": {B0: 400.0}}, kelvins=(2700,))   # basis in C, basis_unit 'relative'
bg = 50.0  # daylight, in C


def ls(ts, c, ident=None, unit="relative", norm="meter-norm/v1"):
    r = syn.light_state(ts, {"desk-1": S * c}, anchored=False, unit=unit, ident=ident)
    r["exposure"]["normalisation_version"] = norm
    return r


dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                       "ls-ref", targets={"A": {"surface_id": "desk-1"}})
for unit, norm in (("relative", "meter-norm/v1"), ("lx", "some-other-norm/v9")):
    ref = ls(t, bg + 400 * 0.2, "ls-ref", unit, norm)
    after = ls(t + 5, bg + 400 * 0.8, None, unit, norm)   # landed exactly as modelled, in C
    decs, states, rep = Run(M, LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    v = decs[0]["verify"]
    res = next(e for e in states[1]["residual"] if e["surface_id"] == "desk-1")
    print(f"RESULT estimate unit {unit!r}, normalisation {norm!r}: point {v['points'][0]['status']} "
          f"residual {v['points'][0]['residual']}; increment {v['increments'][0]['status']} "
          f"measured_change {v['increments'][0]['measured_change']} vs predicted {v['increments'][0]['predicted_change']}; "
          f"cause {[e['cause'] for e in (v['explanation'] or [])]}; LightState flags {res['flags']}; "
          f"verdict {rep['hit_rate']['verdict']}")

#!/usr/bin/env python3
"""verify_relative-unit-seam: does den-verify notice two different 'relative' scales?

What it checks (synthetic only, no network). rung 7 (den-photometry card.py
light_state_record) writes an unanchored estimate as pi*C_nc/rho_card with
unit 'relative'; rung 8 Stage 1's basis is in normalised counts C with
basis_unit 'relative' (predict.py docstring, model.py light_model record).
The probe builds a perfect model in C and LightStates that carry the same
light as pi*C/rho_card (rho_card 0.18, a factor of about 17.45), with an
exposure.normalisation_version that differs from the model's meter_norm, and
also one estimate whose own `unit` field says 'nc'. It reports whether
den-verify refuses the pairing ('unit mismatch' / unverifiable) or scores it.

Run:
    python3 verify_relative-unit-seam.py <root of the rung-10-verify checkout>
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
S = math.pi / 0.18
t = syn.T0
model = syn.hue_model({"p": {B0: 100.0}, "q": {B0: 50.0}}, kelvins=(2700,))   # basis in C, 'relative'
model["meter_norm"] = {"version": "meter-norm/v1"}
fails = 0
for bg in ("reference_frame", "zero"):
    m = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"),
                                        {"background": bg})).scored()
    ref = syn.light_state(t, {"p": S * 25.0, "q": S * 12.5}, anchored=False, unit="relative", ident="ls-ref")
    after = syn.light_state(t + 5, {"p": S * 80.0, "q": S * 40.0}, anchored=False, unit="relative")
    after["surface_estimates"][1]["unit"] = "nc"
    for f in (ref, after):
        f["exposure"]["normalisation_version"] = "card-norm/v9"
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.25}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                           "ls-ref", targets={"A": {"surface_id": "p"}, "B": {"surface_id": "q"}})
    d, s, r = Run(m, LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    v = d[0]["verify"]
    pts = {p["point_id"]: (p["status"], p["residual"]) for p in v["points"]}
    inc = {i["point_id"]: (i["measured_change"], i["predicted_change"], i["status"]) for i in v["increments"]}
    print(f"RESULT background={bg}: unit {v['unit']}, points {pts}, increments {inc}, "
          f"verdict {r['hit_rate']['verdict']}")
    if any(st in ("within", "outside") for st, _ in pts.values()):
        fails += 1
        print(f"FAIL background={bg}: a 'relative' frame on a different scale and normalisation version (and one "
              "estimate whose unit field says 'nc') is scored against the 'relative' model; nothing is unverifiable")
print(f"RESULT {fails} failing case(s); a perfect model in C reads as residual ~{S - 1:.2f} with background zero")

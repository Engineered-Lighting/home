#!/usr/bin/env python3
"""verify_dark-scene-false-misses: independent re-check (skeptical verifier, R10).

What it checks (synthetic only, no network): one bulb, one point, no daylight.
The camera truth is the light model times a constant factor (1 + e): a pure
scale error the +/-15% tolerance is meant to absorb. Every bulb lands exactly
as commanded. For several dim-downs (before level -> after level) and errors
e, it prints the point status under background "reference_frame" (the
proposal) and under "zero". With background zero the residual is exactly e, so
any 'outside' under reference_frame with |e| <= 0.15 is a false miss caused by
D = measured_ref - E(before) carrying the before-state error into a smaller
after-state prediction.

Run:
    python3 verify_dark-scene-false-misses.py <root of rung-10-verify checkout>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
sys.dont_write_bytecode = True
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
TEXT = method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md")
model = syn.hue_model({"pt": {B0: 400.0}}, kelvins=(2700,))
lm = LightModel(model)
t = syn.T0
false_misses = 0
for bg in ("reference_frame", "zero"):
    M = method.parse(method.closed_copy(TEXT, {"background": bg})).scored()
    for lv0, lv1 in ((0.8, 0.6), (0.8, 0.4), (0.8, 0.25), (0.8, 0.1)):
        row = []
        for e in (-0.05, 0.0, 0.05, 0.08):
            truth = lambda lv: (1 + e) * 400.0 * lv
            ref = syn.light_state(t, {"pt": truth(lv0)}, anchored=False, ident="ls-ref")
            aft = syn.light_state(t + 6, {"pt": truth(lv1)}, anchored=False)
            d = syn.hue_decision("d", t + 0.5, {B0: lv0},
                                 {B0: {"level": lv1, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                                 "ls-ref", targets={"A": {"surface_id": "pt"}})
            out, _, rep = Run(M, lm, [d], [ref, aft], clock=Clock(syn.UTC_OFFSET_S)).run()
            p = out[0]["verify"]["points"][0]
            row.append(f"e={e:+.2f}:{p['status']}({p['residual']})")
            if bg == "reference_frame" and p["status"] == "outside":
                false_misses += 1
        print(f"RESULT bg={bg:15s} dim {lv0}->{lv1}: " + "  ".join(row))
print(f"RESULT false misses under reference_frame with |model scale error| <= 8%: {false_misses}")
print("FAIL reproduced" if false_misses else "PASS not reproduced")

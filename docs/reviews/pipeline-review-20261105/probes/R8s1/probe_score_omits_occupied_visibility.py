#!/usr/bin/env python3
"""Does a passing Stage 1 score say that rung 8's occupied-visibility qualification was not run?

    python3 -I probe_score_omits_occupied_visibility.py <branch-checkout-root>

Fits and scores a synthetic den with Stage 1's own code (SyntheticDen, schedule, fit, score,
light_model; no camera, no HA), then looks at every key and string value of score.json's body and
light-model.json's record. ladder.json rung 8's gate has three parts: the relative error (median
8%, p90 19%), the occupied visibility V_total = (V_direct*E_dir + E_ind)/B qualified on occupied
hold-outs, and relative and absolute qualification reported separately. FAIL if the outputs
carry a 'pass' verdict and an explicit 'absolute: not run' line but say nothing about the
occupied-visibility part. Pure computation, no network.
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import model  # noqa: E402
import synthetic  # noqa: E402

SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
m = model.load_stage1().proposed().closed(block_px=40)
den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
plan = model.schedule(m, SNAPSHOT, synthetic.KELVIN_RANGE, {e: lo for e, (lo, _) in synthetic.KELVIN_RANGE.items()})
steps, t = [], 0.0
for step in plan["steps"]:
    t += 2.5
    steps.append({**step, "t": t, "reading": den.reading(step["scene"], t), "spread": [1e-4] * den.blocks})
readings = {"blocks": den.blocks, "mask": {"unmasked_share": [1.0] * den.blocks}, "steps": steps}
fitted = model.fit(readings, plan["temperatures"], m)
scored = model.score(readings, fitted, m)
fixtures = [{"fixture_id": e.split(".", 1)[1], "channels": [{"channel_id": "white", "kind": "tunable_white",
             "entity_id": e, "cct_range_k": list(synthetic.KELVIN_RANGE[e])}]} for e in guard.LIGHTS]
lm = model.light_model(fitted, fixtures, m, session_id="s1", readings_sha256="0" * 64, meter_norm_sha256="1" * 64,
                       score_error=scored["error"])
text = json.dumps({"score": scored, "light_model": {k: v for k, v in lm.items() if k not in ("basis",)}}).lower()
print(f"RESULT score verdict={scored['error']['verdict']} light-model verdict={lm['error']['verdict']}")
print(f"RESULT score top-level keys: {sorted(scored)}")
print(f"RESULT score['absolute'] = {scored['absolute']!r}")
mentions = [w for w in ("visib", "occupied", "v_total", "v_direct") if w in text]
print(f"RESULT words about the occupied-visibility part found in either output: {mentions}")
if scored["error"]["verdict"] == "pass" and not mentions:
    print("FAIL a 'pass' rung 8 score and light model with 'absolute: not run' but no statement that the "
          "occupied-visibility qualification (ladder rung 8 gate) was not run")
    sys.exit(1)
print("PASS")

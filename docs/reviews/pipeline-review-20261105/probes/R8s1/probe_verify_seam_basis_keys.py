#!/usr/bin/env python3
"""Can rung 10's light-model adapter read the light-model.json rung 8 Stage 1 writes?

    python3 -I probe_verify_seam_basis_keys.py <rung-8-stage1-root> <rung-10-verify-root>

Builds a light-model.json with Stage 1's own model.light_model() from a synthetic fit (Stage 1's
SyntheticDen and schedule, no camera, no HA), then loads it with rung 10's
tools/den-verify/light_model.py (the one adapter verify.py uses for --model) and asks it for one
scene keyed as Stage 1's README says (`den_front_left.white`, level 0.5, 2700 K). Then it swaps the
adapter's backend for Stage 1's predict.predict(), as the adapter's docstring says to do, and asks
again. Also checks the record's `basis` keys against schemas.json's LightModel ("per channel:
B_c(p) at full output"). Prints PASS/FAIL and RESULT lines. Pure computation, no network.
"""
import importlib.util
import json
import sys
from pathlib import Path

S1 = Path(sys.argv[1]).resolve()
RV = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(S1 / "tools" / "den-light"))
import guard  # noqa: E402
import model  # noqa: E402
import predict as s1_predict  # noqa: E402
import synthetic  # noqa: E402

sys.path.append(str(RV / "tools" / "den-verify"))  # for its `import fake_model`
spec = importlib.util.spec_from_file_location("verify_light_model", RV / "tools" / "den-verify" / "light_model.py")
verify_lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_lm)

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
# force one excluded block, as any real session with a clipped or masked block has
fitted["excluded"][0] = "mask"
err = model.score(readings, fitted, m)["error"]
fixtures = [{"fixture_id": e.split(".", 1)[1], "channels": [{"channel_id": "white", "kind": "tunable_white",
             "entity_id": e, "cct_range_k": list(synthetic.KELVIN_RANGE[e])}]} for e in guard.LIGHTS]
lm = json.loads(json.dumps(model.light_model(fitted, fixtures, m, session_id="s1", readings_sha256="0" * 64,
                                             meter_norm_sha256="1" * 64, score_error=err)))
channels = [c["channel_key"] for c in lm["channels"]]
print(f"RESULT Stage 1 channels: {channels}")
print(f"RESULT Stage 1 basis keys (first 3 of {len(lm['basis'])}): {sorted(lm['basis'])[:3]}")
fails = 0
if set(lm["basis"]) != set(channels):
    print("FAIL schemas.json LightModel.basis is 'per channel: B_c(p) at full output'; Stage 1 keys it per "
          "channel@temperature, so `basis` holds no channel key")
    fails += 1
scene = {"den_front_left.white": {"level": 0.5, "cct_k": 2700}}
print(f"RESULT Stage 1 predict on its own record: r01c01 = {s1_predict.predict(scene, lm)['surfaces']['r01c01']:.6f}")

adapter = verify_lm.LightModel(lm)
try:
    out = adapter.predict(scene)
    print(f"PASS rung 10 adapter predicted {len(out)} surfaces")
except Exception as exc:  # noqa: BLE001
    print(f"FAIL rung 10 adapter, as built: {type(exc).__name__}: {exc}")
    fails += 1


def stage1_backend(model_data, sc):
    """The swap the adapter's docstring asks for: Stage 1's predict(), argument shape {bulb: {u, k}}."""
    s1_scene = {key: {"level": v["u"] / 254.0, "cct_k": v["k"]} for key, v in sc.items()}
    return {k: v for k, v in s1_predict.predict(s1_scene, model_data)["surfaces"].items() if k != "unit"}


verify_lm._backend_predict = stage1_backend
try:
    out = verify_lm.LightModel(lm).predict(scene)
    print(f"PASS rung 10 adapter with Stage 1's predict as backend: {len(out)} surfaces")
except Exception as exc:  # noqa: BLE001
    print(f"FAIL rung 10 adapter with Stage 1's predict swapped in: {type(exc).__name__}: {exc}")
    fails += 1
# bypass the adapter's channel check to reach the next seam: Stage 1 writes None for excluded points
adapter = verify_lm.LightModel(lm)
adapter.channels = set(channels)
try:
    out = adapter.predict(scene)
    print(f"PASS adapter (channel check bypassed) predicted {len(out)} surfaces")
except Exception as exc:  # noqa: BLE001
    print(f"FAIL adapter with the channel check bypassed: {type(exc).__name__}: {exc} (excluded points are None)")
    fails += 1
print("RESULT", "ALL PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""verify_light-model-basis-keys-not-channels: rung 10's adapter vs Stage 1's light-model.json, step by step.

What it checks (synthetic fit in memory; no I/O, no network):
 1. Stage 1's basis keys vs its channel keys, and the canonical schemas.json LightModel example's basis keys.
 2. rung 10's LightModel adapter (as built) on a Stage 1 model: predict() for one lit channel.
 3. Even with the adapter's channel set taken from model["channels"] and the backend calling Stage 1's
    predict() with {channel: {level, cct_k}}, a model with one excluded point: does the surface mean crash?

How to run:
    python3 -B verify_light-model-basis-keys-not-channels.py <rung-8-stage1 root> <rung-10-verify root> <live-records root>
"""
import importlib.util
import json
import sys
from pathlib import Path

S1, V, LR = (Path(a).resolve() for a in sys.argv[1:4])
sys.path.insert(0, str(S1 / "tools" / "den-light"))
import model, predict, synthetic  # noqa: E402,E401
import test_stage1 as T  # noqa: E402

schema = json.loads((LR / "docs/architecture/live-v1/schemas.json").read_text())
ex = [r for r in schema["records"] if r["name"] == "LightModel"][0]["example"]
print("RESULT schema example basis keys:", list(ex["basis"]))
m = T.method1()
den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
r = T.readings_for(den, m)
fitted = model.fit(r, r["temperatures"], m)
fitted["excluded"][0] = "mask"   # one excluded block, as a clipped bulb block would be
s = model.score(r, fitted, m)
lm = json.loads(json.dumps(model.light_model(fitted, T.FIXTURES, m, session_id="s1", readings_sha256="0" * 64,
                                             meter_norm_sha256="1" * 64, score_error=s["error"])))
chans = [c["channel_key"] for c in lm["channels"]]
print("RESULT stage1 channels:", chans[:2], "... basis keys:", sorted(lm["basis"])[:2], "...")
sys.path.insert(0, str(V / "tools" / "den-verify"))
spec = importlib.util.spec_from_file_location("lmv", V / "tools/den-verify/light_model.py")
lmv = importlib.util.module_from_spec(spec); spec.loader.exec_module(lmv)
state = {chans[0]: {"level": 0.5, "cct_k": 3000}}
try:
    lmv.LightModel(lm).predict(state); print("RESULT adapter as built: ok")
except Exception as exc:
    print("RESULT adapter as built:", type(exc).__name__, exc)
a = lmv.LightModel(lm); a.channels = set(chans)
lmv._backend_predict = lambda data, scene: predict.predict(
    {k: {"level": v["u"] / 254, "cct_k": v["k"]} for k, v in scene.items()}, data)["surfaces"]
try:
    out = a.predict(state); print("RESULT adapter with channels fixed + Stage 1 backend: ok", len(out))
except Exception as exc:
    print("RESULT adapter with channels fixed + Stage 1 backend:", type(exc).__name__, exc)

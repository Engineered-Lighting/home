#!/usr/bin/env python3
"""verify_stage1-model-unreadable: independent re-check (skeptical verifier, R10).

What it checks (synthetic only, temp dir, no network): builds a light-model.json
with rung 8 Stage 1's OWN code path (test_stage1's synthetic den -> model.fit ->
model.light_model), in a subprocess (both tools have a module named synthetic),
then loads it with den-verify's LightModel and asks for predictions keyed the
way Stage 1's predict() wants them (channel_key) and by HA entity id.

Run:
    python3 verify_stage1-model-unreadable.py <root of rung-10-verify> <root of rung-8-stage1>
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

r10 = Path(sys.argv[1]).resolve()
r8 = Path(sys.argv[2]).resolve()
BUILD = r'''
import sys, json
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import test_stage1 as t
m = t.method1()
den = t.synthetic.SyntheticDen(block_px=40, noise=1e-4)
r = t.readings_for(den, m)
fitted = t.model.fit(r, r["temperatures"], m)
scored = t.model.score(r, fitted, m)
lm = t.model.light_model(fitted, t.FIXTURES, m, session_id="s1", readings_sha256="0"*64,
                         meter_norm_sha256="1"*64, score_error=scored["error"])
lm = json.loads(json.dumps(lm))
import predict
ck = lm["channels"][0]["channel_key"]
out = predict.predict({ck: {"level": 0.5, "cct_k": 2700}}, lm)
pts = [k for k in out["surfaces"] if k != "unit"]
print(json.dumps({"stage1_predict_point0": out["surfaces"][pts[0]], "n_points": len(pts)}), file=sys.stderr)
json.dump(lm, open(sys.argv[2], "w"))
'''
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "light-model.json"
    p = subprocess.run([sys.executable, "-B", "-c", BUILD, str(r8 / "tools" / "den-light"), str(path)],
                       capture_output=True, text=True, cwd=tmp)
    if p.returncode != 0:
        print("RESULT could not build a Stage 1 model:", p.stderr[-800:])
        sys.exit(1)
    print("RESULT Stage 1's own predict() works on its model:", p.stderr.strip().splitlines()[-1])
    lm = json.loads(path.read_text())
    print("RESULT Stage 1 basis keys (first 3):", list(lm["basis"])[:3])
    print("RESULT Stage 1 channels:", [(c["channel_key"], c["entity_id"]) for c in lm["channels"]])
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(r10 / "tools" / "den-verify"))
    from light_model import LightModel
    try:
        model = LightModel(lm)
        print("RESULT den-verify LightModel loads it; its channel set (first 3):", sorted(model.channels)[:3])
    except Exception as e:  # noqa: BLE001
        print(f"RESULT den-verify LightModel() raised {type(e).__name__}: {e}")
        print("FAIL reproduced (cannot even load)")
        sys.exit(0)
    fails = 0
    for label, key in (("channel_key", lm["channels"][0]["channel_key"]), ("entity_id", lm["channels"][0]["entity_id"])):
        try:
            out = model.predict({key: {"level": 0.5, "cct_k": 2700}})
            print(f"RESULT den-verify predict keyed by {label}: ok, first value {next(iter(out.values()))}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"RESULT den-verify predict keyed by {label}: {type(e).__name__}: {e}")
    # what if someone keys by the basis key itself (the only thing den-verify accepts)?
    bk = list(lm["basis"])[0]
    try:
        model.predict({bk: {"level": 0.5, "cct_k": 2700}})
        print("RESULT keyed by a basis key: ok")
    except Exception as e:  # noqa: BLE001
        print(f"RESULT keyed by the basis key {bk!r} (den-verify's own channel names): {type(e).__name__}: {e}")
    print("FAIL reproduced: den-verify cannot predict from Stage 1's light-model.json" if fails == 2
          else "PASS not reproduced")

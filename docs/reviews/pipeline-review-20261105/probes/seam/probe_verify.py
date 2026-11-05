#!/usr/bin/env python3
"""Probe: rung 8 Stage 1's real light-model.json and predict() against rung 10's den-verify, and den-core's
decisions against den-verify's reader. Synthetic only.

    python3 probe_verify.py <wt-root> <out-dir> <den-core-live run folder with log.jsonl and decision.jsonl>

light-model.json is made by den-light's own model.fit/score/light_model on readings from its own SyntheticDen
(the raw inputs, via its test helpers readings_for/method1/FIXTURES)."""
import contextlib
import io
import json
import subprocess
import sys
import traceback
from pathlib import Path

wt, out, core = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
out.mkdir(parents=True, exist_ok=True)
light = wt / "rung-8-stage1" / "tools" / "den-light"
verify = wt / "rung-10-verify" / "tools" / "den-verify"
sys.path.insert(0, str(light))
import model  # noqa: E402
import predict  # noqa: E402
import guard  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402

m = T.method1()
den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
r = T.readings_for(den, m)
fitted = model.fit(r, r["temperatures"], m)
scored = model.score(r, fitted, m)
lm = json.loads(json.dumps(model.light_model(fitted, T.FIXTURES, m, session_id="seam-1", readings_sha256="0" * 64,
                                             meter_norm_sha256="1" * 64, score_error=scored["error"])))
lm_path = out / "light-model.json"
lm_path.write_text(json.dumps(lm, ensure_ascii=False), encoding="utf-8", newline="\n")
print("light-model.json keys:", sorted(lm))
print("basis keys (channels):", sorted(lm["basis"])[:6], "| dimming keys:", sorted(lm.get("dimming") or {})[:6])
scene_ha = {"light.den_front_left": guard.scene(True, 254, 2700)}
p8 = predict.predict(predict.scene_from_ha(scene_ha, lm), lm)
print("stage1 predict.predict(scene, model) ->", type(p8).__name__, sorted(p8)[:5] if isinstance(p8, dict) else p8)

for name in [n for n in list(sys.modules) if n in ("records", "light_model", "fake_model", "method", "settle",
                                                     "score", "synthetic")]:
    sys.modules.pop(name)
sys.path.remove(str(light))
sys.path.insert(0, str(verify))
import light_model as VLM  # noqa: E402
print("den-verify light_model backend:", getattr(VLM, "BACKEND", None))
try:
    lmv = VLM.LightModel(lm)
    state = {"light.den_front_left": {"on": True, "brightness": 254, "kelvin": 2700}}
    print("den-verify LightModel(lm).predict ->", str(lmv.predict(state))[:200])
except Exception as exc:  # the seam under test
    print("den-verify LightModel on the Stage 1 light-model.json: %s: %s" % (type(exc).__name__, exc))
    print("   at", traceback.extract_tb(exc.__traceback__)[-1])

empty = out / "light_states-none.jsonl"
empty.write_text("", encoding="utf-8")
for label, decisions in (("log.jsonl (den-event-1 envelopes)", core / "log.jsonl"),
                         ("decision.jsonl (unwrapped by the harness)", core / "decision.jsonl")):
    target = out / ("verify-" + decisions.stem)
    p = subprocess.run([sys.executable, "-B", str(verify / "verify.py"), "run", "--decisions", str(decisions),
                        "--light-states", str(empty), "--model", str(lm_path), "--out", str(target), "--preview"],
                       capture_output=True, text=True)
    lines = (p.stdout + p.stderr).strip().splitlines() or [""]
    print(f"verify.py run --decisions {label}: exit {p.returncode}:", " / ".join(lines[-2:])[:400])

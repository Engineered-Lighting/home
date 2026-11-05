#!/usr/bin/env python3
"""verify_SEAM-18: can den-verify (rung 10) predict through rung 8 Stage 1's real light-model.json?

    python3 -B verify_SEAM-18.py <wt-root containing rung-10-verify/ and rung-8-stage1/>

1. Builds a Stage 1 light-model.json with den-light's own synthetic helpers (verify_stage1_model.py).
2. Calls den-verify's LightModel (its only path to rung 8) with the state shapes den-verify itself builds:
   canon Decision readback {level, cct_k} keyed by HA entity id (as den-verify's own hue fixtures key it),
   keyed by Stage 1's channel key, keyed by Stage 1's flattened basis key, and den-core's HA-form readback.
3. End to end: den-verify's CLI (--preview) on its own synthetic hue decision (front_left 0.2 -> 0.8) with
   (a) den-verify's own hue_model (control) and (b) Stage 1's light-model.json.
Temp dirs only; no network or hardware."""
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_stage1_model as V  # noqa: E402

wt = Path(sys.argv[1]).resolve()
vdir = wt / "rung-10-verify" / "tools" / "den-verify"
tmp = Path(tempfile.mkdtemp(prefix="v18-"))
stage1 = tmp / "stage1-light-model.json"
lm, _ = V.build(wt, stage1)
sys.path.insert(0, str(vdir))
import light_model as VLM  # noqa: E402
import synthetic as syn  # noqa: E402
import method  # noqa: E402
from records import write_jsonl  # noqa: E402

v = VLM.LightModel(lm)
print("RESULT den-verify backend:", VLM.BACKEND, "| channels it sees:", sorted(v.channels)[:3], "...")
for label, state in (
        ("canon readback keyed by HA entity id", {"light.den_front_left": {"level": 0.8, "cct_k": 2700}}),
        ("canon readback keyed by Stage 1 channel key", {"den_front_left.white": {"level": 0.8, "cct_k": 2700}}),
        ("keyed by Stage 1 flattened basis key", {"den_front_left.white@2202K": {"level": 0.8, "cct_k": 2700}}),
        ("den-core's HA-form readback (no level)", {"light.den_front_left": {"on": True, "brightness": 255,
                                                                              "kelvin": 2700}})):
    try:
        out = v.predict(state)
        vals = [x for x in out.values() if x is not None]
        print("RESULT %s: no error, %d surfaces, max %s" % (label, len(out), max(vals) if vals else None))
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print("RESULT %s: %s: %s [%s:%d]" % (label, type(e).__name__, e, Path(tb.filename).name, tb.lineno))

B0 = syn.HUE[0]
t = syn.T0
codes = {}
for label, model in (("den-verify hue_model (control)", syn.hue_model({"desk-1": {B0: 400}})), ("Stage 1", lm)):
    folder = tmp / label.split()[0].replace("-", "")
    folder.mkdir()
    pts = [p["point_id"] for p in model["sample_points"]][:3]
    ref = syn.light_state(t, {p: 100.0 for p in pts}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 4, {p: 300.0 for p in pts}, anchored=False)
    dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send",
                                                                                   "sent_at": t + 0.5}]}},
                           "ls-ref", model=model["version"])
    write_jsonl(folder / "decisions.jsonl", [dec])
    write_jsonl(folder / "light_states.jsonl", [ref, after])
    (folder / "light-model.json").write_text(json.dumps(model), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(vdir / "verify.py"), "run", "--decisions",
                        str(folder / "decisions.jsonl"), "--light-states", str(folder / "light_states.jsonl"),
                        "--model", str(folder / "light-model.json"), "--utc-offset-h", "-7",
                        "--out", str(folder / "out"), "--preview"], capture_output=True, text=True)
    lines = (p.stdout + p.stderr).strip().splitlines() or [""]
    codes[label] = p.returncode
    print("RESULT CLI %s: exit %d: %s" % (label, p.returncode, lines[-1][:220]))
ok = codes["den-verify hue_model (control)"] == 0 and codes["Stage 1"] != 0
print("PASS (defect reproduced): den-verify cannot predict through Stage 1's model" if ok else
      "FAIL (defect not reproduced)")
print("tmp:", tmp)

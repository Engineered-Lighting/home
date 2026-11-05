#!/usr/bin/env python3
"""Probe: den-verify's LightModel adapter (its only path to rung 8) on Stage 1's real light-model.json.
    python3 probe_verify_model.py <wt-root> <light-model.json>"""
import json
import sys
import traceback
from pathlib import Path

wt, lm_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
lm = json.loads(lm_path.read_text(encoding="utf-8"))
sys.path.insert(0, str(wt / "rung-8-stage1" / "tools" / "den-light"))
import predict  # noqa: E402
out8 = predict.predict({"den_front_left.white": {"level": 1.0, "cct_k": 2700}}, lm)
pts = out8["surfaces"]
print("Stage 1 predict({'den_front_left.white': {level 1.0, cct_k 2700}}, model): surfaces",
      type(pts).__name__, (list(pts.items())[:3] if isinstance(pts, dict) else str(pts)[:160]))
sys.path.pop(0)
for n in ("predict", "model", "guard", "synthetic", "method", "records"):
    sys.modules.pop(n, None)
sys.path.insert(0, str(wt / "rung-10-verify" / "tools" / "den-verify"))
import light_model as VLM  # noqa: E402
v = VLM.LightModel(lm)
for state in ({"den_front_left.white": {"level": 1.0, "cct_k": 2700}},
              {"den_front_left.white@2202K": {"level": 1.0, "cct_k": 2700}}):
    try:
        got = v.predict(state)
        print("den-verify LightModel.predict(%s) -> %s" % (state, str(list(got.items())[:3])))
    except Exception as exc:
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        print("den-verify LightModel.predict(%s) -> %s: %s  [%s:%s]" % (state, type(exc).__name__, exc,
                                                                        Path(tb.filename).name, tb.lineno))

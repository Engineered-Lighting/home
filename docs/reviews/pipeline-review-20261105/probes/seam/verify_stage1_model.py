#!/usr/bin/env python3
"""Helper for the verify_SEAM-17/18 probes: build a real rung 8 Stage 1 light-model.json from den-light's own
SyntheticDen readings (its test helpers method1/readings_for/FIXTURES), synthetic only, no hardware.

    python3 -B verify_stage1_model.py <wt-root containing rung-8-stage1/> <out light-model.json>

Prints the top-level keys, the basis/dimming/channels key forms and one predict() call."""
import json
import sys
from pathlib import Path


def build(wt, out_path):
    light = Path(wt).resolve() / "rung-8-stage1" / "tools" / "den-light"
    sys.path.insert(0, str(light))
    try:
        import model, predict, synthetic, test_stage1 as T  # noqa: E401
        m = T.method1()
        den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
        r = T.readings_for(den, m)
        fitted = model.fit(r, r["temperatures"], m)
        scored = model.score(r, fitted, m)
        lm = json.loads(json.dumps(model.light_model(fitted, T.FIXTURES, m, session_id="verify-1",
                                                     readings_sha256="0" * 64, meter_norm_sha256="1" * 64,
                                                     score_error=scored["error"])))
        Path(out_path).write_text(json.dumps(lm), encoding="utf-8", newline="\n")
        p = predict.predict({"den_front_left.white": {"level": 0.8, "cct_k": 2700}}, lm)
        return lm, p
    finally:
        sys.path.remove(str(light))
        for n in ("model", "predict", "synthetic", "test_stage1", "guard", "meter", "probe", "sweep"):
            sys.modules.pop(n, None)


if __name__ == "__main__":
    lm, p = build(sys.argv[1], sys.argv[2])
    print("RESULT light-model.json top-level keys:", sorted(lm))
    print("RESULT basis keys:", sorted(lm["basis"])[:4], "... dimming keys:", sorted(lm["dimming"]))
    print("RESULT channels:", [(c.get("channel_key"), c.get("entity_id")) for c in lm["channels"]])
    s = p["surfaces"]
    print("RESULT Stage 1 predict({'den_front_left.white': {level 0.8, cct_k 2700}}) ->", len(s) - 1,
          "points, max", max(v for k, v in s.items() if k != "unit" and v is not None), "unit", s["unit"])

#!/usr/bin/env python3
"""probe_verify_seam: rung 10 (den-verify) cannot read Stage 1's light-model.json through its adapter.

What it checks: Stage 1's light_model() keys `basis` by "<channel_key>@<kelvin>K" (not by channel, as the
live-v1 LightModel example `basis: {"F1.spot": {...}}` does) and stores `dimming[key]` as
{levels, g, shared_with, ...}. rung 10's tools/den-verify/light_model.py takes the channels to be
set(data["basis"]) and its backend (fake_model.py) expects basis[bulb][kelvin][point] and dimming as a
table or {"same_as": bulb}. Its own comment says to swap only _backend_predict for Stage 1's predict()
"and keep its argument shape: {bulb: {"u", "k"}}", but Stage 1's predict takes {channel_key: {level, cct_k}}.

How to run (synthetic fit in memory, no I/O beyond imports):
    python3 -B probe_verify_seam.py <root of the claude/rung-8-stage1 checkout> [<root of claude/rung-10-verify>]
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
VERIFY = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT.parent / "rung-10-verify"
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import model  # noqa: E402
import predict  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    m = T.method1()
    den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
    r = T.readings_for(den, m)
    fitted = model.fit(r, r["temperatures"], m)
    scored = model.score(r, fitted, m)
    lm = json.loads(json.dumps(model.light_model(fitted, T.FIXTURES, m, session_id="s1", readings_sha256="0" * 64,
                                                 meter_norm_sha256="1" * 64, score_error=scored["error"])))
    channels = [c["channel_key"] for c in lm["channels"]]
    print("RESULT light-model.json channels:", channels)
    print("RESULT light-model.json basis keys (first 3):", sorted(lm["basis"])[:3])
    print("RESULT basis keys that are channel keys:", sorted(set(lm["basis"]) & set(channels)))
    state = {channels[0]: {"level": 0.5, "cct_k": 3000}}
    print("RESULT Stage 1 predict() on", state, "-> point r00c00 =",
          predict.predict(state, lm)["surfaces"]["r00c00"])
    sys.path.insert(0, str(VERIFY / "tools" / "den-verify"))
    lmod = load("den_verify_light_model", VERIFY / "tools" / "den-verify" / "light_model.py")
    adapter = lmod.LightModel(lm)
    print("RESULT rung 10 adapter channels (from basis keys, first 3):", sorted(adapter.channels)[:3])
    failures = []
    try:
        adapter.predict(state)
    except Exception as exc:  # the adapter as built, with its current backend
        failures.append(f"adapter.predict: {type(exc).__name__}: {exc}")
    # the documented swap: _backend_predict calls Stage 1's predict with the adapter's {bulb: {u, k}} shape
    lmod._backend_predict = lambda data, scene: predict.predict(scene, data)["surfaces"]
    try:
        adapter.predict(state)
    except Exception as exc:
        failures.append(f"adapter.predict after swapping _backend_predict: {type(exc).__name__}: {exc}")
    for f in failures:
        print("RESULT", f)
    if failures:
        print("FAIL: rung 10's adapter cannot use Stage 1's light-model.json or predict() as documented; "
              "the basis keys are not channel keys")
    else:
        print("PASS")


if __name__ == "__main__":
    main()

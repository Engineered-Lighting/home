"""Probe (attack lens): den-verify cannot read the light-model.json that rung 8 Stage 1 actually writes.

What it checks
  den-verify predicts only through its own fake_model.py, whose file shape is
  basis[bulb][kelvin][point] and dimming[bulb] = [[u, g], ...]. Rung 8 Stage 1
  (tools/den-light/model.py light_model(), predict.py) writes channels[] with
  channel_key "<fixture_id>.<channel_id>" (e.g. den_front_left.white),
  basis["<channel_key>@<K>K"] = {point_id: value}, cct[channel_key] =
  {kelvin, basis_keys}, dimming[channel_key] = {levels, g}, and sample points
  that are image blocks (r00c00 ...) with no surface map.

  1. A model in Stage 1's shape is built and Stage 1's own predict() (imported
     from the rung-8-stage1 checkout) evaluates it.
  2. den-verify's LightModel loads the same dict without complaint, but every
     prediction raises KeyError ("has no basis for ..."), whether the Decision
     names channels by HA entity id or by Stage 1 channel key.
  3. A full run on scored values over one decision ends with every decision
     not verified and verdict "insufficient".
  4. light_model.py says to swap in Stage 1's predict() "and keep its argument
     shape: {bulb: {"u", "k"}}": Stage 1 predict() given that shape either
     raises (entity ids) or silently treats every channel as off (channel keys).

Run
  python3 probe_atk-stage1-model-unreadable.py <den-verify checkout root> <rung-8-stage1 checkout root>
Synthetic data only; no network; writes nothing (no __pycache__ in either checkout).
"""
import importlib.util
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
STAGE1 = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

spec = importlib.util.spec_from_file_location("stage1_predict", STAGE1 / "tools/den-light/predict.py")
stage1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage1)

HUE = syn.HUE
KEYS = {e: e.split(".", 1)[1] + ".white" for e in HUE}
POINTS = ["r00c00", "r00c01"]
TEMPS = [2202, 4000, 6500]
model = {"record": "light_model", "contract": "live-v1", "version": "lm-den-s1", "room_version": "den-oak-view/x",
         "sample_points": [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": [0, 0, 40, 40],
                            "excluded": None} for p in POINTS],
         "channels": [{"channel_key": KEYS[e], "fixture_id": KEYS[e].split(".")[0], "channel_id": "white",
                       "entity_id": e, "kind": "tunable_white", "cct_range_k": [2202, 6500]} for e in HUE],
         "basis": {f"{KEYS[e]}@{k}K": {p: 100.0 + i for i, p in enumerate(POINTS)} for e in HUE for k in TEMPS},
         "basis_unit": "relative", "spot_model": None,
         "dimming": {KEYS[e]: {"levels": [0, 127, 254], "g": [0.0, 0.5, 1.0], "shared_with": None} for e in HUE},
         "cct": {KEYS[e]: {"kelvin": TEMPS, "basis_keys": [f"{KEYS[e]}@{k}K" for k in TEMPS], "entity_id": e}
                 for e in HUE},
         "bounce": "measured", "glare": None, "error": {"median_pct": None, "p90_pct": None}}

out = stage1.predict({KEYS[HUE[0]]: {"level": 0.5, "cct_k": 2700}}, model)
print(f"RESULT stage1 predict(): {out['surfaces']}")
ok_stage1 = abs(out["surfaces"]["r00c00"] - 50.0) < 1e-9

lm = LightModel(model)
errors = []
for label, key in (("entity id", HUE[0]), ("stage1 channel key", KEYS[HUE[0]])):
    try:
        lm.predict({key: {"level": 0.5, "cct_k": 2700}})
        errors.append(None)
        print(f"RESULT den-verify predict by {label}: no error")
    except Exception as e:  # noqa: BLE001
        errors.append(e)
        print(f"RESULT den-verify predict by {label}: {type(e).__name__}: {e}")

M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
t = syn.T0
ref = syn.light_state(t, {p: 100.0 for p in POINTS}, anchored=False, ident="ls-ref")
after = syn.light_state(t + 5, {p: 150.0 for p in POINTS}, anchored=False, ident="ls-after")
dec = syn.hue_decision("d-1", t + 0.5, {HUE[0]: 0.2}, {HUE[0]: {"level": 0.7, "steps": [
    {"kind": "send", "sent_at": t + 0.6}]}}, "ls-ref", model="lm-den-s1",
    targets={"A": {"surface_id": "r00c00"}})
decisions, _, rep = Run(M, lm, [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
print(f"RESULT full run: verified {rep['decisions']['verified']} of {rep['decisions']['total']}; "
      f"verify_status={decisions[0].get('verify_status')!r}; verdict={rep['hit_rate']['verdict']}")

for label, scene in (("entity id {u,k}", {HUE[0]: {"u": 127, "k": 2700}}),
                     ("channel key {u,k}", {KEYS[HUE[0]]: {"u": 127, "k": 2700}})):
    try:
        r = stage1.predict(scene, model)
        print(f"RESULT stage1 predict() with the adapter's documented {label}: {r['surfaces']}")
    except Exception as e:  # noqa: BLE001
        print(f"RESULT stage1 predict() with the adapter's documented {label}: {type(e).__name__}: {e}")

bad = ok_stage1 and all(isinstance(e, KeyError) for e in errors) and rep["hit_rate"]["verdict"] == "insufficient"
print("FAIL: den-verify cannot score any decision against a Stage 1 light-model.json"
      if bad else "PASS: den-verify reads Stage 1's light model")

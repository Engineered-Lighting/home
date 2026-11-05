"""Verifier probe (reproduce lens) for finding incumbent-decisions-unscoreable, decide.py half.

What it checks
  The attack probe drove den-core-live's incumbent.py. This drives rung 9's own
  coordinator v0 (den-core-live tools/den-core/decide.py, make_policy() with its
  default fake light model) offline with synthetic events, prints the shape of
  the Decision it writes (applied_scene / readback / steps / actuator_state /
  inputs.light_state), and feeds it to den-verify's Run on closed proposed
  values with synthetic frames:
    1. a Decision made before any bulb was observed (actuator_state all null);
    2. a Decision made after every bulb was observed.

Run
  python3 -B verify_incumbent-decisions-unscoreable.py <den-verify checkout root> <den-core-live checkout root>
Synthetic data only; nothing contacts Home Assistant or any host; temp dirs only.
"""
import sys
import traceback

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
CORE = Path(sys.argv[2]).resolve()
# den-core-live and den-verify both have light_model.py and synthetic.py: import decide first, then
# drop den-core's same-named modules before importing den-verify's.
sys.path.insert(0, str(CORE / "tools" / "den-core"))
import decide  # noqa: E402  (den-core-live)
for name in ("light_model", "synthetic", "events"):
    sys.modules.pop(name, None)
sys.path.pop(0)
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
HUE = list(decide.LIGHTS)
T0 = syn.T0


def drive(observe):
    p = decide.make_policy()
    evs = [{"t": T0, "kind": "mode", "data": {"policy": "den_lighting@1.0.4", "revision": 1, "gaming": False,
                                              "occupancy": "off", "holds": [],
                                              "normal": {b: {"on": True, "brightness": 200, "kelvin": 2700}
                                                         for b in HUE}}}]
    for i, b in enumerate(observe):
        evs.append({"t": T0 + 0.1 * (i + 1), "kind": "light_observed",
                    "data": {"entity_id": b, "on": True, "brightness": 120, "kelvin": 2700}})
    evs.append({"t": T0 + 1, "kind": "occupancy", "data": {"value": "on"}})
    out = []
    for e in evs:
        out += [o["data"] for o in p.handle(e) if o["kind"] == "decision"]
    return out


for label, obs in (("none observed", []), ("all observed", HUE)):
    ds = drive(obs)
    print(f"{label}: decide.py emitted {len(ds)} decision(s)")
    if not ds:
        continue
    d = ds[-1]
    b = HUE[0]
    print(f"  policy={d['policy']} applied_scene[{b}]={d['applied_scene'].get(b)}")
    print(f"  actuator_state[{b}]={d['inputs']['actuator_state'].get(b)}; inputs.light_state="
          f"{d['inputs'].get('light_state')!r}; predicted.surfaces keys={sorted(d['predicted']['surfaces'])[:4]}")
    pts = [k for k in d["predicted"]["surfaces"] if k != "unit"] or ["desk-1"]
    model = syn.hue_model({p: {e: 100.0 for e in HUE} for p in pts})
    frames = [syn.light_state(d["ts"] - 1, {p: 300.0 for p in pts}, anchored=False),
              syn.light_state(d["ts"] + 1, {p: 150.0 for p in pts}, anchored=False)]
    try:
        decs, _, rep = Run(M, LightModel(model), ds, frames, clock=Clock(syn.UTC_OFFSET_S)).run()
        v = decs[-1]["verify"]
        print(f"RESULT {label}: verify={'null: ' + str(decs[-1].get('verify_status')) if v is None else ''}"
              f"{'' if v is None else [(p['point_id'], p['status']) for p in v['points']]}; "
              f"verdict={rep['hit_rate']['verdict']}")
    except Exception as e:  # noqa: BLE001
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print(f"RESULT {label}: den-verify crashed: {type(e).__name__}: {e} at {Path(tb.filename).name}:{tb.lineno}")

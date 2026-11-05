"""Probe: a held/superseded share >= held_share_min drops a point's MISSES but keeps its HITS.

What it checks
  den-verify (rung 10) excludes an `outside` point from misses when held or
  superseded channels give >= held_share_min (proposed 0.05) of its predicted
  light, but a `within` point with the same held share still counts as a hit
  (score.py evaluate(): excluded_held only when status == "outside"; run():
  counted rows that are within always add a hit). With four ceiling bulbs every
  bulb lights every surface, so one held bulb puts every point over 5%, and
  then only hits can be counted for that decision.

  Case 1: one decision, 20 points, a held bulb gives ~12% at each point,
          3 points land within and 17 land 40% low. True rate 3/20 = 15%.
  Case 2: the same with the bulb NOT held (control): 3/20 -> fail.
  Case 3: a realistic mix: two free decisions at 16/20 (80%, fail on their
          own) plus two decisions under a hold at 16/20. Excluding held points
          from both hits and misses gives 32/40 = 80% fail; the tool reports
          64/72 = 88.9% pass.

Run
  python3 probe_held-hits-kept-misses-dropped.py <den-verify branch checkout root>
Synthetic data only; no network; writes nothing (no __pycache__ either).
"""
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0, B1, B2, B3 = syn.HUE
M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
N = 20
NAMES = [f"p{i:02d}" for i in range(N)]
MODEL = syn.hue_model({n: {B0: 400.0, B1: 150.0} for n in NAMES})
LM = LightModel(MODEL)


def decision_case(i, t, held, n_within):
    before = {B0: {"level": 0.2, "cct_k": 2700}, B1: {"level": 0.3, "cct_k": 2700}}
    after_state = {B0: {"level": 0.8, "cct_k": 2700}, B1: {"level": 0.3, "cct_k": 2700}}
    pb, pa = LM.predict(before), LM.predict(after_state)
    ref = syn.light_state(t, {n: pb[n] for n in NAMES}, anchored=False, ident=f"ls-ref-{i}")
    applied = {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]},
               B1: {"status": "held" if held else "sent", "level": 0.3}}
    hold_set = ({"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                          "pinned": {"level_pct": 30, "cct_k": 2700, "source": "read-back"}}]}
                if held else None)
    dec = syn.hue_decision(f"d-{i}", t + 0.5, {B0: 0.2, B1: 0.3}, applied, f"ls-ref-{i}",
                           targets={f"T{j}": {"surface_id": n} for j, n in enumerate(NAMES)}, hold_set=hold_set)
    vals = {n: (pa[n] if j < n_within else pa[n] * 0.6) for j, n in enumerate(NAMES)}
    after = syn.light_state(t + 5, vals, anchored=False, ident=f"ls-after-{i}")
    share = 0.3 * 150 / (0.8 * 400 + 0.3 * 150)
    return dec, [ref, after], share


def run(cases):
    decs, frames = [], []
    for d, f, _ in cases:
        decs.append(d)
        frames += f
    _, _, rep = Run(M, LM, decs, frames, clock=Clock(syn.UTC_OFFSET_S)).run()
    return rep


def show(label, rep, truth):
    hr = rep["hit_rate"]
    print(f"RESULT {label}: tool hits={hr['hits']} misses={hr['misses']} value={hr['value']} "
          f"verdict={hr['verdict']} excluded_held={rep['excluded_held_or_superseded']} | {truth}")
    return hr


t0 = syn.T0
c1 = decision_case(1, t0, held=True, n_within=3)
print(f"held bulb share of predicted light at each point: {c1[2]:.3f} (held_share_min {M['held_share_min']})")
hr1 = show("case1 held, 3 of 20 within", run([c1]), "true rate 3/20 = 0.15 -> fail")
hr2 = show("case2 control not held, 3 of 20 within", run([decision_case(2, t0, held=False, n_within=3)]),
           "true rate 0.15 -> fail")
mix = [decision_case(10, t0, False, 16), decision_case(11, t0 + 100, False, 16),
       decision_case(12, t0 + 200, True, 16), decision_case(13, t0 + 300, True, 16)]
hr3 = show("case3 mix", run(mix), "excluding held points from hits AND misses: 32/40 = 0.80 -> fail")

bad = hr1["verdict"] == "pass" and hr2["verdict"] == "fail" and hr3["verdict"] == "pass"
print("FAIL: held-share exclusion drops misses but keeps hits; a 15% decision reads 100% pass, "
      "and a mix that is 80% on unheld points passes" if bad else "PASS: no asymmetric held exclusion")

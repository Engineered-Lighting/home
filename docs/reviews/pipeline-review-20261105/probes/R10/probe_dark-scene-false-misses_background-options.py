#!/usr/bin/env python3
"""probe_dark-scene-false-misses_background-options: verifier's cross-check of dark-scene-false-misses.

What it checks (synthetic only, no network, no files written): the same three ON
scenes, one vacancy OFF and one dim-down 0.8 -> 0.1 as
probe_dark-scene-false-misses.py, with a light model that is 5% off per point
(x1.05 / x0.95 at every state), scored three ways:
  1. background "zero" (the option VERIFY-V1.md gives for runs after dark), no daylight;
  2. background "reference_frame" (the proposal), no daylight;
  3. background "reference_frame", a steady twilight daylight of 50 relative units per point.
Prints RESULT lines with each verdict, and PASS/FAIL for whether dark scenes flip it.

Run:
    python3 probe_dark-scene-false-misses_background-options.py <root of the rung-10-verify checkout>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0, B1, B2, B3 = syn.HUE
TEXT = method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md")
names = [f"p{i}" for i in range(6)]
lm = LightModel(syn.hue_model({n: {B0: 300.0, B1: 100.0} for n in names}, kelvins=(2700,)))
err = {n: (1.05 if i % 2 == 0 else 0.95) for i, n in enumerate(names)}
targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(names)}


def case(did, t, before, after_cmd, landed, daylight):
    truth = lambda lv: {n: daylight + err[n] * (300 * lv.get(B0, 0) + 100 * lv.get(B1, 0)) for n in names}
    ref = syn.light_state(t, truth(before), anchored=False, ident=f"ls-{did}")
    after_vals = {n: max(v, 0.5) for n, v in truth(landed).items()}
    applied = {b: {"level": lv, "steps": [{"kind": "send", "sent_at": t + 0.5}]} for b, lv in after_cmd.items()}
    dec = syn.hue_decision(did, t + 0.5, before, applied, f"ls-{did}", targets=targets)
    return [dec], [ref, syn.light_state(t + 5, after_vals, anchored=False)]


def run(background, daylight, dark):
    M = method.parse(method.closed_copy(TEXT, {"background": background})).scored()
    t = syn.T0
    parts = [case(f"d-on{i}", t + 100 * i, {B0: 0.3, B1: 0.5}, {B0: 0.8}, {B0: 0.8, B1: 0.5}, daylight)
             for i in range(3)]
    if dark:
        parts.append(case("d-off", t + 400, {B0: 0.8, B1: 0.5}, {B0: 0.0, B1: 0.0}, {B0: 0.0, B1: 0.0}, daylight))
        parts.append(case("d-dim", t + 500, {B0: 0.8, B1: 0.5}, {B0: 0.1, B1: 0.1}, {B0: 0.1, B1: 0.1}, daylight))
    d = [x for p in parts for x in p[0]]
    f = [x for p in parts for x in p[1]]
    decs, _, rep = Run(M, lm, d, f, clock=Clock(syn.UTC_OFFSET_S)).run()
    pts = {dd["decision_id"]: [(p["point_id"], p["status"], p["residual"]) for p in dd["verify"]["points"]]
           for dd in decs if dd["decision_id"] in ("d-off", "d-dim")}
    return rep["hit_rate"], pts


for label, bg, dl in (("1 zero, dark", "zero", 0.0), ("2 reference_frame, dark", "reference_frame", 0.0),
                      ("3 reference_frame, twilight 50", "reference_frame", 50.0)):
    hr, pts = run(bg, dl, True)
    hr0, _ = run(bg, dl, False)
    print(f"RESULT {label}: with dark scenes hits {hr['hits']} misses {hr['misses']} -> {hr['verdict']}; "
          f"without: hits {hr0['hits']} misses {hr0['misses']} -> {hr0['verdict']}")
    for k, v in pts.items():
        print(f"RESULT {label}: {k} {v}")
    print(("FAIL " if hr["verdict"] != hr0["verdict"] else "PASS ") + f"{label}: dark scenes "
          + ("flip" if hr["verdict"] != hr0["verdict"] else "do not flip") + " the verdict")

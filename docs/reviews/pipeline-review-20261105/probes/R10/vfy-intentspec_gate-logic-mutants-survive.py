"""Verifier probe: are the five surviving den-verify mutants observable (non-equivalent)?

What it checks
  The finding gate-logic-mutants-survive says five mutants of score.py pass the
  whole suite. A surviving mutant only matters if it is not equivalent. For each
  mutant this script copies tools/den-verify and docs/architecture/live-v1 from
  the checkout into a temp dir, applies the one exact-text mutation, and runs a
  small synthetic scenario built to tell the original from the mutant:

    vacuous-pass              no scored point (only point missing from the frame)
    held-symmetric-exclusion  a point that is within, lit only by a held bulb
    drift-ignores-occupancy   an outside point whose increment also misses, on an
                              occupied frame, nobody near the point
    v_min-ignored             a point with clipped_frac 0.6 (usable 0.4 < v_min 0.5)
    no-person-change-window   the first settled frame falls inside a person's change

  It prints the original and mutant outcome for each and DIFFERS / SAME.

Run
  python3 verify_gate-logic-mutants-survive.py <den-verify branch checkout root>
Synthetic data only; no network; temp dirs only; the checkout is never modified.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()

MUTANTS = {
    "vacuous-pass": ('"verdict": ("insufficient" if not n else', '"verdict": ("pass" if not n else'),
    "held-symmetric-exclusion": (
        'row["excluded_held"] = row["status"] == "outside" and row["held_share"] >= m["held_share_min"]',
        'row["excluded_held"] = row["status"] in ("within", "outside") and row["held_share"] >= m["held_share_min"]'),
    "drift-ignores-occupancy": ('if (inc["status"] == "outside" and not occ', 'if (inc["status"] == "outside"'),
    "v_min-ignored": ('if 1 - float(e.get("clipped_frac") or 0) < m["v_min"]:', "if False:"),
    "no-person-change-window": ('self.windows.add_change(ts, (c.get("action") or {}).get("settle_s"),',
                                '(lambda *a: None)(ts, (c.get("action") or {}).get("settle_s"),'),
}

SCENARIO = r'''
import json, sys
sys.dont_write_bytecode = True
import method, synthetic as syn
from light_model import LightModel
from records import Clock
from score import Run
from pathlib import Path
B0, B1 = syn.HUE[0], syn.HUE[1]
m = method.parse(method.closed_copy(method.lf_text(Path("VERIFY-V1.md")))).scored()
def run(decs, frames, model, **kw):
    return Run(m, LightModel(model), decs, frames, clock=Clock(syn.UTC_OFFSET_S), **kw).run()
t = syn.T0
st = lambda b, lv: {b: {"level": lv, "cct_k": 2700}}
which = sys.argv[1]
if which == "vacuous-pass":
    model = syn.hue_model({"desk-1": {B0: 400.0}})
    ref = syn.light_state(t, {"desk-1": 80.0}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"other": 1.0}, anchored=False)  # desk-1 not in the frame
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                           "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    _, _, rep = run([dec], [ref, after], model)
    print(json.dumps({"verdict": rep["hit_rate"]["verdict"], "n": rep["hit_rate"]["hits"] + rep["hit_rate"]["misses"]}))
elif which == "held-symmetric-exclusion":
    model = syn.hue_model({"sofa-1": {B1: 400.0}})
    lm = LightModel(model)
    v = lm.predict(st(B1, 0.3))["sofa-1"]
    ref = syn.light_state(t, {"sofa-1": v}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"sofa-1": v}, anchored=False)  # exactly where predicted: within
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.2, B1: 0.3}, {
        B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]},
        B1: {"status": "held", "level": 0.3}}, "ls-ref", targets={"B": {"surface_id": "sofa-1"}})
    _, _, rep = run([dec], [ref, after], model)
    hr = rep["hit_rate"]
    print(json.dumps({"hits": hr["hits"], "misses": hr["misses"], "verdict": hr["verdict"],
                      "excluded_held": rep["excluded_held_or_superseded"]}))
elif which == "drift-ignores-occupancy":
    model = syn.hue_model({"desk-1": {B0: 400.0}})
    lm = LightModel(model)
    vb, va = lm.predict(st(B0, 0.2))["desk-1"], lm.predict(st(B0, 0.8))["desk-1"]
    ref = syn.light_state(t, {"desk-1": vb}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"desk-1": va * 1.4}, anchored=False)  # 40% high, increment misses high too
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                           "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    occ = [{"start": t + 4, "end": t + 6, "people": [{"track_id": "A", "xy": [9.0, 9.0]}]}]
    d, _, rep = run([dec], [ref, after], model, occupancy=occ)
    print(json.dumps({"cause": d[0]["verify"]["explanation"][0]["cause"], "drift": rep["misses_explained"]["drift"]}))
elif which == "v_min-ignored":
    model = syn.hue_model({"desk-1": {B0: 400.0}})
    lm = LightModel(model)
    vb, va = lm.predict(st(B0, 0.2))["desk-1"], lm.predict(st(B0, 0.8))["desk-1"]
    ref = syn.light_state(t, {"desk-1": vb}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"desk-1": va * 0.5}, anchored=False, clipped={"desk-1": 0.6})
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                           "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    d, _, rep = run([dec], [ref, after], model)
    print(json.dumps({"status": d[0]["verify"]["points"][0]["status"], "verdict": rep["hit_rate"]["verdict"]}))
elif which == "no-person-change-window":
    model = syn.hue_model({"desk-1": {B0: 400.0}})
    lm = LightModel(model)
    vb, va = lm.predict(st(B0, 0.2))["desk-1"], lm.predict(st(B0, 0.8))["desk-1"]
    ref = syn.light_state(t, {"desk-1": vb}, anchored=False, ident="ls-ref")
    mid = syn.light_state(t + 4, {"desk-1": va * 0.5}, anchored=False)    # inside A's change
    late = syn.light_state(t + 8, {"desk-1": va}, anchored=False)        # settled, on target
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                           "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    corr = {"correction_id": "c-1", "ts": t + 5, "action": {"settle_s": 2}, "snapshot": {"light_state": ["ls-ref"]}}
    d, _, rep = run([dec], [ref, mid, late], model, corrections=[corr])
    v = d[0]["verify"]
    print(json.dumps({"verify_frame_ts_minus_t0": round(v["ts"] - t, 2), "status": v["points"][0]["status"],
                      "verdict": rep["hit_rate"]["verdict"]}))
'''


def copy_tree(dst):
    shutil.copytree(ROOT / "tools" / "den-verify", dst / "tools" / "den-verify",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "docs" / "architecture" / "live-v1", dst / "docs" / "architecture" / "live-v1")


def outcome(tree, which):
    work = tree / "tools" / "den-verify"
    (work / "_scenario.py").write_text(SCENARIO, encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", "_scenario.py", which], cwd=work, capture_output=True, text=True,
                       timeout=120)
    return (p.stdout.strip() or p.stderr.strip().splitlines()[-1])


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    base = tmp / "base"
    copy_tree(base)
    differ = 0
    for name, (old, new) in MUTANTS.items():
        work = tmp / name
        shutil.copytree(base, work)
        f = work / "tools" / "den-verify" / "score.py"
        text = f.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"RESULT {name}: pattern matched {text.count(old)} times, not run")
            continue
        f.write_text(text.replace(old, new), encoding="utf-8")
        o, mu = outcome(base, name), outcome(work, name)
        same = o == mu
        differ += not same
        print(f"RESULT {name}: original {o} | mutant {mu} -> {'SAME' if same else 'DIFFERS'}")
    print(f"RESULT {differ} of {len(MUTANTS)} surviving mutants are observable on a synthetic case")
    print("PASS: every surviving mutant changes an output (non-equivalent)" if differ == len(MUTANTS)
          else "FAIL: some surviving mutants look equivalent")

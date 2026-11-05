#!/usr/bin/env python3
"""probe_held-exclusion-hides-free-misses: does the held/superseded exclusion hide a FREE bulb's failure?

What it checks (synthetic only, temp dirs, no network): den-verify excludes an
`outside` point from misses when held or superseded channels give at least
`held_share_min` (proposed 0.05) of its predicted light, but keeps a `within`
held point as a hit. Two decisions on the den's four Hue bulbs, same evening:

  d-free: no hold; front_left 0.2 -> 0.8; all 20 points land within.
  d-hold: front_right is held at 30% (hold_set pinned); front_left 0.2 -> 0.8
          is commanded but the bulb lands at 0.4 (a free-bulb delivery fault).
          front_right gives ~8.6% of every point's predicted light.

Truth by hand: front_left missed at all 20 points of d-hold, so 20 of 40 points
hit (50%) and the >= 85% gate fails. The miss is the free bulb's, not the held
one's. The probe runs the tool's scorer with every open slot closed at its
proposed value (as the owner would on accepting the proposals) and prints the
verdict, then the CLI line the owner sees.

Run:
    python3 probe_held-exclusion-hides-free-misses.py <root of the rung-10-verify checkout>
"""
import io
import contextlib
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock, write_jsonl  # noqa: E402
from score import Run  # noqa: E402

B0, B1, B2, B3 = syn.HUE
TEXT = method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md")
M = method.parse(method.closed_copy(TEXT)).scored()   # every open slot at its proposal

names = [f"p{i:02d}" for i in range(20)]
# every point: front_left 400 at full, front_right 100 at full (single 2700 K capture: no mired factor)
model = syn.hue_model({n: {B0: 400.0, B1: 100.0} for n in names}, kelvins=(2700,))
lm = LightModel(model)
t = syn.T0


def frame(ts, values, ident=None):
    return syn.light_state(ts, values, anchored=False, ident=ident)


before = {n: 400 * 0.2 + 100 * 0.3 for n in names}          # 110 everywhere
pred_after = {n: 400 * 0.8 + 100 * 0.3 for n in names}      # 350 everywhere
landed_half = {n: 400 * 0.4 + 100 * 0.3 for n in names}     # 190: front_left landed at 0.4

ref1 = frame(t, before, "ls-ref1")
d_free = syn.hue_decision("d-free", t + 0.5, {B0: 0.2, B1: 0.3}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                          "ls-ref1", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(names)})
after1 = frame(t + 5, pred_after)

t2 = t + 600
ref2 = frame(t2, before, "ls-ref2")
d_hold = syn.hue_decision("d-hold", t2 + 0.5, {B0: 0.2, B1: 0.3},
                          {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t2 + 0.5}]},
                           B1: {"status": "held", "level": 0.3}},
                          "ls-ref2", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(names)},
                          hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                                             "pinned": {"level_pct": 30, "cct_k": 2700,
                                                                        "source": "read-back"}}]})
after2 = frame(t2 + 5, landed_half)

decisions = [d_free, d_hold]
frames = [ref1, after1, ref2, after2]

# hand truth
held_share = 100 * 0.3 / 350
hand_hits = 20 + 0
hand_n = 40
print(f"RESULT hand: held share at every d-hold point = {held_share:.4f} (>= 0.05)")
print(f"RESULT hand: d-hold residual at every point = {(190 - 350) / 350:+.4f} (outside +/-15%)")
print(f"RESULT hand: hit rate {hand_hits}/{hand_n} = {hand_hits / hand_n:.3f} -> fail (gate 0.85)")

run = Run(M, lm, decisions, frames, clock=Clock(syn.UTC_OFFSET_S))
decs_out, states_out, report = run.run()
hr = report["hit_rate"]
print(f"RESULT tool: hits {hr['hits']} misses {hr['misses']} value {hr['value']} verdict {hr['verdict']}; "
      f"excluded_held_or_superseded {report['excluded_held_or_superseded']}")
dh = next(d for d in decs_out if d["decision_id"] == "d-hold")
causes = sorted({e["cause"] for e in dh["verify"]["explanation"]})
print(f"RESULT tool: d-hold point statuses {sorted({p['status'] for p in dh['verify']['points']})}, causes {causes}")

# the symmetric counterfactual: same d-hold, but front_left lands right -> those points count as HITS
after2_ok = frame(t2 + 5, pred_after)
r2 = Run(M, lm, decisions, [ref1, after1, ref2, after2_ok], clock=Clock(syn.UTC_OFFSET_S)).run()[2]["hit_rate"]
print(f"RESULT tool (d-hold lands right): hits {r2['hits']} misses {r2['misses']} -> held-point hits are counted, "
      f"held-point misses are not")

# the line the owner sees
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    write_jsonl(tmp / "d.jsonl", decisions)
    write_jsonl(tmp / "l.jsonl", frames)
    (tmp / "m.json").write_text(json.dumps(model), encoding="utf-8")
    (tmp / "VERIFY-V1.md").write_text(method.closed_copy(TEXT), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--method", str(tmp / "VERIFY-V1.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                         "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"),
                         "--utc-offset-h", "-7", "--out", str(tmp / "out")])
    print("RESULT owner sees: " + buf.getvalue().strip().splitlines()[0])

if hr["verdict"] == "pass" and hand_hits / hand_n < 0.85:
    print("FAIL a free bulb's 20 misses were excluded as 'held'; the gate passes at "
          f"{hr['value']} where the hand count is {hand_hits / hand_n:.2f}")
else:
    print("PASS the free bulb's misses were counted")

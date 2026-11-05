#!/usr/bin/env python3
"""verify_held-exclusion-hides-free-misses: independent re-check (skeptical verifier, R10).

What it checks (synthetic only, no network, no files outside a temp dir): with
every open slot of VERIFY-V1.md closed at its proposal, does an `outside` point
get dropped from misses merely because a held bulb gives >= held_share_min of
its predicted light, even when the held bulb cannot possibly explain the miss?

Scenario differs from the reporter's: the FREE bulb (front_left) is commanded
0 -> 1.0 and the camera shows it never came on at all (measured = held bulb's
light only). The held bulb (front_right, pinned 20%) gives only ~5.6% of the
predicted light. Hand truth: 10 hits (clean decision) + 10 misses = 0.50, fail.
Also checks: the same decision with no hold -> those 10 points are misses.

Run:
    python3 verify_held-exclusion-hides-free-misses.py <root of rung-10-verify checkout>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
sys.dont_write_bytecode = True
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0, B1, B2, B3 = syn.HUE
TEXT = method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md")
M = method.parse(method.closed_copy(TEXT)).scored()
print(f"INFO held_share_min (proposal) = {M['held_share_min']}")

names = [f"q{i}" for i in range(10)]
model = syn.hue_model({n: {B0: 340.0, B1: 100.0} for n in names}, kelvins=(2700,))
lm = LightModel(model)
t = syn.T0
tg = {f"T{i}": {"surface_id": n} for i, n in enumerate(names)}


def fr(ts, v, ident=None):
    return syn.light_state(ts, {n: v for n in names}, anchored=False, ident=ident)


# decision 1: clean, lands right
d1 = syn.hue_decision("d1", t + 0.5, {B0: 0.0, B1: 0.2},
                      {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-r1", targets=tg)
# decision 2: front_right held at 20%; front_left commanded on but stays dark
t2 = t + 900
hold = {"revision": 3, "held": [{"entity_id": B1, "channels": [B1],
                                 "pinned": {"level_pct": 20, "cct_k": 2700, "source": "read-back"}}]}
d2 = syn.hue_decision("d2", t2 + 0.5, {B0: 0.0, B1: 0.2},
                      {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t2 + 0.5}]},
                       B1: {"status": "held", "level": 0.2}}, "ls-r2", targets=tg, hold_set=hold)
d2_nohold = syn.hue_decision("d2", t2 + 0.5, {B0: 0.0, B1: 0.2},
                             {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t2 + 0.5}]}},
                             "ls-r2", targets=tg)
frames = [fr(t, 20.0, "ls-r1"), fr(t + 6, 360.0), fr(t2, 20.0, "ls-r2"), fr(t2 + 6, 20.0)]

print(f"INFO held share = {20.0 / 360.0:.4f}; d2 residual = {(20 - 360) / 360:+.4f}; "
      f"held bulb alone could move the prediction by at most {20 / 360:.1%}")
for label, decs in (("with hold", [d1, d2]), ("no hold", [d1, d2_nohold])):
    run = Run(M, lm, decs, frames, clock=Clock(syn.UTC_OFFSET_S))
    _, _, rep = run.run()
    hr = rep["hit_rate"]
    print(f"RESULT {label}: hits {hr['hits']} misses {hr['misses']} value {hr['value']} verdict {hr['verdict']} "
          f"excluded {rep['excluded_held_or_superseded']} causes "
          f"{ {k: v for k, v in rep['misses_explained'].items() if k in ('daylight','shadow','drift','unexplained')} }")
    if label == "with hold":
        with_hold = hr
    else:
        no_hold = hr
if with_hold["verdict"] == "pass" and no_hold["verdict"] == "fail":
    print("FAIL reproduced: a free bulb that never came on is hidden by a 5.6% held share; gate passes")
else:
    print("PASS not reproduced")

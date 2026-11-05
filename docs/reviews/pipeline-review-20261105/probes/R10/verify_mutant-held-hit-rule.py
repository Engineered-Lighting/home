#!/usr/bin/env python3
"""verify_mutant-held-hit-rule: is mutant M07 (held points excluded whether within or outside) equivalent?

Skeptical verifier, R10. Synthetic only, no network. One decision with
front_right held (pinned 30%) and front_left 0.2 -> 0.8 landing exactly as
predicted, at 5 points that both bulbs light (held share ~8.6%), plus one
decision with no hold at 5 points. The unmutated rule counts the held points
that land within as hits (10 hits); M07 would exclude them (5 hits). Also
runs the suite's own HeldAndSuperseded scenario shape: every held point there
is outside and the free points carry no held share, so it cannot tell.

Run:
    python3 verify_mutant-held-hit-rule.py <root of a rung-10-verify checkout or mutated copy>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0, B1 = syn.HUE[0], syn.HUE[1]
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
names = [f"p{i}" for i in range(5)]
lm = LightModel(syn.hue_model({n: {B0: 400.0, B1: 100.0} for n in names}, kelvins=(2700,)))
t = syn.T0
tg = {f"T{i}": {"surface_id": n} for i, n in enumerate(names)}
fr = lambda ts, v, ident=None: syn.light_state(ts, {n: v for n in names}, anchored=False, ident=ident)
free = syn.hue_decision("d-free", t + 0.5, {B0: 0.2, B1: 0.3},
                        {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-r1", targets=tg)
t2 = t + 600
held = syn.hue_decision("d-held", t2 + 0.5, {B0: 0.2, B1: 0.3},
                        {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t2 + 0.5}]},
                         B1: {"status": "held", "level": 0.3}}, "ls-r2", targets=tg,
                        hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                                           "pinned": {"level_pct": 30, "cct_k": 2700}}]})
frames = [fr(t, 110.0, "ls-r1"), fr(t + 5, 350.0), fr(t2, 110.0, "ls-r2"), fr(t2 + 5, 350.0)]
_, _, rep = Run(M, lm, [free, held], frames, clock=Clock(syn.UTC_OFFSET_S)).run()
hr = rep["hit_rate"]
print(f"RESULT held points landing within: hits {hr['hits']} misses {hr['misses']} "
      f"excluded {rep['excluded_held_or_superseded']} verdict {hr['verdict']}")

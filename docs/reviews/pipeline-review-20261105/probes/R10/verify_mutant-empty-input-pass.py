#!/usr/bin/env python3
"""verify_mutant-empty-input-pass: is mutant M05 (n == 0 -> "pass") equivalent? (skeptical verifier, R10)

What it checks (synthetic only, no network): the hit-rate verdict for
  (a) a run with no decisions at all,
  (b) a run whose only decision is superseded before it settled and whose
      second was sent mid-transition (the MidFade test's own scenario: 0 counted),
  (c) a run where every point is excluded as held.
Run it on a clean checkout and on a copy with M05 applied; differing output
means the mutant is not equivalent and its survival is a test gap.

Run:
    python3 verify_mutant-empty-input-pass.py <root of a rung-10-verify checkout or mutated copy>
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
lm = LightModel(syn.hue_model({"desk-1": {B0: 400.0, B1: 100.0}}, kelvins=(2700,)))
t = syn.T0
ref = syn.light_state(t, {"desk-1": 40.0}, anchored=False, ident="ls-ref")
clk = Clock(syn.UTC_OFFSET_S)

r_a = Run(M, lm, [], [ref], clock=clk).run()[2]["hit_rate"]
first = syn.hue_decision("d-1", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": [{"kind": "send", "sent_at": t + 1.0}]}},
                         "ls-ref")
second = syn.hue_decision("d-2", t + 2.0, {B0: 0.3}, {B0: {"level": 0.9, "steps": [{"kind": "send", "sent_at": t + 2.0}]}},
                          "ls-ref", targets={"A": {"surface_id": "desk-1"}})
r_b = Run(M, lm, [first, second], [ref, syn.light_state(t + 2.5, {"desk-1": 9.0}, anchored=False),
                                   syn.light_state(t + 6.0, {"desk-1": 9.0}, anchored=False)], clock=clk).run()[2]["hit_rate"]
held = syn.hue_decision("d-h", t + 0.5, {B0: 0.1, B1: 0.5},
                        {B0: {"level": 0.5, "steps": [{"kind": "send", "sent_at": t + 0.5}]}, B1: {"status": "held", "level": 0.5}},
                        "ls-ref", targets={"A": {"surface_id": "desk-1"}},
                        hold_set={"revision": 2, "held": [{"entity_id": B1, "channels": [B1],
                                                           "pinned": {"level_pct": 50, "cct_k": 2700}}]})
r_c = Run(M, lm, [held], [ref, syn.light_state(t + 6.0, {"desk-1": 5.0}, anchored=False)], clock=clk).run()[2]["hit_rate"]
for lab, r in (("a no decisions", r_a), ("b superseded + not admitted", r_b), ("c all held-excluded", r_c)):
    print(f"RESULT {lab}: hits {r['hits']} misses {r['misses']} value {r['value']} verdict {r['verdict']}")
bad = [r for r in (r_a, r_b, r_c) if r["verdict"] == "pass"]
print("FAIL an empty or all-excluded run reports pass" if bad else "PASS empty runs are insufficient")

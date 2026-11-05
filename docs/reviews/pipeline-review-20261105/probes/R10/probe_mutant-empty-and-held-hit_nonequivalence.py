#!/usr/bin/env python3
"""probe_mutant-empty-and-held-hit_nonequivalence: verifier's check that mutants M05 and M07 are not equivalent.

What it checks (synthetic only, no network, no files written):
  A. M05 (verdict for n == 0 -> "pass"): a run with no decisions, and a run whose
     only decision is all held-excluded, must give verdict "insufficient".
  B. M07 (held points excluded whether within or outside): a decision during a hold
     whose points all land within must count those points as hits (VERIFY-V1.md:80).
Run it on a clean checkout and on a mutated copy; differing RESULT lines mean the
mutant is observable, so its survival is a test gap.

Run:
    python3 probe_mutant-empty-and-held-hit_nonequivalence.py <root of a rung-10-verify checkout or mutated copy>
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

B0, B1 = syn.HUE[0], syn.HUE[1]
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
lm = LightModel(syn.hue_model({"p": {B0: 400.0, B1: 100.0}}, kelvins=(2700,)))
t = syn.T0
clock = Clock(syn.UTC_OFFSET_S)

hr = Run(M, lm, [], [syn.light_state(t, {"p": 10.0}, anchored=False)], clock=clock).run()[2]["hit_rate"]
print(f"RESULT A1 no decisions: verdict {hr['verdict']} (expected insufficient)")


def held_case(measured_after):
    ref = syn.light_state(t, {"p": 400 * 0.2 + 100 * 0.3}, anchored=False, ident="ls-ref")
    dec = syn.hue_decision("d-hold", t + 0.5, {B0: 0.2, B1: 0.3},
                           {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]},
                            B1: {"status": "held", "level": 0.3}}, "ls-ref",
                           targets={"A": {"surface_id": "p"}},
                           hold_set={"revision": 2, "held": [{"entity_id": B1, "channels": [B1],
                                                              "pinned": {"level_pct": 30, "cct_k": 2700,
                                                                         "source": "read-back"}}]})
    after = syn.light_state(t + 5, {"p": measured_after}, anchored=False)
    return Run(M, lm, [dec], [ref, after], clock=clock).run()[2]


rep = held_case(100.0)  # far outside, held share ~8.6% -> excluded
hr = rep["hit_rate"]
print(f"RESULT A2 all held-excluded: hits {hr['hits']} misses {hr['misses']} verdict {hr['verdict']} "
      f"(expected insufficient); excluded {rep['excluded_held_or_superseded']}")
rep = held_case(400 * 0.8 + 100 * 0.3)  # lands exactly
hr = rep["hit_rate"]
print(f"RESULT B held point within: hits {hr['hits']} misses {hr['misses']} verdict {hr['verdict']} "
      f"(VERIFY-V1.md:80 says 1 hit); excluded {rep['excluded_held_or_superseded']}")

#!/usr/bin/env python3
"""verify_mutant-held-share-boundary: is the held-share boundary (>= held_share_min) load-bearing?

What it checks (synthetic only, no network, nothing written outside memory):
  1. A point whose held share is exactly held_share_min (5 of 100 predicted,
     held_share_min 0.05) and that misses by 30%: the pinned code excludes it
     as 'held'; the same run with the comparison flipped to '>' (applied to an
     in-memory copy of score.py's source) counts it as a miss. So the operator
     changes the verdict at the boundary.
  2. held_share_min closed at 0 (method.py accepts any finite number): a point
     with NO held channel at all (held share 0.0) that misses by 30% satisfies
     0.0 >= 0 and is excluded as 'held'. With one hit and one such miss the
     verdict is 'pass' at 100%; with '>' it is 'fail' at 50%.
The tool's own suite has no case at held_share == held_share_min (see the
mutation run in the verifier's notes); this probe shows what that gap lets through.

Run:
    python3 verify_mutant-held-share-boundary.py <root of the rung-10-verify checkout>
"""
import sys
import types
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-verify"
sys.path.insert(0, str(tool))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import score as score_orig  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402

OLD = 'row["excluded_held"] = row["status"] == "outside" and row["held_share"] >= m["held_share_min"]'
NEW = 'row["excluded_held"] = row["status"] == "outside" and row["held_share"] > m["held_share_min"]'
src = (tool / "score.py").read_text(encoding="utf-8")
assert src.count(OLD) == 1, "pattern not found once"
mutant = types.ModuleType("score_mutant")
mutant.__file__ = str(tool / "score_mutant.py")
exec(compile(src.replace(OLD, NEW), mutant.__file__, "exec"), mutant.__dict__)

B0, B1 = syn.HUE[0], syn.HUE[1]
TEXT = method.lf_text(tool / "VERIFY-V1.md")


def closed(over=None):
    return method.parse(method.closed_copy(TEXT, over)).scored()


def run(mod, m, decisions, frames, model):
    return mod.Run(m, LightModel(model), decisions, frames, clock=Clock(syn.UTC_OFFSET_S)).run()


t = syn.T0
fails = 0

# 1. exactly at the boundary: B1 held at 100% gives 5 of the 100 predicted
model = syn.hue_model({"p": {B0: 95.0, B1: 5.0}}, kelvins=(2700,))
ref = syn.light_state(t, {"p": 95.0 * 0.5 + 5.0}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d1", t + 0.5, {B0: 0.5, B1: 1.0},
                       {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t + 0.5}]},
                        B1: {"status": "held", "level": 1.0}}, "ls-ref",
                       targets={"A": {"surface_id": "p"}},
                       hold_set={"revision": 2, "held": [{"entity_id": B1, "channels": [B1],
                                                          "pinned": {"level_pct": 100, "cct_k": 2700}}]})
after = syn.light_state(t + 5, {"p": 70.0}, anchored=False)
m = closed()
for name, mod in (("pinned >=", score_orig), ("mutant >", mutant)):
    d, s, r = run(mod, m, [dec], [ref, after], model)
    v = d[0]["verify"]
    print(f"RESULT 1 {name}: held_share_min {m['held_share_min']}, cause "
          f"{[e['cause'] for e in v['explanation'] or []]}, hits/misses {r['hit_rate']['hits']}/"
          f"{r['hit_rate']['misses']}, excluded {r['excluded_held_or_superseded']}, verdict {r['hit_rate']['verdict']}")

# 2. held_share_min = 0: a free-bulb miss with no held channel at all
model = syn.hue_model({"hit": {B0: 100.0}, "miss": {B2: 100.0} if False else {syn.HUE[2]: 100.0}}, kelvins=(2700,))
B2 = syn.HUE[2]
ref = syn.light_state(t, {"hit": 50.0, "miss": 50.0}, anchored=False, ident="ls-ref")
dec = syn.hue_decision("d2", t + 0.5, {B0: 0.5, B2: 0.5},
                       {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t + 0.5}]},
                        B2: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-ref",
                       targets={"A": {"surface_id": "hit"}, "B": {"surface_id": "miss"}})
after = syn.light_state(t + 5, {"hit": 100.0, "miss": 70.0}, anchored=False)
m0 = closed({"held_share_min": 0})
verdicts = {}
for name, mod in (("pinned >=", score_orig), ("mutant >", mutant)):
    d, s, r = run(mod, m0, [dec], [ref, after], model)
    v = d[0]["verify"]
    hr = r["hit_rate"]
    verdicts[name] = hr["verdict"]
    flags = next(e for e in s[1]["residual"] if e["surface_id"] == "miss")["flags"]
    print(f"RESULT 2 {name}: held_share_min 0, no held channel; miss cause "
          f"{[e['cause'] for e in v['explanation'] or []]}, residual flags {flags}, hit rate {hr['value']} "
          f"({hr['hits']}/{hr['hits'] + hr['misses']}), verdict {hr['verdict']}, within_tolerance {v['within_tolerance']}")
if verdicts["pinned >="] == "pass" and verdicts["mutant >"] == "fail":
    fails += 1
    print("FAIL with held_share_min closed at 0, every outside point (held share 0.0 >= 0) is excluded as "
          "'held': a 50% hit rate reads as a 100% pass; the boundary operator decides the verdict")
print(f"RESULT {fails} failing case(s)")

#!/usr/bin/env python3
"""test-predictor.py — unit tests for the anticipatory-lighting Markov
predictor (predictor/ package, Addendum 37 §6).

Covers the motion gate, the seed-adjacency loader + Dirichlet prior,
cold-start quiet, online learning, time-of-day conditioning, count decay,
and __exit__ handling. Exit 0 = all pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # repo root -> import predictor

import predictor as P                            # noqa: E402

_passed = 0
_failed = 0


def check(name, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


T0 = 1_700_000_000.0
DAY = 86400.0

# ───────────────────────── motion_factor ──────────────────────────────

check("motion_factor: settled (<0.3 m/s) -> 0", P.motion_factor(0.1) == 0.0)
check("motion_factor: walking (>=1.0 m/s) -> 1", P.motion_factor(1.5) == 1.0)
check("motion_factor: mid-speed -> linear",
      abs(P.motion_factor(0.65) - 0.5) < 1e-9, str(P.motion_factor(0.65)))
check("motion_factor: unknown speed -> 1 (no suppression)",
      P.motion_factor(None) == 1.0)

# ──────────────────── seed adjacency loader ────────────────────────────

adj = P.parse_adjacency("adjacency:\n  a: [b]\n  c: [a]\n")
check("parse_adjacency: edges are made symmetric",
      "c" in adj["a"] and "b" in adj["a"] and adj["b"] == {"a"},
      str(adj))

seed_path = HERE.parent / "predictor" / "seed_adjacency.yaml"
ADJ = P.load_adjacency(str(seed_path))
check("load_adjacency: seed_adjacency.yaml loads the zone graph",
      len(ADJ) >= 15, str(len(ADJ)))
check("load_adjacency: explicit edge present (office~sofa)",
      "sofa" in ADJ.get("office", set()))
check("load_adjacency: symmetric edge present (office~front_door)",
      "front_door" in ADJ.get("office", set()),
      str(ADJ.get("office")))
check("load_adjacency: driveway~front_door",
      "front_door" in ADJ.get("e28", set()))

# ─────────────────────────── prior ────────────────────────────────────

pred = P.Predictor(adjacency=ADJ)
pri = pred.prior("office")
check("prior: seed-adjacent zone gets ALPHA",
      pri["sofa"] == P.ALPHA, str(pri["sofa"]))
check("prior: __exit__ always gets ALPHA",
      pri[P.EXIT] == P.ALPHA)
check("prior: non-adjacent zone gets EPSILON",
      pri["workshop_zone"] == P.EPSILON, str(pri["workshop_zone"]))

# ─────────────────── cold-start: predicts but quiet ───────────────────

r = pred.predict("office", "evening", T0)
check("cold-start: zero observations -> markov_confidence ~0",
      r["markov_confidence"] < 0.01, str(r["markov_confidence"]))
check("cold-start: observations counted as 0",
      r["observations"] == 0, str(r["observations"]))
check("cold-start: still returns a top_zone (no crash)",
      r["top_zone"] in pred.targets, str(r["top_zone"]))

# ───────────────────── online learning ────────────────────────────────

learn = P.Predictor(adjacency=ADJ)
for i in range(40):
    learn.update("office", "evening", "sink", T0 + i)
r = learn.predict("office", "evening", T0 + 40)
check("learning: 40 obs office/evening->sink => top_zone == sink",
      r["top_zone"] == "sink", str(r["top_zone"]))
check("learning: learned destination probability > 0.8",
      r["predictions"][0]["p"] > 0.8, str(r["predictions"][0]["p"]))
check("learning: evidence saturates at 1.0",
      r["evidence"] == 1.0, str(r["evidence"]))
check("learning: markov_confidence is high (> 0.7)",
      r["markov_confidence"] > 0.7, str(r["markov_confidence"]))

# ─────────────── time-of-day conditioning ─────────────────────────────

tod = P.Predictor(adjacency=ADJ)
for i in range(25):
    tod.update("office", "evening", "sink", T0 + i)
    tod.update("office", "morning", "front_door", T0 + i)
check("tod: office/evening -> sink",
      tod.predict("office", "evening", T0 + 30)["top_zone"] == "sink")
check("tod: office/morning -> front_door (same from-zone, different ToD)",
      tod.predict("office", "morning", T0 + 30)["top_zone"] == "front_door")

# ───────────────────────── count decay ────────────────────────────────

cs = P.CountStore(half_life_days=23.0)
cs.add("a", "x", "b", T0)
got = cs.get("a", "x", T0 + 23 * DAY)["b"]
check("decay: a count halves after one 23-day half-life",
      abs(got - 0.5) < 0.02, str(got))
got2 = cs.get("a", "x", T0 + 46 * DAY)["b"]
check("decay: a count quarters after two half-lives",
      abs(got2 - 0.25) < 0.02, str(got2))

# ───────────────────────── motion gate ────────────────────────────────

r_still = learn.predict("office", "evening", T0 + 40, speed_mps=0.1)
check("motion gate: settled person -> effective_confidence 0",
      r_still["effective_confidence"] == 0.0,
      str(r_still["effective_confidence"]))
r_walk = learn.predict("office", "evening", T0 + 40, speed_mps=1.5)
check("motion gate: walking person -> effective == markov confidence",
      r_walk["effective_confidence"] == r_walk["markov_confidence"],
      str(r_walk))

# ─────────────────────── __exit__ as a target ─────────────────────────

ex = P.Predictor(adjacency=ADJ)
for i in range(40):
    ex.update("front_door", "night", P.EXIT, T0 + i)
check("__exit__: a habitual leave predicts __exit__",
      ex.predict("front_door", "night", T0 + 40)["top_zone"] == P.EXIT)

# ───────────── stitch_transitions — room-to-room reconstruction ────────

def _rec(ts, fz, tz, cam="living_room"):
    return {"ts": ts, "from_zone": fz, "to_zone": tz,
            "tod_bucket": "evening", "prev_zone": None, "prev2_zone": None,
            "track_id": "t" + str(ts), "camera": cam}


check("to_epoch: float passes through",
      P.to_epoch(1700000000.0) == 1700000000.0)
check("to_epoch: ISO-8601 string -> epoch float",
      isinstance(P.to_epoch("2026-05-20T18:00:00+00:00"), float))

st = P.stitch_transitions([
    _rec(T0, "office", P.EXIT, "living_room"),
    _rec(T0 + 3, P.ENTRY, "sink", "kitchen"),
])
check("stitch: exit + entry within gap -> one office->sink room move",
      len(st) == 1 and st[0]["from_zone"] == "office"
      and st[0]["to_zone"] == "sink" and st[0].get("stitched") is True,
      str(st))

st = P.stitch_transitions([
    _rec(T0, "office", P.EXIT),
    _rec(T0 + 30, P.ENTRY, "sink"),
])
check("stitch: gap too large -> exit kept, entry dropped",
      len(st) == 1 and st[0]["to_zone"] == P.EXIT, str(st))

st = P.stitch_transitions([_rec(T0, "sofa", "front_left")])
check("stitch: within-camera move passes through unchanged",
      len(st) == 1 and st[0]["to_zone"] == "front_left"
      and "stitched" not in st[0], str(st))

check("stitch: a lone entry (no preceding exit) is dropped",
      P.stitch_transitions([_rec(T0, P.ENTRY, "sofa")]) == [])

st = P.stitch_transitions([_rec(T0, "sofa", P.EXIT)])
check("stitch: a lone exit is kept as __exit__",
      len(st) == 1 and st[0]["to_zone"] == P.EXIT)

st = P.stitch_transitions([
    _rec(T0, "front_left", "sofa", "living_room"),
    _rec(T0 + 10, "sofa", P.EXIT, "living_room"),
    _rec(T0 + 14, P.ENTRY, "island_left", "kitchen"),
    _rec(T0 + 60, "island_left", P.EXIT, "kitchen"),
])
zones = [(r["from_zone"], r["to_zone"]) for r in st]
check("stitch: mixed stream -> within-cam + stitched + genuine exit",
      zones == [("front_left", "sofa"), ("sofa", "island_left"),
                ("island_left", P.EXIT)], str(zones))
check("stitch: output is chronologically sorted",
      all(P.to_epoch(st[i]["ts"]) <= P.to_epoch(st[i + 1]["ts"])
          for i in range(len(st) - 1)))

# ───────────── clean_transitions — ghost-track quality filter ──────────

def _trec(tid, score, fz="sofa", tz="office"):
    return {"ts": T0, "from_zone": fz, "to_zone": tz, "tod_bucket": "evening",
            "track_id": tid, "frigate_score": score}


check("clean: min_score 0 -> no-op (keeps everything)",
      len(P.clean_transitions([_trec("a", 0.3), _trec("b", 0.9)], 0.0)) == 2)
check("clean: drops a whole low-score ghost track",
      P.clean_transitions([_trec("g", 0.55), _trec("g", 0.61)], 0.7) == [])
check("clean: keeps a track that reaches the threshold on any one record",
      len(P.clean_transitions([_trec("r", 0.55), _trec("r", 0.83)], 0.7)) == 2,
      "a real track caught at a momentary low score must stay intact")
check("clean: mixed stream — ghost dropped, real kept",
      [r["track_id"] for r in P.clean_transitions(
          [_trec("ghost", 0.5), _trec("real", 0.9)], 0.7)] == ["real"])
check("clean: records with no frigate_score are kept (synthetic-safe)",
      len(P.clean_transitions(
          [{"track_id": "s", "ts": T0, "from_zone": "sofa"}], 0.7)) == 1)
check("clean: records with no track_id are kept",
      len(P.clean_transitions(
          [{"ts": T0, "frigate_score": 0.1, "from_zone": "sofa"}], 0.7)) == 1)

# ─────────── transitions_since — train-from-a-cutoff window ─────────────

check("since: None -> no-op (keeps everything)",
      len(P.transitions_since([_trec("a", 0.9), _trec("b", 0.9)], None)) == 2)
check("since: epoch cutoff drops earlier records",
      [r["ts"] for r in P.transitions_since(
          [{"ts": 100.0}, {"ts": 300.0}], 200.0)] == [300.0])
check("since: ISO-8601 cutoff drops pre-cutoff records",
      [r["track_id"] for r in P.transitions_since(
          [{"ts": "2026-05-20T10:00:00+00:00", "track_id": "old"},
           {"ts": "2026-05-21T20:00:00+00:00", "track_id": "new"}],
          "2026-05-21T18:20:00+00:00")] == ["new"])

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(0 if _failed == 0 else 1)

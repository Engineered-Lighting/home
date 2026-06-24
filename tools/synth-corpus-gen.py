#!/usr/bin/env python3
"""synth-corpus-gen.py — Job A of the predictor validation harness
(Addendum 37 §18).

Generates a synthetic transitions.jsonl from a KNOWN generative household-
routine model over the real 15-zone graph. Because the ground-truth
transition model is known, its theoretical top-1/top-2 accuracy ceiling is
computable — so the predictor can be verified to converge toward it
(tools/test-predictor-harness.py), validating the predictor AND the harness
with zero real data.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from predictor import EXIT, ZONES, load_adjacency        # noqa: E402

TODS = ["night", "morning", "midday", "afternoon", "evening", "late"]
MIDNIGHT_TS = 1_699_920_000.0     # a UTC midnight — keeps sim hours aligned


def _tod(hour):
    if hour < 6:
        return "night"
    if hour < 10:
        return "morning"
    if hour < 14:
        return "midday"
    if hour < 18:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "late"


def build_truth(adjacency, seed=42):
    """Ground-truth generative model: (from_zone, tod) -> {to_zone: prob}.
    Destinations favour seed-adjacent zones (people move between connected
    rooms) and the dominant destination varies with time-of-day — so a
    predictor must learn ToD conditioning to reach the ceiling."""
    rng = random.Random(seed)
    truth = {}
    for fz in ZONES:
        adj = sorted(adjacency.get(fz, set()) - {fz})
        for tod in TODS:
            cands = list(adj) + [EXIT]
            rng.shuffle(cands)
            if len(cands) < 4:
                extra = [z for z in ZONES if z != fz and z not in cands]
                rng.shuffle(extra)
                cands = (cands + extra)[:4]
            else:
                cands = cands[:4]
            truth[(fz, tod)] = {cands[0]: 0.68, cands[1]: 0.18,
                                cands[2]: 0.09, cands[3]: 0.05}
    return truth


def _sample(dist, rng):
    r = rng.random()
    acc = 0.0
    for z, p in dist.items():
        acc += p
        if r <= acc:
            return z
    return next(reversed(dist))


def generate(adjacency, days=40, seed=42):
    """Simulate `days` of household movement. Returns (records, ceiling)
    where ceiling has the theoretical top-1/top-2 accuracy of an optimal
    predictor on this corpus."""
    rng = random.Random(seed + 1)
    truth = build_truth(adjacency, seed)
    records = []
    ceil1 = ceil2 = 0.0
    for day in range(days):
        base = MIDNIGHT_TS + day * 86400.0
        t = base + rng.uniform(6.5, 9.0) * 3600.0
        day_end = base + rng.uniform(21.0, 23.5) * 3600.0
        cur = rng.choice(ZONES)
        while t < day_end:
            tod = _tod(int((t % 86400) // 3600))
            dist = truth[(cur, tod)]
            nxt = _sample(dist, rng)
            ordered = sorted(dist.values(), reverse=True)
            ceil1 += ordered[0]
            ceil2 += ordered[0] + ordered[1]
            records.append({"ts": round(t, 1), "from_zone": cur,
                            "to_zone": nxt, "tod_bucket": tod})
            if nxt == EXIT:
                t += rng.uniform(20, 180) * 60.0      # left — gap, re-enter
                cur = rng.choice(ZONES)
            else:
                cur = nxt
                t += rng.uniform(1.5, 18.0) * 60.0
    n = len(records) or 1
    return records, {"top1": ceil1 / n, "top2": ceil2 / n,
                     "records": len(records)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="tools/synthetic-transitions.jsonl")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    adj = load_adjacency(str(repo / "predictor" / "seed_adjacency.yaml"))
    records, ceiling = generate(adj, days=args.days, seed=args.seed)
    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    with open(out, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(records)} records -> {out}")
    print(f"theoretical ceiling: top-1 {ceiling['top1']:.3f}  "
          f"top-2 {ceiling['top2']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

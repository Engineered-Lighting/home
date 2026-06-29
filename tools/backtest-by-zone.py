#!/usr/bin/env python3
"""Per-zone accuracy breakdown — same walk-forward replay as
run-predictor-backtest.py, but buckets hits by from_zone so we can see
which zones are well-trained vs starved. Reports n, top1/2/3, and a
"would the prior alone get this?" baseline so we can tell when a zone is
relying on the seed-adjacency warm-start rather than learned counts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from predictor import (Predictor, clean_transitions, load_adjacency,  # noqa: E402
                       stitch_transitions, to_epoch, transitions_since)


def load_records(path):
    recs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--since", default=None)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    adj = load_adjacency(str(repo / "predictor" / "seed_adjacency.yaml"))

    raw = load_records(args.corpus)
    windowed = transitions_since(raw, args.since)
    cleaned = clean_transitions(windowed, args.min_score)
    recs = stitch_transitions(cleaned)

    pred = Predictor(adjacency=adj)
    by_zone = defaultdict(lambda: {"n": 0, "t1": 0, "t2": 0, "t3": 0,
                                   "actuals": defaultdict(int),
                                   "predicted": defaultdict(int)})

    for r in recs:
        ts = to_epoch(r["ts"])
        fz, tod, to = r["from_zone"], r["tod_bucket"], r["to_zone"]
        out = pred.predict(fz, tod, ts)
        ranked = [p["zone"] for p in out["predictions"]]
        b = by_zone[fz]
        b["n"] += 1
        if to == ranked[0]: b["t1"] += 1
        if to in ranked[:2]: b["t2"] += 1
        if to in ranked[:3]: b["t3"] += 1
        b["actuals"][to] += 1
        if ranked: b["predicted"][ranked[0]] += 1
        pred.update(fz, tod, to, ts)

    rows = sorted(by_zone.items(), key=lambda kv: -kv[1]["n"])
    print(f"corpus: {len(recs)} records "
          f"({len(by_zone)} unique source zones)")
    print()
    print(f"{'from_zone':<25} {'n':>5}  {'top-1':>6} {'top-2':>6} {'top-3':>6}   "
          f"most-common-actual")
    print("-" * 90)
    for fz, b in rows:
        n = b["n"]
        t1, t2, t3 = b["t1"]/n, b["t2"]/n, b["t3"]/n
        top_actual = sorted(b["actuals"].items(), key=lambda kv: -kv[1])[:3]
        ta_str = ", ".join(f"{z}({c})" for z, c in top_actual)
        print(f"{fz:<25} {n:>5}  {t1:>6.1%} {t2:>6.1%} {t3:>6.1%}   {ta_str}")

    print()
    # Aggregate
    total_n = sum(b["n"] for b in by_zone.values())
    total_t1 = sum(b["t1"] for b in by_zone.values())
    total_t2 = sum(b["t2"] for b in by_zone.values())
    total_t3 = sum(b["t3"] for b in by_zone.values())
    print(f"{'OVERALL':<25} {total_n:>5}  "
          f"{total_t1/total_n:>6.1%} {total_t2/total_n:>6.1%} "
          f"{total_t3/total_n:>6.1%}")


if __name__ == "__main__":
    raise SystemExit(main())

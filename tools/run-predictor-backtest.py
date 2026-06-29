#!/usr/bin/env python3
"""run-predictor-backtest.py — Job B of the predictor validation harness
(Addendum 37 §18).

Walk-forward replay of a transitions.jsonl corpus (synthetic or real): at
each transition, predict -> score -> THEN update. A hard assertion guards
against look-ahead leakage. Reports top-1/2/3 accuracy overall and over the
converged tail. Used by tools/test-predictor-harness.py on synthetic data
and, in the deferred tail, on the real soak corpus.

Two corpus-quality knobs, both applied before stitching/replay and both
no-ops on the synthetic corpus:
  --since      ignore records collected before a cutoff (e.g. a Frigate fix)
  --min-score  drop low-confidence ghost tracks (predictor.clean_transitions)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from predictor import (Predictor, clean_transitions, load_adjacency,  # noqa: E402
                       stitch_transitions, to_epoch, transitions_since)

# Default ghost-filter threshold. A no-op on the synthetic corpus (no
# frigate_score); active on the real logger corpus. Tuned via the
# --min-score sweep against the real soak data.
DEFAULT_MIN_SCORE = 0.0

# Recommended --since cutoff for the REAL corpus: the Frigate dining-camera
# threshold fix (person threshold 0.60 -> 0.75) landed 2026-05-21 ~18:20 UTC.
# Records before that are from the old low-threshold, ghost-heavy regime and
# should be excluded from training:  --since 2026-05-21T18:20:00+00:00


def load_records(path):
    """Load a transitions.jsonl corpus. Tolerant of a torn final line — the
    logger appends, so a kill mid-write can leave one malformed line."""
    recs = []
    bad = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"  note: skipped {bad} malformed line(s)")
    return recs


def backtest(records, adjacency, tail_frac=0.3, min_score=0.0, since=None):
    """Walk-forward: predict each transition, score it, THEN learn from it —
    so a prediction is never informed by its own outcome. Raises on any
    non-chronological record (look-ahead-leakage guard).

    `since` trims the corpus to records at/after a cutoff (e.g. the moment a
    Frigate fix landed). `min_score` drops low-confidence ghost tracks. Both
    run before stitching, so the predictor trains on real movement only.
    """
    raw_n = len(records)
    windowed = transitions_since(records, since)
    cleaned = clean_transitions(windowed, min_score)
    # stitch cross-camera room moves back together (a no-op on a corpus
    # with no __entry__/__exit__ pairs); the result is sorted chronologically
    recs = stitch_transitions(cleaned)
    pred = Predictor(adjacency=adjacency)
    n = len(recs)
    tail_start = int(n * (1.0 - tail_frac))
    hits = {"top1": 0, "top2": 0, "top3": 0}
    tail = {"top1": 0, "top2": 0, "top3": 0, "n": 0}
    last_ts = None

    for i, r in enumerate(recs):
        ts = to_epoch(r["ts"])
        if last_ts is not None and ts < last_ts:
            raise AssertionError("look-ahead leakage: records not sorted")
        last_ts = ts
        fz, tod, to = r["from_zone"], r["tod_bucket"], r["to_zone"]

        out = pred.predict(fz, tod, ts)             # PREDICT (pre-update)
        ranked = [p["zone"] for p in out["predictions"]]
        t1 = to == ranked[0]
        t2 = to in ranked[:2]
        t3 = to in ranked[:3]
        for key, ok in (("top1", t1), ("top2", t2), ("top3", t3)):
            if ok:
                hits[key] += 1
                if i >= tail_start:
                    tail[key] += 1
        if i >= tail_start:
            tail["n"] += 1

        pred.update(fz, tod, to, ts)                # UPDATE (post-score)

    def _rate(h, d):
        return round(h / d, 4) if d else 0.0

    tn = tail["n"] or 1
    return {
        "records": n,
        "raw_records": raw_n,
        "time_filtered": raw_n - len(windowed),
        "ghost_filtered": len(windowed) - len(cleaned),
        "overall": {k: _rate(hits[k], n) for k in ("top1", "top2", "top3")},
        "converged": {k: _rate(tail[k], tn)
                      for k in ("top1", "top2", "top3")},
        "tail_records": tail["n"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="tools/synthetic-transitions.jsonl")
    ap.add_argument("--tail-frac", type=float, default=0.3)
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                    help="Drop tracks whose best frigate_score is below this "
                         "(0 = no filter; no-op on the synthetic corpus).")
    ap.add_argument("--since", default=None,
                    help="Ignore corpus records before this ISO-8601 time — "
                         "train from a clean cutoff (e.g. a Frigate fix) "
                         "without deleting the older history.")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    corpus = Path(args.corpus)
    if not corpus.is_absolute():
        corpus = repo / corpus
    adj = load_adjacency(str(repo / "predictor" / "seed_adjacency.yaml"))
    result = backtest(load_records(corpus), adj, tail_frac=args.tail_frac,
                      min_score=args.min_score, since=args.since)

    print(f"corpus: {result['records']} records  "
          f"(converged tail: {result['tail_records']})")
    if result["time_filtered"]:
        print(f"  time filter: dropped {result['time_filtered']} of "
              f"{result['raw_records']} raw records before {args.since}")
    if result["ghost_filtered"]:
        print(f"  ghost filter: dropped {result['ghost_filtered']} "
              f"low-confidence records (min_score {args.min_score})")
    o, c = result["overall"], result["converged"]
    print(f"  overall    top-1 {o['top1']:.3f}  top-2 {o['top2']:.3f}  "
          f"top-3 {o['top3']:.3f}")
    print(f"  converged  top-1 {c['top1']:.3f}  top-2 {c['top2']:.3f}  "
          f"top-3 {c['top3']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

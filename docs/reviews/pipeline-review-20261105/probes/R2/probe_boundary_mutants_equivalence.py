#!/usr/bin/env python3
"""probe_boundary_mutants_equivalence: can the '>'-vs-'>=' boundary mutants on den-observe's
depth-gap and detector-age rules ever change an outcome with realistic timestamps?

What it checks
    The mutants M01 (observe.depth_state: abs(gap) > max_gap_s -> >=) and M08
    (observe.freshness: age > max_age_s -> >=) differ from the original only
    when the difference of two float timestamps is exactly the threshold.
    The probe draws 200,000 synthetic host-monotonic capture times written to
    0.1 ms (as frames.csv t_host_s is), sets the depth time exactly 67.0 ms
    earlier and the completion time exactly 1.0 s later (both rounded to
    0.1 ms), and calls den-observe's own depth_state() and freshness() with
    max_depth_gap_s 0.067 and detector_max_age_s 1.0. It counts how often the
    computed gap/age equals the threshold exactly, i.e. how often the original
    and the mutant would disagree.

How to run
    python3 probe_boundary_mutants_equivalence.py <branch checkout root>

Synthetic numbers only; nothing is written; nothing connects anywhere.
"""
import random
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import observe  # noqa: E402


class S:  # the two settings the functions read
    max_gap_s, max_age_s, max_validity_s = 0.067, 1.0, 3.0


class D:
    mm = [1]


random.seed(1)
N = 200000
gap_equal = age_equal = 0
for _ in range(N):
    t = round(random.uniform(1000.0, 90000.0), 4)
    meta = {"t_depth": round(t - 0.067, 4)}
    row = {"frame": {"capture_ts": t}, "available_ts": round(t + 1.0, 4)}
    state, gap = observe.depth_state(row, meta, D, S)
    if abs(gap) == S.max_gap_s:
        gap_equal += 1
    fresh = observe.freshness(row, t, 0.0, S, state, gap)
    if row["available_ts"] - t == S.max_age_s:
        age_equal += 1
print(f"RESULT M01 (depth gap '>' vs '>='): outcomes differ in {gap_equal} of {N} frames placed exactly "
      f"67.0 ms apart at 0.1 ms resolution")
print(f"RESULT M08 (detector age '>' vs '>='): outcomes differ in {age_equal} of {N} results completed "
      f"exactly 1.0 s after capture at 0.1 ms resolution")

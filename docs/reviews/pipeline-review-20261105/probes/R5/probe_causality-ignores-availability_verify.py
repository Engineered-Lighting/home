#!/usr/bin/env python3
"""probe_causality-ignores-availability_verify.py: size of the capture-as-arrival look-ahead
at latencies rung 2 would accept (its detector_max_age_s is open, proposed 1.0 s).

What it checks
  Reuses probe_causality_available_ts.py's synthetic rung-2-shaped scene (one
  confirmed track, a result every 0.5 s) at latencies 0.3, 0.5 and 1.0 s, routes
  2..30 s with router v0, and for each routed bundle counts keypoint entries
  whose result arrived after the send, and compares the tool's `missing` with
  a bundle assembled only from results available by the send.

How to run
  python3 probe_causality-ignores-availability_verify.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network, no files written.
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = sys.argv[1]
sys.argv = [sys.argv[0], ROOT]
sys.path.insert(0, HERE)
import probe_causality_available_ts as base  # noqa: E402

m, R = base.method.load_method(), base.method.load_router()
any_late = False
for latency in (0.3, 0.5, 1.0):
    obs, lines = base.r2_like(latency)
    st = base.records.Store(obs, lines)
    clock = base.fakes.FakeClock(base.T0 + 2.0)
    sender = base.fakes.FakeSender(clock)
    r = base.router.Router(st, m, R, clock, sender)
    base.router.run(r, clock, sender, base.T0 + 30.0)
    late_counts, flag_diffs = [], 0
    for rec, b in zip([x for x in r.records if x['outcome'] != 'no_box'], r.bundles):
        kp, nkp, fr, nfr, crop_future = base.future_counts(b, st, rec['sent_ts'])
        late_counts.append((kp, nkp))
        cb = base.causal_bundle(obs, lines, m, rec['sent_ts'])
        flag_diffs += cb['missing'] != b['missing']
        any_late |= kp > 0
    print('RESULT latency %.1f s: %d routed bundles; late keypoint entries per bundle %s; bundles whose missing '
          'differs from the causal bundle: %d' % (latency, len(late_counts), sorted(set(late_counts)), flag_diffs))
print('RESULT %s: look-ahead present at accepted latencies; missing flags differ only if latency approaches max_gap_s'
      % ('FAIL' if any_late else 'PASS'))

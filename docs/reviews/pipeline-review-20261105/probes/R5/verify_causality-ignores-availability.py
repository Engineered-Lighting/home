#!/usr/bin/env python3
"""verify_causality-ignores-availability.py: replay bundles hold not-yet-arrived evidence, and the
gate prefers them over bundles built only from what had arrived.

What it checks
  One confirmed track A, a keypoints observation every 0.5 s for 40 s, each
  arriving `latency` s after capture (written as an `arrival` side table here,
  since schemas.json's Observation has no arrival field). Router v0 replays
  2..30 s with the tool as written. For each routed send time t:
    - counts keypoint entries / frames in the tool's bundle captured in
      (t - latency, t], i.e. not yet arrived at t;
    - builds the bundle a live router could have built at t (assemble() on a
      store holding only observations arrived by t, and only track lines whose
      observations had all arrived), and runs gate.check() on both bundle sets
      against the full recorded store (what an offline gate check sees).
  Expected if the finding holds: the tool's bundles contain not-yet-arrived
  entries and pass the gate; the causal bundles fail its verbatim clause.

How to run
  python3 verify_causality-ignores-availability.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network, no files written.
"""
import os
import sys

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import fakes    # noqa: E402
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
import synth    # noqa: E402
from assemble import assemble  # noqa: E402

M, R = method.load_method(), method.load_router()


def scene(latency):
    obs, lines, arrival = [], [], {}
    for t in synth.steps(0.0, 40.0, 0.5):
        synth.seen(obs, lines, 'A', t, 1.95, 0.95)
        arrival[obs[-1]['obs_id']] = obs[-1]['capture_ts'] + latency
    return obs, lines, arrival


def main():
    bad = 0
    for latency in (0.3, 1.46):
        obs, lines, arrival = scene(latency)
        st = records.Store(obs, lines)
        clock = fakes.FakeClock(synth.ts(2.0))
        sender = fakes.FakeSender(clock)
        r = router.Router(st, M, R, clock, sender)
        recs = router.run(r, clock, sender, synth.ts(30.0))
        sent = [x for x in recs if x['outcome'] != 'no_box']
        future = []
        causal = []
        for rec, b in zip(sent, r.bundles):
            t = rec['sent_ts']
            future.append(sum(1 for k in b['keypoint_series'] if arrival[k['obs_id']] > t + 1e-6))
            known = [o for o in obs if arrival[o['obs_id']] <= t + 1e-6]
            ids = {o['obs_id'] for o in known}
            kl = [l for l in lines if all(i in ids for i in l['last_obs_ids'])]
            causal.append(assemble(records.Store(known, kl), M, 'A', t))
        g_tool = gate.check(r.bundles, st, M, (), recs, rule=R)
        g_causal = gate.check(causal, st, M, ())
        vf = g_causal['clauses']['verbatim']['failures']
        print('latency %.2f s: %d routed bundles; not-yet-arrived keypoint entries per bundle = %s' % (
            latency, len(sent), sorted(set(future))))
        print('    tool bundles   -> gate verdict %s (missing flagged in any: %s)' % (
            g_tool['verdict'], sorted({m for b in r.bundles for m in b['missing']})))
        print('    causal bundles -> gate verdict %s; verbatim failures %d, e.g. %s; causal missing = %s' % (
            g_causal['verdict'], len(vf), vf[:1], sorted({m for b in causal for m in b['missing']})))
        if any(future) and g_tool['verdict'] == 'pass':
            bad += 1
    print('RESULT %s: %d of 2 latencies: replay bundles used evidence not yet arrived at their send and passed the gate' % (
        'FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

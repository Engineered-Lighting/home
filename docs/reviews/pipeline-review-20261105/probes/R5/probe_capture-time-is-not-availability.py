"""Probe: bundles use observations (and track lines) that were not yet available at their emission time.

What it checks
  Rung 2 (tools/den-observe observe.py, METHOD.md "Freshness") writes on every Observation
  `freshness.available_ts`, the time the detector result was complete (R5: about 1.46 s
  after capture in the den). Rung 5 treats every record as known at its `capture_ts`
  (METHOD-V1 "Only records known by t1 are read: observations captured by then").
  This probe builds one person observed every 0.5 s with available_ts = capture + L, for
  L in (0.3, 0.8, 1.46), routes it with the tool's own router, and for every routed bundle
  counts keypoint entries / frames / positions whose source observation was only available
  after the bundle's send time. It then rebuilds the same bundle from a causal store (only
  observations with available_ts <= t1, and only track lines whose observations are) and
  compares `missing`.
  PASS would mean no routed bundle holds evidence that arrived after its send time.

How to run
  python3 probe_capture-time-is-not-availability.py <root of the branch checkout>

Synthetic data only; no network.
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import fakes     # noqa: E402
import method    # noqa: E402
import records   # noqa: E402
import router    # noqa: E402
import synth     # noqa: E402
from assemble import assemble  # noqa: E402

M, RULE = method.load_method(), method.load_router()


def scene(latency, step=0.5, until=40.0):
    obs, tracks = [], []
    for t in synth.steps(0.0, until, step):
        synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
        o = obs[-1]
        o['freshness'] = {'available_ts': round(o['capture_ts'] + latency, 3), 'status': 'fresh'}
    return obs, tracks


def causal_store(obs, tracks, t1):
    avail = {o['obs_id'] for o in obs if o['freshness']['available_ts'] <= t1 + 1e-6}
    o2 = [o for o in obs if o['obs_id'] in avail]
    t2 = [l for l in tracks if all(x in avail for x in l['last_obs_ids'])]
    return records.Store(o2, t2)


bad_total = 0
for latency in (0.3, 0.8, 1.46, 1.6):
    obs, tracks = scene(latency)
    by_id = {o['obs_id']: o for o in obs}
    st = records.Store(obs, tracks)
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock, latency_s=1.25)
    r = router.Router(st, M, RULE, clock, sender)
    recs = router.run(r, clock, sender, synth.ts(40.0))
    future_kp = future_fr = 0
    flag_diff = []
    for b in r.bundles:
        t1 = b['interval'][1]
        future_kp += sum(1 for k in b['keypoint_series'] if by_id[k['obs_id']]['freshness']['available_ts'] > t1 + 1e-6)
        future_fr += sum(1 for f in b['frames'] if f['ts'] + latency > t1 + 1e-6)
        c = assemble(causal_store(obs, tracks, t1), M, 'A', t1)
        if c['missing'] != b['missing']:
            flag_diff.append((round(t1 - synth.T0, 1), b['missing'], c['missing']))
    n_kp = sum(len(b['keypoint_series']) for b in r.bundles)
    print('RESULT latency %.2f s: %d bundles; %d of %d keypoint entries and %d frames were not available at send time; '
          'missing differs from a causal build in %d bundles %s' % (
              latency, len(r.bundles), future_kp, n_kp, future_fr, len(flag_diff), flag_diff[:2]))
    bad_total += future_kp

# The tool's own late-result case: the track line that lists a late result is dated by the line before it,
# but an ordinary observation is "known" at its capture time.
print('FAIL: routed bundles carry evidence that arrived after their send time' if bad_total
      else 'PASS: no future evidence in routed bundles')

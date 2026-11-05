"""Probe: router.run() ends the whole replay at the first tick where every eligible track is no_box.

What it checks
  One person (A), confirmed and confident for 60 s, observed every 0.5 s. From 10.0 s to
  15.0 s the detector results are marked by rung 2 as `rejected:detector_result_expired`
  (R5: 115 of 228 den detector results expired), so the bundle at 14 s has no valid boxed
  frame and is logged `no_box`. A 4 s pace from 2 s to 60 s should give ~15 starts.
  The probe counts the starts router.run() actually makes, then runs the CLI `route` and
  `gate` on the same files and prints the exit codes and verdict.
  A second variant uses image_box = null for the same stretch (same effect).

How to run
  python3 probe_no-box-ends-replay.py <root of the branch checkout>
  e.g. python3 probe_no-box-ends-replay.py .../wt/rung-5-bundle

Synthetic data only; temp dirs only; no network.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, TOOL)

import fakes     # noqa: E402
import method    # noqa: E402
import records   # noqa: E402
import router    # noqa: E402
import synth     # noqa: E402


def scene(variant):
    obs, tracks = [], []
    for t in synth.steps(0.0, 60.0, 0.5):
        synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
        if 10.0 <= t < 15.0:
            if variant == 'expired':
                obs[-1]['validity'] = 'rejected:detector_result_expired'
            else:
                obs[-1]['image_box'] = None
    return obs, tracks


def replay(obs, tracks, start=2.0, until=60.0):
    st = records.Store(obs, tracks)
    m, rule = method.load_method(), method.load_router()
    clock = fakes.FakeClock(synth.ts(start))
    sender = fakes.FakeSender(clock, latency_s=1.25)
    r = router.Router(st, m, rule, clock, sender)
    recs = router.run(r, clock, sender, synth.ts(until))
    return recs


ok_all = True
for variant in ('expired', 'null_box'):
    obs, tracks = scene(variant)
    recs = replay(obs, tracks)
    sent = [r for r in recs if r['outcome'] != 'no_box']
    last = max(r['sent_ts'] for r in recs) - synth.T0
    print('RESULT %s: %d records, %d sent, last record at %.1f s; outcomes %s' % (
        variant, len(recs), len(sent), last, [(round(r['sent_ts'] - synth.T0, 1), r['outcome']) for r in recs]))
    # Expected: starts at 2,6,10,(14 no_box),18,...,58 -> 14 sent, last record near 58 s
    if last < 50.0:
        print('FAIL %s: replay stopped at %.1f s although A is confirmed and seen with boxes until 60 s' % (variant, last))
        ok_all = False
    else:
        print('PASS %s: replay continued after the no_box tick' % variant)

# The same through the command line, then the gate on what route wrote.
obs, tracks = scene('expired')
with tempfile.TemporaryDirectory() as d:
    o, t = os.path.join(d, 'obs.jsonl'), os.path.join(d, 'tracks.jsonl')
    records.write_jsonl(o, obs)
    records.write_jsonl(t, tracks)
    cli = [sys.executable, '-B', os.path.join(TOOL, 'den_bundle.py')]
    rq, bd = os.path.join(d, 'req.jsonl'), os.path.join(d, 'bundles.jsonl')
    p = subprocess.run(cli + ['route', '--observations', o, '--tracks', t, '--from', '2', '--to', '60',
                              '--requests', rq, '--bundles', bd], capture_output=True, text=True)
    print('RESULT route exit %d: %s' % (p.returncode, p.stdout.strip().replace('\n', ' | ')))
    g = subprocess.run(cli + ['gate', '--observations', o, '--tracks', t, '--bundles', bd, '--requests', rq],
                       capture_output=True, text=True)
    print('RESULT gate exit %d: %s' % (g.returncode, g.stdout.strip().replace('\n', ' | ')))
    if p.returncode == 0 and g.returncode == 0:
        print('FAIL cli: route exits 0 and the gate passes on a replay that silently stopped at the no_box tick')
        ok_all = False

print('OVERALL', 'PASS' if ok_all else 'FAIL')

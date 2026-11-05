"""Verifier probe (intent lens) for finding track-line-dating-untested.

What it checks
  1) With the checkout's records.py (max dating), a track line naming two observations captured
     at 5.0 s and 5.5 s is dated 5.5 s, so a bundle ending at 5.2 s does not carry it.
  2) With the M15 mutant (min dating) applied to a temp copy, the same line is dated 5.0 s, the
     5.2 s bundle carries that line's position (the line's Kalman state after the 5.5 s
     evidence), and gate.check on that bundle reports no failures (gate shares records.Store).
  3) Whether the suite's fixtures ever list observations at two capture times in one line.
Synthetic data only, temp dirs only; the checkout is never modified.

How to run
  python3 verify-intent_track-line-dating.py <root of the branch checkout>
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
SRC = os.path.join(ROOT, 'tools', 'den-bundle')

CHILD = r'''
import sys, copy, json
sys.path.insert(0, sys.argv[1])
import method, records, synth, gate
from assemble import assemble
M = method.load_method()
obs, lines = [], []
for t in (3.0, 3.5, 4.0, 4.5):
    o = synth.observation(synth.oid('A', t), t, 1.95, 0.95)
    obs.append(o); lines.append(synth.track_line('A', 1.95, 0.95, [o['obs_id']]))
o50 = synth.observation(synth.oid('A', 5.0), 5.0, 1.95, 0.95)
o55 = synth.observation(synth.oid('A', 5.5), 5.5, 2.40, 0.95)
obs += [o50, o55]
lines.append(synth.track_line('A', 2.40, 0.95, [o50['obs_id'], o55['obs_id']]))  # one update, two capture times
st = records.Store(obs, lines)
last = st.lines['A'][-1]
b = assemble(st, M, 'A', synth.ts(5.2))
xs = [(round(p['ts'] - synth.T0, 3), p['x']) for p in b['positions']]
res = gate.check([b], st, M)
fails = {k: len(v['failures']) for k, v in res['clauses'].items() if v['failures']}
print('RESULT line dated %.3f s; bundle@5.2 positions %s; gate failures %s' % (last.ts - synth.T0, xs, fails or 'none'))
multi = 0
for name in ('crossing', 'missing_depth', 'stale_observation', 'two_tracks', 'track_leaves'):
    sc = getattr(synth, name)()
    byid = {x['obs_id']: x for x in sc['observations']}
    for l in sc['tracks']:
        ts = {byid[i]['capture_ts'] for i in l['last_obs_ids'] if i in byid}
        multi += len(ts) > 1
print('RESULT fixture lines naming observations at more than one capture time: %d' % multi)
'''

with tempfile.TemporaryDirectory() as tmp:
    for label, mutate in (('original', False), ('M15 min-dating', True)):
        d = os.path.join(tmp, label.split()[0])
        shutil.copytree(SRC, d, ignore=shutil.ignore_patterns('__pycache__'))
        if mutate:
            p = os.path.join(d, 'records.py')
            s = open(p, encoding='utf-8').read()
            assert s.count('ts = max(known) if known else None') == 1
            open(p, 'w', encoding='utf-8', newline='\n').write(s.replace('ts = max(known) if known else None',
                                                                          'ts = min(known) if known else None'))
        out = subprocess.run([sys.executable, '-B', '-c', CHILD, d], capture_output=True, text=True)
        print('== %s' % label)
        print(out.stdout.strip() or out.stderr.strip())

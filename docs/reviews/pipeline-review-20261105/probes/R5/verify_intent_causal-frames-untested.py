#!/usr/bin/env python3
"""Verify (intent/spec lens) finding causal-frames-untested: are the surviving mutants
A4, G14 and A1 real behaviour changes (not equivalent mutants), and in which direction?

What it checks
  Copies <checkout>/tools/den-bundle into temp folders: the pinned code ("orig") and one
  copy per mutant (same text replacements as mutate_den_bundle_validate.py):
    A4  assemble.person_frames reads store.listed(track_id, None)   (frames non-causal)
    A3  assemble.gated_keypoints reads store.listed(track_id, None) (keypoints non-causal)
    G14 gate._gated_ids no longer skips lines dated after t1
    A1  assemble.association_safe drops the line in effect at t0
  Then, in a subprocess per copy (module names would collide in one process), on the tool's
  own synthetic fixtures:
    1) stale_observation, A at t1 = 4.0 (the late 3.2 s result's line is dated 5.0):
       frame times and keypoint ids, orig vs A4 / A3.
    2) the PINNED gate on the A4 bundle and on the A3 bundle; the G14 gate on the orig
       bundle and on the A3 bundle.
    3) association_safe: (b) stale_observation A at 8.5 (no line inside [5.5, 8.5]);
       (c) crossing with since_ts of the confident lines after the ambiguity set to 0.0,
       A at 10.2 (window [7.2, 10.2]: the line in effect at 7.2 is ambiguous).
  Prints RESULT lines. Synthetic data only, temp dirs only; the checkout is never modified.

How to run
  python3 verify_intent_causal-frames-untested.py <root of the claude/rung-5-bundle checkout>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
SRC = os.path.join(ROOT, 'tools', 'den-bundle')

MUTANTS = {
    'A4': ('assemble.py', "    for oid in store.listed(track_id, t1):\n        o = store.obs[oid]",
           "    for oid in store.listed(track_id, None):\n        o = store.obs[oid]"),
    'A3': ('assemble.py', "for oid, statuses in store.listed(track_id, t1).items():",
           "for oid, statuses in store.listed(track_id, None).items():"),
    'G14': ('gate.py', "        if line.ts > t1 + EPS:\n            continue\n", ""),
    'A1': ('assemble.py', "in_effect = at_start[-1:] + [l for l in lines if l.ts > t0 + EPS]",
           "in_effect = [l for l in lines if l.ts > t0 + EPS]"),
}

WORKER = r'''
import json, sys
sys.dont_write_bytecode = True
d, mode, arg = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, d)
import method, records, synth, gate
from assemble import assemble
M = method.load_method()
ts = synth.ts
def stale():
    s = synth.stale_observation()
    return records.Store(s['observations'], s['tracks'])
if mode == 'bundle':
    b = assemble(stale(), M, 'A', ts(float(arg)))
    print(json.dumps(b))
elif mode == 'gate':
    b = json.load(open(arg))
    res = gate.check([b], stale(), M)
    print(json.dumps({'verdict': res['verdict'],
                      'fails': {k: v['failures'] for k, v in res['clauses'].items() if v['failures']}}))
elif mode == 'safe':
    s = synth.stale_observation()
    out = {'stale_8.5': assemble(records.Store(s['observations'], s['tracks']), M, 'A', ts(8.5))['association_safe']}
    c = synth.crossing()
    for line in c['tracks']:
        if line['association']['status'] == 'confident' and line['association']['since_ts'] == ts(7.5):
            line['association']['since_ts'] = ts(0.0)
    out['crossing_since0_10.2'] = assemble(records.Store(c['observations'], c['tracks']), M, 'A', ts(10.2))['association_safe']
    c = synth.crossing()
    out['crossing_asis_10.2'] = assemble(records.Store(c['observations'], c['tracks']), M, 'A', ts(10.2))['association_safe']
    print(json.dumps(out))
'''


def make(tmp, name):
    dst = os.path.join(tmp, name)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__'))
    if name in MUTANTS:
        fname, old, new = MUTANTS[name]
        p = os.path.join(dst, fname)
        text = open(p, encoding='utf-8').read()
        assert text.count(old) == 1, (name, text.count(old))
        open(p, 'w', encoding='utf-8', newline='\n').write(text.replace(old, new))
    return dst


def work(d, mode, arg):
    p = subprocess.run([sys.executable, '-B', '-c', WORKER, d, mode, arg], capture_output=True, text=True, timeout=300)
    if p.returncode:
        raise SystemExit('worker failed: %s' % p.stderr)
    return json.loads(p.stdout)


def rel(t):
    return round(t - 1793066400.0, 3)


def main():
    tmp = tempfile.mkdtemp(prefix='verify-r5-causal-')
    try:
        dirs = {n: make(tmp, n) for n in ('orig', 'A4', 'A3', 'G14', 'A1')}
        bundles = {}
        for n in ('orig', 'A4', 'A3'):
            b = work(dirs[n], 'bundle', '4.0')
            bundles[n] = b
            path = os.path.join(tmp, 'bundle-%s.json' % n)
            json.dump(b, open(path, 'w'))
            print('%-4s t1=4.0 frames %s' % (n, [rel(f['ts']) for f in b['frames']]))
            print('%-4s t1=4.0 keypoint ids %s' % (n, [k['obs_id'] for k in b['keypoint_series']]))
        a4_diff = [rel(f['ts']) for f in bundles['A4']['frames']] != [rel(f['ts']) for f in bundles['orig']['frames']]
        print('RESULT A4 %s: frames at t1=4.0 %s' % ('DIVERGES' if a4_diff else 'EQUAL',
              'include the 3.2 s frame whose track line is dated 5.0' if a4_diff else 'are the same'))
        for gate_of, bundle_of in (('orig', 'A4'), ('orig', 'A3'), ('orig', 'orig'), ('G14', 'orig'), ('G14', 'A3')):
            r = work(dirs[gate_of], 'gate', os.path.join(tmp, 'bundle-%s.json' % bundle_of))
            print('RESULT gate[%s] on bundle[%s] at t1=4.0: %s %s' % (gate_of, bundle_of, r['verdict'], r['fails']))
        for n in ('orig', 'A1'):
            print('RESULT %-4s association_safe %s' % (n, work(dirs[n], 'safe', '-')))
    finally:
        shutil.rmtree(tmp, True)


if __name__ == '__main__':
    main()

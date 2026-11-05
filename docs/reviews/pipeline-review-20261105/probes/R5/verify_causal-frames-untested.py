#!/usr/bin/env python3
"""verify_causal-frames-untested.py: are mutants A4, G14 and A1 real (non-equivalent) and missed by the suite?

What it checks
  Each mutant is applied to a temp copy of <checkout>/tools/den-bundle (the checkout
  is never modified). For each one:
    1. the tool's own suite on the mutant copy:  python3 -B -m unittest discover -s <copy>
       (survived = suite still OK);
    2. a synthetic input, built from the tool's own synth.py, on which the mutant's
       output differs from the unmutated code (so it is not an equivalent mutant):
       - A4  person_frames reads store.listed(track, None) instead of (track, t1).
             stale_observation, A, t1 = T0+4.0: the late result captured at 3.2 s is
             listed only by a line dated 5.0 s. Mutant frames include 3.2; real ones do
             not. Also: the unmutated gate passes the mutant bundle.
       - G14 gate._gated_ids stops skipping lines dated after t1. The real (causal)
             bundle at t1 = T0+4.0 fails the G14 gate's verbatim clause; a fully
             non-causal bundle (A3+A4) fails the real gate but passes the G14 gate.
       - A1  association_safe drops the line in effect at t0.
             (a) track_leaves, B, t1 = T0+23.5: no B line inside [20.5, 23.5], last line
                 20.0 confident -> real True, mutant False;
             (b) crossing with since_ts set to null, A, t1 = T0+10.2: the 7.0 line
                 (ambiguous) is in effect at t0 = 7.2, lines inside are confident ->
                 real False (part of the window was ambiguous), mutant True;
             (c) the finding's suggested test (ambiguous lines before t0, none inside)
                 -> both False, so that test would not kill A1.

How to run
  python3 verify_causal-frames-untested.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, temp dirs only, no network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

MUT = {
    'A4': [('assemble.py', "    for oid in store.listed(track_id, t1):\n        o = store.obs[oid]",
            "    for oid in store.listed(track_id, None):\n        o = store.obs[oid]")],
    'A3': [('assemble.py', "for oid, statuses in store.listed(track_id, t1).items():",
            "for oid, statuses in store.listed(track_id, None).items():")],
    'G14': [('gate.py', "        if line.ts > t1 + EPS:\n            continue\n", "")],
    'A1': [('assemble.py', "in_effect = at_start[-1:] + [l for l in lines if l.ts > t0 + EPS]",
            "in_effect = [l for l in lines if l.ts > t0 + EPS]")],
}

HELPER = r'''
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import synth, records, method, gate
from assemble import assemble
M = method.load_method()
ts = synth.ts
job = json.loads(sys.argv[2])
def store(scene):
    return records.Store(scene['observations'], scene['tracks'])
def scene_named(name, null_since=False, amb_only=False):
    if amb_only:
        lines = [synth.track_line('A', 1.0, 1.0, [synth.oid('A', t)], status='ambiguous', since=1.0) for t in (1.0, 1.5, 2.0)]
        obs = [synth.observation(synth.oid('A', t), t, 1.0, 1.0) for t in (1.0, 1.5, 2.0)]
        return {'observations': obs, 'tracks': lines}
    s = synth.SCENES[name]()
    if null_since:
        for l in s['tracks']:
            l['association']['since_ts'] = None
    return s
out = {}
if job['op'] == 'bundle':
    s = scene_named(job['scene'], job.get('null_since', False), job.get('amb_only', False))
    b = assemble(store(s), M, job['track'], ts(job['t1']))
    out = b
elif job['op'] == 'gate':
    s = scene_named(job['scene'])
    res = gate.check([job['bundle']], store(s), M)
    out = {'verdict': res['verdict'], 'failures': {k: v['failures'] for k, v in res['clauses'].items() if v['failures']}}
print(json.dumps(out))
'''


def make_copy(root, ids, base):
    dst = os.path.join(base, '+'.join(ids) or 'real', 'den-bundle')
    shutil.copytree(os.path.join(root, 'tools', 'den-bundle'), dst, ignore=shutil.ignore_patterns('__pycache__'))
    for mid in ids:
        for fname, old, new in MUT[mid]:
            p = os.path.join(dst, fname)
            text = open(p, encoding='utf-8').read()
            if text.count(old) != 1:
                raise SystemExit('FAIL mutant %s pattern matched %d times in %s' % (mid, text.count(old), fname))
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text.replace(old, new))
    return dst


def call(copy_dir, job, helper):
    p = subprocess.run([sys.executable, '-B', helper, copy_dir, json.dumps(job)], capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise SystemExit('FAIL helper error: %s' % p.stderr[-2000:])
    return json.loads(p.stdout)


def suite(copy_dir):
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', copy_dir],
                       cwd=os.path.dirname(copy_dir), capture_output=True, text=True, timeout=600)
    tail = [l for l in p.stderr.strip().splitlines() if l.startswith(('Ran ', 'OK', 'FAILED'))]
    return p.returncode, ' / '.join(tail)


def rel(t):
    return round(t - 1793066400.0, 3)


def main(root):
    base = tempfile.mkdtemp(prefix='verify-r5-causal-')
    helper = os.path.join(base, 'helper.py')
    with open(helper, 'w') as f:
        f.write(HELPER)
    bad = []
    try:
        real = make_copy(root, [], base)
        copies = {k: make_copy(root, [k], base) for k in ('A4', 'G14', 'A1')}
        a3a4 = make_copy(root, ['A3', 'A4'], base)

        rc, tail = suite(real)
        print('RESULT baseline suite on unmutated copy: rc=%d %s' % (rc, tail))
        for mid in ('A4', 'G14', 'A1'):
            rc, tail = suite(copies[mid])
            print('RESULT suite on mutant %-4s: %s (rc=%d) %s' % (mid, 'SURVIVED' if rc == 0 else 'caught', rc, tail))

        # A4
        job = {'op': 'bundle', 'scene': 'stale_observation', 'track': 'A', 't1': 4.0}
        rb, mb = call(real, job, helper), call(copies['A4'], job, helper)
        rf, mf = [rel(f['ts']) for f in rb['frames']], [rel(f['ts']) for f in mb['frames']]
        diff = rf != mf
        print('%s A4 non-equivalent: real frames %s ; mutant frames %s' % ('PASS' if diff else 'FAIL', rf, mf))
        g = call(real, {'op': 'gate', 'scene': 'stale_observation', 'bundle': mb}, helper)
        print('INFO A4 mutant bundle under the unmutated gate: verdict %s %s' % (g['verdict'], g['failures']))
        bad += [] if diff else ['A4']

        # G14
        g_real_on_real = call(real, {'op': 'gate', 'scene': 'stale_observation', 'bundle': rb}, helper)
        g14_on_real = call(copies['G14'], {'op': 'gate', 'scene': 'stale_observation', 'bundle': rb}, helper)
        nc = call(a3a4, job, helper)
        g_real_on_nc = call(real, {'op': 'gate', 'scene': 'stale_observation', 'bundle': nc}, helper)
        g14_on_nc = call(copies['G14'], {'op': 'gate', 'scene': 'stale_observation', 'bundle': nc}, helper)
        print('INFO G14 causal bundle t1=4.0: real gate %s ; G14 gate %s %s' % (
            g_real_on_real['verdict'], g14_on_real['verdict'], g14_on_real['failures']))
        print('INFO G14 non-causal (A3+A4) bundle t1=4.0: real gate %s %s ; G14 gate %s %s' % (
            g_real_on_nc['verdict'], g_real_on_nc['failures'], g14_on_nc['verdict'], g14_on_nc['failures']))
        g14_diff = (g_real_on_real['verdict'] != g14_on_real['verdict']) or (g_real_on_nc['verdict'] != g14_on_nc['verdict'])
        print('%s G14 non-equivalent (changes a gate verdict)' % ('PASS' if g14_diff else 'FAIL'))
        bad += [] if g14_diff else ['G14']

        # A1
        cases = [
            ('(a) track_leaves B t1=23.5, no line inside window', {'op': 'bundle', 'scene': 'track_leaves', 'track': 'B', 't1': 23.5}),
            ('(b) crossing since_ts=null A t1=10.2, ambiguous line in effect at t0', {'op': 'bundle', 'scene': 'crossing', 'track': 'A', 't1': 10.2, 'null_since': True}),
            ("(c) finding's suggested test: ambiguous lines before t0, none inside", {'op': 'bundle', 'scene': None, 'track': 'A', 't1': 6.0, 'amb_only': True}),
        ]
        a1_diff = False
        for label, job in cases:
            r, m = call(real, job, helper)['association_safe'], call(copies['A1'], job, helper)['association_safe']
            a1_diff = a1_diff or (r != m)
            print('INFO A1 %s: real association_safe=%s, mutant=%s -> %s' % (label, r, m, 'differs' if r != m else 'same'))
        print('%s A1 non-equivalent' % ('PASS' if a1_diff else 'FAIL'))
        bad += [] if a1_diff else ['A1']
    finally:
        shutil.rmtree(base, True)
    print('RESULT %s' % ('CONFIRMED: A4, G14, A1 change outputs and the suite misses them' if not bad else 'NOT CONFIRMED for %s' % bad))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))

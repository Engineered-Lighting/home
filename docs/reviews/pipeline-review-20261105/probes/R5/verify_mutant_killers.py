#!/usr/bin/env python3
"""verify_mutant_killers.py: are den-bundle's surviving gate mutants G3-G13 real (non-equivalent)?

What it checks
  For each mutant id (same text replacements as mutate_den_bundle_validate.py):
  1. copies <checkout>/tools/den-bundle to a fresh temp folder, applies the one
     replacement (must match exactly once), runs the tool's own suite there
         python3 -B -m unittest discover -s <copy> -f
     and reports "Ran N tests" and OK/FAILED (so a survivor is not a 0-test run);
  2. runs one small "killer" scenario (synthetic, built from the tool's own
     synth.py fixtures) against an unmutated copy and against the mutated copy,
     and prints the clause result for each. A mutant is non-equivalent when the
     original gate fails the faulty input and the mutated gate passes it.
  The checkout is never modified; everything runs in temp folders.

How to run
  python3 verify_mutant_killers.py <root of the claude/rung-5-bundle checkout> [mutant-id ...]

Synthetic data only, no network.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MUTANTS = {
    'G3': ('gate.py', "'keypoints': o.get('keypoints'), ", ""),
    'G4': ('gate.py', "'transform_id': o.get('transform_id'),", ""),
    'G5': ('gate.py', "expect = {'ts': o['capture_ts'], 'camera_id': camera_of(o), ", "expect = {"),
    'G6': ('gate.py', "and is_valid(o) and st == {'confident'}", "and is_valid(o) and 'confident' in st"),
    'G7': ('gate.py', "and is_valid(o) and st == {'confident'}", "and st == {'confident'}"),
    'G8': ('gate.py', "'object_box': any(ob['room_position'] is None for ob in b['objects']),", "'object_box': False,"),
    'G9': ('gate.py', "return not any(str(j.get('lift') or '').startswith('plane') for j in o.get('joints_room') or [])",
           "return True"),
    'G10': ('gate.py', "if abs((t1 - t0) - method['window_s']) > EPS:", "if (t1 - t0) - method['window_s'] > EPS:"),
    'G11': ('gate.py', "elif abs(b['interval'][1] - r['sent_ts']) > EPS:", "elif r['sent_ts'] - b['interval'][1] > EPS:"),
    'G12': ('gate.py', "if prev.get('done_ts') is None or r['sent_ts'] < prev['done_ts'] - EPS:",
            "if prev.get('done_ts') is not None and r['sent_ts'] < prev['done_ts'] - EPS:"),
    'G13': ('gate.py', "if prev.get('outcome') != 'ok' and prev.get('done_ts') is not None \\",
            "if prev.get('outcome') == 'refused' and prev.get('done_ts') is not None \\"),
}

KILLERS = r'''
import copy, json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import fakes, gate, method, records, synth
from assemble import assemble
from router import Router, run
M, R = method.load_method(), method.load_router()
ts = synth.ts
which = sys.argv[2]

def routed(scene, start=2.0, until=30.0, script=None):
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(ts(start)); sender = fakes.FakeSender(clock, script=script)
    r = Router(st, M, R, clock, sender); recs = run(r, clock, sender, ts(until))
    return st, r.bundles, recs

def res(bundles, st, clause, requests=None, rule=None):
    out = gate.check(bundles, st, M, (), requests, rule=rule)
    return {'verdict': out['verdict'], clause: out['clauses'][clause]['failures'][:2]}

out = {}
if which in ('G3', 'G4', 'G5'):
    st, b, recs = routed(synth.two_tracks())
    b = copy.deepcopy(b)
    k = b[0]['keypoint_series']
    if which == 'G3':
        k[0]['keypoints'][0][0] += 25.0
    elif which == 'G4':
        k[0]['transform_id'] = 'den-cam-align@2026-10-18'
    else:
        k[0]['ts'] = k[1]['ts']          # moved onto another existing frame of the same camera
    out = res(b, st, 'verbatim')
elif which in ('G6', 'G7'):
    scene = synth.two_tracks()
    if which == 'G6':
        # one in-window observation of A is also named by an ambiguous line of A
        target = synth.oid('A', 9.0)
        extra = synth.track_line('A', 1.95, 0.95, [target], status='ambiguous', since=9.0)
        i = [n for n, l in enumerate(scene['tracks']) if l['last_obs_ids'] == [target]][0]
        scene['tracks'].insert(i + 1, extra)
    st, b, recs = routed(scene)
    bundle = [x for x in b if x['track_id'] == 'A' and x['interval'][0] <= ts(9.0) <= x['interval'][1]][0]
    bundle = copy.deepcopy(bundle)
    if which == 'G6':
        o = st.obs[synth.oid('A', 9.0)]
        assert all(e['obs_id'] != o['obs_id'] for e in bundle['keypoint_series'])
    else:
        o = st.obs[bundle['keypoint_series'][0]['obs_id']]
        o['validity'] = 'rejected:out_of_frame'          # the assembler kept a rejected observation
        bundle['keypoint_series'] = bundle['keypoint_series'][1:]
    bundle['keypoint_series'].append({'obs_id': o['obs_id'], 'ts': o['capture_ts'], 'camera_id': 'den-oak',
                                      'transform_id': o['transform_id'], 'keypoints': o['keypoints'],
                                      'joints_room': o['joints_room']})
    bundle['keypoint_series'].sort(key=lambda e: e['ts'])
    out = res([bundle], st, 'verbatim')
elif which == 'G8':
    st, b, recs = routed(synth.two_tracks())
    b = copy.deepcopy(b[:1])
    b[0]['objects'] = [{'label': 'cutting board', 'relation': 'using', 'room_position': None}]
    out = res(b, st, 'missing')
elif which == 'G9':
    scene = synth.two_tracks()
    target = synth.oid('A', 9.0)
    o = [x for x in scene['observations'] if x['obs_id'] == target][0]
    o['joints_room'][9]['lift'] = 'plane:0.95'          # one joint on a plane; depth_ts on time (33 ms)
    st, b, recs = routed(scene)
    bundle = copy.deepcopy([x for x in b if any(e['obs_id'] == target for e in x['keypoint_series'])][0])
    assert 'depth' in bundle['missing'], bundle['missing']
    bundle['missing'] = [m for m in bundle['missing'] if m != 'depth']
    out = res([bundle], st, 'missing')
    out['depth_ts_minus_capture'] = round(o['depth_ts'] - o['capture_ts'], 3)
elif which == 'G10':
    st, b, recs = routed(synth.two_tracks())
    bundle = copy.deepcopy(b[0])
    t0 = bundle['interval'][0] + 0.5                     # 2.5 s, not 3.0 s
    keep = [i for i, f in enumerate(bundle['frames']) if f['ts'] >= t0 - 1e-6]
    bundle['interval'][0] = t0
    bundle['frames'] = [bundle['frames'][i] for i in keep]
    for other in bundle['others']:
        other['image_box'] = [other['image_box'][i] for i in keep]
    bundle['keypoint_series'] = [e for e in bundle['keypoint_series'] if e['ts'] >= t0 - 1e-6]
    bundle['positions'] = [p for p in bundle['positions'] if p['ts'] >= t0 - 1e-6]
    out = res([bundle], st, 'window')
elif which == 'G11':
    st, b, recs = routed(synth.two_tracks())
    r = copy.deepcopy(recs)
    r[2]['sent_ts'] -= 0.5                                # the bundle ends 0.5 s after its send
    out = res(b, st, 'no_wait', requests=r)
elif which in ('G12', 'G13'):
    stamp = R.stamp()
    base = lambda i, t, outcome, done: {'request_id': 'rq-%d' % i, 'track_id': 'A', 'tracks': ['A'], 'sent_ts': t,
                                        'done_ts': done, 'outcome': outcome, 'rule': stamp}
    if which == 'G12':
        r = [base(1, 0.0, None, None), base(2, 4.0, 'ok', 5.25)]   # rq-1 never answered, rq-2 started anyway
        out = {'in_flight': gate.check_requests(r, R, ['A'])['in_flight']}
    else:
        out = {}
        for bad in ('error', 'timeout'):
            r = [base(1, 0.0, bad, 1.25), base(2, 6.25, 'ok', 7.5)]  # 5 s after an error/timeout, not 30 s
            out[bad] = gate.check_requests(r, R, ['A'])['backoff']
print(json.dumps(out))
'''


def run_suite(dst, cwd):
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', dst, '-f'],
                       cwd=cwd, capture_output=True, text=True, timeout=600)
    ran = re.findall(r'^Ran (\d+) tests?', p.stderr, re.M)
    last = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''
    return p.returncode, (ran[-1] if ran else '?'), last


def killer(tool_dir, mid, helper):
    p = subprocess.run([sys.executable, '-B', helper, tool_dir, mid], capture_output=True, text=True, timeout=300)
    if p.returncode:
        return 'ERROR ' + p.stderr.strip().splitlines()[-1]
    return p.stdout.strip()


def main():
    root = sys.argv[1]
    ids = sys.argv[2:] or sorted(MUTANTS, key=lambda s: int(s[1:]))
    src = os.path.join(root, 'tools', 'den-bundle')
    tmp = tempfile.mkdtemp(prefix='verify-r5-mut-')
    nonequiv = survived = 0
    try:
        helper = os.path.join(tmp, 'killers.py')
        with open(helper, 'w', encoding='utf-8') as f:
            f.write(KILLERS)
        orig = os.path.join(tmp, 'orig', 'den-bundle')
        shutil.copytree(src, orig, ignore=shutil.ignore_patterns('__pycache__'))
        for mid in ids:
            fname, old, new = MUTANTS[mid]
            dst = os.path.join(tmp, mid, 'den-bundle')
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__'))
            path = os.path.join(dst, fname)
            with open(path, encoding='utf-8') as f:
                text = f.read()
            if text.count(old) != 1:
                print('%-4s not-run: pattern matched %d times' % (mid, text.count(old)))
                continue
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text.replace(old, new))
            code, ran, last = run_suite(dst, os.path.join(tmp, mid))
            k_orig, k_mut = killer(orig, mid, helper), killer(dst, mid, helper)
            surv = code == 0
            survived += surv
            differs = k_orig != k_mut
            nonequiv += differs
            print('%-4s suite: %s (Ran %s tests, last line %r)' % (mid, 'survived' if surv else 'caught', ran, last))
            print('     killer, original gate: %s' % k_orig)
            print('     killer, mutated gate:  %s' % k_mut)
            print('     %s' % ('NON-EQUIVALENT (original rejects, mutant differs)' if differs else 'no behavioural difference found'),
                  flush=True)
    finally:
        shutil.rmtree(tmp, True)
    print('RESULT %s: %d mutants run, %d survived the suite, %d shown non-equivalent' % (
        'FAIL' if survived else 'PASS', len(ids), survived, nonequiv))
    return 1 if survived else 0


if __name__ == '__main__':
    sys.exit(main())

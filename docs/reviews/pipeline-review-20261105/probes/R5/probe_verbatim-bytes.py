"""Probe: byte-compare keypoint_series entries with their Observations, and test whether the gate's
`verbatim` clause compares bytes or only Python equality.

What it checks
  1) For every fixture, route through the CLI, read the bundles file back, and for every
     keypoint_series entry compare json.dumps(sort_keys) of obs_id, ts (capture_ts), camera_id
     (source.sensor_id), transform_id, keypoints and joints_room with the observation line.
  2) Tamper one bundle so keypoints scores become JSON true/1.0 style values that are == in
     Python but differ in bytes (0/1 ints -> floats, 1 -> true), and an int -> float in
     joints_room, then run gate.check: a byte-verbatim gate must flag it.

How to run
  python3 probe_verbatim-bytes.py <root of the branch checkout>
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, TOOL)
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402

CLI = [sys.executable, '-B', os.path.join(TOOL, 'den_bundle.py')]
spans = {'crossing': ('2.5', '13'), 'missing_depth': ('2', '8'), 'stale_observation': ('2', '9'),
         'two_tracks': ('2', '70'), 'track_leaves': ('2', '40')}
entries = mismatched = 0
with tempfile.TemporaryDirectory() as d:
    subprocess.run(CLI + ['fixtures', '--out', d], capture_output=True, check=True)
    for name, (a, b) in spans.items():
        o, t = os.path.join(d, name, 'observations.jsonl'), os.path.join(d, name, 'tracks.jsonl')
        rq, bd = os.path.join(d, name + '_rq.jsonl'), os.path.join(d, name + '_bd.jsonl')
        subprocess.run(CLI + ['route', '--observations', o, '--tracks', t, '--from', a, '--to', b,
                              '--requests', rq, '--bundles', bd], capture_output=True, check=True)
        obs = {x['obs_id']: x for x in records.read_jsonl(o)}
        for bundle in records.read_jsonl(bd):
            for k in bundle['keypoint_series']:
                ob = obs[k['obs_id']]
                want = {'obs_id': ob['obs_id'], 'ts': ob['capture_ts'], 'camera_id': ob['source']['sensor_id'],
                        'transform_id': ob['transform_id'], 'keypoints': ob['keypoints'], 'joints_room': ob['joints_room']}
                for f, v in want.items():
                    entries += 1
                    if json.dumps(k.get(f), sort_keys=True) != json.dumps(v, sort_keys=True):
                        mismatched += 1
print('RESULT byte compare over all routed fixture bundles: %d field copies, %d differ in bytes' % (entries, mismatched))
print('PASS byte-verbatim copies' if mismatched == 0 else 'FAIL byte-verbatim copies')

# 2) the gate's own comparison
sc = synth.missing_depth()
st = records.Store(sc['observations'], sc['tracks'])
M = method.load_method()
from assemble import assemble  # noqa: E402
b = assemble(st, M, 'A', synth.ts(3.0))
t = copy.deepcopy(b)
k = t['keypoint_series'][0]
k['joints_room'][0]['xyz'][2] = int(k['joints_room'][0]['xyz'][2]) if float(k['joints_room'][0]['xyz'][2]).is_integer() else k['joints_room'][0]['xyz'][2]
k['joints_room'][5]['cov'][0][1] = 0.0          # int 0 in the observation -> float 0.0
k['joints_room'][5]['cov'][1][0] = False        # int 0 -> JSON false
same_python = k['joints_room'] == st.obs[k['obs_id']]['joints_room']
diff_bytes = json.dumps(k['joints_room']) != json.dumps(st.obs[k['obs_id']]['joints_room'])
res = gate.check([t], st, M)
v = res['clauses']['verbatim']['failures']
print('RESULT tampered entry: python == %s, bytes differ %s, gate verbatim failures %d' % (same_python, diff_bytes, len(v)))
print('FAIL gate verbatim accepts a byte-different copy (0 -> 0.0, 0 -> false)' if diff_bytes and not v
      else 'PASS gate verbatim is byte-strict')

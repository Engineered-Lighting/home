"""Verify (reproduce lens): the gate's `verbatim` clause compares copied fields with Python ==,
so a keypoint_series entry whose JSON turns an integer 0 into 0.0 or false (or 1 into true) passes.

What it checks
  Through the tool's own CLI and files (so JSON types are what a later serializer would write):
  fixtures -> route missing_depth (2..8 s) -> read bundles.jsonl. In the first bundle that has a
  keypoint entry, rewrite one joints_room cov element that is integer 0 in the observation:
    variant 'float': 0 -> 0.0, variant 'bool': 0 -> false.
  Writes each tampered bundles file and runs `den_bundle.py gate` on it; prints the exit code
  and the verbatim line, and confirms the tampered JSON text differs from the observation's.

How to run
  python3 verify_verbatim-clause-type-lax.py <root of the branch checkout>
  Synthetic fixtures, temp dirs only; the checkout is never modified.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, TOOL)
import records  # noqa: E402

CLI = [sys.executable, '-B', os.path.join(TOOL, 'den_bundle.py')]
with tempfile.TemporaryDirectory() as d:
    subprocess.run(CLI + ['fixtures', '--out', d], capture_output=True, check=True)
    o, t = os.path.join(d, 'missing_depth', 'observations.jsonl'), os.path.join(d, 'missing_depth', 'tracks.jsonl')
    rq, bd = os.path.join(d, 'rq.jsonl'), os.path.join(d, 'bd.jsonl')
    subprocess.run(CLI + ['route', '--observations', o, '--tracks', t, '--from', '2', '--to', '8',
                          '--requests', rq, '--bundles', bd], capture_output=True, check=True)
    base = subprocess.run(CLI + ['gate', '--observations', o, '--tracks', t, '--bundles', bd],
                          capture_output=True, text=True)
    print('RESULT untampered gate exit %d' % base.returncode)
    obs = {x['obs_id']: x for x in records.read_jsonl(o)}
    for variant, value in (('float', 0.0), ('bool', False)):
        bundles = records.read_jsonl(bd)
        b = next(x for x in bundles if x['keypoint_series'])
        k = b['keypoint_series'][0]
        orig = obs[k['obs_id']]['joints_room']
        j = next(i for i, jt in enumerate(orig) if jt.get('cov'))
        assert type(orig[j]['cov'][0][1]) is int and orig[j]['cov'][0][1] == 0
        k['joints_room'][j]['cov'][0][1] = value
        tf = os.path.join(d, 'bd_%s.jsonl' % variant)
        records.write_jsonl(tf, bundles)
        back = next(x for x in records.read_jsonl(tf) if x['bundle_id'] == b['bundle_id'])['keypoint_series'][0]
        text_differs = json.dumps(back['joints_room'], sort_keys=True) != json.dumps(orig, sort_keys=True)
        g = subprocess.run(CLI + ['gate', '--observations', o, '--tracks', t, '--bundles', tf],
                           capture_output=True, text=True)
        vline = next(l for l in g.stdout.splitlines() if l.startswith('verbatim'))
        print('RESULT %s: cov[0][1] in file is %s (type %s); JSON text differs from the observation: %s; '
              'gate exit %d; %s' % (variant, json.dumps(back['joints_room'][j]['cov'][0][1]),
                                     type(back['joints_room'][j]['cov'][0][1]).__name__, text_differs,
                                     g.returncode, vline.strip()))
        print(('FAIL gate passes a non-verbatim copy (%s)' % variant) if text_differs and g.returncode == 0
              else 'PASS gate rejects the %s copy' % variant)

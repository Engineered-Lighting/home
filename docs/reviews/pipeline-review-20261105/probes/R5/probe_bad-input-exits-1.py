"""Probe: malformed records make `den_bundle.py gate` crash with a traceback and exit 1,
which the README defines as "the gate failed", instead of exit 2 "refused or bad input".

What it checks (synthetic files in a temp dir, real command line):
  a) a request record without `tracks`          (e.g. a log from another tool version)
  b) a bundle record without `others`
  c) a crossing record without `interval`
  d) a track line whose last_obs_ids is a string, not a list
  e) a request with an unknown track in `tracks`
For each: exit code, and whether stderr is a Python traceback.
PASS means exit 2 with a 'refused:' line for every case.

How to run
  python3 probe_bad-input-exits-1.py <root of the branch checkout>
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, TOOL)
import records  # noqa: E402

CLI = [sys.executable, '-B', os.path.join(TOOL, 'den_bundle.py')]
bad = 0
with tempfile.TemporaryDirectory() as d:
    fx = os.path.join(d, 'fx')
    subprocess.run(CLI + ['fixtures', '--out', fx], capture_output=True, check=True)
    obs, tr, cr = (os.path.join(fx, 'crossing', n) for n in ('observations.jsonl', 'tracks.jsonl', 'crossings.jsonl'))
    rq, bd = os.path.join(d, 'rq.jsonl'), os.path.join(d, 'bd.jsonl')
    subprocess.run(CLI + ['route', '--observations', obs, '--tracks', tr, '--from', '2.5', '--to', '13',
                          '--requests', rq, '--bundles', bd], capture_output=True, check=True)
    R, B, C, T = records.read_jsonl(rq), records.read_jsonl(bd), records.read_jsonl(cr), records.read_jsonl(tr)

    def case(name, reqs=R, bundles=B, crossings=C, tracks=T):
        p = lambda n: os.path.join(d, name + n)  # noqa: E731
        records.write_jsonl(p('r'), reqs)
        records.write_jsonl(p('b'), bundles)
        records.write_jsonl(p('c'), crossings)
        records.write_jsonl(p('t'), tracks)
        g = subprocess.run(CLI + ['gate', '--observations', obs, '--tracks', p('t'), '--bundles', p('b'),
                                  '--requests', p('r'), '--crossings', p('c')], capture_output=True, text=True)
        tb = 'Traceback' in g.stderr
        last = (g.stderr.strip().splitlines() or [''])[-1]
        print('RESULT %s: exit %d, traceback %s, last stderr line %r' % (name, g.returncode, tb, last))
        return g.returncode

    import copy
    r = copy.deepcopy(R); del r[0]['tracks']
    b = copy.deepcopy(B); del b[0]['others']
    c = copy.deepcopy(C); del c[0]['interval']
    t = copy.deepcopy(T); t[3]['last_obs_ids'] = t[3]['last_obs_ids'][0]
    e = copy.deepcopy(R); e[1]['tracks'] = ['Z']; e[1]['track_id'] = 'Z'
    results = {'a_request_without_tracks': case('a', reqs=r), 'b_bundle_without_others': case('b', bundles=b),
               'c_crossing_without_interval': case('c', crossings=c), 'd_last_obs_ids_string': case('d', tracks=t),
               'e_unknown_track_in_request': case('e', reqs=e)}
    for name, code in results.items():
        if code != 2:
            bad += 1
            print('FAIL %s: exit %d (README: 1 = gate failed, 2 = bad input)' % (name, code))
print('OVERALL', 'FAIL' if bad else 'PASS')

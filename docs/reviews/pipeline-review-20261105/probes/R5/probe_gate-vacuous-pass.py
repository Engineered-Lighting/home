"""Probe: rung 5's gate (den_bundle.py gate) passes, exit 0, when nothing was checked.

What it checks, each through the real command line on synthetic files in a temp dir:
  a) empty bundles and empty requests files            -> expect refusal, not 'verdict: pass'
  b) observations and tracks from different sessions   -> route writes 0 requests (exit 0), gate passes
     (every track line is 'undated' and silently dropped; Store.undated is never reported)
  c) crossing annotation whose track_ids are the annotator's names, not the tracker's ids
     -> crossing clause 'checked 0', verdict pass
  d) gate run without --crossings on the crossing fixture -> crossing 'checked 0', verdict pass
  e) the README's own crossing replay (--from 2.5 --to 13): which annotated tracks get a bundle
     that meets the crossing (A gets none: its bundles end at 2.5 and 10.5)
  f) a tracker that never writes association.status (rung 3's draft core keeps a float
     `confidence`, no association) -> every bundle association_safe false, keypoint_series empty,
     gate passes every clause
How to run
  python3 probe_gate-vacuous-pass.py <root of the branch checkout>
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
import records  # noqa: E402
import synth    # noqa: E402

CLI = [sys.executable, '-B', os.path.join(TOOL, 'den_bundle.py')]


def run(args):
    p = subprocess.run(CLI + args, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def short(out):
    keep = [l for l in out.splitlines() if 'checked' in l or 'verdict' in l or 'requests' in l]
    return ' | '.join(' '.join(l.split()) for l in keep)


fails = 0
with tempfile.TemporaryDirectory() as d:
    p = lambda name: os.path.join(d, name)  # noqa: E731
    sc = synth.crossing()
    records.write_jsonl(p('obs.jsonl'), sc['observations'])
    records.write_jsonl(p('tracks.jsonl'), sc['tracks'])
    records.write_jsonl(p('cross.jsonl'), sc['crossings'])

    # a) empty bundles and requests
    open(p('empty_b.jsonl'), 'w').close()
    open(p('empty_r.jsonl'), 'w').close()
    code, out, err = run(['gate', '--observations', p('obs.jsonl'), '--tracks', p('tracks.jsonl'),
                          '--bundles', p('empty_b.jsonl'), '--requests', p('empty_r.jsonl'),
                          '--crossings', p('cross.jsonl')])
    print('RESULT a) empty inputs: exit %d: %s' % (code, short(out)))
    if code == 0:
        fails += 1
        print('FAIL a) empty bundles/requests give verdict pass')

    # b) observations of one session, tracks of another (ids never match)
    other = synth.two_tracks()
    for o in other['observations']:
        o['obs_id'] = 's2-' + o['obs_id']        # another session: no obs_id in common
    records.write_jsonl(p('obs_other.jsonl'), other['observations'])
    code, out, err = run(['route', '--observations', p('obs_other.jsonl'), '--tracks', p('tracks.jsonl'),
                          '--from', '2', '--to', '60', '--requests', p('rb.jsonl'), '--bundles', p('bb.jsonl')])
    nreq = sum(1 for _ in open(p('rb.jsonl')))
    print('RESULT b) mismatched files: route exit %d, %d requests, stdout %r, stderr %r' % (code, nreq, out, err))
    code2, out2, _ = run(['gate', '--observations', p('obs_other.jsonl'), '--tracks', p('tracks.jsonl'),
                          '--bundles', p('bb.jsonl'), '--requests', p('rb.jsonl'), '--crossings', p('cross.jsonl')])
    print('RESULT b) gate exit %d: %s' % (code2, short(out2)))
    if code == 0 and code2 == 0:
        fails += 1
        print('FAIL b) mismatched observation/track files: route exits 0 with 0 requests and the gate passes')

    # route the README's crossing replay for c), d), e)
    code, out, _ = run(['route', '--observations', p('obs.jsonl'), '--tracks', p('tracks.jsonl'),
                        '--from', '2.5', '--to', '13', '--requests', p('rq.jsonl'), '--bundles', p('bd.jsonl')])
    bundles = records.read_jsonl(p('bd.jsonl'))
    c0, c1 = sc['crossings'][0]['interval']
    meets = {}
    for b in bundles:
        t0, t1 = b['interval']
        meets.setdefault(b['track_id'], []).append((round(t1 - synth.T0, 1), t0 <= c1 and c0 <= t1))
    print('RESULT e) README crossing replay: bundle end times and whether each meets the crossing: %s' % meets)
    unmet = [tid for tid in sc['crossings'][0]['track_ids'] if not any(m for _, m in meets.get(tid, []))]
    if unmet:
        fails += 1
        print('FAIL e) annotated track(s) %s have no bundle meeting the crossing; the clause is never checked for them' % unmet)

    # c) annotation names people, not tracker ids
    records.write_jsonl(p('cross_names.jsonl'), [{'crossing_id': 'x1', 'track_ids': ['person-1', 'person-2'],
                                                 'interval': [c0, c1]}])
    # make the tracker miss the crossing entirely so a correct gate must FAIL
    tr2 = json.loads(json.dumps(sc['tracks']))
    for l in tr2:
        l['association'] = {'status': 'confident', 'hypotheses': [], 'since_ts': synth.ts(0.0)}
    records.write_jsonl(p('tracks_conf.jsonl'), tr2)
    run(['route', '--observations', p('obs.jsonl'), '--tracks', p('tracks_conf.jsonl'), '--from', '2.5', '--to', '13',
         '--requests', p('rq2.jsonl'), '--bundles', p('bd2.jsonl')])
    code, out, _ = run(['gate', '--observations', p('obs.jsonl'), '--tracks', p('tracks_conf.jsonl'),
                        '--bundles', p('bd2.jsonl'), '--requests', p('rq2.jsonl'), '--crossings', p('cross.jsonl')])
    print('RESULT c0) tracker misses the crossing, annotation uses tracker ids: exit %d: %s' % (code, short(out)))
    code, out, _ = run(['gate', '--observations', p('obs.jsonl'), '--tracks', p('tracks_conf.jsonl'),
                        '--bundles', p('bd2.jsonl'), '--requests', p('rq2.jsonl'), '--crossings', p('cross_names.jsonl')])
    print('RESULT c) same, annotation names that match no track: exit %d: %s' % (code, short(out)))
    if code == 0:
        fails += 1
        print('FAIL c) a tracker that misses the crossing passes when the annotation ids match no track')

    # d) no --crossings at all
    code, out, _ = run(['gate', '--observations', p('obs.jsonl'), '--tracks', p('tracks_conf.jsonl'),
                        '--bundles', p('bd2.jsonl'), '--requests', p('rq2.jsonl')])
    print('RESULT d) same, no --crossings: exit %d: %s' % (code, short(out)))
    if code == 0:
        fails += 1
        print('FAIL d) gate passes with no crossings file although the ladder clause needs annotated crossings')

    # f) tracker with no association field at all
    tr3 = json.loads(json.dumps(sc['tracks']))
    for l in tr3:
        l.pop('association', None)
        l['confidence'] = 0.97
    records.write_jsonl(p('tracks_noassoc.jsonl'), tr3)
    run(['route', '--observations', p('obs.jsonl'), '--tracks', p('tracks_noassoc.jsonl'), '--from', '2', '--to', '12',
         '--requests', p('rq3.jsonl'), '--bundles', p('bd3.jsonl')])
    b3 = records.read_jsonl(p('bd3.jsonl'))
    code, out, _ = run(['gate', '--observations', p('obs.jsonl'), '--tracks', p('tracks_noassoc.jsonl'),
                        '--bundles', p('bd3.jsonl'), '--requests', p('rq3.jsonl'), '--crossings', p('cross.jsonl')])
    print('RESULT f) no association field: %d bundles, association_safe %s, keypoint entries %d, missing %s; gate exit %d: %s' % (
        len(b3), sorted({b['association_safe'] for b in b3}), sum(len(b['keypoint_series']) for b in b3),
        sorted({tuple(b['missing']) for b in b3}), code, short(out)))
    if code == 0:
        fails += 1
        print('FAIL f) every bundle unsafe and empty keypoint_series, yet the gate passes')

print('OVERALL', 'FAIL (%d vacuous passes)' % fails if fails else 'PASS')

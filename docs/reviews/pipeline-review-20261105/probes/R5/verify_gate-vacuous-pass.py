#!/usr/bin/env python3
"""verify_gate-vacuous-pass.py: does rung 5's gate pass when it checked nothing?

What it checks (independently of probe_gate_vacuous_pass.py, calling gate.check directly)
  1. scored check (every slot closed at its proposed value, in memory) on an
     empty bundle list, no requests, no crossings -> verdict?
  2. practice check on the crossing fixture routed by the tool while the
     tracker stayed confident (so association_safe is true at the crossing),
     with the crossing written in relative seconds [5.0, 7.0] (the way the
     command line's --at/--from/--to accept times) -> crossing 'checked' and verdict;
     the same with the fixture's Unix-time crossing -> verdict.
  3. track lines naming no observation in the observations file: Store.undated
     and whether den_bundle.py's route output mentions it.

How to run
  python3 verify_gate-vacuous-pass.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network; temp files only.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import den_bundle  # noqa: E402
import fakes       # noqa: E402
import gate        # noqa: E402
import method      # noqa: E402
import records     # noqa: E402
import router      # noqa: E402
import synth       # noqa: E402

M, R = method.load_method(), method.load_router()


def closed(settings, maker):
    return maker({k: (v['proposed'] if method.is_open(v) else v) for k, v in settings.block.items()})


def main():
    bad = 0
    scene = synth.crossing()
    st = records.Store(scene['observations'], scene['tracks'])
    res = gate.check([], st, closed(M, method.method_from), (), None, scored=True, rule=closed(R, method.router_from))
    print('%s 1. scored, slots closed, 0 bundles: verdict %s, practice %s, checked %s' % (
        'FAIL' if res['verdict'] == 'pass' else 'PASS', res['verdict'], res['practice'],
        {k: v['checked'] for k, v in res['clauses'].items()}))
    bad += res['verdict'] == 'pass'

    sc = synth.crossing()
    for line in sc['tracks']:
        line['association'] = {'status': 'confident', 'hypotheses': [], 'since_ts': synth.ts(0.0)}
    st2 = records.Store(sc['observations'], sc['tracks'])
    clock = fakes.FakeClock(synth.ts(2.5))
    sender = fakes.FakeSender(clock)
    r = router.Router(st2, M, R, clock, sender)
    recs = router.run(r, clock, sender, synth.ts(13.0))
    rel = [{'crossing_id': 'x1', 'track_ids': ['A', 'B'], 'interval': [5.0, 7.0]}]
    g_rel = gate.check(r.bundles, st2, M, rel, recs, rule=R)
    g_abs = gate.check(r.bundles, st2, M, sc['crossings'], recs, rule=R)
    print('%s 2. confident tracker, %d routed bundles; relative crossing: crossing checked %d, verdict %s; '
          'Unix-time crossing: crossing checked %d, verdict %s' % (
              'FAIL' if g_rel['verdict'] == 'pass' else 'PASS', len(r.bundles), g_rel['clauses']['crossing']['checked'],
              g_rel['verdict'], g_abs['clauses']['crossing']['checked'], g_abs['verdict']))
    bad += g_rel['verdict'] == 'pass'

    tmp = tempfile.mkdtemp(prefix='verify-r5-vac-')
    try:
        sc3 = synth.two_tracks()
        for line in sc3['tracks']:
            line['last_obs_ids'] = ['other-run-' + i for i in line['last_obs_ids']]
        o, t = os.path.join(tmp, 'o.jsonl'), os.path.join(tmp, 't.jsonl')
        records.write_jsonl(o, sc3['observations'])
        records.write_jsonl(t, sc3['tracks'])
        st3 = records.Store(sc3['observations'], sc3['tracks'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = den_bundle.main(['route', '--observations', o, '--tracks', t, '--from', '2', '--to', '70',
                                    '--requests', os.path.join(tmp, 'rq.jsonl'), '--bundles', os.path.join(tmp, 'b.jsonl')])
        text = out.getvalue()
        silent = 'undated' not in text and code == 0
        print('%s 3. Store.undated = %d of %d lines; route exit %d, output mentions undated: %s; output: %r' % (
            'FAIL' if silent else 'PASS', st3.undated, len(sc3['tracks']), code, 'undated' in text, text.strip()))
        bad += silent
    finally:
        shutil.rmtree(tmp, True)
    print('RESULT %s: %d of 3 empty or unmatched inputs passed or went unreported' % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

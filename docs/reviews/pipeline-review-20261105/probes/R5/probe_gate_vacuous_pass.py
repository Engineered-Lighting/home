#!/usr/bin/env python3
"""probe_gate_vacuous_pass.py: rung 5's gate says "pass" when it checked nothing.

What it checks
  gate.check() fails only on a recorded failure; it never asks that anything
  was checked. Through the command line (den_bundle.py gate):
  (a) an empty bundles file and an empty requests file, with one annotated
      crossing: verdict pass, exit 0;
  (b) bundles from a tracker that stayed `confident` through the crossing
      (so every bundle meeting it has association_safe true) but a crossings
      file whose interval is written in relative seconds ([5.0, 7.0], the way
      --at/--from/--to accept times): the crossing clause checks 0 bundles,
      verdict pass, exit 0 (with Unix times the same bundles fail);
  (c) track lines whose last_obs_ids name no observation in the file (e.g. an
      observation file from another run): every line is silently "undated",
      `route` sends nothing and prints no warning, and the gate passes;
  (d) (a) again with --scored, after closing every slot at its proposed value
      in temp copies of METHOD-V1.md and ROUTER-V0.md: a scored pass, exit 0.
  A sound gate would fail or refuse (a)-(d).

How to run
  python3 probe_gate_vacuous_pass.py <root of the claude/rung-5-bundle checkout>

Synthetic data only (the tool's own fixtures), no network; temp files only.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
TOOL = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, TOOL)

import den_bundle  # noqa: E402
import records     # noqa: E402
import synth       # noqa: E402


def cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = den_bundle.main(list(argv))
    return code, out.getvalue()


def verdict(text):
    lines = [l for l in text.splitlines() if l.startswith('verdict') or l.startswith('refused')]
    return lines[-1] if lines else text.strip().splitlines()[-1]


def close_slots(src, dst):
    with open(src, encoding='utf-8') as f:
        text = f.read()
    # replace every {"open": true, "proposed": X, "why": "..."} by X
    text = re.sub(r'\{"open": true, "proposed": ([^,]+), "why": "(?:[^"\\]|\\.)*"\}', r'\1', text)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def main():
    tmp = tempfile.mkdtemp(prefix='probe-r5-vacuous-')
    bad = 0
    try:
        p = lambda *a: os.path.join(tmp, *a)
        code, _ = cli('fixtures', '--out', p('fx'))
        assert code == 0
        obs, trk, crs = p('fx', 'crossing', 'observations.jsonl'), p('fx', 'crossing', 'tracks.jsonl'), p('fx', 'crossing', 'crossings.jsonl')

        # (a) empty bundles and requests
        records.write_jsonl(p('empty.jsonl'), [])
        code, out = cli('gate', '--observations', obs, '--tracks', trk, '--bundles', p('empty.jsonl'),
                        '--requests', p('empty.jsonl'), '--crossings', crs)
        bad += code == 0
        print('%s (a) empty bundles + empty requests + 1 crossing: exit %d, %s' % ('FAIL' if code == 0 else 'PASS', code, verdict(out)))

        # (b) tracker confident through the crossing; crossings in relative seconds
        scene = synth.crossing()
        for line in scene['tracks']:
            line['association'] = {'status': 'confident', 'hypotheses': [], 'since_ts': synth.ts(0.0)}
        records.write_jsonl(p('conf_obs.jsonl'), scene['observations'])
        records.write_jsonl(p('conf_trk.jsonl'), scene['tracks'])
        code, _ = cli('route', '--observations', p('conf_obs.jsonl'), '--tracks', p('conf_trk.jsonl'), '--from', '2.5',
                      '--to', '13', '--requests', p('rq.jsonl'), '--bundles', p('bd.jsonl'))
        assert code == 0
        records.write_jsonl(p('rel_cross.jsonl'), [{'crossing_id': 'x1', 'track_ids': ['A', 'B'], 'interval': [5.0, 7.0]}])
        code_rel, out_rel = cli('gate', '--observations', p('conf_obs.jsonl'), '--tracks', p('conf_trk.jsonl'),
                                '--bundles', p('bd.jsonl'), '--requests', p('rq.jsonl'), '--crossings', p('rel_cross.jsonl'))
        code_abs, out_abs = cli('gate', '--observations', p('conf_obs.jsonl'), '--tracks', p('conf_trk.jsonl'),
                                '--bundles', p('bd.jsonl'), '--requests', p('rq.jsonl'), '--crossings', crs)
        crossing_rel = [l for l in out_rel.splitlines() if l.startswith('crossing')]
        bad += code_rel == 0
        print('%s (b) confident tracker, crossing written [5.0, 7.0]: exit %d, %s | %s ; same bundles with Unix-time '
              'crossing: exit %d, %s' % ('FAIL' if code_rel == 0 else 'PASS', code_rel, crossing_rel, verdict(out_rel),
                                         code_abs, verdict(out_abs)))

        # (c) track lines naming no known observation
        lines = records.read_jsonl(p('fx', 'two_tracks', 'tracks.jsonl'))
        for line in lines:
            line['last_obs_ids'] = ['x' + i for i in line['last_obs_ids']]
        records.write_jsonl(p('undated.jsonl'), lines)
        o2 = p('fx', 'two_tracks', 'observations.jsonl')
        code, out = cli('route', '--observations', o2, '--tracks', p('undated.jsonl'), '--from', '2', '--to', '70',
                        '--requests', p('rq2.jsonl'), '--bundles', p('bd2.jsonl'))
        route_out = out.strip().replace('\n', ' | ')
        code, out = cli('gate', '--observations', o2, '--tracks', p('undated.jsonl'), '--bundles', p('bd2.jsonl'),
                        '--requests', p('rq2.jsonl'))
        bad += code == 0
        print('%s (c) %d track lines, none dated: route printed "%s"; gate exit %d, %s' % (
            'FAIL' if code == 0 else 'PASS', len(lines), route_out, code, verdict(out)))

        # (d) scored, all slots closed at their proposed values, empty input
        close_slots(os.path.join(TOOL, 'METHOD-V1.md'), p('METHOD-closed.md'))
        close_slots(os.path.join(TOOL, 'ROUTER-V0.md'), p('ROUTER-closed.md'))
        code, out = cli('--method', p('METHOD-closed.md'), '--rule', p('ROUTER-closed.md'), 'gate', '--observations', obs,
                        '--tracks', trk, '--bundles', p('empty.jsonl'), '--scored')
        bad += code == 0
        print('%s (d) --scored, slots closed, empty bundles, no requests, no crossings: exit %d, %s' % (
            'FAIL' if code == 0 else 'PASS', code, verdict(out)))
        print('    clause lines: %s' % [l.strip() for l in out.splitlines() if 'checked' in l])
    finally:
        shutil.rmtree(tmp, True)
    print('RESULT %s: %d of 4 empty or unmatched inputs got verdict pass' % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

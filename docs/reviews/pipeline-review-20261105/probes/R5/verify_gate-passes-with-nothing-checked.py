"""Verify: `den_bundle.py gate --scored` gives 'verdict: pass' (exit 0) when nothing was checked.

What it checks
  Copies tools/den-bundle from the given checkout into a temp dir, closes every open slot in
  METHOD-V1.md and ROUTER-V0.md at its proposed value (as an owner would when closing them),
  writes the crossing fixture's observations/tracks, and runs `gate --scored` with
  (1) empty bundles and requests files, and (2) bundles/requests routed from observations of
  another session (no obs_id in common). A sound scored gate must not pass on zero checks.
  Also runs the unmodified tool with `gate --scored` to show it refuses (exit 2) while slots are open.

How to run
  python3 verify_gate-passes-with-nothing-checked.py <root of the branch checkout>

Synthetic data only; temp dirs only; no network.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
SRC = os.path.join(ROOT, 'tools', 'den-bundle')

fails = 0
with tempfile.TemporaryDirectory() as d:
    tool = os.path.join(d, 'den-bundle')
    shutil.copytree(SRC, tool, ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0, tool)
    import records  # noqa: E402
    import synth    # noqa: E402

    def close_slots(path, fence):
        text = open(path, encoding='utf-8').read()
        start = text.index(fence) + len(fence)
        end = text.index('```', start)
        block = json.loads(text[start:end])
        for k, v in list(block.items()):
            if isinstance(v, dict) and v.get('open') is True:
                block[k] = v['proposed']
        text = text[:start] + '\n' + json.dumps(block, indent=2) + '\n' + text[end:]
        open(path, 'w', encoding='utf-8', newline='\n').write(text)

    cli = [sys.executable, '-B', os.path.join(tool, 'den_bundle.py')]
    p = lambda n: os.path.join(d, n)  # noqa: E731
    sc = synth.crossing()
    records.write_jsonl(p('obs.jsonl'), sc['observations'])
    records.write_jsonl(p('tracks.jsonl'), sc['tracks'])
    records.write_jsonl(p('cross.jsonl'), sc['crossings'])
    open(p('empty_b.jsonl'), 'w').close()
    open(p('empty_r.jsonl'), 'w').close()

    def gate(obs, extra):
        r = subprocess.run(cli + ['gate', '--observations', obs, '--tracks', p('tracks.jsonl')] + extra,
                           capture_output=True, text=True)
        return r.returncode, ' | '.join(' '.join(l.split()) for l in r.stdout.splitlines()), r.stderr.strip()

    code, out, err = gate(p('obs.jsonl'), ['--bundles', p('empty_b.jsonl'), '--requests', p('empty_r.jsonl'),
                                           '--crossings', p('cross.jsonl'), '--scored'])
    print('RESULT open slots, --scored, empty inputs: exit %d stderr %r' % (code, err[:200]))

    close_slots(os.path.join(tool, 'METHOD-V1.md'), '```json den-bundle-method')
    close_slots(os.path.join(tool, 'ROUTER-V0.md'), '```json den-bundle-router')

    code, out, err = gate(p('obs.jsonl'), ['--bundles', p('empty_b.jsonl'), '--requests', p('empty_r.jsonl'),
                                           '--crossings', p('cross.jsonl'), '--scored'])
    print('RESULT closed slots, --scored, empty inputs: exit %d: %s %s' % (code, out, err[:200]))
    if code == 0 and 'verdict: pass' in out:
        fails += 1
        print('FAIL scored gate passes with every clause checked 0')

    other = synth.two_tracks()
    for o in other['observations']:
        o['obs_id'] = 's2-' + o['obs_id']
    records.write_jsonl(p('obs_other.jsonl'), other['observations'])
    r = subprocess.run(cli + ['route', '--observations', p('obs_other.jsonl'), '--tracks', p('tracks.jsonl'),
                              '--from', '2', '--to', '60', '--requests', p('rq.jsonl'), '--bundles', p('bd.jsonl')],
                       capture_output=True, text=True)
    n = sum(1 for _ in open(p('rq.jsonl')))
    print('RESULT closed slots, route on mismatched files: exit %d, %d requests, stdout %r' % (r.returncode, n, r.stdout.strip()))
    code, out, err = gate(p('obs_other.jsonl'), ['--bundles', p('bd.jsonl'), '--requests', p('rq.jsonl'),
                                                 '--crossings', p('cross.jsonl'), '--scored'])
    print('RESULT closed slots, --scored, mismatched files: exit %d: %s' % (code, out))
    if code == 0 and 'verdict: pass' in out:
        fails += 1
        print('FAIL scored gate passes on a replay that routed nothing')

print('OVERALL', 'FAIL (%d)' % fails if fails else 'PASS')

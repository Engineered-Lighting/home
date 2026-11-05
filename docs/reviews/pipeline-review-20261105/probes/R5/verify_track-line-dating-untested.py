"""Verify (reproduce lens): records.Store dates a track line by its NEWEST observation; does the
suite or the gate notice if it dated by the OLDEST instead (mutant M15)?

What it checks
  1) Copies tools/den-bundle twice (control, mutant). The mutant changes records.py
     `ts = max(known) if known else None` -> `ts = min(known) if known else None`.
     Runs `python -B -m unittest -f test_assemble` and then `unittest discover -f` in each copy.
  2) A synthetic scene where each track line names two keypoints observations, captured 0.5 s
     apart (allowed: Track.last_obs_ids is "every Observation associated in that update").
     A walks right at 0.5 m/s; the line written at u names o(u-0.5) and o(u), position x(u).
     Through each copy's own CLI: `assemble --track A --at 5.2`, then `gate` on that bundle.
     With newest-dating the 5.5 s line is not known at 5.2; with oldest-dating it is dated 5.0
     and its position (x(5.5), which uses 5.5 s evidence) enters the 5.2 s bundle.
     Also runs the PINNED gate on the mutant's bundle.

How to run
  python3 verify_track-line-dating-untested.py <root of the branch checkout> [<mutation work dir>]
  Synthetic data only, temp dirs only; the checkout is never modified.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
WORK = sys.argv[2] if len(sys.argv) > 2 else None
SRC = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, SRC)
import records  # noqa: E402
import synth    # noqa: E402

OLD, NEW = 'ts = max(known) if known else None', 'ts = min(known) if known else None'


def suite(d, module=None):
    args = [sys.executable, '-B', '-m', 'unittest'] + (['-f', module] if module else ['discover', '-f', '-s', '.', '-p', 'test_*.py'])
    p = subprocess.run(args, cwd=d, capture_output=True, text=True, timeout=600)
    tail = [l for l in p.stderr.strip().splitlines() if l.startswith(('Ran ', 'OK', 'FAILED'))]
    return p.returncode, ' '.join(tail)


def scene(folder):
    obs, lines = [], []
    x = lambda t: round(1.0 + 0.5 * t, 3)
    for k in range(0, 17):
        u = round(0.5 * k, 3)
        o = synth.observation(synth.oid('A', u), u, x(u), 1.0, k=k)
        obs.append(o)
        named = [synth.oid('A', round(u - 0.5, 3)), o['obs_id']] if k else [o['obs_id']]
        line = synth.track_line('A', x(u), 1.0, named)
        lines.append(line)
    os.makedirs(folder, exist_ok=True)
    records.write_jsonl(os.path.join(folder, 'observations.jsonl'), obs)
    records.write_jsonl(os.path.join(folder, 'tracks.jsonl'), lines)
    return os.path.join(folder, 'observations.jsonl'), os.path.join(folder, 'tracks.jsonl')


with tempfile.TemporaryDirectory(dir=WORK) as tmp:
    o, t = scene(os.path.join(tmp, 'scene'))
    out = {}
    for name in ('control', 'mutant'):
        d = os.path.join(tmp, name, 'den-bundle')
        shutil.copytree(SRC, d, ignore=shutil.ignore_patterns('__pycache__'))
        if name == 'mutant':
            p = os.path.join(d, 'records.py')
            text = open(p, encoding='utf-8').read()
            assert text.count(OLD) == 1
            open(p, 'w', encoding='utf-8', newline='\n').write(text.replace(OLD, NEW))
        rc1, s1 = suite(d, 'test_assemble')
        rc2, s2 = suite(d)
        print('RESULT %s suite: test_assemble rc=%d (%s); discover rc=%d (%s)' % (name, rc1, s1, rc2, s2))
        cli = [sys.executable, '-B', os.path.join(d, 'den_bundle.py')]
        bfile = os.path.join(tmp, name + '_bundle.jsonl')
        a = subprocess.run(cli + ['assemble', '--observations', o, '--tracks', t, '--track', 'A', '--at', '5.2',
                                  '--out', bfile], capture_output=True, text=True)
        assert a.returncode == 0, a.stderr
        b = records.read_jsonl(bfile)[0]
        g = subprocess.run(cli + ['gate', '--observations', o, '--tracks', t, '--bundles', bfile],
                           capture_output=True, text=True)
        out[name] = (bfile, b, g.returncode)
        pos = [(round(p['ts'] - synth.T0, 3), p['x']) for p in b['positions']]
        print('RESULT %s bundle at 5.2 s: positions (ts, x) %s; own gate exit %d' % (name, pos, g.returncode))
    pinned = subprocess.run([sys.executable, '-B', os.path.join(SRC, 'den_bundle.py'), 'gate', '--observations', o,
                             '--tracks', t, '--bundles', out['mutant'][0]], capture_output=True, text=True)
    leak = [p for p in out['mutant'][1]['positions'] if p['x'] > 1.0 + 0.5 * 5.2 + 1e-9]
    print('RESULT mutant bundle carries %d position(s) with x beyond A\'s true x at 5.2 s (%.2f): %s; '
          'pinned gate on it exits %d' % (len(leak), 1.0 + 0.5 * 5.2, [(round(p['ts'] - synth.T0, 3), p['x']) for p in leak],
                                          pinned.returncode))
    if leak and out['mutant'][2] == 0 and pinned.returncode == 0:
        print('FAIL dating by the oldest observation leaks future positions; neither the gate nor (see suite rc) the tests notice')
    else:
        print('PASS no undetected leak')

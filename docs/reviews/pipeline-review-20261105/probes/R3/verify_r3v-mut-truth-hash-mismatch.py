"""verify_r3v-mut-truth-hash-mismatch.py -- R3 verifier, reproduce lens.

Checks finding r3v-mut-truth-hash-mismatch. pipeline.score_session refuses a
scored run when truth.jsonl names an observations_sha256 other than the take's
observations.jsonl ("if want and want != obs_hash"). The reported mutant
disables that check ("if False:") and survives the den-track suite. This probe
shows the mutant is NOT equivalent, in two scenarios, with every METHOD.md
slot closed at its proposed value:

  S1 truth.jsonl of take X copied into take Y's folder (Y tracked normally).
  S2 observations.jsonl regenerated after labelling (here: last line dropped)
     and re-tracked, so tracks.run.json matches it, while truth.jsonl still
     names the old file.

Original: exit 2, "labels a different observations file". Mutant: SCORED.

Run (stdlib only, synthetic data, temp copies of tools/den-track; touches
nothing in the checkout and opens no network connection):
  python3 -B verify_r3v-mut-truth-hash-mismatch.py <branch checkout root>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
OLD = "        if want and want != obs_hash:"
NEW = "        if False:"
FENCE = '```json den-track-method'


def copy_tool(dst, mutate):
    shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), dst,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if mutate:
        p = os.path.join(dst, 'pipeline.py')
        src = open(p, encoding='utf-8').read()
        assert src.count(OLD) == 1, 'pinned line not found'
        open(p, 'w', encoding='utf-8', newline='\n').write(src.replace(OLD, NEW))


def all_closed(tool, dst):
    text = open(os.path.join(tool, 'METHOD.md'), encoding='utf-8').read()
    start = text.index(FENCE)
    body_start = start + len(FENCE) + 1
    end = text.index('\n```', body_start)
    block = json.loads(text[body_start:end])

    def close(n):
        if isinstance(n, dict) and n.get('open') is True:
            return n['proposed']
        if isinstance(n, dict):
            return {k: close(v) for k, v in n.items()}
        return n
    open(dst, 'w', encoding='utf-8', newline='\n').write(
        text[:start] + FENCE + '\n' + json.dumps(close(block), indent=2) + text[end:])
    return dst


def run(tool, *argv):
    p = subprocess.run([sys.executable, '-B', os.path.join(tool, 'den_track.py')] + list(argv),
                       capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    return p.returncode, p.stdout, p.stderr


def scenario(tool, meth, base, kind):
    session = os.path.join(base, 'session-' + kind)
    code, _, err = run(tool, '--method', meth, 'synth', '--out', session, '--scenes', 'clean_crossing', '--seeds', '2')
    assert code == 0, err
    takes = sorted(os.listdir(session))
    if kind == 'S1':
        assert run(tool, '--method', meth, 'track', '--session', session)[0] == 0
        shutil.copyfile(os.path.join(session, takes[0], 'truth.jsonl'), os.path.join(session, takes[1], 'truth.jsonl'))
    else:
        obs = os.path.join(session, takes[0], 'observations.jsonl')
        lines = open(obs, encoding='utf-8').readlines()
        open(obs, 'w', encoding='utf-8', newline='\n').writelines(lines[:-1])
        assert run(tool, '--method', meth, 'track', '--session', session)[0] == 0
    code, out, err = run(tool, '--method', meth, 'score', '--session', session)
    head = next((l for l in out.splitlines() if l.startswith('E2 geometry-only arm')), '')
    return code, head, err.strip().replace('\n', ' ')[:240]


tmp = tempfile.mkdtemp(prefix='verify-r3-hash-')
codes = {}
for label, mutate in (('original', False), ('mutant', True)):
    base = os.path.join(tmp, label)
    tool = os.path.join(base, 'den-track')
    copy_tool(tool, mutate)
    meth = all_closed(tool, os.path.join(base, 'METHOD.closed.md'))
    for kind in ('S1', 'S2'):
        code, head, err = scenario(tool, meth, base, kind)
        codes[(label, kind)] = code
        print('RESULT %s %s: exit %d %s %s' % (label, kind, code, head, err))
ok = all(codes[('original', k)] == 2 for k in ('S1', 'S2')) and all(codes[('mutant', k)] in (0, 1) for k in ('S1', 'S2'))
print('%s original refuses both mismatches; mutant scores both' % ('PASS' if ok else 'FAIL'))
shutil.rmtree(tmp, ignore_errors=True)

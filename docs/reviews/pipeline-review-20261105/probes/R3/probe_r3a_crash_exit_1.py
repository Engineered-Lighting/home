"""probe_r3a_crash_exit_1: den_track.py documents exit 1 as "score computed
but gate not met" and 2 as "refused or an input error", but input errors
outside its caught exception list escape as a traceback with Python's exit
code 1, so a script (or the owner reading $LASTEXITCODE) sees "gate not met"
for a crash.  Cases: tracks.run.json cut short (an interrupted `track`
leaves it so), a truth.jsonl saved as UTF-16 (PowerShell 5.1 '>' or
Out-File), and a slot closed as {"open": "true", ...}.

Also checks: track output is byte-identical across two runs, and every
ambiguous record's hypotheses sum to 1.

Run:  python3 probe_r3a_crash_exit_1.py <root of the rung-3-track checkout>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
CLI = os.path.join(TOOL, 'den_track.py')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import synth  # noqa: E402


def write_method(dst, mutate=None):
    text = open(method_mod.METHOD_PATH, encoding='utf-8').read()
    resolved = method_mod.resolve(method_mod.read_block(method_mod.METHOD_PATH))
    resolved['room'] = synth.ROOM_MODEL
    if mutate:
        mutate(resolved)
    start = text.index('```json den-track-method')
    end = text.index('```', start + 10)
    open(dst, 'w', encoding='utf-8', newline='\n').write(
        text[:start] + '```json den-track-method\n' + json.dumps(resolved, indent=2) + '\n' + text[end:])


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1:]


def main():
    tmp = tempfile.mkdtemp(prefix='r3a_exit_')
    bad = []
    try:
        m = os.path.join(tmp, 'METHOD.md')
        write_method(m)
        sess = os.path.join(tmp, 's')
        run('--method', m, 'synth', '--out', sess, '--seeds', '1', '--scenes', 'touching_crossing')
        run('--method', m, 'track', '--session', sess)
        take = os.path.join(sess, os.listdir(sess)[0])
        first = open(os.path.join(take, 'tracks.jsonl'), 'rb').read()
        run('--method', m, 'track', '--session', sess)
        second = open(os.path.join(take, 'tracks.jsonl'), 'rb').read()
        print('determinism: tracks.jsonl identical across runs: %s' % (first == second))
        recs = [json.loads(l) for l in second.decode().splitlines() if l.strip()]
        sums = [sum(h['p'] for h in r['association']['hypotheses']) for r in recs if r['association']['status'] == 'ambiguous']
        print('hypotheses: %d ambiguous records, sum of p in [%.4f, %.4f]' % (len(sums), min(sums or [1]), max(sums or [1])))

        rp = os.path.join(take, 'tracks.run.json')
        good = open(rp, encoding='utf-8').read()
        open(rp, 'w', encoding='utf-8').write(good[:len(good) // 2])
        rc, last = run('--method', m, 'score', '--session', sess)
        print('tracks.run.json cut short: exit %d %s' % (rc, last))
        bad += ['run.json'] if rc == 1 else []
        open(rp, 'w', encoding='utf-8').write(good)

        tp = os.path.join(take, 'truth.jsonl')
        t8 = open(tp, encoding='utf-8').read()
        open(tp, 'w', encoding='utf-16').write(t8)      # BOM + UTF-16LE, as PS 5.1 '>' writes
        rc, last = run('--method', m, 'score', '--session', sess)
        print('truth.jsonl as UTF-16: exit %d %s' % (rc, last))
        bad += ['utf16'] if rc == 1 else []
        open(tp, 'w', encoding='utf-8').write(t8)

        m2 = os.path.join(tmp, 'METHOD2.md')
        write_method(m2, lambda v: v['tracker'].update(confident_at={'open': 'true', 'proposed': 0.9, 'why': 'x'}))
        rc, last = run('--method', m2, 'track', '--session', sess)
        print('slot written "open": "true": track exit %d %s' % (rc, last))
        bad += ['slot'] if rc == 1 else []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('RESULT: %s' % ('FAIL: crash exits 1 ("gate not met") in: %s' % ', '.join(bad) if bad else 'PASS'))


if __name__ == '__main__':
    main()

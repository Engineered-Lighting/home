"""probe_v_unlabelled_takes_count: verifier probe for finding vacuous-empty-windows.

A realistic way to get windows that hold no labelled observation: truth
files whose window lines are written but whose frame lines are not yet
(labelling unfinished). Writes 20 synthetic clean_crossing takes (one window
each, as the README's script has), a CLOSED copy of METHOD.md, tracks, then
strips every frame line from the truth files of 18 takes (take line and
window line kept, observations_sha256 kept) and runs a SCORED `score`.
A sound gate would refuse or exclude those windows; prints what the tool does.

Synthetic data, temp dirs only, no network.
Run: python3 probe_v_unlabelled_takes_count.py <root of the rung-3-track checkout>
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


def closed_method(dst):
    src = os.path.join(TOOL, 'METHOD.md')
    with open(src, encoding='utf-8') as f:
        text = f.read()
    resolved = method_mod.resolve(method_mod.read_block(src))
    resolved['room'] = synth.ROOM_MODEL
    start = text.index('```json den-track-method')
    end = text.index('```', start + 10)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + '```json den-track-method\n' + json.dumps(resolved, indent=2) + '\n' + text[end:])


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    tmp = tempfile.mkdtemp(prefix='v_unlab_')
    try:
        m = os.path.join(tmp, 'METHOD.md')
        closed_method(m)
        sess = os.path.join(tmp, 's')
        rc, out = run('--method', m, 'synth', '--out', sess, '--seeds', '20', '--scenes', 'clean_crossing', '--noise', 'clean')
        assert rc == 0, out
        rc, out = run('--method', m, 'track', '--session', sess)
        assert rc == 0, out
        rc, out = run('--method', m, 'score', '--session', sess)
        rep = json.load(open(os.path.join(sess, 'e2-report.json')))
        print('all 20 takes labelled: exit %d, windows %d, IDF1 %.3f, gate %s' % (rc, rep['crossings'], rep['pooled']['idf1'], rep['gate']['passed']))
        takes = sorted(d for d in os.listdir(sess) if os.path.isdir(os.path.join(sess, d)))
        for t in takes[2:]:
            p = os.path.join(sess, t, 'truth.jsonl')
            lines = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
            keep = [l for l in lines if l['line'] != 'frame']
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                for l in keep:
                    f.write(json.dumps(l) + '\n')
        rc, out = run('--method', m, 'score', '--session', sess)
        if rc == 2:
            print('18 takes unlabelled: REFUSED: %s' % out.strip().splitlines()[-1])
            print('RESULT: PASS (refused)')
            return
        rep = json.load(open(os.path.join(sess, 'e2-report.json')))
        empty = sum(1 for r in rep['windows'] if r['observations'] == 0)
        print('18 takes with no frame lines: exit %d, windows %d, windows with 0 labelled observations %d, IDF1 %.3f, '
              'bound %.4f, gate %s' % (rc, rep['crossings'], empty, rep['pooled']['idf1'], rep['silent_swap_rate_upper_95'],
                                       rep['gate']['passed']))
        print('  CLI gate lines: %s' % [l for l in out.splitlines() if l.startswith(('takes', 'pooled', 'one-sided', 'gate'))])
        print('RESULT: %s' % ('FAIL: gate PASSED with 18 of 20 windows holding no labelled observation'
                              if rep['gate']['passed'] and empty >= 18 else 'PASS/not reproduced'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

"""probe_r3a_vacuous_windows: E2's gate counts windows that hold no scored
observation as passed trials, so it can PASS with 1 real crossing.

What it checks (synthetic data, temp dirs only, no network):
  1. Writes one synthetic clean_crossing take with den_track.py synth.
  2. Writes a CLOSED copy of METHOD.md (every open slot replaced by its
     proposed value; room = the synthetic den) and tracks the take.
  3. Variant A: appends 19 'crossing' windows to truth.jsonl at times after
     the take ended (no observation inside any of them) and runs a SCORED
     `score`.  Expect a sound gate to refuse or fail; the tool says PASSED
     with n = 20 and a 13.9% bound.
  4. Variant B: instead, labels 19 single frames outside the real window as
     "?" (cannot tell) and wraps a window around each.  Every observation
     inside those windows is excluded, yet each window counts as a passed
     trial.

Run:  python3 probe_r3a_vacuous_windows.py <root of the rung-3-track checkout>
Prints RESULT lines; FAIL means the defect reproduced.
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
    with open(method_mod.METHOD_PATH, encoding='utf-8') as f:
        text = f.read()
    block = method_mod.read_block(method_mod.METHOD_PATH)
    resolved = method_mod.resolve(block)
    resolved['room'] = synth.ROOM_MODEL
    start = text.index('```json den-track-method')
    end = text.index('```', start + 10)
    new = text[:start] + '```json den-track-method\n' + json.dumps(resolved, indent=2) + '\n' + text[end:]
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new)


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def read_lines(p):
    with open(p, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def write_lines(p, lines):
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        for l in lines:
            f.write(json.dumps(l) + '\n')


def main():
    tmp = tempfile.mkdtemp(prefix='r3a_vac_')
    try:
        m = os.path.join(tmp, 'METHOD.md')
        closed_method(m)
        sess = os.path.join(tmp, 'session')
        rc, out = run('--method', m, 'synth', '--out', sess, '--seeds', '1', '--scenes', 'clean_crossing')
        assert rc == 0, out
        rc, out = run('--method', m, 'track', '--session', sess)
        assert rc == 0, out
        take = os.path.join(sess, os.listdir(sess)[0])
        truth_p = os.path.join(take, 'truth.jsonl')
        orig = read_lines(truth_p)
        rc0, out0 = run('--method', m, 'score', '--session', sess)
        print('baseline (1 real window): exit %d; %s' % (rc0, [l for l in out0.splitlines() if l.startswith(('gate', 'pooled', 'one-sided', 'takes'))]))

        # Variant A: 19 windows after the take ended, holding nothing.
        frames = [l for l in orig if l['line'] == 'frame']
        t_last = max(l['capture_ts'] for l in frames)
        lines = list(orig)
        for k in range(19):
            s = t_last + 10 + 3 * k
            lines.append({'truth_version': 'den-track-truth/v1', 'line': 'window', 'window_id': 'v%02d' % k,
                          'kind': 'crossing', 'start_ts': s, 'end_ts': s + 2})
        write_lines(truth_p, lines)
        rcA, outA = run('--method', m, 'score', '--session', sess)
        rep = json.load(open(os.path.join(sess, 'e2-report.json')))
        empty = sum(1 for r in rep['windows'] if r['observations'] == 0)
        print('variant A: exit %d; windows %d, windows with 0 observations %d, gate passed %s, bound %s'
              % (rcA, rep['crossings'], empty, rep['gate']['passed'], rep['silent_swap_rate_upper_95']))
        print('variant A gate lines: %s' % [l for l in outA.splitlines() if l.startswith(('gate', 'pooled', 'one-sided'))])
        a_bad = rep['gate']['passed'] and empty >= 19

        # Variant B: 19 frames outside the real window labelled '?', a window round each.
        win = [l for l in orig if l['line'] == 'window'][0]
        outside = [l for l in frames if (l['capture_ts'] < win['start_ts'] - 0.3 or l['capture_ts'] > win['end_ts'] + 0.3) and l['labels']]
        lines = []
        picked = set(id(l) for l in outside[:19])
        wins = []
        for l in orig:
            if l['line'] == 'frame' and id(l) in picked:
                l = dict(l, labels=[dict(x, person='?') for x in l['labels']])
                wins.append({'truth_version': 'den-track-truth/v1', 'line': 'window', 'window_id': 'q%02d' % len(wins),
                             'kind': 'occlusion', 'start_ts': l['capture_ts'] - 0.01, 'end_ts': l['capture_ts'] + 0.01})
            lines.append(l)
        write_lines(truth_p, lines + wins)
        rcB, outB = run('--method', m, 'score', '--session', sess)
        if rcB == 2:
            print('variant B refused: %s' % outB.strip().splitlines()[-1])
            b_bad = False
        else:
            rep = json.load(open(os.path.join(sess, 'e2-report.json')))
            empty = sum(1 for r in rep['windows'] if r['observations'] == 0)
            print('variant B: exit %d; windows %d (picked %d), windows with 0 scored observations %d, gate passed %s, bound %s'
                  % (rcB, rep['crossings'], len(wins), empty, rep['gate']['passed'], rep['silent_swap_rate_upper_95']))
            b_bad = rep['gate']['passed'] and empty >= len(wins)
        print('RESULT variant A (empty windows): %s' % ('FAIL: gate PASSED on 1 real window + 19 empty ones' if a_bad else 'PASS'))
        print('RESULT variant B (all-unsure windows): %s' % ('FAIL: gate PASSED with every observation in 19 windows excluded' if b_bad else 'PASS/not reproduced'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

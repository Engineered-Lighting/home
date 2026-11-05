"""probe_r3v_empty_windows_count.py -- R3 (den-track), validate lens.

What it checks
  Whether a window that holds no labelled observation (so no evidence at
  all) still counts as a passed E2 trial toward `min_crossings` (20) and the
  one-sided bound. A truth window on the wrong clock, or marked outside the
  recorded span, is such a window.

How
  In a temp folder: a copy of METHOD.md with every open slot closed at its
  proposed value (room = the synthetic den, as the owner would measure it);
  `den_track.py synth` writes 20 clean-crossing take folders; in 19 of them
  the truth window is moved 100 s past the take's last frame (no labelled
  observation inside it); `track` and a SCORED `score` run through the CLI.
  FAIL if the scored gate passes with 20 windows although only one window
  holds any labelled observation.

Run (stdlib only, synthetic data, temp folders only):
  python3 -B probe_r3v_empty_windows_count.py <branch checkout root>
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import den_track  # noqa: E402
import method  # noqa: E402
import synth  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = den_track.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def closed_method(dst, room, **override):
    src = method.METHOD_PATH
    text = open(src, encoding='utf-8').read()
    block = json.loads(json.dumps(method.Method(src).block))

    def close(node):
        if method.is_open(node):
            return node['proposed']
        if isinstance(node, dict):
            return {k: close(v) for k, v in node.items()}
        return node

    block = close(block)
    block['room'] = room
    for k, v in override.items():
        block['scoring'][k] = v
    start = text.index(method.FENCE)
    end = text.index('\n```', start + len(method.FENCE))
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + method.FENCE + '\n' + json.dumps(block, indent=2) + text[end:])
    return dst


def main():
    tmp = tempfile.mkdtemp(prefix='probe-r3v-empty-')
    try:
        m = closed_method(os.path.join(tmp, 'METHOD.md'), synth.ROOM_MODEL)
        session = os.path.join(tmp, 'session')
        code, out, err = run('--method', m, 'synth', '--out', session, '--seeds', '20',
                             '--scenes', 'clean_crossing', '--noise', 'clean')
        assert code == 0, err
        takes = sorted(os.listdir(session))
        for name in takes[1:]:
            path = os.path.join(session, name, 'truth.jsonl')
            lines = [json.loads(l) for l in open(path, encoding='utf-8')]
            last = max(l['capture_ts'] for l in lines if l['line'] == 'frame')
            for l in lines:
                if l['line'] == 'window':
                    l['start_ts'], l['end_ts'] = last + 100.0, last + 101.0
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.writelines(json.dumps(l) + '\n' for l in lines)
        code, out, err = run('--method', m, 'track', '--session', session)
        assert code == 0, err
        code, out, err = run('--method', m, 'score', '--session', session)
        rep = json.load(open(os.path.join(session, 'e2-report.json'), encoding='utf-8'))
        empty = sum(1 for w in rep['windows'] if w['observations'] == 0)
        print('RESULT scored run exit %d; practice=%s; windows %d, windows with 0 labelled observations %d; '
              'pooled IDF1 %s; upper bound %.4f; gate passed %s'
              % (code, rep['practice'], rep['crossings'], empty, rep['pooled']['idf1'],
                 rep['silent_swap_rate_upper_95'], rep['gate']['passed']))
        for line in out.splitlines():
            if line.startswith(('E2 ', 'takes', 'silent', 'pooled', 'one-sided', 'gate')):
                print('  cli> ' + line)
        if rep['gate']['passed'] and empty >= 19:
            print('FAIL the scored gate passes with %d windows of which %d hold no labelled observation' %
                  (rep['crossings'], empty))
            return 1
        print('PASS empty windows do not count toward the gate')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

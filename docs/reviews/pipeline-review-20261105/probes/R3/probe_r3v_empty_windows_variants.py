"""probe_r3v_empty_windows_variants.py -- R3 (den-track), verifier for r3v-empty-windows-count.

What it checks (all SCORED runs through the CLI, closed METHOD.md copy in a temp dir,
20 synthetic clean-crossing takes, 1 left intact):
  A   in 19 takes the window is moved past the last frame (frames still labelled):
      does the gate pass?  (the reported scenario)
  A2  as A, plus a swap of the truth labels A<->B halfway through ONE of those
      19 takes, so a consistent track looks like a confident switch outside any
      window: does the take-wide silent-swap check still fail the gate?
  B   in 19 takes every frame line is removed (only the take and window lines
      remain, i.e. the takes were never labelled): does the gate pass?

Run (stdlib only, synthetic data, temp folders only):
  python3 -B probe_r3v_empty_windows_variants.py <branch checkout root>
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


def closed_method(dst):
    text = open(method.METHOD_PATH, encoding='utf-8').read()

    def close(node):
        if method.is_open(node):
            return node['proposed']
        if isinstance(node, dict):
            return {k: close(v) for k, v in node.items()}
        return node
    block = close(json.loads(json.dumps(method.Method().block)))
    block['room'] = synth.ROOM_MODEL
    start = text.index(method.FENCE)
    end = text.index('\n```', start + len(method.FENCE))
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + method.FENCE + '\n' + json.dumps(block, indent=2) + text[end:])
    return dst


def variant(tag, edit):
    tmp = tempfile.mkdtemp(prefix='probe-r3v-ew-')
    try:
        m = closed_method(os.path.join(tmp, 'METHOD.md'))
        session = os.path.join(tmp, 'session')
        code, _, err = run('--method', m, 'synth', '--out', session, '--seeds', '20',
                           '--scenes', 'clean_crossing', '--noise', 'clean')
        assert code == 0, err
        takes = sorted(os.listdir(session))
        for i, name in enumerate(takes[1:]):
            path = os.path.join(session, name, 'truth.jsonl')
            lines = [json.loads(l) for l in open(path, encoding='utf-8')]
            lines = edit(i, lines)
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.writelines(json.dumps(l) + '\n' for l in lines)
        code, _, err = run('--method', m, 'track', '--session', session)
        assert code == 0, err
        code, out, err = run('--method', m, 'score', '--session', session)
        rep = json.load(open(os.path.join(session, 'e2-report.json'), encoding='utf-8'))
        empty = sum(1 for w in rep['windows'] if w['observations'] == 0)
        unlabelled = sum(1 for t in rep['takes'] if t['labelled'] == 0)
        print('RESULT %-3s exit %d practice=%s windows %d empty-windows %d takes-with-0-labels %d '
              'silent-in %d silent-outside %d IDF1 %s gate passed %s problems %s'
              % (tag, code, rep['practice'], rep['crossings'], empty, unlabelled,
                 rep['silent_swaps'], rep['silent_swaps_outside_windows'],
                 None if rep['pooled']['idf1'] is None else round(rep['pooled']['idf1'], 4),
                 rep['gate']['passed'], rep['problems']))
        return rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def move_windows(i, lines):
    last = max(l['capture_ts'] for l in lines if l['line'] == 'frame')
    for l in lines:
        if l['line'] == 'window':
            l['start_ts'], l['end_ts'] = last + 100.0, last + 101.0
    return lines


def move_and_swap(i, lines):
    lines = move_windows(i, lines)
    if i == 0:
        frames = [l for l in lines if l['line'] == 'frame']
        mid = frames[len(frames) // 2]['capture_ts']
        for l in frames:
            if l['capture_ts'] >= mid:
                for lab in l['labels']:
                    lab['person'] = {'A': 'B', 'B': 'A'}.get(lab['person'], lab['person'])
    return lines


def drop_frames(i, lines):
    return [l for l in lines if l['line'] != 'frame']


a = variant('A', move_windows)
a2 = variant('A2', move_and_swap)
b = variant('B', drop_frames)
print('%s A  (windows off the clock) gate passes' % ('FAIL' if a['gate']['passed'] else 'PASS'))
print('%s A2 (same + a confident switch in an off-clock take) gate %s'
      % ('PASS' if not a2['gate']['passed'] else 'FAIL', 'fails (take-wide check works)' if not a2['gate']['passed'] else 'passes'))
print('%s B  (19 takes never labelled) gate passes' % ('FAIL' if b['gate']['passed'] else 'PASS'))

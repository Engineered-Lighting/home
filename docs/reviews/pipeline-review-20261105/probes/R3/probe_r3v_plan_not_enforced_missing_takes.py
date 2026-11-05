"""probe_r3v_plan_not_enforced_missing_takes.py -- R3 (den-track), validate lens.

What it checks
  METHOD.md's `scoring.crossings_plan` slot says the sample is "fixed before
  recording, so the sample and its bound are set in advance" (30 takes, by
  kind), and a scored run refuses while that slot is open. This checks
  whether a closed plan is ever compared with the session, and what happens
  to a planned take folder that has no observations.jsonl (for example a
  rung-2 replay that refused or failed for that take, which leaves no
  observations.jsonl behind).

How
  In a temp folder: METHOD.md copy with every slot closed at its proposed
  value (crossings_plan total 30; room = the synthetic den); `den_track.py
  synth` writes 30 clean-crossing take folders; observations.jsonl is
  removed from 10 of them (truth.jsonl stays); `track` and a SCORED
  `score` run through the CLI. FAIL if the scored gate passes on 20 of the
  30 planned takes with no word about the plan or the 10 missing takes.

Run (stdlib only, synthetic data, temp folders only):
  python3 -B probe_r3v_plan_not_enforced_missing_takes.py <branch checkout root>
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
    tmp = tempfile.mkdtemp(prefix='probe-r3v-plan-')
    try:
        m = closed_method(os.path.join(tmp, 'METHOD.md'), synth.ROOM_MODEL)
        plan = json.loads(json.dumps(method.Method(m).values['scoring']['crossings_plan']))
        session = os.path.join(tmp, 'session')
        code, out, err = run('--method', m, 'synth', '--out', session, '--seeds', '30',
                             '--scenes', 'clean_crossing', '--noise', 'clean')
        assert code == 0, err
        takes = sorted(os.listdir(session))
        missing = takes[::3]
        for name in missing:
            os.remove(os.path.join(session, name, 'observations.jsonl'))
        code, out, err = run('--method', m, 'track', '--session', session)
        assert code == 0, err
        tracked = [l.split()[0] for l in out.splitlines() if not l.startswith('note')]
        code, out, err = run('--method', m, 'score', '--session', session)
        text = open(os.path.join(session, 'e2-report.json'), encoding='utf-8').read()
        rep = json.loads(text)
        named = [t for t in missing if t in text or t in out or t in err]
        print('RESULT closed crossings_plan total %s; take folders %d; tracked %d; scored run exit %d; '
              'practice=%s; takes in report %d; windows %d; gate passed %s'
              % (plan.get('total'), len(takes), len(tracked), code, rep['practice'], len(rep['takes']),
                 rep['crossings'], rep['gate']['passed']))
        print('RESULT missing take folders named anywhere in the CLI output or report: %d of %d; '
              '"plan" or "crossings_plan" in report: %s' % (len(named), len(missing),
                                                         'crossings_plan' in text or '"plan"' in text))
        for line in out.splitlines():
            if line.startswith(('E2 ', 'takes', 'one-sided', 'gate')):
                print('  cli> ' + line)
        if rep['gate']['passed'] and not named:
            print('FAIL the scored gate passes on %d of %d planned takes; the plan is never checked and the '
                  '%d take folders without observations.jsonl are skipped silently' % (len(rep['takes']),
                                                                                     plan.get('total'), len(missing)))
            return 1
        print('PASS')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

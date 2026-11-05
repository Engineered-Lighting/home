"""probe_r3v_blind_not_enforced.py -- R3 (den-track), validate lens.

What it checks
  owner-choices.md (labelling): the owner labels alone, BLIND to system
  output. den-track-truth/v1 carries `blind` and `labeller` on its take line
  and the README's step 4 says the owner labels "before looking at any
  tracker output". This checks whether a SCORED run refuses, flags or even
  reports truth files that say `"blind": false`.

How
  In a temp folder: METHOD.md copy with every slot closed at its proposed
  value (room = the synthetic den); `den_track.py synth` writes 20
  clean-crossing takes; every truth.jsonl take line is rewritten with
  "blind": false and "labeller": "owner-after-viewing-tracks"; `track` and a
  SCORED `score` run through the CLI. FAIL if the scored gate passes and
  neither the CLI output nor e2-report.json says the labels were not blind.

Run (stdlib only, synthetic data, temp folders only):
  python3 -B probe_r3v_blind_not_enforced.py <branch checkout root>
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
    tmp = tempfile.mkdtemp(prefix='probe-r3v-lab-')
    try:
        m = closed_method(os.path.join(tmp, 'METHOD.md'), synth.ROOM_MODEL)
        session = os.path.join(tmp, 'session')
        code, out, err = run('--method', m, 'synth', '--out', session, '--seeds', '20',
                             '--scenes', 'clean_crossing', '--noise', 'clean')
        assert code == 0, err
        for name in sorted(os.listdir(session)):
            path = os.path.join(session, name, 'truth.jsonl')
            lines = [json.loads(l) for l in open(path, encoding='utf-8')]
            lines[0]['blind'] = False
            lines[0]['labeller'] = 'owner-after-viewing-tracks'
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.writelines(json.dumps(l) + '\n' for l in lines)
        code, out, err = run('--method', m, 'track', '--session', session)
        assert code == 0, err
        code, out, err = run('--method', m, 'score', '--session', session)
        text = open(os.path.join(session, 'e2-report.json'), encoding='utf-8').read()
        rep = json.loads(text)
        said = ('blind' in text) or ('blind' in out) or ('blind' in err) or ('labeller' in text)
        print('RESULT truth "blind": false on all %d takes; scored run exit %d; practice=%s; gate passed %s; '
              'problems %r; "blind"/"labeller" mentioned in output or report: %s'
              % (len(rep['takes']), code, rep['practice'], rep['gate']['passed'], rep['problems'], said))
        if rep['gate']['passed'] and not said:
            print('FAIL a scored run accepts and passes on labels that say they were not blind, and the report '
                  'does not record it')
            return 1
        print('PASS')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

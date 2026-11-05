"""verify_spec_crash-exits-as-gate-not-met: verifier probe for the finding
that malformed inputs crash with exit 1 (documented as "gate not met").

Checks, in temp dirs with synthetic data only:
  1. A truth.jsonl whose `labels` entry is not an object (a hand-written
     slip) -> what exit code `score --practice` gives (traceback = 1?).
  2. Whether a crashed `score` leaves a previous e2-report.json in place.
  3. How method.py treats a slot "closed" as {"open": false, "proposed": X}
     (not the documented way, which replaces the whole object): is it seen as
     closed, and what value does the tool then use for `room`?

Run:  python3 verify_spec_crash-exits-as-gate-not-met.py <root of the rung-3-track checkout>
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


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1:]


def main():
    tmp = tempfile.mkdtemp(prefix='r3spec_exit_')
    try:
        sess = os.path.join(tmp, 's')
        run('synth', '--out', sess, '--seeds', '1', '--scenes', 'clean_crossing')
        run('track', '--session', sess)
        rc, last = run('score', '--session', sess, '--practice')
        print('baseline practice score: exit %d %s' % (rc, last))
        rep = os.path.join(sess, 'e2-report.json')
        before = open(rep, 'rb').read()
        take = os.path.join(sess, os.listdir(sess)[0]) if os.path.isdir(os.path.join(sess, os.listdir(sess)[0])) else None
        take = [os.path.join(sess, d) for d in sorted(os.listdir(sess)) if os.path.isdir(os.path.join(sess, d))][0]
        tp = os.path.join(take, 'truth.jsonl')
        lines = [json.loads(l) for l in open(tp, encoding='utf-8') if l.strip()]
        for rec in lines:
            if rec.get('line') == 'frame' and rec.get('labels'):
                rec['labels'][0] = ['o-x', 'A']       # a list where an object belongs
                break
        with open(tp, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(json.dumps(r) + '\n' for r in lines)
        rc, last = run('score', '--session', sess, '--practice')
        print('RESULT label entry not an object: exit %d %s' % (rc, last))
        after = open(rep, 'rb').read()
        print('RESULT previous e2-report.json left unchanged after the crash: %s' % (before == after))

        text = open(method_mod.METHOD_PATH, encoding='utf-8').read()
        block = method_mod.read_block(method_mod.METHOD_PATH)
        block['room'] = {'open': False, 'proposed': {'exits': [[-2.4, 4.4]], 'floor': [-2.2, 0.8, 2.2, 5.2]},
                         'why': 'owner measured'}
        start = text.index('```json den-track-method')
        end = text.index('```', start + 10)
        mp = os.path.join(tmp, 'METHOD.md')
        with open(mp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text[:start] + '```json den-track-method\n' + json.dumps(block, indent=2) + '\n' + text[end:])
        m = method_mod.Method(mp)
        names = [n for n, _ in m.open]
        room = m.values['room']
        print('RESULT room written {"open": false, "proposed": {...}}: listed open=%s; exits the tracker gets=%r; floor=%r'
              % ('room' in names, room.get('exits'), room.get('floor')))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

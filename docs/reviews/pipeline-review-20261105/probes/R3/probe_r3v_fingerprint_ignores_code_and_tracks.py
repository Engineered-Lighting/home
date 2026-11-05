"""probe_r3v_fingerprint_ignores_code_and_tracks.py -- R3 (den-track), validate lens.

What it checks
  The scored run's "exact method" check. score refuses tracks made under
  another METHOD.md fingerprint, but the fingerprint covers only METHOD.md's
  JSON block: not the tracker code, and tracks.run.json does not hash
  tracks.jsonl. This checks that
    (a) after a change to the tracker CODE (tracker_core.py) the same takes
        re-track and re-score as SCORED under the same method fingerprint,
        with a different result and no refusal or note; and
    (b) tracks.jsonl edited after tracking (a confident record turned
        ambiguous) is scored without a problem.
  So a diagnosis-informed code change, which per the ladder needs fresh
  held-out takes, is invisible to the scored run and to e2-report.json.

How
  Copies tools/den-track to a temp folder (the checkout is not touched), a
  closed METHOD.md copy (room = the synthetic den), 20 synthetic takes
  (touching crossings, typical noise); track + score; then a one-line change
  to the copy's tracker_core.py (doubt accumulates only on risk >= 0.6);
  re-track + re-score; then an edit of one tracks.jsonl.

Run (stdlib only, synthetic data, temp folders only):
  python3 -B probe_r3v_fingerprint_ignores_code_and_tracks.py <branch checkout root>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')

CLOSE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import method, synth
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
block['room'] = synth.ROOM_MODEL
start = text.index(method.FENCE)
end = text.index('\n```', start + len(method.FENCE))
open(sys.argv[2], 'w', encoding='utf-8', newline='\n').write(
    text[:start] + method.FENCE + '\n' + json.dumps(block, indent=2) + text[end:])
'''


def cli(tool, *args):
    p = subprocess.run([sys.executable, '-B', os.path.join(tool, 'den_track.py')] + list(args),
                       capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    return p.returncode, p.stdout, p.stderr


def summary(session):
    rep = json.load(open(os.path.join(session, 'e2-report.json'), encoding='utf-8'))
    return rep


def main():
    tmp = tempfile.mkdtemp(prefix='probe-r3v-fp-')
    try:
        tool = os.path.join(tmp, 'den-track')
        shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), tool,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        m = os.path.join(tmp, 'METHOD.md')
        subprocess.run([sys.executable, '-B', '-c', CLOSE, tool, m], check=True,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        session = os.path.join(tmp, 'session')
        code, out, err = cli(tool, '--method', m, 'synth', '--out', session, '--seeds', '20',
                             '--scenes', 'touching_crossing', '--noise', 'typical')
        assert code == 0, err
        assert cli(tool, '--method', m, 'track', '--session', session)[0] == 0
        c1, o1, e1 = cli(tool, '--method', m, 'score', '--session', session)
        r1 = summary(session)
        # (a) change the tracker code, not METHOD.md
        path = os.path.join(tool, 'tracker_core.py')
        src = open(path, encoding='utf-8').read()
        assert src.count("        if risk >= p['risk_floor']:") == 1
        open(path, 'w', encoding='utf-8', newline='\n').write(
            src.replace("        if risk >= p['risk_floor']:", "        if risk >= 0.6:"))
        assert cli(tool, '--method', m, 'track', '--session', session)[0] == 0
        c2, o2, e2 = cli(tool, '--method', m, 'score', '--session', session)
        r2 = summary(session)
        print('RESULT before code change: exit %d, %s, fingerprint %s, silent %d, reported %d, ambiguous share %.3f'
              % (c1, 'practice' if r1['practice'] else 'SCORED', r1['method_fingerprint'][:23], r1['silent_swaps'],
                 r1['reported_switches'], r1['ambiguous_share']))
        print('RESULT after code change : exit %d, %s, fingerprint %s, silent %d, reported %d, ambiguous share %.3f, '
              'problems %r' % (c2, 'practice' if r2['practice'] else 'SCORED', r2['method_fingerprint'][:23],
                               r2['silent_swaps'], r2['reported_switches'], r2['ambiguous_share'], r2['problems']))
        fails = 0
        changed = (r1['silent_swaps'], r1['reported_switches'], r1['ambiguous_share']) != \
                  (r2['silent_swaps'], r2['reported_switches'], r2['ambiguous_share'])
        if not r2['practice'] and r1['method_fingerprint'] == r2['method_fingerprint'] and changed:
            fails += 1
            print('FAIL (a) a tracker code change gives different scored results under the same method fingerprint, '
                  'with no refusal and nothing in the report to tell the two runs apart')
        # (b) edit tracks.jsonl after tracking
        take = sorted(d for d in os.listdir(session) if os.path.isdir(os.path.join(session, d)))[0]
        tpath = os.path.join(session, take, 'tracks.jsonl')
        lines = [json.loads(l) for l in open(tpath, encoding='utf-8')]
        flipped = 0
        for rec in lines:
            if rec['association']['status'] == 'confident' and rec['state'] == 'confirmed' and flipped < 5:
                rec['association']['status'] = 'ambiguous'
                flipped += 1
        open(tpath, 'w', encoding='utf-8', newline='\n').writelines(json.dumps(r) + '\n' for r in lines)
        c3, o3, e3 = cli(tool, '--method', m, 'score', '--session', session)
        r3 = summary(session) if c3 in (0, 1) else None
        print('RESULT after editing %d records of %s/tracks.jsonl: exit %d, problems %r, stderr %r'
              % (flipped, take, c3, r3 and r3['problems'], e3.strip()[:120]))
        if c3 in (0, 1) and r3 and not r3['problems']:
            fails += 1
            print('FAIL (b) tracks.jsonl edited after tracking is scored without a problem (no hash of the tracks)')
        print('FAIL count: %d' % fails)
        return 1 if fails else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

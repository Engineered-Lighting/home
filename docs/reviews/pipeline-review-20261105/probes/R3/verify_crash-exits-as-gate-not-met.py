"""verify_crash-exits-as-gate-not-met: independent re-check that malformed
inputs make den_track.py exit 1 (documented as "score computed but gate not
met") with a traceback, instead of 2 ("refused or an input error").

Cases, each on a synthetic session in a temp dir (closed copy of METHOD.md):
  contrast  missing truth.jsonl            -> expected handled, exit 2
  a  tracks.run.json empty (what a kill right after open('w') leaves)
  b  tracks.run.json cut in half (Ctrl-C mid json.dump; the with-block flushes)
  c  truth.jsonl as UTF-16LE with BOM (PowerShell 5.1 '>' / Out-File default)
  d  a slot "closed" as {"open": false, ...} (flag flipped instead of replaced)
  e  a slot written {"open": "true", ...}
Also records whether a stale e2-report.json from an earlier run is left in
place after the crash.

Synthetic data only, temp dirs only, no network.

Run:  python3 verify_crash-exits-as-gate-not-met.py <root of the rung-3-track checkout>
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
FENCE = '```json den-track-method'


def write_method(src, dst, slot_patch=None):
    text = open(src, encoding='utf-8').read()
    start = text.index(FENCE)
    end = text.index('\n```', start + len(FENCE))
    block = json.loads(text[start + len(FENCE):end])

    def close(node):
        if isinstance(node, dict) and node.get('open') is True:
            return node['proposed']
        if isinstance(node, dict):
            return {k: close(v) for k, v in node.items()}
        return node
    block = close(block)
    if slot_patch:
        slot_patch(block)
    open(dst, 'w', encoding='utf-8', newline='\n').write(
        text[:start] + FENCE + '\n' + json.dumps(block, indent=2) + text[end:])


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    lines = (p.stderr or p.stdout).strip().splitlines()
    return p.returncode, (lines[-1] if lines else ''), 'Traceback' in p.stderr


def main():
    tmp = tempfile.mkdtemp(prefix='verify_r3_exit_')
    rows = []
    try:
        src = os.path.join(TOOL, 'METHOD.md')
        m = os.path.join(tmp, 'METHOD.md')
        write_method(src, m)
        sess = os.path.join(tmp, 'session')
        assert run('--method', m, 'synth', '--out', sess, '--seeds', '1', '--scenes', 'clean_crossing')[0] == 0
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        take = os.path.join(sess, os.listdir(sess)[0])
        rc, last, tb = run('--method', m, 'score', '--session', sess)
        report = os.path.join(sess, 'e2-report.json')
        print('baseline scored run: exit %d (1 = gate not met: 1 window < 20), report written: %s'
              % (rc, os.path.isfile(report)))
        report_mtime = os.path.getmtime(report)

        truth = os.path.join(take, 'truth.jsonl')
        runf = os.path.join(take, 'tracks.run.json')
        good_truth = open(truth, encoding='utf-8').read()
        good_run = open(runf, encoding='utf-8').read()

        os.rename(truth, truth + '.bak')
        rows.append(('contrast: no truth.jsonl', 'score') + run('--method', m, 'score', '--session', sess))
        os.rename(truth + '.bak', truth)

        open(runf, 'w').close()
        rows.append(('a: tracks.run.json empty', 'score') + run('--method', m, 'score', '--session', sess))
        open(runf, 'w', encoding='utf-8').write(good_run[:len(good_run) // 2])
        rows.append(('b: tracks.run.json cut in half', 'score') + run('--method', m, 'score', '--session', sess))
        open(runf, 'w', encoding='utf-8').write(good_run)

        with open(truth, 'wb') as f:
            f.write(b'\xff\xfe' + good_truth.replace('\n', '\r\n').encode('utf-16-le'))
        rows.append(('c: truth.jsonl UTF-16LE+BOM', 'score') + run('--method', m, 'score', '--session', sess))
        open(truth, 'w', encoding='utf-8', newline='\n').write(good_truth)

        m_false = os.path.join(tmp, 'METHOD-false.md')
        write_method(src, m_false, lambda b: b['tracker'].update(
            confident_at={'open': False, 'proposed': 0.9, 'why': 'closed by flipping the flag'}))
        rows.append(('d: slot {"open": false}', 'track') + run('--method', m_false, 'track', '--session', sess))
        m_str = os.path.join(tmp, 'METHOD-str.md')
        write_method(src, m_str, lambda b: b['tracker'].update(
            confident_at={'open': 'true', 'proposed': 0.9, 'why': 'x'}))
        rows.append(('e: slot {"open": "true"}', 'track') + run('--method', m_str, 'track', '--session', sess))

        stale = os.path.isfile(report) and os.path.getmtime(report) == report_mtime
        print('%-34s %-6s %-5s %-9s %s' % ('case', 'cmd', 'exit', 'traceback', 'last line'))
        bad = []
        for name, cmd, rc, last, tb in rows:
            print('%-34s %-6s %-5d %-9s %s' % (name, cmd, rc, tb, last[:110]))
            if rc == 1 and tb:
                bad.append(name.split(':')[0])
        print('e2-report.json from the earlier run still in place, unchanged, after the crashes: %s' % stale)
        print('RESULT: %s' % (('FAIL: traceback with exit 1 (documented: gate not met) in cases %s' % ', '.join(bad))
                              if bad else 'PASS: every malformed input exits 2'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

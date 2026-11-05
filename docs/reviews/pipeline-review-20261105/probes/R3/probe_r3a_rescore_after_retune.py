"""probe_r3a_rescore_after_retune: nothing stops the same held-out takes
being scored, retuned and scored again, and nothing records that it happened.

Steps (synthetic data, temp dirs only, no network):
  1. `score --practice` with the repository METHOD.md (slots OPEN) on a
     session of synthetic takes: the full gate verdict and per-window table
     print before any slot is closed (a peek at held-out results).
  2. Closes the slots in a copy of METHOD.md, tracks, scores (report 1).
  3. Edits a tracker number in that METHOD.md, keeps "version":
     "den-track-method/1", re-tracks and scores the SAME takes: expect a
     refusal (takes already scored under another fingerprint) or at least a
     version change; also looks for any trace of report 1.
  4. Edits truth.jsonl after scoring (a frame relabelled "?") and scores
     again: expect a refusal or a truth hash in the report.

Run:  python3 probe_r3a_rescore_after_retune.py <root of the rung-3-track checkout>
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
    with open(method_mod.METHOD_PATH, encoding='utf-8') as f:
        text = f.read()
    resolved = method_mod.resolve(method_mod.read_block(method_mod.METHOD_PATH))
    resolved['room'] = synth.ROOM_MODEL
    if mutate:
        mutate(resolved)
    start = text.index('```json den-track-method')
    end = text.index('```', start + 10)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + '```json den-track-method\n' + json.dumps(resolved, indent=2) + '\n' + text[end:])


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    tmp = tempfile.mkdtemp(prefix='r3a_res_')
    try:
        sess = os.path.join(tmp, 's')
        assert run('synth', '--out', sess, '--seeds', '2', '--scenes', 'touching_crossing,near_pass')[0] == 0
        assert run('track', '--session', sess)[0] == 0          # repository METHOD.md, slots open
        rc, out = run('score', '--session', sess, '--practice')
        peek = [l for l in out.splitlines() if l.startswith(('E2', 'pooled', 'gate', 'silent'))]
        print('step 1 (slots open, --practice on the session): exit %d; %s' % (rc, peek))
        peek_bad = any(l.startswith('gate:') for l in peek)

        m = os.path.join(tmp, 'METHOD.md')
        write_method(m)
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        rc1, out1 = run('--method', m, 'score', '--session', sess)
        r1 = json.load(open(os.path.join(sess, 'e2-report.json')))
        print('step 2 scored: exit %d, version %s, fingerprint %s..., IDF1 %.3f, reported %d'
              % (rc1, r1['method_version'], r1['method_fingerprint'][:19], r1['pooled']['idf1'], r1['reported_switches']))

        write_method(m, lambda v: v['tracker'].update(gate_chi2=9.21, settle_s=1.5))
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        rc2, out2 = run('--method', m, 'score', '--session', sess)
        refused = rc2 == 2
        r2 = json.load(open(os.path.join(sess, 'e2-report.json')))
        trace = [f for f in os.listdir(sess) if os.path.isfile(os.path.join(sess, f))]
        print('step 3 re-scored the same takes after a retune: exit %d %s, version %s, fingerprint %s..., IDF1 %.3f, reported %d'
              % (rc2, '(REFUSED)' if refused else '(accepted)', r2.get('method_version'), r2.get('method_fingerprint', '')[:19],
                 r2['pooled']['idf1'], r2['reported_switches']))
        print('  session folder files: %s (report 1 kept: %s)' % (trace, any(r1['method_fingerprint'] in open(os.path.join(sess, f)).read() for f in trace)))
        retune_bad = not refused and r2['method_version'] == r1['method_version']

        take = sorted(d for d in os.listdir(sess) if os.path.isdir(os.path.join(sess, d)))[0]
        tp = os.path.join(sess, take, 'truth.jsonl')
        lines = [json.loads(l) for l in open(tp, encoding='utf-8') if l.strip()]
        for l in lines:
            if l['line'] == 'frame' and l['labels']:
                l['labels'] = [dict(x, person='?') for x in l['labels']]
                break
        with open(tp, 'w', encoding='utf-8', newline='\n') as f:
            for l in lines:
                f.write(json.dumps(l) + '\n')
        rc3, out3 = run('--method', m, 'score', '--session', sess)
        r3 = json.load(open(os.path.join(sess, 'e2-report.json')))
        has_hash = 'truth' in json.dumps(r3.get('takes')) and 'sha256' in json.dumps(r3.get('takes'))
        print('step 4 truth edited after scoring: exit %d %s; report names a truth hash: %s'
              % (rc3, '(REFUSED)' if rc3 == 2 else '(accepted)', has_hash))
        print('RESULT practice peek: %s' % ('FAIL: the gate verdict on the real takes prints while slots are open' if peek_bad else 'PASS'))
        print('RESULT retune re-score: %s' % ('FAIL: same takes re-scored under a new fingerprint, same version string, no trace of the earlier score' if retune_bad else 'PASS'))
        print('RESULT truth edit: %s' % ('FAIL: truth edited after scoring is re-scored with no hash or refusal' if rc3 != 2 and not has_hash else 'PASS'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

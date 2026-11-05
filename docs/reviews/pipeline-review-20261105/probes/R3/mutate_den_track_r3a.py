"""mutate_den_track_r3a: apply gate-logic mutants to a temp copy of
tools/den-track, run the narrowest test module(s) with failfast, and print a
caught/survived table.  Never touches the checkout it is given.

Run:  python3 mutate_den_track_r3a.py <root of the rung-3-track checkout>
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
SRC = os.path.join(ROOT, 'tools', 'den-track')

MUTANTS = [
    ('scorer_core.py', "'silent_swaps_zero': failed == 0 and outside_silent == 0,", "'silent_swaps_zero': failed == 0,", 'test_scorer'),
    ('scorer_core.py', "'windows_met': n >= min_windows,", "'windows_met': n >= 0,", 'test_scorer test_cli'),
    ('scorer_core.py', "'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate,", "'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate - 0.1,", 'test_scorer test_cli'),
    ('scorer_core.py', "'confident': confident, 'silent': confident is True})", "'confident': confident, 'silent': confident is True and before != 'A'})", 'test_scorer'),
    ('scorer_core.py', "failed = sum(1 for r in rows if r['silent_swaps'] > 0)", "failed = sum(1 for r in rows if r['silent_swaps'] > 1)", 'test_scorer'),
    ('pipeline.py', "        if run.get('observations_sha256') != obs_hash:", "        if False:", 'test_cli'),
    ('pipeline.py', "        if run.get('open_slots'):", "        if False:", 'test_cli'),
    ('pipeline.py', "        if rec['state'] not in scored_states:", "        if rec['state'] == 'ended':", 'test_cli test_scorer'),
    ('tracker_core.py', "        if not tr.ambiguous and tr.p_keep < p['confident_at']:", "        if not tr.ambiguous and tr.p_keep < p['confident_at'] - 0.3:", 'test_tracker'),
    ('scorer_math.py', "        return 1 - alpha ** (1 / n)", "        return 1 - alpha ** (1 / (n + 1))", 'test_scorer'),
]


def main():
    rows = []
    for i, (fname, old, new, tests) in enumerate(MUTANTS, 1):
        tmp = tempfile.mkdtemp(prefix='r3a_mut_')
        try:
            dst = os.path.join(tmp, 'den-track')
            shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__'))
            p = os.path.join(dst, fname)
            text = open(p, encoding='utf-8').read()
            if text.count(old) != 1:
                rows.append((i, fname, new.strip()[:60], 'not-run', 'pattern count %d' % text.count(old)))
                continue
            open(p, 'w', encoding='utf-8').write(text.replace(old, new))
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            r = subprocess.run([sys.executable, '-m', 'unittest', '-f'] + tests.split(), cwd=dst, env=env,
                               capture_output=True, text=True, timeout=600)
            tail = (r.stderr or r.stdout).strip().splitlines()
            failed_test = next((l for l in tail if l.startswith(('FAIL:', 'ERROR:'))), '')
            rows.append((i, fname, new.strip()[:60], 'caught' if r.returncode else 'survived', failed_test[:70] or tests))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print('%-3s %-16s %-62s %-9s %s' % ('#', 'file', 'mutant', 'result', 'caught by'))
    for row in rows:
        print('%-3s %-16s %-62s %-9s %s' % row)


if __name__ == '__main__':
    main()

"""mutate_den_track_attack: mutation run over den-track's gate logic (attack lens).

Each mutant is one exact text replacement in one file of tools/den-track. For
each, the tool folder is copied to a fresh temp dir, the mutant is applied,
and the branch's own suite (test_scorer.py, the only test module) runs with
failfast. A mutant is "caught" when the suite fails, "survived" when it passes.
The checkout itself is never written.

Run:  python3 -B mutate_den_track_attack.py <branch checkout root>
Prints a caught/survived table and a RESULT line.
"""
import os
import shutil
import subprocess
import sys
import tempfile

MUTANTS = [
    ('scorer_core.py', "'passed': n > 0 and failed == 0 and pooled_idf1",
     "'passed': n > 0 and pooled_idf1", 'gate ignores silent swaps'),
    ('scorer_core.py', "pooled_idf1 is not None and pooled_idf1 >= idf1_gate,\n        },",
     "pooled_idf1 is not None and pooled_idf1 > idf1_gate,\n        },", 'IDF1 gate strict > instead of >='),
    ('scorer_core.py', "silent = conf is not None and conf >= confident_at",
     "silent = conf is None or conf >= confident_at", 'missing confidence counted silent (fail closed)'),
    ('scorer_core.py', "inside = [l for l in links if w['start'] <= l[0] <= w['end']]",
     "inside = [l for l in links if w['start'] < l[0] <= w['end']]", 'window start made exclusive'),
    ('scorer_core.py', "sw = [s for s in all_switches if w['start'] <= s['time'] <= w['end']]",
     "sw = list(all_switches)", 'every switch counted in every window'),
    ('scorer_core.py', "failed = sum(1 for r in rows if r['silent_swaps'] > 0)",
     "failed = sum(r['silent_swaps'] for r in rows)", 'bound counts swaps, not crossings'),
    ('scorer_core.py', "key=lambda l: (l[0], str(l[3]), l[1])", "key=lambda l: (l[1],)",
     'switches ordered by obs id, not time'),
    ('scorer_math.py', "if binomial_cdf(failures, n, mid) > alpha:", "if binomial_cdf(failures, n, mid) < alpha:",
     'Clopper-Pearson bisection inverted'),
    ('method.py', "        if self.open:\n", "        if False:\n", 'require_closed never refuses'),
    ('method.py', "return isinstance(value, dict) and value.get('open') is True",
     "return False", 'no slot is ever open'),
    ('tracker_core.py', "tr.confidence *= (1.0 - risk[i])", "tr.confidence *= 1.0",
     'tracker confidence never falls'),
    ('associate.py', "                    moved += w\n", "                    agree += w\n",
     'association risk always 0'),
]


def run(root, file, old, new):
    tmp = tempfile.mkdtemp(prefix='r3mut-')
    tool = os.path.join(tmp, 'den-track')
    shutil.copytree(os.path.join(root, 'tools', 'den-track'), tool)
    path = os.path.join(tool, file)
    src = open(path, encoding='utf-8').read()
    if src.count(old) != 1:
        shutil.rmtree(tmp)
        return 'not-run', 'pattern found %d times' % src.count(old)
    open(path, 'w', encoding='utf-8').write(src.replace(old, new))
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-f', 'test_scorer'], cwd=tool,
                       capture_output=True, text=True, timeout=600)
    shutil.rmtree(tmp)
    if p.returncode == 0:
        return 'survived', ''
    lines = [l for l in p.stderr.splitlines() if l.startswith(('FAIL:', 'ERROR:'))]
    return 'caught', lines[0] if lines else p.stderr.strip().splitlines()[-1]


def main(root):
    base = run(root, 'scorer_core.py', 'import', 'import')   # identity mutant: the suite must pass
    print('baseline (unmutated copy): %s' % ('suite passes' if base[0] == 'survived' else base))
    survived = 0
    print('%-16s %-48s %-9s %s' % ('file', 'mutant', 'result', 'caught by'))
    for file, old, new, what in MUTANTS:
        result, by = run(root, file, old, new)
        survived += result == 'survived'
        print('%-16s %-48s %-9s %s' % (file, what, result, by))
    print('RESULT: %d of %d mutants survived' % (survived, len(MUTANTS)))
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B mutate_den_track_attack.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))

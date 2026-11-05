"""mutate_den_track_spec_fullsuite: re-run the gate-logic mutants that a
narrow-module run reported as surviving, this time against the WHOLE
den-track suite (every test_*.py), to check whether any other module
catches them.  Applies each mutant to a temp copy; never touches the
checkout it is given.

Run:  python3 mutate_den_track_spec_fullsuite.py <root of the rung-3-track checkout>

Prints one row per mutant: caught (with the first failing test) or survived.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
SRC = os.path.join(ROOT, 'tools', 'den-track')

MUTANTS = [
    ('M3 idf1 gate - 0.1', 'scorer_core.py',
     "'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate,",
     "'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate - 0.1,"),
    ('M5 one silent switch not a failure', 'scorer_core.py',
     "failed = sum(1 for r in rows if r['silent_swaps'] > 0)",
     "failed = sum(1 for r in rows if r['silent_swaps'] > 1)"),
    ('M7 open_slots run check removed', 'pipeline.py',
     "        if run.get('open_slots'):",
     "        if False:"),
    ('M8 tentative records scored', 'pipeline.py',
     "        if rec['state'] not in scored_states:",
     "        if rec['state'] == 'ended':"),
    ('M8b every state scored', 'pipeline.py',
     "        if rec['state'] not in scored_states:",
     "        if False:"),
]


def main():
    rows = []
    for label, fname, old, new in [('M0 no mutation (baseline)', 'scorer_core.py', 'import', 'import')] + MUTANTS:
        tmp = tempfile.mkdtemp(prefix='r3spec_mut_')
        try:
            dst = os.path.join(tmp, 'tools', 'den-track')
            shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__'))
            # test_records reads ../../docs/architecture/live-v1/schemas.json
            shutil.copytree(os.path.join(ROOT, 'docs', 'architecture', 'live-v1'),
                            os.path.join(tmp, 'docs', 'architecture', 'live-v1'))
            p = os.path.join(dst, fname)
            with open(p, encoding='utf-8') as f:
                text = f.read()
            if old != 'import' and text.count(old) != 1:
                rows.append((label, 'not-run', 'pattern count %d' % text.count(old)))
                continue
            with open(p, 'w', encoding='utf-8') as f:
                f.write(text if old == 'import' else text.replace(old, new))
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-f', '-s', dst, '-t', dst],
                               cwd=dst, env=env, capture_output=True, text=True, timeout=900)
            out = (r.stderr or '') + (r.stdout or '')
            lines = out.strip().splitlines()
            first = next((l for l in lines if l.startswith(('FAIL:', 'ERROR:'))), '')
            ran = next((l for l in lines if l.startswith('Ran ')), '')
            rows.append((label, 'caught' if r.returncode else 'survived', (first or ran)[:90]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print('%-38s %-9s %s' % ('mutant', 'result', 'detail (full suite, failfast)'))
    for row in rows:
        print('RESULT %-31s %-9s %s' % row)


if __name__ == '__main__':
    main()

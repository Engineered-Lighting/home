"""mutate_den_track.py -- R3 (den-track) validate lens: mutation runner.

Applies each mutant below, one at a time, to a fresh copy of
tools/den-track (plus docs/architecture/live-v1, which test_records reads)
under a scratch folder, runs the den-track unittest suite with failfast, and
prints a caught/survived table. The checkout itself is never touched.

Run (stdlib only; ~15 s per mutant):
  python3 -B mutate_den_track.py <branch checkout root> [scratch folder]
Default scratch folder: a new temp folder. Optional third arg: comma list of
mutant ids to run.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
SCRATCH = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tempfile.mkdtemp(prefix='mut-den-track-')
ONLY = set(sys.argv[3].split(',')) if len(sys.argv) > 3 else None

# id, file, old, new, what it breaks
MUTANTS = [
    ('M01', 'scorer_core.py', "pooled_idf1 >= idf1_gate", "pooled_idf1 > idf1_gate",
     'IDF1 gate boundary: exactly 0.9 no longer passes'),
    ('M02', 'scorer_core.py', "'silent_swaps_zero': failed == 0 and outside_silent == 0,",
     "'silent_swaps_zero': failed == 0,", 'silent swaps outside windows no longer fail the gate'),
    ('M03', 'scorer_core.py', "failed = sum(1 for r in rows if r['silent_swaps'] > 0)",
     "failed = sum(1 for r in rows if r['silent_swaps'] > 1)",
     'a window with exactly one silent switch is not a failed trial (gate and bound)'),
    ('M04', 'pipeline.py', "    if not practice:\n        method.require_closed()",
     "    if not practice:\n        pass", 'scored run no longer calls require_closed (open-slot refusal)'),
    ('M05', 'pipeline.py', "        if rec['state'] not in scored_states:\n            continue",
     "        if False:\n            continue", 'tentative/coasting/ended records are scored too'),
    ('M06', 'method.py', "                walk('%s.%s' % (prefix, k) if prefix else k, v)",
     "                walk(k, v) if not prefix else None",
     'open_slots finds only top-level slots: nested open slots no longer block a scored run'),
    ('M07', 'scorer_math.py', "        return 1 - alpha ** (1 / n)", "        return 3 / n",
     'bound with 0 failures becomes the rule of three'),
    ('M08', 'scorer_core.py', "        sw = [s for s in all_switches if w['start'] <= s['time'] <= w['end']]",
     "        sw = switches(inside)", 'switches found inside the window only (person before the window forgotten)'),
    ('M09', 'tracker_core.py', "        if risk >= p['risk_floor']:", "        if risk >= 0.5:",
     'doubt accumulates only on large per-frame risk'),
    ('M10', 'pipeline.py', "        if want and want != obs_hash:", "        if False:",
     'truth.jsonl naming a different observations file is accepted in a scored run'),
    ('M11', 'records.py', "'status': 'ambiguous' if tr.ambiguous else 'confident',", "'status': 'confident',",
     'every record says confident'),
    ('M12', 'tracker_core.py', "            if m.get('heading') is not None and not tr.ambiguous:",
     "            if m.get('heading') is not None:", 'heading/heading_as_of move while the track is ambiguous'),
    ('M13', 'truth.py', "        if b['start'] < a['end']:", "        if False:", 'overlapping windows accepted'),
    ('M14', 'scorer_core.py', "        'windows_met': n >= min_windows,",
     "        'windows_met': n >= min_windows or n > 0,", 'any non-zero number of windows meets the count'),
]


def copy_tree(dst):
    shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), os.path.join(dst, 'tools', 'den-track'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(os.path.join(ROOT, 'docs', 'architecture', 'live-v1'),
                    os.path.join(dst, 'docs', 'architecture', 'live-v1'))


def run_suite(tool_dir):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    t0 = time.time()
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-f', '-s', '.'], cwd=tool_dir,
                       env=env, capture_output=True, text=True, timeout=900)
    tail = [l for l in p.stderr.splitlines() if l.startswith(('FAIL:', 'ERROR:', 'Ran ', 'OK', 'FAILED'))]
    return p.returncode, time.time() - t0, tail


def main():
    os.makedirs(SCRATCH, exist_ok=True)
    base = os.path.join(SCRATCH, 'baseline')
    if not os.path.isdir(base):
        copy_tree(base)
    code, secs, tail = run_suite(os.path.join(base, 'tools', 'den-track'))
    print('baseline: exit %d in %.1fs %s' % (code, secs, ' | '.join(tail)))
    if code != 0:
        print('RESULT baseline is not green; mutation results would be meaningless')
        return 2
    rows = []
    for mid, fname, old, new, what in MUTANTS:
        if ONLY and mid not in ONLY:
            continue
        d = os.path.join(SCRATCH, mid)
        shutil.rmtree(d, ignore_errors=True)
        copy_tree(d)
        path = os.path.join(d, 'tools', 'den-track', fname)
        src = open(path, encoding='utf-8').read()
        if src.count(old) != 1:
            rows.append((mid, fname, 'not-run', 'old text found %d times' % src.count(old), what))
            continue
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src.replace(old, new))
        code, secs, tail = run_suite(os.path.join(d, 'tools', 'den-track'))
        first = next((l for l in tail if l.startswith(('FAIL:', 'ERROR:'))), '')
        rows.append((mid, fname, 'caught' if code != 0 else 'survived', first or ' | '.join(tail), what))
        print('%s %-16s %-8s %5.1fs %s' % (mid, fname, rows[-1][2], secs, rows[-1][3]), flush=True)
    print('')
    print('%-4s %-16s %-9s %s' % ('id', 'file', 'result', 'mutation / caught by'))
    for mid, fname, res, by, what in rows:
        print('%-4s %-16s %-9s %s -- %s' % (mid, fname, res, what, by))
    survived = [r[0] for r in rows if r[2] == 'survived']
    print('RESULT %d mutants, %d caught, %d survived: %s' % (len(rows), sum(1 for r in rows if r[2] == 'caught'),
                                                          len(survived), ', '.join(survived) or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

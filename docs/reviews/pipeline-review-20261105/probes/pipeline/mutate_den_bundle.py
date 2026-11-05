"""mutate_den_bundle.py: mutants on router v0's rate rules, its gate and the assembler's causality.

What it does
  Copies tools/den-bundle from the given checkout into a fresh temp folder per mutant,
  applies one textual mutation, runs that copy's own suite failfast
  (python3 -B -m unittest discover -s <copy> -f), and prints a caught/survived table.
  The mutants target the pipeline-level rules: the 4 s interval, the 15-a-minute cap,
  the 30 s back-off, the timeout, round-robin, track eligibility, the per-person period,
  and reading evidence captured after the send time.

How to run
  python3 -I mutate_den_bundle.py <claude/rung-5-bundle checkout root> [work dir]

Synthetic data only; the checkout is never modified.
"""
import os
import shutil
import subprocess
import sys
import tempfile

MUTANTS = [
    ('router.py', "t = max(t, self.starts[-1] + self.rule['min_interval_s'])", "t = max(t, self.starts[-1])",
     'no 4 s interval between starts'),
    ('router.py', "if outcome != 'ok':\n            self.backoff_until", "if outcome not in ('ok', 'refused'):\n            self.backoff_until",
     'no back-off after a refusal'),
    ('router.py', "if len(self.starts) >= cap:", "if len(self.starts) > cap:", 'per-minute cap allows 16'),
    ('router.py', "order = order[i + 1:] + order[:i + 1]", "order = order", 'no rotation: first-seen track always first'),
    ('router.py', "line.ts >= now - self.method['window_s'] - EPS", "line.ts >= now - 10 * self.method['window_s'] - EPS",
     'a track silent for 30 s stays eligible'),
    ('router.py', "now >= rec['sent_ts'] + self.rule['timeout_s'] - EPS", "now >= rec['sent_ts'] + 2 * self.rule['timeout_s'] - EPS",
     'timeout doubled'),
    ('router.py', "self.rule['minute_s'] / float(self.rule['max_per_minute'])) * len(tracks)",
     "self.rule['minute_s'] / float(self.rule['max_per_minute'])) * 1", 'per_person_period_s ignores the track count'),
    ('assemble.py', "if o.get('kind') not in PERSON_KINDS or not is_valid(o) or not inside(o['capture_ts'], t0, t1):",
     "if o.get('kind') not in PERSON_KINDS or not is_valid(o) or not inside(o['capture_ts'], t0, t1 + 1.5):",
     'frames captured up to 1.5 s after the send enter the bundle'),
    ('records.py', "return lines if until is None else [l for l in lines if l.ts <= until + EPS]",
     "return lines if until is None else [l for l in lines if l.ts <= until + 1.5 + EPS]",
     'track lines dated up to 1.5 s after the send are read'),
    ('gate.py', "and r['sent_ts'] < prev['done_ts'] + rule['backoff_s'] - EPS:",
     "and r['sent_ts'] < prev['done_ts'] + rule['backoff_s'] - 30 - EPS:", 'gate backoff clause disabled'),
]


def main():
    root = os.path.abspath(sys.argv[1])
    work = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tempfile.mkdtemp(prefix='mut-den-bundle-')
    os.makedirs(work, exist_ok=True)
    src = os.path.join(root, 'tools', 'den-bundle')
    rows = []
    for n, (fname, old, new, why) in enumerate(MUTANTS, 1):
        dst = os.path.join(work, 'm%02d' % n, 'den-bundle')
        if os.path.exists(os.path.dirname(dst)):
            shutil.rmtree(os.path.dirname(dst))
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__'))
        path = os.path.join(dst, fname)
        text = open(path, encoding='utf-8').read()
        if text.count(old) != 1:
            rows.append((n, fname, why, 'not-run', 'pattern found %d times' % text.count(old)))
            continue
        open(path, 'w', encoding='utf-8', newline='\n').write(text.replace(old, new))
        p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', dst, '-f'],
                           cwd=work, capture_output=True, text=True, timeout=900)
        out = p.stdout + p.stderr
        first = next((l for l in out.splitlines() if l.startswith(('FAIL:', 'ERROR:'))), '')
        rows.append((n, fname, why, 'caught' if p.returncode != 0 else 'survived', first))
    print('%-3s %-12s %-10s %-55s %s' % ('#', 'file', 'result', 'mutation', 'caught by'))
    for n, fname, why, res, by in rows:
        print('%-3d %-12s %-10s %-55s %s' % (n, fname, res, why, by))
    print('RESULT %d caught, %d survived, %d not run' % (
        sum(r[3] == 'caught' for r in rows), sum(r[3] == 'survived' for r in rows), sum(r[3] == 'not-run' for r in rows)))


if __name__ == '__main__':
    main()

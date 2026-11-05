"""Mutation runner for tools/den-bundle (rung 5), attack lens. (Named *_attack so it does not
collide with another reviewer's mutate_den_bundle.py in this shared folder.)

Applies each mutant (one exact string replacement) to a fresh temp copy of tools/den-bundle,
runs the narrowest test module that should catch it with unittest -f, and if it survives
re-runs the whole suite (discover -f) to confirm. It also replays the CLI end to end
(fixtures -> route -> gate) on the crossing fixture and prints the gate's exit code, to show
whether the gate itself would notice the mutant on routed fixture data (0 = gate passes).
Prints a caught/survived table.

How to run
  python3 mutate_den_bundle_attack.py <root of the branch checkout> [mutant ids...]
Temp dirs only; the checkout is never modified.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
SRC = os.path.join(ROOT, 'tools', 'den-bundle')
ONLY = set(sys.argv[2:])

MUTANTS = [
    ('M1', 'assemble.py', "'missing': [s for s in SOURCES if flags[s]],", "'missing': list(SOURCES),", 'test_assemble'),
    ('M2', 'gate.py', 'return not times or any(b - a > max_gap + EPS', 'return not times or any(b - a > 2 * max_gap + EPS', 'test_gate'),
    ('M3', 'gate.py', "if b['track_id'] in c['track_ids'] and t0 <= c1 + EPS and c0 <= t1 + EPS:",
     "if b['track_id'] in c['track_ids'] and c0 - EPS <= t0 and t1 <= c1 + EPS:", 'test_gate'),
    ('M4', 'gate.py', 'if len(window) > cap:', 'if len(window) > cap + 1:', 'test_gate'),
    ('M5', 'gate.py', "r['sent_ts'] < prev['done_ts'] + rule['backoff_s'] - EPS", "r['sent_ts'] < prev['done_ts'] + rule['backoff_s'] / 2 - EPS", 'test_gate'),
    ('M6', 'gate.py', "if r['track_id'] != expected:", 'if False:', 'test_gate'),
    ('M7', 'gate.py', "r['sent_ts'] < prev['done_ts'] - EPS", "r['sent_ts'] < prev['done_ts'] - 5", 'test_gate'),
    ('M8', 'router.py', "        if outcome != 'ok':\n            self.backoff_until", "        if outcome == 'refused':\n            self.backoff_until", 'test_router'),
    ('M9', 'router.py', "t = max(t, self.starts[-cap] + self.rule['minute_s'])", "t = max(t, self.starts[-cap] + self.rule['minute_s'] - 1)", 'test_router'),
    ('M10', 'assemble.py', 'if isinstance(since, (int, float)) and since > t0 + EPS and first_ts < since - EPS:', 'if False:', 'test_assemble'),
    ('M11', 'assemble.py', "statuses == {'confident'}", "'confident' in statuses", 'test_assemble'),
    ('M12', 'gate.py', "'keypoints': o.get('keypoints'), 'joints_room': o.get('joints_room')}", "'keypoints': o.get('keypoints')}", 'test_gate'),
    ('M13', 'router.py', "\n                    and line.ts >= now - self.method['window_s'] - EPS):", '):', 'test_router'),
    ('M14', 'gate.py', "if b['association_safe'] is not False:", "if b['association_safe'] is not False and False:", 'test_gate'),
    ('M15', 'records.py', 'ts = max(known) if known else None', 'ts = min(known) if known else None', 'test_assemble'),
    ('M16', 'gate.py', "or stamp.get('sha256') != rule.sha256:", ':', 'test_gate'),
    ('M17', 'assemble.py', "'depth': not kp_obs or not all(depth_usable(o, method['depth_skew_s']) for o in kp_obs),",
     "'depth': not kp_obs,", 'test_assemble'),
    ('M18', 'router.py', "            order = order[i + 1:] + order[:i + 1]\n", '', 'test_router'),
]


def run_tests(d, module=None):
    if module:
        args = [sys.executable, '-B', '-m', 'unittest', '-f', module]
    else:
        args = [sys.executable, '-B', '-m', 'unittest', 'discover', '-f', '-s', '.', '-p', 'test_*.py']
    p = subprocess.run(args, cwd=d, capture_output=True, text=True, timeout=600)
    first = [l for l in p.stderr.strip().splitlines() if l.startswith(('FAIL:', 'ERROR:'))]
    tail = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''
    return p.returncode == 0, (first[0] if first else tail)


def cli_gate(d):
    cli = [sys.executable, '-B', os.path.join(d, 'den_bundle.py')]
    fx = os.path.join(d, '_fx')
    subprocess.run(cli + ['fixtures', '--out', fx], capture_output=True)
    o, t, c = (os.path.join(fx, 'crossing', n) for n in ('observations.jsonl', 'tracks.jsonl', 'crossings.jsonl'))
    rq, bd = os.path.join(d, '_rq.jsonl'), os.path.join(d, '_bd.jsonl')
    subprocess.run(cli + ['route', '--observations', o, '--tracks', t, '--from', '2.5', '--to', '13',
                          '--requests', rq, '--bundles', bd], capture_output=True)
    g = subprocess.run(cli + ['gate', '--observations', o, '--tracks', t, '--bundles', bd, '--requests', rq,
                              '--crossings', c], capture_output=True, text=True)
    return g.returncode


rows = []
for mid, fname, old, new, module in MUTANTS:
    if ONLY and mid not in ONLY:
        continue
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, 'den-bundle')
        shutil.copytree(SRC, d)
        path = os.path.join(d, fname)
        text = open(path, encoding='utf-8').read()
        if text.count(old) != 1:
            rows.append((mid, fname, 'not-run', '%d matches' % text.count(old), '', ''))
            continue
        open(path, 'w', encoding='utf-8', newline='\n').write(text.replace(old, new))
        passed, why = run_tests(d, module)
        caught_by = module if not passed else ''
        if passed:
            passed, why = run_tests(d)
            caught_by = 'full suite' if not passed else ''
        rows.append((mid, fname, 'survived' if passed else 'caught', caught_by or '-',
                     'e2e gate exit %d' % cli_gate(d), why[:100]))
print('%-4s %-12s %-9s %-14s %-17s %s' % ('id', 'file', 'result', 'caught_by', 'e2e', 'first failure'))
for r in rows:
    print('%-4s %-12s %-9s %-14s %-17s %s' % r)
n = len(rows)
print('RESULT %d mutants: %d caught, %d survived' % (n, sum(r[2] == 'caught' for r in rows), sum(r[2] == 'survived' for r in rows)))

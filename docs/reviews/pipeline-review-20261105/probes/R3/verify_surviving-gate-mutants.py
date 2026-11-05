"""verify_surviving-gate-mutants: independent re-check of the claim that
den-track's tests miss gate-logic mutants.

Mutants (each applied to a fresh temp copy of tools/den-track, never to the
checkout):
  M3  scorer_core.py  IDF1 gate lowered by 0.1 (0.9 acts as 0.8)
  M5  scorer_core.py  a window counts as failed only with MORE THAN ONE silent swap
  M7  pipeline.py     the run.json open_slots check removed (expected redundant
                      with the fingerprint check; shown for completeness)
  M8  pipeline.py     every non-ended record scored (tentative and coasting too)

Part 1 runs the WHOLE suite (unittest discover, all test modules) on each
mutant and prints caught/survived.
Part 2 shows each of M3, M5, M8 changes a verdict or a number on synthetic
input, i.e. none is an equivalent mutant:
  - M5: one window with exactly one silent switch (track 2 ends, track 1
    takes B's observations, A gets a new track 3) plus 19 clean windows;
  - M3: 20 windows, each with a fragment, pooled IDF1 0.85, no switch;
  - M8: synthetic takes run through the real tracker in practice mode;
    compares the pooled report under the original and under M8.

Synthetic data only; temp dirs only; no network.

Run:  python3 verify_surviving-gate-mutants.py <root of the rung-3-track checkout> [workdir]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
WORK = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None
SRC = os.path.join(ROOT, 'tools', 'den-track')

MUTANTS = {
    'orig': None,
    'M3': ('scorer_core.py', "pooled_idf1 >= idf1_gate,", "pooled_idf1 >= idf1_gate - 0.1,"),
    'M5': ('scorer_core.py', "failed = sum(1 for r in rows if r['silent_swaps'] > 0)",
           "failed = sum(1 for r in rows if r['silent_swaps'] > 1)"),
    'M7': ('pipeline.py', "        if run.get('open_slots'):", "        if False:"),
    'M8': ('pipeline.py', "        if rec['state'] not in scored_states:", "        if rec['state'] == 'ended':"),
}

SCENARIOS = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import scorer_core, pipeline, method, synth

def L(rows, t0, conf=True):
    return [(t0 + k, 'o%d-%d' % (t0, i), p, t, conf if t is not None else None)
            for k, frame in enumerate(rows) for i, (p, t) in enumerate(frame)]

out = {}
# M5 scenario: take 1 has one window with a one-sided switch.
w0 = L([[('A', 1), ('B', 2)]] * 5 + [[('B', 1), ('A', 3)]] * 5, 0)
t1 = scorer_core.score_take(w0, [{'id': 'w0', 'kind': 'crossing', 'start': 0, 'end': 9}])
clean = []
wins = []
for k in range(19):
    clean += L([[('A', 1), ('B', 2)]] * 10, 100 * (k + 1))
    wins.append({'id': 'c%d' % k, 'kind': 'crossing', 'start': 100 * (k + 1), 'end': 100 * (k + 1) + 9})
t2 = scorer_core.score_take(clean, wins)
res = scorer_core.pool([('t1', t1), ('t2', t2)], 0.9, 20)
out['one_sided'] = {'window_silent_swaps': t1['windows'][0]['silent_swaps'],
                    'windows': res['crossings'], 'failed_windows': res['crossings_with_silent_swap'],
                    'pooled_idf1': round(res['pooled']['idf1'], 4), 'passed': res['gate']['passed']}
# M3 scenario: 20 takes, one window each, A fragments 7 -> 3 frames, no switch.
takes = []
for k in range(20):
    rows = [[('A', 1), ('B', 2)]] * 7 + [[('A', 3), ('B', 2)]] * 3
    takes.append(('f%d' % k, scorer_core.score_take(L(rows, 0), [{'id': 'w', 'kind': 'crossing', 'start': 0, 'end': 9}])))
res = scorer_core.pool(takes, 0.9, 20)
out['idf1_085'] = {'silent': res['silent_swaps'], 'pooled_idf1': round(res['pooled']['idf1'], 4),
                   'passed': res['gate']['passed']}
# M8 scenario: practice on synthetic scenes, real tracker.
values = dict(method.Method().values, room=synth.ROOM_MODEL)
rep = pipeline.practice(values, seeds=2)
tent = 0
for name in sorted(synth.SCENES):
    take_id, recs, truth = pipeline.synthetic_take(synth.scenario(name, 0, 'typical'), values)
    tent += sum(1 for r in recs if r['state'] == 'tentative' and r['last_obs_ids'])
out['practice'] = {'idtp': rep['pooled']['idtp'], 'idfp': rep['pooled']['idfp'], 'idfn': rep['pooled']['idfn'],
                   'idf1': round(rep['pooled']['idf1'], 4), 'silent': rep['silent_swaps'],
                   'reported': rep['reported_switches'], 'failed_windows': rep['crossings_with_silent_swap'],
                   'tentative_records_with_obs_seed0': tent}
print(json.dumps(out, sort_keys=True))
'''


def make_copy(base, name):
    # Keep the repo layout: test_records reads ../../docs/architecture/live-v1/schemas.json.
    dst = os.path.join(base, name, 'tools', 'den-track')
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(os.path.join(ROOT, 'docs', 'architecture', 'live-v1'),
                    os.path.join(base, name, 'docs', 'architecture', 'live-v1'))
    m = MUTANTS[name]
    if m:
        fname, old, new = m
        p = os.path.join(dst, fname)
        text = open(p, encoding='utf-8').read()
        if text.count(old) != 1:
            raise SystemExit('FAIL: mutant %s pattern found %d times' % (name, text.count(old)))
        open(p, 'w', encoding='utf-8', newline='\n').write(text.replace(old, new))
    return dst


def main():
    if WORK:
        os.makedirs(WORK, exist_ok=True)
    base = tempfile.mkdtemp(prefix='verify_r3_mut_', dir=WORK)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    try:
        print('Part 1: whole suite (unittest discover) per mutant')
        scen = {}
        for name in MUTANTS:
            dst = make_copy(base, name)
            r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', dst], cwd=dst, env=env,
                               capture_output=True, text=True, timeout=900)
            tail = [l for l in r.stderr.strip().splitlines() if l.startswith(('Ran ', 'OK', 'FAILED', 'FAIL:', 'ERROR:'))]
            verdict = 'baseline' if name == 'orig' else ('caught' if r.returncode else 'survived')
            print('  %-5s rc=%d %-9s %s' % (name, r.returncode, verdict, ' | '.join(tail[:3])))
            s = subprocess.run([sys.executable, '-c', SCENARIOS, dst], cwd=base, env=env,
                               capture_output=True, text=True, timeout=900)
            scen[name] = json.loads(s.stdout) if s.returncode == 0 else {'error': s.stderr.strip().splitlines()[-1:]}
        print('Part 2: verdicts on synthetic input')
        for key in ('one_sided', 'idf1_085', 'practice'):
            for name in ('orig', 'M3', 'M5', 'M8'):
                print('  %-10s %-5s %s' % (key, name, json.dumps(scen[name].get(key, scen[name]), sort_keys=True)))
        o, m3, m5, m8 = scen['orig'], scen['M3'], scen['M5'], scen['M8']
        flip5 = (not o['one_sided']['passed']) and m5['one_sided']['passed']
        flip3 = (not o['idf1_085']['passed']) and m3['idf1_085']['passed']
        diff8 = o['practice'] != m8['practice']
        print('RESULT: M5 flips NOT MET -> PASSED on one silent switch: %s' % ('YES' if flip5 else 'NO'))
        print('RESULT: M3 flips NOT MET -> PASSED at pooled IDF1 0.85: %s' % ('YES' if flip3 else 'NO'))
        print('RESULT: M8 changes the practice report: %s' % ('YES' if diff8 else 'NO'))
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == '__main__':
    main()

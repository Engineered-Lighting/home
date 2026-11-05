"""verify_r3v_mut_intent.py -- R3 (den-track) verifier, intent/spec lens.

Independently re-checks six surviving-mutant findings:
  r3v-mut-idf1-boundary          M01 scorer_core.py  'pooled_idf1 >= idf1_gate' -> '>'
  r3v-mut-single-silent-switch   M03 scorer_core.py  "silent_swaps'] > 0" -> "> 1"
  r3v-mut-scored-states          M05 pipeline.py     scored_states filter removed
  r3v-mut-nested-open-slot       M06 method.py       open_slots walks the top level only
  r3v-mut-truth-hash-mismatch    M10 pipeline.py     truth-hash mismatch check disabled
  r3v-mut-heading-while-ambiguous M12 tracker_core.py heading moves while ambiguous

For each mutant it (1) applies it to a fresh copy of tools/den-track (plus
docs/architecture/live-v1, which test_records reads), runs the whole
den-track suite with failfast, and reports caught/survived; and (2) runs a
small discriminator on the ORIGINAL copy and on the MUTANT copy, so a
surviving mutant is shown to change behaviour (not an equivalent mutant).
The checkout is never touched. Synthetic data, temp folders, no network.

Run (stdlib only, ~2 min):
  python3 -B verify_r3v_mut_intent.py <branch checkout root> [scratch folder]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
SCRATCH = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tempfile.mkdtemp(prefix='verify-r3v-')

MUTANTS = [
    ('M01', 'r3v-mut-idf1-boundary', 'scorer_core.py', "pooled_idf1 >= idf1_gate", "pooled_idf1 > idf1_gate"),
    ('M03', 'r3v-mut-single-silent-switch', 'scorer_core.py',
     "failed = sum(1 for r in rows if r['silent_swaps'] > 0)",
     "failed = sum(1 for r in rows if r['silent_swaps'] > 1)"),
    ('M05', 'r3v-mut-scored-states', 'pipeline.py',
     "        if rec['state'] not in scored_states:\n            continue",
     "        if False:\n            continue"),
    ('M06', 'r3v-mut-nested-open-slot', 'method.py',
     "                walk('%s.%s' % (prefix, k) if prefix else k, v)",
     "                walk(k, v) if not prefix else None"),
    ('M10', 'r3v-mut-truth-hash-mismatch', 'pipeline.py', "        if want and want != obs_hash:",
     "        if False:"),
    ('M12', 'r3v-mut-heading-while-ambiguous', 'tracker_core.py',
     "            if m.get('heading') is not None and not tr.ambiguous:",
     "            if m.get('heading') is not None:"),
]

COMMON = r'''
import contextlib, io, json, os, shutil, sys, tempfile
sys.path.insert(0, os.getcwd())
def run_cli(*a):
    import den_track
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        c = den_track.main(list(a))
    return c, o.getvalue(), e.getvalue()
def method_copy(dst, close_all=False, close=()):
    import method
    text = open(method.METHOD_PATH, encoding='utf-8').read()
    block = json.loads(json.dumps(method.Method().block))
    def closer(node):
        if method.is_open(node):
            return node['proposed']
        if isinstance(node, dict):
            return {k: closer(v) for k, v in node.items()}
        return node
    if close_all:
        block = closer(block)
    for dotted in close:
        parts = dotted.split('.')
        node = block
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = node[parts[-1]]['proposed']
    start = text.index(method.FENCE)
    end = text.index('\n```', start + len(method.FENCE))
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + method.FENCE + '\n' + json.dumps(block, indent=2) + text[end:])
    return dst
'''

DISCRIMINATORS = {
    # IDF1 exactly 0.9: idtp 9, idfp 1, idfn 1 -> 18/20.
    'M01': r'''
import scorer_core
links = [(float(k), 'o%d' % k, 'A', 1, True) for k in range(9)] + [(9.0, 'o9', 'A', None, None), (10.0, 'o10', None, 1, True)]
res = scorer_core.pool([('t', scorer_core.score_take(links, [{'id': 'w', 'start': 0, 'end': 10}]))], 0.9, 1)
print(json.dumps({'pooled_idf1': res['pooled']['idf1'], 'idf1_met': res['gate']['idf1_met'], 'gate_passed': res['gate']['passed']}))
''',
    # One-sided jump (the METHOD.md known-limit failure): B's own track is
    # gone, B comes back on A's track 1, A goes on on a new track 3. Exactly
    # one silent switch, inside one of 20 windows; pooled IDF1 stays > 0.9.
    'M03': r'''
import scorer_core
links = []
for k in range(200):
    links += [(float(k), 'a%d' % k, 'A', 1, True), (float(k), 'b%d' % k, 'B', 2, True)]
for k in range(200, 210):
    links += [(float(k), 'b%d' % k, 'B', 1, True), (float(k), 'a%d' % k, 'A', 3, True)]
windows = [{'id': 'c%02d' % i, 'start': 10 * i, 'end': 10 * i + 5} for i in range(19)] + [{'id': 'jump', 'start': 195, 'end': 209}]
res = scorer_core.pool([('t', scorer_core.score_take(links, windows))], 0.9, 20)
print(json.dumps({'windows': res['crossings'], 'silent_swaps': res['silent_swaps'],
                  'crossings_with_silent_swap': res['crossings_with_silent_swap'],
                  'pooled_idf1': round(res['pooled']['idf1'], 4),
                  'bound': round(res['silent_swap_rate_upper_95'], 4), 'gate_passed': res['gate']['passed']}))
''',
    # A tentative record holding a labelled observation.
    'M05': r'''
import pipeline
truth = {'labels': {'o1': (1.0, 'A'), 'o2': (2.0, 'A')}, 'windows': [], 'unsure': 0}
recs = [{'track_id': 'T1', 'state': 'tentative', 'association': {'status': 'confident'}, 'last_obs_ids': ['o1']},
        {'track_id': 'T1', 'state': 'confirmed', 'association': {'status': 'confident'}, 'last_obs_ids': ['o2']}]
links, _ = pipeline.links_for_take(recs, truth, ['confirmed'])
print(json.dumps({'links': [[l[1], l[3]] for l in links]}))
''',
    # Close only the top-level slot 'room'; the four nested ones stay open.
    'M06': COMMON + r'''
import method
d = tempfile.mkdtemp(prefix='v-m06-')
try:
    m = method_copy(os.path.join(d, 'METHOD.md'), close=('room',))
    names = [n for n, _ in method.Method(m).open]
    sess = os.path.join(d, 's')
    run_cli('synth', '--out', sess, '--seeds', '1', '--scenes', 'clean_crossing')
    tc = run_cli('--method', m, 'track', '--session', sess)[0]
    c, o, e = run_cli('--method', m, 'score', '--session', sess)
    print(json.dumps({'open_slots_seen': names, 'track_exit': tc, 'score_exit': c,
                      'refused': 'refusing a scored run' in e, 'printed_SCORED': 'SCORED' in o}))
finally:
    shutil.rmtree(d, ignore_errors=True)
''',
    # Closed method; take B's truth.jsonl replaced by take A's.
    'M10': COMMON + r'''
d = tempfile.mkdtemp(prefix='v-m10-')
try:
    m = method_copy(os.path.join(d, 'METHOD.md'), close_all=True)
    sess = os.path.join(d, 's')
    run_cli('synth', '--out', sess, '--seeds', '1', '--scenes', 'clean_crossing,touching_crossing')
    a, b = sorted(os.listdir(sess))
    shutil.copyfile(os.path.join(sess, a, 'truth.jsonl'), os.path.join(sess, b, 'truth.jsonl'))
    tc = run_cli('--method', m, 'track', '--session', sess)[0]
    c, o, e = run_cli('--method', m, 'score', '--session', sess)
    rep = {}
    p = os.path.join(sess, 'e2-report.json')
    if os.path.exists(p):
        rep = json.load(open(p, encoding='utf-8'))
    print(json.dumps({'track_exit': tc, 'score_exit': c, 'refused_mismatch': 'labels a different observations file' in e,
                      'printed_SCORED': 'SCORED' in o,
                      'take_notes': [(t['take'], t['labelled'], t['linked_but_unlabelled']) for t in rep.get('takes', [])]}))
finally:
    shutil.rmtree(d, ignore_errors=True)
''',
    # Two people meet (test_tracker's meeting-point scene) with a heading on
    # every measurement; count updates where an ambiguous track's heading moved.
    'M12': r'''
import method, synth, tracker_core
VALUES = dict(method.Method().values, room=synth.ROOM_MODEL)
tr = tracker_core.TrackerCore(dict(VALUES['tracker']))
ts = [100.0 + 0.25 * k for k in range(17)]
def walker(tag, xy0, v, hd):
    return [{'ids': ['o-%s-%d' % (tag, k)], 'xy': (xy0[0] + v[0] * (t - ts[0]), xy0[1] + v[1] * (t - ts[0])),
             'R': [[0.01, 0.0], [0.0, 0.01]], 'heading': (hd + k, 5.0)} for k, t in enumerate(ts)]
a, b = walker('a', (-2.0, 3.0), (1.0, 0.0), 90.0), walker('b', (2.0, 3.05), (-1.0, 0.0), 270.0)
amb, moved = 0, 0
for k, t in enumerate(ts):
    live, _ = tr.step(t, [a[k], b[k]])
    for x in live:
        if x.ambiguous and x.last_obs_ids:
            amb += 1
            moved += x.heading_as_of == t
print(json.dumps({'ambiguous_linked_updates': amb, 'heading_moved_while_ambiguous': moved}))
''',
}


def copy_tree(dst):
    shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), os.path.join(dst, 'tools', 'den-track'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'e2-report.json'))
    shutil.copytree(os.path.join(ROOT, 'docs', 'architecture', 'live-v1'),
                    os.path.join(dst, 'docs', 'architecture', 'live-v1'))
    return os.path.join(dst, 'tools', 'den-track')


def run_suite(tool_dir):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    t0 = time.time()
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-f', '-s', '.'], cwd=tool_dir,
                       env=env, capture_output=True, text=True, timeout=900)
    tail = [l for l in p.stderr.splitlines() if l.startswith(('FAIL:', 'ERROR:', 'Ran ', 'OK', 'FAILED'))]
    return p.returncode, time.time() - t0, ' | '.join(tail)


def discriminate(tool_dir, code):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    code = 'import json, os, sys\nsys.path.insert(0, os.getcwd())\n' + code
    p = subprocess.run([sys.executable, '-B', '-c', code], cwd=tool_dir, env=env, capture_output=True, text=True,
                       timeout=600)
    if p.returncode != 0:
        return {'error': p.stderr.strip().splitlines()[-1:] or ['exit %d' % p.returncode]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def main():
    os.makedirs(SCRATCH, exist_ok=True)
    base_dir = os.path.join(SCRATCH, 'original')
    shutil.rmtree(base_dir, ignore_errors=True)
    base = copy_tree(base_dir)
    code, secs, tail = run_suite(base)
    print('baseline suite: exit %d in %.1fs %s' % (code, secs, tail), flush=True)
    if code != 0:
        print('RESULT baseline not green; stopping')
        return 2
    survived_and_live = 0
    for mid, fid, fname, old, new in MUTANTS:
        d = os.path.join(SCRATCH, mid)
        shutil.rmtree(d, ignore_errors=True)
        tool = copy_tree(d)
        path = os.path.join(tool, fname)
        src = open(path, encoding='utf-8').read()
        if src.count(old) != 1:
            print('%s %s NOT-RUN: old text found %d times' % (mid, fid, src.count(old)))
            continue
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src.replace(old, new))
        code, secs, tail = run_suite(tool)
        verdict = 'caught' if code != 0 else 'survived'
        orig = discriminate(base, DISCRIMINATORS[mid])
        mut = discriminate(tool, DISCRIMINATORS[mid])
        differs = orig != mut
        print('%s %-32s suite: %-8s (%.1fs; %s)' % (mid, fid, verdict, secs, tail), flush=True)
        print('    original: %s' % json.dumps(orig))
        print('    mutant:   %s' % json.dumps(mut))
        print('    %s %s: mutant %s the suite and %s behaviour on the discriminator'
              % ('PASS' if verdict == 'survived' and differs else 'FAIL', fid, verdict,
                 'changes' if differs else 'does NOT change'), flush=True)
        survived_and_live += verdict == 'survived' and differs
    print('RESULT %d of %d mutants survive the suite while changing behaviour' % (survived_and_live, len(MUTANTS)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

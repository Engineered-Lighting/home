"""verify_r3v-mut-heading-while-ambiguous.py -- R3 verifier, reproduce lens.

Checks finding r3v-mut-heading-while-ambiguous. tracker_core.step accepts a
measurement's heading only when the track is not ambiguous
("if m.get('heading') is not None and not tr.ambiguous:"); METHOD.md says
"An ambiguous update does not move `heading`". The reported mutant drops the
"and not tr.ambiguous" guard and survives the den-track suite. This probe
shows the mutant is NOT equivalent on the tool's own synthetic crossing
scenes: it tracks them with the original and with the mutant code and counts
Track records that say `ambiguous` yet carry a heading_as_of equal to the
record's own time (heading refreshed by an update made while ambiguous), and
records whose heading/heading_as_of differ between the two runs.

Run (stdlib only, synthetic data, temp copies of tools/den-track; touches
nothing in the checkout and opens no network connection):
  python3 -B verify_r3v-mut-heading-while-ambiguous.py <branch checkout root> [seeds=3]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

OLD = "            if m.get('heading') is not None and not tr.ambiguous:"
NEW = "            if m.get('heading') is not None:"
SCENES = ('touching_crossing', 'close_crossing', 'near_pass', 'crossing_behind_armchair')

CHILD = r'''
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import method, pipeline, synth
values = dict(method.Method().values, room=synth.ROOM_MODEL)
tf = values['record']['time_field']
out = []
for name in sys.argv[3].split(','):
    for seed in range(int(sys.argv[2])):
        _, recs, _ = pipeline.synthetic_take(synth.scenario(name, seed, 'typical'), values)
        for i, r in enumerate(recs):
            out.append(['%s-%d' % (name, seed), i, r[tf], r['track_id'], r['association']['status'],
                        r['heading_as_of'], r['heading'], bool(r['last_obs_ids'])])
print(json.dumps(out))
'''


def main():
    root = os.path.abspath(sys.argv[1])
    seeds = sys.argv[2] if len(sys.argv) > 2 else '3'
    tmp = tempfile.mkdtemp(prefix='verify-r3-heading-')
    runs = {}
    for label, mutate in (('original', False), ('mutant', True)):
        tool = os.path.join(tmp, label, 'den-track')
        shutil.copytree(os.path.join(root, 'tools', 'den-track'), tool,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        if mutate:
            p = os.path.join(tool, 'tracker_core.py')
            src = open(p, encoding='utf-8').read()
            assert src.count(OLD) == 1, 'pinned line not found'
            open(p, 'w', encoding='utf-8', newline='\n').write(src.replace(OLD, NEW))
        child = os.path.join(tmp, 'child.py')
        open(child, 'w', encoding='utf-8').write(CHILD)
        p = subprocess.run([sys.executable, '-B', child, tool, seeds, ','.join(SCENES)], capture_output=True,
                           text=True, timeout=900, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        assert p.returncode == 0, p.stderr
        runs[label] = json.loads(p.stdout)
    for label, rows in runs.items():
        amb_fresh = [r for r in rows if r[4] == 'ambiguous' and r[7] and r[5] is not None and r[5] == r[2]]
        amb_seen = [r for r in rows if r[4] == 'ambiguous' and r[7]]
        print('RESULT %s: records=%d ambiguous_and_linked=%d ambiguous_with_heading_refreshed_this_frame=%d'
              % (label, len(rows), len(amb_seen), len(amb_fresh)))
        for r in amb_fresh[:3]:
            print('  e.g. %s t=%s %s status=%s heading_as_of=%s heading=%s' % (r[0], r[2], r[3], r[4], r[5], r[6]))
    o, m = runs['original'], runs['mutant']
    same_shape = len(o) == len(m) and all(a[:5] == b[:5] for a, b in zip(o, m))
    diff = [(a, b) for a, b in zip(o, m) if (a[5], a[6]) != (b[5], b[6])]
    print('RESULT records identical apart from heading fields: %s; records whose heading/heading_as_of differ: %d'
          % (same_shape, len(diff)))
    orig_fresh = sum(1 for r in o if r[4] == 'ambiguous' and r[7] and r[5] is not None and r[5] == r[2])
    mut_fresh = sum(1 for r in m if r[4] == 'ambiguous' and r[7] and r[5] is not None and r[5] == r[2])
    ok = orig_fresh == 0 and mut_fresh > 0 and diff
    print('%s mutant is not equivalent: it refreshes heading during ambiguous updates on synthetic crossings'
          % ('PASS' if ok else 'FAIL'))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

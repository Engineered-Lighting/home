"""verify_r3v-mut-scored-states.py -- R3 verifier, reproduce lens.

Checks finding r3v-mut-scored-states. pipeline.links_for_take skips records
whose state is not in METHOD.md's scored_states (["confirmed"]), so "an
observation a tentative track holds counts as missed" (METHOD.md, Scoring).
The reported mutant removes that filter ("if False:") and survives the
den-track suite. This probe shows the mutant is NOT equivalent on the tool's
own synthetic scenes:

  - counts, per record state, the records holding observation ids and how
    many of those observations are labelled (tentative records do; coasting
    and ended records hold none by construction);
  - scores the same takes with the original and the mutant pipeline and
    prints pooled IDTP/IDFP/IDFN, IDF1, switches and silent swaps.

Run (stdlib only, synthetic data, temp copy of tools/den-track; touches
nothing in the checkout and opens no network connection):
  python3 -B verify_r3v-mut-scored-states.py <branch checkout root> [seeds=3] [noise=typical] [first_seed=0]
"""
import os
import shutil
import sys
import tempfile
import types

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1])
SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
NOISE = sys.argv[3] if len(sys.argv) > 3 else 'typical'
FIRST = int(sys.argv[4]) if len(sys.argv) > 4 else 0

tmp = tempfile.mkdtemp(prefix='verify-r3-states-')
tool = os.path.join(tmp, 'den-track')
shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), tool,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
sys.path.insert(0, tool)

import method as method_mod  # noqa: E402
import pipeline              # noqa: E402
import synth                 # noqa: E402

OLD = "        if rec['state'] not in scored_states:\n            continue"
NEW = "        if False:\n            continue"
src = open(os.path.join(tool, 'pipeline.py'), encoding='utf-8').read()
assert src.count(OLD) == 1, 'pinned lines not found'
mut = types.ModuleType('pipeline_mut')
mut.__file__ = os.path.join(tool, 'pipeline_mut.py')
exec(compile(src.replace(OLD, NEW), mut.__file__, 'exec'), mut.__dict__)

values = dict(method_mod.Method(os.path.join(tool, 'METHOD.md')).values, room=synth.ROOM_MODEL)
print('RESULT scored_states in METHOD.md: %s; noise %s seeds %d-%d' % (values['scoring']['scored_states'], NOISE, FIRST, FIRST + SEEDS - 1))
takes = []
for name in sorted(synth.SCENES):
    for seed in range(FIRST, FIRST + SEEDS):
        takes.append(pipeline.synthetic_take(synth.scenario(name, seed, NOISE), values))

by_state = {}
for _, recs, truth in takes:
    for r in recs:
        k = by_state.setdefault(r['state'], [0, 0, 0])
        k[0] += 1
        if r['last_obs_ids']:
            k[1] += 1
            k[2] += sum(1 for o in r['last_obs_ids'] if o in truth['labels'])
for st, (n, with_ids, labelled) in sorted(by_state.items()):
    print('RESULT state %-10s records=%6d holding_obs=%6d labelled_obs_held=%6d' % (st, n, with_ids, labelled))

orig = pipeline.score_takes(takes, values)
m = mut.score_takes(takes, values)
for label, r in (('original', orig), ('mutant  ', m)):
    p = r['pooled']
    print('RESULT %s: idtp=%d idfp=%d idfn=%d idf1=%.4f silent_swaps=%d reported=%d windows_failed=%d gate=%s'
          % (label, p['idtp'], p['idfp'], p['idfn'], p['idf1'], r['silent_swaps'], r['reported_switches'],
             r['crossings_with_silent_swap'], r['gate']['passed']))
differs = (orig['pooled']['idtp'], orig['pooled']['idfn']) != (m['pooled']['idtp'], m['pooled']['idfn'])
print('%s mutant is not equivalent: tentative records hold labelled observations and the scores differ'
      % ('PASS' if differs and by_state.get('tentative', [0, 0, 0])[2] > 0 else 'FAIL'))
shutil.rmtree(tmp, ignore_errors=True)

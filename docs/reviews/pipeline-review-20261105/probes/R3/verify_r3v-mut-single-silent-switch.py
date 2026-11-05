"""verify_r3v-mut-single-silent-switch.py -- R3 verifier, reproduce lens.

Checks finding r3v-mut-single-silent-switch. scorer_core.pool() counts a
window as a failed trial when it holds at least one silent switch
("r['silent_swaps'] > 0"). The reported mutant changes that to "> 1" and
survives the den-track suite. This probe shows the mutant is NOT equivalent:

 1. One window (of 20) holds exactly one silent switch: track 1 moves from A
    to B while B's own track is gone (a one-sided jump). The original pool()
    counts 1 failed window and fails the gate; the mutant counts 0, gives the
    0-failure bound and PASSES the gate.
 2. With --practice, it runs the tool's own practice harness (synthetic den,
    seeds 200-239, typical and noisy, as METHOD.md's tuning record) and lists
    every window with a silent swap, so we can see how many hold exactly one.

Run (stdlib only, synthetic data, temp copy of tools/den-track; touches
nothing in the checkout and opens no network connection):
  python3 -B verify_r3v-mut-single-silent-switch.py <branch checkout root> [--practice]
"""
import os
import shutil
import sys
import tempfile
import types

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1])
PRACTICE = '--practice' in sys.argv[2:]

tmp = tempfile.mkdtemp(prefix='verify-r3-single-')
tool = os.path.join(tmp, 'den-track')
shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), tool,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
sys.path.insert(0, tool)

import scorer_core  # noqa: E402  (the temp copy of the pinned code)

OLD = "failed = sum(1 for r in rows if r['silent_swaps'] > 0)"
NEW = "failed = sum(1 for r in rows if r['silent_swaps'] > 1)"
src = open(os.path.join(tool, 'scorer_core.py'), encoding='utf-8').read()
assert src.count(OLD) == 1, 'pinned line not found'
mut = types.ModuleType('scorer_core_mut')
mut.__file__ = os.path.join(tool, 'scorer_core_mut.py')
exec(compile(src.replace(OLD, NEW), mut.__file__, 'exec'), mut.__dict__)


def links_from(rows, t0=0.0, prefix='o'):
    return [(t0 + k, '%s%d' % (prefix, k), p, t, True if t is not None else None) for k, (p, t) in enumerate(rows)]


# 19 clean windows plus one window with a one-sided jump (exactly one silent switch).
takes = []
for i in range(19):
    links = links_from([('A', 1), ('B', 2)] * 20)
    takes.append(('clean%d' % i, scorer_core.score_take(links, [{'id': 'c1', 'kind': 'crossing', 'start': 0, 'end': 39}])))
jump_rows = [('A', 1), ('B', 2)] * 20 + [('B', 1)] * 2      # B's own track 2 is lost; B lands on track 1
jump_links = links_from(jump_rows)
res_jump = scorer_core.score_take(jump_links, [{'id': 'c1', 'kind': 'crossing', 'start': 0, 'end': 41}])
print('RESULT jump window: switches=%d silent_swaps=%d idf1=%.3f'
      % (res_jump['windows'][0]['switches'], res_jump['windows'][0]['silent_swaps'], res_jump['windows'][0]['idf1']))
takes.append(('jump', res_jump))

orig = scorer_core.pool(takes, 0.9, 20)
m = mut.pool(takes, 0.9, 20)
for name, r in (('original', orig), ('mutant  ', m)):
    print('RESULT %s: windows=%d crossings_with_silent_swap=%d silent_swaps=%d bound=%.4f idf1=%.4f '
          'silent_swaps_zero=%s passed=%s'
          % (name, r['crossings'], r['crossings_with_silent_swap'], r['silent_swaps'],
             r['silent_swap_rate_upper_95'], r['pooled']['idf1'], r['gate']['silent_swaps_zero'], r['gate']['passed']))
ok = (orig['crossings_with_silent_swap'] == 1 and not orig['gate']['passed']
      and m['crossings_with_silent_swap'] == 0 and m['gate']['passed'])
print('%s mutant is not equivalent: original fails the gate on one single-switch window, mutant passes it'
      % ('PASS' if ok else 'FAIL'))

if PRACTICE:
    import method as method_mod  # noqa: E402
    import pipeline              # noqa: E402
    import synth                 # noqa: E402
    meth = method_mod.Method(os.path.join(tool, 'METHOD.md'))
    values = dict(meth.values, room=synth.ROOM_MODEL)
    for noise in ('typical', 'noisy'):
        rep = pipeline.practice(values, seeds=40, noise=noise, first_seed=200)
        bad = [r for r in rep['windows'] if r['silent_swaps'] > 0]
        print('RESULT practice seeds 200-239 %s: windows=%d failed(orig)=%d failed(mutant, >1)=%d'
              % (noise, rep['crossings'], sum(1 for r in bad), sum(1 for r in bad if r['silent_swaps'] > 1)))
        for r in bad:
            print('  window %s %s silent_swaps=%d reported=%d' % (r['take'], r['id'], r['silent_swaps'],
                                                                    r['reported_switches']))
shutil.rmtree(tmp, ignore_errors=True)

"""verify_r3v-mut-idf1-boundary.py -- R3 verifier, reproduce lens.

Checks finding r3v-mut-idf1-boundary. scorer_core.pool() passes the IDF1 part
of the gate at "pooled_idf1 >= idf1_gate" (ladder: "IDF1 >= 0.9"). The
reported mutant uses ">" and survives the den-track suite. This probe shows
the mutant is NOT equivalent: 20 clean windows whose pooled counts give IDF1
exactly 0.9 in floating point (IDTP 900, IDFP 100, IDFN 100) pass the
original gate and fail the mutant's. It also prints how the boundary looks
at a value just below 0.9.

Run (stdlib only, synthetic links, temp copy of tools/den-track; touches
nothing in the checkout and opens no network connection):
  python3 -B verify_r3v-mut-idf1-boundary.py <branch checkout root>
"""
import os
import shutil
import sys
import tempfile
import types

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1])
tmp = tempfile.mkdtemp(prefix='verify-r3-idf1-')
tool = os.path.join(tmp, 'den-track')
shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), tool,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
sys.path.insert(0, tool)
import scorer_core  # noqa: E402

OLD = "pooled_idf1 >= idf1_gate"
NEW = "pooled_idf1 > idf1_gate"
src = open(os.path.join(tool, 'scorer_core.py'), encoding='utf-8').read()
assert src.count(OLD) == 1, 'pinned text not found'
mut = types.ModuleType('scorer_core_mut')
mut.__file__ = os.path.join(tool, 'scorer_core_mut.py')
exec(compile(src.replace(OLD, NEW), mut.__file__, 'exec'), mut.__dict__)


def window_links(tp, fp, fn):
    """One person A on track 1: tp matched, fp false observations on track 1, fn unlinked A."""
    rows = [('A', 1)] * tp + [(None, 1)] * fp + [('A', None)] * fn
    return [(float(k), 'o%d' % k, p, t, True if t is not None else None) for k, (p, t) in enumerate(rows)]


def session(tp, fp, fn):
    w = [{'id': 'c1', 'kind': 'crossing', 'start': 0, 'end': 10 ** 6}]
    return [('t%d' % i, scorer_core.score_take(window_links(tp, fp, fn), w)) for i in range(20)]


for tp, fp, fn in ((45, 5, 5), (44, 5, 5)):
    takes = session(tp, fp, fn)
    o = scorer_core.pool(takes, 0.9, 20)
    m = mut.pool(takes, 0.9, 20)
    print('RESULT IDTP=%d IDFP=%d IDFN=%d pooled_idf1=%r (== 0.9: %s) original idf1_met=%s passed=%s | mutant idf1_met=%s passed=%s'
          % (o['pooled']['idtp'], o['pooled']['idfp'], o['pooled']['idfn'], o['pooled']['idf1'],
             o['pooled']['idf1'] == 0.9, o['gate']['idf1_met'], o['gate']['passed'], m['gate']['idf1_met'],
             m['gate']['passed']))
takes = session(45, 5, 5)
o, m = scorer_core.pool(takes, 0.9, 20), mut.pool(takes, 0.9, 20)
ok = o['pooled']['idf1'] == 0.9 and o['gate']['passed'] and not m['gate']['passed']
print('%s mutant is not equivalent: IDF1 exactly 0.9 passes the original gate and fails the mutant'
      % ('PASS' if ok else 'FAIL'))
shutil.rmtree(tmp, ignore_errors=True)

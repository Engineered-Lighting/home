"""probe_r3v_bound_recompute.py -- R3 (den-track), validate lens.

What it checks
  scorer_math.upper_bound(k, n, alpha) -- the one-sided (1 - alpha)
  Clopper-Pearson upper bound on the silent-swap rate -- against an
  independent computation: exact rational bisection on the binomial tail
  P(X <= k; n, p) = alpha (fractions.Fraction, no floats inside the tail),
  for k = 0..5 and n in 10, 20, 25, 30, 40, 100. Also prints the closed form
  1 - alpha^(1/n) and the rule of three 3/n for k = 0, the ladder's quoted
  values (about 14% at 20, 10% at 30) and the README's (13.9%, 9.5%).

Run (stdlib only):
  python3 -B probe_r3v_bound_recompute.py <branch checkout root>
"""
import math
import os
import sys
from fractions import Fraction

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

from scorer_math import upper_bound  # noqa: E402


def tail_le(k, n, p):
    q = 1 - p
    return sum(math.comb(n, i) * p ** i * q ** (n - i) for i in range(k + 1))


def cp_upper(k, n, alpha=Fraction(1, 20), iters=60):
    if k >= n:
        return Fraction(1)
    lo, hi = Fraction(0), Fraction(1)
    for _ in range(iters):
        mid = (lo + hi) / 2
        if tail_le(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


fails = 0
for n in (10, 20, 25, 30, 40, 100):
    for k in range(0, 6):
        tool = upper_bound(k, n, 0.05)
        mine = float(cp_upper(k, n))
        ok = abs(tool - mine) < 1e-9
        fails += 0 if ok else 1
        extra = ''
        if k == 0:
            extra = ' closed form %.6f, rule of three %.4f' % (1 - 0.05 ** (1 / n), 3 / n)
        print('%s n=%3d k=%d tool %.6f independent %.6f%s' % ('PASS' if ok else 'FAIL', n, k, tool, mine, extra))

for n, quoted in ((20, 0.139), (30, 0.095)):
    v = upper_bound(0, n)
    ok = round(v, 3) == quoted
    fails += 0 if ok else 1
    print('%s README value at n=%d: %.4f (README %.3f, ladder "about %d%%")' % ('PASS' if ok else 'FAIL', n, v, quoted,
                                                                         14 if n == 20 else 10))
ok = upper_bound(0, 0) is None and upper_bound(3, 3) == 1.0
fails += 0 if ok else 1
print('%s edges: n=0 -> %r, k=n -> %r' % ('PASS' if ok else 'FAIL', upper_bound(0, 0), upper_bound(3, 3)))
print('FAIL count: %d' % fails)
sys.exit(1 if fails else 0)

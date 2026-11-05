"""verify_cp: independent Clopper-Pearson for 35/40 by bisection on binomial tails, and the exact
session-bootstrap percentile interval for 4 sessions at 5/10, 10/10, 10/10, 10/10.
Run: python3 -B verify_cp.py   (no args needed). Synthetic only."""
from math import comb
from itertools import product

def tail_ge(x, n, p):
    return sum(comb(n, k) * p**k * (1 - p)**(n - k) for k in range(x, n + 1))

def tail_le(x, n, p):
    return sum(comb(n, k) * p**k * (1 - p)**(n - k) for k in range(0, x + 1))

def bisect(f, lo, hi):
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2

x, n = 35, 40
lower = bisect(lambda p: tail_ge(x, n, p) >= 0.025, 0.0, 1.0)
upper = bisect(lambda p: tail_le(x, n, p) <= 0.025, 0.0, 1.0)
print(f"RESULT CP 95% for {x}/{n}: [{lower:.4f}, {upper:.4f}]")
# exact bootstrap distribution over 4^4 ordered resamples
sess = [(5, 10), (10, 10), (10, 10), (10, 10)]
vals = []
for pick in product(range(4), repeat=4):
    s = sum(sess[i][0] for i in pick); t = sum(sess[i][1] for i in pick)
    vals.append(s / t)
vals.sort()
N = len(vals)
import math
lo = vals[math.ceil(0.025 * N) - 1]; hi = vals[math.ceil(0.975 * N) - 1]
print(f"RESULT exact session-bootstrap percentile 95% (256 equiprobable resamples): [{lo:.4f}, {hi:.4f}]")

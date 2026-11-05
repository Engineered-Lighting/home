"""probe_r3a_idf1_window_mapping: recompute IDF1 and the one-sided bound
independently and compare with den-track's scorer.

IDF1 here = IDTP / (IDTP + 0.5 IDFP + 0.5 IDFN) with ONE optimal one-to-one
person->track matching per take (brute force over permutations), over every
labelled observation of the take that is inside a window (case A-D) or over
the whole take (case E).  den-track instead gives each window its own best
mapping and pools the counts.  The bound is recomputed from the regularized
incomplete beta function (continued fraction), not from a binomial sum.

Run:  python3 probe_r3a_idf1_window_mapping.py <root of the rung-3-track checkout>
"""
import itertools
import math
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))
import scorer_core  # noqa: E402
import scorer_math  # noqa: E402


def global_idf1(links):
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    co = {}
    for l in links:
        if l[2] is not None and l[3] is not None:
            co[(l[2], l[3])] = co.get((l[2], l[3]), 0) + 1
    best = 0
    pad = tracks + [None] * len(people)
    for perm in itertools.permutations(pad, len(people)):
        best = max(best, sum(co.get((p, t), 0) for p, t in zip(people, perm) if t is not None))
    n_truth = sum(1 for l in links if l[2] is not None)
    n_pred = sum(1 for l in links if l[3] is not None)
    idfp, idfn = n_pred - best, n_truth - best
    d = best + 0.5 * idfp + 0.5 * idfn
    return best / d if d else None


def tool_idf1(links, windows):
    res = scorer_core.score_take(links, windows)
    pooled = scorer_core.pool([('t', res)], 0.9, 1)
    return pooled['pooled']['idf1']


def L(t, person, track, conf=True):
    return (float(t), 'o%s%s' % (t, person), person, track, conf)


def case(name, links, windows, scope_all=False):
    inside = links if scope_all else [l for l in links if any(w['start'] <= l[0] <= w['end'] for w in windows)]
    g, t = global_idf1(inside), tool_idf1(links, windows)
    ok = abs(g - t) < 1e-9
    print('%-58s tool %.3f  independent %.3f  %s' % (name, t, g, 'match' if ok else 'MISMATCH'))
    return ok


W = lambda i, s, e: {'id': 'w%d' % i, 'kind': 'crossing', 'start': s, 'end': e}
oks = []
# A: one window, a swap half way through
links = [L(t, 'A', 'T1' if t < 5 else 'T2') for t in range(10)] + [L(t, 'B', 'T2' if t < 5 else 'T1') for t in range(10)]
oks.append(case('A one window, swap mid-window', links, [W(1, 0, 9)]))
# B: one window, A fragments T1 -> T3
links = [L(t, 'A', 'T1' if t < 5 else 'T3') for t in range(10)] + [L(t, 'B', 'T2') for t in range(10)]
oks.append(case('B one window, fragment T1->T3', links, [W(1, 0, 9)]))
# C: two windows in one take; a reported swap between them persists
links = [L(t, 'A', 'T1' if t < 10 else 'T2', t < 10 or t >= 12) for t in range(20)] + \
        [L(t, 'B', 'T2' if t < 10 else 'T1', t < 10 or t >= 12) for t in range(20)]
oks.append(case('C two windows, swap between them persists', links, [W(1, 0, 9), W(2, 10, 19)]))
# D: two windows; A's track fragments between them (T1 -> T3)
links = [L(t, 'A', 'T1' if t < 10 else 'T3') for t in range(20)] + [L(t, 'B', 'T2') for t in range(20)]
oks.append(case('D two windows, fragment between them', links, [W(1, 0, 9), W(2, 10, 19)]))
# E: one window; A fragments 3 times outside it (IDF1 over the whole take)
links = [L(t, 'A', 'T%d' % (1 + t // 5 * 2)) for t in range(20)] + [L(t, 'B', 'T2') for t in range(20)]
oks.append(case('E one window [15,19]; fragments outside it (whole take)', links, [W(1, 15, 19)], scope_all=True))
print('RESULT IDF1: %s' % ('PASS' if all(oks) else 'FAIL: den-track IDF1 (per-window mapping, in-window only) '
                                                   'exceeds the take-level IDF1 in %d of %d cases' % (oks.count(False), len(oks))))


# ---- the bound, independently: Beta(1-alpha; k+1, n-k) via a continued fraction
def betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 / (1 + aa * d)
        c = 1 + aa / c
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 / (1 + aa * d)
        c = 1 + aa / c
        de = d * c
        h *= de
        if abs(de - 1) < 1e-15:
            break
    return h


def ibeta(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * betacf(a, b, x) / a
    return 1 - bt * betacf(b, a, 1 - x) / b


def cp_upper(k, n, alpha=0.05):
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if ibeta(k + 1, n - k, mid) < 1 - alpha:
            lo = mid
        else:
            hi = mid
    return hi


ok_b = True
for k, n in ((0, 20), (0, 30), (1, 20), (1, 30), (2, 30), (3, 25)):
    t, h = scorer_math.upper_bound(k, n), cp_upper(k, n)
    extra = '  1-0.05^(1/n) %.4f  rule of three %.4f' % (1 - 0.05 ** (1 / n), 3 / n) if k == 0 else ''
    ok = abs(t - h) < 1e-6
    ok_b &= ok
    print('bound k=%d n=%d  tool %.4f  independent %.4f  %s%s' % (k, n, t, h, 'match' if ok else 'MISMATCH', extra))
print('RESULT bound: %s' % ('PASS' if ok_b else 'FAIL'))

"""probe_atk_windowed_idf1: recompute IDF1 and the one-sided bound independently.

Part 1 (stats): an independent IDF1 = IDTP / (IDTP + 0.5 IDFP + 0.5 IDFN) with
ONE global optimal one-to-one person->track matching found by brute force over
every injective mapping, compared with scorer_core.id_counts/idf1 on small
constructed cases (perfect, one swap halfway, a fragment, unlinked and false
observations, a swap plus a fragment).

Part 2 (stats): the exact one-sided 95% Clopper-Pearson upper bound computed
with rational arithmetic (fractions.Fraction bisection, 80 steps) against
scorer_math.upper_bound, for k = 0 (n = 20, 30, with the 1 - 0.05^(1/n) closed
form and the rule of three 3/n beside it) and k = 1, 2, 3.

Part 3 (attack): a take of 10 crossing windows where the tracker FLAGS a swap
(confidence 0.3) in the last 2 frames of window 5 and never swaps back. The
scorer re-maps people to tracks in every window and sums, so the persistent
swap costs almost nothing; the global IDF1 over the same in-window links does
not forgive it. The gate needs IDF1 >= 0.9.

Run:  python3 -B probe_atk_windowed_idf1.py <branch checkout root>
The tool folder is copied to a temp dir first. Synthetic data only.
"""
import itertools
import os
import shutil
import sys
import tempfile
from fractions import Fraction

sys.dont_write_bytecode = True


def load(root):
    tmp = tempfile.mkdtemp(prefix='r3atk-')
    shutil.copytree(os.path.join(root, 'tools', 'den-track'), os.path.join(tmp, 'den-track'))
    sys.path.insert(0, os.path.join(tmp, 'den-track'))
    import scorer_core
    import scorer_math
    return scorer_core, scorer_math


def global_idf1(links):
    """Brute force: best injective map people -> tracks (people may stay unmapped)."""
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    best = 0
    slots = tracks + [None] * len(people)
    for perm in itertools.permutations(slots, len(people)):
        used = [t for t in perm if t is not None]
        if len(used) != len(set(used)):
            continue
        m = dict(zip(people, perm))
        tp = sum(1 for l in links if l[2] is not None and l[3] is not None and m.get(l[2]) == l[3])
        best = max(best, tp)
    n_truth = sum(1 for l in links if l[2] is not None)
    n_pred = sum(1 for l in links if l[3] is not None)
    d = n_truth + n_pred
    return (None if d == 0 else Fraction(2 * best, d)), best, n_pred - best, n_truth - best


def frames(rows, t0=0.0, conf=1.0):
    return [(t0 + k, 'o%04d-%d' % (int(t0) + k, i), p, t, conf if t is not None else None)
            for k, row in enumerate(rows) for i, (p, t) in enumerate(row)]


def cp_upper(k, n, alpha=Fraction(1, 20)):
    def cdf(p):
        return sum(Fraction(__import__('math').comb(n, i)) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
    lo, hi = Fraction(k, n), Fraction(1)
    for _ in range(80):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
        # keep the fractions small
        lo = Fraction(lo).limit_denominator(10 ** 30)
        hi = Fraction(hi).limit_denominator(10 ** 30)
    return float(hi)


def main(root):
    sc, sm = load(root)
    bad = 0
    print('-- part 1: IDF1 on constructed cases (scorer id_counts over the whole link set vs brute force)')
    cases = {
        'perfect 10 frames': [[('A', 1), ('B', 2)]] * 10,
        'one swap halfway': [[('A', 1), ('B', 2)]] * 5 + [[('A', 2), ('B', 1)]] * 5,
        'fragment (A 1 -> 3 at frame 6)': [[('A', 1), ('B', 2)]] * 6 + [[('A', 3), ('B', 2)]] * 4,
        'unlinked + false obs': [[('A', 1)]] * 8 + [[('A', None)]] * 2 + [[(None, 1)]] * 2,
        'swap at 3 then fragment at 7': [[('A', 1), ('B', 2)]] * 3 + [[('A', 2), ('B', 1)]] * 4
                                        + [[('A', 2), ('B', 4)]] * 3,
    }
    for name, rows in cases.items():
        links = frames(rows)
        c = sc.id_counts(links)
        tool = sc.idf1(c)
        hand, tp, fp, fn = global_idf1(links)
        ok = abs(tool - float(hand)) < 1e-12 and (c['idtp'], c['idfp'], c['idfn']) == (tp, fp, fn)
        print('%-32s tool=%.6f (tp %d fp %d fn %d)  hand=%s=%.6f  %s' % (
            name, tool, c['idtp'], c['idfp'], c['idfn'], hand, float(hand), 'MATCH' if ok else 'MISMATCH'))
        bad += 0 if ok else 1

    print('-- part 2: one-sided 95% upper bounds')
    for k, n in ((0, 20), (0, 30), (1, 20), (2, 30), (3, 25), (1, 2)):
        tool = sm.upper_bound(k, n)
        hand = cp_upper(k, n)
        extra = ''
        if k == 0:
            extra = ' closed=%.6f rule_of_three=%.4f' % (1 - 0.05 ** (1 / n), 3 / n)
        ok = abs(tool - hand) < 1e-9
        print('k=%d n=%-3d tool=%.6f exact=%.6f%s %s' % (k, n, tool, hand, extra, 'MATCH' if ok else 'MISMATCH'))
        bad += 0 if ok else 1

    print('-- part 3: persistent flagged swap, per-window re-mapping vs one global mapping')
    links, windows = [], []
    for w in range(10):
        t0 = 100.0 * w
        windows.append({'id': 'c%d' % w, 'kind': 'crossing', 'start': t0, 'end': t0 + 9})
        for f in range(10):
            swapped = (w > 5) or (w == 5 and f >= 8)
            conf = 0.3 if (w >= 5) else 0.99          # the swap was flagged: confidence fell at the crossing
            for person in ('A', 'B'):
                track = ('T2' if person == 'A' else 'T1') if swapped else ('T1' if person == 'A' else 'T2')
                links.append((t0 + f, 'o-%d-%d-%s' % (w, f, person), person, track, conf))
    res = sc.score_windows(links, windows, 0.9, 0.9)
    inside = [l for l in links if any(x['start'] <= l[0] <= x['end'] for x in windows)]
    g, tp, fp, fn = global_idf1(inside)
    print('scorer pooled IDF1=%.4f silent=%d flagged=%d passed=%s' % (
        res['pooled']['idf1'], res['silent_swaps'], res['flagged_switches'], res['gate']['passed']))
    print('global IDF1 over the same in-window links=%.4f (tp %d fp %d fn %d)' % (float(g), tp, fp, fn))
    if res['gate']['passed'] and float(g) < 0.9:
        print('FAIL part 3: the gate passes on pooled per-window IDF1 %.3f while one global mapping gives %.3f'
              % (res['pooled']['idf1'], float(g)))
        bad += 1
    print('RESULT: %s (%d problem(s))' % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_windowed_idf1.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))

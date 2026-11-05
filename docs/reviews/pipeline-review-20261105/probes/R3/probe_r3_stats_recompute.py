#!/usr/bin/env python3
"""Independent recomputation of den-track's E2 statistics (rung 3).

Checks, against my own implementations (none of the tool's code is reused):
  1. IDF1 = IDTP / (IDTP + 0.5 IDFP + 0.5 IDFN) with an optimal GLOBAL
     one-to-one person->track matching (brute force over all injections), on
     small constructed cases: perfect, one swap, a fragment, swap + fragment,
     false and unlinked observations, more tracks than people.
     Compared with scorer_core.id_counts/idf1 on the same links, and with the
     pooled IDF1 that scorer_core.score_windows reports for multi-window takes.
  2. The one-sided 95% upper bound on a failure rate: exact 1 - 0.05^(1/n)
     for 0 events, the rule of three for reference, and Clopper-Pearson for
     k > 0 (log-space binomial CDF + bisection), against scorer_math.upper_bound.
  3. Silent-swap counting: my own per-track walk over labelled links, against
     scorer_core.switches.

Run:
    python3 probe_r3_stats_recompute.py <root of the branch checkout>
Synthetic data only; imports the tool from <root>/tools/den-track without
writing bytecode into the checkout.
"""
import itertools
import math
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
if not ROOT:
    print(__doc__)
    sys.exit(2)
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))
import scorer_core  # noqa: E402
import scorer_math  # noqa: E402

fails = 0


def check(name, tool, mine, tol=1e-9):
    global fails
    ok = (tool is None and mine is None) or (tool is not None and mine is not None and abs(tool - mine) <= tol)
    if not ok:
        fails += 1
    print('%s %-62s tool=%s mine=%s' % ('PASS' if ok else 'FAIL', name,
                                        'None' if tool is None else '%.6f' % tool,
                                        'None' if mine is None else '%.6f' % mine))
    return ok


# ---------------------------------------------------------------- IDF1 (mine)
def my_global_idf1(links):
    """links: (time, obs_id, person|None, track|None, conf). Global matching by brute force."""
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None}, key=str)
    co = {}
    for l in links:
        if l[2] is not None and l[3] is not None:
            co[(l[2], l[3])] = co.get((l[2], l[3]), 0) + 1
    best = 0
    # every injection of people into tracks-or-nothing
    slots = list(tracks) + [None] * len(people)
    for perm in itertools.permutations(range(len(slots)), len(people)):
        total = 0
        used = set()
        okp = True
        for pi, si in enumerate(perm):
            t = slots[si]
            if t is not None:
                if t in used:
                    okp = False
                    break
                used.add(t)
                total += co.get((people[pi], t), 0)
        if okp:
            best = max(best, total)
    n_truth = sum(1 for l in links if l[2] is not None)
    n_pred = sum(1 for l in links if l[3] is not None)
    idtp, idfp, idfn = best, n_pred - best, n_truth - best
    den = idtp + 0.5 * idfp + 0.5 * idfn
    return None if den == 0 else idtp / den


def mk(rows, t0=0.0, conf=0.99):
    return [(t0 + k, 'o%d' % (int(t0) + k), p, t, conf if t is not None else None) for k, (p, t) in enumerate(rows)]


print('--- 1. IDF1, single take, global matching')
cases = {
    'perfect two people': mk([('A', 1), ('B', 2)] * 10),
    'one swap halfway': mk([('A', 1), ('B', 2)] * 5 + [('A', 2), ('B', 1)] * 5),
    'one swap at 80%': mk([('A', 1), ('B', 2)] * 8 + [('A', 2), ('B', 1)] * 2),
    'fragment (A 1 -> 3)': mk([('A', 1), ('B', 2)] * 6 + [('A', 3), ('B', 2)] * 4),
    'swap then fragment': mk([('A', 1), ('B', 2)] * 4 + [('A', 2), ('B', 1)] * 3 + [('A', 3), ('B', 1)] * 3),
    'false + unlinked obs': mk([('A', 1)] * 8 + [('A', None)] * 2 + [(None, 1)] * 2),
    'three tracks, two people': mk([('A', 1), ('B', 2), (None, 3)] * 5 + [('A', 3), ('B', 2)] * 2),
    'three people, two tracks': mk([('A', 1), ('B', 2), ('C', 1)] * 4),
}
for name, links in cases.items():
    check('IDF1 %s' % name, scorer_core.idf1(scorer_core.id_counts(links)), my_global_idf1(links))

print('--- 1b. pooled IDF1 reported by score_windows vs global IDF1 over the same in-window links')
# Two crossing windows; the tracker swaps A and B between them (t 10..19, outside both windows).
pre = mk([('A', 1), ('B', 2)] * 5, t0=0.0)            # t 0..9    window c1
gap = mk([('A', 1), ('B', 2)] * 5, t0=10.0)           # t 10..19  no window
post = mk([('A', 2), ('B', 1)] * 5, t0=20.0)          # t 20..29  window c2, swapped
links = pre + gap + post
windows = [{'id': 'c1', 'kind': 'crossing', 'start': 0, 'end': 9},
           {'id': 'c2', 'kind': 'crossing', 'start': 20, 'end': 29}]
res = scorer_core.score_windows(links, windows, confident_at=0.9, idf1_gate=0.9)
inside = [l for l in links if any(w['start'] <= l[0] <= w['end'] for w in windows)]
check('pooled IDF1 (swap between windows)', res['pooled']['idf1'], my_global_idf1(inside))
check('pooled IDF1 vs whole-take global IDF1', res['pooled']['idf1'], my_global_idf1(links))
print('RESULT gate=%s silent_swaps_in_windows=%d silent_switches_outside=%d pooled_idf1=%.3f' % (
    res['gate']['passed'], res['silent_swaps'], res['silent_switches_outside_windows'], res['pooled']['idf1']))

# A flagged (low-confidence) swap between windows: silent-swap gate rightly ignores it, but IDF1 should see it.
links_low = pre + mk([('A', 1), ('B', 2)] * 5, t0=10.0, conf=0.2) + mk([('A', 2), ('B', 1)] * 5, t0=20.0, conf=0.2)
res_low = scorer_core.score_windows(links_low, windows, confident_at=0.9, idf1_gate=0.9)
inside_low = [l for l in links_low if any(w['start'] <= l[0] <= w['end'] for w in windows)]
check('pooled IDF1 (flagged swap between windows)', res_low['pooled']['idf1'], my_global_idf1(inside_low))
print('RESULT flagged-swap take: gate passed=%s (silent swaps %d, pooled IDF1 %.3f, global IDF1 %.3f)' % (
    res_low['gate']['passed'], res_low['silent_swaps'], res_low['pooled']['idf1'], my_global_idf1(inside_low)))


# A realistic session: 20 crossing windows of 20 links each (A and B), one FLAGGED swap
# in the middle of crossing 10 that the tracker never undoes. Silent-swap gate: 0 (flagged).
session, wins, t = [], [], 0.0
for c in range(20):
    rows = []
    for k in range(10):
        swapped = c > 9 or (c == 9 and k >= 5)
        rows += [('A', 2), ('B', 1)] if swapped else [('A', 1), ('B', 2)]
    session += mk(rows, t0=t, conf=0.3 if c >= 9 else 0.99)
    wins.append({'id': 'c%d' % c, 'kind': 'crossing', 'start': t, 'end': t + len(rows) - 1})
    t += len(rows) + 30.0
res_mid = scorer_core.score_windows(session, wins, confident_at=0.9, idf1_gate=0.9)
in_mid = [l for l in session if any(w['start'] <= l[0] <= w['end'] for w in wins)]
check('pooled IDF1 (one flagged swap at crossing 10 of 20)', res_mid['pooled']['idf1'], my_global_idf1(in_mid))
print('RESULT mid-session flagged swap: gate passed=%s pooled IDF1 %.3f, global IDF1 %.3f' % (
    res_mid['gate']['passed'], res_mid['pooled']['idf1'], my_global_idf1(in_mid)))

# ------------------------------------------------------- upper bound (mine)
def log_binom_cdf(k, n, p):
    if p <= 0:
        return 0.0
    if p >= 1:
        return -math.inf if k < n else 0.0
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p) + (n - i) * math.log1p(-p)
             for i in range(k + 1)]
    m = max(terms)
    return m + math.log(sum(math.exp(t - m) for t in terms))


def my_cp_upper(k, n, alpha=0.05):
    if n == 0:
        return None
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    la = math.log(alpha)
    for _ in range(100):
        mid = (lo + hi) / 2
        if log_binom_cdf(k, n, mid) > la:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


print('--- 2. one-sided 95%% upper bound')
for n in (1, 5, 10, 20, 25, 30, 100):
    check('0 events in %d: exact 1-0.05^(1/n)' % n, scorer_math.upper_bound(0, n), 1 - 0.05 ** (1 / n), 1e-12)
for k, n in ((1, 20), (2, 20), (1, 30), (3, 30), (5, 30), (10, 30), (29, 30), (1, 2), (7, 100)):
    check('Clopper-Pearson %d events in %d' % (k, n), scorer_math.upper_bound(k, n), my_cp_upper(k, n), 1e-9)
print('INFO rule of three: n=20 -> %.4f, n=30 -> %.4f (exact: %.4f, %.4f)' % (
    3 / 20, 3 / 30, 1 - 0.05 ** (1 / 20), 1 - 0.05 ** (1 / 30)))
check('ladder value n=20 (about 14%)', round(scorer_math.upper_bound(0, 20), 4), 0.1391, 1e-12)
check('ladder value n=30 (about 10%)', round(scorer_math.upper_bound(0, 30), 4), 0.0950, 1e-12)
check('n=0 gives None', scorer_math.upper_bound(0, 0), None)


# --------------------------------------------------- silent swaps (mine)
def my_switches(links, thr):
    by_track = {}
    for l in sorted(links, key=lambda l: (l[0], l[1])):
        if l[2] is None or l[3] is None:
            continue
        by_track.setdefault(l[3], []).append(l)
    out = []
    for t, seq in by_track.items():
        for prev, cur in zip(seq, seq[1:]):
            if prev[2] != cur[2]:
                out.append((cur[0], t, cur[4] is not None and cur[4] >= thr))
    return sorted(out)


print('--- 3. silent-swap counting')
sw_cases = {
    'confident swap': mk([('A', 1), ('B', 2)] * 3 + [('B', 1), ('A', 2)] * 3, conf=0.99),
    'flagged swap': mk([('A', 1), ('B', 2)] * 3 + [('B', 1), ('A', 2)] * 3, conf=0.3),
    'swap across a false obs': mk([('A', 1), (None, 1), ('B', 1)], conf=0.95),
    'swap and swap back': mk([('A', 1)] * 2 + [('B', 1)] * 2 + [('A', 1)] * 2, conf=0.95),
    'threshold exactly 0.9': mk([('A', 1)] * 2 + [('B', 1)], conf=0.9),
}
for name, links in sw_cases.items():
    tool = sorted((s['time'], s['track'], s['silent']) for s in scorer_core.switches(links, 0.9))
    mine = my_switches(links, 0.9)
    ok = tool == mine
    fails += 0 if ok else 1
    print('%s switches %-40s tool=%s mine=%s' % ('PASS' if ok else 'FAIL', name, tool, mine))

print()
print('RESULT: %d mismatch(es)' % fails)

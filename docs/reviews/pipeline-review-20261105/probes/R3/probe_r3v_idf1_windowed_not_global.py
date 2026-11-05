"""probe_r3v_idf1_windowed_not_global.py -- R3 (den-track), validate lens.

What it checks
  1. scorer_core.id_counts / scorer_core.idf1 against an independent IDF1
     (IDTP / (IDTP + 0.5 IDFP + 0.5 IDFN), optimal global one-to-one matching
     of people to tracks found by brute force) on small constructed cases:
     perfect, one swap halfway, a fragment, unlinked and false observations.
  2. The gate's "pooled IDF1" (scorer_core.pool: every window gets its OWN best
     mapping, labelled observations outside windows are dropped) against the
     standard IDF1 (one global matching per take, every labelled observation,
     counts summed over takes) on a constructed 20-take session in which the
     tracker makes a REPORTED (ambiguous) swap at the end of each crossing
     window and keeps the swapped identities afterwards.
  3. The same comparison on the tool's own synthetic practice takes.

Run (stdlib only, synthetic data, nothing written to the checkout):
  python3 -B probe_r3v_idf1_windowed_not_global.py <branch checkout root>
"""
import itertools
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import scorer_core  # noqa: E402


# ---- independent IDF1 ---------------------------------------------------------
def my_idf1_counts(links):
    """links: (time, obs_id, person, track, confident). Global optimal 1:1 matching."""
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    co = {}
    for l in links:
        if l[2] is not None and l[3] is not None:
            co[(l[2], l[3])] = co.get((l[2], l[3]), 0) + 1
    best = 0
    slots = tracks + [None] * len(people)          # a person may map to no track
    for perm in itertools.permutations(slots, len(people)):
        used = [t for t in perm if t is not None]
        if len(used) != len(set(used)):
            continue
        best = max(best, sum(co.get((p, t), 0) for p, t in zip(people, perm) if t is not None))
    n_truth = sum(1 for l in links if l[2] is not None)
    n_pred = sum(1 for l in links if l[3] is not None)
    return best, n_pred - best, n_truth - best


def my_idf1(tp, fp, fn):
    d = tp + 0.5 * fp + 0.5 * fn
    return None if d == 0 else tp / d


def links_from(rows, confident=True):
    return [(float(k), 'o%d' % k, p, t, confident if t is not None else None) for k, (p, t) in enumerate(rows)]


fails = 0


def check(name, links):
    global fails
    c = scorer_core.id_counts(links)
    tool = scorer_core.idf1(c)
    tp, fp, fn = my_idf1_counts(links)
    mine = my_idf1(tp, fp, fn)
    ok = (c['idtp'], c['idfp'], c['idfn']) == (tp, fp, fn) and (
        (tool is None and mine is None) or abs(tool - mine) < 1e-12)
    fails += 0 if ok else 1
    print('%s small case %-28s tool (tp,fp,fn)=(%s,%s,%s) idf1=%s | independent (%s,%s,%s) idf1=%s'
          % ('PASS' if ok else 'FAIL', name, c['idtp'], c['idfp'], c['idfn'],
             None if tool is None else round(tool, 4), tp, fp, fn, None if mine is None else round(mine, 4)))


# ---- 1. small cases -------------------------------------------------------------
check('perfect', links_from([('A', 1), ('B', 2)] * 10))
check('one swap halfway', links_from([('A', 1), ('B', 2)] * 5 + [('A', 2), ('B', 1)] * 5))
check('fragment', links_from([('A', 1), ('B', 2)] * 6 + [('A', 3), ('B', 2)] * 4))
check('swap + fragment', links_from([('A', 1), ('B', 2)] * 4 + [('A', 2), ('B', 1)] * 3 + [('A', 3), ('B', 1)] * 3))
check('unlinked and false obs', links_from([('A', 1)] * 8 + [('A', None)] * 2 + [(None, 1)] * 2))
check('three people two tracks', links_from([('A', 1), ('B', 2), ('C', 1)] * 4))
check('empty', [])


# ---- 2. constructed session: reported swap at the end of each window -----------
def take_links():
    out = []
    for k in range(40):
        swapped = k >= 24
        conf = not (24 <= k <= 27)          # ambiguous while the tracker is in doubt
        out.append((float(k), 'a%d' % k, 'A', 'T2' if swapped else 'T1', conf))
        out.append((float(k), 'b%d' % k, 'B', 'T1' if swapped else 'T2', conf))
    return out


window = [{'id': 'c1', 'kind': 'crossing', 'start': 10.0, 'end': 24.0}]
takes = [('take-%02d' % i, scorer_core.score_take(take_links(), window)) for i in range(20)]
rep = scorer_core.pool(takes, 0.9, 20, 0.05)
tp = fp = fn = 0
for _ in range(20):
    a, b, c = my_idf1_counts(take_links())
    tp, fp, fn = tp + a, fp + b, fn + c
glob = my_idf1(tp, fp, fn)
print('RESULT constructed session: windows %d, silent swaps %d, reported switches %d, tool pooled IDF1 %.4f, '
      'gate passed %s' % (rep['crossings'], rep['silent_swaps'], rep['reported_switches'], rep['pooled']['idf1'],
                          rep['gate']['passed']))
print('RESULT independent IDF1 (global matching per take, all labelled observations, summed): %.4f' % glob)
if rep['gate']['passed'] and glob < 0.9:
    fails += 1
    print('FAIL gate PASSES on windowed IDF1 %.3f while the standard IDF1 of the same tracks is %.3f (< 0.9)'
          % (rep['pooled']['idf1'], glob))
else:
    print('PASS windowed and global IDF1 agree on the gate')

# ---- 3. the tool's own synthetic practice takes -----------------------------------
try:
    import method
    import pipeline
    import synth
    values = dict(method.Method().values, room=synth.ROOM_MODEL)
    takes = []
    for name in sorted(synth.SCENES):
        for seed in range(3):
            takes.append(pipeline.synthetic_take(synth.scenario(name, seed, 'typical'), values))
    report = pipeline.score_takes(takes, values)
    tp = fp = fn = 0
    for take_id, recs, truth in takes:
        links, _ = pipeline.links_for_take(recs, truth, values['scoring']['scored_states'])
        a, b, c = my_idf1_counts(links)
        tp, fp, fn = tp + a, fp + b, fn + c
    print('RESULT synthetic practice (8 scenes x 3 seeds, typical): tool pooled windowed IDF1 %.4f; '
          'independent whole-take global IDF1 %.4f; labelled obs in windows %d of %d'
          % (report['pooled']['idf1'], my_idf1(tp, fp, fn),
             sum(r['observations'] for r in report['windows']),
             sum(len(pipeline.links_for_take(r, t, ['confirmed'])[0]) for _, r, t in takes)))
except Exception as e:  # part 3 is informative only
    print('RESULT part 3 skipped: %r' % (e,))

print('FAIL count: %d' % fails)
sys.exit(1 if fails else 0)

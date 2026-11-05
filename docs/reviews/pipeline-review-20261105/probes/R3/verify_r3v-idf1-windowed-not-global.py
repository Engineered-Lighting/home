"""verify_r3v-idf1-windowed-not-global.py -- R3 (den-track), reproduce lens.

What it checks
  Whether the gate's "pooled IDF1" (scorer_core.score_take: each window gets
  its own best people-to-tracks mapping over only the labelled observations
  inside it; scorer_core.pool sums those counts) differs materially from the
  standard IDF1 (one optimal global matching per take over every labelled
  observation the take holds, IDTP/IDFP/IDFN summed over takes) on the REAL
  tracker's output, not only on hand-built links.

  For each synthetic scene (the tool's own synth.py, typical noise, the
  synthetic den closed as the room) it tracks N seeds with the real tracker
  and prints: windowed pooled IDF1 (the tool's number), independent global
  IDF1 (brute-force matching written here), the share of labelled
  observations that lie outside every window, and the takes whose global
  IDF1 is below 0.9 while their window IDF1 is at or above 0.9.

Run (stdlib only, synthetic data in memory, nothing written to the checkout):
  python3 -B verify_r3v-idf1-windowed-not-global.py <branch checkout root> [seeds] [clean|typical|noisy]
"""
import itertools
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
NOISE = sys.argv[3] if len(sys.argv) > 3 else 'typical'
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import method  # noqa: E402
import pipeline  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402


def global_counts(links):
    """Independent IDF1 counts: brute-force optimal 1:1 people->tracks matching."""
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    co = {}
    for l in links:
        if l[2] is not None and l[3] is not None:
            co[(l[2], l[3])] = co.get((l[2], l[3]), 0) + 1
    best = 0
    for perm in itertools.permutations(tracks + [None] * len(people), len(people)):
        used = [t for t in perm if t is not None]
        if len(used) != len(set(used)):
            continue
        best = max(best, sum(co.get((p, t), 0) for p, t in zip(people, perm) if t is not None))
    n_truth = sum(1 for l in links if l[2] is not None)
    n_pred = sum(1 for l in links if l[3] is not None)
    return best, n_pred - best, n_truth - best


def f1(tp, fp, fn):
    d = 2 * tp + fp + fn
    return None if d == 0 else 2 * tp / d


values = dict(method.Method().values, room=synth.ROOM_MODEL)
scored = values['scoring']['scored_states']
tot_w = [0, 0, 0]
tot_g = [0, 0, 0]
tot_in = tot_all = 0
flip_takes = []
print('%-26s %6s %8s %8s %9s %8s' % ('scene', 'takes', 'win_IDF1', 'glob_IDF1', 'outside%', 'reported'))
for name in sorted(synth.SCENES):
    sw = [0, 0, 0]
    sg = [0, 0, 0]
    n_in = n_all = rep_sw = 0
    for seed in range(SEEDS):
        take_id, recs, truth = pipeline.synthetic_take(synth.scenario(name, seed, NOISE), values)
        links, _ = pipeline.links_for_take(recs, truth, scored)
        res = scorer_core.score_take(links, truth['windows'])
        wc = [sum(r[k] for r in res['windows']) for k in ('idtp', 'idfp', 'idfn')]
        gc = global_counts(links)
        rep_sw += sum(r['reported_switches'] for r in res['windows']) + res['switches_outside_windows'] \
            - res['silent_swaps_outside_windows']
        for i in range(3):
            sw[i] += wc[i]
            sg[i] += gc[i]
        inside = sum(r['observations'] for r in res['windows'])
        n_in += inside
        n_all += len(links)
        wf, gf = f1(*wc), f1(*gc)
        if wf is not None and gf is not None and wf >= 0.9 and gf < 0.9:
            flip_takes.append((take_id, round(wf, 3), round(gf, 3)))
    for i in range(3):
        tot_w[i] += sw[i]
        tot_g[i] += sg[i]
    tot_in += n_in
    tot_all += n_all
    print('%-26s %6d %8.3f %8.3f %8.1f%% %8d' % (name, SEEDS, f1(*sw), f1(*sg), 100.0 * (n_all - n_in) / n_all, rep_sw))
print('RESULT noise %s, seeds 0-%d' % (NOISE, SEEDS - 1))
print('RESULT all scenes: windowed pooled IDF1 %.4f; independent global IDF1 %.4f; labelled obs outside windows %.1f%%'
      % (f1(*tot_w), f1(*tot_g), 100.0 * (tot_all - tot_in) / tot_all))
print('RESULT takes with window IDF1 >= 0.9 but global IDF1 < 0.9: %d of %d' % (len(flip_takes), SEEDS * len(synth.SCENES)))
for t in flip_takes:
    print('  %s window %.3f global %.3f' % t)

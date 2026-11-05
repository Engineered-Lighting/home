"""probe_r3v_idf1_windowed_by_scene.py -- R3 (den-track), verifier for r3v-idf1-windowed-not-global.

What it checks
  How far the gate's windowed IDF1 (each window its own best mapping, labelled
  observations outside windows dropped) is from the standard whole-take IDF1
  (one optimal global matching per take, all labelled observations, summed over
  takes) on the tool's OWN synthetic scenes, per scene, typical noise, with the
  method's proposed values and the synthetic den. Informative: it gauges how
  realistic a verdict flip is, it does not assert one.

Run (stdlib only, synthetic data in memory, nothing written to the checkout):
  python3 -B probe_r3v_idf1_windowed_by_scene.py <branch checkout root> [seeds]
"""
import itertools
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import method  # noqa: E402
import pipeline  # noqa: E402
import synth  # noqa: E402


def global_counts(links):
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    co = {}
    for l in links:
        if l[2] is not None and l[3] is not None:
            co[(l[2], l[3])] = co.get((l[2], l[3]), 0) + 1
    best = 0
    slots = tracks + [None] * len(people)
    for perm in itertools.permutations(slots, len(people)):
        used = [t for t in perm if t is not None]
        if len(used) != len(set(used)):
            continue
        best = max(best, sum(co.get((p, t), 0) for p, t in zip(people, perm) if t is not None))
    return best, sum(1 for l in links if l[3] is not None) - best, sum(1 for l in links if l[2] is not None) - best


def f1(tp, fp, fn):
    d = 2 * tp + fp + fn
    return None if d == 0 else 2.0 * tp / d


values = dict(method.Method().values, room=synth.ROOM_MODEL)
tot_w = [0, 0, 0]
tot_g = [0, 0, 0]
for name in sorted(synth.SCENES):
    takes = [pipeline.synthetic_take(synth.scenario(name, s, 'typical'), values) for s in range(SEEDS)]
    rep = pipeline.score_takes(takes, values)
    w = rep['pooled']
    g = [0, 0, 0]
    for _, recs, truth in takes:
        links, _ = pipeline.links_for_take(recs, truth, values['scoring']['scored_states'])
        a, b, c = global_counts(links)
        g = [g[0] + a, g[1] + b, g[2] + c]
    for i, k in enumerate(('idtp', 'idfp', 'idfn')):
        tot_w[i] += w[k]
    tot_g = [x + y for x, y in zip(tot_g, g)]
    print('RESULT scene %-26s seeds %d windowed IDF1 %.4f  whole-take IDF1 %.4f  reported %d  silent %d'
          % (name, SEEDS, w['idf1'], f1(*g), rep['reported_switches'], rep['silent_swaps']))
print('RESULT all scenes windowed %.4f whole-take %.4f' % (f1(tot_w[0], tot_w[1], tot_w[2]), f1(*tot_g)))

"""verify_tiebreak_idf1-per-window-mapping: does den-track's pooled IDF1
(each window's own mapping, in-window observations only) differ from a
standard take-level IDF1 (one optimal mapping per take over every labelled
observation, counts summed over takes) on the tool's OWN synthetic scenes,
which have exactly one window per take (the documented script)?

Also prints, per scene, how many takes end with a person on a different
track than they started on in a way the in-window score cannot see (a
reported switch that persists after the window closes).

Synthetic data only, in memory; nothing touches a network or device.

Run:  python3 -I verify_tiebreak_idf1-per-window-mapping.py <root of the rung-3-track checkout> [seeds] [noise] [first_seed]
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
NOISE = sys.argv[3] if len(sys.argv) > 3 else 'typical'
FIRST = int(sys.argv[4]) if len(sys.argv) > 4 else 0
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402

m = method_mod.Method()
values = dict(m.values, room=synth.ROOM_MODEL)
sc = values['scoring']

by_scene = {}
tool_takes = []
whole = {'idtp': 0, 'idfp': 0, 'idfn': 0}
for name in sorted(synth.SCENES):
    for seed in range(FIRST, FIRST + SEEDS):
        take_id, recs, truth = pipeline.synthetic_take(synth.scenario(name, seed, NOISE), values)
        links, _ = pipeline.links_for_take(recs, truth, sc['scored_states'])
        res = scorer_core.score_take(links, truth['windows'])
        tool_takes.append((take_id, res))
        c = scorer_core.id_counts(links)          # one mapping, whole take
        k = by_scene.setdefault(name, {'tool': [0, 0, 0], 'whole': [0, 0, 0], 'n_windows': 0,
                                       'persist_after': 0, 'tool_take_ge': 0, 'whole_take_ge': 0, 'takes': 0})
        k['takes'] += 1
        k['n_windows'] += len(truth['windows'])
        for i, key in enumerate(('idtp', 'idfp', 'idfn')):
            k['whole'][i] += c[key]
            whole[key] += c[key]
            k['tool'][i] += sum(r[key] for r in res['windows'])
        # does the in-window mapping disagree with the whole-take mapping?
        wmap = c['mapping']
        for r in res['windows']:
            inside = [l for l in links if r['start'] <= l[0] <= r['end']]
            if scorer_core.id_counts(inside)['mapping'] != wmap:
                k['persist_after'] += 1

pooled = scorer_core.pool(tool_takes, sc['idf1_gate'], sc['min_crossings'], sc['alpha'])
f = lambda t: (2 * t[0] / (2 * t[0] + t[1] + t[2])) if (2 * t[0] + t[1] + t[2]) else float('nan')
print('noise=%s seeds=%d-%d, one window per take (synthetic script)' % (NOISE, FIRST, FIRST + SEEDS - 1))
print('%-26s %6s %8s %10s %18s' % ('scene', 'takes', 'tool', 'take-level', 'window mapping != take mapping'))
for name, k in sorted(by_scene.items()):
    print('%-26s %6d %8.3f %10.3f %18d' % (name, k['takes'], f(k['tool']), f(k['whole']), k['persist_after']))
tool_idf1 = pooled['pooled']['idf1']
whole_idf1 = scorer_core.idf1(whole)
print('POOLED tool (per-window, in-window) IDF1 = %.4f  gate idf1_met=%s' % (tool_idf1, pooled['gate']['idf1_met']))
print('POOLED take-level (one mapping per take, all labelled obs) IDF1 = %.4f  meets 0.9: %s'
      % (whole_idf1, whole_idf1 >= sc['idf1_gate']))
print('silent swaps in windows %d, outside %d, reported %d, switches outside windows %d'
      % (pooled['silent_swaps'], pooled['silent_swaps_outside_windows'], pooled['reported_switches'],
         pooled['switches_outside_windows']))
diff = (tool_idf1 >= sc['idf1_gate']) != (whole_idf1 >= sc['idf1_gate'])
print('RESULT: %s' % ('IDF1 gate verdict DIFFERS between the two definitions' if diff
                      else 'IDF1 gate verdict is the SAME under both definitions (tool %s take-level)'
                      % ('<=' if tool_idf1 <= whole_idf1 else '>')))

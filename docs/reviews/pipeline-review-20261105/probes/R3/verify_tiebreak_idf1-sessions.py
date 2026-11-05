"""verify_tiebreak_idf1-sessions: on many synthetic 30-take sessions shaped
like the proposed crossings plan (one window per take: 4 clean + 4 close +
4 touching crossings, 6 behind the armchair, 4 occlusions, 4 near passes,
2 entry/exit + 2 swaps at the door), how often does den-track's IDF1 gate
verdict (each window's own mapping, in-window observations only) differ
from the verdict of a take-level IDF1 (one optimal mapping per take over
every labelled observation, counts summed over takes)?

Synthetic data only, in memory; nothing touches a network or device.

Run:  python3 -I verify_tiebreak_idf1-sessions.py <root of the rung-3-track checkout> [sessions] [noise]
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
NOISE = sys.argv[3] if len(sys.argv) > 3 else 'typical'
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402

PLAN = [('clean_crossing', 4), ('close_crossing', 4), ('touching_crossing', 4), ('crossing_behind_armchair', 6),
        ('armchair_occlusion', 4), ('near_pass', 4), ('entry_exit', 2), ('swap_at_door', 2)]
m = method_mod.Method()
values = dict(m.values, room=synth.ROOM_MODEL)
sc = values['scoring']
gate = sc['idf1_gate']
higher = flips_tool_pass = flips_tool_fail = 0
diffs = []
for s in range(N):
    tool_takes, whole = [], {'idtp': 0, 'idfp': 0, 'idfn': 0}
    for name, count in PLAN:
        for i in range(count):
            seed = 5000 + s * 10 + i
            take_id, recs, truth = pipeline.synthetic_take(synth.scenario(name, seed, NOISE), values)
            links, _ = pipeline.links_for_take(recs, truth, sc['scored_states'])
            tool_takes.append((take_id, scorer_core.score_take(links, truth['windows'])))
            c = scorer_core.id_counts(links)
            for k in whole:
                whole[k] += c[k]
    rep = scorer_core.pool(tool_takes, gate, sc['min_crossings'], sc['alpha'])
    t, w = rep['pooled']['idf1'], scorer_core.idf1(whole)
    diffs.append(t - w)
    higher += t > w
    if (t >= gate) and not (w >= gate):
        flips_tool_pass += 1
        print('session %d: tool IDF1 %.4f passes, take-level %.4f fails (silent swaps %d, gate passed=%s)'
              % (s, t, w, rep['silent_swaps'] + rep['silent_swaps_outside_windows'], rep['gate']['passed']))
    elif (w >= gate) and not (t >= gate):
        flips_tool_fail += 1
        print('session %d: tool IDF1 %.4f fails, take-level %.4f passes' % (s, t, w))
diffs.sort()
print('noise=%s sessions=%d of 30 takes' % (NOISE, N))
print('tool IDF1 above take-level in %d of %d sessions; difference min %.4f median %.4f max %.4f'
      % (higher, N, diffs[0], diffs[len(diffs) // 2], diffs[-1]))
print('RESULT: IDF1 verdict flips: tool passes / take-level fails in %d; tool fails / take-level passes in %d (of %d)'
      % (flips_tool_pass, flips_tool_fail, N))

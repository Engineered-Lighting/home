"""probe_v_unsure_realistic2: verifier probe for finding unsure-labels-flip-silence.

Models the finding's own wording ("the owner labels about 1 s of a touching
crossing 'cannot tell'"): every observation within a span of L seconds is
marked "?", the span starting `offset` seconds after the moment of closest
approach of the two people's TRUE positions minus L/2 (offset 0 = centred on
closest approach). Counts, on real tracker output, switches whose verdict
flips and windows whose pass/fail flips.

Synthetic data only, in memory, no network.
Run: python3 probe_v_unsure_realistic2.py <root of the rung-3-track checkout>
"""
import copy
import math
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402

VALUES = dict(method_mod.Method(os.path.join(TOOL, 'METHOD.md')).values, room=synth.ROOM_MODEL)


def sw(res):
    out = {}
    for s in [s for r in res['windows'] for s in r['switch_detail']] + list(res['outside_detail']):
        out[(s['track'], s['from'], s['to'])] = s['silent']
    return out


def main():
    spans = [(1.0, 0.0), (1.0, 0.5), (1.0, 1.0), (2.0, 0.0), (2.0, 1.0), (3.0, 0.0)]
    tot = {k: [0, 0, 0, 0] for k in spans}
    nwin = base_fail = base_rep = base_sil = 0
    for name in ('touching_crossing', 'close_crossing', 'near_pass', 'crossing_behind_armchair'):
        for seed in range(200, 220):
            sc = synth.scenario(name, seed, 'typical')
            obs, lines = synth.records(sc)
            frames = pipeline.observation_frames(obs, VALUES['input'])
            recs = pipeline.track_frames(frames, VALUES)
            tr = pipeline.truth_from_lines(lines)
            dist = [(f['t'], math.dist(f['truth_xy']['A'], f['truth_xy']['B'])) for f in sc['frames']
                    if f['truth_xy'].get('A') and f['truth_xy'].get('B')]
            if not dist:
                continue
            t_close = min(dist, key=lambda x: x[1])[0]
            links, _ = pipeline.links_for_take(recs, tr, VALUES['scoring']['scored_states'])
            res = scorer_core.score_take(links, tr['windows'])
            s0 = sw(res)
            f0 = [r['silent_swaps'] > 0 for r in res['windows']]
            nwin += len(f0)
            base_fail += sum(f0)
            base_rep += sum(1 for v in s0.values() if not v)
            base_sil += sum(1 for v in s0.values() if v)
            for (L, off) in spans:
                a, b = t_close - L / 2 + off, t_close + L / 2 + off
                tr2 = copy.deepcopy(tr)
                for o, (ts, p) in list(tr2['labels'].items()):
                    if a <= ts <= b:
                        del tr2['labels'][o]
                links2, _ = pipeline.links_for_take(recs, tr2, VALUES['scoring']['scored_states'])
                res2 = scorer_core.score_take(links2, tr2['windows'])
                s1 = sw(res2)
                f1 = [r['silent_swaps'] > 0 for r in res2['windows']]
                t = tot[(L, off)]
                t[0] += sum(1 for k, v in s1.items() if k in s0 and s0[k] is False and v is True)
                t[1] += sum(1 for k, v in s1.items() if k in s0 and s0[k] is True and v is False)
                t[2] += sum(1 for x, y in zip(f0, f1) if y and not x)
                t[3] += sum(1 for x, y in zip(f0, f1) if x and not y)
    print('touching/close/near_pass/behind_armchair, seeds 200-219, typical: windows %d, baseline failed %d, switches reported %d silent %d'
          % (nwin, base_fail, base_rep, base_sil))
    for k in spans:
        t = tot[k]
        print('  "?" span %.1f s, offset %+.1f s from centred on closest approach: reported->silent %d, silent->reported %d, '
              'windows newly failed %d, failed windows now passing %d' % (k[0], k[1], t[0], t[1], t[2], t[3]))
    print('RESULT: %s' % ('flips seen' if any(sum(v) for v in tot.values()) else 'no verdict flips under these labelling models'))


if __name__ == '__main__':
    main()

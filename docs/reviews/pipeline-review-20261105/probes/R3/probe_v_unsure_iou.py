"""probe_v_unsure_iou: verifier probe for finding unsure-labels-flip-silence.

Labeller model: every observation in a frame is marked "?" while the two
people's TRUE image boxes (synth._box at the true floor point, no jitter)
overlap with IoU above a threshold, optionally held for `hold` seconds after
the overlap ends (an owner who stays unsure a little longer). Counts verdict
flips on real tracker output.

Synthetic data only, in memory, no network.
Run: python3 probe_v_unsure_iou.py <root of the rung-3-track checkout>
"""
import copy
import os
import random
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402

VALUES = dict(method_mod.Method(os.path.join(TOOL, 'METHOD.md')).values, room=synth.ROOM_MODEL)
RNG = random.Random(0)


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def sw(res):
    out = {}
    for s in [s for r in res['windows'] for s in r['switch_detail']] + list(res['outside_detail']):
        out[(s['track'], s['from'], s['to'])] = s['silent']
    return out


def main():
    models = [(0.0, 0.0), (0.2, 0.0), (0.0, 0.5), (0.0, 1.0)]
    tot = {k: [0, 0, 0, 0, 0] for k in models}
    nwin = base_fail = base_rep = 0
    for name in ('touching_crossing', 'close_crossing', 'near_pass', 'crossing_behind_armchair'):
        for seed in range(200, 220):
            sc = synth.scenario(name, seed, 'typical')
            obs, lines = synth.records(sc)
            frames = pipeline.observation_frames(obs, VALUES['input'])
            recs = pipeline.track_frames(frames, VALUES)
            tr = pipeline.truth_from_lines(lines)
            ov = []
            for f in sc['frames']:
                p = f['truth_xy']
                if p.get('A') and p.get('B'):
                    ov.append((f['t'], iou(synth._box(p['A'], RNG, 0.0), synth._box(p['B'], RNG, 0.0))))
            links, _ = pipeline.links_for_take(recs, tr, VALUES['scoring']['scored_states'])
            res = scorer_core.score_take(links, tr['windows'])
            s0 = sw(res)
            f0 = [r['silent_swaps'] > 0 for r in res['windows']]
            nwin += len(f0)
            base_fail += sum(f0)
            base_rep += sum(1 for v in s0.values() if not v)
            for (thr, hold) in models:
                unsure_t = [t for t, v in ov if v > thr]
                tr2 = copy.deepcopy(tr)
                for o, (ts, p) in list(tr2['labels'].items()):
                    if any(u <= ts <= u + hold + 1e-9 for u in unsure_t):
                        del tr2['labels'][o]
                        tot[(thr, hold)][4] += 1
                links2, _ = pipeline.links_for_take(recs, tr2, VALUES['scoring']['scored_states'])
                res2 = scorer_core.score_take(links2, tr2['windows'])
                s1 = sw(res2)
                f1 = [r['silent_swaps'] > 0 for r in res2['windows']]
                t = tot[(thr, hold)]
                t[0] += sum(1 for k, v in s1.items() if k in s0 and s0[k] is False and v is True)
                t[1] += sum(1 for k, v in s1.items() if k in s0 and s0[k] is True and v is False)
                t[2] += sum(1 for x, y in zip(f0, f1) if y and not x)
                t[3] += sum(1 for x, y in zip(f0, f1) if x and not y)
    print('windows %d, baseline failed %d, reported switches %d' % (nwin, base_fail, base_rep))
    for k in models:
        t = tot[k]
        print('  "?" while true-box IoU > %.1f, held %.1f s after: unsure obs %d, reported->silent %d, silent->reported %d, '
              'windows newly failed %d, failed windows now passing %d' % (k[0], k[1], t[4], t[0], t[1], t[2], t[3]))


if __name__ == '__main__':
    main()

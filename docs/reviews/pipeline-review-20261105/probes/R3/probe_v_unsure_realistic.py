"""probe_v_unsure_realistic: verifier probe for finding unsure-labels-flip-silence.

The reported probe marks "?" exactly from a reported switch to the track's
first confident record of the new person (an adversarial span chosen to
outlast the tracker's ambiguity interval). This probe instead models a
plausible labeller: every observation in a frame is marked "?" (cannot tell)
while the two people's TRUE floor positions are closer than d metres (their
bodies overlap in the image). It then counts, on real tracker output, how
many switches change verdict (reported->silent, silent->reported) and how
many windows change pass/fail.

Synthetic data only, in memory, no network.
Run: python3 probe_v_unsure_realistic.py <root of the rung-3-track checkout>
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


def run(scenes, seeds, dists, noise='typical'):
    tot = {d: {'rep2sil': 0, 'sil2rep': 0, 'win_fail_new': 0, 'win_fail_gone': 0, 'unsure_obs': 0} for d in dists}
    base_sil = base_rep = base_failed = nwin = 0
    for name in scenes:
        for seed in seeds:
            sc = synth.scenario(name, seed, noise)
            obs, lines = synth.records(sc)
            frames = pipeline.observation_frames(obs, VALUES['input'])
            recs = pipeline.track_frames(frames, VALUES)
            tr = pipeline.truth_from_lines(lines)
            close_t = {}
            for f in sc['frames']:
                p = f['truth_xy']
                if 'A' in p and 'B' in p and p['A'] and p['B']:
                    close_t[f['t']] = math.dist(p['A'], p['B'])
            links, _ = pipeline.links_for_take(recs, tr, VALUES['scoring']['scored_states'])
            res = scorer_core.score_take(links, tr['windows'])
            sw0 = {(s['track'], s['from'], s['to']): s['silent'] for r in res['windows'] for s in r['switch_detail']}
            sw0.update({(s['track'], s['from'], s['to']): s['silent'] for s in res['outside_detail']})
            fail0 = [r['silent_swaps'] > 0 for r in res['windows']]
            nwin += len(fail0)
            base_failed += sum(fail0)
            base_sil += sum(1 for v in sw0.values() if v)
            base_rep += sum(1 for v in sw0.values() if not v)
            for d in dists:
                tr2 = copy.deepcopy(tr)
                for o, (ts, person) in list(tr2['labels'].items()):
                    dd = close_t.get(ts)
                    if dd is not None and dd < d:
                        del tr2['labels'][o]
                        tot[d]['unsure_obs'] += 1
                links2, _ = pipeline.links_for_take(recs, tr2, VALUES['scoring']['scored_states'])
                res2 = scorer_core.score_take(links2, tr2['windows'])
                sw1 = {(s['track'], s['from'], s['to']): s['silent'] for r in res2['windows'] for s in r['switch_detail']}
                sw1.update({(s['track'], s['from'], s['to']): s['silent'] for s in res2['outside_detail']})
                for k, v in sw1.items():
                    if k in sw0:
                        if sw0[k] is False and v is True:
                            tot[d]['rep2sil'] += 1
                        if sw0[k] is True and v is False:
                            tot[d]['sil2rep'] += 1
                    elif v is True:
                        tot[d]['rep2sil'] += 0  # new switch key; counted below via windows
                fail1 = [r['silent_swaps'] > 0 for r in res2['windows']]
                tot[d]['win_fail_new'] += sum(1 for a, b in zip(fail0, fail1) if b and not a)
                tot[d]['win_fail_gone'] += sum(1 for a, b in zip(fail0, fail1) if a and not b)
    print('scenes %s seeds %d-%d noise %s: windows %d, baseline switches silent %d reported %d, failed windows %d'
          % (','.join(scenes), seeds[0], seeds[-1], noise, nwin, base_sil, base_rep, base_failed))
    for d in dists:
        t = tot[d]
        print('  "?" while true distance < %.2f m: unsure obs %5d | reported->silent %2d | silent->reported %2d | '
              'windows newly failed %2d | failed windows now passing %2d'
              % (d, t['unsure_obs'], t['rep2sil'], t['sil2rep'], t['win_fail_new'], t['win_fail_gone']))
    return tot


if __name__ == '__main__':
    dists = [0.3, 0.45, 0.6, 0.8, 1.0]
    run(['touching_crossing', 'close_crossing', 'near_pass'], list(range(200, 220)), dists)
    run(['crossing_behind_armchair'], list(range(200, 220)), dists)

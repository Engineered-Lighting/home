"""probe_r3a_unsure_launders_swap: the scorer judges a switch silent or
reported by the track's status at the FIRST LABELLED observation of the new
person, not over the frames in which the switch could have happened.  When
the owner labels the overlap frames "?" (cannot tell, which the truth format
allows and excludes), a swap made while the track said `confident` is scored
as reported (gate passes), and a swap made inside an ambiguous interval can
be scored as silent (gate fails).

Part 1 (constructed track records, the scorer the CLI uses:
pipeline.score_takes): T1 holds A (confident); at t=5,6 T1 holds B's
observations while still `confident` (labelled "?"); at t=7 T1 turns
ambiguous and its first labelled B observation arrives.  Expect: a silent
swap.  Tool: 0 silent, 1 reported, gate silent_swaps_zero True.
Part 2 (reverse): T1 turns ambiguous at t=5 when it takes B (labelled "?"),
the interval settles at t=7 (confident), first labelled B at t=7.  Tool: 1
silent swap, though the tracker reported the doubt while the switch happened.
Part 3 (real tracker output): synthetic touching/behind-armchair crossings,
seeds 200-239: for each silent swap found, relabel as "?" the frames between
the switch and the track's next ambiguous labelled record; count how many
silent swaps the scorer then calls reported.

Part 4 (real tracker output, reverse): for each reported switch on
synthetic touching/near-pass/close crossings (seeds 200-219), mark "?"
from the switch to the track's first confident record of the new person;
count switches then scored silent and the "?" run length needed.

Run:  python3 probe_r3a_unsure_launders_swap.py <root of the rung-3-track checkout>
"""
import copy
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import synth  # noqa: E402

VALUES = dict(method_mod.Method().values, room=synth.ROOM_MODEL)


def rec(t, tid, status, obs, state='confirmed'):
    return {'schema_version': 'track/v1', 'capture_ts': t, 'track_id': tid, 'state': state,
            'association': {'status': status, 'hypotheses': [], 'since_ts': 0.0}, 'last_obs_ids': obs}


def truth(labels, windows):
    return {'take': {}, 'labels': labels, 'windows': windows, 'unsure': 0}


def constructed(status_t1):
    recs, labels = [], {}
    for t in range(10):
        a, b = 'a%d' % t, 'b%d' % t
        if t < 5:
            on1, on2 = a, b
        else:
            on1, on2 = b, a          # the swap happens at t=5
        recs.append(rec(float(t), 'T1', status_t1[t], [on1]))
        recs.append(rec(float(t), 'T2', status_t1[t], [on2]))
        if t in (5, 6):
            continue                 # owner: cannot tell ("?"), excluded
        labels[a] = (float(t), 'A')
        labels[b] = (float(t), 'B')
    return pipeline.score_takes([('take', recs, truth(labels, [{'id': 'w', 'kind': 'crossing', 'start': 4.5, 'end': 9.5}]))], VALUES)


def part12():
    conf, amb = 'confident', 'ambiguous'
    r1 = constructed([conf] * 7 + [amb] * 3)
    print('part 1: T1/T2 confident while holding the other person at t=5,6; ambiguous from t=7')
    print('  tool: silent_swaps=%d reported=%d silent_swaps_zero=%s' % (r1['silent_swaps'], r1['reported_switches'], r1['gate']['silent_swaps_zero']))
    p1 = r1['silent_swaps'] == 0 and r1['gate']['silent_swaps_zero']
    print('RESULT part 1: %s' % ('FAIL: a swap made while confident is scored as reported' if p1 else 'PASS'))
    r2 = constructed([conf] * 5 + [amb] * 2 + [conf] * 3)
    print('part 2: ambiguous at t=5,6 while the swap happens; settled (confident) from t=7')
    print('  tool: silent_swaps=%d reported=%d silent_swaps_zero=%s' % (r2['silent_swaps'], r2['reported_switches'], r2['gate']['silent_swaps_zero']))
    p2 = r2['silent_swaps'] > 0
    print('RESULT part 2: %s' % ('FAIL: a swap made inside an ambiguous interval is scored as silent' if p2 else 'PASS'))


def part3():
    import scorer_core
    found = laundered = 0
    for name in ('touching_crossing', 'crossing_behind_armchair', 'close_crossing'):
        for seed in range(200, 240):
            take_id, recs, tr = pipeline.synthetic_take(synth.scenario(name, seed, 'typical'), VALUES)
            links, _ = pipeline.links_for_take(recs, tr, VALUES['scoring']['scored_states'])
            res = scorer_core.score_take(links, tr['windows'])
            silent = [s for r in res['windows'] for s in r['switch_detail'] if s['silent']] + \
                     [s for s in res['outside_detail'] if s['silent']]
            if not silent:
                continue
            for s in silent:
                found += 1
                track, t_sw, newp = s['track'], s['time'], s['to']
                # previous labelled obs of the old person on that track
                prev_t = max(l[0] for l in links if l[3] == track and l[0] < t_sw and l[2] is not None)
                # the track's records after the switch
                times = sorted({r['capture_ts'] for r in recs if r['track_id'] == track and r['capture_ts'] >= t_sw
                                and r['state'] == 'confirmed' and r['association']['status'] == 'ambiguous'
                                and any(tr['labels'].get(o, (0, None))[1] == newp for o in r['last_obs_ids'])})
                if not times:
                    continue
                t_amb = times[0]
                tr2 = copy.deepcopy(tr)
                for o, (ts, p) in list(tr2['labels'].items()):
                    if prev_t < ts < t_amb:
                        del tr2['labels'][o]     # the owner marks these frames "?"
                links2, _ = pipeline.links_for_take(recs, tr2, VALUES['scoring']['scored_states'])
                res2 = scorer_core.score_take(links2, tr2['windows'])
                still = [x for r in res2['windows'] for x in r['switch_detail'] if x['silent'] and x['track'] == track] + \
                        [x for x in res2['outside_detail'] if x['silent'] and x['track'] == track]
                if not still:
                    laundered += 1
                    print('  %s: silent switch on %s at %.2f (%s->%s); "?" on %.2f..%.2f (%.2f s) -> scored as reported'
                          % (take_id, track, t_sw, s['from'], newp, prev_t, t_amb, t_amb - prev_t))
    print('part 3: real tracker output: silent swaps found %d, laundered into reported by "?" labels %d' % (found, laundered))
    print('RESULT part 3: %s' % ('FAIL: %d silent swap(s) disappear when the overlap frames are marked "?"' % laundered if laundered else 'PASS/not reproduced'))




def part4():
    """Reverse on real tracker output: for each REPORTED switch, mark "?" the
    frames from the switch until the track's first `confident` record that
    holds the new person; count switches the scorer then calls silent, and
    how long the "?" run had to be."""
    import scorer_core
    found = flipped = 0
    spans = []
    for name in ('touching_crossing', 'near_pass', 'close_crossing'):
        for seed in range(200, 220):
            take_id, recs, tr = pipeline.synthetic_take(synth.scenario(name, seed, 'typical'), VALUES)
            links, _ = pipeline.links_for_take(recs, tr, VALUES['scoring']['scored_states'])
            res = scorer_core.score_take(links, tr['windows'])
            rep = [s for r in res['windows'] for s in r['switch_detail'] if not s['silent']]
            for s in rep:
                found += 1
                track, t_sw, newp = s['track'], s['time'], s['to']
                conf = sorted(r['capture_ts'] for r in recs if r['track_id'] == track and r['capture_ts'] > t_sw
                              and r['state'] == 'confirmed' and r['association']['status'] == 'confident'
                              and any(tr['labels'].get(o, (0, None))[1] == newp for o in r['last_obs_ids']))
                if not conf:
                    continue
                t_c = conf[0]
                tr2 = copy.deepcopy(tr)
                for o, (ts, p) in list(tr2['labels'].items()):
                    if t_sw <= ts < t_c:
                        del tr2['labels'][o]
                links2, _ = pipeline.links_for_take(recs, tr2, VALUES['scoring']['scored_states'])
                res2 = scorer_core.score_take(links2, tr2['windows'])
                now = [x for r in res2['windows'] for x in r['switch_detail'] if x['silent'] and x['track'] == track] + \
                      [x for x in res2['outside_detail'] if x['silent'] and x['track'] == track]
                if now:
                    flipped += 1
                    spans.append(round(t_c - t_sw, 2))
    spans.sort()
    print('part 4: reported switches %d; scored SILENT after "?" from the switch to the settle: %d; "?" run lengths (s): %s'
          % (found, flipped, spans))
    print('RESULT part 4: %s' % ('FAIL: %d reported switch(es) become silent swaps (gate fails) under "?" runs of median %.2f s'
                                 % (flipped, spans[len(spans) // 2]) if flipped else 'PASS/not reproduced'))


if __name__ == '__main__':
    part12()
    part3()
    part4()

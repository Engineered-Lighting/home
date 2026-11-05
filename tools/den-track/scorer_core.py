"""E2 scoring on labelled links, independent of record field names.

A link is one observation as both sides saw it:
    (time, obs_id, person, track, confident)
person is the owner's label ('A', 'B', ...) or None for "not a person"; track
is the track the tracker put the observation on, or None; confident is whether
that track's record reported confidence (association.status is `confident`).
"""
from scorer_math import best_matching, upper_bound


def id_counts(links):
    """IDTP, IDFP, IDFN over links, with the best one-to-one mapping of
    people to tracks (Ristani et al. 2016, at observation level)."""
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    co = {}
    for _, _, person, track, _ in links:
        if person is not None and track is not None:
            co[(person, track)] = co.get((person, track), 0) + 1
    weights = [[co.get((p, t), 0) for t in tracks] for p in people]
    idtp, pairs = best_matching(weights)
    n_truth = sum(1 for l in links if l[2] is not None)
    n_pred = sum(1 for l in links if l[3] is not None)
    mapping = {people[i]: tracks[j] for i, j in pairs}
    return {'idtp': idtp, 'idfp': n_pred - idtp, 'idfn': n_truth - idtp, 'mapping': mapping}


def idf1(counts):
    d = 2 * counts['idtp'] + counts['idfp'] + counts['idfn']
    return None if d == 0 else 2 * counts['idtp'] / d


def switches(links):
    """Every time a track moves from one labelled person to another.

    Returns [{time, obs_id, track, from, to, silent}] in time order. A switch
    is silent when the track's record reported confidence at that link.
    """
    last = {}
    out = []
    for time, obs_id, person, track, confident in sorted(links, key=lambda l: (l[0], str(l[3]), l[1])):
        if person is None or track is None:
            continue
        before = last.get(track)
        if before is not None and before != person:
            out.append({'time': time, 'obs_id': obs_id, 'track': track, 'from': before, 'to': person,
                        'silent': confident is True})
        last[track] = person
    return out


def _inside(time, window):
    return window['start'] <= time <= window['end']


def score_take(links, windows):
    """One take's crossings. windows: [{'id', 'kind', 'start', 'end'}].

    Switches are found on the whole take (so a track's person before a window
    is known) and counted in the window they fall in. IDF1 uses each window's
    own best mapping.
    """
    all_switches = switches(links)
    rows = []
    for w in windows:
        inside = [l for l in links if _inside(l[0], w)]
        counts = id_counts(inside)
        sw = [s for s in all_switches if _inside(s['time'], w)]
        tracked = [l for l in inside if l[3] is not None]
        doubted = sum(1 for l in tracked if not l[4])
        rows.append({
            'id': w['id'], 'kind': w.get('kind'), 'start': w['start'], 'end': w['end'],
            'observations': len(inside),
            'idtp': counts['idtp'], 'idfp': counts['idfp'], 'idfn': counts['idfn'],
            'idf1': idf1(counts),
            'mapping': counts['mapping'],
            'switches': len(sw), 'silent_swaps': sum(1 for s in sw if s['silent']),
            'flagged_switches': sum(1 for s in sw if not s['silent']),
            'ambiguous_share': (doubted / len(tracked)) if tracked else None,
            'switch_detail': sw,
        })
    outside = [l for l in links if not any(_inside(l[0], w) for w in windows)]
    tracked_outside = [l for l in outside if l[3] is not None]
    outside_switches = [s for s in all_switches if not any(_inside(s['time'], w) for w in windows)]
    whole = id_counts(links)
    return {
        'windows': rows,
        'whole': {k: whole[k] for k in ('idtp', 'idfp', 'idfn')},
        'outside': {
            'switches': len(outside_switches),
            'silent_swaps': sum(1 for s in outside_switches if s['silent']),
            'tracked': len(tracked_outside),
            'ambiguous': sum(1 for l in tracked_outside if not l[4]),
        },
    }


def summarize(takes, idf1_gate, alpha=0.05):
    """Pooled E2 numbers and the gate, over score_take results.

    A silent swap anywhere fails the gate. The bound is per crossing: how many
    of the n crossings held a silent swap. Pooled IDF1 sums the windows' counts;
    whole-take IDF1, one mapping per take, is reported beside it.
    """
    rows = [r for take in takes for r in take['windows']]
    n = len(rows)
    failed = sum(1 for r in rows if r['silent_swaps'] > 0)
    pooled = {k: sum(r[k] for r in rows) for k in ('idtp', 'idfp', 'idfn')}
    whole = {k: sum(take['whole'][k] for take in takes) for k in ('idtp', 'idfp', 'idfn')}
    outside_silent = sum(take['outside']['silent_swaps'] for take in takes)
    tracked = sum(take['outside']['tracked'] for take in takes)
    ambiguous = sum(take['outside']['ambiguous'] for take in takes)
    pooled_idf1 = idf1(pooled)
    silent_zero = failed == 0 and outside_silent == 0
    idf1_met = pooled_idf1 is not None and pooled_idf1 >= idf1_gate
    return {
        'crossings': n,
        'crossings_with_silent_swap': failed,
        'silent_swaps': sum(r['silent_swaps'] for r in rows),
        'flagged_switches': sum(r['flagged_switches'] for r in rows),
        'silent_swap_rate_upper_95': upper_bound(failed, n, alpha),
        'pooled': dict(pooled, idf1=pooled_idf1),
        'whole_take': dict(whole, idf1=idf1(whole)),
        'switches_outside_crossings': sum(take['outside']['switches'] for take in takes),
        'silent_swaps_outside_crossings': outside_silent,
        'ambiguous_share_outside_crossings': (ambiguous / tracked) if tracked else None,
        'gate': {
            'silent_swaps_zero': silent_zero,
            'idf1_at_least': idf1_gate,
            'idf1_met': idf1_met,
            'passed': n > 0 and silent_zero and idf1_met,
        },
    }


def score_windows(links, windows, idf1_gate, alpha=0.05):
    """One take: per-window rows and the pooled numbers."""
    take = score_take(links, windows)
    return dict(summarize([take], idf1_gate, alpha), windows=take['windows'])

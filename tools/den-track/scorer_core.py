"""E2 scoring on labelled links, independent of record field names.

A link is one observation as both sides saw it:
    (time, obs_id, person, track, confident)
person is the owner's label ('A', 'B', ...) or None for "not a person";
track is the track the tracker put the observation on, or None; confident
is True when that track's record said `confident`, False when it said
`ambiguous`, None when no track took the observation.
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

    Returns [{time, obs_id, track, from, to, confident, silent}] in time
    order. A switch is silent when the track's record that took the new
    person's observation said `confident`.
    """
    last = {}
    out = []
    for time, obs_id, person, track, confident in sorted(links, key=lambda l: (l[0], str(l[3]), l[1])):
        if person is None or track is None:
            continue
        before = last.get(track)
        if before is not None and before != person:
            out.append({'time': time, 'obs_id': obs_id, 'track': track, 'from': before, 'to': person,
                        'confident': confident, 'silent': confident is True})
        last[track] = person
    return out


def score_take(links, windows):
    """Per-window E2 numbers for one take.

    windows: [{'id', 'kind', 'start', 'end'}]. Switches are found on the whole
    take (so a track's person before a window is known) and counted in the
    window they fall in; switches outside every window are counted too.
    IDF1 uses each window's own best mapping."""
    all_switches = switches(links)
    rows = []
    for w in windows:
        inside = [l for l in links if w['start'] <= l[0] <= w['end']]
        counts = id_counts(inside)
        sw = [s for s in all_switches if w['start'] <= s['time'] <= w['end']]
        taken = [l for l in inside if l[3] is not None]
        rows.append({
            'id': w['id'], 'kind': w.get('kind'), 'start': w['start'], 'end': w['end'],
            'observations': len(inside),
            'idtp': counts['idtp'], 'idfp': counts['idfp'], 'idfn': counts['idfn'],
            'idf1': idf1(counts),
            'switches': len(sw), 'silent_swaps': sum(1 for s in sw if s['silent']),
            'reported_switches': sum(1 for s in sw if not s['silent']),
            'ambiguous_share': (sum(1 for l in taken if l[4] is False) / len(taken)) if taken else None,
            'switch_detail': sw,
        })
    outside = [s for s in all_switches if not any(w['start'] <= s['time'] <= w['end'] for w in windows)]
    taken = [l for l in links if l[3] is not None]
    return {
        'windows': rows,
        'switches_outside_windows': len(outside),
        'silent_swaps_outside_windows': sum(1 for s in outside if s['silent']),
        'outside_detail': outside,
        'ambiguous_share': (sum(1 for l in taken if l[4] is False) / len(taken)) if taken else None,
    }


def pool(takes, idf1_gate, min_windows, alpha=0.05):
    """The gate over every window of every take.

    takes: [(take_id, score_take(...) result)]. A window with a silent swap
    is a failed trial; the bound is the one-sided (1 - alpha) upper bound on
    the failure rate. The gate also needs no silent swap outside the windows,
    pooled IDF1 at or above the gate, and at least `min_windows` windows."""
    rows = [dict(r, take=take_id) for take_id, res in takes for r in res['windows']]
    pooled = {k: sum(r[k] for r in rows) for k in ('idtp', 'idfp', 'idfn')}
    n = len(rows)
    failed = sum(1 for r in rows if r['silent_swaps'] > 0)
    outside_silent = sum(res['silent_swaps_outside_windows'] for _, res in takes)
    pooled_idf1 = idf1(pooled)
    taken_obs = sum(r['idtp'] + r['idfp'] for r in rows)
    amb = sum((r['ambiguous_share'] or 0) * (r['idtp'] + r['idfp']) for r in rows)
    gate = {
        'silent_swaps_zero': failed == 0 and outside_silent == 0,
        'idf1_at_least': idf1_gate,
        'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate,
        'windows_at_least': min_windows,
        'windows_met': n >= min_windows,
    }
    gate['passed'] = gate['silent_swaps_zero'] and gate['idf1_met'] and gate['windows_met']
    return {
        'windows': rows,
        'crossings': n,
        'crossings_with_silent_swap': failed,
        'silent_swaps': sum(r['silent_swaps'] for r in rows),
        'reported_switches': sum(r['reported_switches'] for r in rows),
        'silent_swaps_outside_windows': outside_silent,
        'switches_outside_windows': sum(res['switches_outside_windows'] for _, res in takes),
        'silent_swap_rate_upper_95': upper_bound(failed, n, alpha),
        'alpha': alpha,
        'pooled': dict(pooled, idf1=pooled_idf1),
        'ambiguous_share': (amb / taken_obs) if taken_obs else None,
        'gate': gate,
    }

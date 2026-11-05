"""E2 scoring on labelled links, independent of record field names.

A link is one observation as both sides saw it:
    (time, obs_id, person, track, confidence)
person is the owner's label ('A', 'B', ...) or None for "not a person";
track is the track the tracker put the observation on, or None; confidence is
what that track reported in the same record.
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


def switches(links, confident_at):
    """Every time a track moves from one labelled person to another.

    Returns [{time, obs_id, track, from, to, confidence, silent}] in time
    order. A switch is silent when the track's confidence in that record is
    at or above `confident_at`.
    """
    last = {}
    out = []
    for time, obs_id, person, track, conf in sorted(links, key=lambda l: (l[0], str(l[3]), l[1])):
        if person is None or track is None:
            continue
        before = last.get(track)
        if before is not None and before != person:
            silent = conf is not None and conf >= confident_at
            out.append({'time': time, 'obs_id': obs_id, 'track': track, 'from': before, 'to': person,
                         'confidence': conf, 'silent': silent})
        last[track] = person
    return out


def score_windows(links, windows, confident_at, idf1_gate, alpha=0.05):
    """Per-window and pooled E2 numbers.

    windows: [{'id', 'kind', 'start', 'end'}]. Switches are found on the whole
    take (so a track's person before a window is known) and counted in the
    window they fall in. IDF1 uses each window's own best mapping, and the
    pooled IDF1 sums the windows' IDTP, IDFP and IDFN.
    """
    all_switches = switches(links, confident_at)
    rows = []
    pooled = {'idtp': 0, 'idfp': 0, 'idfn': 0}
    for w in windows:
        inside = [l for l in links if w['start'] <= l[0] <= w['end']]
        counts = id_counts(inside)
        for k in pooled:
            pooled[k] += counts[k]
        sw = [s for s in all_switches if w['start'] <= s['time'] <= w['end']]
        conf = [l[4] for l in inside if l[3] is not None and l[4] is not None]
        low = sum(1 for c in conf if c < confident_at)
        rows.append({
            'id': w['id'], 'kind': w.get('kind'), 'start': w['start'], 'end': w['end'],
            'observations': len(inside),
            'idtp': counts['idtp'], 'idfp': counts['idfp'], 'idfn': counts['idfn'],
            'idf1': idf1(counts),
            'switches': len(sw), 'silent_swaps': sum(1 for s in sw if s['silent']),
            'flagged_switches': sum(1 for s in sw if not s['silent']),
            'low_confidence_share': (low / len(conf)) if conf else None,
            'switch_detail': sw,
        })
    n = len(rows)
    failed = sum(1 for r in rows if r['silent_swaps'] > 0)
    pooled_idf1 = idf1(pooled)
    outside = [s for s in all_switches if not any(w['start'] <= s['time'] <= w['end'] for w in windows)]
    return {
        'windows': rows,
        'crossings': n,
        'crossings_with_silent_swap': failed,
        'silent_swaps': sum(r['silent_swaps'] for r in rows),
        'flagged_switches': sum(r['flagged_switches'] for r in rows),
        'silent_swap_rate_upper_95': upper_bound(failed, n, alpha),
        'pooled': dict(pooled, idf1=pooled_idf1),
        'switches_outside_windows': len(outside),
        'silent_switches_outside_windows': sum(1 for s in outside if s['silent']),
        'gate': {
            'silent_swaps_zero': failed == 0,
            'idf1_at_least': idf1_gate,
            'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate,
            'passed': n > 0 and failed == 0 and pooled_idf1 is not None and pooled_idf1 >= idf1_gate,
        },
    }

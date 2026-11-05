"""E2 scoring on labelled links, independent of record field names.

A link is one observation as both sides saw it:
    (time, obs_id, person, track, confident)
person is the owner's label ('A', 'B', ...) or None for "not a person"; track
is the track the tracker put the observation on, or None; confident is whether
that track's record reported confidence (association.status is `confident`).
An observation the owner cannot tell is no link. It is passed apart, as a link
whose person is UNSURE, and only its track and status are used, by switches().
"""
from scorer_math import best_matching, upper_bound

UNSURE = '?'


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


def switches(links, unsure=(), unresolved_is_silent=False):
    """Every time a track moves from one labelled person to another.

    Returns [{time, obs_id, track, from, to, silent, unresolved}] in time order. A
    switch is silent when the track's record reported confidence at that link.
    Across cannot-tell observations the move happened somewhere in the span, so it
    is judged on every record from the first of them to the new person's link:
    silent if all reported confidence, flagged if none did, and unresolved if some
    did, which is not silent unless unresolved_is_silent says to read it so.
    """
    last, span = {}, {}
    out = []
    for time, obs_id, person, track, confident in sorted(list(links) + list(unsure), key=lambda l: (l[0], str(l[3]), l[1])):
        if person is None or track is None:
            continue
        if person == UNSURE:
            span.setdefault(track, []).append(confident)
            continue
        seen = span.pop(track, []) + [confident]
        before = last.get(track)
        if before is not None and before != person:
            sure, doubted = all(s is True for s in seen), any(s is not True for s in seen)
            unresolved = doubted and any(s is True for s in seen)
            out.append({'time': time, 'obs_id': obs_id, 'track': track, 'from': before, 'to': person,
                        'silent': sure or (unresolved and unresolved_is_silent), 'unresolved': unresolved})
        last[track] = person
    return out


def _inside(time, window):
    return window['start'] <= time <= window['end']


def _why_no_trial(inside, window, min_people):
    """Why a window cannot show that identities held, or None if it can."""
    named = [l for l in inside if l[2] is not None]
    if not named or len({l[2] for l in named}) < min_people:
        return 'fewer than %d people scored' % min_people
    mark = window.get('mark')
    if mark and min(l[0] for l in named) >= mark[0]:
        return 'nobody scored before the mark'
    if mark and max(l[0] for l in named) <= mark[1]:
        return 'nobody scored after the mark'
    return None


def score_take(links, windows, min_people=2, unsure=(), unresolved_is_silent=False):
    """One take's crossings. windows: [{'id', 'kind', 'start', 'end'}], and 'mark',
    the owner's [t0, t1] inside the padded window, when there is one.

    Switches are found on the whole take (so a track's person before a window
    is known) and counted in the window they fall in. IDF1 uses each window's
    own best mapping. A window is a trial (`admitted`) only if it holds a silent
    swap, or scored observations of at least min_people people, on both sides of
    the mark: one that saw one person, nobody, or nothing after the contact (a
    take cut short) cannot show that identities held.
    """
    all_switches = switches(links, unsure, unresolved_is_silent)
    rows = []
    for w in windows:
        inside = [l for l in links if _inside(l[0], w)]
        counts = id_counts(inside)
        sw = [s for s in all_switches if _inside(s['time'], w)]
        tracked = [l for l in inside if l[3] is not None]
        doubted = sum(1 for l in tracked if not l[4])
        silent = sum(1 for s in sw if s['silent'])
        why = None if silent else _why_no_trial(inside, w, min_people)
        rows.append({
            'id': w['id'], 'kind': w.get('kind'), 'start': w['start'], 'end': w['end'],
            'observations': len(inside),
            'people_scored': sorted({l[2] for l in inside if l[2] is not None}),
            'admitted': why is None,
            'excluded_because': why,
            'idtp': counts['idtp'], 'idfp': counts['idfp'], 'idfn': counts['idfn'],
            'idf1': idf1(counts),
            'mapping': counts['mapping'],
            'switches': len(sw), 'silent_swaps': silent,
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
        'unresolved_switches': sum(1 for s in all_switches if s['unresolved']),
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
    of the n admitted crossings held a silent swap. Pooled IDF1 sums the admitted
    windows' counts; whole-take IDF1, one mapping per take, is reported beside it.
    A window that is not admitted counts for nothing and is listed as excluded.
    """
    rows = [r for take in takes for r in take['windows']]
    admitted = [r for r in rows if r['admitted']]
    n = len(admitted)
    failed = sum(1 for r in admitted if r['silent_swaps'] > 0)
    pooled = {k: sum(r[k] for r in admitted) for k in ('idtp', 'idfp', 'idfn')}
    whole = {k: sum(take['whole'][k] for take in takes) for k in ('idtp', 'idfp', 'idfn')}
    outside_silent = sum(take['outside']['silent_swaps'] for take in takes)
    tracked = sum(take['outside']['tracked'] for take in takes)
    ambiguous = sum(take['outside']['ambiguous'] for take in takes)
    pooled_idf1 = idf1(pooled)
    silent_zero = failed == 0 and outside_silent == 0
    idf1_met = pooled_idf1 is not None and pooled_idf1 >= idf1_gate
    return {
        'crossings': n,
        'crossings_marked': len(rows),
        'crossings_excluded': [{'take': r.get('take'), 'id': r['id'], 'people_scored': r['people_scored'],
                                'because': r['excluded_because']} for r in rows if not r['admitted']],
        'crossings_with_silent_swap': failed,
        'silent_swaps': sum(r['silent_swaps'] for r in rows),
        'flagged_switches': sum(r['flagged_switches'] for r in rows),
        'unresolved_switches': sum(take['unresolved_switches'] for take in takes),
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


def score_windows(links, windows, idf1_gate, alpha=0.05, min_people=2):
    """One take: per-window rows and the pooled numbers."""
    take = score_take(links, windows, min_people)
    return dict(summarize([take], idf1_gate, alpha), windows=take['windows'])

"""E2 scoring on labelled links, independent of record field names.

A link is one observation as both sides saw it:
    (time, obs_id, person, track, confident)
person is the owner's label ('A', 'B', ...) or None for "not a person"; track
is the track the tracker put the observation on, or None; confident is whether
that track's record reported confidence (association.status is `confident`).
An observation the owner cannot tell is no link. It is passed apart, as a link
whose person is UNSURE, and only its track and status are used, by switches().
"""
from bisect import bisect_left
import math

from scorer_math import best_matching, upper_bound

UNSURE = '?'
TRIAL_SIDES = ('take', 'window')


def id_counts(links):
    """IDTP, IDFP, IDFN over links, with the best one-to-one mapping of
    people to tracks (Ristani et al. 2016, at observation level).

    A person is one instance per frame, so a second box of one person adds no truth:
    a track that took it claims someone who is not there, a false positive and no more.
    """
    people = sorted({l[2] for l in links if l[2] is not None})
    tracks = sorted({l[3] for l in links if l[3] is not None})
    co = {}
    for time, _, person, track, _ in links:
        if person is not None and track is not None:
            co.setdefault((person, track), set()).add(time)
    weights = [[len(co.get((p, t), ())) for t in tracks] for p in people]
    idtp, pairs = best_matching(weights)
    n_truth = len({(l[0], l[2]) for l in links if l[2] is not None})
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


def trial_problem(rule):
    """Why this scoring.trial_rule cannot be used, or None."""
    def positive(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0

    if not isinstance(rule, dict):
        return 'scoring.trial_rule must say sides, min_scored, contact_reach_m and unseen_s, not %r' % (rule,)
    if rule.get('sides') not in TRIAL_SIDES:
        return 'scoring.trial_rule.sides must be %s, not %r' % (' or '.join(TRIAL_SIDES), rule.get('sides'))
    if type(rule.get('min_scored')) is not int or rule['min_scored'] < 1:
        return 'scoring.trial_rule.min_scored must be a whole number above 0, not %r' % (rule.get('min_scored'),)
    for name in ('contact_reach_m', 'unseen_s'):
        if not positive(rule.get(name)):
            return 'scoring.trial_rule.%s must be a number above 0, not %r' % (name, rule.get(name))
    return None


def _sighting_counts(links, window, whole_take):
    """person -> how many frames saw them in the window, and before and after the mark.

    Frames, not boxes: two boxes of one person in a frame are one sighting. The sides reach
    over the whole take if whole_take, and stop at the window if not.
    """
    t0, t1 = window['mark']
    counts = {}
    for time, person in sorted({(l[0], l[2]) for l in links if l[2] is not None}):
        c = counts.setdefault(person, {'window': 0, 'before': 0, 'after': 0})
        inside = _inside(time, window)
        c['window'] += inside
        if whole_take or inside:
            if time < t0:
                c['before'] += 1
            elif time > t1:
                c['after'] += 1
    return counts


def _at(path, time):
    """Where a person was at a time, in a straight line between sightings; None outside them."""
    i = bisect_left(path, (time,))
    if i < len(path) and path[i][0] == time:
        return path[i][1:]
    if i == 0 or i == len(path):
        return None
    (t0, x0, y0), (t1, x1, y1) = path[i - 1], path[i]
    share = (time - t0) / (t1 - t0)
    return (x0 + share * (x1 - x0), y0 + share * (y1 - y0))


def _closest(links, people, window, positions):
    """How near any two of these people came inside the window, or None if that cannot be read."""
    paths = {p: sorted((l[0],) + tuple(positions[l[1]]) for l in links if l[2] == p and l[1] in positions) for p in people}
    span = window['end'] - window['start']
    times = {l[0] for l in links if l[2] in paths and _inside(l[0], window)}
    times |= {window['start'] + span * k / 40 for k in range(41)}       # so a meeting while both are hidden is read too
    best = None
    for a in people:
        for b in people:
            if a < b:
                for time in times:
                    pa, pb = _at(paths[a], time), _at(paths[b], time)
                    if pa is not None and pb is not None:
                        d = math.dist(pa, pb)
                        best = d if best is None or d < best else best
    return best


def _longest_unseen(links, person, window):
    """The longest this person went unseen at some time during the mark, or 0 if no two sightings reach across any of it."""
    t0, t1 = window['mark']
    times = sorted({l[0] for l in links if l[2] == person})
    return max((b - a for a, b in zip(times, times[1:]) if a <= t1 and b >= t0), default=0.0)


def _door_gap(links, left, came, mark):
    """In a door swap, the shortest time from the last sighting of someone leaving to the first of someone coming in."""
    last = max(l[0] for l in links if l[2] in left and l[0] < mark[0])
    first = min(l[0] for l in links if l[2] in came and l[0] > mark[1])
    return first - last


def _coast_note(limit):
    return 'a track coasts for %g s (tracker.max_coast_s), so no track was left to swap' % limit


def _read_past_window(links, window, followed, least):
    """{(time, person)}: each followed person's nearest `least` sightings on each side of the mark, those outside the window.

    A person is followed on sightings that may lie anywhere in the take, and those sightings are where the crossing's
    outcome shows, so the window reads them. If the sides stop at the window, they are all inside it and this is empty.
    """
    past = set()
    for person in followed:
        times = sorted({l[0] for l in links if l[2] == person})
        before = [t for t in times if t < window['mark'][0]]
        after = [t for t in times if t > window['mark'][1]]
        past |= {(t, person) for t in before[-least:] + after[:least] if not _inside(t, window)}
    return past


def _trial(links, window, min_people, rule, positions, max_unseen_s=None):
    """Why a window cannot show that identities held (or None if it can), and what was seen in it.

    Past max_unseen_s without a sighting a person's track has ended, whatever the sightings around it show: they come
    back on a new track, so nothing was tested, and that is the same whether the sides reach over the take or not.
    """
    people = sorted({l[2] for l in links if l[2] is not None and _inside(l[0], window)})
    seen = {'followed': [], 'closest_m': None, 'scored_sides': {}, 'longest_unseen_s': {}, 'past_window': set()}
    limit = float('inf') if max_unseen_s is None else max_unseen_s
    if len(people) < min_people:
        return 'fewer than %d people scored' % min_people, seen
    if rule is None or not window.get('mark'):
        return None, seen
    least = rule['min_scored']
    kind = window.get('kind')
    in_window = _sighting_counts(links, window, False)
    if kind == 'door':
        # One leaves and the other comes in, so nobody is on both sides, and the sides stop at the window.
        left = [p for p, c in in_window.items() if c['before'] >= least and c['after'] == 0]
        came = [p for p, c in in_window.items() if c['after'] >= least and c['before'] == 0]
        seen['scored_sides'] = in_window
        if not left or not came:
            return 'no door swap: nobody with %d sightings before the mark and none after it, and another the other way round' % least, seen
        gap = _door_gap([l for l in links if _inside(l[0], window)], left, came, window['mark'])
        if gap > limit:
            return 'nobody was seen at the door for %.1f s, and %s' % (gap, _coast_note(limit)), seen
        return None, seen
    counts = in_window if rule['sides'] == 'window' else _sighting_counts(links, window, True)
    seen['scored_sides'] = counts
    enough = sorted(p for p, c in counts.items() if min(c.values()) >= least)
    seen['longest_unseen_s'] = {p: _longest_unseen(links, p, window) for p in enough}
    seen['followed'] = [p for p in enough if seen['longest_unseen_s'][p] <= limit]
    seen['past_window'] = _read_past_window(links, window, seen['followed'], least)
    if len(seen['followed']) < min_people:
        lost = [p for p in enough if p not in seen['followed']]
        return ('fewer than %d people followed through the mark, with %d sightings each in the window and on each side of '
                'the mark (%s)%s' % (min_people, least, '; '.join('%s %d in the window, %d before, %d after'
                                                                   % (p, c['window'], c['before'], c['after'])
                                                                   for p, c in sorted(counts.items())),
                                     '; %s unseen for longer across it: %s' % (' and '.join(lost), _coast_note(limit)) if lost else '')), seen
    if kind == 'occlusion':
        if max(seen['longest_unseen_s'][p] for p in seen['followed']) < rule['unseen_s']:
            return 'nobody was out of sight for %g s during the mark' % rule['unseen_s'], seen
        return None, seen
    seen['closest_m'] = _closest(links, seen['followed'], window, positions or {})
    if seen['closest_m'] is None or seen['closest_m'] > rule['contact_reach_m']:
        return 'they never came within %g m of each other in the window (closest: %s)' % (
            rule['contact_reach_m'], 'unknown' if seen['closest_m'] is None else '%.1f m' % seen['closest_m']), seen
    return None, seen


def score_take(links, windows, min_people=2, unsure=(), unresolved_is_silent=False, trial=None, positions=None, max_unseen_s=None):
    """One take's crossings. windows: [{'id', 'kind', 'start', 'end'}], and 'mark',
    the owner's [t0, t1] inside the padded window, when there is one.

    Switches are found on the whole take (so a track's person before a window
    is known) and counted in the window they fall in. IDF1 uses each window's
    own best mapping. Both also read each followed person's nearest sightings
    outside the window (_read_past_window). A window is a trial (`admitted`) only
    if it holds or reads a silent swap, or holds at least min_people people, and
    under `trial` (scoring.trial_rule) those
    people can be seen to go through what the kind of crossing claims, with
    `positions` ({obs_id: (x, y)}) saying where they were, and none unseen across
    the mark for longer than max_unseen_s, the tracker's coast. See METHOD.md.
    """
    all_switches = switches(links, unsure, unresolved_is_silent)
    rows, read_past = [], set()
    for w in windows:
        inside = [l for l in links if _inside(l[0], w)]
        why, seen = _trial(links, w, min_people, trial, positions, max_unseen_s)
        past = seen['past_window']
        read_past |= past
        counts = id_counts(inside + [l for l in links if (l[0], l[2]) in past])
        sw = [s for s in all_switches if _inside(s['time'], w) or (s['time'], s['to']) in past]
        tracked = [l for l in inside if l[3] is not None]
        doubted = sum(1 for l in tracked if not l[4])
        silent = sum(1 for s in sw if s['silent'])
        why = None if silent else why
        rows.append({
            'id': w['id'], 'kind': w.get('kind'), 'start': w['start'], 'end': w['end'],
            'observations': len(inside),
            'people_scored': sorted({l[2] for l in inside if l[2] is not None}),
            'admitted': why is None,
            'excluded_because': why,
            'followed': seen['followed'],
            'closest_m': seen['closest_m'],
            'scored_sides': seen['scored_sides'],
            'longest_unseen_s': seen['longest_unseen_s'],
            'idtp': counts['idtp'], 'idfp': counts['idfp'], 'idfn': counts['idfn'],
            'idf1': idf1(counts),
            'mapping': id_counts(inside)['mapping'],    # what rung 5 reads: the tracks in the window
            'switches': len(sw), 'silent_swaps': silent,
            'flagged_switches': sum(1 for s in sw if not s['silent']),
            'ambiguous_share': (doubted / len(tracked)) if tracked else None,
            'switch_detail': sw,
        })
    outside = [l for l in links if not any(_inside(l[0], w) for w in windows)]
    tracked_outside = [l for l in outside if l[3] is not None]
    outside_switches = [s for s in all_switches if not any(_inside(s['time'], w) for w in windows)
                        and (s['time'], s['to']) not in read_past]
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

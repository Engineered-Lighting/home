"""Per-activity scoring of JEV answers against labelled room intervals.

Shared by the regression-set CLI (lab_regression.py, an operator tool) and the
controller's automatic results after a recording. Local only: nothing here
reads or writes a model input, and nothing here is sent anywhere.

The rules are reasoning.score_intervals' (the findings scorer), made per
activity and per arm, with the breakdown the regression gate needs:

* time is session-relative seconds; an answer counts from its available_at
  (live delivery) to min(end, valid_until), and at any instant the shown
  answer is the one with the latest start (then latest available_at);
* an answer of insufficient_evidence (or an abstaining answer, activities [])
  is an abstention; labelled time with no current answer is no_answer;
* exact@coverage is correct answered time over answered time; coverage is
  answered time over labelled time; false-confident seconds are answered,
  wrong seconds at a reported confidence >= 0.8;
* the confusion row of a labelled activity holds seconds per shown answer
  (activities, 'abstain' and 'no_answer'); an answered activity the interval
  does not accept is a false positive of that activity in that interval;
* time-to-first-correct is measured from each label's start (a label
  boundary) to the first instant the shown answer is correct.

The empty room is scored under its own key, empty_room. The stored label
vocabulary has no such activity: the owner labels it 'other' (the room holds
no activity), so an 'other' answer is correct there and any specific activity
answered there is a false positive.
"""
import json
import math
import re

SCHEMA = 'lab-results-1'
EVENT_KIND = 'results.auto_score'
EMPTY_ROOM = 'empty_room'
ABSTAIN, NO_ANSWER = 'abstain', 'no_answer'
ABSTAIN_ACTIVITIES = frozenset({'insufficient_evidence', 'unknown'})
NOT_TRUTH = frozenset({'insufficient_evidence', 'unknown', 'unlabeled', 'unlabelled', 'Unclear', ''})
FALSE_CONFIDENT_P = 0.8  # reasoning.score_intervals' threshold
# Answers a labelled key accepts; any other key accepts only itself.
ACCEPT = {EMPTY_ROOM: ('other',)}
# The stored label an empty-room interval carries.
STORED_LABEL = {EMPTY_ROOM: 'other'}
EMPTY_ROOM_TEXT = re.compile(r'\b(empty|out of view|nobody|no one|vacant)\b', re.I)
# The Lab's bookmark quick tags (app/src/home-perception-lab.jsx BOOKMARK_TAGS).
BOOKMARK_TAGS = {'reading': 'reading', 'phone': 'phone_use', 'computer': 'computer_work', 'resting': 'resting',
                 'other': 'other'}
MIN_DRAFT_S = 2.0
MAX_DRAFT_LABELS = 200
# Automatic results: sessions that ended at least SETTLE_S ago (late answers
# land first) and at most WINDOW_S ago; a few per pass.
SETTLE_S = 30.0
WINDOW_S = 6 * 3600.0
PER_PASS = 1


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def bookmark_activity(text):
    """The activity a bookmark's tag or text names, or None. The quick tags
    first, then the Lab UI's cue rules (guessActivity), with the empty room
    and leaving added."""
    x = str(text or '').strip().lower()
    if x in BOOKMARK_TAGS:
        return BOOKMARK_TAGS[x]
    if EMPTY_ROOM_TEXT.search(x):
        return EMPTY_ROOM
    if 'closed' in x:
        return 'other'
    if re.search(r'read|book', x):
        return 'reading'
    if 'phone' in x:
        return 'phone_use'
    if re.search(r'computer|desk|\bpc\b|laptop', x):
        return 'computer_work'
    if re.search(r'rest|sofa|couch|\btv\b', x):
        return 'resting'
    if re.search(r'leav|walk|getting up|transition', x):
        return 'transition'
    if re.search(r'\bother\b', x):
        return 'other'
    return None


def scoring_key(label, text=''):
    """The key a stored label is scored under: an 'other' label whose note says
    the room is empty is the empty room."""
    if label == 'other' and EMPTY_ROOM_TEXT.search(str(text or '')):
        return EMPTY_ROOM
    return label


def draft_labels(bookmarks, duration_s):
    """Unreviewed draft labels from a session's bookmarks: each bookmark's tag
    or text sets the activity from its time until the next bookmark (the last
    until the recording ends). A bookmark that names no activity ends the
    previous draft and starts none. bookmarks: [(at_s, text, event_id)]."""
    marks = sorted((b for b in bookmarks if finite(b[0]) and b[0] >= 0), key=lambda b: (b[0], str(b[2])))
    result = []
    for i, (at, text, event_id) in enumerate(marks):
        activity = bookmark_activity(text)
        end = marks[i+1][0] if i+1 < len(marks) else duration_s
        if activity is None or not finite(end) or end - at < MIN_DRAFT_S:
            continue
        result.append({'start': round(at, 3), 'end': round(end, 3), 'activity': activity,
                       'label': STORED_LABEL.get(activity, activity), 'reviewed': False, 'provenance': 'bookmark_draft',
                       'source': 'bookmark:%s' % event_id, 'text': str(text or '')[:200]})
    return result[:MAX_DRAFT_LABELS]


def spans(cutoffs, *, max_gap_s=15.0, tail_s=15.0):
    """The time a run's requests speak for: each request's cutoff until the next
    (when within max_gap_s) or for tail_s; merged, sorted [(a, b)]."""
    points = sorted(c for c in cutoffs if finite(c))
    raw = [(c, min(points[i+1], c + max_gap_s) if i+1 < len(points) and points[i+1] - c <= max_gap_s else c + tail_s)
           for i, c in enumerate(points)]
    return merge(raw)


def merge(intervals):
    result = []
    for a, b in sorted((a, b) for a, b in intervals if b > a):
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return result


def intersect(first, second):
    """Intersection of two sorted span lists; None means everywhere."""
    if first is None:
        return second
    if second is None:
        return first
    result, i, j = [], 0, 0
    while i < len(first) and j < len(second):
        a, b = max(first[i][0], second[j][0]), min(first[i][1], second[j][1])
        if b > a:
            result.append((a, b))
        if first[i][1] < second[j][1]:
            i += 1
        else:
            j += 1
    return result


def prediction(value):
    """A scorable answer from a recorded typed prediction, or None."""
    if not isinstance(value, dict) or value.get('mode') == 'diagnostic' or value.get('scored') is False:
        return None
    if not all(finite(value.get(k)) for k in ('start', 'end', 'available_at', 'valid_until')):
        return None
    if value['end'] <= value['start'] or value['valid_until'] < value['start']:
        return None
    shown = [a for a in (value.get('activities') or []) if a not in ABSTAIN_ACTIVITIES]
    probabilities = value.get('probabilities')
    if not isinstance(probabilities, dict) or not all(finite(p) and 0 <= p <= 1 for p in probabilities.values()):
        probabilities = None
    return {'start': value['start'], 'end': value['end'], 'available_at': value['available_at'],
            'valid_until': value['valid_until'], 'activity': shown[0] if len(shown) == 1 else '+'.join(sorted(shown)) or None,
            'confidence': value.get('confidence') if finite(value.get('confidence')) else None,
            'probabilities': probabilities}


def _row():
    return {'labelled_s': 0.0, 'covered_s': 0.0, 'correct_s': 0.0, 'abstain_s': 0.0, 'no_answer_s': 0.0,
            'false_confident_s': 0.0, 'confusion': {}, 'ttfc_s': [], 'missed_boundaries': 0, 'boundaries': 0,
            'sessions': []}


def truth(labels):
    """The labels accumulate() scores: finite, non-empty intervals of a truth key."""
    return [l for l in labels if finite(l.get('start')) and finite(l.get('end')) and l['end'] > l['start']
            and l.get('activity') not in NOT_TRUTH]


def segments(labels, predictions, *, support=None):
    """The elementary segments of one session and arm, in time order:
    (start, end, truth_key, shown, confidence, covering_label_indexes) for each
    span of labelled time between consecutive label or prediction boundaries,
    where shown is the answered activity, ABSTAIN or NO_ANSWER and the indexes
    refer to truth(labels). Arguments as accumulate()."""
    labels = truth(labels)
    predictions = [p for p in predictions if p]
    points = {x for l in labels for x in (l['start'], l['end'])}
    points |= {x for p in predictions for x in (p['start'], p['end'], p['available_at'], p['valid_until'])}
    if support is not None:
        points |= {x for s in support for x in s}
    points = sorted(points)
    inside = lambda t: support is None or any(a <= t < b for a, b in support)
    for start, end in zip(points, points[1:]):
        if end <= start:
            continue
        mid = (start + end) / 2
        covering = [i for i, l in enumerate(labels) if l['start'] <= mid < l['end']]
        if not covering or not inside(mid):
            continue
        key = '+'.join(sorted({labels[i]['activity'] for i in covering}))
        current = [p for p in predictions if p['start'] <= mid < min(p['end'], p['valid_until']) and p['available_at'] <= mid]
        if not current:
            yield start, end, key, NO_ANSWER, None, covering
            continue
        shown = max(current, key=lambda p: (p['start'], p['available_at']))
        yield start, end, key, shown['activity'] or ABSTAIN, shown['confidence'], covering


def accumulate(labels, predictions, *, support=None, accept=None, session_id=None, rows=None):
    """Add one session and arm to per-activity rows ({key: row}). labels:
    [{'start','end','activity'}] (session seconds); predictions: prediction()
    values; support: sorted spans the comparison covers, None for all time."""
    accept = {**ACCEPT, **(accept or {})}
    rows = {} if rows is None else rows
    labels = truth(labels)
    inside = lambda t: support is None or any(a <= t < b for a, b in support)
    first = {}  # label index -> first correct time
    for start, end, key, shown, confidence, covering in segments(labels, predictions, support=support):
        width = end - start
        row = rows.setdefault(key, _row())
        if session_id is not None and session_id not in row['sessions']:
            row['sessions'].append(session_id)
        row['labelled_s'] += width
        row['confusion'][shown] = row['confusion'].get(shown, 0.0) + width
        if shown == NO_ANSWER:
            row['no_answer_s'] += width
            continue
        if shown == ABSTAIN:
            row['abstain_s'] += width
            continue
        row['covered_s'] += width
        if shown in accept.get(key, (key,)):
            row['correct_s'] += width
            for i in covering:
                first.setdefault(i, start)
        elif confidence is not None and confidence >= FALSE_CONFIDENT_P:
            row['false_confident_s'] += width
    for i, label in enumerate(labels):
        if not inside(label['start']):
            continue  # the boundary itself is outside the compared time
        row = rows.setdefault(label['activity'], _row())
        row['boundaries'] += 1
        if i in first:
            row['ttfc_s'].append(first[i] - label['start'])
        else:
            row['missed_boundaries'] += 1
    return rows


def combine(parts):
    """Sum per-activity rows of several sessions or arms."""
    result = {}
    for rows in parts:
        for key, row in rows.items():
            into = result.setdefault(key, _row())
            for name in ('labelled_s', 'covered_s', 'correct_s', 'abstain_s', 'no_answer_s', 'false_confident_s',
                         'missed_boundaries', 'boundaries'):
                into[name] += row[name]
            into['ttfc_s'] += row['ttfc_s']
            into['sessions'] += [s for s in row['sessions'] if s not in into['sessions']]
            for shown, seconds in row['confusion'].items():
                into['confusion'][shown] = into['confusion'].get(shown, 0.0) + seconds
    return result


def metrics(row, key, accept=None):
    """The reported numbers of one accumulated row."""
    accept = {**ACCEPT, **(accept or {})}
    labelled, covered = row['labelled_s'], row['covered_s']
    ttfc = sorted(row['ttfc_s'])
    wrong = {k: round(v, 3) for k, v in sorted(row['confusion'].items())
             if k not in (ABSTAIN, NO_ANSWER) and k not in accept.get(key, (key,))}
    return {'labelled_s': round(labelled, 3), 'sessions': len(row['sessions']),
            'coverage': round(covered / labelled, 4) if labelled else None,
            'exact_at_coverage': round(row['correct_s'] / covered, 4) if covered else None,
            'abstention': round(row['abstain_s'] / labelled, 4) if labelled else None,
            'no_answer': round(row['no_answer_s'] / labelled, 4) if labelled else None,
            'covered_s': round(covered, 3), 'correct_s': round(row['correct_s'], 3),
            'false_confident_s': round(row['false_confident_s'], 3),
            'false_positive_s': wrong,
            'confusion': {k: round(v, 3) for k, v in sorted(row['confusion'].items())},
            'boundaries': row['boundaries'], 'missed_boundaries': row['missed_boundaries'],
            'ttfc_median_s': round(ttfc[len(ttfc) // 2], 2) if ttfc else None,
            'ttfc_max_s': round(ttfc[-1], 2) if ttfc else None}


def report(rows_by_arm, accept=None):
    """{arm: {key: metrics}} plus each arm's total over all activities."""
    result = {}
    for arm, rows in sorted(rows_by_arm.items()):
        result[arm] = {key: metrics(row, key, accept) for key, row in sorted(rows.items())}
    return result


# ---- Automatic results after a recording (controller maintenance) ----

def _live_outputs(db, sid):
    """{arm: [prediction]} and the set of wires, from recorded reasoning.output
    (what went out live) and the wire of each reasoning.request."""
    wires = {}
    for rid, wire in db.execute("""SELECT json_extract(payload,'$.request_id'),json_extract(payload,'$.wire_version')
                                    FROM events WHERE session_id=? AND kind='reasoning.request'""", (sid,)):
        wires[rid] = wire
    arms = {}
    used = set()
    for (raw,) in db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.output' ORDER BY id", (sid,)):
        value = json.loads(raw)
        if value.get('mode', 'live') != 'live':
            continue
        p = prediction(value.get('prediction'))
        if p is None:
            continue
        arms.setdefault(str(value.get('arm', 'unknown')), []).append(p)
        rid = value.get('request_id')
        # Requests before the facts-only wires recorded no wire_version.
        used.add((wires[rid] or 'pre-v6') if rid in wires else 'unrecorded')
    return arms, sorted(used)


def truth_labels(annotations):
    """Scorable session-clock labels from saved annotations."""
    result = []
    for item in annotations:
        if item.get('deleted') or item.get('time_basis', 'session') != 'session' or item.get('label') in NOT_TRUTH:
            continue
        if not finite(item.get('at_ms')) or not finite(item.get('end_ms')) or item['end_ms'] <= item['at_ms']:
            continue
        result.append({'start': item['at_ms'] / 1000, 'end': item['end_ms'] / 1000,
                       'activity': scoring_key(item['label'], item.get('notes')), 'reviewed': item.get('reviewed') is True})
    return result


def session_results(store, sid):
    """The per-activity results of one finished recording: its live JEV answers
    against its saved session labels, or else against draft labels made from
    its bookmarks. Draft labels are stored only in this local result."""
    session = store.get_session(sid)
    start, end = session.get('started_at'), session.get('ended_at')
    with store.lock:
        rows = store.db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind IN ('bookmark','bookmark.added') ORDER BY id",
                                (sid,)).fetchall()
        outputs, wires = _live_outputs(store.db, sid)
    marks = [(value.get('at_ms'), value.get('label'), i) for i, value in ((i, json.loads(p)) for i, p in rows)]
    duration = end - start if finite(start) and finite(end) and end > start else None
    drafts = draft_labels([(at / 1000 if finite(at) else None, text, i) for at, text, i in marks],
                          duration if duration is not None else max((m[0] / 1000 for m in marks if finite(m[0])), default=0))
    saved = truth_labels(store.list_annotations(sid))
    labels, truth = (saved, 'saved_labels') if saved else (drafts, 'bookmark_drafts') if drafts else ([], 'none')
    rows = {arm: accumulate(labels, predictions, session_id=sid) for arm, predictions in outputs.items()}
    # What each arm showed over the whole recording, labelled or not.
    whole = [{'start': 0.0, 'end': duration, 'activity': 'recording'}] if duration else []
    shown = {arm: {k: round(v, 1) for k, v in sorted(accumulate(whole, predictions).get('recording', _row())['confusion'].items())}
             for arm, predictions in sorted(outputs.items())}
    return {'schema': SCHEMA, 'session_id': sid, 'generated_at': store.clock(), 'truth': truth,
            'reviewed_labels': sum(1 for l in saved if l['reviewed']), 'labels_used': len(labels),
            'draft_labels': drafts, 'wires': wires, 'answers': {arm: len(p) for arm, p in sorted(outputs.items())},
            'arms': report(rows), 'shown_s': shown, 'duration_s': duration, 'local_only': True,
            'note': 'Draft labels come from bookmarks and are unreviewed; they are local evaluation data only.'}


def pending_sessions(store, *, now=None, settle_s=SETTLE_S, window_s=WINDOW_S, limit=PER_PASS):
    """Finished recordings without automatic results, newest first."""
    now = store.clock() if now is None else now
    with store.lock:
        rows = store.db.execute("""SELECT s.id FROM sessions s WHERE s.site_id=? AND s.state IN ('stopped','failed','interrupted')
            AND s.started_at IS NOT NULL AND s.ended_at IS NOT NULL AND s.ended_at<=? AND s.ended_at>=?
            AND NOT EXISTS (SELECT 1 FROM events e WHERE e.session_id=s.id AND e.kind=?)
            AND NOT EXISTS (SELECT 1 FROM session_erasures x WHERE x.session_id=s.id)
            ORDER BY s.ended_at DESC LIMIT ?""", (store.settings.site_id, now - settle_s, now - window_s, EVENT_KIND, limit)).fetchall()
    return [r[0] for r in rows]


def record_results(store, sid):
    """Compute and store one session's automatic results (a local event)."""
    value = session_results(store, sid)
    return store.append_event(sid, EVENT_KIND, value)


def latest_results(store, sid):
    """The stored automatic results of a session, or None."""
    with store.lock:
        row = store.db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                               (sid, EVENT_KIND)).fetchone()
    return {**json.loads(row[1]), 'event_id': row[0]} if row else None

"""Absolute, reasoner-agnostic evaluation of bake-off arms against the labelled room.

Offline operator tool, run on the controller host; not part of the controller
release. Privacy: local only. The ledger is opened read-only (?mode=ro,
lab_regression.open_ro) and never written; the tick files are read; nothing
is sent anywhere and no network connection is opened.

Every bake-off arm writes the common tick record (schema lab-ticks-1): a JSONL
file whose first line is a header ({'schema', 'run', 'arm', 'consumes_qwen',
'calibration', 'classes', 'source'}) and whose other lines are emissions
({'session_id', 'arm' (the live arm a|b), 't', 'available_at',
'probabilities', 'closed_at'?, 'facts_key'?}). Probabilities cover the header
classes (reading, computer_work, phone_use, resting, transition, other, empty,
abstain), finite in [0, 1], summing to 1 +- 0.01; a missing class is 0.
'abstain' is the arm's own abstention mass; 'empty' is a positive empty-room
output that only local evidence may produce.

Held view (applied here, never by a producer, the hold rule of
jev_smoothing.facts_bound): an emission covers [available_at, min(the next
emission's available_at, closed_at, available_at + hold_s)]. Decision at a
threshold tau: let e = p[empty] and c* the most probable of the five S classes
plus 'other'; shown = 'empty' when e is the overall maximum, else c* when
p[c*] >= tau, else abstain. Inside the empty room only 'empty' is correct.
Every labelled held-out and legacy session is scored for every live arm the
run emits for; a (session, arm) with no emission is no answer throughout and
is listed as 'missing', so a run cannot skip a hard session.

Split (schema lab-split-1, frozen before any arm was tuned): a session is
held-out iff it is not in 'dev' and started after frozen_at; everything else
is legacy. Legacy sessions are never reported as held-out: they appear only in
the separate 'legacy' slice, and naming one as held-out refuses (SystemExit).
Calendar days are the session's started_at in the split's site_timezone.

Labels: lab_regression.gather's intervals, from the ledger and the private
manifest (--manifest, default lab_regression.MANIFEST as lab_regression uses;
a missing file is no manifest). Gold = reviewed and owner_reviewed or
owner_statement provenance (a 'provenance' stored on the annotation wins over
the inferred one). A manifest label of provenance owner_statement counts as
reviewed: the owner's own statement is the review, so it needs no
'"reviewed": true'. Silver is everything else, only with --silver. A run that
consumes Qwen never sees qwen_proposed intervals.

Metrics (per run; over held-out sessions, per session, the legacy slice and
each split slice tag): window TP/FN/FP, precision, recall, F1 per S class
over window_s windows (truth = the key covering >= 80 % of the window, the
held answer at the midpoint); macro-F1 over the classes that pass the sample
gate (>= 3 held-out sessions, >= 2 days, >= 480 labelled seconds);
passes_stage1 when all five pass and macro-F1 >= 0.70; Wilson 95 % lower
bounds on precision with one independent trial per 12 s hold; a tau sweep
with coverage at precision >= 0.90; band precision at p >= 0.8; ECE (5
equal-mass bins) and Brier; empty-room safety and detection; time-to-first-
correct; per-session bands. A tick is one second of labelled time, taken from
lab_scoring.segments so seconds match the regression scorer.

Usage:
  python lab_eval.py eval  --ledger DB --split FILE --run NAME=TICKS.jsonl [--run ...] [--manifest FILE]
                           [--tau X | --fit loo|dev] [--silver] [--json] [--window-s 5] [--hold-s 12] [--held-out SID ...]
  python lab_eval.py split --ledger DB --split FILE
"""
import argparse
import bisect
from collections import defaultdict
import datetime
import json
import math
from pathlib import Path
import re
import sys
import zoneinfo

import lab_regression as regression
import lab_scoring as scoring

TICKS_SCHEMA = 'lab-ticks-1'
SPLIT_SCHEMA = 'lab-split-1'
REPORT_SCHEMA = 'lab-eval-report-1'
S_CLASSES = ('reading', 'computer_work', 'phone_use', 'resting', 'transition')
ANSWER_CLASSES = S_CLASSES + ('other',)
CLASSES = ANSWER_CLASSES + ('empty', 'abstain')
HEADER_ARMS = ('A', 'B', 'C', 'D', 'E', 'F', 'reference')
SESSION_ID = re.compile('[0-9a-f]{32}')
TIME_TOLERANCE_S = 1e-6
EMPTY, ABSTAIN = 'empty', 'abstain'
GOLD_PROVENANCES = frozenset({'owner_reviewed', 'owner_statement'})
# Inside the empty room only the positive local 'empty' answer is correct.
ACCEPT = {scoring.EMPTY_ROOM: (EMPTY,)}
TRUTH_CLASS = {scoring.EMPTY_ROOM: EMPTY}  # truth key -> header class (one-hot for Brier)
HOLD_S = 12.0
WINDOW_S = 5.0
WINDOW_TRUTH_FRACTION = 0.80
CONFIDENT_P = 0.8
PROBABILITY_SUM_TOLERANCE = 0.01
TAU_SWEEP = tuple(round(0.30 + 0.05 * i, 2) for i in range(14))  # 0.30 .. 0.95
DEFAULT_TAU = 0.5
MACRO_F1_PASS = 0.70
PRECISION_TARGET = 0.90
GATE_MIN_SESSIONS, GATE_MIN_DAYS, GATE_MIN_LABELLED_S = 3, 2, 480.0
TRIAL_S = 12.0  # one independent trial per hold
MIN_CALIBRATION_TICKS = 200
EMPTY_SAFETY_MAX = 0.01      # confident activity answers in the empty room
EMPTY_DETECTION_MIN = 0.95   # 'empty' shown in the empty room
ECE_BINS = 5
BOOTSTRAP_MIN_SESSIONS = 8
Z95 = 1.959964
INSUFFICIENT = 'insufficient_data'


def finite(value):
    return scoring.finite(value)


# ---- Inputs: tick files, the split, labels ----

def json_line(file, number, line):
    try:
        return json.loads(line)
    except json.JSONDecodeError as error:
        raise SystemExit('%s line %d: not JSON (%s)' % (file, number, error.msg))


def read_ticks(file):
    """(header, emissions) of one lab-ticks-1 file; SystemExit on a bad line."""
    lines = Path(regression.path(file)).read_text(encoding='utf-8').splitlines()
    if not lines:
        raise SystemExit('%s: empty tick file' % file)
    header = json_line(file, 1, lines[0])
    if not isinstance(header, dict) or header.get('schema') != TICKS_SCHEMA:
        raise SystemExit('%s: header schema %s required' % (file, TICKS_SCHEMA))
    if header.get('arm') not in HEADER_ARMS:
        raise SystemExit('%s: header arm must be one of %s' % (file, '|'.join(HEADER_ARMS)))
    classes = header.get('classes')
    if not isinstance(classes, list) or set(classes) != set(CLASSES):
        raise SystemExit('%s: header classes must be exactly %s' % (file, list(CLASSES)))
    if header.get('calibration') not in ('distribution', 'none') or not isinstance(header.get('consumes_qwen'), bool):
        raise SystemExit('%s: header calibration (distribution|none) and consumes_qwen (bool) required' % file)
    emissions = []
    for number, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        value = json_line(file, number, line)
        if not isinstance(value, dict) or not isinstance(value.get('session_id'), str)                 or not SESSION_ID.fullmatch(value['session_id']) or value.get('arm') not in ('a', 'b'):
            raise SystemExit('%s line %d: session_id (32 hex) and arm a|b required' % (file, number))
        if not finite(value.get('t')) or not finite(value.get('available_at')) or value['available_at'] < 0:
            raise SystemExit('%s line %d: finite t and available_at required' % (file, number))
        if value['t'] > value['available_at'] + TIME_TOLERANCE_S:
            raise SystemExit('%s line %d: t after available_at (an answer before its observation)' % (file, number))
        if 'closed_at' in value and value['closed_at'] is not None and not finite(value['closed_at']):
            raise SystemExit('%s line %d: closed_at must be finite' % (file, number))
        raw = value.get('probabilities')
        if not isinstance(raw, dict) or set(raw) - set(CLASSES):
            raise SystemExit('%s line %d: probabilities over the header classes required' % (file, number))
        probabilities = {c: raw.get(c, 0) for c in CLASSES}
        if not all(finite(p) and 0 <= p <= 1 for p in probabilities.values()):
            raise SystemExit('%s line %d: probabilities must be finite in [0, 1]' % (file, number))
        if abs(sum(probabilities.values()) - 1) > PROBABILITY_SUM_TOLERANCE:
            raise SystemExit('%s line %d: probabilities must sum to 1 +- %s' % (file, number, PROBABILITY_SUM_TOLERANCE))
        emissions.append({**value, 'probabilities': {c: float(p) for c, p in probabilities.items()}})
    return header, emissions


def load_split(file):
    value = json.loads(Path(regression.path(file)).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != SPLIT_SCHEMA or not finite(value.get('frozen_at')):
        raise SystemExit('split schema %s with frozen_at required' % SPLIT_SCHEMA)
    for name in ('dev', 'slices'):
        if not isinstance(value.get(name, {}), dict):
            raise SystemExit('split %s must be an object' % name)
    return value


def site_zone(name):
    """(tzinfo, note): the site's zone, or UTC with a note when the host has
    no tz database (Windows without tzdata)."""
    try:
        return zoneinfo.ZoneInfo(str(name or 'UTC')), None
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return datetime.timezone.utc, 'time zone %s unavailable on this host; calendar days computed in UTC' % name


def calendar_day(epoch, zone):
    if not finite(epoch):
        return None
    return datetime.datetime.fromtimestamp(epoch, zone).date().isoformat()


def check_split_keys(sessions, split):
    """Every 'dev' and 'slices' key must be a full ledger session id: a prefix
    or a typo in 'dev' would silently make that dev session held-out."""
    for name in ('dev', 'slices'):
        for key in split.get(name) or {}:
            if key in sessions:
                continue
            matches = [sid for sid in sessions if sid.startswith(str(key))]
            hint = '; did you mean %s?' % matches[0] if len(matches) == 1 else ''
            raise SystemExit('split %s key %r is not a session id of this ledger (full 32-hex ids required)%s' % (name, key, hint))


def assign(sessions, split):
    """{session_id: {'assignment': held_out|legacy, 'dev': bool, 'day', 'influence', 'slices'}}.
    Refuses (SystemExit) a 'dev' or 'slices' key that is not a session id."""
    check_split_keys(sessions, split)
    zone, note = site_zone(split.get('site_timezone'))
    dev, slices = split.get('dev') or {}, split.get('slices') or {}
    result = {}
    for sid, session in sessions.items():
        started = session.get('started_at')
        held_out = sid not in dev and finite(started) and started > split['frozen_at']
        result[sid] = {'assignment': 'held_out' if held_out else 'legacy', 'dev': sid in dev,
                       'day': calendar_day(started, zone), 'influence': list(dev.get(sid) or []),
                       'slices': list(slices.get(sid) or [])}
    return result, note


def held_out_sessions(assignment, requested=None):
    """The held-out session ids, or the requested subset; a legacy or dev
    session asked for as held-out refuses."""
    held = {sid for sid, a in assignment.items() if a['assignment'] == 'held_out'}
    if requested is None:
        return held
    for sid in requested:
        if sid not in assignment:
            raise SystemExit('unknown session %s' % sid)
        if sid not in held:
            raise SystemExit('refusing to report %s as held-out: it is %s%s' % (
                sid, assignment[sid]['assignment'], ' (dev)' if assignment[sid]['dev'] else ''))
    return set(requested)


def stored_provenances(db):
    """{source: provenance} for annotations that store their own provenance."""
    result = {}
    for sid, aid, raw in db.execute('SELECT session_id,id,payload FROM annotations WHERE deleted=0'):
        kind = json.loads(raw).get('provenance')
        if kind in regression.PROVENANCES:
            result['annotation:' + aid] = kind
    return result


def labels(db, manifest):
    """lab_regression.gather's intervals with a stored provenance applied and
    a 'gold' flag; plus (sessions, excluded). A manifest owner_statement is
    reviewed (gather leaves it unreviewed without '"reviewed": true')."""
    sessions, intervals, excluded = regression.gather(db, manifest)
    stored = stored_provenances(db)
    result = []
    for x in intervals:
        kind = stored.get(x['source'], x['provenance'])
        reviewed = x['reviewed'] or (x['source'].startswith('manifest:') and kind == 'owner_statement')
        result.append({**x, 'provenance': kind, 'reviewed': reviewed, 'gold': reviewed and kind in GOLD_PROVENANCES})
    return sessions, result, excluded


def select_labels(intervals, *, consumes_qwen, silver):
    """The intervals one run is scored against, and what was dropped."""
    kept, dropped = [], defaultdict(int)
    for x in intervals:
        if consumes_qwen and x['provenance'] == 'qwen_proposed':
            dropped['qwen_proposed'] += 1
        elif x['gold'] or silver:
            kept.append(x)
        else:
            dropped['silver'] += 1
    return kept, dict(dropped)


# ---- The held view and the decision ----

def held_view(emissions, hold_s=HOLD_S):
    """{(session_id, arm): [undecided records]}: each emission from its
    available_at to min(next available_at, closed_at, available_at + hold_s)."""
    groups = defaultdict(list)
    for e in emissions:
        groups[(e['session_id'], e['arm'])].append(e)
    result = {}
    for key, items in groups.items():
        items.sort(key=lambda e: (e['available_at'], e['t']))
        records = []
        for i, e in enumerate(items):
            start = e['available_at']
            ends = [start + hold_s]
            if finite(e.get('closed_at')):
                ends.append(e['closed_at'])
            if i + 1 < len(items):
                ends.append(items[i + 1]['available_at'])
            end = min(ends)
            if end > start:
                records.append({'start': start, 'end': end, 'observed_at': e['t'], 'probabilities': e['probabilities']})
        result[key] = records
    return result


def decide(probabilities, tau):
    """(shown, confidence) at threshold tau; shown is a class name or ABSTAIN."""
    e = probabilities[EMPTY]
    best = max(ANSWER_CLASSES, key=lambda c: probabilities[c])
    if e >= max(probabilities[c] for c in CLASSES if c != EMPTY):
        return EMPTY, e
    if probabilities[best] >= tau:
        return best, probabilities[best]
    return ABSTAIN, probabilities[best]


def decided(records, tau):
    """lab_scoring.prediction records of the held answers at tau."""
    result = []
    for r in records:
        shown, confidence = decide(r['probabilities'], tau)
        result.append(scoring.prediction({'start': r['start'], 'end': r['end'], 'available_at': r['start'], 'valid_until': r['end'],
                                          'activities': [] if shown == ABSTAIN else [shown], 'confidence': confidence,
                                          'probabilities': r['probabilities']}))
    return result


def held_at(records, starts, t):
    """The held record covering t, or None. records are in start order and
    never overlap (each ends by the next one's start); starts are their
    starts."""
    index = bisect.bisect_right(starts, t) - 1
    if index >= 0 and t < records[index]['end']:
        return records[index]
    return None


# ---- Windows ----

def window_truth(intervals, window_s=WINDOW_S):
    """[(window_start, truth_key)] for windows from 0 that one key covers
    >= 80 %, and the count of labelled windows dropped for a weaker majority."""
    by_key = defaultdict(list)
    for x in scoring.truth(intervals):
        by_key[x['activity']].append((x['start'], x['end']))
    spans = {key: scoring.merge(v) for key, v in by_key.items()}
    last = max((b for v in spans.values() for _, b in v), default=0)
    result, dropped = [], 0
    k = 0
    while k * window_s < last:
        a, b = k * window_s, (k + 1) * window_s
        k += 1
        covered = {key: sum(max(0.0, min(end, b) - max(start, a)) for start, end in v) for key, v in spans.items()}
        if not any(covered.values()):
            continue
        key = max(covered, key=covered.get)
        if covered[key] >= WINDOW_TRUTH_FRACTION * window_s:
            result.append((a, key))
        else:
            dropped += 1
    return result, dropped


def window_answers(windows, records, window_s):
    """[(truth_key, probabilities or None)]: the held answer at each window's
    midpoint, before any threshold."""
    starts = [r['start'] for r in records]
    result = []
    for start, key in windows:
        held = held_at(records, starts, start + window_s / 2)
        result.append((key, held['probabilities'] if held else None))
    return result


def window_pairs(answers, tau):
    """[(truth_key, shown)] at tau from window_answers(); no held answer or an
    abstention is ABSTAIN."""
    return [(key, decide(p, tau)[0] if p else ABSTAIN) for key, p in answers]


def class_counts(pairs):
    """Per S class {tp, fn, fp}, plus labelled and answered window counts."""
    counts = {c: {'tp': 0, 'fn': 0, 'fp': 0} for c in S_CLASSES}
    answered = 0
    for key, shown in pairs:
        answered += shown != ABSTAIN
        for c in S_CLASSES:
            if key == c and shown == c:
                counts[c]['tp'] += 1
            elif key == c:
                counts[c]['fn'] += 1
            elif shown == c:
                counts[c]['fp'] += 1
    return counts, len(pairs), answered


def ratio(a, b, places=4):
    return round(a / b, places) if b else None


def prf(count):
    tp, fn, fp = count['tp'], count['fn'], count['fp']
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = ratio(2 * tp, 2 * tp + fn + fp)
    return precision, recall, f1


def macro_f1(counts, classes):
    scores = [prf(counts[c])[2] for c in classes]
    scores = [s for s in scores if s is not None]
    return round(sum(scores) / len(scores), 4) if scores else None


def micro_precision(counts):
    tp = sum(counts[c]['tp'] for c in S_CLASSES)
    return ratio(tp, tp + sum(counts[c]['fp'] for c in S_CLASSES))


def sweep(units, taus=TAU_SWEEP):
    """[{tau, coverage, precision, macro_f1, per-class precision}] over units."""
    result = []
    for tau in taus:
        pairs = [pair for u in units for pair in window_pairs(u['answers'], tau)]
        counts, labelled, answered = class_counts(pairs)
        with_data = [c for c in S_CLASSES if counts[c]['tp'] + counts[c]['fn']]
        result.append({'tau': tau, 'coverage': ratio(answered, labelled), 'precision': micro_precision(counts),
                       'macro_f1': macro_f1(counts, with_data), 'with_data': with_data,
                       'class_precision': {c: prf(counts[c])[0] for c in S_CLASSES}})
    return result


def meets_precision(point):
    """A sweep point is feasible when it answers something and every class
    with labelled windows shows answers at precision >= PRECISION_TARGET. A
    class that shows nothing (precision None) does not meet the target: else
    a tau high enough to show nothing at all would always be 'feasible'."""
    if not point['coverage']:
        return False
    return all(point['class_precision'][c] is not None and point['class_precision'][c] >= PRECISION_TARGET
               for c in point['with_data'])


def fit_tau(units):
    """The tau of the sweep with the best macro-F1 among taus where every
    class's precision is >= PRECISION_TARGET (meets_precision, where
    attainable), else the best macro-F1 overall; the lowest such tau on ties."""
    points = sweep(units)
    feasible = [p for p in points if meets_precision(p)]
    pool = feasible or points
    best = max(pool, key=lambda p: (p['macro_f1'] or 0, -p['tau']))
    return best['tau'], bool(feasible)


# ---- Ticks (seconds of labelled time) ----

def tick_stats(unit, tau):
    """Second-based statistics of one session and arm at tau, from
    lab_scoring.segments: per shown class seconds, calibration samples (ECE:
    answered seconds only), Brier samples (every second with a held answer,
    abstentions included, so abstaining on hard seconds does not flatter
    it), the empty room, and accumulate()'s rows (time-to-first-correct)."""
    predictions = decided(unit['records'], tau)
    starts = [r['start'] for r in unit['records']]
    stats = {'shown_s': defaultdict(float), 'shown_correct_s': defaultdict(float), 'answered_s': 0.0, 'correct_s': 0.0,
             'confident_s': 0.0, 'confident_correct_s': 0.0, 'empty_s': 0.0, 'empty_detected_s': 0.0, 'empty_unsafe_s': 0.0,
             'samples': [], 'brier_samples': [], 'labelled_s': 0.0}
    for start, end, key, shown, confidence, _ in scoring.segments(unit['labels'], predictions):
        width = end - start
        stats['labelled_s'] += width
        if key == scoring.EMPTY_ROOM:
            stats['empty_s'] += width
        record = held_at(unit['records'], starts, (start + end) / 2)
        truth = TRUTH_CLASS.get(key, key if key in CLASSES else None)
        if record is not None:
            stats['brier_samples'].append({'weight': width, 'probabilities': record['probabilities'], 'truth': truth})
        if shown in (scoring.ABSTAIN, scoring.NO_ANSWER):
            continue
        correct = shown in ACCEPT.get(key, (key,))
        stats['answered_s'] += width
        stats['correct_s'] += width * correct
        stats['shown_s'][shown] += width
        stats['shown_correct_s'][shown] += width * correct
        if confidence >= CONFIDENT_P:
            stats['confident_s'] += width
            stats['confident_correct_s'] += width * correct
        if key == scoring.EMPTY_ROOM:
            stats['empty_detected_s'] += width * (shown == EMPTY)
            stats['empty_unsafe_s'] += width * (shown in ANSWER_CLASSES and confidence >= CONFIDENT_P)
        stats['samples'].append({'confidence': confidence, 'correct': correct, 'weight': width})
    stats['rows'] = scoring.accumulate(unit['labels'], predictions, accept=ACCEPT, session_id=unit['session_id'])
    return stats


def wilson_lower(successes, trials, z=Z95):
    """Wilson 95 % lower bound; fractional trials allowed (one per hold)."""
    if trials <= 0:
        return None
    p = successes / trials
    centre = p + z * z / (2 * trials)
    spread = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return round(max(0.0, (centre - spread) / (1 + z * z / trials)), 4)


def ece(samples, bins=ECE_BINS):
    """Expected calibration error over equal-mass bins of weighted samples."""
    items = sorted(samples, key=lambda s: s['confidence'])
    total = sum(s['weight'] for s in items)
    if not total:
        return None
    binned, cumulative = [[] for _ in range(bins)], 0.0
    for s in items:
        index = min(bins - 1, int((cumulative + s['weight'] / 2) / total * bins))
        binned[index].append(s)
        cumulative += s['weight']
    error = 0.0
    for group in binned:
        weight = sum(s['weight'] for s in group)
        if not weight:
            continue
        accuracy = sum(s['weight'] * s['correct'] for s in group) / weight
        confidence = sum(s['weight'] * s['confidence'] for s in group) / weight
        error += weight / total * abs(accuracy - confidence)
    return round(error, 4)


def brier(samples):
    """Mean squared error of the distribution against the one-hot truth over
    the header classes, over every held second (abstentions included);
    seconds whose truth is no single class are left out."""
    total, weight = 0.0, 0.0
    for s in samples:
        if s['truth'] is None or s['probabilities'] is None:
            continue
        total += s['weight'] * sum((s['probabilities'][c] - (c == s['truth'])) ** 2 for c in CLASSES)
        weight += s['weight']
    return round(total / weight, 4) if weight else None


def percentile(values, fraction):
    if not values:
        return None
    items = sorted(values)
    return round(items[min(len(items) - 1, int(math.ceil(fraction * len(items)) - 1))], 2)


# ---- A scope: held-out, legacy, a slice, one session ----

def unit_report(unit, tau):
    """One session and arm at tau: window pairs, tick stats."""
    return {'session_id': unit['session_id'], 'arm': unit['arm'], 'tau': tau,
            'pairs': window_pairs(unit['answers'], tau), 'dropped': unit['dropped'],
            'ticks': tick_stats(unit, tau)}


def class_support(units, assignment):
    """Per S class: sessions, days and labelled seconds from the intervals
    (each session once, however many arms it has)."""
    support = {c: {'sessions': set(), 'days': set(), 'labelled_s': 0.0} for c in S_CLASSES}
    first = {}
    for u in units:
        first.setdefault(u['session_id'], u)
    for u in first.values():
        for x in scoring.truth(u['labels']):
            if x['activity'] in support:
                row = support[x['activity']]
                row['sessions'].add(u['session_id'])
                row['days'].add(assignment[u['session_id']]['day'])
                row['labelled_s'] += x['end'] - x['start']
    return support


def gate(row):
    return ('ok' if len(row['sessions']) >= GATE_MIN_SESSIONS and len(row['days'] - {None}) >= GATE_MIN_DAYS
            and row['labelled_s'] >= GATE_MIN_LABELLED_S else INSUFFICIENT)


def scope_report(units, reports, assignment, *, calibration, bands=True):
    """The metrics of one scope from its units' reports."""
    pairs = [pair for r in reports for pair in r['pairs']]
    counts, labelled, answered = class_counts(pairs)
    support = class_support(units, assignment)
    classes = {}
    for c in S_CLASSES:
        precision, recall, f1 = prf(counts[c])
        shown = sum(r['ticks']['shown_s'][c] for r in reports)
        correct = sum(r['ticks']['shown_correct_s'][c] for r in reports)
        ttfc = [v for r in reports for v in r['ticks']['rows'].get(c, {'ttfc_s': []})['ttfc_s']]
        classes[c] = {**counts[c], 'precision': precision, 'recall': recall, 'f1': f1,
                      'precision_lb': wilson_lower(correct / TRIAL_S, shown / TRIAL_S), 'n_eff': round(shown / TRIAL_S, 2),
                      'shown_s': round(shown, 1), 'sessions': len(support[c]['sessions']),
                      'days': len(support[c]['days'] - {None}), 'labelled_s': round(support[c]['labelled_s'], 1),
                      'gate': gate(support[c]), 'ttfc': {'n': len(ttfc), 'p50_s': percentile(ttfc, 0.5), 'p90_s': percentile(ttfc, 0.9)}}
    passing = [c for c in S_CLASSES if classes[c]['gate'] == 'ok']
    excluded = [c for c in S_CLASSES if c not in passing]
    macro = macro_f1(counts, passing)
    sweep_points = sweep(units)
    at_p90 = [p['coverage'] for p in sweep_points if p['precision'] is not None and p['precision'] >= PRECISION_TARGET
              and p['coverage'] is not None]
    samples = [s for r in reports for s in r['ticks']['samples']]
    brier_samples = [s for r in reports for s in r['ticks']['brier_samples']]
    answered_s = sum(r['ticks']['answered_s'] for r in reports)
    confident_s = sum(r['ticks']['confident_s'] for r in reports)
    confident_correct_s = sum(r['ticks']['confident_correct_s'] for r in reports)
    empty_s = sum(r['ticks']['empty_s'] for r in reports)
    ttfc_all = [v for r in reports for row in r['ticks']['rows'].values() for v in row['ttfc_s']]
    sessions = sorted({r['session_id'] for r in reports})
    result = {
        'sessions': len(sessions), 'days': len({assignment[s]['day'] for s in sessions} - {None}),
        'missing': ['%s/%s' % (u['session_id'], u['arm']) for u in units if u['missing']],
        'labelled_s': round(sum(r['ticks']['labelled_s'] for r in reports), 1),
        'taus': sorted({r['tau'] for r in reports}),
        'windows': {'labelled': labelled, 'answered': answered, 'dropped': sum(r['dropped'] for r in reports)},
        'coverage': ratio(answered, labelled), 'micro_precision': micro_precision(counts),
        'macro_f1': macro, 'excluded_classes': excluded,
        'passes_stage1': bool(not excluded and macro is not None and macro >= MACRO_F1_PASS),
        'classes': classes,
        'risk_coverage': {'sweep': [{'tau': p['tau'], 'coverage': p['coverage'], 'precision': p['precision']} for p in sweep_points],
                          'coverage_at_p90': max(at_p90) if at_p90 else None},
        'empty_room': {'empty_s': round(empty_s, 1),
                       'safety': ratio(sum(r['ticks']['empty_unsafe_s'] for r in reports), empty_s),
                       'detection': ratio(sum(r['ticks']['empty_detected_s'] for r in reports), empty_s)},
        'latency': {'n': len(ttfc_all), 'ttfc_p50_s': percentile(ttfc_all, 0.5), 'ttfc_p90_s': percentile(ttfc_all, 0.9)},
    }
    room = result['empty_room']  # bars: None when the scope has no empty-room time
    room['safety_ok'] = None if room['safety'] is None else room['safety'] <= EMPTY_SAFETY_MAX
    room['detection_ok'] = None if room['detection'] is None else room['detection'] >= EMPTY_DETECTION_MIN
    if calibration == 'none':
        result['band_precision'] = None
        result['calibration'] = 'none'
    else:
        result['band_precision'] = {'ticks_s': round(confident_s, 1), 'n_eff': round(confident_s / TRIAL_S, 2),
                                    'precision': ratio(confident_correct_s, confident_s),
                                    'precision_lb': wilson_lower(confident_correct_s / TRIAL_S, confident_s / TRIAL_S)}
        per_session = []  # only sessions with enough answered seconds of their own
        for sid in sessions:
            mine = [s for r in reports if r['session_id'] == sid for s in r['ticks']['samples']]
            if sum(s['weight'] for s in mine) >= MIN_CALIBRATION_TICKS:
                per_session.append(ece(mine))
        enough = answered_s >= MIN_CALIBRATION_TICKS
        result['calibration'] = {'answered_ticks_s': round(answered_s, 1), 'ece_status': 'ok' if enough else INSUFFICIENT,
                                 'ece': ece(samples) if enough else None, 'brier': brier(brier_samples),
                                 'session_ece_range': [min(per_session), max(per_session)] if per_session else None}
    if bands:
        per = []
        for sid in sessions:
            mine = [r for r in reports if r['session_id'] == sid]
            c, n, a = class_counts([pair for r in mine for pair in r['pairs']])
            with_data = [k for k in S_CLASSES if c[k]['tp'] + c[k]['fn']]
            per.append({'macro_f1': macro_f1(c, with_data), 'coverage': ratio(a, n), 'precision': micro_precision(c)})
        result['bands'] = {name: [min(v), max(v)] if (v := [p[name] for p in per if p[name] is not None]) else None
                           for name in ('macro_f1', 'coverage', 'precision')}
        result['bands']['bootstrap'] = 'n/a (< %d sessions)' % BOOTSTRAP_MIN_SESSIONS if len(sessions) < BOOTSTRAP_MIN_SESSIONS \
            else 'n/a (deferred)'
    return result


# ---- A run ----

def build_units(intervals, held, window_s, *, sessions, arms):
    """[{session_id, arm, labels, records, answers, dropped, missing}] for
    every labelled session of `sessions` and every arm of the run. A
    (session, arm) the tick file never speaks for is scored with no records
    (no answer throughout) and marked missing: a run cannot drop a hard
    session without a penalty."""
    by_session = defaultdict(list)
    for x in intervals:
        if x['session_id'] in sessions:
            by_session[x['session_id']].append(x)
    units = []
    for sid in sorted(by_session):
        windows, dropped = window_truth(by_session[sid], window_s)
        for arm in arms:
            records = held.get((sid, arm), [])
            units.append({'session_id': sid, 'arm': arm, 'labels': by_session[sid], 'records': records,
                          'answers': window_answers(windows, records, window_s), 'dropped': dropped,
                          'missing': (sid, arm) not in held})
    return units


def choose_taus(held_units, legacy_units, assignment, *, fit, tau):
    """{session_id: tau} for every unit's session, and the fit description."""
    units = held_units + legacy_units
    if tau is not None:
        return {u['session_id']: tau for u in units}, {'mode': 'fixed', 'tau': tau}
    if fit == 'dev':
        if not legacy_units:
            raise SystemExit('--fit dev needs labelled legacy sessions')
        chosen, constrained = fit_tau(legacy_units)
        return {u['session_id']: chosen for u in units}, {'mode': 'dev', 'tau': chosen, 'precision_constrained': constrained}
    days = sorted({assignment[u['session_id']]['day'] for u in held_units} - {None})
    overall, overall_constrained = fit_tau(held_units) if held_units else (DEFAULT_TAU, False)
    taus, by_day, fallback = {}, {}, []
    for day in days:
        others = [u for u in held_units if assignment[u['session_id']]['day'] != day]
        if others:
            by_day[day], _ = fit_tau(others)
        elif legacy_units:
            by_day[day], _ = fit_tau(legacy_units)
            fallback.append(day)
        else:
            by_day[day] = DEFAULT_TAU
            fallback.append(day)
        for u in held_units:
            if assignment[u['session_id']]['day'] == day:
                taus[u['session_id']] = by_day[day]
    for u in held_units:
        taus.setdefault(u['session_id'], overall)  # a session without a day
    for u in legacy_units:
        taus[u['session_id']] = overall
    return taus, {'mode': 'loo', 'taus_by_day': by_day, 'fallback_days': fallback, 'legacy_tau': overall,
                  'precision_constrained': overall_constrained}


def evaluate_run(name, header, emissions, intervals, assignment, *, held_out, silver, fit, tau, window_s, hold_s):
    """The report of one run."""
    kept, dropped = select_labels(intervals, consumes_qwen=header['consumes_qwen'], silver=silver)
    held = held_view(emissions, hold_s)
    arms = sorted({e['arm'] for e in emissions}) or ['a']
    # A held-out session left out of a restricted held-out set is in neither
    # scope: it never joins the legacy slice.
    legacy = {sid for sid, a in assignment.items() if a['assignment'] == 'legacy'}
    units = build_units(kept, held, window_s, sessions=set(held_out) | legacy, arms=arms)
    held_units = [u for u in units if u['session_id'] in held_out]
    legacy_units = [u for u in units if assignment[u['session_id']]['assignment'] == 'legacy']
    units = held_units + legacy_units
    taus, fit_info = choose_taus(held_units, legacy_units, assignment, fit=fit, tau=tau)
    reports = {(u['session_id'], u['arm']): unit_report(u, taus[u['session_id']]) for u in units}
    calibration = header['calibration']

    def scope(selected, bands=True):
        return scope_report(selected, [reports[(u['session_id'], u['arm'])] for u in selected], assignment,
                            calibration=calibration, bands=bands)

    tags = sorted({t for u in held_units for t in assignment[u['session_id']]['slices']})
    return {'run': header.get('run', name), 'arm': header.get('arm'), 'consumes_qwen': header['consumes_qwen'],
            'calibration': calibration, 'source': header.get('source'), 'emissions': len(emissions),
            'labels': {'intervals': len(kept), 'gold': sum(1 for x in kept if x['gold']),
                       'silver': sum(1 for x in kept if not x['gold']), 'dropped': dropped},
            'fit': fit_info,
            'held_out': scope(held_units),
            'legacy': scope(legacy_units),
            'slices': {tag: scope([u for u in held_units if tag in assignment[u['session_id']]['slices']]) for tag in tags},
            'sessions': {sid: {'assignment': assignment[sid]['assignment'], 'dev': assignment[sid]['dev'],
                               'day': assignment[sid]['day'], **scope([u for u in units if u['session_id'] == sid], bands=False)}
                         for sid in sorted({u['session_id'] for u in units})}}


def evaluate(db, split, runs, *, manifest=None, held_out=None, silver=False, fit='loo', tau=None, window_s=WINDOW_S,
             hold_s=HOLD_S):
    """The whole report. runs: {name: ticks file}."""
    sessions, intervals, _ = labels(db, manifest or {'schema': regression.MANIFEST_SCHEMA})
    assignment, note = assign(sessions, split)
    held = held_out_sessions(assignment, held_out)
    report = {'schema': REPORT_SCHEMA, 'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'split': {'frozen_at': split['frozen_at'], 'site_timezone': split.get('site_timezone'), 'timezone_note': note,
                        'held_out': sorted(held),
                        'legacy': sorted(s for s, a in assignment.items() if a['assignment'] == 'legacy')},
              'settings': {'window_s': window_s, 'hold_s': hold_s, 'silver': silver, 'fit': 'fixed' if tau is not None else fit,
                           'tau': tau},
              'runs': {}, 'local_only': True}
    for name, file in sorted(runs.items()):
        header, emissions = read_ticks(file)
        report['runs'][name] = evaluate_run(name, header, emissions, intervals, assignment, held_out=held, silver=silver,
                                            fit=fit, tau=tau, window_s=window_s, hold_s=hold_s)
    return report


# ---- Printing ----

def fmt(value, width=6):
    if value is None:
        return '%*s' % (width, '-')
    if isinstance(value, float):
        return '%*.3f' % (width, value)
    return '%*s' % (width, value)


def print_scope(title, scope):
    print('  %s: %d sessions, %d days, %.0f labelled s, tau %s' % (title, scope['sessions'], scope['days'], scope['labelled_s'],
                                                                   ', '.join(str(t) for t in scope['taus']) or '-'))
    if not scope['sessions']:
        return
    print('    %-14s %5s %5s %5s %6s %6s %6s %6s %6s %5s %4s %8s  %s' % (
        'class', 'tp', 'fn', 'fp', 'prec', 'rec', 'f1', 'p_lb', 'n_eff', 'sess', 'days', 'label_s', 'gate'))
    for c, row in scope['classes'].items():
        print('    %-14s %5d %5d %5d %s %s %s %s %s %5d %4d %8.0f  %s' % (
            c, row['tp'], row['fn'], row['fp'], fmt(row['precision']), fmt(row['recall']), fmt(row['f1']), fmt(row['precision_lb']),
            fmt(row['n_eff']), row['sessions'], row['days'], row['labelled_s'], row['gate']))
    if scope['missing']:
        print('    MISSING (scored as no answer): %s' % ', '.join(scope['missing']))
    w = scope['windows']
    print('    macro-F1 %s (excluded: %s)  passes_stage1 %s  windows %d labelled / %d answered / %d dropped' % (
        fmt(scope['macro_f1']), ', '.join(scope['excluded_classes']) or 'none', scope['passes_stage1'],
        w['labelled'], w['answered'], w['dropped']))
    print('    coverage %s  micro precision %s  coverage@P90 %s' % (
        fmt(scope['coverage']), fmt(scope['micro_precision']), fmt(scope['risk_coverage']['coverage_at_p90'])))
    if scope['calibration'] == 'none':
        print('    calibration: none (band precision and ECE not reported)')
    else:
        band, cal = scope['band_precision'], scope['calibration']
        print('    band precision (p >= %.1f) %s lb %s over %.0f s  ECE %s (%s) Brier %s session ECE %s' % (
            CONFIDENT_P, fmt(band['precision']), fmt(band['precision_lb']), band['ticks_s'], fmt(cal['ece']), cal['ece_status'],
            fmt(cal['brier']), cal['session_ece_range']))
    e = scope['empty_room']
    bar = lambda ok: 'n/a' if ok is None else 'ok' if ok else 'BAR'
    print('    empty room: %.0f s, safety %s (%s), detection %s (%s)' % (
        e['empty_s'], fmt(e['safety']), bar(e['safety_ok']), fmt(e['detection']), bar(e['detection_ok'])))
    lat = scope['latency']
    print('    time-to-first-correct: p50 %s p90 %s n %d' % (fmt(lat['ttfc_p50_s']), fmt(lat['ttfc_p90_s']), lat['n']))
    if 'bands' in scope:
        b = scope['bands']
        print('    per-session bands: macro-F1 %s coverage %s precision %s; bootstrap: %s' % (
            b['macro_f1'], b['coverage'], b['precision'], b['bootstrap']))


def print_report(report):
    split = report['split']
    print('lab-eval: %d held-out, %d legacy sessions; frozen_at %s; %s%s' % (
        len(split['held_out']), len(split['legacy']), split['frozen_at'], split['site_timezone'],
        '; NOTE ' + split['timezone_note'] if split['timezone_note'] else ''))
    for name, run in report['runs'].items():
        print()
        print('run %s (arm %s, consumes_qwen %s, calibration %s): %d emissions, %d labels (%d gold, %d silver), dropped %s, fit %s' % (
            name, run['arm'], run['consumes_qwen'], run['calibration'], run['emissions'], run['labels']['intervals'],
            run['labels']['gold'], run['labels']['silver'], run['labels']['dropped'] or 'none', json.dumps(run['fit'])))
        print_scope('HELD-OUT', run['held_out'])
        print_scope('LEGACY (not held-out; legacy and dev sessions, reported separately)', run['legacy'])
        for tag, scope in run['slices'].items():
            print_scope('slice %s (held-out)' % tag, scope)
        print('  sessions:')
        for sid, s in run['sessions'].items():
            print('    %s %-8s %s %-10s macro-F1 %s coverage %s precision %s' % (
                sid[:8], s['assignment'] + ('/dev' if s['dev'] else ''), s['day'], 'tau %s' % s['taus'][0] if s['taus'] else '',
                fmt(s['macro_f1']), fmt(s['coverage']), fmt(s['micro_precision'])))


def print_split(sessions, assignment, note):
    if note:
        print('NOTE: ' + note)
    print('%-32s %-9s %-10s %-5s %s' % ('session', 'split', 'day', 'dev', 'name | influence | slices'))
    for sid, a in sorted(assignment.items(), key=lambda kv: (sessions[kv[0]].get('started_at') or 0, kv[0])):
        print('%-32s %-9s %-10s %-5s %s | %s | %s' % (sid, a['assignment'], a['day'] or '-', 'dev' if a['dev'] else '',
                                                     (sessions[sid].get('name') or '')[:30], ', '.join(a['influence']),
                                                     ', '.join(a['slices'])))


# ---- CLI ----

def parse_runs(values):
    runs = {}
    for value in values:
        name, _, file = value.partition('=')
        if not name or not file:
            raise SystemExit('--run NAME=TICKS.jsonl required')
        runs[name] = file
    return runs


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('eval', 'split'):
        p = commands.add_parser(name)
        p.add_argument('--ledger', default=regression.LEDGER)
        p.add_argument('--split', required=True)
        if name == 'eval':
            p.add_argument('--manifest', default=regression.MANIFEST, help='private manifest; a missing file is none')
            p.add_argument('--run', action='append', default=[], help='NAME=TICKS.jsonl (repeatable)')
            p.add_argument('--tau', type=float, default=None)
            p.add_argument('--fit', choices=('loo', 'dev'), default='loo')
            p.add_argument('--silver', action='store_true')
            p.add_argument('--json', action='store_true')
            p.add_argument('--window-s', type=float, default=WINDOW_S)
            p.add_argument('--hold-s', type=float, default=HOLD_S)
            p.add_argument('--held-out', action='append', default=None, help='restrict the held-out set (refuses a legacy session)')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    db = regression.open_ro(args.ledger)
    try:
        split = load_split(args.split)
        if args.command == 'split':
            sessions, _, _ = regression.gather(db, {})
            assignment, note = assign(sessions, split)
            print_split(sessions, assignment, note)
            return 0
        if not args.run:
            raise SystemExit('at least one --run NAME=TICKS.jsonl required')
        if args.tau is not None and not 0 <= args.tau <= 1:
            raise SystemExit('--tau in [0, 1] required')
        if not (0 < args.window_s <= 60) or not (0 < args.hold_s <= 60):
            raise SystemExit('--window-s and --hold-s in (0, 60] required')
        manifest = regression.load_manifest(args.manifest) or {'schema': regression.MANIFEST_SCHEMA}
        report = evaluate(db, split, parse_runs(args.run), manifest=manifest, held_out=args.held_out, silver=args.silver,
                          fit=args.fit, tau=args.tau, window_s=args.window_s, hold_s=args.hold_s)
    finally:
        db.close()
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print_report(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())

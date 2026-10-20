"""Perception Lab regression set, per-activity scoring, ship gate and bounded replay.

Offline operator tool, run on the controller host; not part of the controller
release. The live ledger is opened read-only (?mode=ro) and nothing is ever
written to it. Principle (owner): a change must hold across all the
interactions in the room, not only the one it targets.

The regression set is every labelled session-clock interval in the ledger's
annotations table, plus the labels of a small private manifest (never in the
repository), e.g. ~/perception-lab/private/regression/manifest.json:

  {"schema": "lab-regression-manifest-1",
   "labels": [{"session_id": "<32 hex>", "activity": "computer_work",
               "start_s": 0, "end_s": null, "provenance": "owner_statement",
               "note": "owner at the PC throughout (unguided)"}],
   "exclude_sessions": [], "gate": {...tolerance overrides...},
   "candidates": {"den-facts-7-desk": {"jobs": ["replay-v6-20260930T17*"]}}}

end_s null is the whole recording. Each interval carries its session, times,
activity, reviewed flag and provenance: owner_reviewed, owner_unreviewed,
bookmark (made from the session's bookmarks), qwen_proposed (the note cites
Qwen) or owner_statement (the manifest). An 'other' label whose note says the
room is empty is scored as empty_room (lab_scoring).

A run is what went out live (recorded reasoning.output, grouped by the
request's wire) or one or more replay jobs of jev_replay_calls.py (its replay
database's replay_results: job, recorded_request_id, cutoff, result). A live
run speaks for each whole recording that sent requests; a replay job speaks
for the time its replayed requests cover (lab_scoring.spans). The gate
compares two runs only on their common time, per session and arm.

Baseline: by default a REPLAY of the baseline wire under the candidate's
conditions, never what went out live: the same recorded requests and cutoffs,
the same question set, from the same ledger snapshot (replay answers are
cached by packet, so one baseline replay serves every candidate). It is the
manifest's 'baseline' (JOBS), else --baseline-wire W, which selects the replay
jobs of wire W that cover the candidate's requests with its question set.
A live baseline differs in cadence and in JEV's run-to-run variance, so it is
used only when named (--baseline live) and is marked as such.

Gate (explicit tolerances, GATE_DEFAULTS; the manifest's 'gate' and
--tolerances FILE override them). For every labelled activity with data and
every arm, the candidate must not:
  * lower exact@coverage by more than exact_drop_max;
  * increase false-confident seconds (wrong at p >= 0.8) by more than
    false_confident_increase_max_s;
  * add false positives: seconds of any answered activity inside another
    activity's intervals (the empty room included), per activity pair, more
    than false_positive_increase_max_s above the baseline;
  * lose more than coverage_drop_max of coverage (so a candidate cannot pass
    by abstaining).
Every activity of the regression set must be tested: one with no common
labelled time leaves the gate INCOMPLETE (exit 4) unless
require_all_activities is false.

Noise (optional, --noise JOBS, repeatable): repeat replays of the same
packets (replay --repeat N re-asks every cached packet once into a new job).
Each repeat job is one run; runs of the baseline's wire join the baseline,
runs of the candidate's wire join the candidate. Per activity, arm and metric
the noise band is the largest spread (max - min) within either group, over
their common time. A difference that breaks a tolerance but stays within
max(tolerance, noise band) is marked within_noise, not FAIL: one replay per
side cannot tell a change from JEV's run-to-run variance (the earlier
repeatability probe: 51/53 same choice, p90 probability delta 0.08). A
difference beyond the band still fails. Without --noise nothing is excused.
An activity whose common data comes from only one session is marked LOW
(low confidence): it is still gated, but one session does not show that a
change holds across the room. Activities with less than min_labelled_s of
common labelled time are 'no data' and not gated. The table is printed and
the exit status is non-zero on failure (1), when nothing is comparable (3) or
when the gate is incomplete (4).

Usage:
  python lab_regression.py set     [--ledger DB] [--manifest FILE] [--json]
  python lab_regression.py score   --run live|JOBS [--wire W] [--replay-db DB ...] [--json]
  python lab_regression.py gate    --candidate JOBS [--baseline-wire W | --baseline JOBS | --baseline live]
                                   [--noise JOBS ...] [--tolerances FILE]
  python lab_regression.py replay  --config JEV_CONFIG --snapshot LEDGER_COPY --replay-db DB --questions v6|v7|v8
                                   [--wire W] [--desk-seat FILE] [--facts-key K] [--job NAME] [--repeat N]
                                   [--max-calls 400] (--dry-run | --yes)
  python lab_regression.py results SESSION_PREFIX

JOBS is a job name, a glob of job names, a comma-separated list of either, or
a candidate named in the manifest.
"""
import argparse
from collections import defaultdict
import fnmatch
import glob
import json
import os
from pathlib import Path
import re
import statistics
import sys
import threading

import lab_scoring as scoring

LEDGER = '~/perception-lab/data/lab.sqlite3'
MANIFEST = '~/perception-lab/private/regression/manifest.json'
REPLAY_DBS = '~/perception-lab/replay/replay-*.sqlite3'
MANIFEST_SCHEMA = 'lab-regression-manifest-1'
ACTIVITIES = ('reading', 'phone_use', 'computer_work', 'resting', 'transition', 'other', scoring.EMPTY_ROOM)
PROVENANCES = ('owner_reviewed', 'owner_unreviewed', 'bookmark', 'qwen_proposed', 'owner_statement')
GATE_DEFAULTS = {
    'exact_drop_max': 0.05,                 # absolute exact@coverage, per activity and arm
    'false_confident_increase_max_s': 1.0,  # seconds wrong at p >= 0.8, per activity and arm
    'false_positive_increase_max_s': 1.0,   # seconds of one answered activity in another's intervals
    'coverage_drop_max': 0.20,              # absolute coverage, per activity and arm
    'min_labelled_s': 10.0,                 # less common labelled time: no data, not gated
    'min_covered_s': 5.0,                   # exact@coverage compared only when both answered this long
    'require_all_activities': True,         # an activity of the set with no common time leaves the gate INCOMPLETE
}
REPLAY_CALL_CAP = 400
BOOKMARK_WINDOW_S = 5.0  # an annotation starting this soon after a bookmark was made from it


def path(value):
    return os.path.expanduser(value)


def open_ro(file):
    import sqlite3
    if not Path(path(file)).is_file():
        raise SystemExit('not found: %s' % file)
    db = sqlite3.connect('file:%s?mode=ro' % path(file), uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def load_manifest(file):
    if file is None or not Path(path(file)).is_file():
        return {}
    value = json.loads(Path(path(file)).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != MANIFEST_SCHEMA:
        raise SystemExit('manifest schema %s required' % MANIFEST_SCHEMA)
    return value


# ---- The regression set ----

def provenance(annotation, bookmarks):
    notes = str(annotation.get('notes') or '')
    if re.search(r'qwen', notes, re.I):
        return 'qwen_proposed'
    start = annotation['at_ms'] / 1000
    if re.search(r'\bcue|bookmark', notes, re.I) or any(0 <= start - b <= BOOKMARK_WINDOW_S for b in bookmarks):
        return 'bookmark'
    return 'owner_reviewed' if annotation.get('reviewed') is True else 'owner_unreviewed'


def gather(db, manifest):
    """(sessions, intervals, excluded): every labelled interval of the ledger
    and the manifest."""
    excluded_sessions = set(manifest.get('exclude_sessions') or [])
    sessions = {r['id']: dict(r) for r in db.execute('SELECT id,name,experiment_id,state,started_at,ended_at FROM sessions')}
    marks = defaultdict(list)
    for sid, raw in db.execute("SELECT session_id,payload FROM events WHERE kind IN ('bookmark','bookmark.added')"):
        at = json.loads(raw).get('at_ms')
        if scoring.finite(at):
            marks[sid].append(at / 1000)
    intervals, excluded = [], []
    for sid, aid, raw in db.execute('SELECT session_id,id,payload FROM annotations WHERE deleted=0 ORDER BY session_id,id'):
        item = json.loads(raw)
        note = str(item.get('notes') or '')[:160]
        reason = ('session excluded by the manifest' if sid in excluded_sessions
                  else 'video clock, not session clock' if item.get('time_basis', 'session') != 'session'
                  else 'not an activity' if item.get('label') in scoring.NOT_TRUTH
                  else 'no interval' if not scoring.finite(item.get('end_ms')) or item['end_ms'] <= item['at_ms'] else None)
        if reason:
            excluded.append({'session_id': sid, 'source': 'annotation:' + aid, 'label': item.get('label'), 'reason': reason})
            continue
        intervals.append({'session_id': sid, 'start': item['at_ms'] / 1000, 'end': item['end_ms'] / 1000,
                          'activity': scoring.scoring_key(item['label'], note), 'label': item['label'],
                          'reviewed': item.get('reviewed') is True, 'provenance': provenance(item, marks[sid]),
                          'source': 'annotation:' + aid, 'note': note})
    for i, label in enumerate(manifest.get('labels') or []):
        sid = label.get('session_id')
        if sid not in sessions:
            raise SystemExit('manifest label %d: unknown session' % i)
        if sid in excluded_sessions:
            continue
        session = sessions[sid]
        duration = (session['ended_at'] or 0) - (session['started_at'] or 0)
        start = label.get('start_s', 0)
        end = label.get('end_s') if label.get('end_s') is not None else duration
        activity = label.get('activity')
        if activity not in ACTIVITIES or not scoring.finite(start) or not scoring.finite(end) or not 0 <= start < end:
            raise SystemExit('manifest label %d: activity and 0 <= start_s < end_s required' % i)
        kind = label.get('provenance', 'owner_statement')
        if kind not in PROVENANCES:
            raise SystemExit('manifest label %d: unknown provenance' % i)
        intervals.append({'session_id': sid, 'start': float(start), 'end': float(end), 'activity': activity,
                          'label': scoring.STORED_LABEL.get(activity, activity), 'reviewed': label.get('reviewed') is True,
                          'provenance': kind, 'source': 'manifest:%d' % i, 'note': str(label.get('note') or '')[:160]})
    intervals.sort(key=lambda x: (sessions[x['session_id']]['started_at'] or 0, x['start']))
    return sessions, intervals, excluded


def coverage(intervals):
    """Per activity: labelled seconds, sessions and provenance."""
    table = {}
    for x in intervals:
        row = table.setdefault(x['activity'], {'seconds': 0.0, 'sessions': set(), 'intervals': 0, 'reviewed_s': 0.0,
                                               'provenance_s': defaultdict(float)})
        width = x['end'] - x['start']
        row['seconds'] += width
        row['sessions'].add(x['session_id'])
        row['intervals'] += 1
        row['reviewed_s'] += width if x['reviewed'] else 0
        row['provenance_s'][x['provenance']] += width
    return {k: {'seconds': round(v['seconds'], 1), 'sessions': len(v['sessions']), 'intervals': v['intervals'],
                'reviewed_s': round(v['reviewed_s'], 1), 'low_confidence': len(v['sessions']) < 2,
                'provenance_s': {p: round(s, 1) for p, s in sorted(v['provenance_s'].items())},
                'session_ids': sorted(v['sessions'])}
            for k, v in sorted(table.items(), key=lambda kv: ACTIVITIES.index(kv[0]) if kv[0] in ACTIVITIES else 99)}


def print_set(sessions, intervals, excluded, table):
    print('Regression set: %d intervals in %d sessions' % (len(intervals), len({x['session_id'] for x in intervals})))
    for x in intervals:
        s = sessions[x['session_id']]
        print('  %s %7.1f-%7.1f %-14s %-8s %-16s %s | %s' % (x['session_id'][:8], x['start'], x['end'], x['activity'],
              'reviewed' if x['reviewed'] else 'draft', x['provenance'], (s['name'] or '')[:40], x['note'][:50]))
    for x in excluded:
        print('  excluded %s %s %s: %s' % (x['session_id'][:8], x['source'], x['label'], x['reason']))
    print()
    print('%-14s %9s %8s %9s %10s  %s' % ('activity', 'seconds', 'sessions', 'intervals', 'reviewed_s', 'provenance (s)'))
    for key, row in table.items():
        print('%-14s %9.1f %8d %9d %10.1f  %s%s' % (key, row['seconds'], row['sessions'], row['intervals'], row['reviewed_s'],
              ', '.join('%s %.0f' % kv for kv in row['provenance_s'].items()),
              '   LOW: one session' if row['low_confidence'] else ''))


# ---- Runs: live and replay ----

def request_index(db, sids):
    """{request_id: (session_id, arm, cutoff)} of recorded JEV requests."""
    if not sids:
        return {}
    return {r[0]: (r[1], r[2], r[3]) for r in db.execute(
        "SELECT id,session_id,arm,json_extract(payload,'$.cutoff') FROM lab_reasoning_requests WHERE session_id IN (%s)"
        % ','.join('?' * len(sids)), list(sids))}


def live_outputs(db, sids):
    """{session_id: {'wires': {arm: {wire}}, 'outputs': {request_id: (arm, prediction value, wire)}}}."""
    result = {}
    for sid in sids:
        wires = {r[0]: (r[1], r[2] or 'pre-v6') for r in db.execute(
            """SELECT json_extract(payload,'$.request_id'),json_extract(payload,'$.arm'),json_extract(payload,'$.wire_version')
               FROM events WHERE session_id=? AND kind='reasoning.request'""", (sid,))}
        slot = result.setdefault(sid, {'wires': defaultdict(set), 'outputs': {}})
        for arm, wire in wires.values():
            slot['wires'][str(arm)].add(wire)
        for (raw,) in db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.output' ORDER BY id", (sid,)):
            value = json.loads(raw)
            if value.get('mode', 'live') != 'live':
                continue
            rid = value.get('request_id')
            slot['outputs'][rid] = (str(value.get('arm', 'unknown')), value.get('prediction'), (wires.get(rid) or (None, 'unrecorded'))[1])
    return result


def live_run(db, sessions, sids, wire=None):
    """What went out live: every recording (of `wire`, when named) that sent
    requests, over its whole duration."""
    outputs = live_outputs(db, sids)
    entries, wires = {}, set()
    for sid, slot in outputs.items():
        session = sessions[sid]
        duration = (session['ended_at'] or 0) - (session['started_at'] or 0)
        for arm, arm_wires in slot['wires'].items():
            if wire is not None and wire not in arm_wires:
                continue
            wires |= arm_wires
            predictions = [scoring.prediction(p) for a, p, w in slot['outputs'].values() if a == arm and (wire is None or w == wire)]
            entries[(sid, arm)] = {'predictions': [p for p in predictions if p], 'support': [(0.0, duration)]}
    return {'name': 'live' + (':' + wire if wire else ''), 'kind': 'live', 'wires': sorted(wires), 'entries': entries}


def replay_databases(values):
    files = []
    for value in values or [REPLAY_DBS]:
        files += sorted(glob.glob(path(value))) if any(c in value for c in '*?[') else [path(value)]
    return [f for f in files if not f.endswith(('-shm', '-wal'))]


def resolve_jobs(spec, databases, manifest):
    """[(database, job)] a JOBS argument names."""
    candidate = (manifest.get('candidates') or {}).get(spec)
    patterns = candidate['jobs'] if candidate else [p.strip() for p in spec.split(',') if p.strip()]
    if candidate and candidate.get('replay_db'):
        databases = replay_databases([candidate['replay_db']])
    found = []
    for file in databases:
        db = open_ro(file)
        try:
            jobs = [r[0] for r in db.execute('SELECT DISTINCT job FROM replay_results ORDER BY job')]
        except Exception:
            jobs = []
        db.close()
        found += [(file, job) for job in jobs if any(fnmatch.fnmatchcase(job, p) for p in patterns)]
    return found


def job_catalog(databases):
    """[{'file','job','wire','questions','requests','repeat'}] of every replay
    job: its wire (the replayed state's schema), question versions, recorded
    request ids and whether it holds only repeatability re-asks."""
    found = []
    for file in databases:
        db = open_ro(file)
        try:
            rows = db.execute('SELECT job,recorded_request_id,question_version,source,result FROM replay_results').fetchall()
        except Exception:
            rows = []
        db.close()
        jobs = {}
        for job, rid, questions, source, raw in rows:
            entry = jobs.setdefault(job, {'file': file, 'job': job, 'wires': set(), 'questions': set(), 'requests': set(),
                                          'sources': set()})
            entry['requests'].add(rid)
            entry['questions'].add(questions)
            entry['sources'].add(source)
            try:
                schema = ((((json.loads(raw) or {}).get('diagnostics') or {}).get('request') or {}).get('state') or {}).get('schema')
            except Exception:
                schema = None
            if schema:
                entry['wires'].add(schema)
        for entry in jobs.values():
            wires = entry.pop('wires')
            entry['wire'] = next(iter(wires)) if len(wires) == 1 else None
            entry['repeat'] = entry.pop('sources') == {'repeat'}
            found.append(entry)
    return found


def baseline_jobs(wire, candidate_jobs, databases):
    """The replay of `wire` under the candidate's conditions: every job of
    that wire (not a repeatability re-ask, not a candidate job) asked with the
    candidate's question set that shares its recorded requests. Returns
    (jobs, covered share of the candidate's requests)."""
    catalog = job_catalog(databases)
    chosen = {(e['file'], e['job']) for e in catalog if (e['file'], e['job']) in set(candidate_jobs)}
    candidate = [e for e in catalog if (e['file'], e['job']) in chosen]
    requests = set().union(*(e['requests'] for e in candidate)) if candidate else set()
    questions = set().union(*(e['questions'] for e in candidate)) if candidate else set()
    jobs = [e for e in catalog if e['wire'] == wire and not e['repeat'] and (e['file'], e['job']) not in chosen
            and e['questions'] <= questions and e['requests'] & requests]
    covered = set().union(*(e['requests'] for e in jobs)) & requests if jobs else set()
    return jobs, (len(covered) / len(requests) if requests else 0.0)


def _latest_visual(records):
    visual = [r for r in records if r.get('source_kind') != 'ha.observation' and (r.get('objects') or r.get('pose_instances'))]
    latest = max(visual, key=lambda r: r['observed_at']) if visual else None
    return (latest['observed_at'], latest['valid_until']) if latest else (None, None)


def replay_run(db, sessions, spec, databases, manifest):
    """A replay candidate: each replayed request's answer, timed as live timed
    it (the live answer's start, end, arrival and validity when that request
    was answered live; else the recorded evidence's own validity and the
    session's median live latency)."""
    jobs = resolve_jobs(spec, databases, manifest)
    if not jobs:
        raise SystemExit('no replay job matches %r' % spec)
    rows = {}
    shape = set()
    for file, job in sorted(jobs, key=lambda fj: fj[1]):
        rdb = open_ro(file)
        for rid, arm, cutoff, status, raw in rdb.execute(
                'SELECT recorded_request_id,arm,cutoff,status,result FROM replay_results WHERE job=?', (job,)):
            result = json.loads(raw) if raw else None
            rows[rid] = (job, arm, cutoff, result.get('status') if isinstance(result, dict) else status, result)
            diagnostics = (result or {}).get('diagnostics') or {}
            shape.add(((diagnostics.get('request') or {}).get('state') or {}).get('schema'))
        rdb.close()
    owners = {}
    ids = list(rows)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i+500]
        for rid, sid in db.execute('SELECT id,session_id FROM lab_reasoning_requests WHERE id IN (%s)' % ','.join('?' * len(chunk)), chunk):
            owners[rid] = sid
    sids = sorted({s for s in owners.values() if s in sessions})
    live = live_outputs(db, sids)
    index = request_index(db, sids)
    latency = {}
    for sid in sids:
        start = sessions[sid]['started_at']
        values = [p['available_at'] + start - index[rid][2] for rid, (_, p, _) in live[sid]['outputs'].items()
                  if rid in index and isinstance(p, dict) and scoring.finite(p.get('available_at')) and scoring.finite(index[rid][2])]
        latency[sid] = statistics.median(values) if values else 3.0
    entries = {}
    unmatched = 0
    for rid, (job, arm, cutoff, status, result) in rows.items():
        sid = owners.get(rid)
        if sid not in sessions:
            unmatched += 1
            continue
        start = sessions[sid]['started_at']
        entry = entries.setdefault((sid, arm), {'predictions': [], 'cutoffs': []})
        entry['cutoffs'].append(cutoff - start)
        if status not in ('ready', 'abstain') or not isinstance(result, dict):
            continue  # no answer: the time stays in support as no_answer
        judgment = result.get('judgment') or {}
        activity, probabilities = judgment.get('activity'), judgment.get('probabilities') or {}
        recorded = live[sid]['outputs'].get(rid)
        timing = recorded[1] if recorded and isinstance(recorded[1], dict) else None
        if timing is None or not all(scoring.finite(timing.get(k)) for k in ('start', 'end', 'available_at', 'valid_until')):
            (raw,) = db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?', (rid,)).fetchone()
            observed, expiry = _latest_visual(json.loads(raw).get('records') or [])
            observed, expiry = (observed, expiry) if observed is not None else (cutoff, cutoff)
            timing = {'start': observed - start, 'end': expiry - start, 'valid_until': expiry - start,
                      'available_at': cutoff - start + latency[sid]}
        entry['predictions'].append(scoring.prediction({
            'start': timing['start'], 'end': timing['end'], 'available_at': timing['available_at'],
            'valid_until': timing['valid_until'], 'activities': [] if status == 'abstain' else [activity],
            'confidence': probabilities.get(activity, judgment.get('confidence')), 'mode': 'live'}))
    for entry in entries.values():
        entry['support'] = scoring.spans(entry.pop('cutoffs'))
        entry['predictions'] = [p for p in entry['predictions'] if p]
    return {'name': spec, 'kind': 'replay', 'jobs': sorted({j for _, j in jobs}), 'wires': sorted(w for w in shape if w),
            'entries': entries, 'unmatched_requests': unmatched}


def make_run(spec, db, sessions, sids, databases, manifest, wire=None):
    if spec == 'live':
        return live_run(db, sessions, sids, wire)
    return replay_run(db, sessions, spec, databases, manifest)


def baseline_run(args, db, sessions, sids, databases, manifest, candidate):
    """The gate's baseline (see the module docstring): explicit --baseline,
    else the manifest's 'baseline', else the replay of --baseline-wire under
    the candidate's conditions. Never live by default."""
    spec = args.baseline or manifest.get('baseline')
    if spec:
        return make_run(spec, db, sessions, sids, databases, manifest, args.baseline_wire)
    if not args.baseline_wire:
        raise SystemExit('gate needs a baseline: --baseline-wire W (a replay of wire W at the candidate\'s cutoffs), '
                         '--baseline JOBS, a manifest "baseline", or --baseline live')
    jobs, covered = baseline_jobs(args.baseline_wire, [(f, j) for f, j in resolve_jobs(args.candidate, databases, manifest)],
                                  databases)
    if not jobs:
        raise SystemExit('no replay of %s at the candidate\'s cutoffs with its questions: run '
                         '"lab_regression.py replay --wire %s --questions Q --job NAME" first, or name --baseline'
                         % (args.baseline_wire, args.baseline_wire))
    run = replay_run(db, sessions, ','.join(sorted({e['job'] for e in jobs})), sorted({e['file'] for e in jobs}), {})
    run['baseline_coverage'] = round(covered, 4)
    return run


def noise_runs(specs, db, sessions, databases, manifest):
    """One replay run per repeat job the --noise specs name."""
    runs = []
    for spec in specs or []:
        for file, job in resolve_jobs(spec, databases, manifest):
            runs.append(replay_run(db, sessions, job, [file], {}))
    return runs


# ---- Scoring and the gate ----

def labels_by_session(intervals):
    result = defaultdict(list)
    for x in intervals:
        result[x['session_id']].append(x)
    return result


def score_run(run, intervals, support=None):
    """{arm: {activity: metrics}}; support: {(sid, arm): spans} to restrict to."""
    labels = labels_by_session(intervals)
    rows = defaultdict(dict)
    for (sid, arm), entry in sorted(run['entries'].items()):
        if sid not in labels:
            continue
        spans = entry['support'] if support is None else support.get((sid, arm))
        if spans is None:
            continue
        scoring.accumulate(labels[sid], entry['predictions'], support=spans, session_id=sid, rows=rows[arm])
    return scoring.report(rows)


def common_support(baseline, candidate):
    return {key: scoring.intersect(baseline['entries'][key]['support'], candidate['entries'][key]['support'])
            for key in set(baseline['entries']) & set(candidate['entries'])}


NOISE_METRICS = ('exact_at_coverage', 'coverage', 'false_confident_s')


def noise_bands(groups, intervals, support):
    """{(arm, activity): {'runs': n, metric: band, 'false_positive_s': {answer: band}}}:
    per group of runs (identical packets, re-asked), the spread (max - min) of
    each metric over the group's common time; the largest spread of any group."""
    bands = {}
    for runs in groups:
        if len(runs) < 2:
            continue
        shared = dict(support)
        for run in runs:
            shared = {k: scoring.intersect(v, run['entries'][k]['support']) for k, v in shared.items() if k in run['entries']}
        reports = [score_run(run, intervals, shared) for run in runs]
        cells = {(arm, key) for report in reports for arm, rows in report.items() for key in rows}
        for arm, key in cells:
            values = [r.get(arm, {}).get(key) for r in reports]
            values = [v for v in values if v is not None]
            if len(values) < 2:
                continue
            band = bands.setdefault((arm, key), {'runs': 0, 'false_positive_s': {}})
            band['runs'] = max(band['runs'], len(values))
            for metric in NOISE_METRICS:
                numbers = [v[metric] for v in values if v.get(metric) is not None]
                if len(numbers) >= 2:
                    band[metric] = max(band.get(metric, 0.0), max(numbers) - min(numbers))
            for answer in {a for v in values for a in v['false_positive_s']}:
                numbers = [v['false_positive_s'].get(answer, 0.0) for v in values]
                band['false_positive_s'][answer] = max(band['false_positive_s'].get(answer, 0.0), max(numbers) - min(numbers))
    return bands


def noise_groups(baseline, candidate, runs):
    """Repeat runs grouped with the side of the same wire."""
    base, cand = [baseline], [candidate]
    for run in runs:
        wire = run['wires'][0] if len(run.get('wires') or ()) == 1 else None
        if wire is not None and wire in (candidate.get('wires') or ()) and wire not in (baseline.get('wires') or ()):
            cand.append(run)
        elif wire is not None and wire in (baseline.get('wires') or ()):
            base.append(run)
    return [base, cand]


def gate(baseline, candidate, intervals, tolerances, noise=None):
    """(verdict rows, overall pass) on the two runs' common time. noise:
    noise_bands(...) or None; a tolerance broken by no more than the noise
    band of that activity, arm and metric is within_noise, not a failure."""
    support = common_support(baseline, candidate)
    before, after = score_run(baseline, intervals, support), score_run(candidate, intervals, support)
    t = tolerances
    rows = []
    for arm in sorted(set(before) | set(after)):
        for key in sorted(set(before.get(arm, {})) | set(after.get(arm, {})),
                          key=lambda k: ACTIVITIES.index(k) if k in ACTIVITIES else 99):
            b, c = before.get(arm, {}).get(key), after.get(arm, {}).get(key)
            row = {'activity': key, 'arm': arm, 'baseline': b, 'candidate': c, 'failures': [], 'within_noise': [],
                   'labelled_s': (b or c)['labelled_s'], 'sessions': (b or c)['sessions']}
            band = (noise or {}).get((arm, key))
            if band is not None:
                row['noise'] = band

            def check(text, delta, tolerance, spread):
                # A broken tolerance: within noise when the spread of the
                # re-asked runs is at least as large as the difference.
                if spread is not None and delta <= max(tolerance, spread) + 1e-9:
                    row['within_noise'].append('%s (noise band %.2f over %d runs)' % (text, spread, band['runs']))
                else:
                    row['failures'].append(text)
            row['low_confidence'] = row['sessions'] < 2
            if b is None or c is None or row['labelled_s'] < t['min_labelled_s']:
                row['verdict'] = 'no data'
                rows.append(row)
                continue
            spread = (lambda metric: band.get(metric) if band else None)
            if (b['covered_s'] >= t['min_covered_s'] and c['covered_s'] >= t['min_covered_s']
                    and c['exact_at_coverage'] < b['exact_at_coverage'] - t['exact_drop_max']):
                check('exact@coverage %.2f -> %.2f' % (b['exact_at_coverage'], c['exact_at_coverage']),
                      b['exact_at_coverage'] - c['exact_at_coverage'], t['exact_drop_max'], spread('exact_at_coverage'))
            if c['coverage'] < b['coverage'] - t['coverage_drop_max']:
                check('coverage %.2f -> %.2f' % (b['coverage'], c['coverage']), b['coverage'] - c['coverage'],
                      t['coverage_drop_max'], spread('coverage'))
            if c['false_confident_s'] > b['false_confident_s'] + t['false_confident_increase_max_s']:
                check('false-confident %.1fs -> %.1fs' % (b['false_confident_s'], c['false_confident_s']),
                      c['false_confident_s'] - b['false_confident_s'], t['false_confident_increase_max_s'],
                      spread('false_confident_s'))
            for answer, seconds in sorted(c['false_positive_s'].items()):
                was = b['false_positive_s'].get(answer, 0.0)
                if seconds > was + t['false_positive_increase_max_s']:
                    check('%s answered in %s intervals %.1fs -> %.1fs' % (answer, key, was, seconds), seconds - was,
                          t['false_positive_increase_max_s'],
                          band['false_positive_s'].get(answer, 0.0) if band else None)
            row['verdict'] = 'FAIL' if row['failures'] else 'within_noise' if row['within_noise'] else 'pass'
            rows.append(row)
    gated = [r for r in rows if r['verdict'] != 'no data']
    tested = {r['activity'] for r in gated}
    untested = [k for k in coverage(intervals) if k not in tested]
    return rows, bool(gated) and all(r['verdict'] in ('pass', 'within_noise') for r in gated), support, untested


def _pct(v):
    return '   -' if v is None else '%4.0f%%' % (100 * v)


def print_scores(run, report, title=None):
    print(title or 'Run %s (%s%s)' % (run['name'], run['kind'], ', wires ' + ','.join(run['wires']) if run.get('wires') else ''))
    print('  %-14s %-3s %4s %8s %6s %7s %6s %6s %7s %8s %s' % ('activity', 'arm', 'sess', 'label_s', 'cover', 'exact@c', 'abst',
          'noans', 'fconf_s', 'ttfc_med', 'false positives (s) / confusion'))
    for arm, rows in report.items():
        for key, m in sorted(rows.items(), key=lambda kv: ACTIVITIES.index(kv[0]) if kv[0] in ACTIVITIES else 99):
            fp = ', '.join('%s %.0f' % kv for kv in m['false_positive_s'].items()) or '-'
            print('  %-14s %-3s %4d %8.1f %6s %7s %6s %6s %7.1f %8s %s%s' % (
                key, arm, m['sessions'], m['labelled_s'], _pct(m['coverage']), _pct(m['exact_at_coverage']),
                _pct(m['abstention']), _pct(m['no_answer']), m['false_confident_s'],
                '-' if m['ttfc_median_s'] is None else '%.1fs' % m['ttfc_median_s'],
                fp, '' if not m['missed_boundaries'] else '  (%d/%d boundaries never correct)' % (m['missed_boundaries'], m['boundaries'])))


def verdict(rows, passed, untested, tolerances):
    """(word, exit status): FAIL 1, nothing comparable 3, INCOMPLETE 4, PASS 0."""
    if not any(r['verdict'] != 'no data' for r in rows):
        return 'NO COMPARABLE DATA', 3
    if not passed:
        return 'FAIL', 1
    if untested and tolerances['require_all_activities']:
        return 'INCOMPLETE (passed where tested; untested: %s)' % ', '.join(untested), 4
    noisy = sorted({'%s/%s' % (r['activity'], r['arm']) for r in rows if r['verdict'] == 'within_noise'})
    if noisy:
        return 'PASS (within measured noise: %s)' % ', '.join(noisy), 0
    return 'PASS', 0


def print_gate(rows, passed, baseline, candidate, tolerances, untested=(), noise=None):
    print('Gate: candidate %s vs baseline %s, on their common time' % (candidate['name'], baseline['name']))
    if baseline['kind'] == 'live':
        print('Baseline is LIVE: its cadence and JEV run-to-run variance differ from the replayed candidate; '
              'prefer --baseline-wire W (a replay under identical conditions).')
    elif baseline.get('baseline_coverage') is not None:
        print('Baseline replay of wire(s) %s covers %.0f%% of the candidate\'s requests' % (
            ','.join(baseline['wires']) or '?', 100 * baseline['baseline_coverage']))
    if noise is None:
        print('Noise: not measured (one replay per side); pass --noise with repeat jobs to separate JEV variance.')
    else:
        print('Noise: %d activity/arm cells measured from repeat runs' % len(noise))
    print('Tolerances: ' + ', '.join('%s=%s' % kv for kv in sorted(tolerances.items())))
    print('  %-14s %-3s %4s %8s %15s %13s %13s  %s' % ('activity', 'arm', 'sess', 'label_s', 'exact@cov b->c', 'cover b->c',
          'fconf_s b->c', 'verdict'))
    for r in rows:
        b, c = r['baseline'] or {}, r['candidate'] or {}
        pair = lambda k, f: '%s->%s' % (f(b.get(k)), f(c.get(k)))
        pct = lambda v: '-' if v is None else '%.0f%%' % (100 * v)
        sec = lambda v: '-' if v is None else '%.1f' % v
        print('  %-14s %-3s %4d %8.1f %15s %13s %13s  %s%s%s%s' % (
            r['activity'], r['arm'], r['sessions'], r['labelled_s'], pair('exact_at_coverage', pct), pair('coverage', pct),
            pair('false_confident_s', sec), r['verdict'], ' LOW (one session)' if r['low_confidence'] and r['verdict'] != 'no data' else '',
            ('  ' + '; '.join(r['failures'])) if r['failures'] else '',
            ('  within noise: ' + '; '.join(r.get('within_noise') or [])) if r.get('within_noise') else ''))
    verdicts = defaultdict(set)
    for r in rows:
        verdicts[r['activity']].add(r['verdict'])
    print('Per activity: ' + ', '.join('%s %s' % (k, 'FAIL' if 'FAIL' in v else 'within_noise' if 'within_noise' in v
                                                  else 'pass' if 'pass' in v else 'no data')
                                       for k, v in verdicts.items()))
    if untested:
        print('Untested (no common time with labels): ' + ', '.join(untested))
    print('GATE %s' % verdict(rows, passed, untested, tolerances)[0])


def tolerances_from(manifest, file):
    result = dict(GATE_DEFAULTS)
    for source in (manifest.get('gate') or {}, json.loads(Path(path(file)).read_text(encoding='utf-8')) if file else {}):
        unknown = set(source) - set(GATE_DEFAULTS)
        if unknown:
            raise SystemExit('unknown gate tolerance: ' + ', '.join(sorted(unknown)))
        for key, value in source.items():
            if key == 'require_all_activities':
                if type(value) is not bool:
                    raise SystemExit('gate tolerance require_all_activities must be true or false')
                result[key] = value
                continue
            if not scoring.finite(value) or value < 0:
                raise SystemExit('gate tolerance %s must be a non-negative number' % key)
            result[key] = float(value)
    return result


# ---- Bounded replay of the whole regression set ----

def replay(args, sids, hooks=None):
    """Drive jev_replay_calls.py over every regression session for one
    candidate. A dry run over every session first (no admission, no transport:
    the packets are built and checked, cached answers counted); the paid run
    only with --yes, only when the whole set fits the call cap."""
    import jev_replay_calls as calls
    if not 0 < args.max_calls <= min(REPLAY_CALL_CAP, calls.HARD_CALL_CAP):
        raise SystemExit('max-calls must be 1..%d' % min(REPLAY_CALL_CAP, calls.HARD_CALL_CAP))
    if Path(path(args.replay_db)).resolve() == Path(path(args.snapshot)).resolve() or \
            Path(path(args.snapshot)).resolve() == Path(path(LEDGER)).resolve():
        raise SystemExit('replay needs a ledger snapshot copy and a separate replay database')
    job = args.job or 'regression-%s-%s-%s' % (args.wire or 'config', args.questions, args.facts_key or 'default')
    if getattr(args, 'repeat', 0):
        if not args.job:
            raise SystemExit('a repeat replay needs its own --job name (one job per repeat run)')
        print('Repeat run: every cached packet of the set is re-asked once (up to %d per session) into job %s'
              % (args.repeat, job))

    def namespace(sid, dry_run, max_calls):
        return argparse.Namespace(ledger=path(args.snapshot), replay_db=path(args.replay_db), config=path(args.config),
                                  session=sid, questions=args.questions, wire=args.wire, desk_seat=args.desk_seat,
                                  arms=args.arms, from_s=None, to_s=None, max_calls=max_calls, dry_run=dry_run,
                                  repeat=getattr(args, 'repeat', 0) or 0, job=job)
    plan = []
    for sid in sids:
        try:
            counts = calls.run(namespace(sid, True, 0), hooks)
        except (ValueError, SystemExit) as error:
            counts = {'error': str(error)[:120] or type(error).__name__}
        plan.append((sid, counts))
    if any('error' in c for _, c in plan):
        for sid, c in plan:
            if 'error' in c:
                print('  %s: dry run refused: %s' % (sid[:8], c['error']))
    to_call = sum(c.get('dry_run_built', 0) for _, c in plan)
    tokens = sum(c.get('estimated_tokens', 0) for _, c in plan)
    print('Replay plan: job %s, wire %s, questions %s, facts key %s' % (job, args.wire or "the config's", args.questions,
                                                                         args.facts_key or 'default'))
    print('  %-8s %8s %7s %6s %7s %12s' % ('session', 'requests', 'cached', 'calls', 'invalid', 'est_tokens'))
    for sid, c in plan:
        print('  %-8s %8d %7d %6d %7d %12d' % (sid[:8], c.get('requests', 0), c.get('cache_hits', 0), c.get('dry_run_built', 0),
                                              c.get('failed', 0), c.get('estimated_tokens', 0)))
    cost = ' (~$%.2f at $%.2f per million input tokens)' % (tokens * args.usd_per_mtok / 1e6, args.usd_per_mtok) if args.usd_per_mtok else ''
    print('Total: %d paid calls, ~%d input tokens%s; cap %d' % (to_call, tokens, cost, args.max_calls))
    if args.facts_key:
        print('Note: the facts key sets cadence and answer validity, not the packet: replay at the recorded cutoffs '
              'does not change with it (jev_cadence_replay.py measures cadence).')
    if args.dry_run or not args.yes:
        print('Dry run only: nothing was admitted or sent.' if args.dry_run else 'Not run: pass --yes to make these calls.')
        return {'job': job, 'planned_calls': to_call, 'estimated_tokens': tokens, 'ran': False}
    if to_call > args.max_calls:
        raise SystemExit('refused: %d calls exceed the cap of %d' % (to_call, args.max_calls))
    made = 0
    if any('error' in c for _, c in plan):
        raise SystemExit('refused: the dry run failed for some sessions')
    for sid, _ in plan:
        counts = calls.run(namespace(sid, False, args.max_calls - made), hooks)
        made += counts.get('calls', 0)
        print(json.dumps(counts, sort_keys=True))
        if counts.get('stopped'):
            break
    return {'job': job, 'planned_calls': to_call, 'calls': made, 'ran': True}


# ---- Stored automatic results of one session ----

class LedgerStore:
    """The read surface lab_scoring.session_results needs, over a read-only ledger."""

    def __init__(self, db):
        import time
        self.db, self.lock, self.clock = db, threading.RLock(), time.time

    def get_session(self, sid):
        return dict(self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone())

    def list_annotations(self, sid):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM annotations WHERE session_id=? AND deleted=0', (sid,))]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('set', 'score', 'gate', 'replay', 'results'))
    parser.add_argument('session', nargs='?', help='results: a session id prefix')
    parser.add_argument('--ledger', default=LEDGER, help='the live ledger, opened read-only')
    parser.add_argument('--manifest', default=MANIFEST)
    parser.add_argument('--replay-db', action='append', default=None, help='replay database(s) or a glob (default %s)' % REPLAY_DBS)
    parser.add_argument('--run', default='live')
    parser.add_argument('--wire', default=None, help='score: live answers of this wire only; replay: the wire to replay')
    parser.add_argument('--baseline', default=None, help="live or JOBS; default: the manifest's 'baseline', else "
                        'the replay of --baseline-wire at the candidate\'s cutoffs')
    parser.add_argument('--baseline-wire', default=None)
    parser.add_argument('--candidate', default=None)
    parser.add_argument('--noise', action='append', default=None, help='repeat replay jobs (JOBS; one run per job)')
    parser.add_argument('--repeat', type=int, default=0, help='replay: re-ask up to N cached packets per session once')
    parser.add_argument('--tolerances', default=None, help='JSON object overriding GATE_DEFAULTS')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--config', default=None, help='replay: the private JEV config')
    parser.add_argument('--snapshot', default=None, help='replay: a read-only copy of the ledger')
    parser.add_argument('--questions', default=None)
    parser.add_argument('--desk-seat', default=None)
    parser.add_argument('--facts-key', default=None)
    parser.add_argument('--arms', default='a,b')
    parser.add_argument('--job', default=None)
    parser.add_argument('--max-calls', type=int, default=REPLAY_CALL_CAP)
    parser.add_argument('--usd-per-mtok', type=float, default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args(argv)
    db = open_ro(args.ledger)
    try:
        return command(args, db)
    finally:
        db.close()


def command(args, db):
    manifest = load_manifest(args.manifest)
    sessions, intervals, excluded = gather(db, manifest)
    sids = sorted({x['session_id'] for x in intervals}, key=lambda s: sessions[s]['started_at'] or 0)
    if args.command == 'set':
        table = coverage(intervals)
        if args.json:
            print(json.dumps({'intervals': intervals, 'excluded': excluded, 'coverage': table}, indent=1, sort_keys=True))
        else:
            print_set(sessions, intervals, excluded, table)
        return 0
    if args.command == 'results':
        matches = [s for s in sessions if args.session and s.startswith(args.session)]
        if len(matches) != 1:
            raise SystemExit('one session prefix required')
        row = db.execute('SELECT payload FROM events WHERE session_id=? AND kind=? ORDER BY id DESC LIMIT 1',
                         (matches[0], scoring.EVENT_KIND)).fetchone()
        value = json.loads(row[0]) if row else {**scoring.session_results(LedgerStore(db), matches[0]), 'stored': False}
        print(json.dumps(value, indent=1, sort_keys=True))
        return 0
    databases = replay_databases(args.replay_db)
    if args.command == 'score':
        run = make_run(args.run, db, sessions, sids, databases, manifest, args.wire)
        report = score_run(run, intervals)
        if args.json:
            print(json.dumps({'run': {k: v for k, v in run.items() if k != 'entries'}, 'report': report}, indent=1, sort_keys=True))
        else:
            print_scores(run, report)
        return 0
    if args.command == 'gate':
        if not args.candidate:
            raise SystemExit('--candidate required')
        tolerances = tolerances_from(manifest, args.tolerances)
        candidate = make_run(args.candidate, db, sessions, sids, databases, manifest)
        baseline = baseline_run(args, db, sessions, sids, databases, manifest, candidate)
        noise = None
        repeats = noise_runs(args.noise, db, sessions, databases, manifest)
        if repeats:
            noise = noise_bands(noise_groups(baseline, candidate, repeats), intervals, common_support(baseline, candidate))
        rows, passed, support, untested = gate(baseline, candidate, intervals, tolerances, noise)
        word, status = verdict(rows, passed, untested, tolerances)
        if args.json:
            print(json.dumps({'rows': rows, 'pass': passed, 'untested': untested, 'verdict': word, 'tolerances': tolerances,
                              'baseline': {k: v for k, v in baseline.items() if k != 'entries'},
                              'noise_runs': [r['name'] for r in repeats]}, indent=1, sort_keys=True, default=list))
        else:
            print('Common time: ' + ', '.join('%s/%s %.0fs' % (sid[:8], arm, sum(b - a for a, b in spans))
                                              for (sid, arm), spans in sorted(support.items())))
            print_gate(rows, passed, baseline, candidate, tolerances, untested, noise)
        return status
    if args.command == 'replay':
        if not (args.config and args.snapshot and args.replay_db and len(args.replay_db) == 1 and args.questions):
            raise SystemExit('replay needs --config, --snapshot, one --replay-db and --questions')
        args.replay_db = args.replay_db[0]
        replay(args, sids)
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

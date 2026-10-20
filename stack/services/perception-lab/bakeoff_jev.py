"""Bake-off arm A: JEV answers (recorded live, or replayed) as a tick-record JSONL.

Offline operator tool. The ledger and the replay database are opened
read-only (?mode=ro); nothing is written to either, only the tick file
(--out). No network, no JEV call: every number was stored earlier by the live
path (reasoning.output events) or by jev_replay_calls.py (replay_results).

Privacy: the tick file keeps JEV's probabilities locally, as the ledger does.
It carries session ids, request ids and wire names, nothing from the packets.

Two sources, one tick-record contract (lab-ticks-1, see bakeoff_vjepa):
  --live               recorded reasoning.output events (mode live) of the sessions.
                       probabilities = judgment.probabilities with insufficient_evidence
                       renamed abstain, missing classes 0; t = prediction.start,
                       available_at = prediction.available_at. closed_at mirrors
                       jev_smoothing.facts_bound: with a finite prediction.valid_through,
                       max(valid_until, min(valid_through, the reasoning.validity
                       closure naming the request, if any)); else prediction.valid_until.
                       JEV never yields 'empty' (a cloud answer): an 'empty' key is dropped.
                       Local code-gate answers (reasoning.gate, mode live) are emitted
                       too: occupancy_vacant = empty 1.0 (local empty evidence),
                       occupancy_unknown = abstain 1.0; closed_at = prediction.valid_until.
  --replay-db F --job J  replay_results rows of job J joined to lab_reasoning_requests
                       for the session and cutoff (lab_regression.replay_run's join).
                       probabilities from result.judgment.probabilities; status abstain
                       with no probabilities = abstain 1.0; t = cutoff - started_at,
                       available_at = t + the session's median live latency (default
                       DEFAULT_LATENCY_S); closed_at = available_at + REPLAY_HOLD_S,
                       because a replay never learns the live facts-key closures.
Header: consumes_qwen is true iff the session's experiment.activated
source_policy.qwen_scene grants an emitted arm (default true when not
recorded); the run name carries the wire (diagnostics.request.state.schema,
else the recorded wire_version); arm 'A', or 'reference' with --reference
(den-facts-7 rows are a reference, not a candidate).

Usage:
  python bakeoff_jev.py --ledger DB --session PREFIX... --out ticks.jsonl --live [--reference]
  python bakeoff_jev.py --ledger DB --session PREFIX... --out ticks.jsonl --replay-db FILE --job NAME [--reference]
"""
import argparse
import json
import os
import statistics
import sys

import bakeoff_vjepa as ticks
import lab_regression

ARM = 'A'
REFERENCE_ARM = 'reference'
RUN = 'jev'
ABSTAIN_SOURCE = 'insufficient_evidence'   # JEV's own abstention class
GATE_CLASSES = {'occupancy_vacant': 'empty', 'occupancy_unknown': 'abstain'}   # live_reasoning.local_abstain reasons
DEFAULT_LATENCY_S = 3.0                    # a session with no live answers
REPLAY_HOLD_S = 12.0                       # a replay's closure: the held-view maximum
finite = ticks.finite


def probabilities(value, status=None):
    """A CLASSES distribution from JEV's probabilities; abstain 1.0 when there are none."""
    if not isinstance(value, dict) or not value:
        return ticks.normalise({'abstain': 1.0})
    masses = {}
    for key, p in value.items():
        name = 'abstain' if key == ABSTAIN_SOURCE else key
        if name in ticks.CLASSES and name != 'empty' and finite(p):   # 'empty' is local evidence only
            masses[name] = masses.get(name, 0.0) + p
    if status == 'abstain' and masses.get('abstain', 0.0) <= 0:
        return ticks.normalise({'abstain': 1.0})
    return ticks.normalise(masses)


def wire_name(result):
    diagnostics = (result or {}).get('diagnostics') if isinstance(result, dict) else None
    return ((((diagnostics or {}).get('request') or {}).get('state') or {}).get('schema'))


def qwen_grants(db, sid):
    """{arm: granted?} from the latest experiment.activated; absent means granted."""
    row = db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id DESC LIMIT 1",
                     (sid,)).fetchone()
    policy = ((json.loads(row[0]) if row else {}).get('source_policy') or {}).get('qwen_scene') or {}
    return {arm: policy.get(arm, 'granted') == 'granted' for arm in ('a', 'b')}


def closures(db, sid):
    """{request_id: valid_through (session seconds)} of recorded validity closures."""
    found = {}
    for (raw,) in db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.validity' ORDER BY id", (sid,)):
        value = json.loads(raw)
        if isinstance(value, dict) and isinstance(value.get('request_id'), str) and finite(value.get('valid_through')):
            found[value['request_id']] = float(value['valid_through'])
    return found


def live_closed_at(prediction, closure=None):
    """closed_at of one live answer, as jev_smoothing.facts_bound bounds it: a
    finite valid_through is cut by a recorded closure, never below the strict
    valid_until; without valid_through (older outputs) the strict valid_until.
    The next answer's arrival and the hold maximum are lab_eval's held view."""
    through = prediction.get('valid_through')
    if not finite(through):
        return prediction['valid_until']
    end = min(through, closure) if finite(closure) else through
    return max(prediction['valid_until'], end)


def has_times(prediction, keys=('start', 'available_at', 'valid_until')):
    return isinstance(prediction, dict) and all(finite(prediction.get(k)) for k in keys)


def gate_ticks(db, sid, counts):
    """Emissions of the session's local code-gate answers (reasoning.gate, mode live)."""
    records = []
    for (raw,) in db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.gate' ORDER BY id", (sid,)):
        value = json.loads(raw)
        prediction = value.get('prediction') if isinstance(value, dict) else None
        name = GATE_CLASSES.get(value.get('reason')) if isinstance(value, dict) else None
        if (name is None or value.get('mode', 'live') != 'live' or value.get('arm') not in ('a', 'b')
                or not has_times(prediction)):
            counts['skipped'] += 1
            continue
        counts['gated'] += 1
        records.append(ticks.emission(sid, value['arm'], prediction['start'], prediction['available_at'],
                                      ticks.normalise({name: 1.0}), closed_at=prediction['valid_until']))
    return records


def live_ticks(db, sid):
    """(emissions, wires, counts) from the session's recorded live outputs and local gate answers."""
    closed = closures(db, sid)
    slot = lab_regression.live_outputs(db, [sid])[sid]
    records, wires, counts = [], set(), {'emitted': 0, 'gated': 0, 'skipped': 0}
    for (raw,) in db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.output' ORDER BY id", (sid,)):
        value = json.loads(raw)
        prediction = value.get('prediction') if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get('mode', 'live') != 'live' or value.get('arm') not in ('a', 'b')
                or not has_times(prediction)):
            counts['skipped'] += 1
            continue
        rid = value.get('request_id')
        judgment = value.get('judgment') or {}
        counts['emitted'] += 1
        wires.add((slot['outputs'].get(rid) or (None, None, 'unrecorded'))[2])
        key = value.get('facts_key')
        records.append(ticks.emission(sid, value['arm'], prediction['start'], prediction['available_at'],
                                      probabilities(judgment.get('probabilities'), value.get('provider_status')),
                                      closed_at=live_closed_at(prediction, closed.get(rid)),
                                      facts_key=key if isinstance(key, str) else None))
    records += gate_ticks(db, sid, counts)
    return records, wires, counts


def median_latency(db, sid, started_at):
    """The session's median live latency (answer available - cutoff), as lab_regression.replay_run computes it."""
    live = lab_regression.live_outputs(db, [sid])[sid]
    index = lab_regression.request_index(db, [sid])
    values = [p['available_at'] + started_at - index[rid][2] for rid, (_, p, _) in live['outputs'].items()
              if rid in index and isinstance(p, dict) and finite(p.get('available_at')) and finite(index[rid][2])]
    return statistics.median(values) if values else DEFAULT_LATENCY_S


def replay_rows(rdb, job):
    """[(recorded_request_id, arm, cutoff, status, result)] of one replay job."""
    rows = []
    for rid, arm, cutoff, status, raw in rdb.execute(
            'SELECT recorded_request_id,arm,cutoff,status,result FROM replay_results WHERE job=? ORDER BY cutoff', (job,)):
        result = json.loads(raw) if raw else None
        rows.append((rid, arm, cutoff, result.get('status') if isinstance(result, dict) else status, result))
    return rows


def replay_ticks(db, rows, sessions):
    """(emissions, wires, counts per session) of replay rows that belong to the sessions."""
    owners = {}
    ids = [rid for rid, *_ in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for rid, sid in db.execute('SELECT id,session_id FROM lab_reasoning_requests WHERE id IN (%s)' % ','.join('?' * len(chunk)), chunk):
            owners[rid] = sid
    latency = {sid: median_latency(db, sid, started_at) for sid, started_at in sessions.items()}
    records, wires = [], set()
    counts = {sid: {'emitted': 0, 'skipped': 0} for sid in sessions}
    for rid, arm, cutoff, status, result in rows:
        sid = owners.get(rid)
        if sid not in sessions:
            continue
        if status not in ('ready', 'abstain') or not isinstance(result, dict) or arm not in ('a', 'b') or not finite(cutoff):
            counts[sid]['skipped'] += 1
            continue
        counts[sid]['emitted'] += 1
        wires.add(wire_name(result))
        t = cutoff - sessions[sid]
        available_at = t + latency[sid]
        records.append(ticks.emission(sid, arm, t, available_at, probabilities((result.get('judgment') or {}).get('probabilities'), status),
                                      closed_at=available_at + REPLAY_HOLD_S))
    return records, wires, counts, latency


def run_name(base, wires):
    named = sorted(w for w in wires if w)
    return base + ('-' + '+'.join(named) if named else '')


def run(db, prefixes, out, *, live=False, replay_db=None, job=None, reference=False, base=RUN):
    sessions = ticks.sessions_for(db, prefixes)
    grants = {sid: qwen_grants(db, sid) for sid in sessions}
    arm = REFERENCE_ARM if reference else ARM
    if live:
        records, wires, counts = [], set(), {}
        for sid in sessions:
            found, found_wires, counts[sid] = live_ticks(db, sid)
            records += found
            wires |= found_wires
        source = {'kind': 'live', 'event_kinds': ['reasoning.output', 'reasoning.gate'],
                  'closures': 'max(valid_until, min(valid_through, reasoning.validity)) as jev_smoothing.facts_bound; '
                              'prediction.valid_until without valid_through or for gate answers',
                  'gate_classes': GATE_CLASSES}
    else:
        rdb = lab_regression.open_ro(replay_db)
        try:
            rows = replay_rows(rdb, job)
        finally:
            rdb.close()
        if not rows:
            raise SystemExit('no replay_results rows for job %r' % job)
        records, wires, counts, latency = replay_ticks(db, rows, sessions)
        source = {'kind': 'replay', 'replay_db': os.path.basename(str(replay_db)), 'job': job,
                  'latency_s': {sid: round(v, 3) for sid, v in latency.items()},
                  'closures': 'not known for replays: closed_at = available_at + %g' % REPLAY_HOLD_S}
    records.sort(key=lambda r: (r['session_id'], r['available_at'], r['arm']))
    emitted_arms = {r['arm'] for r in records} or {'a', 'b'}
    consumes = any(grants[sid][a] for sid in sessions for a in emitted_arms)
    source.update({'ledger': os.path.basename(str(db.execute('PRAGMA database_list').fetchone()[2] or '')),
                   'sessions': sorted(sessions), 'wires': sorted(w for w in wires if w), 'counts': counts,
                   'qwen_scene_granted': grants})
    ticks.write_ticks(out, ticks.header(run_name(base, wires), arm, source, consumes_qwen=consumes, calibration='distribution'), records)
    return counts


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ledger', default=lab_regression.LEDGER, help='the live ledger, opened read-only')
    parser.add_argument('--session', nargs='+', required=True, help='session id prefixes')
    parser.add_argument('--out', required=True, help='tick-record JSONL to write')
    parser.add_argument('--live', action='store_true', help='recorded live reasoning.output events')
    parser.add_argument('--replay-db', default=None, help='replay database (read-only)')
    parser.add_argument('--job', default=None, help='replay job name')
    parser.add_argument('--reference', action='store_true', help="header arm 'reference' instead of 'A'")
    parser.add_argument('--run', default=RUN, help='run name prefix; the wire is appended')
    args = parser.parse_args(argv)
    if args.live == bool(args.replay_db and args.job):
        raise SystemExit('pass --live, or --replay-db FILE with --job NAME')
    db = lab_regression.open_ro(args.ledger)
    try:
        counts = run(db, args.session, args.out, live=args.live, replay_db=args.replay_db, job=args.job,
                     reference=args.reference, base=args.run)
    finally:
        db.close()
    for sid, c in counts.items():
        print('%s emitted %d gated %d skipped %d' % (sid[:8], c['emitted'], c.get('gated', 0), c['skipped']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

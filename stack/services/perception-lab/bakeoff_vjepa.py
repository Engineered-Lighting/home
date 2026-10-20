"""Bake-off arm C(i): stored V-JEPA probabilities as a tick-record JSONL.

Offline operator tool. The ledger is opened read-only (?mode=ro) and nothing
is written to it; the only output is the tick file (--out). No network, no
model, no clock: every number comes from vjepa.observation events the live
worker already stored (vjepa_scene.build_observation).

Privacy: the tick file keeps the Lab's own raw probabilities locally, as the
ledger does. It carries session ids and the pinned model version, never
frames, boxes, embeddings or identifiers of people.

Tick-record contract (lab-ticks-1, read by lab_eval): a header line, then one
line per emission with a distribution over CLASSES that sums to 1. Held view
is lab_eval's job: a producer never extends an answer past its own closure.

The distribution of one valid observation (every step a fixed rule, no fit):
  reading, computer_work, phone_use  = zero_shot heads, clipped to [0, 1]
  transition                         = posture walking + 0.5 * posture standing
  resting                            = posture sitting * (1 - max zero_shot)
                                       when every zero_shot < RESTING_ZERO_SHOT_MAX, else 0
  other, empty                       = 0 (V-JEPA is occupancy-gated: it never sees an empty room)
  abstain                            = max(0, 1 - the rest), then the total is normalised to 1
A zero_shot of None (single-person gate not met) is abstain 1.0. Invalid
observations are counted and not emitted.

Usage:
  python bakeoff_vjepa.py --ledger DB --session PREFIX [PREFIX ...] --out ticks.jsonl [--run NAME]
"""
import argparse
import json
import math
import os
import sys

import lab_eval
import vjepa_scene as scene

SCHEMA = 'lab-ticks-1'
CLASSES = ('reading', 'computer_work', 'phone_use', 'resting', 'transition', 'other', 'empty', 'abstain')
ARM = 'C'
RUN = 'vjepa-stored'
RESTING_ZERO_SHOT_MAX = 0.3   # resting mass only when no zero-shot head reaches this
STANDING_TRANSITION = 0.5     # share of the standing posture counted as transition
SUM_TOLERANCE = 0.01


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clip(value):
    return min(1.0, max(0.0, float(value))) if finite(value) else 0.0


def normalise(masses):
    """A full distribution over CLASSES (missing class 0) that sums to 1;
    abstain 1.0 when there is no mass at all."""
    full = {c: clip(masses.get(c, 0.0)) for c in CLASSES}
    total = sum(full.values())
    if total <= 0:
        return {**{c: 0.0 for c in CLASSES}, 'abstain': 1.0}
    return {c: v / total for c, v in full.items()}


def distribution(result):
    """The arm C(i) distribution of one valid vjepa-clip-result-1."""
    zero = result.get('zero_shot')
    if zero is None:
        return normalise({'abstain': 1.0})
    posture = result.get('posture') or {}
    heads = {k: clip(zero.get(k)) for k in scene.ZERO_SHOT}
    masses = dict(heads)
    masses['transition'] = clip(posture.get('walking')) + STANDING_TRANSITION * clip(posture.get('standing'))
    peak = max(heads.values())
    masses['resting'] = clip(posture.get('sitting')) * (1 - peak) if peak < RESTING_ZERO_SHOT_MAX else 0.0
    masses['abstain'] = max(0.0, 1 - sum(masses.values()))
    return normalise(masses)


def header(run, arm, source, *, consumes_qwen, calibration):
    return {'schema': SCHEMA, 'run': run, 'arm': arm, 'consumes_qwen': bool(consumes_qwen), 'calibration': calibration,
            'classes': list(CLASSES), 'source': source}


def emission(session_id, arm, t, available_at, probabilities, closed_at=None, facts_key=None):
    record = {'session_id': session_id, 'arm': arm, 't': t, 'available_at': available_at, 'probabilities': probabilities}
    if closed_at is not None:
        record['closed_at'] = closed_at
    if facts_key is not None:
        record['facts_key'] = facts_key
    return record


def write_ticks(file, head, records):
    with open(file, 'w', encoding='utf-8') as out:
        out.write(json.dumps(head, sort_keys=True) + '\n')
        for record in records:
            out.write(json.dumps(record, sort_keys=True) + '\n')


def read_ticks(file):
    """(header, emissions) of a tick file, through lab_eval's validating reader."""
    return lab_eval.read_ticks(file)


def sessions_for(db, prefixes):
    """{session_id: started_at} of the sessions the prefixes name (each prefix must match exactly one)."""
    rows = db.execute('SELECT id,started_at FROM sessions ORDER BY started_at').fetchall()
    found = {}
    for prefix in prefixes:
        matches = [r for r in rows if r[0].startswith(prefix)]
        if len(matches) != 1:
            raise SystemExit('session prefix %r matches %d sessions' % (prefix, len(matches)))
        if not finite(matches[0][1]):
            raise SystemExit('session %s has no started_at' % matches[0][0][:8])
        found[matches[0][0]] = float(matches[0][1])
    return found


def observations(db, sid):
    for (raw,) in db.execute("SELECT payload FROM events WHERE session_id=? AND kind='vjepa.observation' ORDER BY id", (sid,)):
        value = json.loads(raw)
        if isinstance(value, dict):
            yield value


def session_ticks(db, sid, started_at):
    """(emissions, counts) of one session. The observation is a shared
    (arm-less) record: it is emitted once per arm unless it names one."""
    counts = {'valid': 0, 'invalid': 0, 'skipped': 0}
    records = []
    for value in observations(db, sid):
        if not all(finite(value.get(k)) for k in ('observed_at', 'available_at', 'valid_until')):
            counts['skipped'] += 1
            continue
        if value.get('status') != 'valid' or not isinstance(value.get('result'), dict):
            counts['invalid'] += 1
            continue
        counts['valid'] += 1
        arms = [value['arm']] if value.get('arm') in ('a', 'b') else ['a', 'b']
        probabilities = distribution(value['result'])
        for arm in arms:
            records.append(emission(sid, arm, value['observed_at'] - started_at, value['available_at'] - started_at,
                                    probabilities, closed_at=value['valid_until'] - started_at))
    return records, counts


def run(db, prefixes, out, run_name=RUN):
    sessions = sessions_for(db, prefixes)
    records, report = [], {}
    for sid, started_at in sessions.items():
        found, counts = session_ticks(db, sid, started_at)
        records += found
        report[sid] = counts
    records.sort(key=lambda r: (r['session_id'], r['available_at'], r['arm']))
    source = {'ledger': os.path.basename(str(db.execute('PRAGMA database_list').fetchone()[2] or '')),
              'event_kind': 'vjepa.observation', 'model_version': scene.MODEL_VERSION,
              'revisions': dict(scene.REVISIONS), 'sessions': sorted(sessions), 'counts': report,
              'rule': {'resting_zero_shot_max': RESTING_ZERO_SHOT_MAX, 'standing_transition': STANDING_TRANSITION}}
    write_ticks(out, header(run_name, ARM, source, consumes_qwen=False, calibration='distribution'), records)
    return report


def main(argv):
    import lab_regression
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ledger', default=lab_regression.LEDGER, help='the live ledger, opened read-only')
    parser.add_argument('--session', nargs='+', required=True, help='session id prefixes')
    parser.add_argument('--out', required=True, help='tick-record JSONL to write')
    parser.add_argument('--run', default=RUN)
    args = parser.parse_args(argv)
    db = lab_regression.open_ro(args.ledger)
    try:
        report = run(db, args.session, args.out, args.run)
    finally:
        db.close()
    for sid, counts in report.items():
        print('%s valid %d invalid %d skipped %d' % (sid[:8], counts['valid'], counts['invalid'], counts['skipped']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

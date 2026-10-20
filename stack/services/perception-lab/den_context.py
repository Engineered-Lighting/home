"""Offline den context builder (den-context-1, docs/VIRTUAL-DEN-CONTRACTS.md).

Offline operator tool, not part of the controller release. The ledger is
opened read-only (jev_replay.LedgerView, sqlite ?mode=ro) and never written;
the only output is the file named by --out. No network, no provider, no model
call. Context derived from real sessions belongs outside the repository.

For each session, one record per second from start+1 to the end. A record at
t uses only rows available by t (available_at <= t, observed_at <= t), so it
is what a live builder could have said at that second.

No coordinates: persons are located only by anchor or zone ids. Placement-
free by owner principle: no now.position, head_toward, monitor_side or
desk_seat word is read (the Qwen visible_basis list is never read either).

Usage: python den_context.py --ledger SNAPSHOT --session PREFIX [--session ...] --out FILE.jsonl
                             [--ticks TICKS.jsonl ...] [--jev-config FILE] [--summary]
"""
import argparse
from collections import Counter
import json
import sys

from jev_cadence_replay import ReplayOccupancy, occupancy_entity
from jev_evidence import DERIVED_HA, HA_FACTS
from jev_replay import LedgerView, _recorded, infer_ha_bindings, recorded_ha_pairs
from occupancy_gate import DEFAULTS as GATE_DEFAULTS, check
from reasoning import finite

SCHEMA = 'den-context-1'
VERSION = 'den-context-builder-1'
STATUSES = ('supplied', 'stale', 'missing', 'not_provisioned', 'unobservable')
ACTIVITIES = ('computer_work', 'gaming', 'desk_hobby', 'reading', 'phone_use', 'resting', 'sleeping_likely', 'listening',
              'cleaning', 'transition', 'other', 'empty')
DETECTOR_WINDOW_S = 3.0          # detector person counts from frames observed in the last 3 s
QWEN_FRESH_S = 10.0              # a Qwen scene reading is fresh for 10 s after observation
REASONING_FRESH_S = 10.0         # a JEV answer is fresh for 10 s after it became available
REASONING_ARM = 'a'
TICKS_ARM = 'a'                  # the detector arm whose bake-off emissions are read
TICKS_HOLD_S = 10.0              # an emission without closed_at holds this long
AMBIENT_FRESH_S = 60.0           # a recorded ambient block (clock buckets) stays current this long
HA_GRACE_S = GATE_DEFAULTS['grace_seconds']
PC_RECENT = ('under_15s', '15_to_60s', 'under_60s')
PC_CONFIDENCE = {'under_15s': 0.8, '15_to_60s': 0.6, 'under_60s': 0.6}
# Per-source mass before the combined distribution is normalised; every
# source's own masses sum to at most 1 (abstention mass is dropped).
DEVICE_RULE_MASS = 0.6
DEVICES = {'pc_input': 'computer_input_recency', 'pc_active': 'computer_active', 'den_audio': 'den_audio_playback',
           'den_light': 'den_light', 'owner_home': 'owner_home'}
DEN_AUDIO_ENTITY = 'media_player.den_den'
DAYLIGHT = {'above_horizon': 'day', 'below_horizon': 'dark'}
DESK = {'anchor_id': 'desk_work_area', 'zone_id': 'desk'}
READING_TARGET = 'reading_chair_book'


def _r(value):
    return round(value, 3) if finite(value) else None


def device_entities(seen, bindings):
    """{device: entity or None}. Bound entities (the config's ha_bindings, else
    those recovered from the recorded requests) first; then the fixed derived
    ids, media_player.den_den, and the only light.* / person.* entity seen."""
    by_fact = {}
    for entity, fact in sorted((bindings or {}).items()):
        by_fact.setdefault(fact, entity)   # a configured binding is provisioned even before its first row
    for entity, fact in DERIVED_HA.items():
        if entity in seen:
            by_fact.setdefault(fact, entity)
    media = sorted(e for e in seen if e.startswith('media_player.'))
    if DEN_AUDIO_ENTITY in seen:
        by_fact.setdefault('den_audio_playback', DEN_AUDIO_ENTITY)
    elif len(media) == 1:
        by_fact.setdefault('den_audio_playback', media[0])
    for domain, fact in (('light.', 'den_light'), ('person.', 'owner_home')):
        found = sorted(e for e in seen if e.startswith(domain))
        if len(found) == 1:
            by_fact.setdefault(fact, found[0])
    return {device: by_fact.get(fact) for device, fact in DEVICES.items()}


def device_value(device, state):
    if device == 'owner_home':
        return 'home' if state == 'home' else 'away'   # never a zone name
    if device == 'pc_input' and state not in HA_FACTS['computer_input_recency']:
        return None
    return state


class SessionRows:
    """One session's rows of the kinds the builder reads, parsed once."""

    def __init__(self, view, session):
        self.session = session
        self.ha, self.frames, self.qwen, self.answers = [], [], [], []
        start = session['started_at']
        query = 'SELECT id,kind,payload FROM events WHERE session_id=? AND kind IN (%s) ORDER BY id'
        kinds = ('ha.observation', 'perception.output', 'qwen.observation', 'reasoning.output')
        for row in view.db.execute(query % ','.join('?' * len(kinds)), (session['id'], *kinds)):
            value = json.loads(row['payload'])
            if row['kind'] == 'ha.observation':
                if (isinstance(value.get('ha'), dict) and finite(value.get('available_at')) and finite(value.get('observed_at'))
                        and value.get('site_id') == session['site_id']):
                    self.ha.append((row['id'], value))
            elif row['kind'] == 'perception.output':
                if (value.get('status') == 'completed' and value.get('arm') in ('a', 'b') and finite(value.get('available_at'))
                        and finite(value.get('observed_at'))):
                    objects = (value.get('result') or {}).get('objects') or []
                    persons = sum(1 for o in objects if isinstance(o, dict) and o.get('label') == 'person')
                    self.frames.append((row['id'], value['arm'], value['observed_at'], value['available_at'], persons))
            elif row['kind'] == 'qwen.observation':
                if (value.get('status') == 'valid' and isinstance(value.get('facts'), dict) and finite(value.get('available_at'))
                        and finite(value.get('observed_at'))):
                    self.qwen.append((row['id'], value))
            else:
                prediction = value.get('prediction') or {}
                probabilities = (value.get('judgment') or {}).get('probabilities')
                if (value.get('arm') == REASONING_ARM and value.get('mode', 'live') == 'live' and finite(prediction.get('available_at'))
                        and isinstance(probabilities, dict)):
                    self.answers.append((row['id'], start + prediction['available_at'], probabilities))
        self.seen = sorted({e for _, v in self.ha for e in v['ha']})

    def latest_ha(self, entity, t):
        """(event id, payload) of the newest causal snapshot carrying entity."""
        best = None
        for event_id, value in self.ha:
            if entity in value['ha'] and value['available_at'] <= t and value['observed_at'] <= t:
                if best is None or (value['observed_at'], event_id) > (best[1]['observed_at'], best[0]):
                    best = (event_id, value)
        return best


def ha_item(rows, entity, t):
    """({value, status, age_s}, event id or None) of one HA entity at t. A stale
    or unknown reading carries no value: missing is never a negative."""
    if entity is None:
        return {'value': None, 'status': 'not_provisioned', 'age_s': None}, None
    found = rows.latest_ha(entity, t)
    if found is None:
        return {'value': None, 'status': 'missing', 'age_s': None}, None
    event_id, value = found
    item = value['ha'][entity] if isinstance(value['ha'][entity], dict) else {}
    age = _r(t - value['observed_at'])
    if not finite(value.get('valid_until')) or t > value['valid_until'] + HA_GRACE_S:
        return {'value': None, 'status': 'stale', 'age_s': age}, event_id
    if item.get('status') != 'known' or not isinstance(item.get('state'), str):
        return {'value': None, 'status': 'missing', 'age_s': age}, event_id
    return {'value': item['state'], 'status': 'supplied', 'age_s': age}, event_id


def occupancy(rows, gate, entity, t, refs):
    """Frigate (camera-derived binary_sensor through ReplayOccupancy) combined
    with the detector's person count over the last DETECTOR_WINDOW_S."""
    sources = []
    latest = {}
    for event_id, arm, observed, available, persons in rows.frames:
        if available <= t and t - DETECTOR_WINDOW_S <= observed <= t:
            if arm not in latest or (observed, event_id) > (latest[arm][0], latest[arm][1]):
                latest[arm] = (observed, event_id, persons)
    detector = None
    if latest:
        detector = min(2, max(v[2] for v in latest.values()))
        newest = max(v[0] for v in latest.values())
        refs.update(v[1] for v in latest.values())
        sources.append({'id': 'detector', 'count': detector, 'status': 'supplied', 'quality': 'measured',
                        'age_s': _r(t - newest)})
    else:
        stale = [f for f in rows.frames if f[3] <= t and f[2] <= t]
        sources.append({'id': 'detector', 'count': None, 'status': 'stale' if stale else 'missing', 'quality': 'measured',
                        'age_s': _r(t - max(f[2] for f in stale)) if stale else None})
    frigate, frigate_status, frigate_age = None, 'not_provisioned', None
    if gate is not None:
        gate.now = t
        reading = check(gate)[1]
        frigate = {'occupied': 1, 'vacant': 0}.get(reading['state'])
        found = rows.latest_ha(entity, t)
        if found is not None:
            refs.add(found[0])
            frigate_age = _r(t - found[1]['observed_at'])
        frigate_status = ('supplied' if frigate is not None else 'stale' if reading['reason'] == 'occupancy_stale' else 'missing')
        sources.append({'id': 'frigate_occupancy', 'count': frigate, 'state': reading['state'], 'reason': reading['reason'],
                        'status': frigate_status, 'quality': 'inferred', 'age_s': frigate_age})
    if detector is None and frigate is None:
        status = 'stale' if 'stale' in (sources[0]['status'], frigate_status) else (
            'not_provisioned' if gate is None and sources[0]['status'] == 'missing' else 'missing')
        return {'count': None, 'status': status, 'quality': None, 'sources': sources, 'age_s': None}
    count = max(c for c in (detector, frigate) if c is not None)
    measured = detector is not None and count == detector
    return {'count': count, 'status': 'supplied', 'quality': 'measured' if measured else 'inferred', 'sources': sources,
            'age_s': sources[0]['age_s'] if measured else frigate_age}


def latest_qwen(rows, t):
    best = None
    for event_id, value in rows.qwen:
        if value['available_at'] <= t and value['observed_at'] <= t:
            if best is None or (value['observed_at'], event_id) > (best[1]['observed_at'], best[0]):
                best = (event_id, value)
    if best is None:
        return None, 'missing'
    value = best[1]
    fresh = t - value['observed_at'] < QWEN_FRESH_S and (not finite(value.get('valid_until')) or t <= value['valid_until'])
    return best, 'supplied' if fresh else 'stale'


def jev_masses(rows, t, refs):
    """Masses of the newest fresh arm-a JEV answer, insufficient_evidence dropped."""
    best = None
    for event_id, available, probabilities in rows.answers:
        if available <= t and (best is None or (available, event_id) > (best[1], best[0])):
            best = (event_id, available, probabilities)
    if best is None or t - best[1] > REASONING_FRESH_S:
        return None
    refs.add(best[0])
    return {k: float(p) for k, p in best[2].items() if k in ACTIVITIES and finite(p) and p > 0}


def ticks_masses(emissions, session_id, k):
    """Average over the tick files of each file's emission open at session time
    k for the configured detector arm; abstain dropped."""
    found = []
    for name, items in emissions:
        current = None
        for item in items.get(session_id, ()):
            closed = item.get('closed_at')
            closed = item['available_at'] + TICKS_HOLD_S if closed is None else closed
            if item['available_at'] <= k < closed and (current is None or item['available_at'] >= current['available_at']):
                current = item
        if current is not None:
            found.append((name, current['probabilities']))
    if not found:
        return None, []
    masses = Counter()
    for _, probabilities in found:
        for key, p in probabilities.items():
            if key in ACTIVITIES and p > 0:
                masses[key] += p / len(found)
    return dict(masses), [name for name, _ in found]


def hypotheses(pc_recent, jev, ticks, tick_names):
    combined, sources = Counter(), []
    if pc_recent:
        combined['computer_work'] += DEVICE_RULE_MASS
        sources.append('device_rule:pc_input_recent')
    if jev:
        combined.update(jev)
        sources.append('jev:arm_%s' % REASONING_ARM)
    if ticks:
        combined.update(ticks)
        sources += ['ticks:%s' % n for n in tick_names]
    total = sum(combined.values())
    if total <= 0:
        return {}, sources
    return {k: round(v / total, 4) for k, v in sorted(combined.items()) if round(v / total, 4) > 0}, sources


def ambience(rows, recorded_ambient, sun_entity, audio, t, tz, refs):
    music = {'value': None if audio['status'] != 'supplied' else audio['value'] == 'playing', 'status': audio['status']}
    if tz is not None:
        from ambient_context import facts
        part = {'value': facts(t, tz)['part_of_day'], 'status': 'supplied'}
    else:
        part = {'value': None, 'status': 'missing'}
    daylight = {'value': None, 'status': 'missing'}
    if sun_entity is not None:
        item, event_id = ha_item(rows, sun_entity, t)
        if item['status'] == 'supplied' and item['value'] in DAYLIGHT:
            daylight = {'value': DAYLIGHT[item['value']], 'status': 'supplied'}
            refs.add(event_id)
        elif item['status'] == 'stale':
            daylight = {'value': None, 'status': 'stale'}
    recorded = [(c, a) for c, a in recorded_ambient if c <= t]
    if recorded:
        cutoff, block = max(recorded, key=lambda x: x[0])
        fresh = t - cutoff <= AMBIENT_FRESH_S
        for name, field in (('part_of_day', 'part'), ('daylight', 'daylight')):
            current = part if field == 'part' else daylight
            if current['status'] == 'supplied':
                continue
            value = block.get(name)
            if value in (None, 'unknown'):
                continue
            replacement = {'value': value if fresh else None, 'status': 'supplied' if fresh else 'stale'}
            if field == 'part':
                part = replacement
            else:
                daylight = replacement
    return {'music_playing': music, 'part_of_day': part, 'daylight': daylight}


def session_context(view, sid, *, config=None, emissions=()):
    """(records, info) of one session: one den-context-1 record per second."""
    config = config or {}
    session = view.get_session(sid)
    start, end = session['started_at'], session['ended_at']
    if end is None:
        return [], {'skipped': 'not ended'}
    rows = SessionRows(view, session)
    recorded = _recorded(view, sid)
    bindings = dict(config['ha_bindings']) if config.get('ha_bindings') else infer_ha_bindings(recorded_ha_pairs(recorded))
    entities = device_entities(set(rows.seen), bindings)
    recorded_ambient = []
    for item in recorded:
        block = (item['payload'].get('context_snapshot') or {}).get('ambient')
        if isinstance(block, dict) and finite(item['payload'].get('cutoff')):
            recorded_ambient.append((item['payload']['cutoff'], block))
    tz = None
    if config.get('site_timezone'):
        from ambient_context import zone
        tz = zone(config['site_timezone'])
    sun = 'sun.sun' if 'sun.sun' in rows.seen else None
    frigate = occupancy_entity(view, sid)
    gate = ReplayOccupancy(view, session, frigate) if frigate else None
    records = []
    for k in range(1, int(end - start) + 1):
        t = start + k
        refs = set()
        devices = {}
        for device, entity in entities.items():
            item, event_id = ha_item(rows, entity, t)
            if item['value'] is not None:
                item['value'] = device_value(device, item['value'])
            if event_id is not None and item['status'] == 'supplied':
                refs.add(event_id)
            devices[device] = item
        occ = occupancy(rows, gate, frigate, t, refs)
        pc = devices['pc_input']
        pc_recent = pc['status'] == 'supplied' and pc['value'] in PC_RECENT
        persons = []
        if occ['count'] is not None and occ['count'] >= 1:
            qwen, qwen_status = latest_qwen(rows, t)
            facts = qwen[1]['facts'] if qwen and qwen_status == 'supplied' else {}
            if qwen and qwen_status == 'supplied':
                refs.add(qwen[0])
            posture = facts.get('subject_posture')
            posture = {'value': posture if isinstance(posture, str) and posture not in ('unknown', 'not_visible') else None,
                       'status': qwen_status, 'quality': 'inferred'}
            if posture['value'] is None and qwen_status == 'supplied':
                posture['status'] = 'unobservable'
            location = (dict(DESK, quality='inferred', basis=['pc_input_recent']) if pc_recent
                        else {'anchor_id': None, 'zone_id': None, 'quality': 'approximate', 'basis': ['camera_person_no_map']})
            engagements = []
            if pc_recent:
                engagements.append({'target_id': DESK['anchor_id'], 'confidence': PC_CONFIDENCE[pc['value']],
                                    'quality': 'inferred', 'basis': ['pc_input_recent']})
            basis = [b for b, ok in (('qwen_held_object_book', facts.get('held_object') == 'book'),
                                      ('qwen_activity_reading', facts.get('activity_hypothesis') == 'reading')) if ok]
            if basis:
                engagements.append({'target_id': READING_TARGET, 'confidence': 0.5 + 0.2 * (len(basis) - 1),
                                    'quality': 'inferred', 'basis': basis})
            ticks, names = ticks_masses(emissions, sid, float(k))
            activity, sources = hypotheses(pc_recent, jev_masses(rows, t, refs), ticks, names)
            persons.append({'pid': 1, 'location': location, 'posture': posture, 'engagements': engagements,
                            'activity_hypotheses': activity, 'hypothesis_sources': sources})
        records.append({'schema': SCHEMA, 'session_id': sid, 't': float(k), 'seq': k, 'built_at_version': VERSION,
                        'occupancy': occ, 'persons': persons, 'devices': devices,
                        'ambience': ambience(rows, recorded_ambient, sun, devices['den_audio'], t, tz, refs),
                        'multi_person': occ['count'] == 2, 'unobserved': [], 'evidence_refs': sorted(refs)})
    bound = {d: e is not None for d, e in entities.items()}
    return records, {'ticks': len(records), 'occupancy_entity': 'bound' if frigate else 'not_found', 'devices_bound': bound}


def summarise(records):
    """{ticks, status shares per field, top activity shares}."""
    n = len(records)
    fields = Counter()
    for r in records:
        fields[('occupancy', r['occupancy']['status'])] += 1
        for device, item in r['devices'].items():
            fields[('devices.' + device, item['status'])] += 1
        for name, item in r['ambience'].items():
            fields[('ambience.' + name, item['status'])] += 1
        for p in r['persons']:
            fields[('posture', p['posture']['status'])] += 1
    shares = {}
    for (field, status), count in sorted(fields.items()):
        total = sum(c for (f, _), c in fields.items() if f == field)
        shares.setdefault(field, {})[status] = round(count / total, 3)
    top = Counter()
    for r in records:
        hyp = r['persons'][0]['activity_hypotheses'] if r['persons'] else None
        top['no_person' if not r['persons'] else max(sorted(hyp), key=lambda a: hyp[a]) if hyp else 'no_hypothesis'] += 1
    counts = Counter('null' if r['occupancy']['count'] is None else r['occupancy']['count'] for r in records)
    return {'ticks': n, 'status_share': shares,
            'occupancy_count_share': {str(k): round(v / n, 3) for k, v in sorted(counts.items(), key=str)} if n else {},
            'top_activity_share': {k: round(v / n, 3) for k, v in top.most_common()} if n else {}}


def load_ticks(files):
    """[(run name, {session_id: [emission]})] of the detector arm TICKS_ARM."""
    import lab_eval
    result = []
    for file in files or ():
        head, items = lab_eval.read_ticks(file)
        by_session = {}
        for item in items:
            if item['arm'] == TICKS_ARM:
                by_session.setdefault(item['session_id'], []).append(item)
        result.append(('%s/%s' % (head.get('arm'), head.get('run')), by_session))
    return result


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ledger', required=True, help='the ledger snapshot, opened read-only')
    parser.add_argument('--session', action='append', nargs='+', required=True, help='session id prefixes')
    parser.add_argument('--out', required=True)
    parser.add_argument('--ticks', action='append', nargs='+', default=[], help='lab-ticks-1 files (bake-off arms)')
    parser.add_argument('--jev-config', help='JEV provisioning file (ha_bindings, site_timezone)')
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args(argv)
    config = None
    if args.jev_config:
        with open(args.jev_config, encoding='utf-8') as stream:
            config = json.load(stream)
    prefixes = [p for group in args.session for p in group]
    emissions = load_ticks([f for group in args.ticks for f in group])
    view = LedgerView(args.ledger)
    try:
        with open(args.out, 'w', encoding='utf-8') as out:
            for sid in sorted(view.session_ids(prefixes)):
                records, info = session_context(view, sid, config=config, emissions=emissions)
                for record in records:
                    out.write(json.dumps(record, sort_keys=True) + '\n')
                line = dict(info, **summarise(records)) if args.summary else info
                print('%s %s' % (sid[:8], json.dumps(line, sort_keys=True)))
    finally:
        view.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

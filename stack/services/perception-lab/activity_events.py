"""Lighting-brain events from the always-on activity loop (contract v1).

docs/plans/den-activity-lighting/CONTRACT.md is the source of truth. This module
only builds and checks the AL-3 event types (v1, plus v1.1 person_position); it never carries an image, a
pixel, a person identity or a lighting command.
"""
import math
import re

SCHEMA = 'activity-lighting-events-1'
BOUND_SCHEMA = 'activity-lighting-events-2'
BOUND_FIELDS = ('evidence_version', 'binding', 'valid_until')
COMMON = ('type', 'home', 'room', 'source', 'observed_at', 'sent_at', 'ttl_s')
TTL_S = {'activity': 12., 'person_pose': 10., 'health': 150., 'cost': 300., 'person_position': 10.}
LABELS = ('reading', 'computer_work', 'phone_use', 'resting', 'transition', 'other', 'insufficient_evidence')
ZONES = ('desk', 'sofa')
COMPONENTS = ('jev', 'deep_stack', 'frigate', 'camera')
EXTRA = {
    'activity': ('label', 'confidence', 'person_count', 'model', 'facts_key', 'zone_hint'),
    'person_pose': ('people', 'person_count'),
    'health': ('component', 'ok', 'detail'),
    'cost': ('component', 'calls', 'est_usd'),
    'person_position': ('people', 'person_count'),  # v1.1 amendment
}
PERSON = ('track_id', 'shoulders_m', 'head_m', 'head_pitch_deg', 'approximate')
POSITION = ('track_id', 'position_m', 'approximate')
MAX_PEOPLE = 8
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,63}')


class EventRefused(ValueError):
    pass


def _need(ok, reason):
    if not ok:
        raise EventRefused(reason)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _text(value, limit=128):
    return isinstance(value, str) and 0 < len(value) <= limit


def _point(value):
    return value is None or (isinstance(value, list) and len(value) == 3
                             and all(_finite(v) and abs(v) <= 1000 for v in value))


def _common(kind, *, home, room, source, observed_at):
    _need(kind in EXTRA, 'activity_event_type_unknown')
    _need(all(isinstance(v, str) and _NAME.fullmatch(v) for v in (home, room, source)), 'activity_event_scope_invalid')
    _need(_finite(observed_at) and observed_at >= 0, 'activity_event_time_invalid')
    return dict(type=kind, home=home, room=room, source=source, observed_at=float(observed_at), ttl_s=TTL_S[kind])


def activity(*, home, room, source, observed_at, label, confidence, person_count, model, facts_key, zone_hint,
             binding=None, valid_until=None):
    event = _common('activity', home=home, room=room, source=source, observed_at=observed_at)
    event.update(label=label, confidence=None if confidence is None else float(confidence),
                 person_count=person_count, model=model, facts_key=facts_key, zone_hint=zone_hint)
    return _bound(event, binding, valid_until)


def person_pose(*, home, room, source, observed_at, people, person_count, binding=None, valid_until=None):
    event = _common('person_pose', home=home, room=room, source=source, observed_at=observed_at)
    event.update(people=people, person_count=person_count)
    return _bound(event, binding, valid_until)


def _bound(event, binding, valid_until):
    if valid_until is not None:
        event.update(evidence_version=2, binding=binding, valid_until=valid_until)
        event['ttl_s'] = min(TTL_S[event['type']], valid_until - event['observed_at'])
    return check(event, sent=False)


def person_position(*, home, room, source, observed_at, people, person_count):
    event = _common('person_position', home=home, room=room, source=source, observed_at=observed_at)
    event.update(people=people, person_count=person_count)
    return check(event, sent=False)


def health(*, home, room, source, observed_at, component, ok, detail):
    event = _common('health', home=home, room=room, source=source, observed_at=observed_at)
    event.update(component=component, ok=ok, detail=detail)
    return check(event, sent=False)


def cost(*, home, room, source, observed_at, calls, est_usd):
    event = _common('cost', home=home, room=room, source=source, observed_at=observed_at)
    event.update(component='jev', calls=calls, est_usd=None if est_usd is None else float(est_usd))
    return check(event, sent=False)


def check(event, *, sent=True):
    """Validate one event exactly against contract v1; returns it unchanged.

    sent=False checks an event before the publisher stamps sent_at.
    """
    _need(isinstance(event, dict) and event.get('type') in EXTRA, 'activity_event_type_unknown')
    kind = event['type']
    keys = set(COMMON) | set(EXTRA[kind])
    bound = event.get('evidence_version') == 2
    if bound:
        _need(kind in ('activity', 'person_pose'), 'activity_bound_type_invalid')
        keys.update(BOUND_FIELDS)
    _need(set(event) == (keys if sent else keys - {'sent_at'}), 'activity_event_keys_invalid')
    _need(all(isinstance(event[k], str) and _NAME.fullmatch(event[k]) for k in ('home', 'room', 'source')),
          'activity_event_scope_invalid')
    _need(_finite(event['observed_at']) and event['observed_at'] >= 0, 'activity_event_time_invalid')
    _need(not sent or (_finite(event['sent_at']) and event['sent_at'] >= 0), 'activity_event_time_invalid')
    if bound:
        _need(_finite(event['valid_until']) and 0 < event['ttl_s'] <= TTL_S[kind]
              and event['observed_at'] + event['ttl_s'] <= event['valid_until'] + 1e-6, 'activity_event_ttl_invalid')
        binding = event['binding']
        if binding is None:
            pass  # Explicitly unbound observation retires reading authority.
        else:
            _need(isinstance(binding, dict) and binding.get('schema') == 'den-sole-reader-source-1'
                  and binding.get('status') == 'available' and binding.get('reason') is None
                  and all(_text(binding.get(k), 256) for k in ('session_id', 'capture_epoch', 'engine_epoch',
                        'reader_epoch', 'instance_id', 'source_frame_id', 'source_request_id'))
                  and type(binding.get('generation')) is int and binding['generation'] >= 1
                  and type(binding.get('source_event_id')) is int and binding['source_event_id'] > 0
                  and all(_finite(binding.get(k)) for k in ('observed_at', 'available_at', 'valid_until', 'interval_start'))
                  and binding['interval_start'] <= binding['observed_at'] <= binding['available_at'] < binding['valid_until']
                  and event['valid_until'] <= binding['valid_until']
                  and event['person_count'] == 1, 'activity_bound_binding_invalid')
    else:
        _need(event['ttl_s'] == TTL_S[kind], 'activity_event_ttl_invalid')
    count = event.get('person_count')
    if kind == 'activity':
        _need(event['label'] in LABELS, 'activity_label_invalid')
        _need(event['confidence'] is None or (_finite(event['confidence']) and 0 <= event['confidence'] <= 1),
              'activity_confidence_invalid')
        _need(count is None or (type(count) is int and 0 <= count <= 64), 'activity_person_count_invalid')
        _need(_text(event['model'], 64) and _text(event['facts_key'], 128), 'activity_model_or_facts_key_invalid')
        _need(event['zone_hint'] is None or event['zone_hint'] in ZONES, 'activity_zone_hint_invalid')
    elif kind == 'person_pose':
        people = event['people']
        _need(type(count) is int and 0 <= count <= 64, 'person_pose_count_invalid')
        _need(isinstance(people, list) and len(people) <= MAX_PEOPLE, 'person_pose_people_invalid')
        for person in people:
            _need(isinstance(person, dict) and set(person) == set(PERSON) and _text(person['track_id'], 64)
                  and isinstance(person['shoulders_m'], dict) and set(person['shoulders_m']) == {'left', 'right'}
                  and all(_point(person['shoulders_m'][s]) for s in ('left', 'right')) and _point(person['head_m'])
                  and (person['head_pitch_deg'] is None or (_finite(person['head_pitch_deg'])
                                                            and -90 <= person['head_pitch_deg'] <= 90))
                  and person['approximate'] is True, 'person_pose_person_invalid')
    elif kind == 'person_position':
        people = event['people']
        _need(type(count) is int and 0 <= count <= 64, 'person_position_count_invalid')
        _need(isinstance(people, list) and 1 <= len(people) <= MAX_PEOPLE, 'person_position_people_invalid')
        for person in people:
            _need(isinstance(person, dict) and set(person) == set(POSITION) and _text(person['track_id'], 64)
                  and person['position_m'] is not None and _point(person['position_m'])
                  and person['approximate'] is True, 'person_position_person_invalid')
    elif kind == 'health':
        _need(event['component'] in COMPONENTS and type(event['ok']) is bool
              and (event['detail'] is None or _text(event['detail'], 160)), 'health_event_invalid')
    else:
        _need(event['component'] == 'jev' and type(event['calls']) is int and event['calls'] >= 0
              and (event['est_usd'] is None or (_finite(event['est_usd']) and event['est_usd'] >= 0)),
              'cost_event_invalid')
    return event


def envelope(events):
    """The POST body; every event must already carry sent_at."""
    return {'schema': BOUND_SCHEMA if any(e.get('evidence_version') == 2 for e in events) else SCHEMA,
            'events': [check(e) for e in events]}

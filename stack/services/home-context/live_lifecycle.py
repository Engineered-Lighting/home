"""Config6 standalone lifecycle admission; never a fact, tracker or control clock.

Lifecycle has its own closed fields. Only protocol-neutral capture lineage is
shared with facts/geometry. Durable prior identity references do not replace or
rewind the current PID slot when an old target is observed absent/reset.
"""
import json

from journal import JournalError, canonical, finite
from live_ingress_validation import (_array, _exact, _id, _integer, _nonnegative, _person, _ref,
                                     validate_capture_lineage, SCOPE_KEYS)

TYPE = 'home-live-person-lifecycle-1'
DISPOSITIONS = frozenset({'continuity_qualified', 'observation_gap', 'use_ended_qualified',
                         'departure_qualified', 'absence_qualified', 'association_reset'})
FIELDS = {'schema', 'version', 'id', *SCOPE_KEYS, 'manifest_ref', 'source_id', 'source_generation',
    'target_person_ref', 'target_subject_ref', 'disposition', 'method_ref', 'qualification_ref',
    'applicability_ref', 'coverage_assessment_ref', 'basis_refs', 'head', 'observed_at', 'available_at',
    'valid_until', 'clock', 'sensor_roots', 'observation_roots', 'observation_root_times',
    'sensor_root_bindings', 'original_receipt_refs', 'window'}


def fail(code, message='lifecycle does not match its independently commissioned authority', status=409):
    raise JournalError(code, message, status)


def validate_lifecycle_policy(policy, sources):
    _exact(policy, {'schema', 'version', 'continuity_methods', 'negative_methods', 'absence_methods', 'operator_clearance'})
    if policy['schema'] != 'home-live-person-lifecycle-policy-1' or type(policy['version']) is not int or policy['version'] != 1:
        fail('invalid_lifecycle_policy')
    methods = [m for source in sources.values() for m in source['method_refs']]
    for name in ('continuity_methods', 'negative_methods', 'absence_methods'):
        _array(policy[name], 16)
        tuples = set()
        for method in policy[name]:
            _exact(method, {'method_ref', 'qualification_ref', 'applicability_ref'} |
                   ({'max_gap_s'} if name == 'continuity_methods' else {'coverage_assessment_ref'} if name == 'absence_methods' else set()))
            for key in ('method_ref', 'qualification_ref', 'applicability_ref'):
                _ref(method[key])
            identity = canonical([method[k] for k in ('method_ref', 'qualification_ref', 'applicability_ref')])
            if identity in tuples or method['method_ref'] not in methods:
                fail('invalid_lifecycle_policy')
            tuples.add(identity)
            if name == 'continuity_methods':
                _nonnegative(method['max_gap_s'], positive=True)
                if method['max_gap_s'] > 30:
                    fail('invalid_lifecycle_policy')
            if name == 'absence_methods':
                _ref(method['coverage_assessment_ref'])
    clearance = policy['operator_clearance']
    if clearance is not None:
        _exact(clearance, {'policy_ref', 'applicability_ref', 'procedure_ref', 'room_assessment_ref', 'max_age_s'})
        for key in ('policy_ref', 'applicability_ref', 'procedure_ref', 'room_assessment_ref'):
            _ref(clearance[key])
        _nonnegative(clearance['max_age_s'], positive=True)
        if clearance['max_age_s'] > 30:
            fail('invalid_lifecycle_policy')


def commissioned_method(payload, policy):
    """No unqualified gap/reset can become positive or negative use evidence."""
    disposition = payload['disposition']
    name = ('continuity_methods' if disposition == 'continuity_qualified' else
            'negative_methods' if disposition == 'use_ended_qualified' else
            'absence_methods' if disposition in {'departure_qualified', 'absence_qualified'} else None)
    if name is None:
        return None
    return next((m for m in policy[name] if all(m[k] == payload[k] for k in ('method_ref', 'qualification_ref', 'applicability_ref'))
                 and (name != 'absence_methods' or m['coverage_assessment_ref'] == payload['coverage_assessment_ref'])), None)


def qualified_negative(payload, policy, person, subject):
    """Match a negative proof to a caller's existing relation, never create one.

    Covered person-wide absence/departure can end that person's prior subject
    relations. End-use remains subject-specific; gap/reset/continuity cannot
    become negative engagement merely by naming a person.
    """
    return (payload.get('schema') == TYPE and payload['disposition'] in {'use_ended_qualified', 'departure_qualified', 'absence_qualified'}
            and payload['target_person_ref'] == person and (payload['target_subject_ref'] == subject
                or payload['target_subject_ref'] is None and payload['disposition'] in {'departure_qualified', 'absence_qualified'})
            and commissioned_method(payload, policy) is not None)


def validate_lifecycle_capture(payload, source, receipt_resolver, policy):
    _exact(payload, FIELDS)
    if payload['schema'] != TYPE or type(payload['version']) is not int or payload['version'] != 1 or payload['disposition'] not in DISPOSITIONS:
        fail('invalid_lifecycle')
    _id(payload['id'])
    _ref(payload['manifest_ref'])
    _integer(payload['source_generation'], 1)
    for key in ('method_ref', 'qualification_ref', 'applicability_ref'):
        _ref(payload[key])
    if payload['method_ref'] not in source['method_refs']:
        fail('forbidden', status=403)
    person, subject = payload['target_person_ref'], payload['target_subject_ref']
    if person is None:
        if payload['disposition'] not in {'observation_gap', 'association_reset'} or subject is not None:
            fail('invalid_lifecycle_target')
    else:
        _person(person, payload)
    if subject is not None:
        _exact(subject, {'subject_id', 'subject_epoch'})
        for value in subject.values():
            _id(value)
    if payload['disposition'] in {'continuity_qualified', 'use_ended_qualified'} and subject is None:
        fail('invalid_lifecycle_target')
    covered = payload['disposition'] in {'departure_qualified', 'absence_qualified'}
    if covered:
        _ref(payload['coverage_assessment_ref'])
    elif payload['coverage_assessment_ref'] is not None:
        fail('invalid_lifecycle_coverage')
    method = commissioned_method(payload, policy)
    if payload['disposition'] not in {'observation_gap', 'association_reset'} and method is None:
        fail('unqualified_lifecycle')
    _exact(payload['window'], {'start_at', 'end_at'})
    start, end = payload['window']['start_at'], payload['window']['end_at']
    if not finite(start) or not finite(end) or not 0 <= start <= end <= payload['observed_at']:
        fail('invalid_clock')
    if payload['disposition'] == 'continuity_qualified' and end - start > method['max_gap_s']:
        fail('unqualified_lifecycle')
    _array(payload['basis_refs'], 16, nonempty=person is not None)
    head = payload['head']
    _exact(head, {'key', 'position', 'supersedes'})
    _exact(head['key'], {'source_id', 'payload_type', 'person_ref', 'subject_ref', 'predicate', 'method_ref'})
    if head['key'] != {'source_id': payload['source_id'], 'payload_type': TYPE, 'person_ref': person, 'subject_ref': subject,
                       'predicate': 'person_lifecycle', 'method_ref': payload['method_ref']}:
        fail('invalid_head')
    _exact(head['position'], {'capture_epoch', 'capture_seq', 'revision'})
    _integer(head['position']['capture_seq'])
    _integer(head['position']['revision'])
    if head['position']['capture_epoch'] != source['capture_epoch']:
        fail('capture_epoch_conflict')
    return validate_capture_lineage(payload, source, receipt_resolver, has_parents=bool(payload['basis_refs']))


def resolve_basis(ingress, ref, grant, event):
    _exact(ref, {'stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest', 'component_id'})
    j, payload = ingress.j, event['payload']
    if ref['stream_epoch'] != j._meta('epoch') or ref['component_id'] is not None:
        fail('invalid_reference')
    row = j.db.execute('SELECT body,revision FROM publications WHERE cursor=?', (ref['cursor'],)).fetchone()
    if row is None:
        fail('reference_unavailable')
    publication = json.loads(row['body'])
    if ref != ingress._publication_ref(publication):
        fail('invalid_reference')
    grant.require('read', event['site_id'], event['room_id'], payload_type=publication['payload_type'], revision=row['revision'])
    parent = publication['payload']
    generation = j.db.execute('SELECT generation FROM live_sources WHERE source_id=?', (parent.get('source_id'),)).fetchone()
    if (publication['payload_type'] not in {'home-live-fact-1', 'home-live-geometry-1', TYPE}
            or parent.get('source_id') != payload['source_id']
            or any(parent[k] != event[k] for k in SCOPE_KEYS) or parent['manifest_ref'] != ingress.config['manifest_ref']
            or not ingress._capability(grant, parent['source_id'], 'read')
            or generation is None or generation['generation'] != parent['source_generation']
            or parent['available_at'] > payload['available_at'] or publication['committed_at'] > payload['available_at']
            or parent['observed_at'] > payload['window']['end_at']):
        fail('invalid_lifecycle_basis')
    return parent


def append_lifecycle(ingress, event, grant):
    """Called only inside the original journal transaction after envelope auth."""
    j, p = ingress.j, event['payload']
    source, policy = ingress.sources[p['source_id']], ingress.manifest['worker']['lifecycle_policy']
    state = j.db.execute('SELECT * FROM live_sources WHERE source_id=?', (p['source_id'],)).fetchone()
    if p['source_generation'] != state['generation']:
        fail('source_generation_conflict')
    if state['barrier_at'] is not None and p['observed_at'] - p['clock']['uncertainty_s'] <= state['barrier_at'] + state['barrier_uncertainty']:
        fail('prebarrier_capture')
    records = validate_lifecycle_capture(p, source, j.live_receipt_resolver, policy)
    if not ingress._current_payload(p, j.clock()):
        fail('expired')
    parents = [resolve_basis(ingress, ref, grant, event) for ref in p['basis_refs']]
    target, subject = p['target_person_ref'], p['target_subject_ref']
    def matching(parent):
        return (parent.get('target_person_ref', parent.get('person_ref')) == target
                and (subject is None or parent.get('target_subject_ref', parent.get('subject_ref')) == subject))
    if target is not None and not any(matching(parent) for parent in parents):
        fail('invalid_lifecycle_basis', 'qualified target must retain exact committed identity ancestry')
    if p['disposition'] == 'continuity_qualified':
        slot = j.db.execute('SELECT * FROM live_identity_slots WHERE key=?', (canonical([p['source_id'], target['pid']]),)).fetchone()
        if (slot is None or slot['generation'] != p['source_generation'] or slot['track_epoch'] != target['track_epoch']
                or slot['subject'] is not None and json.loads(slot['subject']) != subject):
            fail('stale_lifecycle_identity', 'continuity cannot revive a previous anonymous identity')
    ingress._bind_lineage(p, records, parents)
    # Deliberately no _identity_fence call: an absence/reset aimed at a previous
    # epoch cannot replace the source's active PID or withdraw its newer heads.
    key, position = canonical(p['head']['key']), p['head']['position']
    old = j.db.execute('SELECT * FROM live_heads WHERE key=?', (key,)).fetchone()
    if old and p['source_generation'] == old['generation'] and (position['capture_seq'], position['revision']) <= (old['capture_seq'], old['revision']):
        fail('stale_head')
    if p['head']['supersedes'] != (json.loads(old['reference']) if old else None):
        fail('head_conflict')
    if old is None and j.db.execute('SELECT count(*) FROM live_heads').fetchone()[0] >= min(ingress.limits['max_heads'], j.limits.max_state_items):
        fail('journal_full', status=507)
    receipt, publication = ingress._record(event)
    if old:
        ingress._invalidate_dependents([old['cursor']])
    horizon = min(p['valid_until'], p['observed_at'] + source['max_age_s']) - p['clock']['uncertainty_s']
    j.db.execute('INSERT INTO live_heads VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET capture_epoch=excluded.capture_epoch,capture_seq=excluded.capture_seq,revision=excluded.revision,generation=excluded.generation,cursor=excluded.cursor,reference=excluded.reference,valid_until=excluded.valid_until,body=excluded.body',
        (key, p['source_id'], position['capture_epoch'], position['capture_seq'], position['revision'], p['source_generation'], publication['cursor'], canonical(ingress._publication_ref(publication)), horizon, canonical(publication)))
    j.db.execute('DELETE FROM live_head_dependencies WHERE head_key=?', (key,))
    # Lifecycle basis is immutable historical identity/method ancestry. An
    # ordinary newer observation may supersede that head without undoing this
    # independently captured proof. Basis is explicitly same-source above, so
    # the source's own barrier/revocation, identity fence and proof expiry still
    # withdraw it; no multi-source continuity authority is implied.
    if j.db.execute('SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM live_heads').fetchone()[0] > j.limits.max_state_bytes:
        fail('journal_full', status=507)
    ingress._prune()
    return receipt

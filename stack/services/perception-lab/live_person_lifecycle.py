"""Pinned producer identity continuity, never temporal engagement or clearance.

Unknown observations do not become negative use or absence. Only a fixed typed
owner capability may supply independently qualified end-use/coverage receipts;
that capability is unavailable by default. Real method qualification is external
to these authored software tests.
"""
from copy import deepcopy
import math
import statistics
import threading

from journal import canonical, digest, finite, identifier

LOCAL_KEYS = {'schema', 'version', 'id', 'capture_method_ref', 'spatial_method_ref', 'model_refs',
              'registration', 'receipt_store_id', 'receipt_component_id', 'continuity', 'negative_methods', 'absence_methods'}
CONTINUITY_KEYS = {'method_ref', 'qualification_ref', 'applicability_ref', 'max_gap_s', 'anchor_joint_ids',
                   'min_anchor_count', 'max_anchor_error_m', 'max_step_m', 'max_speed_m_s'}
METHOD_KEYS = {'method_ref', 'qualification_ref', 'applicability_ref'}
SCOPE = {'site_id', 'room_id', 'session_id', 'domain_epoch'}
RECEIPT_KEYS = {'schema', 'version', 'id', 'session_ref', 'configuration_digest', 'capture_id', 'source_id',
                'source_generation', 'target_person_ref', 'target_subject_ref', 'disposition', 'method_ref',
                'qualification_ref', 'applicability_ref', 'coverage_assessment_ref', 'basis_refs', 'window',
                'observed_at', 'available_at', 'valid_until', 'coverage_status', 'truth_role'}


class LifecycleRefusal(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def pin(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'digest'} or not isinstance(value['digest'], str)
            or len(value['digest']) != 64 or any(c not in '0123456789abcdef' for c in value['digest'])):
        raise ValueError('immutable lifecycle method and qualification pins required')
    identifier(value['id'], 'lifecycle_pin')


def validate_policy(policy, source, worker_policy):
    if (not isinstance(policy, dict) or set(policy) != LOCAL_KEYS
            or policy['schema'] != 'home-local-person-lifecycle-policy-1' or type(policy['version']) is not int
            or policy['version'] != 1 or not isinstance(worker_policy, dict)
            or worker_policy.get('schema') != 'home-live-person-lifecycle-policy-1' or worker_policy.get('version') != 1
            or not isinstance(policy['model_refs'], dict) or set(policy['model_refs']) != {'detector', 'pose'}
            or policy['registration'] != source['registration']):
        raise ValueError('closed fixed local lifecycle and worker policies required')
    for key in ('id', 'receipt_store_id', 'receipt_component_id'):
        identifier(policy[key], 'lifecycle_' + key)
    for ref in (policy['capture_method_ref'], policy['spatial_method_ref'], *policy['model_refs'].values()):
        pin(ref)
    if (any(ref not in source['method_refs'] for ref in (policy['capture_method_ref'], policy['spatial_method_ref']))
            or any(ref not in source['model_refs'] for ref in policy['model_refs'].values())
            or policy['receipt_store_id'] not in source['receipt_store_ids']
            or policy['receipt_component_id'] not in source['receipt_component_ids']
            or policy['receipt_component_id'] in {'rgb', 'depth', 'spatial', 'interaction'}):
        raise ValueError('registered fixed lifecycle capabilities required')
    continuity = policy['continuity']
    if continuity is not None:
        if not isinstance(continuity, dict) or set(continuity) != CONTINUITY_KEYS:
            raise ValueError('explicit measured unique continuity bounds required')
        for key in METHOD_KEYS:
            pin(continuity[key])
        worker = {key: continuity[key] for key in METHOD_KEYS | {'max_gap_s'}}
        anchors = continuity['anchor_joint_ids']
        if (worker not in worker_policy['continuity_methods']
                or continuity['method_ref'] not in source['association_method_refs']
                or continuity['method_ref'] not in source['method_refs']
                or not finite(continuity['max_gap_s']) or not 0 < continuity['max_gap_s'] <= 30
                or not isinstance(anchors, list) or not 1 <= len(anchors) <= 17
                or len(set(anchors)) != len(anchors) or any(type(i) is not int or not 0 <= i <= 16 for i in anchors)
                or type(continuity['min_anchor_count']) is not int or not 1 <= continuity['min_anchor_count'] <= len(anchors)
                or not all(finite(continuity[k]) and continuity[k] >= 0 for k in
                           ('max_anchor_error_m', 'max_step_m', 'max_speed_m_s'))):
            raise ValueError('qualified registered unique continuity bounds required')
    for name in ('negative_methods', 'absence_methods'):
        methods = policy[name]
        keys = METHOD_KEYS | ({'coverage_assessment_ref'} if name == 'absence_methods' else set())
        if (not isinstance(methods, list) or len(methods) > 16 or len({canonical(m) for m in methods}) != len(methods)
                or any(not isinstance(m, dict) or set(m) != keys or m not in worker_policy[name] for m in methods)):
            raise ValueError('explicit registered qualified receipt methods required')
        for method in methods:
            for ref in method.values():
                pin(ref)
            if method['method_ref'] not in source['method_refs']:
                raise ValueError('registered lifecycle evidence method required')
    if len(canonical(policy).encode()) > 65536:
        raise ValueError('bounded lifecycle policy required')


class LivePersonLifecycle:
    def __init__(self, policy, *, worker_policy, source, session_ref, source_id, configuration_digest,
                 revision, clock, publish, max_captures=24):
        validate_policy(policy, source, worker_policy)
        if (not isinstance(session_ref, dict) or set(session_ref) != SCOPE
                or not all(callable(port) for port in (clock, publish)) or type(max_captures) is not int
                or not 1 <= max_captures <= 24):
            raise ValueError('fixed bounded lifecycle ports required')
        for value in (*session_ref.values(), source_id, revision):
            identifier(value, 'lifecycle_identity')
        self.policy, self.source, self.scope = deepcopy(policy), deepcopy(source), deepcopy(session_ref)
        self.policy_ref = {'id': policy['id'], 'digest': digest(policy)}
        self.source_digest = digest(self.source)
        self.source_id, self.configuration_digest, self.revision = source_id, configuration_digest, revision
        self.clock, self.publish, self.max_captures = clock, publish, max_captures
        self.lock = threading.RLock()
        self.prepared, self.head_refs, self.originals, self.records = {}, {}, {}, {}
        self.qualified_receipts, self.relations, self.identities = {}, {}, {}
        self.closed = False

    def consider(self, context, numeric, estimate, prior, *, person_count):
        """Return association disposition; never update observation time or dwell.

        The spatial owner applies a successful association only after its exact
        publication ACK. A gap retains no new anchor and does not refresh the old
        observation. The commissioned method's applicability includes unique
        single-person association under its explicit measured bounds.
        """
        self._binding(context)
        if type(person_count) is not int or not 0 <= person_count <= 2:
            raise LifecycleRefusal('lifecycle_person_budget_exceeded')
        payload, method = context['capture_payload'], self.policy['continuity']
        base = {'anchor_m': None, 'method_ref': method['method_ref'] if method else self.policy['spatial_method_ref']}
        if person_count > 1:
            return {**base, 'action': 'reset', 'reason': 'association_ambiguous'}
        if prior is None:
            return {**base, 'action': 'start' if person_count == 1 else 'gap', 'reason': 'no_prior_identity'}
        position = payload['head']['position']
        dt = payload['observed_at'] - prior['observed_at']
        if (method is None or payload['source_generation'] != prior['source_generation']
                or position['capture_epoch'] != prior['capture_epoch'] or position['capture_seq'] <= prior['capture_seq']
                or not finite(dt) or not 0 < dt <= method['max_gap_s']):
            return {**base, 'action': 'reset', 'reason': 'continuity_bound_or_epoch_failed'}
        measured = []
        seen = set()
        from live_spatial_geometry import pairing_budget_ok
        if not pairing_budget_ok(numeric, self.source.get('part_policies', {}).get('body')):
            return {**base, 'action': 'gap', 'reason': 'association_observation_unavailable'}
        for joint in estimate['joints'] if estimate is not None else []:
            point = joint.get('xyz_m')
            if (joint.get('joint_id') in method['anchor_joint_ids'] and joint.get('status') == 'measured'
                    and joint.get('joint_id') not in seen
                    and isinstance(point, list) and len(point) == 3 and all(finite(v) for v in point)
                    and finite(joint.get('spread_m')) and joint['spread_m'] >= 0
                    and joint['spread_m'] + numeric['projection']['error_bound_m'] + numeric['registration']['error_bound_m']
                        <= method['max_anchor_error_m']):
                measured.append(point)
                seen.add(joint['joint_id'])
        if person_count == 0 or len(measured) < method['min_anchor_count']:
            return {**base, 'action': 'gap', 'reason': 'association_observation_unavailable'}
        anchor = [statistics.median(point[axis] for point in measured) for axis in range(3)]
        distance = math.dist(anchor, prior['anchor_m'])
        if distance > method['max_step_m'] or distance > method['max_speed_m_s'] * dt + 2 * method['max_anchor_error_m']:
            return {**base, 'action': 'reset', 'reason': 'continuity_motion_bound_failed'}
        return {**base, 'action': 'continue', 'reason': 'qualified_unique_continuity', 'anchor_m': anchor}

    def _binding(self, context):
        if (self.closed or context['session_ref'] != self.scope or context['source_id'] != self.source_id
                or context['configuration_digest'] != self.configuration_digest
                or context.get('model_refs') != self.policy['model_refs']
                or context['capture_payload']['registration'] != self.policy['registration']
                or digest(self.policy) != self.policy_ref['digest'] or digest(self.source) != self.source_digest):
            raise LifecycleRefusal('lifecycle_binding_changed')

    def resolve_receipt(self, store_id, receipt_id):
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        # Journal HTTP admission resolves this immutable entry on another
        # thread while the publisher awaits its ACK. It must not acquire the
        # publication lock. Entries clear only after the supervisor drains.
        return deepcopy(self.records[receipt_id])

    def original_receipt(self, store_id, receipt_id):
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.originals[receipt_id])

    def clear(self):
        with self.lock:
            self.closed = True
            for rows in (self.prepared, self.head_refs, self.originals, self.records, self.qualified_receipts, self.relations, self.identities):
                rows.clear()
        return True

    def observe_qualified(self, receipt):
        """Trusted typed owner port, unavailable until a qualified method supplies it.

        This is not an HTTP/operator action or a configurable callable. No built-in
        detector absence, hand release, LA label or elapsed timeout invokes it.
        """
        with self.lock:
            if (self.closed or not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS
                    or receipt['schema'] != 'home-qualified-person-lifecycle-receipt-1'
                    or type(receipt['version']) is not int or receipt['version'] != 1
                    or receipt['truth_role'] != 'runtime_qualified_method_result'
                    or receipt['session_ref'] != self.scope or receipt['source_id'] != self.source_id
                    or receipt['configuration_digest'] != self.configuration_digest
                    or type(receipt['source_generation']) is not int or receipt['source_generation'] < 1
                    or receipt['disposition'] not in {'use_ended_qualified', 'departure_qualified', 'absence_qualified'}
                    or not all(finite(receipt[key]) for key in ('observed_at', 'available_at', 'valid_until'))
                    or not 0 <= receipt['observed_at'] <= receipt['available_at'] <= self.clock() < receipt['valid_until']
                    or receipt['valid_until'] - receipt['observed_at'] > self.source['max_age_s']
                    or not isinstance(receipt['window'], dict) or set(receipt['window']) != {'start_at', 'end_at'}
                    or not all(finite(t) for t in receipt['window'].values())
                    or not 0 <= receipt['window']['start_at'] <= receipt['window']['end_at'] <= receipt['observed_at']
                    or not isinstance(receipt['basis_refs'], list) or len(receipt['basis_refs']) > 16):
                raise LifecycleRefusal('qualified_lifecycle_receipt_invalid')
            identifier(receipt['id'], 'qualified_lifecycle_receipt_id')
            identifier(receipt['capture_id'], 'qualified_lifecycle_capture_id')
            self._target(receipt['target_person_ref'], receipt['target_subject_ref'], receipt['disposition'])
            negative = receipt['disposition'] == 'use_ended_qualified'
            keys = METHOD_KEYS | (set() if negative else {'coverage_assessment_ref'})
            method = {key: receipt[key] for key in keys}
            if (method not in self.policy['negative_methods' if negative else 'absence_methods']
                    or receipt['coverage_status'] != ('not_applicable' if negative else 'qualified_current')
                    or negative and (receipt['coverage_assessment_ref'] is not None or receipt['target_subject_ref'] is None
                                     or not receipt['basis_refs'])):
                raise LifecycleRefusal('lifecycle_method_not_qualified')
            old = self.qualified_receipts.get(receipt['id'])
            if old is not None and old != receipt:
                raise LifecycleRefusal('qualified_lifecycle_receipt_identity_conflict')
            if old is None and len(self.qualified_receipts) >= self.max_captures * 2:
                raise LifecycleRefusal('qualified_lifecycle_receipt_capacity_exhausted')
            self.qualified_receipts[receipt['id']] = deepcopy(receipt)
        return True

    def remember_interaction(self, event, receipt):
        """Record actual committed positive basis for a later exact-relation end."""
        payload = event['payload']
        if payload.get('kind') != 'object_interaction' or payload.get('status') != 'supplied' or payload.get('value') is not True:
            return
        if (receipt.get('status') != 'accepted' or receipt.get('id') != event['id']
                or receipt.get('payload_digest') != digest(payload)):
            raise LifecycleRefusal('lifecycle_positive_basis_unconfirmed')
        ref = {key: receipt[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
        with self.lock:
            key = canonical(ref)
            if key not in self.relations and len(self.relations) >= self.max_captures:
                raise LifecycleRefusal('lifecycle_relation_basis_capacity_exhausted')
            self.relations[key] = deepcopy(event)

    def publish_receipts(self, context, *, prior):
        with self.lock:
            receipts = [deepcopy(r) for r in self.qualified_receipts.values() if r['capture_id'] == context['capture']['id']]
        outcomes = []
        for receipt in receipts:
            target_prior = prior
            if receipt['disposition'] in {'departure_qualified', 'absence_qualified'}:
                target_prior = self.identities.get(digest(receipt['target_person_ref']), prior)
            outcomes.append(self.publish_observation(context, receipt['disposition'], prior=target_prior, evidence=receipt))
        return outcomes

    def _evidence_binding(self, receipt, context, prior):
        if (prior is None or receipt['target_person_ref'] != prior['person_ref']
                    or receipt['source_generation'] != context['capture_payload']['source_generation']
                    or receipt['observed_at'] != context['capture_payload']['observed_at']
                    or receipt['disposition'] == 'use_ended_qualified' and receipt['target_subject_ref'] != prior['subject_ref']):
            raise LifecycleRefusal('qualified_lifecycle_receipt_capture_or_target_mismatch')
        # This successor provisions no method that can establish old-person
        # authority across a source/capture discontinuity. Fresh coverage alone
        # cannot transport an old target into the new generation; recovery then
        # requires the separately attributed applicable room-clearance path.
        if (prior['source_generation'] != context['capture_payload']['source_generation']
                or prior['capture_epoch'] != context['capture_payload']['head']['position']['capture_epoch']):
            raise LifecycleRefusal('cross_generation_lifecycle_method_unavailable')
        if receipt['disposition'] == 'use_ended_qualified':
            basis = [self.relations.get(canonical(ref)) for ref in receipt['basis_refs']]
            if not any(event and event['payload']['person_ref'] == receipt['target_person_ref']
                           and event['payload']['subject_ref'] == receipt['target_subject_ref']
                           and event['payload']['source_generation'] == receipt['source_generation']
                           and event['payload']['observed_at'] < receipt['window']['start_at'] for event in basis):
                raise LifecycleRefusal('qualified_end_use_prior_relation_required')

    def _target(self, person, subject, disposition):
        if person is None:
            if subject is not None or disposition not in {'observation_gap', 'association_reset'}:
                raise LifecycleRefusal('lifecycle_exact_prior_person_required')
            return
        if (not isinstance(person, dict) or set(person) != SCOPE | {'track_epoch', 'pid'}
                or any(person[key] != self.scope[key] for key in SCOPE)
                or not (type(person['pid']) is int and person['pid'] > 0 or isinstance(person['pid'], str))):
            raise LifecycleRefusal('lifecycle_exact_prior_person_required')
        identifier(person['track_epoch'], 'track_epoch')
        if isinstance(person['pid'], str):
            identifier(person['pid'], 'person_id')
        if subject is None and disposition in {'continuity_qualified', 'use_ended_qualified'}:
            raise LifecycleRefusal('lifecycle_exact_prior_subject_required')
        if subject is not None:
            if not isinstance(subject, dict) or set(subject) != {'subject_id', 'subject_epoch'}:
                raise LifecycleRefusal('lifecycle_exact_prior_subject_required')
            for value in subject.values():
                identifier(value, 'lifecycle_subject')

    def publish_observation(self, context, disposition, *, prior=None, diagnostics=None, evidence=None):
        """Freeze one original event per capture/target/method and retry exactly.

        Public output carries lineage and a method disposition, never old geometry
        under a new clock. Qualified end-use/absence uses only the separately
        validated typed receipt path below; it cannot be requested as a gap.
        """
        with self.lock:
            self._binding(context)
            continuity = self.policy['continuity']
            if disposition in {'continuity_qualified', 'observation_gap', 'association_reset'}:
                if evidence is not None or continuity is None:
                    raise LifecycleRefusal('lifecycle_method_not_qualified')
                method = {key: continuity[key] for key in METHOD_KEYS}
                coverage = None
            else:
                if (not isinstance(evidence, dict) or self.qualified_receipts.get(evidence.get('id')) != evidence
                        or evidence['capture_id'] != context['capture']['id'] or evidence['disposition'] != disposition):
                    raise LifecycleRefusal('qualified_lifecycle_receipt_required')
                method = {key: evidence[key] for key in METHOD_KEYS}
                coverage = deepcopy(evidence['coverage_assessment_ref'])
                self._evidence_binding(evidence, context, prior)
            payload = context['capture_payload']
            person = evidence['target_person_ref'] if evidence else prior['person_ref'] if prior else None
            subject = evidence['target_subject_ref'] if evidence else prior['subject_ref'] if prior else None
            self._target(person, subject, disposition)
            if prior is not None:
                identity_key = digest(prior['person_ref'])
                if identity_key not in self.identities and len(self.identities) >= self.max_captures:
                    raise LifecycleRefusal('lifecycle_prior_identity_capacity_exhausted')
                self.identities[identity_key] = deepcopy(prior)
            key = digest([context['capture']['id'], person, subject, method['method_ref']])
            fingerprint = digest([payload, prior, disposition, diagnostics, evidence])
            entry = self.prepared.get(key)
            if entry is not None:
                if entry['fingerprint'] != fingerprint:
                    raise LifecycleRefusal('lifecycle_identity_conflict')
                if entry['result'] is not None:
                    return deepcopy(entry['result'])
            else:
                if len(self.prepared) >= self.max_captures * 4:
                    raise LifecycleRefusal('lifecycle_capacity_exhausted')
                now = self.clock()
                valid_until = min(payload['valid_until'], evidence['valid_until']) if evidence else payload['valid_until']
                if (not finite(now) or now < payload['available_at']
                        or evidence and now < evidence['available_at']
                        or now + payload['clock']['uncertainty_s'] >= valid_until):
                    raise LifecycleRefusal('lifecycle_capture_expired')
                reference_keys = ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')
                current = {name: context['publication_receipt'][name] for name in reference_keys} | {'component_id': None}
                basis, ancestors = [current], [payload]
                if prior and prior.get('publication_ref') is not None:
                    basis.append(deepcopy(prior['publication_ref']))
                    ancestors.append(prior['publication']['payload'])
                if evidence:
                    for ref in evidence['basis_refs']:
                        if ref not in basis:
                            basis.append(deepcopy(ref))
                        event = self.relations.get(canonical(ref))
                        if event:
                            ancestors.append(event['payload'])
                roots, bindings, originals = {}, {}, {}
                for ancestor in ancestors:
                    roots.update(deepcopy(ancestor['observation_root_times']))
                    bindings.update(deepcopy(ancestor['sensor_root_bindings']))
                    originals.update({canonical(ref): deepcopy(ref) for ref in ancestor['original_receipt_refs']})
                if len(roots) > 32 or len(originals) >= 32:
                    raise LifecycleRefusal('lifecycle_ancestry_capacity_exhausted')
                observed = payload['observed_at']
                window = deepcopy(evidence['window']) if evidence else {'start_at': prior['observed_at'] if prior else observed, 'end_at': observed}
                if window['start_at'] > observed:
                    raise LifecycleRefusal('lifecycle_observation_order_invalid')
                receipt_id = 'lifecycle-original:' + key
                original = {'schema': 'home-original-person-lifecycle-1', 'version': 1,
                    'configuration_digest': self.configuration_digest, 'policy_ref': deepcopy(self.policy_ref),
                    'capture_id': context['capture']['id'], 'disposition': disposition,
                    'target_person_ref': deepcopy(person), 'target_subject_ref': deepcopy(subject),
                    'method': deepcopy(method), 'observed_at': observed, 'available_at': now,
                    'window': window, 'diagnostics': deepcopy(diagnostics), 'basis_refs': deepcopy(basis),
                    'qualified_receipt': deepcopy(evidence),
                    'observation_root_times': roots, 'sensor_root_bindings': bindings,
                    'truth_role': 'runtime_lifecycle_method_result'}
                ref = {'store_id': self.policy['receipt_store_id'], 'receipt_id': receipt_id,
                    'digest': digest(original), 'component_id': self.policy['receipt_component_id'],
                    'observed_at': observed, 'available_at': now, 'clock_mapping_id': payload['clock']['mapping_id']}
                originals[canonical(ref)] = ref
                body = {'schema': 'home-live-person-lifecycle-1', 'version': 1, 'id': 'lifecycle:' + key,
                    **deepcopy(self.scope), 'manifest_ref': deepcopy(payload['manifest_ref']),
                    'source_id': self.source_id, 'source_generation': payload['source_generation'],
                    'target_person_ref': deepcopy(person), 'target_subject_ref': deepcopy(subject),
                    'disposition': disposition, **deepcopy(method), 'coverage_assessment_ref': coverage,
                    'basis_refs': basis, 'head': {'key': {'source_id': self.source_id,
                        'payload_type': 'home-live-person-lifecycle-1', 'person_ref': deepcopy(person),
                        'subject_ref': deepcopy(subject), 'predicate': 'person_lifecycle', 'method_ref': deepcopy(method['method_ref'])},
                        'position': deepcopy(payload['head']['position']), 'supersedes': None},
                    'observed_at': observed, 'available_at': now,
                    'valid_until': valid_until,
                    'clock': deepcopy(payload['clock']), 'sensor_roots': sorted({r for values in bindings.values() for r in values}),
                    'observation_roots': sorted(roots), 'observation_root_times': roots, 'sensor_root_bindings': bindings,
                    'original_receipt_refs': list(originals.values()), 'window': window}
                body['head']['supersedes'] = deepcopy(self.head_refs.get(canonical(body['head']['key'])))
                event = {'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': body['id'],
                    'producer_id': self.source['producer_id'], 'stream_epoch': self.source['producer_epoch'],
                    **deepcopy(self.scope), **{name: body[name] for name in ('observed_at', 'available_at', 'valid_until')},
                    'payload_type': body['schema'], 'payload_version': '1.0', 'revision': self.revision, 'payload': body}
                self.originals[receipt_id] = original
                self.records[receipt_id] = {'ref': ref, 'capture_epoch': body['head']['position']['capture_epoch'],
                    'capture_seq': body['head']['position']['capture_seq'],
                    'observation_root_times': roots, 'sensor_root_bindings': bindings}
                entry = {'fingerprint': fingerprint, 'event': event, 'result': None}
                self.prepared[key] = entry
            event = entry['event']
            ack = self.publish(deepcopy(event))
            if (not isinstance(ack, dict) or ack.get('status') != 'accepted' or ack.get('id') != event['id']
                    or ack.get('payload_digest') != digest(event['payload'])):
                raise LifecycleRefusal('lifecycle_publication_unconfirmed')
            self.head_refs[canonical(event['payload']['head']['key'])] = {
                name: ack[name] for name in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
            entry['result'] = {'status': 'published', 'receipt': ack, 'publication': event}
            return deepcopy(entry['result'])

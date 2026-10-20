"""Per-observation relational proposals from owned capture and spatial receipts.

The named holding rule is experimental. It neither establishes reading accuracy
nor computes engagement persistence. Proximity, material visibility and missing
distinctions remain separate predicates. All numeric details stay owner-local.
"""
from copy import deepcopy
import math
import threading
import time

from journal import canonical, digest, finite, identifier
from live_capture import _original_digest
from live_ingress_validation import validate_config
from live_interaction_geometry import estimate_interaction, validate_material_policy
from live_spatial import _pin
from schema_registry import SchemaRegistry, LIVE_PROFILE


POLICY_KEYS = {'schema', 'version', 'capture_method_ref', 'spatial_method_ref', 'model_refs',
               'receipt_store_id', 'receipt_component_id', 'qualification_ref', 'material_geometry', 'max_age_s', 'semantic_model_refs'}


class InteractionRefusal(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def validate_policy(policy, source, method):
    if (not isinstance(policy, dict) or set(policy) != POLICY_KEYS
            or policy['schema'] != 'home-live-interaction-policy-1' or type(policy['version']) is not int
            or policy['version'] != 1 or not isinstance(policy['model_refs'], dict)
            or set(policy['model_refs']) != {'detector', 'pose'}
            or not isinstance(policy['semantic_model_refs'], list) or len(policy['semantic_model_refs']) > 2):
        raise ValueError('closed experimental interaction policy required')
    for ref in (method, policy['capture_method_ref'], policy['spatial_method_ref'],
                policy['qualification_ref'], *policy['model_refs'].values(), *policy['semantic_model_refs']):
        _pin(ref)
    if (method['digest'] != digest(policy) or method not in source['method_refs']
            or policy['capture_method_ref'] not in source['method_refs']
            or policy['spatial_method_ref'] not in source['association_method_refs']
            or any(ref not in source['model_refs'] for ref in policy['model_refs'].values())
            or any(ref not in source['model_refs'] for ref in policy['semantic_model_refs'])
            or len({canonical(ref) for ref in policy['semantic_model_refs']}) != len(policy['semantic_model_refs'])
            or policy['receipt_store_id'] not in source['receipt_store_ids']
            or policy['receipt_component_id'] not in source['receipt_component_ids']
            or policy['receipt_component_id'] in {'rgb', 'depth', 'spatial'}
            or not finite(policy['max_age_s']) or not 0 < policy['max_age_s'] <= source['max_age_s']):
        raise ValueError('registered interaction capability, ancestry and bounded lifetime required')
    identifier(policy['receipt_store_id'], 'receipt_store_id')
    identifier(policy['receipt_component_id'], 'receipt_component_id')
    validate_material_policy(policy['material_geometry'])
    if len(canonical(policy).encode()) > 65536:
        raise ValueError('bounded interaction policy required')


class LiveInteractionProducer:
    def __init__(self, live_config, *, source_id, revision, policy, method_ref, capture_input,
                 spatial_original, clock, publish, barrier, max_captures=24,
                 monotonic=time.monotonic, optional_sources=None, assessment_recorder=None, lifecycle=None):
        self.config = validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
        self.scope = self.config['manifest']['identity']
        self.source_id, self.revision = identifier(source_id, 'source_id'), identifier(revision, 'revision')
        self.source = self.config['manifest']['sources'][source_id]
        validate_policy(policy, self.source, method_ref)
        if (not all(callable(port) for port in (capture_input, spatial_original, clock, publish, barrier, monotonic))
                or type(max_captures) is not int or not 1 <= max_captures <= 24):
            raise ValueError('fixed bounded interaction capabilities required')
        self.policy, self.method = deepcopy(policy), deepcopy(method_ref)
        self.binding_digest = digest([self.policy, self.method, self.source])
        self.capture_input, self.spatial_original = capture_input, spatial_original
        self.clock, self.monotonic, self.publish, self.barrier = clock, monotonic, publish, barrier
        self.max_captures, self.optional_sources = max_captures, optional_sources
        self.assessment_recorder = assessment_recorder
        self.lifecycle = lifecycle
        self.lock = threading.Lock()
        self.closed = self.reset_requested = False
        self.originals, self.records, self.prepared, self.head_refs = {}, {}, {}, {}
        self.pending = None

    def resolve_receipt(self, store_id, receipt_id):
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.records[receipt_id])

    def original_receipt(self, store_id, receipt_id):
        """Owner-local diagnostics; never an HTTP geometry or media channel."""
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.originals[receipt_id])

    def reset(self, reason='source_unavailable'):
        self.reset_requested = True
        if not self.lock.acquire(False):
            return False
        try:
            self.pending = None
            self.reset_requested = False
            return True
        finally:
            self.lock.release()

    def clear(self):
        self.closed = True
        if not self.lock.acquire(False):
            return False
        try:
            self.pending = None
            for owned in (self.originals, self.records, self.prepared, self.head_refs):
                owned.clear()
            if self.optional_sources is not None:
                self.optional_sources.clear()
            return True
        finally:
            self.lock.release()

    def _check(self, context, authorized, deadline, cancel):
        cancelled = cancel() if callable(cancel) else cancel.is_set()
        if self.closed or self.reset_requested or cancelled or authorized() is not True:
            raise InteractionRefusal('interaction_not_authorized')
        if digest([self.policy, self.method, self.source]) != self.binding_digest:
            raise InteractionRefusal('interaction_policy_changed')
        now, mono = self.clock(), self.monotonic()
        capture = context['capture']
        if (not finite(now) or not finite(mono) or not finite(deadline) or mono >= deadline
                or not capture['available_at'] <= now < capture['effective_valid_until']):
            raise InteractionRefusal('interaction_capture_expired')
        return now

    def _inputs(self, context):
        if (context['schema'] != 'home-acquisition-local-context-1' or context['version'] != 1
                or context['session_ref'] != self.scope or context['source_id'] != self.source_id
                or context['availability_clock'] != 'utc_epoch_seconds'
                or context['model_availability_clock'] != 'monotonic_seconds'):
            raise InteractionRefusal('interaction_scope_or_clock_mismatch')
        numeric = self.capture_input(context['capture']['id'])
        if (numeric['capture'] != context['capture'] or numeric['capture_payload'] != context['capture_payload']
                or numeric['original_receipts'] != context['original_receipts']
                or context['capture_payload']['method_ref'] != self.policy['capture_method_ref']):
            raise InteractionRefusal('interaction_capture_mismatch')
        result = context['spatial']
        if (not isinstance(result, dict) or result.get('status') != 'supplied'
                or not isinstance(result.get('publication'), dict) or not isinstance(result.get('receipt'), dict)):
            raise InteractionRefusal('interaction_person_association_unavailable')
        event, ack = result['publication'], result['receipt']
        spatial = event['payload']
        if (ack.get('status') != 'accepted' or ack.get('id') != event['id']
                or ack.get('payload_digest') != digest(spatial)
                or spatial['method_ref'] != self.policy['spatial_method_ref']
                or spatial['head']['position'] != context['capture_payload']['head']['position']
                or spatial['association']['person_status'] != 'qualified'
                or spatial['source_generation'] != context['capture_payload']['source_generation']
                or spatial['source_id'] != self.source_id
                or any(spatial[key] != self.scope[key] for key in self.scope)):
            raise InteractionRefusal('interaction_spatial_mismatch')
        refs = [ref for ref in spatial['original_receipt_refs']
                if ref['component_id'] not in {'rgb', 'depth'}]
        if len(refs) != 1:
            raise InteractionRefusal('interaction_spatial_original_unavailable')
        original = self.spatial_original(refs[0]['store_id'], refs[0]['receipt_id'])
        if (refs[0]['digest'] != _original_digest(original)
                or original['capture_id'] != context['capture']['id']
                or original['model_refs'] != self.policy['model_refs']
                or original['detector'] != context['detector'] or original['pose'] != context['pose']
                or original['configuration_digest'] != context['configuration_digest']
                or original['detector_available_at'] != context['detector_available_at']
                or original['pose_available_at'] != context['pose_available_at']):
            raise InteractionRefusal('interaction_model_capture_mismatch')
        now = self.clock()
        if (not finite(ack.get('committed_at')) or not original['available_at'] <= ack['committed_at'] <= now
                or any(not finite(context[key]) or not context['capture']['available_at'] <= context[key] <= now
                       for key in ('detector_available_at', 'pose_available_at'))):
            raise InteractionRefusal('interaction_model_clock_invalid')
        return numeric, spatial, original, ack

    def _supplemental(self, context):
        if self.optional_sources is None:
            return {'schema': 'home-interaction-supplemental-1', 'version': 1,
                    'la': {'status': 'unavailable', 'reason': 'not_configured'},
                    'pc': {'status': 'unavailable', 'reason': 'qualified_person_device_attribution_unavailable'}}
        try:
            result = self.optional_sources.read(context)
            if not isinstance(result, dict) or len(canonical(result).encode()) > 65536:
                raise ValueError('bounded optional receipt required')
            return deepcopy(result)
        except Exception:
            return {'schema': 'home-interaction-supplemental-1', 'version': 1,
                    'la': {'status': 'unavailable', 'reason': 'optional_source_unavailable'},
                    'pc': {'status': 'unavailable', 'reason': 'qualified_person_device_attribution_unavailable'}}

    def _semantic(self, supplemental, spatial, now):
        la = supplemental.get('la', {})
        if (la.get('status') != 'fresh_proposal' or la.get('model_ref') not in self.policy['semantic_model_refs']
                or la.get('person_association') != 'qualified' or la.get('person_ref') != spatial['person_ref']
                or la.get('probability') is not None or la.get('observed_at') != spatial['observed_at']
                or not all(finite(la.get(key)) for key in ('available_at', 'valid_until'))
                or not la['observed_at'] <= la['available_at'] <= now
                or now + spatial['clock']['uncertainty_s'] >= la['valid_until']
                or set(la.get('sensor_roots', [])) != set(spatial['sensor_roots'])
                or not la.get('original_receipt_refs') or not isinstance(la.get('receipt'), dict)):
            return None
        return la

    def _predicates(self, estimate, spatial, semantic):
        rows = [('person_presence', 'supplied', 'present', 'qualified_person_detection', ['body'])]
        materials = estimate.get('materials', [])
        relations = estimate.get('relations', [])
        holding = [item for item in relations if item.get('holding_proposal') is True]
        unique = len(materials) == 1
        usable = (unique and len(holding) == 1 and estimate.get('status') != 'conflicted'
                  and spatial['subject_ref'] is not None
                  and all(spatial['parts'][key]['status'] == 'supplied' for key in ('work', 'hand_left', 'hand_right')))
        near = any(item.get('status') == 'near' for item in relations)
        ambiguous = len(materials) > 1 or estimate.get('status') == 'conflicted'
        rows.append(('material_state', 'supplied' if unique else 'unobservable',
                     'visible_state_unknown' if unique else None,
                     'qualified_material_state' if unique else 'subject_association_ambiguous' if ambiguous else 'material_occluded', []))
        semantic_kinds = set()
        if semantic is not None and unique and materials[0]['category'] == 'book':
            state, basis = semantic.get('material_state'), set(semantic.get('visible_basis', []))
            if state in {'open', 'closed'} and ('book_pages_open' if state == 'open' else 'book_closed') in basis:
                rows[-1] = ('material_state', 'supplied', state, 'open_material_no_use_claim' if state == 'open'
                            else 'qualified_material_state', [])
                semantic_kinds.add('material_state')
        if usable:
            rows.append(('hand_material_relation', 'supplied', 'supported_holding',
                         'per_capture_holding_without_motion_requirement', ['work', 'hand_left', 'hand_right']))
        elif near and unique:
            rows.append(('hand_material_relation', 'supplied', 'near', 'proximity_only', ['hand_left', 'hand_right']))
        else:
            rows.append(('hand_material_relation', 'unobservable', None,
                         'subject_association_ambiguous' if ambiguous else 'unsupported_distinction', []))
        rows.append(('object_interaction', 'supplied' if usable else 'unobservable', True if usable else None,
                     'qualified_object_interaction' if usable else 'subject_association_ambiguous' if ambiguous else 'unsupported_distinction',
                     ['work', 'hand_left', 'hand_right'] if usable else []))
        # A task label or held material alone cannot establish a refinement.
        # This explicit experimental conjunction retains both original sources.
        rows.append(('activity_refinement', 'unobservable', None, 'unsupported_distinction', []))
        if semantic is not None and usable:
            facts, basis = semantic['receipt']['facts'], set(semantic.get('visible_basis', []))
            reading = (materials[0]['category'] == 'book' and semantic.get('activity_proposal') == 'reading'
                       and facts['held_object'] == 'book' and facts['book_state'] == 'open'
                       and facts['gaze_target'] == 'book' and {'book_in_hands', 'book_pages_open'} <= basis)
            phone = (materials[0]['category'] == 'phone' and semantic.get('activity_proposal') == 'phone_use'
                     and facts['held_object'] == 'phone' and facts['gaze_target'] == 'phone'
                     and {'phone_in_hands', 'phone_screen_toward_face'} <= basis)
            if reading or phone:
                rows[-1] = ('activity_refinement', 'supplied', 'reading' if reading else 'phone_use',
                            'qualified_activity_refinement', ['work', 'hand_left', 'hand_right'])
                semantic_kinds.add('activity_refinement')
        return rows, semantic_kinds

    def _semantic_original(self, context, semantic, now):
        """Explicit adapter from same-camera LA media roots to original frames."""
        position = context['capture_payload']['head']['position']
        receipt_id = 'interaction-la-original:' + digest([self.method, context['capture']['id'], semantic['receipt']['request_id']])
        original = {'schema': 'home-original-interaction-la-1', 'version': 1,
                    'model_ref': semantic['model_ref'], 'receipt': semantic['receipt'],
                    'frame_bindings': semantic['frame_bindings'], 'qualification': 'uncalibrated_model_proposal',
                    'observed_at': semantic['observed_at'], 'available_at': now,
                    'original_available_at': semantic['available_at'], 'valid_until': semantic['valid_until'],
                    'observation_root_times': semantic['observation_root_times'],
                    'sensor_root_bindings': semantic['sensor_root_bindings'], 'truth_role': 'runtime_model_proposal'}
        ref = {'store_id': self.policy['receipt_store_id'], 'receipt_id': receipt_id,
               'digest': _original_digest(original), 'component_id': self.policy['receipt_component_id'],
               'observed_at': semantic['observed_at'], 'available_at': now,
               'clock_mapping_id': self.source['capture_clock']['mapping_id']}
        self.originals[receipt_id] = deepcopy(original)
        self.records[receipt_id] = {'ref': ref, 'capture_epoch': position['capture_epoch'], 'capture_seq': position['capture_seq'],
            'observation_root_times': deepcopy(semantic['observation_root_times']),
            'sensor_root_bindings': deepcopy(semantic['sensor_root_bindings'])}
        return ref

    def _prepare(self, context, numeric, spatial, spatial_original, ack, fingerprint, authorized, deadline, cancel):
        estimate = estimate_interaction(numeric, context['detector']['result'], spatial_original, self.policy, self.source)
        supplemental = self._supplemental(context)
        # Availability belongs to completion, never the pre-computation tick.
        now = self._check(context, authorized, deadline, cancel)
        semantic = self._semantic(supplemental, spatial, now)
        if self.assessment_recorder is not None and self.optional_sources is not None:
            self.assessment_recorder.semantic_check(context['capture']['id'], supplemental.get('la', {}),
                                                   admitted=semantic is not None, checked_at=now)
        rows, semantic_kinds = self._predicates(estimate, spatial, semantic)
        semantic_ref = self._semantic_original(context, semantic, now) if semantic_kinds else None
        capture = context['capture_payload']
        receipt_id = 'interaction-original:' + digest([self.scope, self.source_id, self.method, context['capture']['id']])
        original = {'schema': 'home-original-interaction-1', 'version': 1,
                    'capture_id': context['capture']['id'], 'capture_position': deepcopy(capture['head']['position']),
                    'method_ref': self.method, 'qualification_ref': self.policy['qualification_ref'],
                    'model_refs': self.policy['model_refs'], 'configuration_digest': context['configuration_digest'],
                    'observed_at': capture['observed_at'], 'available_at': now,
                    'person_ref': deepcopy(spatial['person_ref']), 'subject_ref': deepcopy(spatial['subject_ref']),
                    'estimate': estimate, 'supplemental': supplemental,
                    'original_receipt_refs': deepcopy(spatial['original_receipt_refs']),
                    'observation_root_times': deepcopy(capture['observation_root_times']),
                    'sensor_root_bindings': deepcopy(capture['sensor_root_bindings']),
                    'truth_role': 'experimental_runtime_interpretation',
                    'unavailable_components': ['qualified_person_device_attribution', 'material_open_closed_without_semantic_support',
                                               'contact_truth', 'manipulation_truth', 'activity_truth'],
                    'method_limitations': ['box_foreground_can_include_hands_or_support_surface',
                                           'two_hand_enclosure_is_an_experimental_holding_hypothesis',
                                           'not_independent_evaluation_truth']}
        ref = {'store_id': self.policy['receipt_store_id'], 'receipt_id': receipt_id,
               'digest': _original_digest(original), 'component_id': self.policy['receipt_component_id'],
               'observed_at': capture['observed_at'], 'available_at': now,
               'clock_mapping_id': self.source['capture_clock']['mapping_id']}
        refs = deepcopy(spatial['original_receipt_refs']) + [ref]
        parent = {key: ack[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
        events = []
        for kind, status, value, reason, required in rows:
            valid = min(capture['valid_until'], spatial['valid_until'],
                        math.nextafter(capture['observed_at'] + self.policy['max_age_s'], -math.inf),
                        math.nextafter(min(capture['observed_at'], numeric['capture']['depth_observed_at'])
                                       + self.source['part_policies']['body']['age']['max_age_s'], -math.inf))
            # The spatial producer qualifies identity from observed torso
            # anchors using the body bound even if a limb is unobservable.
            # Do not extend that association past its commissioned horizon.
            parents = [deepcopy(parent)]
            for name in required:
                part = spatial['parts'][name]
                if part['status'] != 'supplied':
                    status, value, reason = 'unobservable', None, 'required_part_unobservable'
                else:
                    valid = min(valid, part['valid_until'])
                    component = deepcopy(parent)
                    component['component_id'] = 'parts.' + name
                    parents.append(component)
            if now + capture['clock']['uncertainty_s'] >= valid:
                raise InteractionRefusal('interaction_support_expired')
            key = {'source_id': self.source_id, 'payload_type': 'home-live-fact-1',
                   'person_ref': deepcopy(spatial['person_ref']), 'subject_ref': deepcopy(spatial['subject_ref']),
                   'predicate': kind, 'method_ref': self.method}
            payload = {key: deepcopy(capture[key]) for key in (*self.scope, 'manifest_ref', 'observed_at', 'source_id',
                       'sensor_roots', 'observation_roots', 'observation_root_times', 'sensor_root_bindings', 'clock', 'source_generation')}
            payload.update(schema='home-live-fact-1', version=1,
                id='interaction:' + digest([self.scope, self.source_id, self.method, context['capture']['id'], kind]),
                person_ref=deepcopy(spatial['person_ref']), subject_ref=deepcopy(spatial['subject_ref']),
                association=deepcopy(spatial['association']),
                head={'key': key, 'position': deepcopy(capture['head']['position']),
                      'supersedes': deepcopy(self.head_refs.get(canonical(key)))},
                available_at=now, valid_until=valid, method_ref=self.method, layer='interpretation', kind=kind,
                status=status, value=value, assertion_basis='inferred', reason=reason,
                model_ref=deepcopy(self.policy['model_refs']['detector']), probability=None, conflicting_refs=[],
                parent_refs=parents, original_receipt_refs=refs)
            if kind in semantic_kinds:
                payload['model_ref'] = deepcopy(semantic['model_ref'])
                payload['valid_until'] = min(valid, semantic['valid_until'])
                payload['observation_root_times'].update(deepcopy(semantic['observation_root_times']))
                payload['sensor_root_bindings'].update(deepcopy(semantic['sensor_root_bindings']))
                payload['observation_roots'] = sorted(payload['observation_root_times'])
                payload['original_receipt_refs'] = list({canonical(item): item for item in
                    [*refs, *deepcopy(semantic['original_receipt_refs']), deepcopy(semantic_ref)]}.values())
            events.append({'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': payload['id'],
                'producer_id': self.source['producer_id'], 'stream_epoch': self.source['producer_epoch'], **self.scope,
                **{key: payload[key] for key in ('observed_at', 'available_at', 'valid_until')},
                'payload_type': 'home-live-fact-1', 'payload_version': '1.0', 'revision': self.revision, 'payload': payload})
        self.originals[receipt_id] = original
        self.records[receipt_id] = {'ref': ref, 'capture_epoch': capture['head']['position']['capture_epoch'],
            'capture_seq': capture['head']['position']['capture_seq'],
            'observation_root_times': deepcopy(capture['observation_root_times']),
            'sensor_root_bindings': deepcopy(capture['sensor_root_bindings'])}
        entry = {'fingerprint': fingerprint, 'events': events, 'acks': [], 'result': None,
                 'attempted': set(), 'semantic': deepcopy(semantic) if semantic_kinds else None,
                 'semantic_ids': {event['id'] for event in events if event['payload']['kind'] in semantic_kinds}}
        self.prepared[context['capture']['id']] = entry
        self.pending = context['capture']['id']
        return entry

    def _fallback(self, event, context, spatial):
        """Freeze a local alternative before any optional publication attempt."""
        event = deepcopy(event)
        payload = event['payload']
        ref = next(ref for ref in payload['original_receipt_refs']
                   if ref['receipt_id'].startswith('interaction-original:'))
        original = self.originals[ref['receipt_id']]
        payload['original_receipt_refs'] = deepcopy(original['original_receipt_refs']) + [deepcopy(ref)]
        payload['observation_root_times'] = deepcopy(original['observation_root_times'])
        payload['sensor_root_bindings'] = deepcopy(original['sensor_root_bindings'])
        payload['observation_roots'] = sorted(payload['observation_root_times'])
        payload['model_ref'] = deepcopy(self.policy['model_refs']['detector'])
        # Drop only the optional horizon. Keep every original local bound;
        # otherwise a nearly expired LA receipt could stop independent input.
        valid = min(context['capture_payload']['valid_until'], spatial['valid_until'],
                    math.nextafter(payload['observed_at'] + self.policy['max_age_s'], -math.inf),
                    math.nextafter(payload['observed_at'] + self.source['part_policies']['body']['age']['max_age_s'], -math.inf))
        if payload['kind'] == 'activity_refinement':
            valid = min(valid, *(spatial['parts'][name]['valid_until'] for name in ('work', 'hand_left', 'hand_right')))
        event['valid_until'] = payload['valid_until'] = valid
        if payload['kind'] == 'material_state':
            payload.update(status='supplied', value='visible_state_unknown', reason='qualified_material_state')
        else:
            payload.update(status='unobservable', value=None, reason='model_unavailable')
        payload['id'] = 'interaction:' + digest([event['id'], 'optional_source_unavailable'])
        event['id'] = payload['id']
        return event

    def offer(self, context, *, authorized, deadline, cancel):
        if not self.lock.acquire(False):
            return {'status': 'refused', 'reason': 'interaction_busy', 'receipt': None, 'publication': None}
        try:
            now = self._check(context, authorized, deadline, cancel)
            numeric, spatial, original, ack = self._inputs(context)
            fingerprint, key = _original_digest(context), context['capture']['id']
            if self.pending is not None and self.pending != key:
                raise InteractionRefusal('prior_interaction_publication_unconfirmed')
            if key in self.prepared:
                entry = self.prepared[key]
                if entry['fingerprint'] != fingerprint:
                    raise InteractionRefusal('interaction_capture_identity_conflict')
                if any(now + event['payload']['clock']['uncertainty_s'] >= event['valid_until'] for event in entry['events']):
                    raise InteractionRefusal('interaction_support_expired')
                if entry['result'] is not None:
                    return deepcopy(entry['result'])
            else:
                if len(self.prepared) >= self.max_captures:
                    raise InteractionRefusal('interaction_capacity_exhausted')
                entry = self._prepare(context, numeric, spatial, original, ack, fingerprint, authorized, deadline, cancel)
            for index in range(len(entry['acks']), len(entry['events'])):
                event = entry['events'][index]
                now = self._check(context, authorized, deadline, cancel)
                if event['id'] in entry['semantic_ids']:
                    supplemental = self._supplemental(context)
                    checked_at = self.clock()
                    current = self._semantic(supplemental, spatial, checked_at)
                    if self.assessment_recorder is not None and self.optional_sources is not None:
                        self.assessment_recorder.semantic_check(key, supplemental.get('la', {}),
                            admitted=current is not None and current['receipt'] == entry['semantic']['receipt'],
                            checked_at=checked_at)
                    if current is None or current['receipt'] != entry['semantic']['receipt']:
                        if event['id'] in entry['attempted']:
                            # A timed-out write may already be durable. Never
                            # replace its payload or guess the current head.
                            raise InteractionRefusal('interaction_optional_publication_unconfirmed')
                        event = self._fallback(event, context, spatial)
                        entry['events'][index] = event
                    now = self._check(context, authorized, deadline, cancel)
                if now + event['payload']['clock']['uncertainty_s'] >= event['valid_until']:
                    raise InteractionRefusal('interaction_support_expired')
                entry['attempted'].add(event['id'])
                receipt = self.publish(deepcopy(event))
                if (not isinstance(receipt, dict) or receipt.get('status') != 'accepted'
                        or receipt.get('id') != event['id'] or receipt.get('payload_digest') != digest(event['payload'])):
                    raise InteractionRefusal('interaction_publication_unconfirmed')
                entry['acks'].append(receipt)
                if self.assessment_recorder is not None:
                    self.assessment_recorder.interaction_publication(key, event, receipt)
                if self.lifecycle is not None:
                    self.lifecycle.remember_interaction(event, receipt)
                self.head_refs[canonical(event['payload']['head']['key'])] = {
                    key: receipt[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
            self.pending = None
            entry['result'] = {'status': 'supplied', 'reason': 'per_observation_interpretations_published',
                'receipt': entry['acks'][-1], 'publication': entry['events'][-1],
                'publications': deepcopy(entry['events']), 'receipts': deepcopy(entry['acks'])}
            return deepcopy(entry['result'])
        except Exception as error:
            return {'status': 'refused', 'reason': getattr(error, 'reason', 'interaction_unavailable'), 'receipt': None, 'publication': None}
        finally:
            if self.reset_requested:
                self.pending = None
                self.reset_requested = False
            self.lock.release()

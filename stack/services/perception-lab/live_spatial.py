"""Single-person spatial estimates before semantic word projection.

Local capabilities bind qualified capture, fixed resident model results and
commissioned uncertainty. This module owns anonymous geometric continuity, not
engagement, preferences, object identity, gaze or lighting. Numeric support and
model provenance stay in bounded local original receipts; the existing closed
geometry wire contract is unchanged.
"""
from copy import deepcopy
import math
import threading
import time

from journal import canonical, digest, finite, identifier
from live_capture import PARTS, _original_digest
from live_ingress_validation import validate_config
from schema_registry import SchemaRegistry, LIVE_PROFILE
from live_spatial_geometry import estimate_geometry, pairing_budget_ok

POLICY_KEYS = {'schema', 'version', 'capture_method_ref', 'model_refs', 'receipt_store_id',
               'receipt_component_id', 'qualification_ref', 'sampling', 'continuity', 'work_margin_m', 'anatomy'}
ANATOMY_KEYS = {'torso_radius_m', 'limb_radius_m', 'hand_radius_m', 'head_half_extents_m',
                'protection_margin_m', 'min_shoulder_baseline_m'}
WIRE_REASONS = {'pose_unavailable', 'missing_measurement_bound', 'depth_unavailable',
                'required_part_unobservable', 'front_back_ambiguous', 'unsupported_distinction'}
REASON_MAP = {'insufficient_depth_support': 'depth_unavailable',
              'depth_spread_exceeded': 'missing_measurement_bound',
              'body_direction_unavailable': 'missing_measurement_bound',
              'head_direction_unvalidated': 'unsupported_distinction'}


class SpatialRefusal(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _pin(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'digest'}
            or not isinstance(value['digest'], str) or len(value['digest']) != 64
            or any(c not in '0123456789abcdef' for c in value['digest'])):
        raise ValueError('explicit immutable spatial policy pins required')
    identifier(value['id'], 'pin_id')


def _positive(value, maximum):
    return finite(value) and 0 < value <= maximum


def validate_policy(policy, source, method):
    if (not isinstance(policy, dict) or set(policy) != POLICY_KEYS
            or policy['schema'] != 'home-live-spatial-policy-1' or type(policy['version']) is not int
            or policy['version'] != 1 or not isinstance(policy['model_refs'], dict)
            or set(policy['model_refs']) != {'detector', 'pose'}):
        raise ValueError('closed commissioned spatial policy required')
    for ref in (method, policy['capture_method_ref'], policy['qualification_ref'], *policy['model_refs'].values()):
        _pin(ref)
    if (method['digest'] != digest(policy) or method not in source['method_refs']
            or method not in source['association_method_refs']
            or policy['capture_method_ref'] not in source['method_refs']
            or any(ref not in source['model_refs'] for ref in policy['model_refs'].values())
            or policy['receipt_store_id'] not in source['receipt_store_ids']
            or policy['receipt_component_id'] not in source['receipt_component_ids']
            or policy['receipt_component_id'] in {'rgb', 'depth'}):
        raise ValueError('spatial methods, models and local receipt capability must be registered')
    identifier(policy['receipt_store_id'], 'receipt_store_id')
    identifier(policy['receipt_component_id'], 'receipt_component_id')
    sampling, continuity, anatomy = policy['sampling'], policy['continuity'], policy['anatomy']
    if (not isinstance(sampling, dict) or set(sampling) != {'radius_px', 'min_support', 'max_spread_m'}
            or not _positive(sampling['radius_px'], 16) or type(sampling['min_support']) is not int
            or not 3 <= sampling['min_support'] <= 512 or not _positive(sampling['max_spread_m'], 1)
            or not isinstance(continuity, dict) or set(continuity) != {'max_gap_s', 'max_speed_m_s', 'max_step_m'}
            or not _positive(continuity['max_gap_s'], source['max_age_s'])
            or not finite(continuity['max_speed_m_s']) or not 0 <= continuity['max_speed_m_s'] <= 10
            or not _positive(continuity['max_step_m'], 5)
            or not isinstance(anatomy, dict) or set(anatomy) != ANATOMY_KEYS):
        raise ValueError('bounded sampling, anatomy and continuity policy required')
    for name in ('torso_radius_m', 'limb_radius_m', 'hand_radius_m', 'min_shoulder_baseline_m'):
        if not _positive(anatomy[name], 2):
            raise ValueError('positive commissioned anatomy dimensions required')
    for value, allow_zero in ((policy['work_margin_m'], False), (anatomy['head_half_extents_m'], False),
                              (anatomy['protection_margin_m'], True)):
        if (not isinstance(value, list) or len(value) != 3 or any(not finite(v) or v < 0 or v > 2
                or not allow_zero and v == 0 for v in value)):
            raise ValueError('commissioned per-axis enclosure dimensions required')
    if len(canonical(policy).encode()) > 65536:
        raise ValueError('bounded spatial policy required')


class LiveSpatialProducer:
    def __init__(self, live_config, *, source_id, revision, policy, method_ref, capture_input,
                 clock, publish, barrier, max_captures=24, monotonic=time.monotonic, assessment_recorder=None, lifecycle=None):
        self.config = validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
        self.scope = self.config['manifest']['identity']
        self.source_id, self.revision = identifier(source_id, 'source_id'), identifier(revision, 'revision')
        self.source = self.config['manifest']['sources'][source_id]
        validate_policy(policy, self.source, method_ref)
        if (not all(callable(port) for port in (capture_input, clock, publish, barrier, monotonic))
                or type(max_captures) is not int or not 1 <= max_captures <= 24):
            raise ValueError('fixed bounded spatial capabilities required')
        self.policy, self.method = deepcopy(policy), deepcopy(method_ref)
        self.binding_digest = digest([self.policy, self.method, self.source])
        self.capture_input, self.clock, self.publish, self.barrier, self.monotonic = capture_input, clock, publish, barrier, monotonic
        self.max_captures = max_captures
        self.assessment_recorder = assessment_recorder
        if lifecycle is not None:
            from live_person_lifecycle import LivePersonLifecycle
            if (not isinstance(lifecycle, LivePersonLifecycle) or (self.config['schema'], self.config['version']) not in {
                    ('home-live-ingress-config-6', 6), ('home-live-ingress-config-7', 7), ('home-live-ingress-config-9', 9), ('home-live-ingress-config-10', 10), ('home-live-ingress-config-11', 11), ('home-live-ingress-config-12', 12), ('home-live-ingress-config-13', 13)}):
                raise ValueError('fixed lifecycle capability requires explicit config6')
        self.lifecycle = lifecycle
        self.lock = threading.Lock()
        self.closed = False
        self.epoch = 0
        self.track = self.subject = None
        self.last_observed = self.last_anchor = self.last_ref = self.last_generation = self.last_sequence = None
        self.last_publication = None
        self.prepared, self.originals, self.records = {}, {}, {}
        self.head_refs = {}
        self.pending = None
        self.reset_requested = False

    def resolve_receipt(self, store_id, receipt_id):
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.records[receipt_id])

    def original_receipt(self, store_id, receipt_id):
        """Owner-local provenance, not an HTTP/semantic expansion route."""
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.originals[receipt_id])

    def _reset(self):
        self.epoch += 1
        self.track = self.subject = None
        self.last_observed = self.last_anchor = self.last_ref = self.last_generation = self.last_sequence = None
        self.last_publication = None
        self.pending = None

    def reset(self, reason='source_unavailable'):
        self.reset_requested = True
        if not self.lock.acquire(False):
            return False
        try:
            self._reset()
            self.reset_requested = False
            return True
        finally:
            self.lock.release()

    def clear(self):
        self.closed = True
        if not self.lock.acquire(False):
            return False
        try:
            self._reset()
            self.prepared.clear()
            self.originals.clear()
            self.records.clear()
            self.head_refs.clear()
            return True
        finally:
            self.lock.release()

    def _check(self, context, authorized, deadline, cancel):
        cancelled = cancel() if callable(cancel) else cancel.is_set()
        if self.closed or cancelled or authorized() is not True:
            raise SpatialRefusal('spatial_not_authorized')
        if digest([self.policy, self.method, self.source]) != self.binding_digest:
            raise SpatialRefusal('spatial_policy_changed')
        now, mono = self.clock(), self.monotonic()
        capture = context['capture']
        if any(not finite(context[key]) or now < context[key]
               for key in ('detector_available_at', 'pose_available_at')):
            raise SpatialRefusal('spatial_model_clock_invalid')
        if (not finite(now) or not finite(mono) or not finite(deadline) or mono >= deadline
                or not capture['available_at'] <= now < capture['effective_valid_until']):
            raise SpatialRefusal('spatial_capture_expired')
        return now

    def _association_lost(self, reason):
        self._reset()
        result = self.barrier(reason)
        return {'status': 'unobservable' if result.get('withdrawal') == 'recorded' else 'refused',
                'reason': reason, 'receipt': result.get('receipt'), 'publication': result.get('publication')}

    def _inputs(self, context):
        if (context['schema'] != 'home-acquisition-local-context-1' or context['version'] != 1
                or context['session_ref'] != self.scope or context['source_id'] != self.source_id):
            raise SpatialRefusal('spatial_scope_mismatch')
        if (context['availability_clock'] != 'utc_epoch_seconds'
                or context['model_availability_clock'] != 'monotonic_seconds'):
            raise SpatialRefusal('spatial_model_clock_invalid')
        numeric = self.capture_input(context['capture']['id'])
        if (numeric['capture'] != context['capture'] or numeric['capture_payload'] != context['capture_payload']
                or numeric['original_receipts'] != context['original_receipts']
                or context['capture_payload']['method_ref'] != self.policy['capture_method_ref']
                or context['capture_payload']['registration'] != self.source['registration']):
            raise SpatialRefusal('spatial_capture_mismatch')
        p = context['capture_payload']
        if any(p[key] != self.scope[key] for key in self.scope):
            raise SpatialRefusal('spatial_scope_mismatch')
        frame_id = context['capture']['id']
        detector, pose = context['detector'], context['pose']
        if (detector['frame_id'] != frame_id or pose['frame_id'] != frame_id
                or detector['model_ref'] != self.policy['model_refs']['detector']
                or pose['model_ref'] != self.policy['model_refs']['pose']
                or pose['detector_ref'] != detector['model_ref']
                or pose['result']['frame_id'] != frame_id or detector['result']['frame_id'] != frame_id
                or detector['proposals'] != pose['proposals']
                or any(proposal['frame_id'] != frame_id for proposal in detector['proposals'])):
            raise SpatialRefusal('spatial_model_capture_mismatch')
        for key in ('detector_available_at', 'pose_available_at'):
            if not finite(context[key]) or not context['capture']['available_at'] <= context[key] <= self.clock():
                raise SpatialRefusal('spatial_model_clock_invalid')
        if context['pose_available_at'] < context['detector_available_at']:
            raise SpatialRefusal('spatial_model_clock_invalid')
        if (not finite(detector['available_at']) or not finite(pose['available_at'])
                or not 0 <= detector['available_at'] <= pose['available_at'] <= self.monotonic()):
            raise SpatialRefusal('spatial_model_clock_invalid')
        instances = pose['result']['pose_instances']
        proposals = pose['proposals']
        if (len(instances) != len(proposals) or any(instance['instance_id'] != proposal['id']
                or instance['crop_xyxy'] != proposal['xyxy'] for instance, proposal in zip(instances, proposals))):
            raise SpatialRefusal('spatial_pose_association_mismatch')
        return numeric, instances

    def _identity(self, payload, estimate, numeric):
        if estimate is None:
            return False
        observed = payload['observed_at']
        position = payload['head']['position']
        anchor = estimate['anchor_m']
        body_policy = self.source['part_policies']['body']
        if (anchor is None or body_policy is None
                or not pairing_budget_ok(numeric, body_policy)
                or body_policy['components_m']['registration'] < numeric['registration']['error_bound_m']
                or any(estimate['joints'][index]['status'] != 'measured'
                       or estimate['joints'][index]['spread_m'] + numeric['projection']['error_bound_m']
                           > body_policy['components_m']['depth'] for index in (5, 6, 11, 12))):
            return False
        if self.track is not None:
            dt = observed - self.last_observed
            policy = self.policy['continuity']
            error = body_policy['bound_at_observation_m']
            distance = math.dist(anchor, self.last_anchor)
            if (payload['source_generation'] != self.last_generation
                    or position['capture_seq'] <= self.last_sequence
                    or not 0 < dt <= policy['max_gap_s'] or distance > policy['max_step_m']
                    or distance > policy['max_speed_m_s'] * dt + 2 * error):
                return False
        else:
            name = digest([self.scope, self.source_id, self.method, self.epoch, position, payload['source_generation']])
            self.track = {**self.scope, 'pid': 1, 'track_epoch': 'track:' + name}
        if estimate['parts']['work'].get('shape') is not None and self.subject is None:
            self.subject = {'subject_id': 'wrist_working_region', 'subject_epoch': 'subject:' + digest([self.track, self.epoch])}
        return True

    def _prior_identity(self):
        if self.track is None or self.last_observed is None:
            return None
        return {'person_ref': deepcopy(self.track), 'subject_ref': deepcopy(self.subject),
                'observed_at': self.last_observed, 'anchor_m': deepcopy(self.last_anchor),
                'source_generation': self.last_generation, 'capture_seq': self.last_sequence,
                'capture_epoch': self.last_publication['payload']['head']['position']['capture_epoch'],
                'publication_ref': deepcopy(self.last_ref), 'publication': deepcopy(self.last_publication)}

    def _lifecycle_observation(self, context, numeric, estimate, person_count):
        prior = self._prior_identity()
        decision = self.lifecycle.consider(context, numeric, estimate, prior, person_count=person_count)
        receipts = self.lifecycle.publish_receipts(context, prior=prior)
        decision['qualified_method_refs'] = [row['publication']['payload']['method_ref'] for row in receipts]
        if any(row['publication']['payload']['disposition'] in {'departure_qualified', 'absence_qualified'} for row in receipts):
            self._reset()
            return decision, prior, {'status': 'unobservable', 'reason': 'qualified_person_departure',
                'receipt': receipts[-1]['receipt'], 'publication': receipts[-1]['publication'], 'lifecycle': receipts}
        if decision['action'] in {'gap', 'reset'}:
            if self.lifecycle.policy['continuity'] is None:
                return decision, prior, self._association_lost('association_lost')
            kind = 'association_reset' if decision['action'] == 'reset' else 'observation_gap'
            if prior is not None or person_count:
                receipts.append(self.lifecycle.publish_observation(context, kind, prior=prior, diagnostics=decision))
            # An observed extra/unattributed person is separately addressable;
            # it cannot be hidden behind the old person's reset event.
            if person_count > 1 and prior is not None:
                receipts.append(self.lifecycle.publish_observation(context, 'association_reset', diagnostics=decision))
            if decision['action'] == 'reset':
                self._reset()
            return decision, prior, {'status': 'unobservable', 'reason': decision['reason'],
                'receipt': receipts[-1]['receipt'] if receipts else None,
                'publication': receipts[-1]['publication'] if receipts else None, 'lifecycle': receipts}
        return decision, prior, None

    def _prepare(self, context, numeric, estimate, fingerprint, now, association_method=None):
        capture = context['capture_payload']
        observed, valid = capture['observed_at'], capture['valid_until']
        position = capture['head']['position']
        receipt_id = 'spatial-original:' + digest([self.scope, self.source_id, self.method, context['capture']['id']])
        original = {'schema': 'home-original-spatial-measurement-1', 'version': 1,
                    'capture_id': context['capture']['id'], 'capture_position': deepcopy(position),
                    'method_ref': self.method, 'qualification_ref': self.policy['qualification_ref'],
                    'model_refs': self.policy['model_refs'], 'configuration_digest': context['configuration_digest'],
                    'observed_at': observed, 'available_at': now,
                    'detector_available_at': context['detector_available_at'], 'pose_available_at': context['pose_available_at'],
                    'model_availability_clock': context['model_availability_clock'],
                    'detector': deepcopy(context['detector']), 'pose': deepcopy(context['pose']),
                    'original_receipt_refs': deepcopy(capture['original_receipt_refs']),
                    'observation_root_times': deepcopy(capture['observation_root_times']),
                    'sensor_root_bindings': deepcopy(capture['sensor_root_bindings']),
                    'registration': deepcopy(capture['registration']), 'joints': estimate['joints'],
                    'part_diagnostics': {name: estimate['parts'][name].get('reason') for name in PARTS},
                    'estimate_basis': 'wrist_surface_region_and_pose_depth_envelopes',
                    'truth_role': 'runtime_model_estimate'}
        ref = {'store_id': self.policy['receipt_store_id'], 'receipt_id': receipt_id,
               'digest': _original_digest(original), 'component_id': self.policy['receipt_component_id'],
               'observed_at': observed, 'available_at': now,
               'clock_mapping_id': self.source['capture_clock']['mapping_id']}
        refs = deepcopy(capture['original_receipt_refs']) + [ref]
        parts = {}
        for name in PARTS:
            value, policy = estimate['parts'][name], self.source['part_policies'][name]
            if value.get('shape') is None or policy is None:
                reason = value.get('reason', 'missing_measurement_bound')
                reason = REASON_MAP.get(reason, reason)
                parts[name] = {'status': 'unobservable', 'reason': reason if reason in WIRE_REASONS else 'required_part_unobservable',
                               'support_refs': refs, 'observed_at': observed, 'valid_until': valid}
                continue
            uncertainty = deepcopy(policy)
            uncertainty['age']['origin_at'] = observed
            # Geometry uses the earlier depth exposure. The RGB association
            # radius covers motion up to RGB, but cannot renew the depth lease.
            until = min(valid, math.nextafter(min(observed, numeric['capture']['depth_observed_at'])
                                               + policy['age']['max_age_s'], -math.inf))
            if now + capture['clock']['uncertainty_s'] >= until:
                parts[name] = {'status': 'unobservable', 'reason': 'stale_capture', 'support_refs': refs,
                               'observed_at': observed, 'valid_until': valid}
                continue
            parts[name] = {'status': 'supplied', 'basis': 'inferred', 'shape': value['shape'],
                           'uncertainty': uncertainty, 'support_refs': refs, 'observed_at': observed,
                           'valid_until': until, 'method_ref': self.method}
        supplied = any(part['status'] == 'supplied' for part in parts.values())
        payload = deepcopy(capture)
        payload.update(id='spatial:' + digest([self.scope, self.source_id, self.method, context['capture']['id']]),
                       person_ref=deepcopy(self.track), subject_ref=deepcopy(self.subject),
                       association={'person_status': 'qualified',
                                    'subject_status': 'qualified' if self.subject is not None else 'not_applicable',
                                    'association_id': 'association:' + digest([self.track, self.subject]), 'generation': self.epoch + 1,
                                    'method_ref': association_method or self.method, 'candidate_person_refs': [], 'candidate_subject_refs': []},
                       status='supplied' if supplied else 'unobservable',
                       reason='qualified_coarse_bounds' if supplied else 'required_part_unobservable',
                       available_at=now, method_ref=self.method, parts=parts, parent_refs=[], original_receipt_refs=refs)
        payload['head'] = {'key': {'source_id': self.source_id, 'payload_type': 'home-live-geometry-1',
                                  'person_ref': deepcopy(self.track), 'subject_ref': deepcopy(self.subject),
                                  'predicate': 'geometry_bundle', 'method_ref': self.method},
                           'position': deepcopy(position), 'supersedes': None}
        payload['head']['supersedes'] = deepcopy(self.head_refs.get(canonical(payload['head']['key'])))
        event = {'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': payload['id'],
                 'producer_id': self.source['producer_id'], 'stream_epoch': self.source['producer_epoch'], **self.scope,
                 **{key: payload[key] for key in ('observed_at', 'available_at', 'valid_until')},
                 'payload_type': 'home-live-geometry-1', 'payload_version': '1.0', 'revision': self.revision, 'payload': payload}
        self.originals[receipt_id] = original
        self.records[receipt_id] = {'ref': ref, 'capture_epoch': position['capture_epoch'], 'capture_seq': position['capture_seq'],
                                    'observation_root_times': deepcopy(capture['observation_root_times']),
                                    'sensor_root_bindings': deepcopy(capture['sensor_root_bindings'])}
        entry = {'fingerprint': fingerprint, 'event': event, 'result': None, 'anchor_m': deepcopy(estimate['anchor_m'])}
        self.prepared[context['capture']['id']] = entry
        self.pending = context['capture']['id']
        return entry

    def offer(self, context, *, authorized, deadline, cancel):
        if not self.lock.acquire(False):
            return {'status': 'refused', 'reason': 'spatial_busy', 'receipt': None, 'publication': None}
        try:
            if self.reset_requested:
                self._reset()
                self.reset_requested = False
            now = self._check(context, authorized, deadline, cancel)
            numeric, instances = self._inputs(context)
            fingerprint = _original_digest(context)
            key = context['capture']['id']
            if self.pending is not None and self.pending != key:
                raise SpatialRefusal('prior_spatial_publication_unconfirmed')
            if key in self.prepared:
                entry = self.prepared[key]
                if entry['fingerprint'] != fingerprint:
                    raise SpatialRefusal('spatial_capture_identity_conflict')
                if entry['result'] is not None:
                    return deepcopy(entry['result'])
            else:
                if len(self.prepared) >= self.max_captures:
                    raise SpatialRefusal('spatial_capacity_exhausted')
                raw_people = sum(box.get('label') == 'person' for box in context['detector']['result']['boxes'])
                person_count = max(len(instances), raw_people)
                if (person_count != 1 or len(instances) != 1) and self.lifecycle is None:
                    return self._association_lost('association_lost')
                estimate = estimate_geometry(numeric, instances[0], self.policy, self.source) if person_count == 1 and len(instances) == 1 else None
                now = self._check(context, authorized, deadline, cancel)
                if self.assessment_recorder is not None and estimate is not None:
                    self.assessment_recorder.capture(context, numeric, estimate, self.policy, self.source)
                    now = self._check(context, authorized, deadline, cancel)
                decision, prior = None, None
                if self.lifecycle is not None:
                    decision, prior, result = self._lifecycle_observation(context, numeric, estimate, min(person_count, 2))
                    if result is not None:
                        return result
                if decision and decision['action'] == 'continue':
                    if estimate['parts']['work'].get('shape') is not None and self.subject is None:
                        self.subject = {'subject_id': 'wrist_working_region', 'subject_epoch': 'subject:' + digest([self.track, self.epoch])}
                elif not self._identity(context['capture_payload'], estimate, numeric):
                    if self.lifecycle is not None and self.lifecycle.policy['continuity'] is not None:
                        published = self.lifecycle.publish_observation(context, 'observation_gap', diagnostics={'reason': 'initial_identity_unqualified'})
                        return {'status': 'unobservable', 'reason': 'initial_identity_unqualified',
                                'receipt': published['receipt'], 'publication': published['publication'], 'lifecycle': [published]}
                    return self._association_lost('association_lost')
                entry = self._prepare(context, numeric, estimate, fingerprint, now,
                                      decision['method_ref'] if decision and decision['action'] == 'continue' else None)
                if decision:
                    entry['lifecycle_prior'], entry['lifecycle_decision'] = prior, decision
                    if decision['action'] == 'continue':
                        entry['anchor_m'] = deepcopy(decision['anchor_m'])
            self._check(context, authorized, deadline, cancel)
            if self.reset_requested:
                raise SpatialRefusal('spatial_reset_pending')
            ack = self.publish(deepcopy(entry['event']))
            event = entry['event']
            if (not isinstance(ack, dict) or ack.get('status') != 'accepted' or ack.get('id') != event['id']
                    or ack.get('payload_digest') != digest(event['payload'])):
                raise SpatialRefusal('spatial_publication_unconfirmed')
            self.last_ref = {key: ack[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
            self.head_refs[canonical(event['payload']['head']['key'])] = deepcopy(self.last_ref)
            self.last_observed = context['capture_payload']['observed_at']
            self.last_anchor = deepcopy(entry['anchor_m'])
            self.last_generation = context['capture_payload']['source_generation']
            self.last_sequence = context['capture_payload']['head']['position']['capture_seq']
            self.last_publication = deepcopy(event)
            self.pending = None
            if self.assessment_recorder is not None:
                self.assessment_recorder.publication(key, event, ack)
            result = {'status': event['payload']['status'], 'reason': event['payload']['reason'],
                      'receipt': ack, 'publication': event}
            if (self.lifecycle is not None and self.lifecycle.policy['continuity'] is not None and self.subject is not None
                    and self.lifecycle.policy['continuity']['method_ref'] not in entry['lifecycle_decision']['qualified_method_refs']):
                prior = entry.get('lifecycle_prior')
                # A person can be qualified while hands/work remain unknown.
                # Establish the first subject from this ACKed geometry, rather
                # than claiming subject-specific continuity from a null target.
                if prior is None or prior['subject_ref'] is None:
                    prior = self._prior_identity()
                published = self.lifecycle.publish_observation(context, 'continuity_qualified', prior=prior,
                    diagnostics=entry['lifecycle_decision'])
                result['lifecycle'] = [published]
            entry['result'] = result
            return deepcopy(entry['result'])
        except Exception as error:
            return {'status': 'refused', 'reason': getattr(error, 'reason', 'spatial_unavailable'), 'receipt': None, 'publication': None}
        finally:
            if self.reset_requested:
                self._reset()
                self.reset_requested = False
            self.lock.release()

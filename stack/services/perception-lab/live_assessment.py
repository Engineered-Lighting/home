"""Fixed bounded owner-local candidates; never operational observation authority.

No media, depth planes, credentials, public route or model execution is retained.
Candidates are recorded before person/bound qualification. The independently
accepted capture calibration is still mandatory; missing estimator bounds remain
missing in the ordinary producer and journal.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading

from journal import digest, finite, identifier


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


class AssessmentRecorder:
    def __init__(self, policy):
        if (not isinstance(policy, dict) or set(policy) != {'schema', 'version', 'id', 'max_records', 'max_bytes'}
                or policy['schema'] != 'home-live-assessment-policy-1' or type(policy['version']) is not int
                or policy['version'] != 1 or type(policy['max_records']) is not int
                or not 1 <= policy['max_records'] <= 24 or type(policy['max_bytes']) is not int
                or not 1024 <= policy['max_bytes'] <= 8 * 1024**2):
            raise ValueError('closed bounded assessment policy required')
        identifier(policy['id'], 'assessment_policy_id')
        self.policy = deepcopy(policy)
        self.policy_ref = {'id': policy['id'], 'digest': digest(policy)}
        self.binding = None
        self.clock = None
        self.records, self.publications = {}, {}
        self.attempts = {}
        self.barriers = {}
        self.semantic_checks, self.interaction_publications = [], {}
        self.optional_la_configured = False
        self.size = 0
        self.problem = None
        self.lock = threading.Lock()

    def bind(self, session_ref, source_id, configuration_digest, clock, interaction_policy=None, *, optional_la_configured=False):
        with self.lock:
            if self.binding is not None or not callable(clock) or type(optional_la_configured) is not bool:
                raise ValueError('assessment recorder may bind once')
            self.binding = {'session_ref': deepcopy(session_ref), 'source_id': source_id,
                            'configuration_digest': configuration_digest}
            self.clock = clock
            self.interaction_policy = deepcopy(interaction_policy)
            self.optional_la_configured = optional_la_configured

    def capture(self, context, numeric, estimate, policy, source):
        from live_spatial_geometry import candidate_geometry
        from live_interaction_geometry import estimate_interaction
        with self.lock:
            if self.binding is None or self.problem is not None:
                raise ValueError(self.problem or 'assessment_not_bound')
            if digest(self.policy) != self.policy_ref['digest']:
                self.problem = 'assessment_policy_changed'
                raise ValueError(self.problem)
            if (context['session_ref'] != self.binding['session_ref']
                    or context['source_id'] != self.binding['source_id']
                    or context['configuration_digest'] != self.binding['configuration_digest']):
                self.problem = 'assessment_scope_mismatch'
                raise ValueError(self.problem)
            capture = context['capture_payload']
            original = {'detector': deepcopy(context['detector']), 'pose': deepcopy(context['pose']),
                        'joints': deepcopy(estimate['joints'])}
            interaction = None
            if self.interaction_policy is not None:
                interaction = estimate_interaction(numeric, context['detector']['result'], original,
                                                  self.interaction_policy, source, candidate_only=True)
            now = self.clock()
            if not finite(now) or now < context['pose_available_at']:
                self.problem = 'assessment_clock_invalid'
                raise ValueError(self.problem)
            key = context['capture']['id']
            body = {'schema': 'home-live-assessment-candidate-1', 'version': 1,
                'id': 'assessment:' + digest([self.binding, key, self.policy_ref]),
                **deepcopy(self.binding), 'capture_id': key, 'capture_position': deepcopy(capture['head']['position']),
                'candidate_only': True, 'truth_role': 'runtime_model_estimate', 'person_ref': None,
                'subject_ref': None, 'operational_geometry': False, 'focused_output': False,
                'assessment_ref': deepcopy(self.policy_ref), 'method_ref': deepcopy(context.get('spatial_method_ref')),
                'model_refs': deepcopy(context['model_refs']), 'registration': deepcopy(capture['registration']),
                'source_generation': capture['source_generation'],
                'frame': 'z_up_metric_floor0', 'unit': 'm',
                'observed_at': capture['observed_at'], 'available_at': now,
                'valid_until': context['capture']['effective_valid_until'], 'clock': deepcopy(capture['clock']),
                'detector_available_at': context['detector_available_at'], 'pose_available_at': context['pose_available_at'],
                'pipeline_times': deepcopy(context.get('pipeline_times', {})),
                'original_receipt_refs': deepcopy(capture['original_receipt_refs']),
                'sensor_roots': deepcopy(capture['sensor_roots']),
                'observation_root_times': deepcopy(capture['observation_root_times']),
                'sensor_root_bindings': deepcopy(capture['sensor_root_bindings']),
                'joints': deepcopy(estimate['joints']), 'parts': candidate_geometry(estimate, policy, source),
                'qualification_reasons': {name: value.get('reason') for name, value in estimate['parts'].items()},
                'detector': original['detector'], 'pose': original['pose'], 'interaction': interaction}
            encoded = _bytes(body)
            # Identity retries cannot replace first availability or the estimate.
            fingerprint = hashlib.sha256(_bytes([context, estimate])).hexdigest()
            if key in self.records:
                if self.records[key]['fingerprint'] != fingerprint:
                    self.problem = 'assessment_identity_conflict'
                    raise ValueError(self.problem)
                return
            if len(self.records) >= self.policy['max_records'] or self.size + len(encoded) > self.policy['max_bytes']:
                self.problem = 'assessment_capacity_exhausted'
                raise ValueError(self.problem)
            self.records[key] = {'fingerprint': fingerprint, 'record': body}
            self.size += len(encoded)

    def publication(self, capture_id, event, receipt):
        with self.lock:
            if capture_id not in self.records:
                raise ValueError('assessment_candidate_missing')
            row = {'capture_id': capture_id, 'receipt': deepcopy(receipt),
                   'status': event['payload']['status'],
                   'available_at': event['payload']['available_at'], 'valid_until': event['payload']['valid_until'],
                   'parts': {name: {'status': value['status'], 'effective_valid_until': min(value['valid_until'],
                       value.get('uncertainty', {}).get('age', {}).get('origin_at', value['observed_at'])
                       + value.get('uncertainty', {}).get('age', {}).get('max_age_s', value['valid_until'] - value['observed_at']))
                       - event['payload']['clock']['uncertainty_s']}
                       for name, value in event['payload']['parts'].items()}}
            old = self.publications.get(capture_id)
            if old is not None and old != row:
                self.problem = 'assessment_publication_conflict'
                raise ValueError(self.problem)
            self.publications[capture_id] = row

    def semantic_check(self, capture_id, la, *, admitted, checked_at):
        """Numeric diagnostics from the fixed existing exact-frame receipt adapter.

        No LA request is issued and no receipt content, label or media is copied.
        Admission to consideration is distinct from an actually published fact.
        """
        with self.lock:
            if not self.optional_la_configured or capture_id not in self.records:
                raise ValueError('assessment_optional_source_not_bound')
            row = {'capture_id': capture_id, 'checked_at': checked_at,
                   'status': la.get('status', 'unavailable'), 'reason': la.get('reason'),
                   'exact_frame': la.get('status') == 'fresh_proposal' and la.get('reason') == 'exact_current_capture_receipt',
                   'admitted': admitted,
                   **{key: la.get(key) if finite(la.get(key)) else None
                      for key in ('observed_at', 'available_at', 'valid_until')}}
            if row in self.semantic_checks:
                return
            if len(self.semantic_checks) >= self.policy['max_records'] * 3:
                self.problem = 'assessment_semantic_capacity_exhausted'
                raise ValueError(self.problem)
            self.semantic_checks.append(deepcopy(row))

    def interaction_publication(self, capture_id, event, receipt):
        with self.lock:
            if capture_id not in self.records:
                raise ValueError('assessment_candidate_missing')
            payload = event['payload']
            key = (capture_id, payload['kind'])
            row = {'capture_id': capture_id, 'predicate': payload['kind'],
                   'status': payload['status'], 'value': deepcopy(payload['value']),
                   'available_at': payload['available_at'],
                   'effective_valid_until': payload['valid_until'] - payload['clock']['uncertainty_s'],
                   'receipt': deepcopy(receipt)}
            old = self.interaction_publications.get(key)
            if old is not None and old != row:
                self.problem = 'assessment_interaction_publication_conflict'
                raise ValueError(self.problem)
            if old is None and len(self.interaction_publications) >= self.policy['max_records'] * 5:
                self.problem = 'assessment_interaction_capacity_exhausted'
                raise ValueError(self.problem)
            self.interaction_publications[key] = row

    def export(self):
        with self.lock:
            return {'schema': 'home-live-assessment-measurements-1', 'version': 1,
                    'binding': deepcopy(self.binding), 'assessment_ref': deepcopy(self.policy_ref),
                    'complete': self.problem is None, 'reason': self.problem,
                    'records': [deepcopy(value['record']) for value in self.records.values()],
                    'publications': deepcopy(list(self.publications.values())),
                    'attempts': deepcopy(list(self.attempts.values())),
                    'barriers': deepcopy(list(self.barriers.values())),
                    'optional_la_configured': self.optional_la_configured,
                    'semantic_checks': deepcopy(self.semantic_checks),
                    'interaction_publications': deepcopy(list(self.interaction_publications.values())),
                    'operational_geometry': False, 'physical_authority': False}

    def control(self, event, receipt):
        with self.lock:
            key = event['id']
            row = {'source_generation': event['payload']['source_generation'], 'receipt': deepcopy(receipt)}
            if key in self.barriers:
                if self.barriers[key] != row:
                    self.problem = 'assessment_control_conflict'
                    return False
                return True
            if len(self.barriers) >= 25:
                self.problem = 'assessment_control_capacity_exhausted'
                return False
            self.barriers[key] = row
            return True

    def cycle(self, result, counters, at):
        """Retain every bounded dispatch attempt, including no measurement.

        Repeated idle/rate-limit polling is not a new acquisition attempt.
        Failure recording itself can never be mistaken for a complete probe.
        """
        with self.lock:
            number = counters['cycles']
            if number <= 0 or number in self.attempts:
                return self.problem is None
            if len(self.attempts) >= 24:
                self.problem = 'assessment_attempt_capacity_exhausted'
                return False
            self.attempts[number] = {'attempt': number, 'available_at': at if finite(at) else None,
                                     'status': result['status'], 'reason': result['reason'],
                                     **{k: result[k] for k in ('capture', 'detector', 'pose', 'spatial', 'interaction')}}
            return self.problem is None

    def write(self, output):
        """Exclusive owner-local export. Output is private, never an agent report."""
        path = Path(output)
        if not path.is_absolute():
            raise ValueError('absolute owner-local assessment output required')
        payload = _bytes(self.export())
        if len(payload) > self.policy['max_bytes'] + 1024 * 1024:
            raise ValueError('assessment_export_capacity_exhausted')
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


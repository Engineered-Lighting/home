"""Finite surveyed-desk geometry admission; never camera or identity discovery.

Only a fixed owner resolver may supply independently assessed originals. The
ordinary Lab head fences the exact frame, not anatomy or person qualification.
This source preserves the existing geometry wire and private five-field receipt
metadata. The guide's unqualified fits and visible faces cannot satisfy it.
"""
from copy import deepcopy
from pathlib import Path
import sys
import threading

from journal import Journal, JournalError, canonical, digest, finite, identifier
from live_capture import REGISTRATION_KEYS, _original_digest
from live_ingress_validation import validate_clock, validate_config
from schema_registry import LIVE_PROFILE, SchemaRegistry
from geometry import deproject, transform_point

# Source-only owner tooling is an explicit composition dependency, not a service.
ASSESSMENT = Path(__file__).resolve().parents[3] / 'tools' / 'den-assessment'
if str(ASSESSMENT) not in sys.path:
    sys.path.append(str(ASSESSMENT))
from desk_enclosure import DISTINCT_METHOD, evaluate_desk_enclosure

POLICY_KEYS = set('schema version source_id capture_source_id method_ref association_method_ref '
                  'candidate_digest assessment_ref projection_ref protection_policy_ref receipt_store_id '
                  'receipt_component_id body_primitive_id protection_primitive_id'.split())
INPUT_KEYS = set('candidate assessment anchor binding capture_ref capture_original capture_record '
                 'projection registration clock_mapping protection_policy'.split())
STEREO_INPUT_KEYS = INPUT_KEYS | {'stereo_support', 'stereo_qualification'}
DEFINITION_KEYS = set('schema version id required_body_part_ids protected_parts '
                      'body_primitive_id protection_primitive_id'.split())
PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection')
REF_KEYS = set('stream_epoch cursor producer_id id payload_digest component_id'.split())
RECORD_KEYS = set('ref capture_epoch capture_seq observation_root_times sensor_root_bindings'.split())


class DeskAdmissionRefusal(ValueError):
    """A bounded category; never private measurement data in public diagnostics."""


def _need(condition, reason):
    if not condition:
        raise DeskAdmissionRefusal(reason)


def _original(value):
    return _original_digest(value, limit=512 * 1024)


def _ref(value):
    _need(isinstance(value, dict) and set(value) == {'id', 'digest'}, 'immutable_pin_required')
    identifier(value['id'], 'pin_id')
    _need(isinstance(value['digest'], str) and len(value['digest']) == 64
          and all(c in '0123456789abcdef' for c in value['digest']), 'immutable_pin_required')


class LabLiveDeskAdmission:
    """Publish one assessed stationary source via one actual authenticated Journal.

    resolve_inputs is a fixed trusted capability, not caller-authored JSON. It
    resolves the pinned originals and authenticates the independent anchor's
    method/applicability. The retainer must fsync the full derived record before
    acknowledging. Process restoration remains a separate composition concern.
    """

    def __init__(self, live_config, *, policy, revision, resolve_inputs, original_retainer, clock,
                 stereo_media_resolver=None, expected_assessment_binding=None, expected_bundle_binding=None):
        self.config = validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
        version = policy.get('version') if isinstance(policy, dict) else None
        _need(type(version) is int and version in (1, 2, 3, 4)
              and set(policy) == POLICY_KEYS | ({'stereo_policy_ref'} if version in (2, 3, 4) else set())
                  | ({'original_family', 'assessment_policy_sha256'} if version == 3 else set())
                  | ({'original_family', 'bundle_input_policy_sha256'} if version == 4 else set())
              and policy['schema'] == 'home-lab-live-desk-admission-policy-' + str(version),
              'closed_desk_admission_policy_required')
        _need(all(callable(v) for v in (resolve_inputs, original_retainer, clock)),
              'fixed_original_resolver_retainer_clock_required')
        _need(callable(stereo_media_resolver) if version in (2, 3, 4) else stereo_media_resolver is None,
              'fixed_stereo_media_resolver_required')
        self.stereo_media_resolver = stereo_media_resolver
        self.policy, self.scope = deepcopy(policy), self.config['manifest']['identity']
        if version == 3:
            _need(policy['original_family'] == 'existing-api-assessment-1'
                  and isinstance(expected_assessment_binding, dict)
                  and set(expected_assessment_binding) == {'scope', 'lab_binding_digest', 'assessment_policy_sha256'}
                  and expected_assessment_binding['scope'] == self.scope
                  and expected_assessment_binding['assessment_policy_sha256'] == policy['assessment_policy_sha256'],
                  'fixed_existing_api_assessment_binding_required')
            for pin in (policy['assessment_policy_sha256'], expected_assessment_binding['lab_binding_digest']):
                _need(isinstance(pin, str) and len(pin) == 64
                      and all(c in '0123456789abcdef' for c in pin), 'fixed_assessment_pin_required')
        else:
            _need(expected_assessment_binding is None, 'unexpected_assessment_binding')
        self.expected_assessment_binding = deepcopy(expected_assessment_binding)
        if version == 4:
            _need(policy['original_family'] == 'existing-api-bundled-rgb-1'
                  and isinstance(expected_bundle_binding, dict)
                  and set(expected_bundle_binding) == {'scope', 'lab_binding_digest', 'bundle_input_policy_sha256'}
                  and expected_bundle_binding['scope'] == self.scope
                  and expected_bundle_binding['bundle_input_policy_sha256'] == policy['bundle_input_policy_sha256'],
                  'fixed_bundled_rgb_binding_required')
            for pin in (policy['bundle_input_policy_sha256'], expected_bundle_binding['lab_binding_digest']):
                _need(isinstance(pin, str) and len(pin) == 64
                      and all(c in '0123456789abcdef' for c in pin), 'fixed_bundled_rgb_pin_required')
        else:
            _need(expected_bundle_binding is None, 'unexpected_bundled_rgb_binding')
        self.expected_bundle_binding = deepcopy(expected_bundle_binding)
        self.revision = identifier(revision, 'revision')
        for name in ('source_id', 'capture_source_id', 'receipt_store_id', 'receipt_component_id',
                     'body_primitive_id', 'protection_primitive_id'):
            identifier(policy[name], name)
        for name in ('method_ref', 'association_method_ref', 'assessment_ref', 'projection_ref', 'protection_policy_ref'):
            _ref(policy[name])
        if version in (2, 3, 4):
            _ref(policy['stereo_policy_ref'])
        _need(isinstance(policy['candidate_digest'], str) and len(policy['candidate_digest']) == 64
              and all(c in '0123456789abcdef' for c in policy['candidate_digest']), 'candidate_pin_required')
        sources = self.config['manifest']['sources']
        _need(policy['source_id'] in sources and policy['capture_source_id'] in sources
              and policy['source_id'] != policy['capture_source_id'], 'separate_desk_source_required')
        self.source, self.capture = sources[policy['source_id']], sources[policy['capture_source_id']]
        s = self.source
        _need(policy['method_ref']['id'] == DISTINCT_METHOD and policy['method_ref'] in s['method_refs']
              and policy['association_method_ref'] in s['association_method_refs']
              and policy['receipt_store_id'] in s['receipt_store_ids']
              and policy['receipt_component_id'] in s['receipt_component_ids']
              and s['registration'] is not None and s['registration'] == self.capture['registration']
              and s['capture_epoch'] == self.capture['capture_epoch']
              and s['capture_clock'] == self.capture['capture_clock']
              and s['capture_clock']['original_unit'] == ('unix_s' if version == 4 else 'unix_ns')
              and s['sensor_roots'] == self.capture['sensor_roots']
              and s['primitive_ids'] == {'body': [policy['body_primitive_id']],
                                        'protection': [policy['protection_primitive_id']]}
              and s['protection_policy_refs'] == [policy['protection_policy_ref']]
              and all(s['part_policies'][part] is not None for part in PARTS),
              'commissioned_desk_source_required')
        self.resolve_inputs, self.retain, self.clock = resolve_inputs, original_retainer, clock
        self.binding_digest = digest([self.config, self.policy, self.revision])
        self.lock, self.authority = threading.RLock(), None
        self.prepared, self.originals, self.records, self.heads = {}, {}, {}, {}
        self.pending, self.quiesced, self.in_flight = None, False, False
        self.progress = -1

    @property
    def source_ids(self):
        return (self.policy['source_id'],)

    def status(self):
        return {'status': 'unconfirmed' if self.pending is not None or self.in_flight else
                'quiescent' if self.quiesced else 'ready', 'pending': deepcopy(self.pending),
                'in_flight': self.in_flight, 'source_ids': list(self.source_ids),
                'retained_originals': len(self.originals)}

    def quiesce(self):
        # Visible immediately even if a synchronous retainer is still in flight.
        self.quiesced = True
        return self.status()

    def _check(self, journal, received_at):
        _need(isinstance(journal, Journal), 'actual_journal_required')
        if self.authority is None:
            self.authority = journal
        _need(self.authority is journal, 'desk_journal_authority_changed')
        _need(digest([self.config, self.policy, self.revision]) == self.binding_digest
              and journal.live_config == self.config, 'desk_binding_changed')
        now = self.clock()
        _need(finite(now) and finite(received_at) and 0 <= received_at <= now,
              'actual_consumer_clock_required')
        return now

    def _resolve(self, anchor_ref):
        value = self.resolve_inputs(deepcopy(anchor_ref))
        expected = STEREO_INPUT_KEYS if self.policy['version'] in (2, 3, 4) else INPUT_KEYS
        _need(isinstance(value, dict) and set(value) == expected, 'complete_fixed_desk_originals_required')
        _original(value)
        return deepcopy(value)

    def _calibration(self, value):
        """Reuse capture's exact wire/body schema and metric math validators.

        live_capture's policy validator bundles these rules with camera pairing;
        keep the same registration/projection rules here without constructing a
        fabricated capture policy or starting its newer acquisition factory.
        """
        r, p, wire = value['registration'], value['projection'], self.source['registration']
        _need(isinstance(r, dict) and set(r) == REGISTRATION_KEYS
              and r['schema'] == 'home-capture-registration-1' and type(r['version']) is int and r['version'] == 1
              and r['site_id'] == self.scope['site_id'] and r['room_id'] == self.scope['room_id']
              and r['source_frame'] == 'rectified_left' and r['frame'] == 'z_up_metric_floor0' and r['unit'] == 'm'
              and finite(r['error_bound_m']) and r['error_bound_m'] >= 0
              and r['transform_ref']['digest'] == digest(r['room_from_rectified_left'])
              and wire['registration_ref'] == r['id'] and wire['registration_digest'] == digest(r)
              and wire['geometry_revision'] == r['geometry_revision'] and wire['geometry_digest'] == r['geometry_digest']
              and wire['coverage_refs'] == r['coverage_refs'] and wire['frame'] == r['frame'] and wire['unit'] == r['unit']
              and wire['transform_chain_refs'] == [r['transform_ref']], 'qualified_registration_required')
        _ref(r['validation_ref'])
        _need(isinstance(p, dict) and {'id': p.get('id'), 'digest': digest(p)} == self.policy['projection_ref']
              and wire['calibration_digest'] == p['id'] and p.get('status') == 'validated'
              and p.get('site_id') == r['site_id'] and p.get('camera_id') == r['camera_id']
              and p.get('mount_revision') == r['mount_revision'] and bool(p.get('validation_receipt'))
              and finite(p.get('error_bound_m')) and p['error_bound_m'] >= 0
              and p.get('frame') == 'rectified_left' and p.get('width') == 640 and p.get('height') == 400
              and bool(p.get('reprojection', {}).get('receipt'))
              and finite(p['reprojection'].get('p95_px')) and 0 <= p['reprojection']['p95_px'] <= 5,
              'qualified_projection_required')
        k, intrinsics = p['reference_intrinsics']['rectified_left'], p['intrinsics']
        _need(intrinsics == {'fx': k[0][0], 'fy': k[1][1], 'cx': k[0][2], 'cy': k[1][2]}
              and k[0][1] == k[1][0] == 0 and k[2] == [0, 0, 1]
              and isinstance(p.get('rgb_distortion_coefficients'), list) and len(p['rgb_distortion_coefficients']) == 14
              and all(finite(v) for v in p['rgb_distortion_coefficients']), 'qualified_intrinsics_required')
        deproject([0, 0], 1, intrinsics, source_frame='rectified_left', calibration_version=p['id'])
        transform_point({'frame': 'rectified_left', 'xyz_m': [0, 0, 1]}, r['room_from_rectified_left'],
                        source_frame='rectified_left', target_frame=r['frame'], version=r['id'],
                        calibration_error_m=r['error_bound_m'])

    def _inputs(self, value, anchor_ref, now, hazards):
        p, s = self.policy, self.source
        b, a, assessment = value['binding'], value['anchor'], value['assessment']
        _need(isinstance(b, dict) and b.get('version') == 2 and b.get('scope') == self.scope
              and b.get('anchor_original_ref') == anchor_ref
              and b.get('method_ref') == p['method_ref'] and b.get('association_method_ref') == p['association_method_ref']
              and b.get('candidate_digest') == p['candidate_digest'] and b.get('assessment_ref') == p['assessment_ref'],
              'pinned_desk_binding_required')
        result = evaluate_desk_enclosure(value['candidate'], binding=b, assessment=assessment,
            anchor=a, now=now, hazards=hazards,
            **({'original_family': p['original_family']} if p['version'] == 4 else {}))
        _need(result['status'] == 'eligible_for_admission_review', result['reason'])
        self._calibration(value)
        r = value['registration']
        _need(b['registration_ref'] == {'id': r['id'], 'digest': digest(r)}
              and assessment['registration']['error_bound_m'] >= r['error_bound_m']
              and value['clock_mapping'] == s['capture_clock']
              and b['clock_mapping_ref'] == {'id': s['capture_clock']['mapping_id'], 'digest': s['capture_clock']['mapping_digest']},
              'registration_or_clock_original_mismatch')
        clock = {**deepcopy(a['clock']), 'status': 'commissioned'}
        validate_clock(clock, s['capture_clock'], a['observed_at'])
        d = value['protection_policy']
        _need(isinstance(d, dict) and set(d) == DEFINITION_KEYS
              and d['schema'] == 'home-desk-protection-definition-1' and type(d['version']) is int and d['version'] == 1
              and {'id': d['id'], 'digest': digest(d)} == p['protection_policy_ref']
              and d['required_body_part_ids'] == [p['body_primitive_id']]
              and d['body_primitive_id'] == p['body_primitive_id']
              and d['protection_primitive_id'] == p['protection_primitive_id']
              and d['protected_parts'] == assessment['protected_parts'] and 'head' in d['protected_parts']
              and value['candidate']['protection_policy_ref'] == p['protection_policy_ref'],
              'complete_protection_definition_required')
        delivery = self.config['manifest'].get('worker', {}).get('delivery_policy')
        if delivery is not None:
            _need(delivery['required_body_parts'] == d['required_body_part_ids']
                  and delivery['protection']['policy_ref'] == p['protection_policy_ref'],
                  'delivery_protection_definition_mismatch')
        record, original = value['capture_record'], value['capture_original']
        _need(isinstance(record, dict) and set(record) == RECORD_KEYS
              and original.get('schema') == 'home-original-lab-observation-' + ('2' if p['version'] == 4 else '1')
              and original.get('version') == (2 if p['version'] == 4 else 1)
              and record['ref']['digest'] == _original_digest(original)
              and record['ref']['observed_at'] == a['observed_at']
              and record['ref']['available_at'] <= a['available_at']
              and record['capture_epoch'] == a['source_position']['capture_epoch'] == s['capture_epoch']
              and record['capture_seq'] == a['source_position']['capture_seq']
              and max(record['observation_root_times'].values()) == a['observed_at'],
              'original_capture_lineage_mismatch')
        payload, frame = original['event']['payload'], original['frame_metadata']
        if p['version'] == 4:
            from lab_bundled_rgb_stereo import validate_bundled_rgb_originals
            _need(original.get('bundled_originals', {}).get('clock_original') == original.get('clock_original'),
                  'bundled_capture_clock_original_changed')
            checked = validate_bundled_rgb_originals(original['bundled_originals'],
                media_refs=original['clock_original']['media_refs'],
                expected_bundle_binding=self.expected_bundle_binding,
                capture_clock_policy=s['capture_clock'], resolve_media=self.stereo_media_resolver,
                event=original['event'], frame=frame)
            derived = checked['clock_original']
            component = derived['component']
            _need(derived == original['clock_original'] and clock == derived['clock']
                  and frame['camera_id'] == r['camera_id'] and frame['site_id'] == self.scope['site_id']
                  and payload['capture_epoch'] == record['capture_epoch'] == frame['capture_epoch'] == component['capture_epoch']
                  and payload['frame_id'] == frame['frame_id']
                  and payload['observed_at'] == frame['observed_at'] == a['observed_at']
                  and frame['available_at'] <= payload['available_at'] <= a['available_at']
                  and frame['sequence'] == record['capture_seq'] == component['sequence']
                  and original['first_consumer_received_at'] <= a['available_at']
                  and original['method_ref'] in self.capture['method_refs']
                  and original['model_ref'] in self.capture['model_refs']
                  and frame['clock_mapping_status'] == 'verified'
                  and frame['timestamp_semantics'] == 'same_stream_ordinal_qualified_receipt_bound'
                  and component['capture_epoch'] == clock['source_epoch'],
                  'exact_original_bundled_rgb_anchor_required')
        else:
            rgb = frame['source_timing_receipt']
            _need(frame['camera_id'] == r['camera_id'] and frame['site_id'] == self.scope['site_id']
                  and payload['capture_epoch'] == record['capture_epoch'] == frame['capture_epoch'] == rgb['session']
                  and payload['frame_id'] == frame['frame_id']
                  and payload['observed_at'] == frame['observed_at'] == a['observed_at']
                  and frame['available_at'] <= payload['available_at'] <= a['available_at']
                  and rgb['sequence'] == frame['sequence'] == record['capture_seq']
                  and str(rgb['observed_at_unix_ns']) == clock['original_value']
                  and original['first_consumer_received_at'] <= a['available_at']
                  and original['method_ref'] in self.capture['method_refs']
                  and original['model_ref'] in self.capture['model_refs']
                  and rgb['schema'] == 3 and frame['clock_mapping_status'] == 'verified'
                  and frame['clock_mapping_id'] == rgb['validation_id']
                  and frame['receiver_clock_validation_id'] == rgb['host_clock_validation_id']
                  and frame['timestamp_semantics'] == rgb['timestamp_semantics'] == 'causal_exact_frame_exposure_end'
                  and frame['source_clock'] == rgb['source_clock'] == 'depthai_device_monotonic'
                  and rgb['session'] == clock['source_epoch']
                  and rgb['uncertainty_seconds'] + rgb['host_clock_uncertainty_seconds'] <= clock['uncertainty_s'],
                  'exact_original_rgb_anchor_required')
        _need(set(record['observation_root_times']) == set(record['sensor_root_bindings'])
              and set().union(*(set(v) for v in record['sensor_root_bindings'].values())) == set(s['sensor_roots'])
              and all(finite(t) and t <= a['observed_at'] for t in record['observation_root_times'].values()),
              'complete_original_root_binding_required')
        if p['version'] in (2, 3, 4):
            from lab_live_desk_anchor import validate_stereo_support
            qualification = value['stereo_qualification']
            _need(isinstance(qualification, dict)
                  and all(qualification.get(key) == value[key] for key in
                          ('projection', 'registration', 'clock_mapping'))
                  and value['stereo_support'].get('capture_ref') == value['capture_ref']
                  and qualification.get('association', {}).get('subject_ref') == b['subject_ref']
                  and qualification.get('stereo_policy', {}).get('association_method_ref') == p['association_method_ref'],
                  'stereo_desk_qualification_binding_mismatch')
            validator = validate_stereo_support
            options = {}
            if p['version'] == 3:
                from lab_existing_api_stereo import validate_existing_api_support
                validator = validate_existing_api_support
                options['expected_assessment_binding'] = self.expected_assessment_binding
            elif p['version'] == 4:
                from lab_bundled_rgb_stereo import validate_bundled_rgb_support
                validator = validate_bundled_rgb_support
                options['expected_bundle_binding'] = self.expected_bundle_binding
                options['capture_clock_policy'] = s['capture_clock']
            stereo = validator(value['stereo_support'], qualification=value['stereo_qualification'],
                anchor=a, capture_record=record, capture_original=original, scope=self.scope,
                sensor_roots=s['sensor_roots'], policy_ref=p['stereo_policy_ref'], now=now,
                resolve_media=self.stereo_media_resolver, **options)
            _need(isinstance(stereo, dict) and set(stereo) ==
                  {'valid_until', 'observation_root_times', 'sensor_root_bindings'},
                  'closed_stereo_support_validation_required')
            result = deepcopy(result)
            result['geometry_proposal']['valid_until'] = min(result['geometry_proposal']['valid_until'],
                                                            stereo['valid_until'])
            result['stereo_lineage'] = stereo
        return result, clock

    def _current(self, value, journal, grant, now):
        ref = value['capture_ref']
        _need(isinstance(ref, dict) and set(ref) == REF_KEYS
              and value['binding']['parent_publication_refs'] == [ref], 'exact_capture_head_reference_required')
        rows = journal.snapshot(grant, self.scope['site_id'], self.scope['room_id'])['items']
        selected = [row for row in rows if {k: row[k] for k in REF_KEYS - {'component_id'}}
                    == {k: ref[k] for k in REF_KEYS - {'component_id'}}]
        _need(ref['component_id'] is None and len(selected) == 1, 'capture_head_not_current')
        body, a = selected[0]['payload'], value['anchor']
        _need(body['source_id'] == self.policy['capture_source_id']
              and body['source_generation'] == a['source_generation']
              and body['head']['position'] == a['source_position']
              and body['observed_at'] == a['observed_at']
              and body['clock']['original_value'] == a['clock']['original_value']
              and value['capture_record']['ref'] in body['original_receipt_refs']
              and body['observation_root_times'] == value['capture_record']['observation_root_times']
              and body['sensor_root_bindings'] == value['capture_record']['sensor_root_bindings']
              and body['method_ref'] == value['capture_original']['method_ref']
              and body.get('model_ref', value['capture_original']['model_ref']) == value['capture_original']['model_ref']
              and selected[0]['committed_at'] <= now < body['valid_until'], 'capture_frame_or_generation_changed')

    def resolve_receipt(self, store_id, receipt_id):
        # Invoked inside Journal's transaction: never start a nested snapshot.
        with self.lock:
            _need(store_id == self.policy['receipt_store_id'], 'desk_receipt_store_mismatch')
            entry = self.originals[receipt_id]
            _need(_original(self._resolve(entry['anchor_ref'])) == entry['inputs_digest'],
                  'underlying_desk_original_changed')
            return deepcopy(self.records[receipt_id])

    def original_receipt(self, store_id, receipt_id):
        self.resolve_receipt(store_id, receipt_id)
        return deepcopy(self.originals[receipt_id])

    def _prepare(self, anchor_ref, value, result, clock, received_at, now):
        s, p, b = self.source, self.policy, value['binding']
        proposal, a = result['geometry_proposal'], value['anchor']
        observed = a['observed_at']
        available = max(received_at, now, a['available_at'])
        until = min(proposal['valid_until'], observed + s['max_age_s'],
                    value['capture_original']['event']['payload']['valid_until'],
                    value['capture_original']['frame_metadata']['valid_until'],
                    *(observed + s['part_policies'][part]['age']['max_age_s'] for part in PARTS))
        _need(available + clock['uncertainty_s'] < until, 'desk_support_intersection_expired')
        identity = [self.scope, p['source_id'], anchor_ref]
        receipt_id = 'desk-original:' + digest(identity)
        original = {'schema': 'home-original-lab-desk-' + str(p['version']), 'version': p['version'],
                    'anchor_ref': deepcopy(anchor_ref),
                    'inputs_digest': _original(value), 'inputs': deepcopy(value), 'policy': deepcopy(p),
                    'first_consumer_received_at': received_at, 'produced_at': available}
        ref = {'store_id': p['receipt_store_id'], 'receipt_id': receipt_id, 'digest': _original(original),
               'component_id': p['receipt_component_id'], 'observed_at': observed, 'available_at': available,
               'clock_mapping_id': clock['mapping_id']}
        record = {'ref': ref, 'capture_epoch': a['source_position']['capture_epoch'],
                  'capture_seq': a['source_position']['capture_seq'],
                  'observation_root_times': deepcopy(value['capture_record']['observation_root_times']),
                  'sensor_root_bindings': deepcopy(value['capture_record']['sensor_root_bindings'])}
        if p['version'] in (2, 3, 4):
            stereo = result['stereo_lineage']
            _need(not set(stereo['observation_root_times']) & set(record['observation_root_times']),
                  'distinct_original_depth_root_required')
            record['observation_root_times'].update(deepcopy(stereo['observation_root_times']))
            record['sensor_root_bindings'].update(deepcopy(stereo['sensor_root_bindings']))
        parts = {}
        for name in PARTS:
            box = deepcopy(proposal['work'] if name == 'work' else proposal['parts'][name])
            if name in ('body', 'protection'):
                box.update(part_id=p[name + '_primitive_id'])
                box = {'kind': 'opaque_primitives' if name == 'body' else 'protected_primitives', 'items': [box],
                       **({'policy_ref': deepcopy(p['protection_policy_ref'])} if name == 'protection' else {})}
            uncertainty = deepcopy(s['part_policies'][name])
            uncertainty['age']['origin_at'] = observed
            parts[name] = {'status': 'supplied', 'basis': 'inferred', 'shape': box, 'uncertainty': uncertainty,
                           'support_refs': [deepcopy(ref)], 'observed_at': observed, 'valid_until': until,
                           'method_ref': deepcopy(p['method_ref'])}
        for name in ('body_forward', 'head_forward'):
            parts[name] = {'status': 'unobservable', 'reason': 'missing_measurement_bound',
                           'support_refs': [deepcopy(ref)], 'observed_at': observed, 'valid_until': until}
        body = {'schema': 'home-live-geometry-1', 'version': 1, 'id': 'desk-geometry:' + digest(identity),
                **deepcopy(self.scope), 'manifest_ref': deepcopy(self.config['manifest_ref']),
                'source_id': p['source_id'], 'source_generation': s['initial_generation'],
                'person_ref': deepcopy(b['person_ref']), 'subject_ref': deepcopy(b['subject_ref']),
                'association': {'person_status': 'qualified', 'subject_status': 'qualified',
                    'association_id': result['association_proposal']['id'], 'generation': a['source_generation'],
                    'method_ref': deepcopy(p['association_method_ref']),
                    'candidate_person_refs': [], 'candidate_subject_refs': []},
                'status': 'supplied', 'reason': 'qualified_coarse_bounds', 'observed_at': observed,
                'available_at': available, 'valid_until': until, 'sensor_roots': deepcopy(s['sensor_roots']),
                'observation_roots': list(record['observation_root_times']),
                'observation_root_times': deepcopy(record['observation_root_times']),
                'sensor_root_bindings': deepcopy(record['sensor_root_bindings']), 'parent_refs': [],
                'original_receipt_refs': [ref], 'clock': clock, 'method_ref': deepcopy(p['method_ref']),
                'registration': deepcopy(s['registration']), 'parts': parts}
        key = {'source_id': p['source_id'], 'payload_type': body['schema'], 'person_ref': deepcopy(b['person_ref']),
               'subject_ref': deepcopy(b['subject_ref']), 'predicate': 'geometry_bundle', 'method_ref': deepcopy(p['method_ref'])}
        body['head'] = {'key': key, 'position': deepcopy(a['source_position']),
                        'supersedes': deepcopy(self.heads.get(canonical(key)))}
        event = {'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': body['id'],
                 'producer_id': s['producer_id'], 'stream_epoch': s['producer_epoch'], **deepcopy(self.scope),
                 **{k: body[k] for k in ('observed_at', 'available_at', 'valid_until')},
                 'payload_type': body['schema'], 'payload_version': '1.0', 'revision': self.revision, 'payload': body}
        return {'value': value, 'event': event, 'original': original, 'record': record,
                'receipt': None, 'retained': False, 'result': result}

    def consume(self, anchor_ref, *, received_at, journal, grant, hazards=()):
        with self.lock:
            self.in_flight = True
            try:
                return self._consume(anchor_ref, received_at=received_at, journal=journal, grant=grant, hazards=hazards)
            finally:
                self.in_flight = False

    def _consume(self, anchor_ref, *, received_at, journal, grant, hazards):
            now = self._check(journal, received_at)
            current_hazards = evaluate_desk_enclosure(None, binding=None, now=now, hazards=hazards)['inherited_hazards']
            _need(current_hazards is not None, 'bounded_inherited_hazards_required')
            hazards = current_hazards
            _need(isinstance(anchor_ref, dict), 'anchor_reference_required')
            identity = digest(anchor_ref)
            _need(self.pending in (None, identity), 'desk_pending_exact_retry_required')
            entry = self.prepared.get(identity)
            if entry is None:
                try:
                    _need(not self.quiesced, 'desk_quiesced')
                    _need(len(self.prepared) < 24, 'desk_session_budget_exhausted')
                    value = self._resolve(anchor_ref)
                    result, clock = self._inputs(value, anchor_ref, now, hazards)
                    _need(value['anchor']['source_position']['capture_seq'] > self.progress, 'desk_capture_not_advancing')
                    if self.policy['version'] in (2, 3, 4):
                        now = self._check(journal, received_at)
                    with journal.lock:
                        self._current(value, journal, grant, now)
                    # Fixed-store resolution and validation may be slow. The
                    # first receipt stays historical; production cannot precede
                    # completed resolution or borrow its consumed lease.
                    now = self._check(journal, received_at)
                    entry = self._prepare(anchor_ref, value, result, clock, received_at, now)
                except (DeskAdmissionRefusal, JournalError, KeyError, TypeError, ValueError, IndexError) as error:
                    reason = str(error) if isinstance(error, DeskAdmissionRefusal) else 'desk_originals_unavailable'
                    return {'status': 'unavailable', 'reason': reason, 'receipt': None,
                            'inherited_hazards': evaluate_desk_enclosure(None, binding=None, now=now, hazards=hazards)['inherited_hazards'],
                            'hazard_clearance': None}
                self.prepared[identity] = entry
                self.pending = identity
            if not entry['retained']:
                ack = self.retain(deepcopy(entry['original']))
                _need(ack == {'status': 'retained', 'digest': _original(entry['original'])}, 'durable_desk_original_required')
                rid = entry['record']['ref']['receipt_id']
                self.originals[rid], self.records[rid] = deepcopy(entry['original']), deepcopy(entry['record'])
                entry['retained'] = True
            # A committed lost ACK is retried verbatim even after expiry/quiesce.
            # Before FIRST admission, recheck all currentness after slow fsync.
            with journal.lock:
                already = journal._receipt(entry['event'])
                if already is None:
                    now = self._check(journal, received_at)
                    _need(not self.quiesced, 'desk_quiesced_before_admission')
                    current = self._resolve(anchor_ref)
                    now = self._check(journal, received_at)
                    _need(_original(current) == entry['original']['inputs_digest'], 'underlying_desk_original_changed')
                    self._inputs(current, anchor_ref, now, hazards)
                    if self.policy['version'] in (2, 3, 4):
                        now = self._check(journal, received_at)
                    self._current(current, journal, grant, now)
                    _need(now + entry['event']['payload']['clock']['uncertainty_s'] < entry['event']['valid_until'],
                          'desk_support_expired_before_admission')
                    _need(not self.quiesced, 'desk_quiesced_before_admission')
                receipt = journal.append_event(deepcopy(entry['event']), grant)
            entry['receipt'], self.pending = deepcopy(receipt), None
            body = entry['event']['payload']
            key = canonical(body['head']['key'])
            if key not in self.heads or receipt['cursor'] > self.heads[key]['cursor']:
                self.heads[key] = {k: receipt[k] for k in REF_KEYS - {'component_id'}} | {'component_id': None}
            self.progress = max(self.progress, body['head']['position']['capture_seq'])
            return {'status': 'admitted', 'receipt': deepcopy(receipt), 'event': deepcopy(entry['event']),
                    'inherited_hazards': deepcopy(hazards), 'hazard_clearance': None}

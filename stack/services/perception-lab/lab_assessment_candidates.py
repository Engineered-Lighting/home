"""Private nominal camera samples before estimator/person qualification.

This inert consumer never opens a camera, Store, model, journal or transport.
EEPROM projection and depth spread are diagnostics, not measured error bounds.
The original model instance is retained; it is never a person/subject identity.
"""
from copy import deepcopy
from decimal import Decimal
import base64
import hashlib
import json
import math

from persisted_evidence import observation_record
from jev_evidence import project

ROLES = ('rgb', 'bundle', 'paired_original')
# Private unavailable output slots only. Importing operational capture for these
# names would require the Home authority graph in this read-only Lab tool.
UNAVAILABLE_PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection',
                     'body_forward', 'head_forward')
LIMITS = {'rgb': 256 * 1024, 'bundle': 4 * 1024**2, 'paired_original': 256 * 1024}
AUTHORITY = dict(operational_geometry=None, geometry_authority=False, identity_authority=False,
                 engagement_authority=False, absence_authority=False,
                 protection_authority=False, beam_authority=False)
EXISTING_MODE = 'existing-api-assessment-1'
EXISTING_ROLES = ('rgb', 'bundle', 'calibration_original', 'association')
EXISTING_LIMITS = {'rgb': LIMITS['rgb'], 'bundle': LIMITS['bundle'],
                   'calibration_original': 24576, 'association': 256 * 1024}
BUNDLED_MODE = 'existing-api-bundled-rgb-1'
BUNDLED_LIMITS = {'support': 256 * 1024, 'anchor_a': LIMITS['rgb'], 'anchor_b': LIMITS['rgb'],
                  'npz': LIMITS['bundle'], 'calibration_original': 24576,
                  'bundle': LIMITS['bundle'], 'rgb': 1024 * 1024}
BUNDLED_START = 'assessment.bundle_rgb.started'
BUNDLED_TERMINAL = 'assessment.bundle_rgb.terminal'
BUNDLED_WINDOW = 'assessment.bundle_rgb.window_closed'


def original_json(data):
    """Bounded callers supply bytes; duplicate/nonfinite JSON is not an original."""
    def unique(items):
        value = {}
        for key, item in items:
            need(key not in value, 'bundled_original_json_duplicate')
            value[key] = item
        return value
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda _: need(False, 'bundled_original_json_nonfinite'))


def encoded(value):
    # Native integer nanoseconds remain exact in private originals.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def need(value, reason):
    if not value:
        raise ValueError(reason)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bounded_reason(error):
    value = str(error)
    return value if 0 < len(value) <= 100 and all(c.isalnum() or c == '_' for c in value) else 'candidate_input_refused'


class LabAssessmentCandidates:
    def __init__(self, session, *, arm, generation, media_generation, lab_profile,
                 sampling=None, max_attempts=24, collection_mode='paired-preview-1',
                 existing_api_binding=None, bundled_rgb_binding=None):
        need(isinstance(session, dict) and all(isinstance(session.get(k), str) and session[k]
             for k in ('id', 'site_id', 'camera_id')) and arm in ('a', 'b')
             and type(generation) is int and generation > 0 and type(media_generation) is int
             and media_generation >= 0 and type(max_attempts) is int and 1 <= max_attempts <= 24,
             'fixed_candidate_collection_binding_required')
        need(isinstance(lab_profile, dict) and all(isinstance(lab_profile.get(k), str) and lab_profile[k]
             for k in ('profile_id', 'engine_epoch', 'model_version', 'artifact_sha256')),
             'original_ready_lab_profile_required')
        sampling = sampling or {'radius_px': 4, 'min_support': 3}
        need(set(sampling) == {'radius_px', 'min_support'} and finite(sampling['radius_px'])
             and 0 < sampling['radius_px'] <= 16 and type(sampling['min_support']) is int
             and 3 <= sampling['min_support'] <= 256, 'bounded_diagnostic_sampling_required')
        self.binding = {'session': {k: session[k] for k in ('id', 'site_id', 'camera_id')},
                        'arm': arm, 'model_generation': generation, 'media_generation': media_generation,
                        'lab_profile': deepcopy(lab_profile), 'sampling': deepcopy(sampling)}
        need(collection_mode in ('paired-preview-1', EXISTING_MODE, BUNDLED_MODE), 'known_private_collection_mode_required')
        self.collection_mode = collection_mode
        if collection_mode == EXISTING_MODE:
            need(isinstance(existing_api_binding, dict) and set(existing_api_binding) == {
                'policy_sha256', 'site_id', 'camera_id', 'session_id', 'media_generation', 'experiment_generation'}
                and isinstance(existing_api_binding['policy_sha256'], str)
                and len(existing_api_binding['policy_sha256']) == 64
                and all(c in '0123456789abcdef' for c in existing_api_binding['policy_sha256'])
                and all(existing_api_binding[k] == session[v] for k, v in
                    (('session_id', 'id'), ('site_id', 'site_id'), ('camera_id', 'camera_id')))
                and existing_api_binding['media_generation'] == media_generation
                and existing_api_binding['experiment_generation'] == generation,
                'fixed_existing_api_assessment_binding_required')
            self.binding.update(collection_mode=collection_mode, existing_api_binding=deepcopy(existing_api_binding))
        elif collection_mode == BUNDLED_MODE:
            source = bundled_rgb_binding
            need(existing_api_binding is None and isinstance(source, dict) and set(source) == {
                'original_family', 'policy_sha256', 'site_id', 'camera_id', 'session_id',
                'media_generation', 'experiment_generation'} and source['original_family'] == BUNDLED_MODE
                and isinstance(source['policy_sha256'], str) and len(source['policy_sha256']) == 64
                and all(c in '0123456789abcdef' for c in source['policy_sha256'])
                and all(source[k] == session[v] for k, v in
                    (('session_id', 'id'), ('site_id', 'site_id'), ('camera_id', 'camera_id')))
                and type(source['media_generation']) is int and source['media_generation'] == media_generation
                and type(source['experiment_generation']) is int and source['experiment_generation'] == generation,
                'fixed_bundled_rgb_binding_required')
            self.binding.update(collection_mode=collection_mode, bundled_rgb_binding=deepcopy(source))
        else:
            need(existing_api_binding is None, 'legacy_collection_binding_changed')
        if collection_mode != BUNDLED_MODE:
            need(bundled_rgb_binding is None, 'legacy_collection_binding_changed')
        self.binding_digest = sha(encoded(self.binding))
        self.max_attempts, self.records = max_attempts, {}

    def matches(self, event):
        p = event.get('payload', {}) if isinstance(event, dict) else {}
        return (event.get('kind') == 'perception.output' and event.get('session_id') == self.binding['session']['id']
                and p.get('arm') == self.binding['arm'] and p.get('generation') == self.binding['model_generation'])

    def collect(self, event, *, received_at, frame_record=None, paired_records=None, refusal_reason=None,
                capture_started=None, capture_terminal=None):
        need(self.matches(event) and type(event.get('id')) is int and event['id'] > 0,
             'matching_original_lab_attempt_required')
        need(finite(received_at) and received_at >= 0, 'actual_candidate_receipt_clock_required')
        raw = encoded(event)
        need(len(raw) <= 2 * 1024**2, 'bounded_original_lab_attempt_required')
        try:
            refs = None if paired_records is None else {k: {'record': v['record'], 'sha256': sha(v['data'])}
                                                        for k, v in paired_records.items()}
        except (TypeError, KeyError, AttributeError):
            refs = {'malformed_media_snapshot': True}
        material = [event, frame_record, refs, refusal_reason]
        if self.collection_mode in (EXISTING_MODE, BUNDLED_MODE):
            material.extend([capture_started, capture_terminal])
        fingerprint = sha(encoded(material))
        if event['id'] in self.records:
            prior = self.records[event['id']]
            need(prior[0] == fingerprint, 'candidate_original_identity_changed')
            return deepcopy(prior[1])
        need(len(self.records) < self.max_attempts, 'candidate_attempt_budget_exhausted')
        p = event['payload']
        clocks = {k: deepcopy(p.get(k)) for k in ('observed_at', 'available_at', 'valid_until')}
        body = dict(schema='home-lab-assessment-attempt-1', version=1,
            id='lab-assessment-' + sha(encoded([self.binding_digest, event['id']])),
            binding_digest=self.binding_digest, owner_local_only=True, candidate_only=True,
            truth_role='nominal_runtime_model_estimate', original_event=deepcopy(event),
            original_event_sha256=sha(raw), source_event_id=event['id'],
            first_consumer_received_at=received_at, candidate_produced_at=None,
            collector_clock_qualification='unassessed', collector_clock_error_bound_s=None,
            source_times=clocks, source_fresh_at_collection=False, image=None, instances=[],
            coordinate_frame='rectified_left', unit='m', error_bound_m=None,
            person_ref=None, subject_ref=None, original_frame_record=deepcopy(frame_record), media_records={}, depth_clock=None,
            parts={name: {'status': 'unavailable', 'reason': 'candidate_only_no_operational_authority'} for name in UNAVAILABLE_PARTS},
            status='refused', reason=None, missing_qualification=['independent_camera_error',
                'independent_estimator_error', 'person_association', 'room_registration',
                'complete_anatomy_and_protection'], **deepcopy(AUTHORITY))
        if self.collection_mode == EXISTING_MODE:
            body.update(schema='home-lab-assessment-attempt-2', version=2,
                collection_mode=EXISTING_MODE, capture_started=deepcopy(capture_started),
                capture_terminal=deepcopy(capture_terminal))
        elif self.collection_mode == BUNDLED_MODE:
            body.update(schema='home-lab-assessment-attempt-3', version=3,
                collection_mode=BUNDLED_MODE, capture_started=deepcopy(capture_started),
                capture_terminal=deepcopy(capture_terminal))
        try:
            record = observation_record(event['id'], event['kind'], p, self.binding['session'])
            need(record is not None, 'original_lab_observation_refused')
            profile = self.binding['lab_profile']
            need(all(p.get(k) == profile[v] for k, v in (('run_id', 'profile_id'), ('engine_epoch', 'engine_epoch'),
                ('model_version', 'model_version'), ('artifact_sha256', 'artifact_sha256'))), 'original_model_profile_changed')
            result = p.get('result', {})
            need(result.get('schema_version') == 1 and result.get('coordinate_space') == 'source_pixels'
                 and result.get('frame_id') == p.get('frame_id') and isinstance(p.get('capture_epoch'), str),
                 'original_model_frame_required')
            need(clocks['observed_at'] <= clocks['available_at']
                 and clocks['observed_at'] < clocks['valid_until'], 'original_model_clock_refused')
            body['source_fresh_at_collection'] = clocks['available_at'] <= received_at < clocks['valid_until']
            body['diagnostic_collector_minus_model_available_s'] = received_at - clocks['available_at']
            body['evidence_roots'] = deepcopy(record['evidence_roots'])
            projected = project([record])[0]
            body['image'] = deepcopy(projected)
            poses = projected['pose_instances']
            body['original_pose_instance_count'] = len(p.get('result', {}).get('pose_instances', []))
            body['projected_pose_instance_count'] = len(poses)
            need(body['original_pose_instance_count'] <= 2, 'existing_pose_projection_capacity_exceeded')
            for pose in poses:
                ids = [j['joint_id'] for j in pose['joints']]
                need(len(ids) == len(set(ids)), 'duplicate_original_joint_id')
            body['instances'] = [dict(instance_ref=pose.get('instance_ref'), joints=[dict(
                joint_id=i, pixel=next((deepcopy(j.get('pixel')) for j in pose['joints'] if j['joint_id'] == i), None),
                status='unavailable', reason='joint_not_observed', camera_xyz_m=None,
                depth_support=0, depth_spread_m=None, error_bound_m=None) for i in range(17)]) for pose in poses]
            if self.collection_mode == EXISTING_MODE:
                self._existing_capture(event, capture_started, capture_terminal)
            elif self.collection_mode == BUNDLED_MODE:
                self._bundled_capture(event, capture_started, capture_terminal)
            if not poses:
                raise ValueError('no_pose_prediction')
            if refusal_reason is not None:
                raise ValueError(bounded_reason(refusal_reason))
            need(paired_records is not None and frame_record is not None, 'original_pair_unavailable')
            if self.collection_mode == EXISTING_MODE:
                plane, metadata, paired = self._existing_media(event, frame_record, paired_records,
                    capture_started, capture_terminal)
            elif self.collection_mode == BUNDLED_MODE:
                plane, metadata, paired = self._bundled_media(event, frame_record, paired_records,
                    capture_started, capture_terminal)
            else:
                plane, metadata, paired = self._media(event, frame_record, paired_records)
            body['media_records'] = {role: deepcopy(v['record']) for role, v in paired_records.items()}
            body['depth_clock'] = ({'status': 'unassessed', 'mapping': None, 'policy': None,
                'original_device_frame': deepcopy(metadata['frames']['depth']),
                'association_created_at': paired['association_created_at'],
                'receipt_time_role': 'retention_and_read_receipts_not_depth_exposure',
                'exposure_error_bound_s': None} if self.collection_mode in (EXISTING_MODE, BUNDLED_MODE) else
                {'status': paired['depth_clock_status'],
                'mapping': deepcopy(paired['depth_clock_mapping']), 'policy': deepcopy(paired['depth_clock_policy']),
                'original_device_frame': deepcopy(metadata['frames']['depth']),
                'first_pair_received_at': paired['first_pair_received_at'], 'pair_retained_at': paired['retained_at'],
                'receipt_time_role': 'controller_receipt_not_depth_exposure', 'exposure_error_bound_s': None})
            from room_mapper import rgbd_model
            from head3d import DepthLifter
            model = rgbd_model(metadata, paired['calibration'])
            for pose, instance in zip(poses, body['instances']):
                lifter = DepthLifter(plane, model)
                observed = {j['joint_id']: j for j in pose['joints']}
                for joint in instance['joints']:
                    source = observed.get(joint['joint_id'])
                    if source is None or source['status'] != 'supported' or source.get('pixel') is None:
                        continue
                    sample = lifter.lift(*source['pixel'], **self.binding['sampling'])
                    joint.update(reason=sample.get('reason'), depth_support=sample.get('support', 0),
                                 depth_spread_m=sample.get('spread_m'))
                    if sample['status'] == 'measured':
                        need(len(sample['xyz']) == 3 and all(finite(v) for v in sample['xyz']), 'finite_nominal_sample_required')
                        joint.update(status='candidate', camera_xyz_m=deepcopy(sample['xyz']), reason='independent_error_unavailable')
                    else:
                        joint['reason'] = sample.get('reason', 'depth_unavailable')
            count = sum(j['status'] == 'candidate' for inst in body['instances'] for j in inst['joints'])
            body.update(status='candidate' if count else 'unavailable',
                        reason='independent_error_unavailable' if count else 'no_metric_prediction')
        except Exception as error:
            body['reason'] = bounded_reason(error)
        self.records[event['id']] = (fingerprint, deepcopy(body))
        return body

    def _media(self, event, frame, records):
        # The existing-API mode deploys without the modern /frame-pair client.
        # Keep the old mode's validator and JSON rules byte-for-byte in use.
        from paired_preview_client import checked_pair, _json
        need(set(records) == set(ROLES), 'exact_candidate_media_roles_required')
        p, b = event['payload'], self.binding
        for role, found in records.items():
            need(set(found) == {'record', 'data'}, 'original_media_snapshot_required')
            row, raw = found['record'], found['data']
            need(isinstance(raw, bytes) and 0 < len(raw) <= LIMITS[role]
                 and len(raw) == row['size'] and sha(raw) == row['sha256']
                 and row['session_id'] == event['session_id'] and row['generation'] == b['media_generation'],
                 'original_media_bytes_or_generation_changed')
        rgb, bundle, pair = (records[k] for k in ROLES)
        need(frame == rgb['record'] and rgb['record']['mime_type'] == 'image/jpeg'
             and bundle['record']['mime_type'] == 'application/x-npz'
             and pair['record']['mime_type'] == 'application/json', 'original_candidate_media_family_changed')
        paired = _json(pair['data'])
        need(paired.get('schema') == 'home-lab-paired-preview-original-1' and paired.get('version') == 1
             and paired['session_id'] == event['session_id'] and paired['site_id'] == b['session']['site_id']
             and paired['camera_id'] == b['session']['camera_id'] and paired['experiment_generation'] == b['model_generation']
             and paired['store_generation'] == b['media_generation'] and paired['capture_epoch'] == p['capture_epoch']
             and paired['rgb_frame_id'] == p['frame_id'] and paired['rgb_media_id'] == rgb['record']['id']
             and paired['rgb_sha256'] == rgb['record']['sha256']
             and paired['bundle_media_id'] == bundle['record']['id'] and paired['bundle_sha256'] == bundle['record']['sha256']
             and rgb['record']['id'] in p.get('media_ids', [])
             and pair['record']['metadata'].get('stream') == 'paired_preview_original'
             and bundle['record']['metadata'].get('stream') == 'rgbd_bundle', 'original_pair_binding_changed')
        need(sha(encoded(paired['calibration'])) == paired['calibration_sha256']
             and bundle['record']['metadata']['calibration'] == paired['calibration'], 'original_calibration_changed')
        header = base64.urlsafe_b64encode(encoded(paired['rgb_receipt'])).decode().rstrip('=')
        # checked_pair invokes recorder.validate_npz's bounded NPY-header,
        # expanded-size/dtype/byte-count checks BEFORE its first np.load.
        checked = checked_pair(rgb['data'], header, bundle['data'], paired['calibration'])
        meta = rgb['record']['metadata']
        need(meta['source_timing_receipt'] == paired['rgb_receipt']
             and checked['source_metadata'] == paired['source_metadata'] == bundle['record']['metadata']['source_metadata']
             and meta['frame_id'] == p['frame_id'] and meta['capture_epoch'] == p['capture_epoch']
             and meta['site_id'] == b['session']['site_id'] and meta['camera_id'] == b['session']['camera_id']
             and meta['observed_at'] == p['observed_at'] == checked['observed_at']
             and meta['sequence'] == paired['rgb_receipt']['sequence']
             and meta['observed_at'] == float(Decimal(paired['rgb_receipt']['observed_at_unix_ns']) / Decimal(1000000000)),
             'original_rgb_frame_changed')
        need(all(finite(paired.get(k)) for k in ('first_pair_received_at', 'retained_at'))
             and 0 <= paired['first_pair_received_at'] <= paired['retained_at'] <= self._receipt(event)
             and finite(meta.get('available_at')) and 0 <= meta['available_at'] <= self._receipt(event),
             'original_pair_receipt_clock_refused')
        from room_facts import decode_bundle
        return (*decode_bundle(bundle['data']), paired)

    def _receipt(self, event):
        # collect() validates source availability separately. Pair receipt is
        # bounded by the model availability, which follows retention/offer.
        return event['payload']['available_at']

    def _bundled_capture(self, event, started, terminal, *, frame_record=None):
        """The bundled family has no challenged-JPEG/model-frame start receipt."""
        sid, binding = self.binding['session']['id'], self.binding['bundled_rgb_binding']
        need(isinstance(started, dict) and started.get('kind') == BUNDLED_START
             and started.get('session_id') == sid and type(started.get('id')) is int and started['id'] > 0,
             'bundled_capture_start_unavailable')
        s = started.get('payload', {})
        need(isinstance(s, dict) and set(s) == {'schema', 'version', 'binding', 'capture_id',
             'anchor_b_media_ref', 'anchor_b_header', 'started_at'}
             and s['schema'] == 'home-lab-bundled-rgb-start-1' and type(s['version']) is int and s['version'] == 1
             and s['binding'] == binding and isinstance(s['capture_id'], str) and 0 < len(s['capture_id']) <= 160
             and isinstance(s['anchor_b_media_ref'], dict)
             and set(s['anchor_b_media_ref']) == {'id', 'generation', 'sha256', 'size'}
             and s['anchor_b_media_ref']['generation'] == self.binding['media_generation']
             and isinstance(s['anchor_b_header'], str) and 0 < len(s['anchor_b_header']) <= 32768
             and finite(s['started_at']) and s['started_at'] >= 0, 'bundled_capture_start_binding_changed')
        need(isinstance(terminal, dict) and terminal.get('kind') == BUNDLED_TERMINAL
             and terminal.get('session_id') == sid and type(terminal.get('id')) is int
             and terminal['id'] > started['id'], 'bundled_capture_terminal_missing')
        t = terminal.get('payload', {})
        need(isinstance(t, dict) and set(t) == {'schema', 'version', 'binding', 'capture_id', 'started_event_id',
             'status', 'reason', 'support_media_ref', 'completed_at'}
             and t['schema'] == 'home-lab-bundled-rgb-terminal-1' and type(t['version']) is int and t['version'] == 1
             and t['binding'] == binding and t['capture_id'] == s['capture_id']
             and type(t['started_event_id']) is int and t['started_event_id'] == started['id']
             and t['status'] in ('retained', 'refused', 'incomplete')
             and isinstance(t['reason'], str) and 0 < len(t['reason']) <= 100
             and (isinstance(t['support_media_ref'], dict) or
                  t['status'] == 'incomplete' and t['support_media_ref'] is None)
             and finite(t['completed_at']) and t['completed_at'] >= s['started_at'],
             'bundled_capture_terminal_binding_changed')
        if event is not None:
            need(event.get('session_id') == sid, 'bundled_model_session_changed')
        return s, t

    def _bundled_media(self, event, frame, records, started, terminal):
        # Importing this candidate consumer and its prepared CLI remains inert.
        from bundle_rgb_input import SUPPORT_SCHEMA, SUPPORT_STREAM, support_refs, validate_support
        need(isinstance(records, dict) and 'support' in records and set(records) <= set(BUNDLED_LIMITS),
             'exact_bundled_media_roles_required')
        sid, generation = self.binding['session']['id'], self.binding['media_generation']
        for role, found in records.items():
            need(isinstance(found, dict) and set(found) == {'record', 'data'}, 'original_media_snapshot_required')
            row, raw = found['record'], found['data']
            need(isinstance(row, dict) and isinstance(raw, bytes) and 0 < len(raw) <= BUNDLED_LIMITS[role]
                 and len(raw) == row['size'] and sha(raw) == row['sha256']
                 and row['session_id'] == sid and row['generation'] == generation,
                 'original_media_bytes_or_generation_changed')
        support_row = records['support']['record']
        support = original_json(records['support']['data'])
        need(isinstance(support, dict) and support.get('schema') == SUPPORT_SCHEMA
             and support_row.get('mime_type') == 'application/json'
             and support_row.get('metadata', {}).get('stream') == SUPPORT_STREAM,
             'bundled_support_original_changed')
        refs = support_refs(support)
        need(isinstance(refs, dict) and set(refs) <= set(BUNDLED_LIMITS) - {'support'}
             and set(records) == {'support'} | set(refs), 'exact_bundled_media_roles_required')
        for role, ref in refs.items():
            need(isinstance(ref, dict) and set(ref) == {'id', 'generation', 'sha256', 'size'}
                 and all(records[role]['record'].get(k) == v for k, v in ref.items()),
                 'bundled_support_media_ref_changed')
        self._bundled_capture(event, started, terminal, frame_record=frame)
        return validate_support(records, binding=self.binding['bundled_rgb_binding'], event=event,
                                frame=frame, started=started, terminal=terminal)

    def _existing_capture(self, event, started, terminal, *, frame_record=None):
        from assessment_existing_api import START_KIND, START_SCHEMA, TERMINAL_KIND, TERMINAL_SCHEMA
        sid = self.binding['session']['id'] if event is None else event['session_id']
        if event is None:
            need(isinstance(frame_record, dict) and frame_record.get('session_id') == sid
                 and frame_record.get('generation') == self.binding['media_generation'],
                 'assessment_original_frame_required')
            p, media_ids = frame_record.get('metadata', {}), [frame_record['id']]
        else:
            p, media_ids = event['payload'], event['payload'].get('media_ids', [])
        need(isinstance(started, dict) and started.get('kind') == START_KIND
             and started.get('session_id') == sid
             and type(started.get('id')) is int and started['id'] > 0,
             'assessment_capture_start_unavailable')
        s = started.get('payload', {})
        need(set(s) == {'schema', 'version', 'binding', 'capture', 'tap_started_at'}
             and s['schema'] == START_SCHEMA and type(s['version']) is int and s['version'] == 1
             and s['binding'] == self.binding['existing_api_binding'], 'assessment_capture_start_binding_changed')
        c = s['capture']
        need(isinstance(c, dict) and set(c) == {'capture_id', 'rgb_frame_id', 'capture_epoch',
             'rgb_media_ref', 'rgb_header', 'rgb_times'} and isinstance(c['capture_id'], str) and c['capture_id']
             and c['rgb_frame_id'] == p.get('frame_id') and c['capture_epoch'] == p.get('capture_epoch')
             and c['rgb_media_ref'].get('id') in media_ids
             and c['rgb_media_ref'].get('generation') == self.binding['media_generation']
             and set(c['rgb_times']) == {'observed_at', 'available_at', 'valid_until'}
             and all(finite(v) and v >= 0 for v in c['rgb_times'].values())
             and c['rgb_times']['observed_at'] == p.get('observed_at')
             and c['rgb_times']['observed_at'] <= c['rgb_times']['available_at'] < c['rgb_times']['valid_until']
             and c['rgb_times']['available_at'] <= p.get('available_at', -1)
             and finite(s['tap_started_at']) and s['tap_started_at'] >= c['rgb_times']['available_at'],
             'assessment_original_rgb_binding_changed')
        need(isinstance(terminal, dict) and terminal.get('kind') == TERMINAL_KIND
             and terminal.get('session_id') == sid
             and type(terminal.get('id')) is int and terminal['id'] > started['id'],
             'assessment_capture_terminal_missing')
        t = terminal.get('payload', {})
        need(set(t) == {'schema', 'version', 'binding', 'capture_id', 'started_event_id', 'attempts',
                       'status', 'reason', 'association_media_ref', 'processing_completed_at'}
             and t['schema'] == TERMINAL_SCHEMA and type(t['version']) is int and t['version'] == 1
             and t['binding'] == s['binding'] and t['capture_id'] == c['capture_id']
             and t['started_event_id'] == started['id'] and t['status'] in ('retained', 'miss', 'incomplete')
             and isinstance(t['reason'], str) and t['reason']
             and finite(t['processing_completed_at']) and t['processing_completed_at'] >= s['tap_started_at']
             and isinstance(t['attempts'], list) and len(t['attempts']) <= 2,
             'assessment_capture_terminal_binding_changed')
        previous = s['tap_started_at']
        for index, attempt in enumerate(t['attempts'], 1):
            need(set(attempt) == {'ordinal', 'npz_media_ref', 'calibration_media_ref',
                 'npz_read_completed_at', 'calibration_read_completed_at', 'result'}
                 and attempt['ordinal'] == index and isinstance(attempt['result'], str)
                 and (attempt['npz_read_completed_at'] is None or finite(attempt['npz_read_completed_at'])
                      and previous <= attempt['npz_read_completed_at'] <= t['processing_completed_at'])
                 and (attempt['calibration_read_completed_at'] is None or
                      finite(attempt['calibration_read_completed_at']) and
                      attempt['npz_read_completed_at'] is not None and
                      attempt['npz_read_completed_at'] <= attempt['calibration_read_completed_at'] <= t['processing_completed_at']),
                 'assessment_attempt_clock_or_identity_changed')
            previous = attempt['calibration_read_completed_at'] or attempt['npz_read_completed_at'] or previous
        if t['status'] != 'retained':
            need(t['association_media_ref'] is None, 'assessment_refused_association_present')
            raise ValueError('assessment_capture_' + t['status'] + '_' + bounded_reason(t['reason']))
        return s, t

    def _existing_media(self, event, frame, records, started, terminal):
        from assessment_existing_api import (validate_match, ASSOCIATION_SCHEMA, ASSOCIATION_STREAM,
                                             CALIBRATION_STREAM)
        need(set(records) == set(EXISTING_ROLES), 'exact_existing_api_media_roles_required')
        def ref(row):
            return {k: row[k] for k in ('id', 'generation', 'sha256', 'size')}
        for role, found in records.items():
            need(set(found) == {'record', 'data'}, 'original_media_snapshot_required')
            row, raw = found['record'], found['data']
            need(isinstance(raw, bytes) and 0 < len(raw) <= EXISTING_LIMITS[role]
                 and len(raw) == row['size'] and sha(raw) == row['sha256']
                 and row['session_id'] == event['session_id'] and row['generation'] == self.binding['media_generation'],
                 'original_media_bytes_or_generation_changed')
        rgb, bundle, calibration, association = (records[k] for k in EXISTING_ROLES)
        s, t = started['payload'], terminal['payload']
        need(frame == rgb['record'] and ref(rgb['record']) == s['capture']['rgb_media_ref']
             and rgb['record']['mime_type'] == 'image/jpeg'
             and bundle['record']['mime_type'] == 'application/x-npz'
             and bundle['record']['metadata'].get('stream') == 'rgbd_bundle'
             and calibration['record']['mime_type'] == association['record']['mime_type'] == 'application/json'
             and calibration['record']['metadata'].get('stream') == CALIBRATION_STREAM
             and association['record']['metadata'].get('stream') == ASSOCIATION_STREAM
             and t['association_media_ref'] == ref(association['record']), 'assessment_original_media_binding_changed')
        def unique(items):
            result = {}
            for key, value in items:
                need(key not in result, 'assessment_original_json_duplicate')
                result[key] = value
            return result
        a = json.loads(association['data'], object_pairs_hook=unique,
                       parse_constant=lambda _: need(False, 'assessment_original_json_nonfinite'))
        need(set(a) == {'schema', 'version', 'binding', 'capture', 'started_event_id',
             'selected_attempt_ordinal', 'bundle_media_ref', 'calibration_original_media_ref', 'source_metadata',
             'calibration_sha256', 'association_created_at', 'depth_clock', 'candidate_only', 'owner_local_only',
             'operational_geometry', 'identity_authority', 'engagement_authority', 'protection_authority', 'beam_authority'}
             and a['schema'] == ASSOCIATION_SCHEMA and type(a['version']) is int and a['version'] == 1
             and a['binding'] == s['binding'] and a['capture'] == s['capture']
             and a['started_event_id'] == started['id']
             and type(a['selected_attempt_ordinal']) is int
             and 1 <= a['selected_attempt_ordinal'] <= len(t['attempts'])
             and a['bundle_media_ref'] == ref(bundle['record'])
             and a['calibration_original_media_ref'] == ref(calibration['record'])
             and a['candidate_only'] is True and a['owner_local_only'] is True
             and a['operational_geometry'] is None
             and all(a[k] is False for k in ('identity_authority', 'engagement_authority', 'protection_authority', 'beam_authority'))
             and a['depth_clock'] == {'status': 'unassessed', 'mapping': None, 'exposure_error_bound_s': None},
             'assessment_association_changed')
        selected = t['attempts'][a['selected_attempt_ordinal'] - 1]
        need(selected['result'] == 'exact' and selected['npz_media_ref'] == a['bundle_media_ref']
             and selected['calibration_media_ref'] == a['calibration_original_media_ref']
             and finite(a['association_created_at'])
             and selected['calibration_read_completed_at'] is not None
             and selected['calibration_read_completed_at'] <= a['association_created_at'] <= t['processing_completed_at'],
             'assessment_association_attempt_changed')
        checked = validate_match(rgb['data'], s['capture']['rgb_header'], bundle['data'], calibration['data'])
        meta, p = rgb['record']['metadata'], event['payload']
        need(checked['source_metadata'] == a['source_metadata'] == bundle['record']['metadata']['source_metadata']
             and sha(encoded(checked['calibration'])) == a['calibration_sha256']
             and bundle['record']['metadata']['calibration'] == checked['calibration']
             and meta['source_timing_receipt'] == checked['receipt']
             and all(meta[k] == s['capture']['rgb_times'][k] for k in ('observed_at', 'available_at', 'valid_until'))
             and meta['frame_id'] == p['frame_id'] and meta['capture_epoch'] == p['capture_epoch']
             and meta['site_id'] == self.binding['session']['site_id']
             and meta['camera_id'] == self.binding['session']['camera_id']
             and meta['observed_at'] == checked['observed_at']
             and meta['sequence'] == checked['receipt']['sequence'], 'assessment_source_original_changed')
        from room_facts import decode_bundle
        return (*decode_bundle(bundle['data']), {**a, 'calibration': checked['calibration']})

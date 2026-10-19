"""Private, inert adapter from original Lab outputs to live context candidates.

The incumbent Lab owns capture and model execution. This module performs no I/O,
opens no camera, runs no model and publishes no qualified geometry. A server-side
consumer can page the existing read-authorized /sessions/{sid}/events route and
pass its original perception.output events here. Numeric candidates remain local.
"""
from copy import deepcopy
import math

from persisted_evidence import observation_record
from reasoning import digest


SCHEMA = 'home-lab-live-candidate-1'
MISSING_QUALIFICATION = (
    'candidate_error_bounds_not_independently_measured',
    'person_continuity_not_qualified',
    'complete_protection_enclosure_not_qualified',
    'work_target_not_qualified',
    'room_collision_assessment_not_bound',
    'fixture_optical_basis_not_bound',
)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    if value is None:
        return None
    result = [float(v) for v in value]
    if len(result) != 3 or not all(_finite(v) for v in result):
        raise ValueError('nonfinite_metric_candidate')
    return result


class LabLiveContextAdapter:
    """One finite session/arm/generation; retries never refresh source clocks.

    `geometry` is optional and contains only `bundle`, `calibration`, `site_map`
    and `frame_metadata`. These are the existing camera NPZ/calibration, saved
    den-site-map-1, and controller-verified metadata of the output's RGB media.
    No approximate image-coordinate to room-coordinate conversion is provided.
    """

    def __init__(self, session, *, arm, generation, max_events=1024):
        if (not isinstance(session, dict)
                or any(not isinstance(session.get(k), str) or not session[k]
                       for k in ('id', 'site_id', 'camera_id'))
                or arm not in ('a', 'b') or type(generation) is not int or generation < 1
                or type(max_events) is not int or not 1 <= max_events <= 4096):
            raise ValueError('bound_lab_session_required')
        self.session = {k: session[k] for k in ('id', 'site_id', 'camera_id')}
        self.arm, self.generation = arm, generation
        self.max_events, self.seen = max_events, {}
        self.last_observation = None

    def consume(self, event, *, received_at, geometry=None):
        if (not isinstance(event, dict) or type(event.get('id')) is not int or event['id'] < 1
                or event.get('kind') != 'perception.output' or not _finite(received_at)
                or event.get('session_id', self.session['id']) != self.session['id']):
            raise ValueError('original_lab_event_required')
        payload = event.get('payload')
        if not isinstance(payload, dict):
            raise ValueError('original_lab_event_required')
        original_digest = digest(payload)
        prior = self.seen.get(event['id'])
        if prior is not None:
            if prior != original_digest:
                raise ValueError('lab_event_identity_conflict')
            return self._empty('duplicate', 'exact_retry', event['id'], original_digest)
        if len(self.seen) >= self.max_events:
            raise ValueError('lab_candidate_event_budget_exhausted')
        record = observation_record(event['id'], 'perception.output', payload, self.session)
        model_result = payload.get('result')
        if (record is None or payload.get('arm') != self.arm
                or payload.get('generation') != self.generation
                or any(not isinstance(payload.get(k), str) or not payload[k]
                       for k in ('frame_id', 'capture_epoch'))
                or model_result.get('schema_version') != 1
                or model_result.get('coordinate_space') != 'source_pixels'
                or model_result.get('frame_id') != payload['frame_id']):
            raise ValueError('lab_event_binding_refused')
        observed, available, expiry = (payload[k] for k in ('observed_at', 'available_at', 'valid_until'))
        if not observed <= available <= received_at < expiry:
            raise ValueError('lab_event_expired_or_future')
        self.seen[event['id']] = original_digest
        order = (observed, event['id'])
        if self.last_observation is not None and order <= self.last_observation:
            return self._empty('late', 'older_observation', event['id'], original_digest)
        self.last_observation = order

        from jev_evidence import project
        # Existing projection validates pixels, instance association and COCO17.
        # Original identities below remain original; projection's egress tokens
        # are never used as substitute frame/epoch identities.
        projected = project([record])[0]
        persons = [o for o in projected['objects'] if o['label'] == 'person']
        poses = projected['pose_instances']
        result = self._empty('candidate', 'operational_qualification_missing', event['id'], original_digest)
        result['lineage'] = {k: deepcopy(payload[k]) for k in
            ('site_id', 'camera_id', 'arm', 'generation', 'frame_id', 'capture_epoch',
             'observed_at', 'available_at', 'valid_until')}
        result['lineage'].update(session_id=self.session['id'], first_consumer_received_at=received_at,
            evidence_roots=deepcopy(record['evidence_roots']))
        for key in ('model_ref', 'model_refs', 'method_ref', 'arm_snapshot', 'runtime'):
            if key in payload:
                result['lineage'][key + '_digest'] = digest(payload[key])
        result['image'] = {'coordinate_space': 'source_pixels',
            'quality': deepcopy(projected.get('quality', {})),
            'persons': deepcopy(persons), 'pose_instances': deepcopy(poses),
            'person_count': len(persons), 'pose_instance_count': len(poses),
            'supported_joint_count': sum(j['status'] == 'supported' for p in poses for j in p['joints'])}
        if geometry is None:
            result['missing_qualification'].insert(0, 'registered_depth_candidate_unavailable')
        else:
            try:
                result['metric'] = self._metric(projected, payload, geometry)
            except (ValueError, KeyError, TypeError) as error:
                reason = str(error) if isinstance(error, ValueError) else 'metric_input_invalid'
                if not reason.replace('_', '').isalnum() or len(reason) > 100:
                    reason = 'metric_input_invalid'
                result['metric'] = {'status': 'unavailable', 'reason': reason}
                result['missing_qualification'].insert(0, reason)
        return result

    def _empty(self, status, reason, event_id, payload_digest):
        return {'schema': SCHEMA, 'version': 1, 'status': status, 'reason': reason,
            'source_event_id': event_id, 'source_payload_digest': payload_digest,
            'owner_local_only': True, 'operational_geometry': None,
            'identity_authority': False, 'engagement_authority': False, 'beam_authority': False,
            'missing_qualification': list(MISSING_QUALIFICATION),
            'image': None, 'metric': {'status': 'unavailable', 'reason': 'registered_depth_candidate_unavailable'}}

    def _metric(self, projected, payload, geometry):
        import room_facts as rf
        import head3d
        from spatial_validation import check_mount
        if not isinstance(geometry, dict) or set(geometry) != {'bundle', 'calibration', 'site_map', 'frame_metadata'}:
            raise ValueError('exact_existing_geometry_inputs_required')
        raw = geometry['bundle']
        if not isinstance(raw, bytes) or not 0 < len(raw) <= 4 * 1024**2:
            raise ValueError('bounded_existing_bundle_required')
        depth, metadata = rf.decode_bundle(raw)
        calibration, frame = geometry['calibration'], geometry['frame_metadata']
        if (not isinstance(frame, dict) or frame.get('stream') != 'rgb'
                or frame.get('frame_id') != payload['frame_id']
                or frame.get('capture_epoch') != payload['capture_epoch']
                or metadata.get('session') != payload['capture_epoch']
                or not isinstance(calibration, dict) or calibration.get('session') != payload['capture_epoch']):
            raise ValueError('metric_source_epoch_or_frame_mismatch')
        receipt = frame.get('source_timing_receipt')
        stamp = receipt.get('frame_device_ns') if isinstance(receipt, dict) else None
        if type(stamp) is not int or stamp <= 0:
            raise ValueError('original_rgb_device_receipt_required')
        depth_stamp = metadata['frames']['rgb'].get('device_timestamp_seconds')
        if not _finite(depth_stamp):
            raise ValueError('bundle_rgb_device_time_required')
        skew = abs(stamp / 1e9 - depth_stamp)
        if skew > rf.PAIR_TOLERANCE_S:
            raise ValueError('registered_depth_outside_existing_pair_window')
        room = rf.load_site_map(geometry['site_map'], site_id=self.session['site_id'], camera_id=self.session['camera_id'])
        if rf.calibration_content_id(calibration) != room['calibration_content_id']:
            raise ValueError('calibration_differs_from_saved_map')
        if check_mount(room['mount_signature'], depth)['status'] != 'unchanged':
            raise ValueError('saved_camera_mount_not_verified')
        # Same established intrinsic/extrinsic construction as RoomFactsStage.
        stage = rf.RoomFactsStage.__new__(rf.RoomFactsStage)
        stage.models, stage.room = {}, room
        model = stage._model(metadata, calibration)
        quality = projected.get('quality', {})
        if (quality.get('image_width'), quality.get('image_height')) != tuple(model['rgb_size']):
            raise ValueError('original_rgb_dimensions_mismatch')
        measured = rf.measure(projected, depth, model, room)
        if measured is None:
            raise ValueError('single_person_pose_or_depth_unavailable')
        body = {name: _point(point) for name, point in measured['body'].items()}
        box, pose = rf._person_pose(projected)
        lifter = head3d.DepthLifter(depth, model, rgb_roi=box)
        joints = head3d.joints_of(pose)
        for name, index in (('hand_left', 9), ('hand_right', 10)):
            sample = lifter.lift(*joints[index], radius_px=rf.JOINT_PATCH_PX) if index in joints else None
            body[name] = (_point(rf.to_room(room['matrix'], sample['xyz']))
                          if sample is not None and sample['status'] == 'measured' else None)
        return {'status': 'candidate', 'frame': rf.MAP_FRAME, 'unit': 'm',
            'torso_m': _point(measured['torso']), 'body_points_m': body,
            'pairing_skew_s': skew, 'calibration_content_id': room['calibration_content_id'],
            'map_ref': {k: room[k] for k in ('map_id', 'revision', 'model_sha256')},
            'mount_check': 'unchanged', 'uncertainty_m': None,
            'method': 'existing_room_facts_measure', 'qualified': False}

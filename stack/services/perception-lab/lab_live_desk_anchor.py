"""One retained visible nose surface for a separately qualified desk method.

This adapter borrows the incumbent Store. It never captures, runs inference,
maps a new depth clock, identifies a person or constructs hidden anatomy. A
supported model keypoint is only a candidate until the fixed independent
association, metric and temporal qualifications all apply. Binary originals
must be retained before a selected RGB-reference anchor can leave this object.
"""
from copy import deepcopy
from decimal import Decimal
import base64
import hashlib
import json
import math
import threading

from capture_timing import commissioning, map_depth_time
from bundle_ingest import calibration_content_id
from depth_association import camera_model
from geometry import transform_point
from head3d import DepthLifter
from journal import canonical, digest, finite, identifier
from lab_live_acquisition import utc_now
from live_capture import REGISTRATION_KEYS
from paired_preview_client import checked_pair, encoded, sha
from persisted_evidence import observation_record
from registered_depth import DERIVATION, SOURCE_PREFIX
from room_facts import decode_bundle
from store import Store

ROOT_LINEAGE = 'persisted-observation-record-1'
SCOPE_KEYS = {'site_id', 'room_id', 'session_id', 'domain_epoch'}
QUALIFICATION_KEYS = {'schema', 'version', 'stereo_policy', 'association', 'projection',
                      'registration', 'clock_mapping', 'depth_clock_policy'}
POLICY_KEYS = set('schema version id status scope method_ref association_method_ref projection_ref registration_ref '
    'clock_mapping_ref depth_clock_ref calibration_content_id requirements_ref assessment_report_ref applicability_ref '
    'nose_error_bound_m max_abs_skew_s max_depth_age_s max_speed_m_s max_accel_m_s2 radius_px min_support '
    'max_spread_m valid_from available_at valid_until'.split())
ASSOCIATION_KEYS = set('schema version id status scope person_ref subject_ref method_ref source_generation source_position '
    'capture_ref instance_ref state occupant_count nose_visible observed_at available_at valid_until '
    'requirements_ref assessment_ref'.split())
SUPPORT_KEYS = set('schema version id scope lab_binding_digest capture_ref source_position source_generation '
    'model_generation media_generation landmark media_refs media_binding_digest depth point stereo_policy_ref '
    'association_ref projection_ref registration_ref first_consumer_received_at produced_at'.split())
DEPTH_KEYS = set('capture_epoch sequence device_timestamp_seconds plane_sha256 root observed_at available_at valid_until '
                 'uncertainty_s clock_mapping_id clock_mapping calibration_content_id'.split())
POINT_KEYS = set('camera_xyz_m room_xyz_m point_observed_at nose_error_bound_m temporal_error_m error_bound_m '
                 'spread_m support radius_px min_support'.split())
MEDIA_ROLES = {'rgb', 'bundle', 'paired_original', 'mapped_depth'}
MEDIA_LIMITS = {'rgb': 256*1024, 'bundle': 4*1024**2, 'paired_original': 256*1024, 'mapped_depth': 640*400*2}


class DeskAnchorRefusal(ValueError):
    """A bounded failure category, without household measurements in messages."""


def need(condition, reason):
    if not condition:
        raise DeskAnchorRefusal(reason)


def exact(value, keys, reason):
    need(isinstance(value, dict) and set(value) == set(keys), reason)


def ref(value):
    exact(value, {'id', 'digest'}, 'stereo_qualification_reference_required')
    identifier(value['id'], 'stereo_qualification_id')
    need(isinstance(value['digest'], str) and len(value['digest']) == 64
         and all(c in '0123456789abcdef' for c in value['digest']), 'stereo_qualification_digest_required')
    return value


def body_ref(value):
    return {'id': value['id'], 'digest': digest(value)}


def vector(value):
    return isinstance(value, list) and len(value) == 3 and all(finite(n) for n in value)


def media_ref(record):
    return {key: deepcopy(record[key]) for key in ('id', 'sha256', 'size')}


def _source_epoch(value):
    exact(value, {'capture_epoch', 'capture_seq', 'revision'}, 'stereo_capture_position_required')
    need(type(value['capture_seq']) is int and value['capture_seq'] >= 0
         and type(value['revision']) is int and value['revision'] >= 0, 'stereo_capture_position_required')
    identifier(value['capture_epoch'], 'stereo_capture_epoch')


def _stereo_policy(p, *, scope, policy_ref, now):
    exact(p, POLICY_KEYS, 'closed_stereo_policy_required')
    need(p['schema'] == 'home-desk-stereo-policy-1' and type(p['version']) is int and p['version'] == 1
         and p['status'] == 'independently_assessed' and p['scope'] == scope
         and body_ref(p) == policy_ref, 'independent_stereo_policy_required')
    for key in ('method_ref', 'association_method_ref', 'projection_ref', 'registration_ref', 'clock_mapping_ref',
                'depth_clock_ref', 'requirements_ref', 'assessment_report_ref', 'applicability_ref'):
        ref(p[key])
    for key in ('nose_error_bound_m', 'max_abs_skew_s', 'max_depth_age_s', 'max_speed_m_s', 'max_accel_m_s2',
                'max_spread_m', 'valid_from', 'available_at', 'valid_until'):
        need(finite(p[key]) and p[key] >= 0, 'finite_stereo_qualification_bound_required')
    need(0 < p['nose_error_bound_m'] <= 10 and 0 < p['max_abs_skew_s'] <= .1
         and 0 < p['max_depth_age_s'] <= 3 and 0 < p['max_spread_m'] <= 10
         and type(p['radius_px']) is int and 1 <= p['radius_px'] <= 16
         and type(p['min_support']) is int and 3 <= p['min_support'] <= 256
         and p['valid_from'] <= p['available_at'] <= now < p['valid_until'], 'stereo_qualification_not_current')
    return p


def _camera_qualification(q, p, *, scope, clock_unit='unix_ns'):
    r, projection = q['registration'], q['projection']
    need(isinstance(r, dict) and set(r) == REGISTRATION_KEYS
         and r['schema'] == 'home-capture-registration-1' and type(r['version']) is int and r['version'] == 1
         and r['site_id'] == scope['site_id'] and r['room_id'] == scope['room_id']
         and r['source_frame'] == 'rectified_left' and r['frame'] == 'z_up_metric_floor0' and r['unit'] == 'm'
         and finite(r['error_bound_m']) and r['error_bound_m'] >= 0
         and r['transform_ref']['digest'] == digest(r['room_from_rectified_left'])
         and body_ref(r) == p['registration_ref'], 'qualified_stereo_registration_required')
    ref(r['validation_ref'])
    need(isinstance(projection, dict) and body_ref(projection) == p['projection_ref']
         and projection.get('status') == 'validated' and projection.get('site_id') == r['site_id']
         and projection.get('camera_id') == r['camera_id']
         and projection.get('mount_revision') == r['mount_revision'] and projection.get('validation_receipt')
         and finite(projection.get('error_bound_m')) and projection['error_bound_m'] >= 0
         and projection.get('frame') == 'rectified_left'
         and (projection.get('width'), projection.get('height')) == (640, 400), 'qualified_stereo_projection_required')
    c = q['clock_mapping']
    need(isinstance(c, dict) and p['clock_mapping_ref'] == {'id': c.get('mapping_id'), 'digest': c.get('mapping_digest')}
         and clock_unit in ('unix_ns', 'unix_s') and c.get('original_unit') == clock_unit,
         'qualified_original_rgb_clock_required')
    dc = commissioning(q['depth_clock_policy'])
    need(body_ref(dc) == p['depth_clock_ref'], 'qualified_original_depth_clock_required')
    return r, projection, dc


def _static_stereo_qualification(originals, *, scope, policy_ref, now):
    """Static metrology only; no person/association or operational authority."""
    exact(originals, {'stereo_policy', 'projection', 'registration', 'clock_mapping', 'depth_clock_policy'},
          'complete_static_stereo_originals_required')
    p = _stereo_policy(originals['stereo_policy'], scope=scope, policy_ref=policy_ref, now=now)
    r, projection, dc = _camera_qualification(originals, p, scope=scope)
    return p, r, projection, dc


def _qualification(q, *, scope, policy_ref, now, clock_unit='unix_ns'):
    exact(q, QUALIFICATION_KEYS, 'complete_fixed_stereo_qualification_required')
    need(q['schema'] == 'home-desk-stereo-qualification-1' and type(q['version']) is int
         and q['version'] == 1, 'stereo_qualification_version_required')
    # Preserve the operational validator's original policy -> association ->
    # camera order. Static candidate measurement never substitutes for this.
    p = _stereo_policy(q['stereo_policy'], scope=scope, policy_ref=policy_ref, now=now)
    a = q['association']
    exact(a, ASSOCIATION_KEYS, 'qualified_stereo_association_original_required')
    need(a['schema'] == 'home-desk-stereo-association-1' and type(a['version']) is int and a['version'] == 1
         and a['status'] == 'qualified' and a['scope'] == scope
         and a['method_ref'] == p['association_method_ref'] and a['state'] == 'seated_unique'
         and type(a['occupant_count']) is int and a['occupant_count'] == 1 and a['nose_visible'] is True,
         'qualified_unique_visible_nose_required')
    _source_epoch(a['source_position'])
    person = a['person_ref']
    need(isinstance(person, dict) and set(person) == SCOPE_KEYS | {'pid', 'track_epoch'}
         and all(person[k] == scope[k] for k in SCOPE_KEYS) and type(person['pid']) is int
         and person['pid'] >= 0, 'qualified_stereo_person_scope_required')
    identifier(person['track_epoch'], 'stereo_track_epoch')
    exact(a['subject_ref'], {'subject_id', 'subject_epoch'}, 'qualified_stereo_subject_required')
    for key in ('subject_id', 'subject_epoch'):
        identifier(a['subject_ref'][key], key)
    for key in ('requirements_ref', 'assessment_ref'):
        ref(a[key])
    need(type(a['source_generation']) is int and a['source_generation'] >= 1
         and all(finite(a[k]) for k in ('observed_at', 'available_at', 'valid_until'))
         and p['available_at'] <= a['observed_at'] <= a['available_at'] <= now < a['valid_until'],
         'stereo_association_not_current')
    r, projection, dc = _camera_qualification(q, p, scope=scope, clock_unit=clock_unit)
    return p, a, r, projection, dc


def _projected_pose(capture_original):
    event = capture_original['event']
    p = event['payload']
    session = {'id': event['session_id'], 'site_id': p['site_id'], 'camera_id': p['camera_id']}
    record = observation_record(event['id'], event['kind'], p, session)
    need(record is not None, 'original_stereo_pose_record_required')
    from jev_evidence import project
    projected = project([record])[0]
    people = [o for o in projected['objects'] if o.get('label') == 'person']
    poses = projected['pose_instances']
    need(len(people) == 1 and len(poses) == 1 and people[0].get('instance_ref') is not None
         and poses[0].get('instance_ref') == people[0]['instance_ref'], 'one_matched_stereo_person_pose_required')
    joints = [j for j in poses[0].get('joints', []) if j.get('joint_id') == 0]
    need(len(joints) == 1 and joints[0].get('status') == 'supported'
         and isinstance(joints[0].get('pixel'), list) and len(joints[0]['pixel']) == 2
         and all(finite(v) for v in joints[0]['pixel']), 'supported_original_nose_pixel_required')
    u, v = joints[0]['pixel']
    need(0 <= u < 640 and 0 <= v < 360, 'original_nose_pixel_outside_rgb')
    return people[0]['instance_ref'], [u, v]


def _checked_media(support, capture_original, resolve_media):
    need(callable(resolve_media), 'fixed_retained_stereo_media_resolver_required')
    exact(support['media_refs'], MEDIA_ROLES, 'exact_stereo_binary_roles_required')
    media = {}
    for role in MEDIA_ROLES:
        requested = support['media_refs'][role]
        exact(requested, {'id', 'sha256', 'size'}, 'exact_stereo_binary_reference_required')
        need(type(requested['size']) is int and 0 < requested['size'] <= MEDIA_LIMITS[role], 'stereo_binary_size_bound')
        found = resolve_media(deepcopy(requested))
        exact(found, {'record', 'data'}, 'retained_stereo_binary_required')
        row, raw = found['record'], found['data']
        need(isinstance(row, dict) and media_ref(row) == requested and isinstance(raw, bytes)
             and len(raw) == requested['size'] and sha(raw) == requested['sha256']
             and row['session_id'] == support['scope']['session_id'] and row['generation'] == support['media_generation'],
             'retained_stereo_binary_mismatch')
        media[role] = (row, raw)
    pair_row, pair_raw = media['paired_original']
    original = json.loads(pair_raw)
    need(original.get('schema') == 'home-lab-paired-preview-original-1' and original.get('version') == 1
         and pair_row['mime_type'] == 'application/json'
         and pair_row['metadata'].get('stream') == 'paired_preview_original', 'original_paired_receipt_required')
    rgb, jpeg = media['rgb']; bundle, raw = media['bundle']; depth, depth_raw = media['mapped_depth']
    frame = capture_original['frame_metadata']; event = capture_original['event']
    need(original['session_id'] == event['session_id'] == support['scope']['session_id']
         and original['site_id'] == support['scope']['site_id'] and original['camera_id'] == frame['camera_id']
         and original['experiment_generation'] == support['model_generation'] == event['payload']['generation']
         and original['store_generation'] == support['media_generation']
         and original['rgb_media_id'] == rgb['id'] and original['bundle_media_id'] == bundle['id']
         and original['rgb_sha256'] == rgb['sha256'] and original['bundle_sha256'] == bundle['sha256']
         and rgb['id'] in event['payload']['media_ids'] and rgb['mime_type'] == 'image/jpeg'
         and rgb['metadata'] == frame and bundle['metadata'].get('stream') == 'rgbd_bundle'
         and bundle['metadata'].get('format') == 'numeric_npz', 'exact_original_stereo_pair_required')
    # Re-encoding is validation only. The retained paired-original JSON bytes
    # remain the original; this header is never represented as original bytes.
    header = base64.urlsafe_b64encode(encoded(original['rgb_receipt'])).decode().rstrip('=')
    checked = checked_pair(jpeg, header, raw, original['calibration'])
    need(checked['receipt'] == frame['source_timing_receipt']
         and checked['source_metadata'] == original['source_metadata'] == bundle['metadata']['source_metadata']
         and checked['receipt']['session'] == original['capture_epoch'] == frame['capture_epoch']
         and original['rgb_frame_id'] == frame['frame_id']
         and json.loads(pair_row['source_key']) == ['paired-preview-v1', checked['identity']]
         and original['calibration_sha256'] == sha(encoded(original['calibration']))
         and bundle['metadata']['calibration'] == original['calibration'], 'original_stereo_components_changed')
    import numpy as np
    plane, metadata = decode_bundle(raw)
    need(depth['mime_type'] == 'application/octet-stream' and len(depth_raw) == 640*400*2
         and np.array_equal(np.frombuffer(depth_raw, dtype='<u2').reshape(400, 640), plane), 'original_depth_plane_mismatch')
    dm = depth['metadata']
    need(dm.get('derivation') == DERIVATION and dm.get('format') == 'depth_u16_le'
         and depth['source_key'] == SOURCE_PREFIX + bundle['id']
         and dm.get('source_media_id') == bundle['id'] and dm.get('source_sha256') == bundle['sha256']
         and dm.get('capture_epoch') == frame['capture_epoch'] and dm.get('alignment_frame') == 'rectified_left'
         and dm.get('clock_mapping_status') == 'verified' and dm.get('geometric_validity') == 'validated'
         and dm.get('units') == 'millimetres' and dm.get('invalid_value') == 0
         and (dm.get('width'), dm.get('height')) == (640, 400), 'existing_qualified_depth_clock_media_required')
    return media, original, plane, metadata


def _check_mapped_depth(frame, dm, metadata, depth_clock, rgb_media_id):
    mapped = map_depth_time(frame, metadata['frames']['depth'], depth_clock)
    mapping = dm['clock_mapping']
    need(dm['observed_at'] == mapped['observed_at'] and dm['uncertainty_seconds'] == mapped['uncertainty_seconds']
         and dm['sequence'] == mapped['sequence'] and mapping['commissioning_id'] == depth_clock['id']
         and mapping['semantics_receipt'] == depth_clock['semantics_receipt']
         and mapping['rgb_media_id'] == rgb_media_id and mapping['rgb_frame_id'] == frame['frame_id']
         and mapping['rgb_frame_device_ns'] == frame['source_timing_receipt']['frame_device_ns']
         and mapping['depth_device_seconds'] == metadata['frames']['depth']['device_timestamp_seconds']
         and mapping['delta_device_seconds'] == mapped['delta_device_seconds']
         and mapping['uncertainty_components_s'] == mapped['uncertainty_components_s'], 'original_depth_clock_mapping_changed')


def _depth_component(frame, dm, raw, scope):
    identity = [scope['site_id'], frame['camera_id'], frame['capture_epoch'], dm['sequence']]
    return dict(capture_epoch=frame['capture_epoch'], sequence=dm['sequence'],
        device_timestamp_seconds=dm['clock_mapping']['depth_device_seconds'], plane_sha256=sha(raw),
        root='lab-depth:' + sha(encoded(identity)), observed_at=dm['observed_at'], available_at=dm['available_at'],
        valid_until=dm['valid_until'], uncertainty_s=dm['uncertainty_seconds'], clock_mapping_id=dm['clock_mapping_id'],
        clock_mapping=deepcopy(dm['clock_mapping']), calibration_content_id=dm['calibration_content_id'])


def _capture_uncertainty(original):
    """Use the explicit source6 outward clock bound in temporal geometry math."""
    frame_bound = original['frame_metadata']['uncertainty_seconds']
    if original.get('schema') != 'home-original-lab-observation-2':
        return frame_bound
    need(original.get('version') == 2, 'explicit_bundled_capture_version_required')
    bound = original['clock_original']['clock']['uncertainty_s']
    need(finite(bound) and bound >= 0, 'outward_bundled_capture_uncertainty_required')
    return max(bound, frame_bound)


def sample_visible_nose_depth(plane, metadata, projection, registration, pixel, *,
                              radius_px, min_support, max_spread_m=None):
    """Pure selected-pixel sample; no clock, association or error qualification.

    The optional spread check preserves the historical anchor default. Callers
    supply their frozen sampling policy and independently enforce applicability.
    """
    model = camera_model(metadata, projection)
    measured = DepthLifter(plane, model, rgb_roi=[*pixel, *pixel]).lift(*pixel,
        radius_px=radius_px, min_support=min_support)
    need(measured.get('status') == 'measured' and (max_spread_m is None or measured['spread_m'] <= max_spread_m),
         'visible_nose_depth_unavailable')
    camera_point = [float(v) for v in measured['xyz']]
    room_point = transform_point({'frame': 'rectified_left', 'xyz_m': camera_point}, registration['room_from_rectified_left'],
        source_frame='rectified_left', target_frame='z_up_metric_floor0', version=registration['id'],
        calibration_error_m=registration['error_bound_m'])['xyz_m']
    return dict(camera_xyz_m=camera_point, room_xyz_m=room_point, spread_m=measured['spread_m'],
        support=measured['support'], radius_px=radius_px, min_support=min_support)


def _nose_point(original, depth, plane, metadata, p, registration, projection, pixel, *, check_spread=False):
    """The existing numeric measurement, independent of any person association."""
    frame = original['frame_metadata']
    skew = original['event']['payload']['observed_at']-depth['observed_at'] + _capture_uncertainty(original)+depth['uncertainty_s']
    temporal = p['max_speed_m_s']*skew + .5*p['max_accel_m_s2']*skew*skew
    measured = sample_visible_nose_depth(plane, metadata, projection, registration, pixel,
        radius_px=p['radius_px'], min_support=p['min_support'],
        max_spread_m=p['max_spread_m'] if check_spread else None)
    return dict(camera_xyz_m=measured['camera_xyz_m'], room_xyz_m=measured['room_xyz_m'], point_observed_at=depth['observed_at'],
        nose_error_bound_m=p['nose_error_bound_m'], temporal_error_m=temporal,
        error_bound_m=p['nose_error_bound_m']+temporal, spread_m=measured['spread_m'], support=measured['support'],
        radius_px=p['radius_px'], min_support=p['min_support'])


def validate_stereo_support(support, *, qualification, anchor, capture_record, capture_original,
                            scope, sensor_roots, policy_ref, now, resolve_media):
    """Pure full-original verifier; retained bytes have no live Store fallback."""
    exact(support, SUPPORT_KEYS, 'closed_stereo_nose_original_required')
    need(support['schema'] == 'home-desk-stereo-nose-anchor-1' and type(support['version']) is int
         and support['version'] == 1 and support['scope'] == scope, 'stereo_original_scope_required')
    p, association, registration, projection, depth_clock = _qualification(qualification, scope=scope,
                                                                         policy_ref=policy_ref, now=now)
    need(support['stereo_policy_ref'] == policy_ref and support['association_ref'] == body_ref(association)
         and support['registration_ref'] == body_ref(registration) and support['projection_ref'] == body_ref(projection),
         'stereo_qualification_original_changed')
    exact(support['depth'], DEPTH_KEYS, 'closed_original_depth_component_required')
    exact(support['point'], POINT_KEYS, 'closed_original_nose_point_required')
    exact(support['landmark'], {'source_event_id', 'instance_ref', 'joint_id', 'rgb_pixel', 'surface'},
          'closed_original_nose_landmark_required')
    media, paired, plane, metadata = _checked_media(support, capture_original, resolve_media)
    dm = media['mapped_depth'][0]['metadata']; frame = capture_original['frame_metadata']
    need(support['media_binding_digest'] == sha(encoded({'scope': scope,
        'lab_binding_digest': support['lab_binding_digest'], 'media_generation': support['media_generation'],
        'camera_id': frame['camera_id']})), 'stereo_media_retention_binding_changed')
    return _validate_stereo_components(support, qualification=qualification, anchor=anchor,
        capture_record=capture_record, capture_original=capture_original, scope=scope,
        sensor_roots=sensor_roots, policy_ref=policy_ref, now=now,
        media=media, calibration=paired['calibration'], plane=plane, metadata=metadata)


def _validate_stereo_components(support, *, qualification, anchor, capture_record, capture_original,
                                scope, sensor_roots, policy_ref, now, media, calibration, plane, metadata, bundled_clock=None):
    """Shared metric/association checks AFTER a version-specific raw verifier."""
    p, association, registration, projection, depth_clock = _qualification(qualification, scope=scope,
                                                                         policy_ref=policy_ref, now=now,
        clock_unit='unix_s' if bundled_clock is not None else 'unix_ns')
    need(support['scope'] == scope and support['stereo_policy_ref'] == policy_ref
         and support['association_ref'] == body_ref(association)
         and support['registration_ref'] == body_ref(registration) and support['projection_ref'] == body_ref(projection),
         'stereo_qualification_original_changed')
    exact(support['depth'], DEPTH_KEYS, 'closed_original_depth_component_required')
    exact(support['point'], POINT_KEYS, 'closed_original_nose_point_required')
    exact(support['landmark'], {'source_event_id', 'instance_ref', 'joint_id', 'rgb_pixel', 'surface'},
          'closed_original_nose_landmark_required')
    dm = media['mapped_depth'][0]['metadata']; frame = capture_original['frame_metadata']
    observed = capture_original['event']['payload']['observed_at']
    pos = {'capture_epoch': frame['capture_epoch'], 'capture_seq': frame['sequence'], 'revision': 0}
    need(support['source_position'] == association['source_position'] == anchor['source_position'] == pos
         and support['source_generation'] == association['source_generation'] == anchor['source_generation']
         and support['capture_ref'] == association['capture_ref']
         and capture_record['capture_epoch'] == pos['capture_epoch'] and capture_record['capture_seq'] == pos['capture_seq']
         and association['observed_at'] == observed and association['person_ref'] == anchor['person_ref']
         and anchor['scope'] == scope and anchor['method_ref'] == association['method_ref'], 'stereo_current_association_mismatch')
    need(registration['camera_id'] == projection['camera_id'] == frame['camera_id']
         and all(anchor['clock'].get(k) == qualification['clock_mapping'].get(k)
                 for k in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')),
         'stereo_camera_or_rgb_clock_changed')
    instance, pixel = _projected_pose(capture_original)
    need(support['landmark'] == {'source_event_id': capture_original['event']['id'], 'instance_ref': instance,
          'joint_id': 0, 'rgb_pixel': pixel, 'surface': 'visible_nose_surface'}
         and association['instance_ref'] == instance, 'original_visible_nose_landmark_changed')
    need(projection['id'] == p['calibration_content_id'] == dm['calibration_content_id']
         == calibration_content_id(calibration), 'stereo_calibration_content_changed')
    if bundled_clock is None:
        _check_mapped_depth(frame, dm, metadata, depth_clock, media['rgb'][0]['id'])
    else:
        # The source6 wrapper has already recomputed all seven raw originals
        # and B-to-depth mapping. This private metric helper never forges a C
        # JPEG receipt or treats its derived UTC original as camera nanoseconds.
        need(support.get('schema') == 'home-desk-stereo-nose-anchor-3'
             and type(support.get('version')) is int and support['version'] == 3
             and support.get('original_family') == 'existing-api-bundled-rgb-1'
             and isinstance(bundled_clock, dict)
             and bundled_clock.get('schema') == 'home-lab-bundled-rgb-clock-original-1'
             and type(bundled_clock.get('version')) is int and bundled_clock['version'] == 1
             and capture_original.get('schema') == 'home-original-lab-observation-2'
             and capture_original.get('version') == 2
             and capture_original.get('clock_original') == bundled_clock
             and support.get('bundled_originals', {}).get('clock_original') == bundled_clock,
             'validated_bundled_clock_original_required')
    d = support['depth']
    expected_depth = _depth_component(frame, dm, media['mapped_depth'][1], scope)
    need(d == expected_depth and d['observed_at'] <= observed and d['available_at'] <= now
         and d['observed_at'] <= d['available_at'] < d['valid_until'], 'original_depth_identity_or_first_time_changed')
    skew = observed-d['observed_at'] + _capture_uncertainty(capture_original) + d['uncertainty_s']
    need(skew <= p['max_abs_skew_s'], 'stereo_depth_rgb_temporal_applicability_exceeded')
    point = _nose_point(capture_original, d, plane, metadata, p, registration, projection, pixel, check_spread=True)
    room_point = point['room_xyz_m']
    expected_clock_value = (str(frame['source_timing_receipt']['observed_at_unix_ns'])
                            if bundled_clock is None else bundled_clock['clock']['original_value'])
    need(support['point'] == point and anchor['point_m'] == room_point and anchor['error_bound_m'] == point['error_bound_m']
         and anchor['observed_at'] == observed and anchor['clock']['original_value'] == expected_clock_value,
         'stereo_anchor_point_or_error_changed')
    if bundled_clock is not None:
        need({**anchor['clock'], 'status': 'commissioned'} == bundled_clock['clock'],
             'stereo_bundled_clock_original_changed')
    need(all(finite(support[k]) for k in ('first_consumer_received_at', 'produced_at'))
         and max(d['available_at'], capture_original['first_consumer_received_at'], association['available_at'])
             <= support['first_consumer_received_at'] <= support['produced_at'] <= anchor['available_at'] <= now,
         'stereo_actual_availability_required')
    until = min(d['valid_until']-d['uncertainty_s'], d['observed_at']+p['max_depth_age_s']-d['uncertainty_s'],
        frame['valid_until'], capture_original['event']['payload']['valid_until'], p['valid_until'], association['valid_until'])
    need(now < until and anchor['valid_until'] <= until, 'stereo_original_support_expired')
    return {'valid_until': until, 'observation_root_times': {d['root']: d['observed_at']},
            'sensor_root_bindings': {d['root']: deepcopy(sensor_roots)}}


class LabLiveDeskAnchor:
    def __init__(self, store, *, scope, lab_binding_digest, stereo_policy_ref, resolve_qualification,
                 retain_media, resolve_media, original_retainer=None, clock=utc_now, max_anchors=24,
                 existing_api_source=None, bundled_rgb_source=None, capture_clock_policy=None):
        need(isinstance(store, Store) and all(callable(x) for x in
            (resolve_qualification, retain_media, resolve_media, clock))
            and (original_retainer is None or callable(original_retainer)), 'fixed_stereo_capabilities_required')
        exact(scope, SCOPE_KEYS, 'fixed_stereo_scope_required'); ref(stereo_policy_ref)
        need(type(max_anchors) is int and 1 <= max_anchors <= 24, 'bounded_stereo_anchor_budget_required')
        self.store, self.scope = store, deepcopy(scope)
        self.lab_binding_digest, self.stereo_policy_ref = lab_binding_digest, deepcopy(stereo_policy_ref)
        self.resolve_qualification, self.retain_media, self.resolve_media = resolve_qualification, retain_media, resolve_media
        self.retain_original, self.clock, self.max_anchors = original_retainer, clock, max_anchors
        self.lock, self.closed = threading.Lock(), False
        self.selection_started = False
        self.prepared, self.candidates, self.depth_components = {}, {}, {}
        need(existing_api_source is None or bundled_rgb_source is None, 'separate_stereo_original_family_required')
        self.existing_api_source, self.bundled_rgb_source = existing_api_source, bundled_rgb_source
        self.capture_clock_policy = deepcopy(capture_clock_policy)
        if bundled_rgb_source is not None:
            from lab_bundled_rgb_stereo import BundledRgbStereoSource
            need(type(bundled_rgb_source) is BundledRgbStereoSource and bundled_rgb_source.store is store,
                 'fixed_bundled_rgb_stereo_source_required')
            need(isinstance(capture_clock_policy, dict)
                 and capture_clock_policy.get('original_unit') == 'unix_s'
                 and bundled_rgb_source.capture_clock_policy == capture_clock_policy,
                 'fixed_bundled_rgb_capture_clock_required')
            binding = bundled_rgb_source.binding()
            need(binding['scope'] == scope and binding['lab_binding_digest'] == lab_binding_digest,
                 'bundled_rgb_anchor_binding_changed')
        else:
            need(capture_clock_policy is None, 'unexpected_bundled_rgb_capture_clock')
        if existing_api_source is not None:
            from lab_existing_api_stereo import ExistingApiStereoSource
            need(type(existing_api_source) is ExistingApiStereoSource and existing_api_source.store is store,
                 'fixed_existing_api_stereo_source_required')
            binding = existing_api_source.binding()
            need(binding['scope'] == scope and binding['lab_binding_digest'] == lab_binding_digest,
                 'existing_api_anchor_binding_changed')

    def binding(self):
        value = {'scope': deepcopy(self.scope), 'lab_binding_digest': self.lab_binding_digest,
                'stereo_policy_ref': deepcopy(self.stereo_policy_ref)}
        if self.existing_api_source is not None:
            from lab_existing_api_stereo import FAMILY
            value.update(original_family=FAMILY,
                assessment_policy_sha256=self.existing_api_source.binding()['assessment_policy_sha256'])
        elif self.bundled_rgb_source is not None:
            need(self.capture_clock_policy == self.bundled_rgb_source.capture_clock_policy,
                 'fixed_bundled_rgb_capture_clock_changed')
            value.update(original_family='existing-api-bundled-rgb-1',
                bundle_input_policy_sha256=self.bundled_rgb_source.binding()['bundle_input_policy_sha256'])
        return value

    def quiesce(self):
        self.closed = True
        if self.existing_api_source is not None: self.existing_api_source.quiesce()
        if self.bundled_rgb_source is not None: self.bundled_rgb_source.quiesce()
        busy = self.lock.locked()
        pending = any(e.get('original_attempted') and not e.get('original_retained') for e in self.prepared.values())
        return {'status': 'unconfirmed' if busy or pending else 'quiescent', 'in_flight': busy}

    def finalize(self):
        if not self.closed or not self.lock.acquire(False):
            return {'status': 'unconfirmed', 'buffers_cleared': False}
        try:
            if any(e.get('original_attempted') and not e.get('original_retained') for e in self.prepared.values()):
                return {'status': 'unconfirmed', 'buffers_cleared': False}
            if self.existing_api_source is not None:
                finalized = self.existing_api_source.finalize()
                if finalized['status'] != 'finalized': return finalized
            if self.bundled_rgb_source is not None:
                finalized = self.bundled_rgb_source.finalize()
                if finalized['status'] != 'finalized': return finalized
            self.prepared.clear(); self.candidates.clear(); self.depth_components.clear()
            return {'status': 'finalized', 'buffers_cleared': True}
        finally:
            self.lock.release()

    def install_original_retainer(self, callback):
        need(callable(callback), 'fixed_stereo_original_retainer_required')
        need(self.lock.acquire(False), 'stereo_retainer_installation_busy')
        try:
            need(not self.closed and not self.selection_started and self.retain_original is None,
                 'stereo_retainer_already_installed_or_used')
            self.retain_original = callback
        finally:
            self.lock.release()

    def _current(self, descriptor):
        need(not self.closed, 'stereo_anchor_quiesced')
        need(descriptor.get('scope') == self.scope and descriptor.get('lab_binding_digest') == self.lab_binding_digest,
             'stereo_capture_binding_changed')
        session = self.store.get_session(self.scope['session_id'])
        need(session['state'] == 'recording' and session['generation'] == descriptor['media_generation']
             and session['site_id'] == self.scope['site_id'], 'stereo_incumbent_session_changed')
        with self.store.lock:
            active = self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id DESC LIMIT 1",
                                          (session['id'],)).fetchone()
        need(active is not None and json.loads(active['payload'])['generation'] == descriptor['model_generation'],
             'stereo_incumbent_model_generation_changed')
        if self.existing_api_source is not None: self.existing_api_source._current(descriptor)
        if self.bundled_rgb_source is not None:
            self.bundled_rgb_source._current(descriptor['original']['event'], descriptor['frame_metadata'])

    def _read(self, media_id, generation, role):
        row = self.store.get_media(self.scope['session_id'], media_id, generation=generation)
        need(type(row['size']) is int and 0 < row['size'] <= MEDIA_LIMITS[role], 'stereo_binary_size_bound')
        with self.store.media_path(self.scope['session_id'], media_id).open('rb') as stream:
            raw = stream.read(row['size']+1)
        need(len(raw) == row['size'] and sha(raw) == row['sha256'], 'original_stereo_media_integrity_failed')
        return row, raw

    def _settle_prepared(self, entry, descriptor):
        """Retry the same retained original bytes after an uncertain ACK."""
        if not entry.get('original_retained'):
            entry['original_attempted'] = True
            try:
                ack = self.retain_original(deepcopy(entry['record']))
            except Exception:
                raise RuntimeError('stereo_nose_retention_unconfirmed') from None
            if ack != {'status': 'retained', 'digest': sha(encoded(entry['record']))}:
                raise RuntimeError('durable_stereo_nose_original_required')
            entry['original_retained'] = True
        self._current(descriptor)
        anchor = deepcopy(entry['anchor'])
        anchor['available_at'] = self.clock()
        self._validate(entry['support'], qualification=entry['qualification'], anchor=anchor,
            capture_record=descriptor['original_record'], capture_original=descriptor['original'],
            scope=self.scope, sensor_roots=descriptor['publication']['payload']['sensor_roots'],
            policy_ref=self.stereo_policy_ref, now=anchor['available_at'], resolve_media=self.resolve_media)
        # Retained binary verification may consume the remaining lease. It is
        # not authority to select a point using the clock sampled before I/O.
        self._current(descriptor)
        completed_at = self.clock()
        need(not self.closed, 'stereo_anchor_quiesced')
        need(finite(completed_at) and completed_at >= anchor['available_at']
             and completed_at + _capture_uncertainty(descriptor['original']) < anchor['valid_until'],
             'stereo_original_support_expired')
        anchor['available_at'] = completed_at
        selected = {'status': 'selected', 'anchor': anchor, 'stereo_support': deepcopy(entry['support']),
                    'stereo_qualification': deepcopy(entry['qualification'])}
        entry['selected'] = deepcopy(selected)
        return deepcopy(selected)

    def _validate(self, support, **kwargs):
        if self.bundled_rgb_source is not None:
            from lab_bundled_rgb_stereo import validate_bundled_rgb_support
            return validate_bundled_rgb_support(support,
                capture_clock_policy=self.capture_clock_policy,
                expected_bundle_binding=self.bundled_rgb_source.binding(), **kwargs)
        if self.existing_api_source is None: return validate_stereo_support(support, **kwargs)
        from lab_existing_api_stereo import validate_existing_api_support
        return validate_existing_api_support(support,
            expected_assessment_binding=self.existing_api_source.binding(), **kwargs)

    def select(self, descriptor, received_at):
        if not self.lock.acquire(False):
            return {'status': 'unavailable', 'reason': 'stereo_anchor_busy'}
        try:
            self.selection_started = True
            need(callable(self.retain_original), 'stereo_original_retainer_not_installed')
            self._current(descriptor)
            need(finite(received_at) and 0 <= received_at <= self.clock(), 'actual_stereo_consumer_clock_required')
            key = digest(descriptor['publication_ref'])
            fingerprint = sha(encoded(descriptor))
            if key in self.prepared:
                entry = self.prepared[key]
                need(entry['fingerprint'] == fingerprint, 'stereo_capture_identity_changed')
                if entry.get('selected') is not None:
                    need(self.clock() < entry['selected']['anchor']['valid_until'], 'stereo_original_support_expired')
                    return deepcopy(entry['selected'])
                if 'record' in entry:
                    return self._settle_prepared(entry, descriptor)
            else:
                need(len(self.prepared) < self.max_anchors, 'stereo_anchor_budget_exhausted')
                entry = {'fingerprint': fingerprint, 'received_at': received_at}
                self.prepared[key] = entry
            family_source = self.bundled_rgb_source if self.bundled_rgb_source is not None else self.existing_api_source
            if family_source is not None:
                status = family_source.metadata_status(descriptor, entry['received_at'])
                if status['status'] != 'ready': return status
            q = deepcopy(self.resolve_qualification(deepcopy(descriptor)))
            p, association, registration, projection, depth_clock = _qualification(q, scope=self.scope,
                policy_ref=self.stereo_policy_ref, now=self.clock(),
                clock_unit='unix_s' if self.bundled_rgb_source is not None else 'unix_ns')
            if self.bundled_rgb_source is not None:
                need(q['clock_mapping'] == self.capture_clock_policy, 'fixed_bundled_rgb_capture_clock_changed')
            frame, original = descriptor['frame_metadata'], descriptor['original']
            rgb_id = original['event']['payload']['media_ids']
            need(len(rgb_id) == 1, 'one_original_rgb_media_required')
            existing = None
            if family_source is not None:
                existing = family_source.select(descriptor, entry['received_at'])
                if existing['status'] != 'selected': return existing
                rows = existing['rows']
                # This is receipt of COMPLETE late support, not the earlier
                # capture/model receipt retained verbatim in descriptor.
                entry.setdefault('support_received_at', self.clock())
            else:
                with self.store.lock:
                    pair_ids = self.store.db.execute("SELECT id FROM media WHERE session_id=? AND json_extract(metadata,'$.stream')='paired_preview_original' "
                        "AND json_extract(metadata,'$.rgb_media_id')=? AND json_extract(metadata,'$.frame_id')=? LIMIT 2",
                        (self.scope['session_id'], rgb_id[0], frame['frame_id'])).fetchall()
                need(len(pair_ids) == 1, 'exact_retained_pair_unavailable')
                pair_row, pair_raw = self._read(pair_ids[0]['id'], descriptor['media_generation'], 'paired_original')
                paired = json.loads(pair_raw)
                with self.store.lock:
                    mapped = self.store.db.execute('SELECT id FROM media WHERE session_id=? AND source_key=? LIMIT 2',
                        (self.scope['session_id'], SOURCE_PREFIX+paired['bundle_media_id'])).fetchall()
                need(len(mapped) == 1, 'existing_depth_clock_media_unavailable')
                rows = {'paired_original': (pair_row, pair_raw),
                    'rgb': self._read(rgb_id[0], descriptor['media_generation'], 'rgb'),
                    'bundle': self._read(paired['bundle_media_id'], descriptor['media_generation'], 'bundle'),
                    'mapped_depth': self._read(mapped[0]['id'], descriptor['media_generation'], 'mapped_depth')}
            refs = {role: media_ref(row) for role, (row, _) in rows.items()}
            try:
                retained = self.retain_media([refs[k] for k in sorted(rows)])
            except Exception:
                raise RuntimeError('stereo_binary_retention_unconfirmed') from None
            if not (isinstance(retained, dict) and set(retained) == {'status', 'binding_digest', 'media_refs'}
                 and retained['status'] == 'retained'
                 and sorted(retained['media_refs'], key=canonical) == sorted(refs.values(), key=canonical)):
                raise RuntimeError('durable_stereo_binary_receipt_required')
            self._current(descriptor)
            instance, pixel = _projected_pose(original)
            dm = rows['mapped_depth'][0]['metadata']
            d = _depth_component(frame, dm, rows['mapped_depth'][1], self.scope)
            previous = self.depth_components.get(d['root'])
            if previous is not None:
                need(d == previous, 'physical_depth_original_changed_or_refreshed')
            else:
                need(len(self.depth_components) < self.max_anchors, 'stereo_depth_component_budget_exhausted')
                self.depth_components[d['root']] = deepcopy(d)
            plane, metadata = decode_bundle(rows['bundle'][1])
            point = _nose_point(original, d, plane, metadata, p, registration, projection, pixel)
            room_point = point['room_xyz_m']
            produced = self.clock()
            support = dict(schema='home-desk-stereo-nose-anchor-1', version=1,
                id='stereo-nose:'+sha(encoded([self.scope, descriptor['publication_ref'], d['root'], instance])),
                scope=deepcopy(self.scope), lab_binding_digest=self.lab_binding_digest,
                capture_ref=deepcopy(descriptor['publication_ref']), source_position=deepcopy(descriptor['source_position']),
                source_generation=descriptor['source_generation'], model_generation=descriptor['model_generation'],
                media_generation=descriptor['media_generation'], landmark={'source_event_id': original['event']['id'],
                    'instance_ref': instance, 'joint_id': 0, 'rgb_pixel': pixel, 'surface': 'visible_nose_surface'},
                media_refs=refs, media_binding_digest=retained['binding_digest'], depth=d,
                point=point, stereo_policy_ref=deepcopy(self.stereo_policy_ref),
                association_ref=body_ref(association), projection_ref=body_ref(projection), registration_ref=body_ref(registration),
                first_consumer_received_at=entry.get('support_received_at', entry['received_at']), produced_at=produced)
            if existing is not None and self.bundled_rgb_source is not None:
                need(existing['bundled_originals']['clock_original'] == existing['clock_original']
                     == original['clock_original'], 'bundled_capture_clock_original_changed')
                support.update(schema='home-desk-stereo-nose-anchor-3', version=3,
                    original_family='existing-api-bundled-rgb-1',
                    bundled_originals=deepcopy(existing['bundled_originals']))
            elif existing is not None:
                from lab_existing_api_stereo import FAMILY
                support.update(schema='home-desk-stereo-nose-anchor-2', version=2, original_family=FAMILY,
                    assessment_originals=dict(binding=self.existing_api_source.binding(),
                        start=deepcopy(existing['assessment_start']), terminal=deepcopy(existing['assessment_terminal'])))
            clock = deepcopy(descriptor['publication']['payload']['clock']); clock['status'] = 'qualified'
            until = min(d['valid_until']-d['uncertainty_s'], d['observed_at']+p['max_depth_age_s']-d['uncertainty_s'],
                frame['valid_until'], original['event']['payload']['valid_until'], p['valid_until'], association['valid_until'])
            anchor = dict(schema='home-desk-seated-anchor-2', version=2, id='stereo-anchor:'+sha(encoded(support)),
                scope=deepcopy(self.scope), person_ref=deepcopy(association['person_ref']),
                method_ref=deepcopy(association['method_ref']), source_generation=descriptor['source_generation'],
                source_position=deepcopy(descriptor['source_position']), frame='z_up_metric_floor0', unit='m',
                state='seated_unique', occupant_count=1, point_m=room_point, error_bound_m=point['error_bound_m'],
                clock=clock, observed_at=original['event']['payload']['observed_at'], available_at=produced, valid_until=until)
            self._validate(support, qualification=q, anchor=anchor, capture_record=descriptor['original_record'],
                capture_original=original, scope=self.scope, sensor_roots=descriptor['publication']['payload']['sensor_roots'],
                policy_ref=self.stereo_policy_ref, now=produced, resolve_media=self.resolve_media)
            record = dict(schema='home-original-lab-stereo-nose-1', version=1, scope=deepcopy(self.scope),
                lab_binding_digest=self.lab_binding_digest, stereo_policy_ref=deepcopy(self.stereo_policy_ref),
                support=deepcopy(support), qualification=deepcopy(q), first_consumer_received_at=support['first_consumer_received_at'], produced_at=produced)
            if existing is not None:
                version = 3 if self.bundled_rgb_source is not None else 2
                record.update(schema='home-original-lab-stereo-nose-' + str(version), version=version)
            entry.update(record=deepcopy(record), support=deepcopy(support), qualification=deepcopy(q), anchor=deepcopy(anchor))
            return self._settle_prepared(entry, descriptor)
        except (ValueError, KeyError, TypeError, IndexError) as error:
            reason = str(error) if isinstance(error, DeskAnchorRefusal) else 'stereo_original_unavailable'
            return {'status': 'unavailable', 'reason': reason}
        finally:
            self.lock.release()

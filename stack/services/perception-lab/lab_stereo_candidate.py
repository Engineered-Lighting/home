"""Pure visible-nose measurement BEFORE person/association qualification.

This consumes the existing independently assessed STATIC metric/error/sampling
policy and qualified camera, registration and clock originals. It does not solve
unknown-estimator assessment (N21). Four snapshots must come from the fixed
retained-media capability; this pure function does not attest their durability.
It has no Store, camera, model, clock reader, callback or publication operation.
"""
from copy import deepcopy
from decimal import Decimal

from journal import digest, finite
from live_capture import _original_digest, PARTS
from paired_preview_client import encoded, sha
from lab_live_desk_anchor import (MEDIA_ROLES, SCOPE_KEYS, DeskAnchorRefusal, exact, need, media_ref,
    _static_stereo_qualification, _checked_media, _projected_pose, _check_mapped_depth,
    _depth_component, _nose_point)

AUTHORITY = {'qualified': False, 'operational_geometry': None, 'geometry_authority': False,
             'identity_authority': False, 'absence_authority': False,
             'protection_authority': False, 'beam_authority': False}


def measure_visible_nose_candidate(descriptor, *, static_originals, policy_ref, retained_media, now):
    """Return a private candidate, or raise bounded DeskAnchorRefusal.

    No association, seated-state, person or subject original is accepted. The
    caller must use the separate full validator before any operational selection.
    Original identity/times/leases remain unchanged, including on repeated reads.
    """
    try:
        return _measure_visible_nose_candidate(descriptor, static_originals=static_originals, policy_ref=policy_ref,
                                               retained_media=retained_media, now=now)
    except (KeyError, TypeError, IndexError, OverflowError):
        raise DeskAnchorRefusal('stereo_candidate_original_unavailable') from None


def _measure_visible_nose_candidate(descriptor, *, static_originals, policy_ref, retained_media, now):
    need(finite(now) and now >= 0, 'actual_stereo_candidate_clock_required')
    exact(descriptor, {'schema','version','scope','lab_binding_digest','publication','publication_ref',
        'original','original_record','frame_metadata','source_position','source_generation',
        'model_generation','media_generation'}, 'original_candidate_capture_descriptor_required')
    need(isinstance(descriptor, dict) and descriptor.get('schema') == 'home-lab-admitted-capture-1'
         and type(descriptor.get('version')) is int and descriptor['version'] == 1,
         'original_candidate_capture_descriptor_required')
    scope = descriptor['scope']; exact(scope, SCOPE_KEYS, 'fixed_stereo_scope_required')
    p, registration, projection, depth_clock = _static_stereo_qualification(static_originals,
        scope=scope, policy_ref=policy_ref, now=now)
    original, record, frame = descriptor['original'], descriptor['original_record'], descriptor['frame_metadata']
    event = original['event']; payload = event['payload']
    expected_pos = {'capture_epoch': frame['capture_epoch'], 'capture_seq': frame['sequence'], 'revision': 0}
    exact(record, {'ref','capture_epoch','capture_seq','observation_root_times','sensor_root_bindings'},
          'candidate_original_capture_mismatch')
    need(original.get('schema') == 'home-original-lab-observation-1' and original.get('version') == 1
         and original['frame_metadata'] == frame and record['ref']['digest'] == _original_digest(original)
         and event['session_id'] == scope['session_id'] and payload['site_id'] == scope['site_id']
         and payload['camera_id'] == frame['camera_id'] == registration['camera_id'] == projection['camera_id']
         and payload['generation'] == descriptor['model_generation']
         and payload['frame_id'] == frame['frame_id'] and payload['capture_epoch'] == frame['capture_epoch']
         and payload['observed_at'] == frame['observed_at']
         and record['capture_epoch'] == expected_pos['capture_epoch']
         and record['capture_seq'] == expected_pos['capture_seq']
         and descriptor['source_position'] == expected_pos
         and record['ref']['observed_at'] == payload['observed_at'], 'candidate_original_capture_mismatch')
    publication, pref = descriptor['publication'], descriptor['publication_ref']
    exact(pref, {'stream_epoch','cursor','producer_id','id','payload_digest','component_id'},
          'candidate_original_publication_mismatch')
    body = publication['payload']
    need(all(publication[k] == pref[k] for k in ('stream_epoch','cursor','producer_id','id','payload_digest'))
         and pref['component_id'] is None and pref['payload_digest'] == digest(body)
         and all(body[k] == scope[k] for k in SCOPE_KEYS)
         and isinstance(body['source_id'],str) and bool(body['source_id'])
         and body['head']['key']['source_id'] == body['source_id']
         and body['method_ref'] == original['method_ref']
         and body.get('model_ref', original['model_ref']) == original['model_ref']
         and body['head']['position'] == expected_pos and body['source_generation'] == descriptor['source_generation']
         and body['observed_at'] == payload['observed_at'] and body['available_at'] == payload['available_at']
         and record['ref'] in body['original_receipt_refs']
         and body['observation_root_times'] == record['observation_root_times']
         and body['sensor_root_bindings'] == record['sensor_root_bindings'], 'candidate_original_publication_mismatch')
    # The canonical first capture publication is an unavailable presence FACT.
    # It carries no geometry or registration. Metrology comes only from the
    # separately protected static policy pin, never from an unrelated fact.
    association=body['association']
    need(body['version']==1 and body['status']=='unobservable' and body['person_ref'] is None
         and body['subject_ref'] is None and association['person_status']=='not_applicable'
         and association['subject_status']=='not_applicable' and association['candidate_person_refs']==[]
         and association['candidate_subject_refs']==[], 'candidate_capture_metadata_form_required')
    if publication['payload_type']=='home-live-fact-1' and body['schema']=='home-live-fact-1':
        need(body['layer']=='observation' and body['kind']=='person_presence' and body['value'] is None
             and body['reason']=='missing_applicability' and body['assertion_basis']=='inferred'
             and body['probability'] is None and body['conflicting_refs']==[] and 'registration' not in body
             and body['head']['key']['predicate']=='person_presence', 'candidate_capture_metadata_form_required')
    elif publication['payload_type']=='home-live-geometry-1' and body['schema']=='home-live-geometry-1':
        need(body['reason']=='missing_measurement_bound' and body['head']['key']['predicate']=='geometry_bundle'
             and set(body['parts'])==set(PARTS) and all(part['status']=='unobservable'
                 and part['reason']=='missing_measurement_bound' and 'shape' not in part
                 and part['support_refs']==[record['ref']] and part['observed_at']==body['observed_at']
                 and part['valid_until']==body['valid_until'] for part in body['parts'].values()),
             'candidate_capture_metadata_form_required')
        need(body['registration']['registration_ref']==registration['id']
             and body['registration']['registration_digest']==digest(registration),
             'candidate_original_camera_binding_mismatch')
    else:
        raise DeskAnchorRefusal('candidate_capture_metadata_form_required')
    need(len(body['sensor_roots']) == 1
         and set(record['observation_root_times']) == set(record['sensor_root_bindings'])
         and set().union(*(set(roots) for roots in record['sensor_root_bindings'].values())) == set(body['sensor_roots']),
         'candidate_original_camera_binding_mismatch')
    receipt = frame['source_timing_receipt']
    need(receipt['session'] == frame['capture_epoch'] and receipt['sequence'] == frame['sequence']
         and frame['timing_generation'] == receipt['timing_generation']
         and frame['frame_id'] == f"{receipt['session']}-{receipt['timing_generation']}-{receipt['sequence']}"
         and type(receipt['observed_at_unix_ns']) is int
         and float(Decimal(receipt['observed_at_unix_ns'])/Decimal(1000000000)) == frame['observed_at']
         and str(receipt['observed_at_unix_ns']) == body['clock']['original_value']
         and frame['available_at'] <= payload['available_at'] <= original['first_consumer_received_at'] <= now
         and record['ref']['available_at'] == payload['available_at']
         and all(finite(v) and v <= now for v in (frame['available_at'], payload['available_at'],
             record['ref']['available_at'], original['first_consumer_received_at'], publication['committed_at'])),
         'candidate_original_clock_or_availability_mismatch')
    exact(retained_media, MEDIA_ROLES, 'exact_stereo_binary_roles_required')
    refs = {role: media_ref(found['record']) for role, found in retained_media.items()}
    by_id = {found['record']['id']: found for found in retained_media.values()}
    need(len(by_id) == 4, 'distinct_stereo_binary_roles_required')
    def resolve(requested):
        found = by_id[requested['id']]
        need(media_ref(found['record']) == requested, 'retained_stereo_binary_mismatch')
        return deepcopy(found)
    skeleton = {'scope': deepcopy(scope), 'model_generation': descriptor['model_generation'],
                'media_generation': descriptor['media_generation'], 'media_refs': refs}
    media, paired, plane, metadata = _checked_media(skeleton, original, resolve)
    dm = media['mapped_depth'][0]['metadata']
    need(projection['id'] == p['calibration_content_id'] == dm['calibration_content_id'],
         'stereo_calibration_content_changed')
    from bundle_ingest import calibration_content_id
    need(dm['calibration_content_id'] == calibration_content_id(paired['calibration']), 'stereo_calibration_content_changed')
    _check_mapped_depth(frame, dm, metadata, depth_clock, media['rgb'][0]['id'])
    depth = _depth_component(frame, dm, media['mapped_depth'][1], scope)
    observed = payload['observed_at']
    need(depth['observed_at'] <= observed and depth['observed_at'] <= depth['available_at'] <= now
         and depth['available_at'] < depth['valid_until'], 'original_depth_identity_or_first_time_changed')
    skew = observed - depth['observed_at'] + frame['uncertainty_seconds'] + depth['uncertainty_s']
    need(skew <= p['max_abs_skew_s'], 'stereo_depth_rgb_temporal_applicability_exceeded')
    instance, pixel = _projected_pose(original)
    point = _nose_point(original, depth, plane, metadata, p, registration, projection, pixel)
    need(point['spread_m'] <= p['max_spread_m'], 'visible_nose_depth_unavailable')
    until = min(depth['valid_until'] - depth['uncertainty_s'],
        depth['observed_at'] + p['max_depth_age_s'] - depth['uncertainty_s'],
        frame['valid_until'], payload['valid_until'], p['valid_until'])
    need(original['first_consumer_received_at'] <= now and now + frame['uncertainty_seconds'] < until,
         'stereo_original_support_expired')
    return dict(schema='home-desk-visible-nose-candidate-1', version=1, status='candidate',
        scope=deepcopy(scope), lab_binding_digest=descriptor['lab_binding_digest'],
        capture_ref=deepcopy(pref), source_position=deepcopy(expected_pos),
        source_generation=descriptor['source_generation'], model_generation=descriptor['model_generation'],
        media_generation=descriptor['media_generation'], person_ref=None, subject_ref=None,
        landmark={'source_event_id': event['id'], 'instance_ref': instance, 'joint_id': 0,
                  'rgb_pixel': pixel, 'surface': 'visible_nose_surface_candidate'},
        media_refs=refs, media_binding_digest=sha(encoded({'scope': scope,
            'lab_binding_digest': descriptor['lab_binding_digest'], 'media_generation': descriptor['media_generation'],
            'camera_id': frame['camera_id']})), depth=depth, point=point, observed_at=observed,
        first_consumer_received_at=original['first_consumer_received_at'], produced_at=now, valid_until=until,
        static_qualification_ref=deepcopy(policy_ref),
        missing_qualification=['exact_capture_person_association', 'assisted_seated_episode',
                               'person_specific_work_and_protection_applicability'], **deepcopy(AUTHORITY))

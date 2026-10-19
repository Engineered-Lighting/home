"""Bounded, inert same-NPZ metric preparation for Apartment diagnostic viewing.

The incumbent owner supplies immutable original bytes and server-held pins. This
module owns no camera, fetch, thread, Store, media route, archive or model. The
returned PNG must be registered in that owner's existing RAM budget BEFORE it
is offered to a model. An unrelated/latest JPEG can never borrow this depth.

RAM provenance is deliberately distinct from retained assessment collections.
Nominal EEPROM projection is explicit and unqualified; it grants no measured
error bound, exposure synchronization, body volume, Follow or device authority.
"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import hashlib
import io
import json
import math
import re

from assessment_existing_api import _json, _receipt
from bundle_ingest import calibration_content_id
from bundle_rgb_input import anchor_metadata, decode_bundle, mapped_timing, receipt_upper, warn

SCHEMA = 'home-apartment-viewing-metric-ram-1'
ATTEMPT_SCHEMA = 'home-apartment-viewing-metric-attempt-1'
LIMITS = {'npz': 4 * 1024**2, 'calibration': 24576, 'anchor_a': 256 * 1024, 'anchor_b': 256 * 1024}
MAX_RETAINED_BYTES = 8 * 1024**2
HEX = re.compile('[a-f0-9]{64}')
NO_AUTHORITY = dict(operational_geometry=None, geometry_authority=False, identity_authority=False,
    engagement_authority=False, absence_authority=False, protection_authority=False, beam_authority=False,
    physical_permission=False, follow_allowed=False)

def need(ok, reason):
    if not ok: raise ValueError(reason)

def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def finite(value): return type(value) in (int, float) and math.isfinite(value)
def text(value): return isinstance(value, str) and 0 < len(value) <= 256
def digest(value): return sha(encoded(value))
def pin(value): return isinstance(value, str) and HEX.fullmatch(value) is not None


def _binding(value):
    need(isinstance(value, dict) and set(value) == {'session', 'arm', 'model_generation', 'media_generation', 'lab_profile','sampling_hz'},
         'metric_fixed_binding_required')
    session = value['session']
    need(isinstance(session, dict) and set(session) == {'id', 'site_id', 'camera_id'}
        and text(session['id']) and session['site_id'] == 'victoria' and session['camera_id'] == 'den'
        and value['arm'] in ('a', 'b') and type(value['model_generation']) is int and value['model_generation'] > 0
        and type(value['media_generation']) is int and value['media_generation'] >= 0,
        'metric_fixed_binding_required')
    need(value['sampling_hz'] is None or finite(value['sampling_hz']) and 0<value['sampling_hz']<=30,
        'metric_owner_sampling_rate_required')
    profile = value['lab_profile']
    need(isinstance(profile, dict) and all(text(profile.get(k)) for k in
        ('profile_id', 'engine_epoch', 'model_version')) and pin(profile.get('artifact_sha256'))
        and len(encoded(value)) <= 32768, 'metric_ready_profile_required')


def _checked(originals, headers, proof, binding, pins, available, started, now, warnings=None, expired='proof_expired'):
    _binding(binding)
    need(isinstance(originals, dict) and set(originals) == set(LIMITS)
        and isinstance(headers, dict) and set(headers) == {'a', 'b'}
        and isinstance(pins, dict) and set(pins) == {'originals', 'anchor_headers', 'receiver_proof',
            'capture_epoch', 'timing_generation', 'calibration_content_id'}
        and set(pins['originals']) == set(LIMITS) and set(pins['anchor_headers']) == {'a', 'b'}
        and text(pins['capture_epoch']) and type(pins['timing_generation']) is int and pins['timing_generation'] >= 0
        and pin(pins['calibration_content_id']), 'metric_original_pins_required')
    for role, maximum in LIMITS.items():
        raw, ref = originals[role], pins['originals'][role]
        need(isinstance(raw, bytes) and 0 < len(raw) <= maximum and isinstance(ref, dict)
            and set(ref) == {'size', 'sha256'} and type(ref['size']) is int and ref['size'] == len(raw)
            and pin(ref['sha256']) and sha(raw) == ref['sha256'], 'metric_original_bytes_changed')
    for role, header in headers.items():
        need(isinstance(header, str) and 0 < len(header) <= 8192
            and pin(pins['anchor_headers'][role]) and sha(header.encode()) == pins['anchor_headers'][role],
            'metric_original_header_changed')
    need(len(encoded(proof)) <= 16384 and pin(pins['receiver_proof']) and digest(proof) == pins['receiver_proof'],
        'metric_receiver_proof_changed')
    need(all(finite(t) and t >= 0 for t in (available, started, now)) and started <= available <= now,
        'metric_actual_owner_clock_required')
    ra = _receipt(originals['anchor_a'], headers['a']); rb = _receipt(originals['anchor_b'], headers['b'])
    # The installed bundle reader validates the complete original host proof
    # against this original B receipt, including its clock identity, bracket
    # order, uncertainty, boot continuity and no-suspend evidence.
    completion_upper = receipt_upper(proof, rb)
    # A delayed decode/response cannot retrospectively reuse an expired proof.
    evidence = proof['evidence']
    proof_deadline = (Decimal(evidence['checked_unix_ns']) + evidence['valid_until_monotonic_ns']
        - evidence['checked_monotonic_ns']) / 10**9 - Decimal(str(evidence['uncertainty_seconds']))
    # Strict unless the owner passes a list (development preview timing only).
    found = None if warnings is None else []
    warn(found, Decimal(str(now)) < proof_deadline, 'metric_receiver_proof_expired', expired, float(proof_deadline))
    radius = int((Decimal(str(evidence['uncertainty_seconds']))*10**9).to_integral_value(rounding=ROUND_CEILING))
    completion_lower = evidence['checked_unix_ns'] + proof['after']['before_ns'] - evidence['checked_monotonic_ns'] - radius
    need(completion_lower / 1e9 <= available <= completion_upper / 1e9, 'metric_owner_receipt_outside_proof')
    need(all(r['session'] == pins['capture_epoch'] and r['timing_generation'] == pins['timing_generation']
        for r in (ra, rb)), 'metric_camera_epoch_changed')
    a = anchor_metadata(ra, site_id='victoria', camera_id='den', available_at=available, started_at=started, warnings=found)
    b = anchor_metadata(rb, site_id='victoria', camera_id='den', available_at=available, started_at=started, warnings=found)
    from store import LabError
    try: depth, nv12, meta = decode_bundle(originals['npz'])
    except LabError as error: raise ValueError('metric_bounded_npz_refused') from error
    cal = _json(originals['calibration'])
    need(isinstance(cal, dict) and cal.get('session') == meta['session'] == pins['capture_epoch']
        and cal.get('depth_alignment') == 'rectified_left' and cal.get('depth_units') == 'millimetres'
        and calibration_content_id(cal) == pins['calibration_content_id'], 'metric_original_calibration_changed')
    timing = mapped_timing({'metadata': a}, {'metadata': b}, meta, proof, warnings=found)
    warn(found, now < timing['valid_until'], 'metric_rgb_receipt_expired', expired, timing['valid_until'])
    if found:
        warnings.extend(dict(item, at=now) for item in found)
    return depth, nv12, meta, cal, timing, float(proof_deadline)


def _png(nv12):
    from PIL import Image
    from room_mapper import nv12_to_rgb
    output = io.BytesIO()
    Image.fromarray(nv12_to_rgb(nv12)).save(output, format='PNG')
    raw = output.getvalue()
    need(0 < len(raw) <= 1024**2, 'metric_derived_png_limit')
    return raw


@dataclass(frozen=True)
class PreparedMetricFrame:
    """One immutable lease. The existing owner must count and retire its bytes.

    JSON properties return defensive copies; numerical working arrays are never
    retained. Python objects/pins are trusted server inputs, not authentication.
    An owner must not queue leases or retain a second set outside its RAM cap.
    """
    _originals: tuple
    _headers_json: bytes
    _proof_json: bytes
    _binding_json: bytes
    _pins_json: bytes
    _descriptor_json: bytes
    _metadata_json: bytes
    image_bytes: bytes
    retained_bytes: int
    available_at: float
    started_at: float

    @property
    def descriptor(self): return _json(self._descriptor_json)
    @property
    def image_metadata(self): return _json(self._metadata_json)
    @property
    def binding(self): return _json(self._binding_json)
    @property
    def retained_byte_allocations(self):
        """Actual immutable byte references for the owner's union accounting.

        Returning a tuple allocates no additional image/original byte buffers.
        These are private server-local originals, never a public JSON field.
        """
        return tuple(raw for _,raw in self._originals)+(self._headers_json,self._proof_json,self._binding_json,
            self._pins_json,self._descriptor_json,self._metadata_json,self.image_bytes)


def prepare_metric_frame(*, originals, anchor_headers, receiver_proof, binding, pins,
                         available_at, started_at, now_s, warnings=None):
    """Validate actual originals and derive the ONLY model image for this lease.

    ``pins`` must come from the incumbent owner, never a model/browser. All
    originals remain in its bounded RAM; this function writes no private media.
    """
    _, nv12, meta, _, timing, deadline = _checked(originals, anchor_headers, receiver_proof, binding,
        pins, available_at, started_at, now_s, warnings)
    image = _png(nv12)
    rgb = meta['frames']['rgb']
    metadata = dict(site_id='victoria', camera_id='den', capture_epoch=meta['session'],
        frame_id=f"{meta['session']}-bundle-rgb-{rgb['sequence']}", stream='rgb', format='png',
        width=640, height=360, sequence=rgb['sequence'], timing_generation=pins['timing_generation'],
        original_family=SCHEMA, source_npz_sha256=pins['originals']['npz']['sha256'],
        calibration_content_id=pins['calibration_content_id'], nv12_sha256=sha(nv12.tobytes(order='C')),
        observed_at=timing['observed_at'], available_at=available_at,
        valid_until=min(timing['valid_until'], deadline), uncertainty_seconds=timing['uncertainty_seconds'],
        timestamp_semantics='same_stream_ordinal_qualified_receipt_bound',
        clock_mapping_status='verified', clock_mapping_id='apartment-bundle-clock-1:'+digest({
            'originals':pins['originals'],'headers':pins['anchor_headers'],'receiver_proof':pins['receiver_proof'],
            'capture_epoch':pins['capture_epoch'],'timing_generation':pins['timing_generation'],'timing':timing}),
        geometric_validity='unvalidated')
    descriptor = dict(schema=SCHEMA, version=1, binding_digest=digest(binding),
        originals=deepcopy(pins['originals']), anchor_headers=deepcopy(pins['anchor_headers']),
        receiver_proof_sha256=pins['receiver_proof'], capture_epoch=meta['session'],
        calibration_content_id=pins['calibration_content_id'], source_metadata_sha256=digest(meta),
        image_sha256=sha(image), image_size=len(image), timing=timing,
        retirement_deadline=min(timing['valid_until'], deadline),
        depth_timing={'status': 'unassessed', 'mapping': None, 'exposure_error_bound_s': None},
        storage='incumbent_owner_bounded_ram', durable_media=False, **NO_AUTHORITY)
    jsons = tuple(encoded(x) for x in (anchor_headers, receiver_proof, binding, pins, descriptor, metadata))
    raw_tuple = tuple((key, originals[key]) for key in LIMITS)
    # Immutable byte aliases count once; equal but distinct allocations do not.
    retained = {id(raw): len(raw) for _, raw in raw_tuple}
    retained.update({id(raw): len(raw) for raw in (*jsons, image)})
    count = sum(retained.values())
    need(count <= MAX_RETAINED_BYTES, 'metric_owner_ram_budget_exceeded')
    return PreparedMetricFrame(raw_tuple, *jsons, image, count, available_at, started_at)


def lift_supported_joints(lease, *, event, image_record, now_s, projection='diagnostic_nominal_eeprom', warnings=None):
    """Reverify originals and lift actual supported pixels into camera metres.

    No checked-reprojection claim is accepted here. A future qualified adapter
    needs a separately verified receipt; supplying a string cannot grant it.
    """
    need(type(lease) is PreparedMetricFrame, 'metric_prepared_same_npz_lease_required')
    need(projection == 'diagnostic_nominal_eeprom', 'metric_explicit_nominal_projection_required')
    originals = dict(lease._originals); headers = _json(lease._headers_json)
    proof = _json(lease._proof_json); binding = lease.binding; pins = _json(lease._pins_json)
    depth, nv12, meta, cal, timing, _ = _checked(originals, headers, proof, binding, pins,
        lease.available_at, lease.started_at, now_s, warnings, 'finalize_expired')
    descriptor = lease.descriptor; image_meta = lease.image_metadata
    need(descriptor['schema'] == SCHEMA and descriptor['binding_digest'] == digest(binding)
        and descriptor['source_metadata_sha256'] == digest(meta)
        and descriptor['originals'] == pins['originals'] and descriptor['timing'] == timing
        and sha(_png(nv12)) == descriptor['image_sha256'] == sha(lease.image_bytes)
        and descriptor['image_size'] == len(lease.image_bytes), 'metric_prepared_original_changed')
    need(isinstance(event, dict) and len(encoded(event)) <= 2 * 1024**2 and type(event.get('id')) is int
        and event['id'] > 0 and event.get('kind') == 'perception.output'
        and event.get('session_id') == binding['session']['id'], 'metric_original_event_required')
    p = event['payload']; f = image_record
    from runtime import arm_valid_until
    # Incumbent per-arm sampling may extend MODEL evidence by a bounded wait.
    # Preserve that separate lease; never copy it onto the original PNG.
    model_valid_until = arm_valid_until(image_meta,binding['sampling_hz'])
    need(isinstance(f, dict) and text(f.get('id')) and f.get('session_id') == binding['session']['id']
        and f.get('generation') == binding['media_generation'] and f.get('mime_type') == 'image/png'
        and f.get('sha256') == descriptor['image_sha256'] and f.get('size') == len(lease.image_bytes)
        and f.get('metadata') == image_meta and p.get('media_ids') == [f['id']],
        'metric_exact_registered_png_required')
    need(p.get('site_id') == 'victoria' and p.get('camera_id') == 'den' and p.get('arm') == binding['arm']
        and p.get('generation') == binding['model_generation'] and p.get('status') == 'completed'
        and p.get('frame_id') == image_meta['frame_id'] and p.get('capture_epoch') == image_meta['capture_epoch']
        and p.get('observed_at') == image_meta['observed_at'] and p.get('valid_until') == model_valid_until
        and finite(p.get('available_at')) and image_meta['available_at'] <= p['available_at'] <= now_s,
        'metric_model_frame_binding_changed')
    profile = binding['lab_profile']
    need(all(p.get(key) == profile[profile_key] for key, profile_key in
        (('run_id', 'profile_id'), ('engine_epoch', 'engine_epoch'), ('model_version', 'model_version'),
         ('artifact_sha256', 'artifact_sha256'))), 'metric_model_profile_changed')
    result = p['result']; snapshot = p.get('arm_snapshot', {}).get('frame', {})
    need(result.get('schema_version') == 1 and result.get('coordinate_space') == 'source_pixels'
        and result.get('frame_id') == image_meta['frame_id'] and snapshot.get('id') == image_meta['frame_id']
        and snapshot.get('media_id') == f['id'] and snapshot.get('generation') == f['generation']
        and snapshot.get('width') == 640 and snapshot.get('height') == 360,
        'metric_original_model_pixels_required')
    poses = result.get('pose_instances', [])
    need(isinstance(poses, list) and len(poses) <= 2, 'metric_instance_budget_exceeded')
    for pose in poses:
        raw = pose.get('joints', [])
        need(isinstance(raw, list) and len(raw) <= 133 and pose.get('layout') in ('coco17', 'coco_wholebody133'),
            'metric_supported_layout_required')
        ids = [j.get('joint_id') for j in raw[:17] if isinstance(j, dict)]
        need(len(ids) == len(set(ids)) and all(type(n) is int and 0 <= n < 17 for n in ids),
            'metric_duplicate_or_invalid_joint')
    from persisted_evidence import observation_record
    from jev_evidence import project
    record = observation_record(event['id'], 'perception.output', p, binding['session'])
    need(record is not None, 'metric_original_observation_refused')
    normalized = project([record])[0]['pose_instances']
    pixels = [j['pixel'] for pose in normalized for j in pose['joints'] if j['status'] == 'supported' and j.get('pixel') is not None]
    lifter = None
    if pixels:
        from depth_association import camera_model, eeprom_rgb_from_rectified_left
        reference = dict(id=pins['calibration_content_id'], mount_revision='diagnostic_nominal_eeprom',
            reference_intrinsics=dict(rectified_left=meta['frames']['depth']['intrinsics'], rgb=meta['frames']['rgb']['intrinsics']),
            rgb_from_rectified_left=eeprom_rgb_from_rectified_left(cal['eeprom']))
        model = camera_model(meta, reference, measuring_reprojection=True)
        from head3d import DepthLifter
        roi = (min(v[0] for v in pixels)-4, min(v[1] for v in pixels)-4,
               max(v[0] for v in pixels)+4, max(v[1] for v in pixels)+4)
        lifter = DepthLifter(depth, model, rgb_roi=roi)
    instances = []
    for pose in normalized:
        observed = {j['joint_id']: j for j in pose['joints']}; joints = []
        for index in range(17):
            source = observed.get(index)
            joint = dict(joint_id=index, status='unavailable', pixel=None, camera_xyz_m=None,
                depth_support=None, depth_spread_m=None, error_bound_m=None, reason='pixel_not_supported')
            if source is not None and source['status'] == 'supported' and source.get('pixel') is not None:
                sample = lifter.lift(*source['pixel'], radius_px=4, min_support=3)
                joint.update(pixel=deepcopy(source['pixel']), depth_support=sample.get('support', 0),
                    depth_spread_m=sample.get('spread_m'), reason=sample.get('reason', 'independent_error_unavailable'))
                if sample['status'] == 'measured':
                    joint.update(status='candidate', camera_xyz_m=sample['xyz'])
            joints.append(joint)
        instances.append(dict(instance_ref=pose.get('instance_ref'), joints=joints))
    return dict(schema=ATTEMPT_SCHEMA, version=1, source_event_id=event['id'], original_event=deepcopy(event),
        original_event_sha256=digest(event), original_frame_record=deepcopy(f), binding_digest=digest(binding),
        coordinate_frame='rectified_left', unit='m', candidate_only=True, owner_local_only=True,
        status='candidate' if any(j['status'] == 'candidate' for i in instances for j in i['joints']) else 'unavailable',
        source_times={k: p[k] for k in ('observed_at', 'available_at', 'valid_until')},
        media_records={'bundle': {'metadata': {'calibration_content_id': pins['calibration_content_id']}}},
        ram_originals=deepcopy(descriptor), instances=instances, person_ref=None, subject_ref=None,
        error_bound_m=None, candidate_produced_at=now_s,
        projection=dict(status='unqualified_nominal_eeprom', independent_reprojection_receipt=None),
        depth_clock={'status': 'unassessed', 'mapping': None, 'exposure_error_bound_s': None}, **NO_AUTHORITY)

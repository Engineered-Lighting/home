"""Opt-in capture qualification without camera, recording or transport ownership.

The acquisition owner supplies authenticated original bytes. One fixed publisher
and commissioned policy are composed locally, never selected by an HTTP caller.
Successful capture admits no person, task or anatomical estimate. Initial pairing
is causal lookback: depth is at or before the exact RGB exposure, never newer.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
import math
import threading

from bundle_ingest import calibration_content_id
from capture_timing import commissioning, map_depth_time
from depth_association import camera_model
from frame_ring import preview_frame
from live_publication import PublicationRefusal
from geometry import deproject, transform_point
from recorder import validate_npz
from spatial_validation import check_mount

# Use the existing wire canonicalizer; the historical calibration-content ID
# deliberately retains bundle_ingest's original hash dialect.
from journal import JournalError, canonical, digest, finite, identifier
from live_ingress_validation import validate_clock, validate_config
from schema_registry import SchemaRegistry, LIVE_PROFILE

PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection', 'body_forward', 'head_forward')
POLICY_KEYS = {'schema', 'version', 'camera_id', 'clock_binding', 'depth_clock', 'projection', 'mount_signature',
               'registration', 'max_skew_s', 'intrinsics_tolerance_px', 'receipt_store_id'}
REGISTRATION_KEYS = {'schema', 'version', 'id', 'site_id', 'room_id', 'camera_id', 'frame', 'unit', 'geometry_revision',
                     'geometry_digest', 'mount_revision', 'source_frame', 'room_from_rectified_left', 'coverage_refs',
                     'transform_ref', 'validation_ref', 'error_bound_m'}


class CaptureRefusal(ValueError):
    def __init__(self, reason, barrier_reason='source_unavailable'):
        super().__init__(reason)
        self.reason, self.barrier_reason = reason, barrier_reason


def _require(condition, reason, barrier_reason='source_unavailable'):
    if not condition:
        raise CaptureRefusal(reason, barrier_reason)


def _object_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate metadata key')
        value[key] = item
    return value


def _original_digest(value, limit=65536):
    # Owner receipts retain native 64-bit nanoseconds. They are not wire JSON:
    # only the resulting opaque SHA and string-valued clock cross that boundary.
    hashed, size = hashlib.sha256(), 0
    for part in json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
        encoded = part.encode()
        size += len(encoded)
        if size > limit:
            raise ValueError('original receipt metadata exceeds bound')
        hashed.update(encoded)
    return hashed.hexdigest()


class LiveCaptureAdapter:
    """One bounded development-session owner of original capture receipts.

    Fresh composition starts at the manifest's initial source generation. An
    existing journal must be explicitly resynchronized by later composition;
    this adapter never guesses a head, rotates epochs or retries a failed CAS.
    No pending delivery is replaced by a newer capture. Capacity is refusal,
    not silent eviction of original identities or retry records.
    """

    def __init__(self, live_config, *, source_id, revision, policy, method_ref, clock, publish,
                 control_clock=None, max_captures=24, clock_authority=None, authority_generation=None):
        self.config = validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
        self.scope = self.config['manifest']['identity']
        self.source_id, self.revision = identifier(source_id, 'source_id'), identifier(revision, 'revision')
        self.source = self.config['manifest']['sources'][source_id]
        self.policy, self.method = deepcopy(policy), deepcopy(method_ref)
        if (not callable(clock) or not callable(publish) or control_clock is not None and not callable(control_clock)
                or authority_generation is not None and not callable(authority_generation)):
            raise ValueError('fixed local clock, publisher and optional control authority required')
        if type(max_captures) is not int or not 1 <= max_captures <= 24:
            raise ValueError('capture session capacity must be between one and 24')
        self.clock, self.publish, self.control_clock = clock, publish, control_clock
        self.clock_authority = clock_authority
        self.read_authority_generation = authority_generation
        self.max_captures = max_captures
        self.lock = threading.Lock()
        self.captures, self.originals, self.resolved, self.components = {}, {}, {}, {}
        self.pending = self.last_ref = self.barrier = None
        self.pending_barrier = None
        self.withdrawn = False
        self.closed = False
        self.generation, self.authority_generation = self.source['initial_generation'], 0
        self.highest_sequence = -1
        self._validate_policy()

    def _validate_policy(self):
        p, s = self.policy, self.source
        try:
            if (not isinstance(p, dict) or set(p) != POLICY_KEYS
                    or (p['schema'], p['version']) not in {('home-live-capture-policy-1', 1), ('home-live-capture-policy-2', 2)}
                    or type(p['version']) is not int or len(canonical(p).encode()) > 65536
                    or self.method['digest'] != digest(p) or self.method not in s['method_refs']
                    or self.method not in s['association_method_refs'] or len(s['sensor_roots']) != 1
                    or s['capture_clock']['original_unit'] != 'unix_ns'
                    or p['receipt_store_id'] not in s['receipt_store_ids']
                    or not {'rgb', 'depth'} <= set(s['receipt_component_ids'])):
                raise ValueError('exact pinned capture policy and physical-source bindings required')
            identifier(p['camera_id'], 'camera_id')
            clock_keys = ({'timing_generation', 'validation_id', 'host_boot_id', 'host_clock_validation_id'} if p['version'] == 1
                          else {'timing_generation', 'host_boot_id', 'validation_mode', 'authority_ref'})
            if (set(p['clock_binding']) != clock_keys
                    or s['capture_clock']['mapping_digest'] != digest(p['clock_binding'])
                    or type(p['clock_binding']['timing_generation']) is not int or p['clock_binding']['timing_generation'] < 0):
                raise ValueError('commissioned RGB clock identity required')
            if p['version'] == 2:
                ref = p['clock_binding']['authority_ref']
                if (not callable(self.clock_authority) or p['clock_binding']['validation_mode'] != 'owner_accepted_per_observation'
                        or not isinstance(ref, dict) or set(ref) != {'id', 'digest'}
                        or not isinstance(ref['digest'], str) or len(ref['digest']) != 64
                        or any(c not in '0123456789abcdef' for c in ref['digest'])):
                    raise ValueError('fixed per-observation clock authority required')
                identifier(ref['id'], 'clock_authority_id')
            commissioning(p['depth_clock'])
            if (not finite(p['max_skew_s']) or not 0 < p['max_skew_s'] <= p['depth_clock']['max_delta_s']
                    or not finite(p['intrinsics_tolerance_px']) or not 0 <= p['intrinsics_tolerance_px'] <= 1):
                raise ValueError('explicit bounded pairing and crop policy required')
            r, projection, wire = p['registration'], p['projection'], s['registration']
            if (set(r) != REGISTRATION_KEYS or r['schema'] != 'home-capture-registration-1'
                    or type(r['version']) is not int or r['version'] != 1
                    or r['site_id'] != self.scope['site_id'] or r['room_id'] != self.scope['room_id']
                    or r['camera_id'] != p['camera_id'] or r['source_frame'] != 'rectified_left'
                    or r['frame'] != 'z_up_metric_floor0' or r['unit'] != 'm'
                    or not finite(r['error_bound_m']) or r['error_bound_m'] < 0
                    or set(r['validation_ref']) != {'id', 'digest'}
                    or r['transform_ref']['digest'] != digest(r['room_from_rectified_left'])
                    or wire is None or wire != {
                        'registration_ref': r['id'], 'registration_digest': digest(r),
                        'geometry_revision': r['geometry_revision'], 'geometry_digest': r['geometry_digest'],
                        'mount_digest': digest(p['mount_signature']), 'calibration_digest': projection['id'],
                        'coverage_refs': r['coverage_refs'], 'frame': r['frame'], 'unit': r['unit'],
                        'transform_chain_refs': [r['transform_ref']]}
                    or projection.get('status') != 'validated' or projection.get('site_id') != r['site_id']
                    or projection.get('camera_id') != r['camera_id'] or projection.get('mount_revision') != r['mount_revision']
                    or not projection.get('validation_receipt') or not finite(projection.get('error_bound_m'))
                    or projection['error_bound_m'] < 0 or projection.get('frame') != 'rectified_left'
                    or projection.get('width') != 640 or projection.get('height') != 400
                    or not projection.get('reprojection', {}).get('receipt')
                    or not finite(projection['reprojection'].get('p95_px'))
                    or not 0 <= projection['reprojection']['p95_px'] <= 5):
                raise ValueError('qualified calibration, registration, mount and reprojection bindings required')
            # Validate rigid room axes even before a sample is offered.
            transform_point({'frame': 'rectified_left', 'xyz_m': [0, 0, 1]}, r['room_from_rectified_left'],
                            source_frame='rectified_left', target_frame=r['frame'], version=r['id'],
                            calibration_error_m=r['error_bound_m'])
            k, intrinsics = projection['reference_intrinsics']['rectified_left'], projection['intrinsics']
            if (intrinsics != {'fx': k[0][0], 'fy': k[1][1], 'cx': k[0][2], 'cy': k[1][2]}
                    or k[0][1] != 0 or k[1][0] != 0 or k[2] != [0, 0, 1]
                    or not isinstance(projection.get('rgb_distortion_coefficients'), list)
                    or len(projection['rgb_distortion_coefficients']) != 14
                    or not all(finite(v) for v in projection['rgb_distortion_coefficients'])):
                raise ValueError('consistent metric intrinsics and explicit distortion required')
            deproject([0, 0], 1, intrinsics, source_frame='rectified_left', calibration_version=projection['id'])
        except (KeyError, TypeError, ValueError, JournalError, RecursionError) as error:
            raise ValueError('invalid commissioned capture policy') from error

    def resolve_receipt(self, store_id, receipt_id):
        """Fixed original-owner metadata capability supplied to journal composition."""
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.resolved[receipt_id])

    def original_receipt(self, store_id, receipt_id):
        """Local authorized inference access; never a semantic HTTP read route."""
        if store_id != self.policy['receipt_store_id']:
            raise KeyError(store_id)
        return deepcopy(self.originals[receipt_id])

    def _component_key(self, name, receipt, metadata):
        sequence = receipt['sequence'] if name == 'rgb' else metadata['frames']['depth']['sequence']
        return canonical([self.scope, self.source_id, receipt['session'], name, sequence])

    def _decode(self, jpeg, header, bundle, calibration, received_at, now):
        _require(isinstance(header, str) and 0 < len(header) <= 8192, 'rgb_receipt_invalid', 'capture_clock_unavailable')
        try:
            receipt = json.loads(base64.b64decode(header + '=' * ((-len(header)) % 4), altchars=b'-_', validate=True),
                                 object_pairs_hook=_object_pairs)
            frame = preview_frame(jpeg, header, now)
        except Exception:
            raise CaptureRefusal('rgb_receipt_invalid', 'capture_clock_unavailable') from None
        binding = self.policy['clock_binding']
        keys = binding if self.policy['version'] == 1 else ('timing_generation', 'host_boot_id')
        _require(receipt['session'] == self.source['capture_epoch']
                 and all(receipt[key] == binding[key] for key in keys),
                 'capture_clock_binding_changed', 'clock_mapping_invalidated')
        if self.policy['version'] == 2:
            try:
                proven = (receipt['validation_id'] == 'causal:' + receipt['challenge_nonce']
                          and self.clock_authority(deepcopy(receipt), deepcopy(binding['authority_ref'])) is True)
            except Exception:
                proven = False
            _require(proven, 'capture_clock_authority_unproven', 'clock_mapping_invalidated')
        _require(finite(received_at) and frame['observed_at'] <= received_at <= now, 'receipt_availability_invalid', 'capture_clock_unavailable')
        _require(isinstance(bundle, bytes) and 0 < len(bundle) <= 4 * 1024**2, 'bundle_invalid', 'depth_unavailable')
        try:
            validate_npz(bundle, 4 * 1024**2)
            import numpy as np
            with np.load(io.BytesIO(bundle), allow_pickle=False) as archive:
                _require(set(archive.files) == {'depth_mm', 'left', 'right', 'rgb_nv12', 'metadata_json'}, 'bundle_invalid', 'depth_unavailable')
                for name, shape, dtype in (('depth_mm', (400, 640), np.uint16), ('left', (400, 640), np.uint8),
                                           ('right', (400, 640), np.uint8), ('rgb_nv12', (540, 640), np.uint8)):
                    value = archive[name]
                    _require(value.shape == shape and value.dtype == dtype, 'bundle_invalid', 'depth_unavailable')
                depth = archive['depth_mm'].copy()
                encoded = archive['metadata_json']
                _require(encoded.dtype == np.uint8 and encoded.ndim == 1 and len(encoded) <= 32768, 'bundle_invalid', 'depth_unavailable')
                meta = json.loads(encoded.tobytes(), object_pairs_hook=_object_pairs)
        except CaptureRefusal:
            raise
        except Exception:
            raise CaptureRefusal('bundle_invalid', 'depth_unavailable') from None
        _require(meta.get('schema') == 1 and meta.get('session') == receipt['session']
                 and meta.get('depth_alignment') == 'rectified_left' and meta.get('depth_units') == 'millimetres'
                 and type(meta.get('invalid_depth')) is int and meta['invalid_depth'] == 0
                 and meta.get('captured_unix_ns_semantics') == 'bundle_packaging_time_not_sensor_exposure'
                 and set(meta.get('frames', {})) == {'rgb', 'depth', 'left', 'right'}, 'bundle_source_mismatch', 'depth_unavailable')
        rgb, depth_frame = meta['frames']['rgb'], meta['frames']['depth']
        _require((rgb.get('width'), rgb.get('height')) == (640, 360)
                 and (depth_frame.get('width'), depth_frame.get('height')) == (640, 400)
                 and rgb.get('source_distortion_coefficients') == self.policy['projection']['rgb_distortion_coefficients'],
                 'projection_or_crop_changed', 'registration_invalidated')
        tolerance = receipt['timestamp_quantization_ns'] / 1_000_000_000 + 1e-6
        _require(type(rgb.get('sequence')) is int and rgb['sequence'] == receipt['sequence']
                 and finite(rgb.get('device_timestamp_seconds'))
                 and abs(rgb['device_timestamp_seconds'] - receipt['frame_device_ns'] / 1_000_000_000) <= tolerance,
                 'rgb_bundle_pair_mismatch', 'depth_unavailable')
        _require(finite(depth_frame.get('device_timestamp_seconds')), 'depth_clock_invalid', 'capture_clock_unavailable')
        delta = depth_frame['device_timestamp_seconds'] - receipt['frame_device_ns'] / 1_000_000_000
        _require(delta <= 0 and -delta <= self.policy['max_skew_s'], 'noncausal_or_excessive_depth_skew', 'depth_unavailable')
        _require(isinstance(calibration, dict) and calibration.get('session') == receipt['session']
                 and calibration.get('depth_alignment') == 'rectified_left' and calibration.get('depth_units') == 'millimetres'
                 and _original_digest(calibration, 24576)
                 and calibration_content_id(calibration) == self.source['registration']['calibration_digest'],
                 'calibration_changed', 'registration_invalidated')
        try:
            # Normal admission only: never measuring_reprojection=True.
            camera_model(meta, self.policy['projection'], intrinsics_tolerance_px=self.policy['intrinsics_tolerance_px'])
            _require(check_mount(self.policy['mount_signature'], depth)['status'] == 'unchanged',
                     'mount_changed_or_unobservable', 'registration_invalidated')
        except CaptureRefusal:
            raise
        except Exception:
            raise CaptureRefusal('projection_or_crop_changed', 'registration_invalidated') from None
        mapped = map_depth_time({'source_timing_receipt': receipt, 'observed_at': frame['observed_at'],
                                 'uncertainty_seconds': receipt['uncertainty_seconds'] + receipt['host_clock_uncertainty_seconds']},
                                depth_frame, self.policy['depth_clock'])
        relative_error = sum(value for key, value in mapped['uncertainty_components_s'].items() if key != 'anchor_s')
        _require(-delta + relative_error <= self.policy['max_skew_s'],
                 'noncausal_or_excessive_depth_skew', 'depth_unavailable')
        error = mapped['uncertainty_seconds']
        prior = self.components.get(self._component_key('depth', receipt, meta))
        if prior is not None:
            original = prior['original']
            old_error = original['depth_clock_mapping']['uncertainty_seconds']
            difference = abs(mapped['observed_at'] - original['observed_at'])
            _require(difference <= error + old_error, 'depth_clock_origin_conflict', 'clock_mapping_invalidated')
            # A newer RGB anchor is another interpretation of the SAME depth
            # exposure. Preserve its first observation/availability and inflate
            # the bound rather than minting or refreshing an original root.
            mapped = {**mapped, 'observed_at': original['observed_at']}
            error = max(error, old_error + difference)
        # Error is subtracted once by the consumer/journal half-open admission.
        valid_until = min(frame['observed_at'] + 3, mapped['observed_at'] + 3,
                          mapped['observed_at'] + self.source['max_age_s'])
        # Epoch-second binary rounding must not round a horizon outward.
        valid_until = math.nextafter(valid_until, -math.inf)
        _require(error <= self.source['capture_clock']['max_uncertainty_s'] and now + error < valid_until,
                 'capture_expired_or_uncertain', 'capture_clock_unavailable')
        if self.barrier is not None:
            p = self.barrier['publication']['payload']
            _require(mapped['observed_at'] - error > p['observed_at'] + p['control']['clock']['uncertainty_s'],
                     'capture_predates_source_barrier', 'capture_clock_unavailable')
        return receipt, frame, meta, depth, mapped, error, valid_until

    def _envelope(self, payload):
        return {'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': payload['id'],
                'producer_id': self.source['producer_id'], 'stream_epoch': self.source['producer_epoch'], **self.scope,
                **{key: payload[key] for key in ('observed_at', 'available_at', 'valid_until')},
                'payload_type': 'home-live-geometry-1', 'payload_version': '1.0', 'revision': self.revision, 'payload': payload}

    def _prepare(self, identity, fingerprint, receipt, frame, meta, depth, mapped, error, valid_until, received_at, now):
        capture_id = 'capture:' + digest(identity)
        roots = {name: 'observation:' + digest(json.loads(self._component_key(name, receipt, meta))) for name in ('rgb', 'depth')}
        times = {'rgb': frame['observed_at'], 'depth': mapped['observed_at']}
        refs, records, new_components = [], [], {}
        for name in ('rgb', 'depth'):
            component_key = self._component_key(name, receipt, meta)
            component_fingerprint = _original_digest(
                [receipt, fingerprint] if name == 'rgb' else
                [meta['frames']['depth'], hashlib.sha256(depth.astype('<u2', copy=False).tobytes()).hexdigest(),
                 self.source['registration']['calibration_digest']])
            prior = self.components.get(component_key)
            if prior is not None:
                _require(prior['fingerprint'] == component_fingerprint, 'component_identity_conflict')
                refs.append(deepcopy(prior['record']['ref']))
                records.append((prior['receipt_id'], prior['original'], prior['record']))
                continue
            original = {'schema': 'home-original-capture-component-1', 'version': 1, 'capture_id': capture_id,
                        'capture_epoch': receipt['session'], 'capture_seq': receipt['sequence'], 'component_id': name,
                        'observed_at': times[name], 'available_at': received_at, 'sensor_roots': self.source['sensor_roots'],
                        'rgb_receipt': receipt if name == 'rgb' else None,
                        'raw_device_timestamp_seconds': meta['frames']['depth']['device_timestamp_seconds'] if name == 'depth' else None,
                        'source_metadata': meta if name == 'depth' else None,
                        'input_digest': fingerprint, 'depth_clock_mapping': mapped if name == 'depth' else None}
            receipt_id = 'component:' + digest(json.loads(component_key))
            ref = {'store_id': self.policy['receipt_store_id'], 'receipt_id': receipt_id, 'digest': _original_digest(original),
                   'component_id': name, 'observed_at': times[name], 'available_at': received_at,
                   'clock_mapping_id': self.source['capture_clock']['mapping_id']}
            record = {'ref': ref, 'capture_epoch': receipt['session'], 'capture_seq': receipt['sequence'],
                      'observation_root_times': {roots[name]: times[name]},
                      'sensor_root_bindings': {roots[name]: self.source['sensor_roots']}}
            refs.append(ref)
            records.append((receipt_id, original, record))
            new_components[component_key] = {'fingerprint': component_fingerprint, 'receipt_id': receipt_id,
                                              'original': original, 'record': record}
        association = {'person_status': 'not_applicable', 'subject_status': 'not_applicable',
                       'association_id': capture_id, 'generation': 1, 'method_ref': self.method,
                       'candidate_person_refs': [], 'candidate_subject_refs': []}
        p = {'schema': 'home-live-geometry-1', 'version': 1, 'id': capture_id, **self.scope,
             'manifest_ref': self.config['manifest_ref'], 'source_id': self.source_id, 'source_generation': self.generation,
             'person_ref': None, 'subject_ref': None, 'association': association,
             'head': {'key': {'source_id': self.source_id, 'payload_type': 'home-live-geometry-1', 'person_ref': None,
                              'subject_ref': None, 'predicate': 'geometry_bundle', 'method_ref': self.method},
                      'position': {'capture_epoch': receipt['session'], 'capture_seq': receipt['sequence'], 'revision': 0},
                      'supersedes': deepcopy(self.last_ref)},
             'status': 'unobservable', 'reason': 'pose_unavailable', 'observed_at': frame['observed_at'],
             'available_at': now, 'valid_until': valid_until, 'sensor_roots': self.source['sensor_roots'],
             'observation_roots': list(roots.values()), 'observation_root_times': {roots[n]: times[n] for n in roots},
             'sensor_root_bindings': {root: self.source['sensor_roots'] for root in roots.values()},
             'parent_refs': [], 'original_receipt_refs': refs, 'clock': {
                 'status': 'commissioned', 'mapping_id': self.source['capture_clock']['mapping_id'],
                 'mapping_digest': self.source['capture_clock']['mapping_digest'], 'source_epoch': receipt['session'],
                 'original_unit': 'unix_ns', 'original_value': str(receipt['observed_at_unix_ns']), 'uncertainty_s': error},
             'registration': self.source['registration'], 'method_ref': self.method,
             'parts': {name: {'status': 'unobservable', 'reason': 'pose_unavailable', 'support_refs': refs,
                              'observed_at': frame['observed_at'], 'valid_until': valid_until} for name in PARTS}}
        local = {'id': capture_id, 'qualification': 'paired_capture_qualified', 'observed_at': frame['observed_at'],
                 'depth_observed_at': mapped['observed_at'], 'available_at': now, 'received_at': received_at,
                 'valid_until': valid_until, 'effective_valid_until': valid_until - error,
                 'uncertainty_s': error, 'source_generation': self.generation, 'human_geometry': 'unavailable'}
        entry = {'fingerprint': fingerprint, 'event': self._envelope(p), 'capture': local, 'depth': depth,
                 'source_metadata': deepcopy(meta),
                 'result': None, 'records': records}
        for key, original, record in records:
            self.originals[key], self.resolved[key] = original, record
        self.components.update(new_components)
        self.captures[canonical(identity)] = entry
        return entry

    def _deliver(self, entry):
        self.pending = entry
        try:
            ack = self.publish(deepcopy(entry['event']))
            _require(isinstance(ack, dict) and ack.get('status') == 'accepted'
                     and ack.get('id') == entry['event']['id'] and ack.get('payload_digest') == digest(entry['event']['payload']),
                     'publication_ack_invalid')
        except PublicationRefusal as error:
            # A positive refusal cannot be confused with a lost write ACK.
            self.pending = None
            entry['result'] = {'status': 'refused', 'reason': error.reason, 'receipt': None,
                'publication': deepcopy(entry['event']), 'capture': None, 'withdrawal': 'not_recorded',
                'publication_outcome': 'terminal_refusal', 'http_status': error.http_status}
            return deepcopy(entry['result'])
        except Exception:
            return {'status': 'refused', 'reason': 'publication_unconfirmed', 'receipt': None,
                    'publication': deepcopy(entry['event']), 'capture': None, 'withdrawal': 'not_recorded'}
        self.pending = None
        self.withdrawn = False
        self.last_ref = {key: ack[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
        self.highest_sequence = entry['event']['payload']['head']['position']['capture_seq']
        result = {'status': 'qualified', 'reason': 'paired_capture_qualified', 'receipt': ack,
                  'publication': entry['event'], 'capture': entry['capture'], 'withdrawal': 'not_needed'}
        entry['result'] = deepcopy(result)
        return deepcopy(result)

    def _refuse(self, reason, barrier_reason, now):
        result = {'status': 'refused', 'reason': reason, 'receipt': None, 'publication': None, 'capture': None, 'withdrawal': 'not_recorded'}
        if self.control_clock is None or self.pending is not None or not finite(now):
            return result
        if self.withdrawn and self.barrier is not None and self.barrier['generation'] == self.generation:
            return {**result, 'receipt': deepcopy(self.barrier['receipt']), 'publication': deepcopy(self.barrier['publication']), 'withdrawal': 'recorded'}
        try:
            if self.pending_barrier is None:
                if self.read_authority_generation is not None:
                    generation = self.read_authority_generation()
                    _require(type(generation) is int and generation >= 0, 'authority_generation_unavailable')
                    self.authority_generation = generation
                control = self.control_clock()
                asserted = control['observed_at']
                validate_clock(control['clock'], self.source['control_clock'], asserted)
                # The independent clock call completes after the caller's
                # earlier sample. Its actual availability must be sampled now;
                # otherwise every progressing real wall clock appears future.
                available = self.clock()
                _require(finite(available) and available >= now, 'control_clock_unavailable')
                now = available
                _require(asserted <= now, 'control_clock_future')
                payload = {'schema': 'home-live-geometry-1', 'version': 1,
                           'id': 'capture-barrier:' + digest([self.scope, self.source_id, self.generation, asserted]), **self.scope,
                           'manifest_ref': self.config['manifest_ref'], 'source_id': self.source_id,
                           'source_generation': self.generation + 1, 'status': 'withdrawn', 'reason': barrier_reason,
                           'observed_at': asserted, 'available_at': now, 'valid_until': now + 1,
                           'control': {'kind': 'source_barrier', 'asserted_at': asserted, 'clock': control['clock'],
                                       'expected_source_generation': self.generation, 'next_source_generation': self.generation + 1,
                                       'expected_authority_generation': self.authority_generation, 'retired_refs': []}}
                self.pending_barrier = self._envelope(payload)
            event = self.pending_barrier
            payload = event['payload']
            ack = self.publish(deepcopy(event))
            _require(ack.get('status') == 'accepted' and ack.get('id') == event['id']
                     and ack.get('payload_digest') == digest(payload), 'publication_ack_invalid')
        except PublicationRefusal as error:
            self.pending_barrier = None
            return {**result, 'publication_outcome': 'terminal_refusal',
                    'withdrawal_reason': error.reason, 'http_status': error.http_status}
        except Exception:
            return result
        self.generation += 1
        self.authority_generation += 1
        self.pending_barrier = None
        self.withdrawn = True
        self.barrier = {'generation': self.generation, 'publication': event, 'receipt': ack}
        return {**result, 'receipt': deepcopy(ack), 'publication': deepcopy(event), 'withdrawal': 'recorded'}

    def offer(self, jpeg, header, bundle, calibration, *, received_at):
        """Qualify one original pair and publish it; no acquisition or hidden retry."""
        if self.closed:
            return {'status': 'refused', 'reason': 'capture_adapter_closed', 'receipt': None,
                    'publication': None, 'capture': None, 'withdrawal': 'not_recorded'}
        if not self.lock.acquire(blocking=False):
            return {'status': 'refused', 'reason': 'capture_adapter_busy', 'receipt': None, 'publication': None,
                    'capture': None, 'withdrawal': 'not_recorded'}
        try:
            now = self.clock()
            # Resolve retries before freshness checks; wrapper arrival never
            # becomes a new original capture/availability or extends its lease.
            identity = fingerprint = None
            if isinstance(header, str) and len(header) <= 8192 and isinstance(jpeg, bytes) and len(jpeg) <= 256 * 1024 and isinstance(bundle, bytes) and len(bundle) <= 4 * 1024**2:
                try:
                    raw = json.loads(base64.b64decode(header + '=' * ((-len(header)) % 4), altchars=b'-_', validate=True), object_pairs_hook=_object_pairs)
                    identity = [self.scope, self.source_id, raw['session'], raw['timing_generation'], raw['sequence']]
                    calibration_hash = _original_digest(calibration, 24576)
                    fingerprint = _original_digest([hashlib.sha256(jpeg).hexdigest(), raw, hashlib.sha256(bundle).hexdigest(), calibration_hash])
                    prior = self.captures.get(canonical(identity))
                    if prior:
                        if prior['fingerprint'] != fingerprint:
                            return self._refuse('capture_identity_conflict', 'source_unavailable', now)
                        return deepcopy(prior['result']) if prior['result'] is not None else self._deliver(prior)
                except (ValueError, TypeError, KeyError, RecursionError, JournalError):
                    pass
            if self.pending is not None:
                return {'status': 'refused', 'reason': 'prior_publication_unconfirmed', 'receipt': None,
                        'publication': None, 'capture': None, 'withdrawal': 'not_recorded'}
            if self.pending_barrier is not None:
                return self._refuse('source_barrier_unconfirmed', 'source_unavailable', now)
            if len(self.captures) >= self.max_captures:
                return self._refuse('capture_capacity_exhausted', 'capacity_exceeded', now)
            try:
                _require(finite(now), 'service_clock_unavailable', 'capture_clock_unavailable')
                decoded = self._decode(jpeg, header, bundle, calibration, received_at, now)
                _require(identity is not None and fingerprint is not None, 'capture_identity_invalid')
                _require(decoded[0]['sequence'] > self.highest_sequence, 'capture_sequence_regressed')
                entry = self._prepare(identity, fingerprint, *decoded, received_at, now)
                return self._deliver(entry)
            except CaptureRefusal as error:
                return self._refuse(error.reason, error.barrier_reason, now)
            except (ValueError, TypeError, KeyError, RecursionError, OverflowError, JournalError):
                return self._refuse('capture_invalid', 'source_unavailable', now)
        finally:
            self.lock.release()

    def withdraw(self, reason='operator_withdrawal', *, retain_originals=False):
        """Permanently stop this session, fence delivery and clear owned buffers.

        A source barrier safely retires even an unacknowledged positive write:
        that write either committed first or its old generation will be refused.
        Retain only control retry metadata; never replay a cancelled capture.
        A busy producer must finish its bounded turn before cleanup is confirmed.
        """
        if reason not in {'source_unavailable', 'operator_withdrawal', 'grant_revoked', 'camera_unavailable'} or type(retain_originals) is not bool:
            raise ValueError('closed lifecycle reason required')
        self.closed = True
        if not self.lock.acquire(blocking=False):
            return {'status': 'unconfirmed', 'reason': 'capture_cleanup_busy', 'withdrawal': 'not_recorded', 'buffers_cleared': False}
        try:
            self.pending = None
            result = self._refuse('capture_adapter_closed', reason, self.clock())
            if retain_originals:
                return {'status': 'stopped' if result['withdrawal'] == 'recorded' else 'unconfirmed',
                        'reason': reason, 'withdrawal': result['withdrawal'], 'buffers_cleared': False,
                        'receipt': deepcopy(result['receipt'])}
            self._clear_originals()
            return {'status': 'stopped' if result['withdrawal'] == 'recorded' else 'unconfirmed',
                    'reason': reason, 'withdrawal': result['withdrawal'], 'buffers_cleared': True}
        finally:
            self.lock.release()

    def _clear_originals(self):
        self.captures.clear()
        self.components.clear()
        self.originals.clear()
        self.resolved.clear()

    def finalize(self):
        """Release retained originals after supervised drain; never publish.

        In particular an unconfirmed barrier is not retried after the recorder
        has declared its terminal cursor. Only withdraw() may attempt that write.
        """
        self.closed = True
        if not self.lock.acquire(blocking=False):
            return False
        try:
            self.pending = None
            self._clear_originals()
            return True
        finally:
            self.lock.release()

    def source_gap(self, reason='source_unavailable'):
        """Withdraw current support without inventing a capture or closing demand."""
        if reason not in {'source_unavailable', 'camera_unavailable', 'depth_unavailable',
                          'association_lost', 'registration_invalidated', 'source_restarted'}:
            raise ValueError('closed source-gap reason required')
        if self.closed or not self.lock.acquire(blocking=False):
            return {'withdrawal': 'not_recorded', 'reason': 'capture_cleanup_busy'}
        try:
            return self._refuse(reason, reason, self.clock())
        finally:
            self.lock.release()

    def measurement_input(self, capture_id):
        """Copy one qualified native depth input for a fixed local spatial stage.

        RGB-to-depth association must use the commissioned projection and its
        original frame metadata. This capability does not turn RGB coordinates
        into rectified-left pixels or expose a new semantic HTTP read route.
        """
        if not self.lock.acquire(blocking=False):
            raise ValueError('capture measurement is busy')
        try:
            entry = next((e for e in self.captures.values() if e['capture']['id'] == capture_id), None)
            if (self.closed or entry is None or entry['result'] is None or self.pending is not None
                    or self.pending_barrier is not None or self.withdrawn
                    or entry['capture']['source_generation'] != self.generation
                    or not entry['capture']['available_at'] <= self.clock() < entry['capture']['effective_valid_until']):
                raise ValueError('capture is unavailable or expired')
            return {'depth_mm': entry['depth'].copy(), 'source_metadata': deepcopy(entry['source_metadata']),
                    'projection': deepcopy(self.policy['projection']),
                    'registration': deepcopy(self.policy['registration']),
                    'intrinsics_tolerance_px': self.policy['intrinsics_tolerance_px'],
                    'capture': deepcopy(entry['capture']), 'capture_payload': deepcopy(entry['event']['payload']),
                    'original_receipts': [deepcopy(original) for _, original, _ in entry['records']]}
        finally:
            self.lock.release()

    def point_at(self, capture_id, pixel):
        """Authorized numeric consumer: one rectified-left pixel, not a person.

        Raw uint16 millimetres are converted once, then the commissioned rigid
        room transform is applied. No RGB-box-as-depth shortcut is exposed.
        """
        entry = next((e for e in self.captures.values() if e['capture']['id'] == capture_id), None)
        if (self.closed or entry is None or entry['result'] is None or self.pending_barrier is not None or self.withdrawn
                or entry['capture']['source_generation'] != self.generation
                or not entry['capture']['available_at'] <= self.clock() < entry['capture']['effective_valid_until']):
            raise ValueError('capture is unavailable or expired')
        if (not isinstance(pixel, (list, tuple)) or len(pixel) != 2
                or any(type(v) is not int for v in pixel) or not 0 <= pixel[0] < 640 or not 0 <= pixel[1] < 400):
            raise ValueError('rectified-left integer pixel required')
        p, r = self.policy['projection'], self.policy['registration']
        depth_m = int(entry['depth'][pixel[1], pixel[0]]) / 1000
        point = deproject(pixel, depth_m, p['intrinsics'], source_frame='rectified_left', calibration_version=p['id'])
        return transform_point(point, r['room_from_rectified_left'], source_frame='rectified_left', target_frame=r['frame'],
                               version=r['id'], calibration_error_m=r['error_bound_m'], source_uncertainty_m=p['error_bound_m'])['xyz_m']

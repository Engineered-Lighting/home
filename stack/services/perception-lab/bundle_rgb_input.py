"""Finite same-owner bundled RGB input using original qualified receipt bounds.

This module is inert on import. Camera acquisition, clock publication, models
and threads remain owned by the incumbent recording. A derived PNG is never
labelled as a causal JPEG; depth/geometry qualification is not inferred.
"""
import base64
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import types
import uuid

from assessment_existing_api import encoded, sha, need, finite, _json, _receipt, media_ref, sync_directory
from bundle_ingest import BundleIngestor
from recorder import validate_npz
from host_home import JETSON_HOME

FAMILY = 'existing-api-bundled-rgb-1'
POLICY_SCHEMA = 'home-lab-bundled-rgb-input-policy-1'
START_KIND, TERMINAL_KIND, WINDOW_KIND = ('assessment.bundle_rgb.' + s for s in ('started', 'terminal', 'window_closed'))
START_SCHEMA, TERMINAL_SCHEMA, WINDOW_SCHEMA = ('home-lab-bundled-rgb-' + s + '-1' for s in ('start', 'terminal', 'window'))
SUPPORT_SCHEMA, SUPPORT_STREAM = 'home-lab-bundled-rgb-input-original-1', 'bundled_rgb_input_original'
ANCHOR_STREAM = 'bundled_rgb_clock_anchor'
ROLE_LIMITS = dict(anchor_a=256*1024, anchor_b=256*1024, npz=4*1024**2,
    calibration_original=24576, bundle=4*1024**2, rgb=1024**2, support=256*1024)
CAMERA_SOURCE = Path(JETSON_HOME + '/victoria-camera-pilot/pilot-20260926/capture.py')
CAMERA_SHA = 'd052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132'
AUTHORITY = dict(candidate_only=True, owner_local_only=True, operational_geometry=None,
    identity_authority=False, engagement_authority=False, protection_authority=False, beam_authority=False)
SUPPORT_KEYS = {'schema', 'version', 'binding', 'capture_id', 'started_event_id', 'anchors',
    'npz_media_ref', 'calibration_media_ref', 'bundle_media_ref', 'rgb_media_ref', 'receiver_clock',
    'source_metadata', 'timing', 'status', 'reason', 'created_at', *AUTHORITY}
SUPPORT_KEYS |= {'la_anchor_frame_ref', 'selected_bundled_frame_ref', 'la_anchor_tap'}


def warn(warnings, ok, reason, check, deadline=None):
    """Strict unless the caller passes a list: then one named timing warning."""
    if ok: return
    if warnings is None: need(False, reason)
    warnings.append(dict(check=check, deadline=deadline))


def policy(value):
    version = value.get('version') if isinstance(value, dict) else None
    need(isinstance(value, dict) and set(value) == {'schema', 'version', 'max_captures', 'max_retained_bytes'}
        and type(version) is int and version in (1, 2)
        and value['schema'] == 'home-lab-bundled-rgb-input-policy-' + str(version)
        and type(value['max_captures']) is int and 1 <= value['max_captures'] <= (8 if version == 1 else 24)
        and type(value['max_retained_bytes']) is int and 0 < value['max_retained_bytes'] <= 40*1024**2,
        'invalid_bundled_rgb_policy')
    return deepcopy(value)


def clock_context():
    """Fixed already-installed public checks; no camera/clock service starts."""
    from chrony_preview_client import commissioned_runtime
    pilot, _, _ = commissioned_runtime()
    raw = CAMERA_SOURCE.read_bytes()
    need(len(raw) <= 256*1024 and sha(raw) == CAMERA_SHA, 'fixed_camera_clock_checks_changed')
    name = '_home_bundled_fixed_camera_clock'
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name); module.__file__ = str(CAMERA_SOURCE)
        sys.modules[name] = module
        try:
            exec(compile(raw, str(CAMERA_SOURCE), 'exec'), module.__dict__)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    need(module.__file__ == str(CAMERA_SOURCE), 'fixed_camera_clock_checks_changed')
    return module, pilot


class QualifiedReceiverClock:
    def begin_current(self):
        """Bracket new local work with the current protected host evidence.

        Unlike ``begin``, this does not bind a new operation to an earlier
        camera receipt. It neither publishes nor renews clock evidence.
        """
        camera, pilot = clock_context()
        config = pilot.load_config(pilot.CONFIG)
        boot = pilot._boot_id()
        evidence = camera.HostClockEvidence(**camera.read_protected_json(config.output_path, config.uid))
        need(evidence.host_boot_id == boot, 'execution_clock_boot_changed')
        before = camera.host_point()
        camera.check_host_clock(evidence, before, boot)
        return camera, pilot, config, evidence, before, boot

    def begin(self, receipt):
        camera, pilot = clock_context()
        config = pilot.load_config(pilot.CONFIG)
        boot = pilot._boot_id()
        body = camera.read_protected_json(config.output_path, config.uid)
        evidence = camera.HostClockEvidence(**body)
        need(evidence.host_boot_id == receipt['host_boot_id'] == boot
            and evidence.validation_id == receipt['host_clock_validation_id']
            and evidence.uncertainty_seconds == receipt['host_clock_uncertainty_seconds'],
            'receiver_clock_anchor_changed')
        before = camera.host_point()
        camera.check_host_clock(evidence, before, boot)
        return camera, pilot, config, evidence, before, boot

    def finish(self, original):
        # The first operation after the full HTTP body returns is this point.
        camera, pilot, config, evidence, before, boot = original
        after = camera.host_point()
        camera.check_host_clock(evidence, after, boot)
        camera.check_no_suspend(before, after)
        need(pilot._boot_id() == boot and camera.HostClockEvidence(**camera.read_protected_json(
            config.output_path, config.uid)) == evidence, 'receiver_clock_changed_during_read')
        return dict(evidence=asdict(evidence), before=asdict(before), after=asdict(after),
            boot_id=boot, validator_source_sha256=CAMERA_SHA)


def _ceil_ns(seconds):
    return int((Decimal(str(seconds))*1000000000).to_integral_value(rounding=ROUND_CEILING))


def host_execution_interval(proof):
    """Recheck original bracketed host work; never sample a historical clock.

    Returned UTC bounds include the original *whole* uncertainty. No unused
    lifetime charge is subtracted and no relation to sensor exposure is granted.
    """
    need(isinstance(proof, dict) and set(proof) == {'evidence', 'before', 'after', 'boot_id', 'validator_source_sha256'}
        and proof['validator_source_sha256'] == CAMERA_SHA, 'bundled_receiver_proof_invalid')
    e = proof['evidence']; points = [proof['before'], proof['after']]
    need(isinstance(e, dict) and set(e) == {'version', 'semantics', 'host_boot_id', 'validation_id',
        'checked_monotonic_ns', 'checked_unix_ns', 'valid_until_monotonic_ns', 'uncertainty_seconds'}
        and type(e['version']) is int and e['version'] == 1 and e['semantics'] == 'commissioned_camera_host_clock_v1'
        and str(uuid.UUID(e['host_boot_id'])) == e['host_boot_id'] == proof['boot_id']
        and isinstance(e['validation_id'], str) and bool(e['validation_id'])
        and finite(e['uncertainty_seconds']) and 0 <= e['uncertainty_seconds'] <= 1
        and all(type(e[k]) is int and 0 <= e[k] < 2**63 for k in
            ('checked_monotonic_ns', 'checked_unix_ns', 'valid_until_monotonic_ns'))
        and e['checked_unix_ns'] > 0 and 0 < e['valid_until_monotonic_ns']-e['checked_monotonic_ns'] <= 5*10**9,
        'bundled_receiver_proof_invalid')
    radius = _ceil_ns(e['uncertainty_seconds'])
    for p in points:
        need(isinstance(p, dict) and set(p) == {'before_ns', 'boot_ns', 'unix_ns', 'after_ns'}
            and all(type(v) is int and 0 <= v < 2**63 for v in p.values())
            and e['checked_monotonic_ns'] <= p['before_ns'] <= p['after_ns'] < e['valid_until_monotonic_ns']
            and p['after_ns']-p['before_ns'] <= 10_000_000
            and e['checked_unix_ns']+p['before_ns']-e['checked_monotonic_ns']-radius <= p['unix_ns']
                <= e['checked_unix_ns']+p['after_ns']-e['checked_monotonic_ns']+radius,
            'bundled_receiver_proof_invalid')
    a, b = points
    need(b['before_ns'] >= a['after_ns'] and b['boot_ns'] >= a['boot_ns']
        and max(p['boot_ns']-p['after_ns'] for p in points) <= min(p['boot_ns']-p['before_ns'] for p in points),
        'bundled_receiver_suspend_or_discontinuity')
    def bound(p, key, sign):
        return e['checked_unix_ns']+p[key]-e['checked_monotonic_ns']+sign*radius
    return dict(start_lower_unix_ns=str(bound(a, 'before_ns', -1)),
        start_upper_unix_ns=str(bound(a, 'after_ns', 1)),
        completion_lower_unix_ns=str(bound(b, 'before_ns', -1)),
        completion_upper_unix_ns=str(bound(b, 'after_ns', 1)),
        elapsed_lower_ns=str(max(0, b['before_ns']-a['after_ns'])),
        elapsed_upper_ns=str(b['after_ns']-a['before_ns']))


def receipt_upper(proof, receipt):
    """Recheck retained original arithmetic; no retrospective clock sampling."""
    interval = host_execution_interval(proof)
    e = proof['evidence']
    need(e['host_boot_id'] == receipt['host_boot_id']
        and e['validation_id'] == receipt['host_clock_validation_id']
        and e['uncertainty_seconds'] == receipt['host_clock_uncertainty_seconds'],
        'bundled_receiver_proof_invalid')
    return int(interval['completion_upper_unix_ns'])


def decode_bundle(raw):
    validate_npz(raw, ROLE_LIMITS['npz'])
    import numpy as np
    with np.load(io.BytesIO(raw), allow_pickle=False) as arrays:
        shapes = {'depth_mm': ((400, 640), 'uint16'), 'left': ((400, 640), 'uint8'),
            'right': ((400, 640), 'uint8'), 'rgb_nv12': ((540, 640), 'uint8')}
        need(set(arrays.files) == set(shapes) | {'metadata_json'}, 'bundled_arrays_invalid')
        for name, (shape, dtype) in shapes.items():
            need(arrays[name].shape == shape and arrays[name].dtype == np.dtype(dtype), 'bundled_arrays_invalid')
        encoded_meta = arrays['metadata_json']
        need(encoded_meta.dtype == np.uint8 and encoded_meta.ndim == 1 and 0 < len(encoded_meta) <= 32768,
            'bundled_metadata_invalid')
        meta = _json(encoded_meta.tobytes())
        depth, nv12 = arrays['depth_mm'].copy(), arrays['rgb_nv12'].copy()
    need(meta.get('schema') == 1 and meta.get('captured_unix_ns_semantics') == 'bundle_packaging_time_not_sensor_exposure'
        and meta.get('depth_alignment') == 'rectified_left' and meta.get('depth_units') == 'millimetres'
        and meta.get('invalid_depth') == 0 and set(meta.get('frames', {})) == {'rgb', 'depth', 'left', 'right'},
        'bundled_metadata_invalid')
    for name, frame in meta['frames'].items():
        need(type(frame.get('sequence')) is int and frame['sequence'] >= 0
            and finite(frame.get('device_timestamp_seconds')) and finite(frame.get('host_monotonic_timestamp_seconds'))
            and (frame.get('width'), frame.get('height')) == ((640, 360) if name == 'rgb' else (640, 400)),
            'bundled_component_invalid')
    return depth, nv12, meta


def mapped_timing(anchor_a, anchor_b, meta, proof, *, warnings=None):
    a, b = (row['metadata'] for row in (anchor_a, anchor_b))
    ra, rb = (row['source_timing_receipt'] for row in (a, b))
    target = meta['frames']['rgb']; target_ns = Decimal(str(target['device_timestamp_seconds']))*10**9
    need(meta['session'] == ra['session'] == rb['session']
        and all(ra[k] == rb[k] for k in ('timing_generation', 'host_boot_id')), 'bundled_rgb_not_inside_original_anchors')
    warn(warnings, ra['sequence'] < target['sequence'] < rb['sequence']
        and ra['frame_device_ns']+ra['timestamp_quantization_ns']+1000 < target_ns
            < rb['frame_device_ns']-rb['timestamp_quantization_ns']-1000,
        'bundled_rgb_not_inside_original_anchors', 'anchor_containment')
    lower = ra['observed_at_unix_ns']-_ceil_ns(a['uncertainty_seconds'])
    ordinal_upper = rb['observed_at_unix_ns']+_ceil_ns(b['uncertainty_seconds'])
    received_upper = receipt_upper(proof, rb)
    upper = min(ordinal_upper, received_upper)
    warn(warnings, lower <= upper, 'bundled_rgb_clock_interval_empty', 'anchor_containment')
    midpoint = (lower+upper)//2
    uncertainty = max(midpoint-lower, upper-midpoint)
    warn(warnings, uncertainty <= 10**9, 'bundled_rgb_clock_uncertainty_excessive', 'clock_uncertainty')
    observed = float(Decimal(midpoint)/1000000000)
    error = uncertainty/1e9
    return dict(method='same-stream-ordinal-qualified-receipt-1', lower_unix_ns=str(lower),
        ordinal_upper_unix_ns=str(ordinal_upper), receipt_upper_unix_ns=str(received_upper),
        upper_unix_ns=str(upper), midpoint_unix_ns=str(midpoint), uncertainty_ns=str(uncertainty),
        ordinal_uncertainty_seconds=(ordinal_upper-lower)/2e9, observed_at=observed,
        uncertainty_seconds=error, valid_until=min(a['valid_until'], b['valid_until'], observed+3-error))


def anchor_metadata(receipt, *, site_id, camera_id, available_at, started_at, warnings=None):
    from store import identifier
    epoch = identifier(receipt['session'])
    observed = float(Decimal(receipt['observed_at_unix_ns'])/1000000000)
    # The validated schema-3 full bound already includes this SAME host clock.
    total = receipt['uncertainty_seconds']
    need(finite(available_at) and finite(started_at) and observed <= available_at
        and observed >= started_at, 'bundled_anchor_clock_refused')
    warn(warnings, total <= 1, 'bundled_anchor_clock_refused', 'clock_uncertainty')
    warn(warnings, available_at-observed+total <= 3, 'bundled_anchor_clock_refused', 'proof_expired', observed+3-total)
    return dict(site_id=site_id, camera_id=camera_id, capture_epoch=epoch,
        frame_id=f"{epoch}-{receipt['timing_generation']}-{receipt['sequence']}",
        sequence=receipt['sequence'], stream=ANCHOR_STREAM, format='jpeg', width=640, height=360,
        original_family=FAMILY, observed_at=observed, available_at=available_at, valid_until=observed+3-total,
        uncertainty_seconds=total, clock_mapping_status='verified', clock_mapping_id=receipt['validation_id'],
        timing_generation=receipt['timing_generation'], source_timing_receipt=deepcopy(receipt),
        timestamp_semantics=receipt['timestamp_semantics'], source_clock=receipt['source_clock'],
        source_timestamp_seconds=receipt['frame_device_ns']/1e9,
        receiver_clock_validation_id=receipt['host_clock_validation_id'], session_time_s=observed-started_at)


def support_refs(support):
    need(isinstance(support, dict) and set(support) == SUPPORT_KEYS and support['schema'] == SUPPORT_SCHEMA
        and type(support['version']) is int and support['version'] == 1 and all(support.get(k) == v for k, v in AUTHORITY.items())
        and set(support['anchors']) == {'a', 'b'}, 'bundled_support_invalid')
    result = {}
    for key in ('a', 'b'):
        anchor = support['anchors'][key]
        if anchor is not None:
            need(isinstance(anchor, dict) and set(anchor) == {'media_ref', 'header'}, 'bundled_anchor_invalid')
            result['anchor_'+key] = anchor['media_ref']
    for role, field in (('npz', 'npz_media_ref'), ('calibration_original', 'calibration_media_ref'),
                        ('bundle', 'bundle_media_ref'), ('rgb', 'rgb_media_ref')):
        if support[field] is not None: result[role] = support[field]
    return result


def validate_support(records, *, binding, event=None, frame=None, started=None, terminal=None):
    need(isinstance(records, dict) and 'support' in records, 'bundled_support_missing')
    for role, item in records.items():
        need(role in ROLE_LIMITS and set(item) == {'record', 'data'}, 'bundled_media_roles_invalid')
        row, raw = item['record'], item['data']
        need(isinstance(raw, bytes) and 0 < len(raw) <= ROLE_LIMITS[role]
            and len(raw) == row['size'] and sha(raw) == row['sha256']
            and row['session_id'] == binding['session_id'] and row['generation'] == binding['media_generation'],
            'bundled_original_bytes_changed')
    support = _json(records['support']['data']); refs = support_refs(support)
    need(support['binding'] == binding and set(records) == set(refs) | {'support'}
        and all(media_ref(records[k]['record']) == v for k, v in refs.items())
        and records['support']['record']['metadata'].get('stream') == SUPPORT_STREAM,
        'bundled_original_binding_changed')
    need(support['la_anchor_frame_ref'] == dict(frame_id=records['anchor_b']['record']['metadata']['frame_id'],
            media_ref=refs['anchor_b']) and set(support['la_anchor_tap']) == {'status', 'reason'}
        and support['la_anchor_tap']['status'] in ('not_requested', 'succeeded', 'failed', 'refused'),
        'bundled_distinct_anchor_lineage_changed')
    if started is not None:
        need(started['kind'] == START_KIND and started['session_id'] == binding['session_id']
            and started['id'] == support['started_event_id']
            and started['payload'] == dict(schema=START_SCHEMA, version=1, binding=binding,
                capture_id=support['capture_id'], anchor_b_media_ref=refs['anchor_b'],
                anchor_b_header=support['anchors']['b']['header'], started_at=started['payload']['started_at'])
            and finite(started['payload']['started_at']) and started['payload']['started_at'] <= support['created_at'],
            'bundled_start_changed')
    if terminal is not None:
        need(terminal['kind'] == TERMINAL_KIND and terminal['session_id'] == binding['session_id']
            and terminal['id'] > support['started_event_id']
            and terminal['payload'] == dict(schema=TERMINAL_SCHEMA, version=1, binding=binding,
                capture_id=support['capture_id'], started_event_id=support['started_event_id'],
                status=support['status'], reason=support['reason'], support_media_ref=media_ref(records['support']['record']),
                completed_at=terminal['payload']['completed_at'])
            and finite(terminal['payload']['completed_at']) and terminal['payload']['completed_at'] >= support['created_at'],
            'bundled_terminal_changed')
    for name in ('a', 'b'):
        if support['anchors'][name] is None: continue
        item = records['anchor_'+name]; receipt = _receipt(item['data'], support['anchors'][name]['header'])
        m = item['record']['metadata']
        need(m == anchor_metadata(receipt, site_id=binding['site_id'], camera_id=binding['camera_id'],
            available_at=m['available_at'], started_at=m['observed_at']-m['session_time_s'])
            and m['available_at'] <= item['record']['created_at'], 'bundled_anchor_changed')
    if support['status'] != 'retained':
        need(support['status'] in ('refused', 'incomplete') and isinstance(support['reason'], str)
            and support['reason'] and support['rgb_media_ref'] is None and support['timing'] is None,
            'bundled_refusal_invalid')
        raise ValueError('bundled_source_'+support['status']+'_'+support['reason'])
    need(set(refs) == set(ROLE_LIMITS)-{'support'}, 'bundled_retained_original_missing')
    depth, nv12, meta = decode_bundle(records['npz']['data'])
    calibration = _json(records['calibration_original']['data'])
    need(meta == support['source_metadata'] == records['bundle']['record']['metadata']['source_metadata']
        and records['npz']['data'] == records['bundle']['data']
        and calibration == records['bundle']['record']['metadata']['calibration']
        and calibration['session'] == meta['session'], 'bundled_calibration_or_component_changed')
    timing = mapped_timing(records['anchor_a']['record'], records['anchor_b']['record'], meta, support['receiver_clock'])
    need(timing == support['timing'], 'bundled_clock_derivation_changed')
    rgb = records['rgb']; m = rgb['record']['metadata']
    need(support['selected_bundled_frame_ref'] == dict(frame_id=m['frame_id'], media_ref=refs['rgb'])
        and m['frame_id'] != support['la_anchor_frame_ref']['frame_id'], 'bundled_selected_lineage_changed')
    need(rgb['record']['mime_type'] == 'image/png' and m.get('original_family') == FAMILY
        and m.get('nv12_sha256') == sha(nv12.tobytes(order='C'))
        and m.get('source_media_id') == records['bundle']['record']['id']
        and m.get('sequence') == meta['frames']['rgb']['sequence']
        and all(m.get(k) == timing[k] for k in ('observed_at', 'uncertainty_seconds', 'valid_until')),
        'bundled_derived_rgb_changed')
    from PIL import Image
    import numpy as np
    from room_mapper import nv12_to_rgb
    with Image.open(io.BytesIO(rgb['data'])) as image:
        need(image.format == 'PNG' and image.size == (640, 360) and getattr(image, 'n_frames', 1) == 1
            and np.array_equal(np.asarray(image.convert('RGB')), nv12_to_rgb(nv12)), 'bundled_rgb_pixels_changed')
    if event is not None:
        p = event['payload']
        need(frame == rgb['record'] and rgb['record']['id'] in p['media_ids']
            and p['frame_id'] == m['frame_id'] and p['capture_epoch'] == m['capture_epoch']
            and p['observed_at'] == m['observed_at'] and p['available_at'] >= support['created_at']
            and p['generation'] == binding['experiment_generation'], 'bundled_model_original_changed')
    return depth, meta, dict(calibration=calibration, association_created_at=support['created_at'])


class BundledRgbInputFactory:
    """Immutable camera configuration; each recording receives new state."""
    def __init__(self, store, *, site_id, camera_id, client, policy, anchor_tap=None):
        self.store, self.site_id, self.camera_id, self.client = store, site_id, camera_id, client
        self.policy, self.anchor_tap = globals()['policy'](policy), anchor_tap
        self.runtime = None

    def bind_runtime(self, runtime):
        need(self.runtime is None and runtime.store is self.store, 'bundled_same_store_required')
        self.runtime = runtime

    def new_session(self, session):
        need(self.runtime is not None and session['site_id'] == self.site_id
            and session['camera_id'] == self.camera_id and session['state'] in ('starting', 'recording'),
            'bundled_recording_session_required')
        source = BundledRgbInput(self.store, site_id=self.site_id, camera_id=self.camera_id,
            client=self.client, policy=self.policy, session_id=session['id'], anchor_tap=self.anchor_tap)
        source.bind_runtime(self.runtime)
        return source


class BundledRgbInput:
    def __init__(self, store, *, site_id, camera_id, client, policy, session_id, anchor_tap=None):
        self.store, self.site_id, self.camera_id, self.client = store, site_id, camera_id, client
        self.policy, self.session_id = globals()['policy'](policy), session_id
        self.anchor_tap = anchor_tap
        self.policy_sha256 = sha(encoded(self.policy)); self.runtime = None
        self.binding = None; self.previous = None; self.records = {}
        self.capture_count = self.retained_bytes = self.reserved_bytes = 0
        self.lock = threading.Lock(); self.stopped = threading.Event()
        self.incomplete = False; self.window_attempted = False; self.window_closed = False
        self.reason = 'idle'; self.clock = QualifiedReceiverClock()

    def bind_runtime(self, runtime):
        need(self.runtime is None and runtime.store is self.store, 'bundled_same_store_required')
        self.runtime = runtime

    def status(self):
        return dict(reason=self.reason, capture_count=self.capture_count, retained_bytes=self.retained_bytes,
            reserved_bytes=self.reserved_bytes,
            incomplete=self.incomplete, window_closed=self.window_closed, in_flight=self.lock.locked(),
            max_captures=self.policy['max_captures'], operational_geometry=None)

    def _current(self, *, expiry=None):
        b, r = self.binding, self.runtime
        need(r is not None and not r.closed and not self.stopped.is_set(), 'stop')
        s = self.store.get_session(b['session_id']); runner = r.runners.get(s['id'])
        pump = r.preview_pumps.get(s['id'])
        need(s['state'] == 'recording' and runner is not None and not runner.closed
            and pump is not None and not pump.stopped.is_set(), 'stop')
        need(s['generation'] == b['media_generation'] and runner.generation == b['experiment_generation'], 'generation_changed')
        now = self.store.clock(); need(finite(now), 'clock_refused')
        if expiry is not None: need(now < expiry, 'expired')
        return now

    def _charge(self, size):
        need(self.retained_bytes+size <= self.policy['max_retained_bytes'], 'capacity_exhausted')
        self.retained_bytes += size
        self.reserved_bytes = max(0, self.reserved_bytes-size)
        try: self.store.check_capacity(size)
        except BaseException:
            self.incomplete = True
            raise

    def _reserve(self, size):
        need(self.retained_bytes+size <= self.policy['max_retained_bytes'], 'capacity_exhausted')
        try: self.store.check_capacity(size)
        except BaseException:
            self.incomplete = True
            raise
        self.reserved_bytes = size

    def _sync(self, row):
        directory = self.store.media_path(self.binding['session_id'], row['id']).parent
        sync_directory(directory); sync_directory(directory.parent)

    def _add(self, data, mime, metadata, key):
        try:
            self._charge(len(data)+len(encoded(metadata))+65536)
            row = self.store.add_media(self.binding['session_id'], data, mime, metadata, key)
            self._sync(row)
            return row
        except BaseException:
            self.incomplete = True
            raise

    def _anchor(self, sid, jpeg, receipt):
        # New-family clock/LA anchors are intentionally absent from the graph's
        # stream='rgb' selection. Only C, decoded from the NPZ, is model RGB.
        from PIL import Image
        now = self.store.clock(); session = self.store.get_session(sid)
        metadata = anchor_metadata(receipt, site_id=self.site_id, camera_id=self.camera_id,
            available_at=now, started_at=session['started_at'])
        with Image.open(io.BytesIO(jpeg)) as image:
            need(image.format == 'JPEG' and image.size == (640, 360)
                and getattr(image, 'n_frames', 1) == 1, 'bundled_anchor_image_invalid')
            image.load()
        key = encoded([receipt['session'], FAMILY, ANCHOR_STREAM, receipt['timing_generation'], receipt['sequence']]).decode()
        row = self._add(jpeg, 'image/jpeg', metadata, key)
        return row, {**metadata, 'session_id':sid, 'media_ids':[row['id']]}

    def _window(self, reason):
        if self.window_attempted or self.binding is None: return
        self.window_attempted = True
        try:
            self._charge(65536)
            self.store.append_event(self.binding['session_id'], WINDOW_KIND, dict(schema=WINDOW_SCHEMA, version=1,
                binding=deepcopy(self.binding), capture_count=self.capture_count, max_captures=self.policy['max_captures'],
                reason=reason, closed_at=self.store.clock()), critical=True)
            self.window_closed = True
        except Exception:
            self.incomplete = True
            raise

    def stop(self):
        self.stopped.set()
        if self.lock.acquire(False):
            try: self._window('incomplete' if self.incomplete else 'stop')
            finally: self.lock.release()

    def ingest(self, sid, jpeg, header):
        need(self.lock.acquire(False), 'bundled_input_busy')
        support = started = identity = None
        try:
            need(sid == self.session_id, 'bundled_recording_session_changed')
            session = self.store.get_session(sid); runner = self.runtime.runners.get(sid)
            need(runner is not None and not runner.closed, 'stop')
            binding = dict(original_family=FAMILY, policy_sha256=self.policy_sha256, site_id=self.site_id,
                camera_id=self.camera_id, session_id=sid, media_generation=session['generation'], experiment_generation=runner.generation)
            need(self.binding is None or self.binding == binding, 'generation_changed')
            self.binding = binding
            receipt = _receipt(jpeg, header)
            identity = encoded([receipt['session'], receipt['timing_generation'], receipt['sequence']]).decode()
            fingerprint = sha(encoded([sha(jpeg), header]))
            if identity in self.records:
                old, frame = self.records[identity]
                need(old == fingerprint, 'bundled_original_identity_changed')
                self._current(expiry=None if frame is None else frame['valid_until'])
                return deepcopy(frame)
            self._current()
            need(not self.incomplete and not self.window_closed and self.capture_count < self.policy['max_captures'],
                'bundled_input_exhausted_or_incomplete')
            self.capture_count += 1; self.records[identity] = (fingerprint, None)
            # Complete raw+duplicate NPZ, RGB PNG, depth/left/right views, original
            # anchors and all bounded metadata. No Store lock spans HTTP/decode.
            self._reserve(2*ROLE_LIMITS['npz']+ROLE_LIMITS['rgb']+512000+2*(256000+4096)
                +12*65536+ROLE_LIMITS['calibration_original']+2*ROLE_LIMITS['anchor_b']+ROLE_LIMITS['support'])
            anchor, anchor_frame = self._anchor(sid, jpeg, receipt)
            capture_id = 'bundled-rgb-'+sha(encoded([binding, identity]))
            self._charge(65536)
            try:
                started = self.store.append_event(sid, START_KIND, dict(schema=START_SCHEMA, version=1, binding=binding,
                    capture_id=capture_id, anchor_b_media_ref=media_ref(anchor), anchor_b_header=header, started_at=self.store.clock()))
            except BaseException:
                self.incomplete = True
                raise
            support = dict(schema=SUPPORT_SCHEMA, version=1, binding=binding, capture_id=capture_id,
                started_event_id=started, anchors=dict(a=deepcopy(self.previous), b=dict(media_ref=media_ref(anchor), header=header)),
                npz_media_ref=None, calibration_media_ref=None, bundle_media_ref=None, rgb_media_ref=None,
                receiver_clock=None, source_metadata=None, timing=None, status='refused', reason='previous_anchor_unavailable',
                created_at=None, la_anchor_frame_ref=dict(frame_id=anchor_frame['frame_id'], media_ref=media_ref(anchor)),
                selected_bundled_frame_ref=None, la_anchor_tap=dict(status='not_requested', reason=None), **AUTHORITY)
            self._current(expiry=anchor_frame['valid_until'])
            if self.anchor_tap is not None:
                try:
                    accepted = self.anchor_tap(jpeg, header)
                    support['la_anchor_tap'] = (dict(status='refused', reason='optional_anchor_callback_refused')
                        if accepted is False else dict(status='succeeded', reason=None))
                except Exception:
                    support['la_anchor_tap'] = dict(status='failed', reason='optional_anchor_callback_failed')
                self._current(expiry=anchor_frame['valid_until'])
            if self.previous is None:
                self.previous = deepcopy(support['anchors']['b'])
                return None
            prior = self.store.get_media(sid, self.previous['media_ref']['id'], session['generation'])
            self._current(expiry=min(anchor_frame['valid_until'], prior['metadata']['valid_until']))
            try: clock_start = self.clock.begin(receipt)
            except Exception: raise ValueError('qualified_receiver_clock_unavailable') from None
            raw, _ = self.client._read('/frame.npz')
            try:
                proof = self.clock.finish(clock_start)
            except Exception:
                # Preserve returned original bytes even when clock qualification
                # failed; they cannot be offered as a timed observation.
                raw_row = self._add(raw, 'application/x-npz', dict(stream='bundled_rgb_raw_npz', candidate_only=True), capture_id+':raw')
                support['npz_media_ref'] = media_ref(raw_row)
                raise ValueError('qualified_receiver_clock_unavailable') from None
            raw_row = self._add(raw, 'application/x-npz', dict(stream='bundled_rgb_raw_npz', candidate_only=True), capture_id+':raw')
            support['npz_media_ref'] = media_ref(raw_row); support['receiver_clock'] = proof
            self._current()
            calibration_raw, _ = self.client._read('/calibration')
            calibration_row = self._add(calibration_raw, 'application/json', dict(stream='bundled_rgb_raw_calibration', candidate_only=True), capture_id+':cal')
            support['calibration_media_ref'] = media_ref(calibration_row)
            self._current()
            _, nv12, meta = decode_bundle(raw); support['source_metadata'] = meta
            calibration = _json(calibration_raw)
            need(calibration.get('session') == meta['session'] == receipt['session'], 'epoch_changed')
            timing = mapped_timing(prior, anchor, meta, proof)
            self._current(expiry=timing['valid_until'])
            need(timing['observed_at'] >= session['started_at'] and timing['observed_at'] <= self.store.clock(), 'bundled_clock_future_or_prerecording')
            self._charge(len(raw)+512000+2*(256000+4096)+6*65536)
            bundle = BundleIngestor(self.store, site_id=self.site_id, camera_id=self.camera_id).ingest(sid, raw, calibration)
            self._sync(bundle); support['bundle_media_ref'] = media_ref(bundle)
            from room_mapper import nv12_to_rgb
            from PIL import Image
            output = io.BytesIO(); Image.fromarray(nv12_to_rgb(nv12)).save(output, format='PNG', compress_level=1)
            png = output.getvalue(); need(len(png) <= ROLE_LIMITS['rgb'], 'bundled_png_exceeds_bound')
            self._current(expiry=timing['valid_until'])
            metadata = dict(site_id=self.site_id, camera_id=self.camera_id, capture_epoch=meta['session'],
                frame_id=f"{meta['session']}-bundle-rgb-{meta['frames']['rgb']['sequence']}",
                sequence=meta['frames']['rgb']['sequence'], timing_generation=receipt['timing_generation'],
                stream='rgb', format='png', width=640, height=360, original_family=FAMILY,
                source_media_id=bundle['id'], nv12_sha256=sha(nv12.tobytes(order='C')),
                observed_at=timing['observed_at'], available_at=self.store.clock(), valid_until=timing['valid_until'],
                uncertainty_seconds=timing['uncertainty_seconds'], clock_mapping_status='verified',
                clock_mapping_id='bundle-rgb-clock-1:'+sha(encoded([support['anchors'], proof, timing])),
                timestamp_semantics='same_stream_ordinal_qualified_receipt_bound', geometric_validity='unvalidated')
            rgb = self._add(png, 'image/png', metadata,
                encoded([meta['session'], FAMILY, receipt['timing_generation'], metadata['sequence']]).decode())
            support.update(rgb_media_ref=media_ref(rgb), timing=timing, status='retained', reason='retained',
                selected_bundled_frame_ref=dict(frame_id=metadata['frame_id'], media_ref=media_ref(rgb)))
            self._current(expiry=timing['valid_until'])
            frame = {**metadata, 'session_id':sid, 'media_ids':[rgb['id']]}
            self.records[identity] = (fingerprint, deepcopy(frame))
            self.previous = deepcopy(support['anchors']['b'])
            return frame
        except Exception as error:
            self.reason = str(error) if str(error).replace('_', '').isalnum() and len(str(error)) <= 100 else type(error).__name__
            if support is not None:
                support.update(status='refused', reason=self.reason, rgb_media_ref=None, timing=None,
                    selected_bundled_frame_ref=None)
            if self.reason in ('capacity_exhausted', 'stop', 'generation_changed') or not isinstance(error, ValueError):
                self.incomplete = True
            return None
        finally:
            try:
                if support is not None:
                    support['created_at'] = self.store.clock()
                    self.reason = support['reason']
                    row = self._add(encoded(support), 'application/json', dict(stream=SUPPORT_STREAM,
                        capture_id=support['capture_id'], status=support['status'], original_family=FAMILY), support['capture_id']+':support')
                    self._charge(65536)
                    self.store.append_event(sid, TERMINAL_KIND, dict(schema=TERMINAL_SCHEMA, version=1,
                        binding=self.binding, capture_id=support['capture_id'], started_event_id=started,
                        status=support['status'], reason=support['reason'], support_media_ref=media_ref(row), completed_at=self.store.clock()), critical=True)
                if self.incomplete or self.stopped.is_set() or self.capture_count >= self.policy['max_captures']:
                    self._window('incomplete' if self.incomplete else 'stop' if self.stopped.is_set() else 'exhausted')
            except Exception:
                self.incomplete = True
                if identity in self.records: self.records[identity] = (self.records[identity][0], None)
                raise
            finally:
                # Release unused allowance only; every actual/uncertain write
                # remains charged. Partial failure is sticky and never retried.
                self.reserved_bytes = 0
                self.lock.release()

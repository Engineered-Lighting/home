"""Finite private assessment tap over the incumbent camera-owner read routes.

Runs AFTER ordinary RGB admission/model offer. No capture/model/journal owner,
depth clock mapping, person association or operational geometry is created.
"""
import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
import math
import os
import threading

from bundle_ingest import BundleIngestor
from preview_client import LocalBundleClient
from recorder import validate_npz

POLICY_SCHEMA = 'home-lab-existing-api-assessment-policy-1'
START_KIND = 'assessment.capture.started'
START_SCHEMA = 'home-lab-existing-api-capture-start-1'
TERMINAL_KIND = 'assessment.capture.terminal'
TERMINAL_SCHEMA = 'home-lab-existing-api-capture-terminal-1'
WINDOW_KIND = 'assessment.window.closed'
WINDOW_SCHEMA = 'home-lab-existing-api-assessment-window-1'
ASSOCIATION_STREAM = 'assessment_existing_api_association'
ASSOCIATION_SCHEMA = 'home-lab-existing-api-association-1'
CALIBRATION_STREAM = 'assessment_existing_api_calibration_original'
UNVALIDATED_STREAM = 'assessment_existing_api_unvalidated_original'
MAX_NPZ = 4 * 1024**2
MAX_CALIBRATION = 24576
MAX_BYTES = 40 * 1024**2
AUTHORITY = dict(candidate_only=True, owner_local_only=True,
    operational_geometry=None, identity_authority=False,
    engagement_authority=False, protection_authority=False, beam_authority=False)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def need(value, reason):
    if not value:
        raise ValueError(reason)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _json(raw):
    def unique(pairs):
        found = {}
        for key, value in pairs:
            need(key not in found, 'refused')
            found[key] = value
        return found
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('refused')))


def policy(value):
    keys = {'schema', 'version', 'max_captures', 'max_npz_reads_per_capture', 'max_retained_bytes'}
    need(isinstance(value, dict) and set(value) == keys and value['schema'] == POLICY_SCHEMA
         and type(value['version']) is int and value['version'] == 1, 'invalid_assessment_policy')
    for key, maximum in (('max_captures', 24), ('max_npz_reads_per_capture', 2), ('max_retained_bytes', MAX_BYTES)):
        need(type(value[key]) is int and 0 < value[key] <= maximum, 'invalid_assessment_policy')
    return deepcopy(value)


def _receipt(jpeg, header):
    from home_shared.protocol import camera_frame
    need(isinstance(jpeg, bytes) and 0 < len(jpeg) <= 256 * 1024
         and isinstance(header, str) and 0 < len(header) <= 8192, 'refused')
    padded = header + '=' * ((-len(header)) % 4)
    camera_frame(jpeg, padded)
    result = _json(base64.b64decode(padded, altchars=b'-_', validate=True))
    need(result.get('schema') == 3, 'refused')
    return result


def validate_match(jpeg, original_header, bundle, calibration_raw):
    """Pure exact identity check; unknown error/clock bounds remain unknown.

    Uses installed bounded NPY-header validation before numpy allocation. This
    intentionally imports neither paired_preview_client nor Home acquisition.
    """
    receipt = _receipt(jpeg, original_header)
    need(isinstance(bundle, bytes) and 0 < len(bundle) <= MAX_NPZ
         and isinstance(calibration_raw, bytes) and 0 < len(calibration_raw) <= MAX_CALIBRATION, 'refused')
    calibration = _json(calibration_raw)
    need(isinstance(calibration, dict), 'refused')
    validate_npz(bundle, MAX_NPZ)
    import numpy as np
    with np.load(io.BytesIO(bundle), allow_pickle=False) as arrays:
        shapes = {'depth_mm': ((400, 640), 'uint16'), 'left': ((400, 640), 'uint8'),
                  'right': ((400, 640), 'uint8'), 'rgb_nv12': ((540, 640), 'uint8')}
        need(set(arrays.files) == set(shapes) | {'metadata_json'}, 'refused')
        for name, (shape, dtype) in shapes.items():
            need(arrays[name].shape == shape and arrays[name].dtype == np.dtype(dtype), 'refused')
        raw = arrays['metadata_json']
        need(raw.dtype == np.uint8 and raw.ndim == 1 and 0 < len(raw) <= 32768, 'refused')
        metadata = _json(raw.tobytes())
        nv12_sha = sha(arrays['rgb_nv12'].tobytes(order='C'))
    frames = metadata.get('frames', {})
    need(metadata.get('schema') == 1 and metadata.get('depth_alignment') == 'rectified_left'
         and metadata.get('depth_units') == 'millimetres' and metadata.get('invalid_depth') == 0
         and metadata.get('captured_unix_ns_semantics') == 'bundle_packaging_time_not_sensor_exposure'
         and set(frames) == {'rgb', 'depth', 'left', 'right'}, 'refused')
    need(metadata.get('session') == receipt['session'] == calibration.get('session'), 'epoch_changed')
    need(calibration.get('depth_alignment') == 'rectified_left'
         and calibration.get('depth_units') == 'millimetres', 'refused')
    for name, frame in frames.items():
        dimensions = (640, 360) if name == 'rgb' else (640, 400)
        need(isinstance(frame, dict) and type(frame.get('sequence')) is int and frame['sequence'] >= 0
             and finite(frame.get('device_timestamp_seconds')) and finite(frame.get('host_monotonic_timestamp_seconds'))
             and (frame.get('width'), frame.get('height')) == dimensions, 'refused')
    rgb = frames['rgb']
    need(rgb['sequence'] >= receipt['sequence'], 'behind')
    need(rgb['sequence'] <= receipt['sequence'], 'ahead')
    tolerance = receipt['timestamp_quantization_ns'] / 1e9 + 1e-6
    need(abs(rgb['device_timestamp_seconds'] - float(Decimal(receipt['frame_device_ns']) / Decimal(1000000000)))
         <= tolerance, 'device_mismatch')
    need(nv12_sha == receipt['preview_sha256'], 'nv12_mismatch')
    return {'receipt': receipt, 'source_metadata': metadata, 'calibration': calibration,
        'observed_at': float(Decimal(receipt['observed_at_unix_ns']) / Decimal(1000000000)),
        'fingerprint': sha(encoded([sha(jpeg), original_header, sha(bundle), sha(calibration_raw)])),
        'identity': encoded([receipt['session'], receipt['timing_generation'], receipt['sequence']]).decode()}


def media_ref(record):
    return {key: record[key] for key in ('id', 'generation', 'sha256', 'size')}


def sync_directory(path):
    """Namespace durability is required before any retained association ACK."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AssessmentExistingApiTap:
    """One finite, recording-bound observer installed by trusted composition.

    The incumbent tap wrapper swallows exceptions, so failure is latched here.
    Missing terminal/window receipts are incomplete evidence, never success.
    """
    def __init__(self, store, *, site_id, camera_id, client, policy):
        need(isinstance(site_id, str) and site_id and isinstance(camera_id, str) and camera_id
             and isinstance(client, LocalBundleClient), 'fixed_assessment_binding_required')
        self.store, self.site_id, self.camera_id, self.client = store, site_id, camera_id, client
        self.policy = globals()['policy'](policy)
        self.policy_sha256 = sha(encoded(self.policy))
        self.runtime = None
        self.binding = None
        self.capture_count = self.retained_bytes = 0
        self.state, self.reason = 'unbound', None
        self._lock = threading.Lock()
        self._records = {}
        self._terminal_attempts = set()
        self._last = self._last_original = None
        self._window_closed = False
        self._window_attempted = False

    def bind_runtime(self, runtime):
        with self._lock:
            need(self.runtime is None and runtime is not None and runtime.store is self.store,
                 'one_shot_runtime_binding_required')
            self.runtime = runtime

    def status(self):
        # Stop/status never waits behind a network call or fsync.
        acquired = self._lock.acquire(False)
        try:
            return dict(state=self.state, reason=self.reason, in_flight=not acquired,
                capture_count=self.capture_count, max_captures=self.policy['max_captures'],
                retained_bytes=self.retained_bytes, max_retained_bytes=self.policy['max_retained_bytes'],
                window_closed=self._window_closed, operational_geometry=None)
        finally:
            if acquired:
                self._lock.release()

    def _now(self):
        value = self.store.clock()
        need(finite(value), 'clock_refused')
        return value

    def _resolve(self, jpeg, header):
        receipt = _receipt(jpeg, header)
        need(self.runtime is not None, 'runtime_not_bound')
        with self.store.lock:
            rows = self.store.db.execute("SELECT id FROM sessions WHERE site_id=? AND camera_id=? AND state='recording'",
                (self.site_id, self.camera_id)).fetchall()
            need(len(rows) == 1, 'recording_unavailable')
            sid = rows[0][0]
            runner = self.runtime.runners.get(sid)
            # Startup admits capture before prepare/activate. No model output
            # exists for these RGBs; do not bind/poison the assessment window.
            if runner is None:
                return None
            if getattr(runner, 'closed', True):
                need(self.binding is None, 'stop')
                return None
            generation = getattr(runner, 'generation', None)
            row = self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if row is None or _json(row[0]).get('generation') != generation:
                return None
            session = self.store.get_session(sid)
            key = encoded([receipt['session'], 'rgb_preview', receipt['timing_generation'], receipt['sequence']]).decode()
            found = self.store.db.execute('SELECT id FROM media WHERE session_id=? AND source_key=?', (sid, key)).fetchall()
            need(len(found) == 1, 'admitted_rgb_unavailable')
            rgb = self.store.get_media(sid, found[0][0], session['generation'])
        need(type(generation) is int and generation > 0 and rgb['sha256'] == sha(jpeg)
             and rgb['size'] == len(jpeg) and rgb['mime_type'] == 'image/jpeg', 'admitted_rgb_changed')
        meta = rgb['metadata']
        need(meta.get('site_id') == self.site_id and meta.get('camera_id') == self.camera_id
             and meta.get('stream') == 'rgb' and meta.get('source_timing_receipt') == receipt
             and meta.get('capture_epoch') == receipt['session']
             and meta.get('sequence') == receipt['sequence']
             and meta.get('timing_generation') == receipt['timing_generation']
             and all(finite(meta.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
             and meta['observed_at'] == float(Decimal(receipt['observed_at_unix_ns']) / Decimal(1000000000))
             and meta['observed_at'] <= meta['available_at'] < meta['valid_until'], 'admitted_rgb_changed')
        binding = dict(policy_sha256=self.policy_sha256, site_id=self.site_id, camera_id=self.camera_id,
            session_id=sid, media_generation=session['generation'], experiment_generation=generation)
        identity = [receipt['session'], receipt['timing_generation'], receipt['sequence']]
        capture = dict(capture_id='assessment-' + sha(encoded([binding, identity])),
            rgb_frame_id=meta['frame_id'], capture_epoch=receipt['session'], rgb_media_ref=media_ref(rgb),
            rgb_header=header, rgb_times={k:meta[k] for k in ('observed_at', 'available_at', 'valid_until')})
        return binding, capture, receipt

    def _current(self, capture, *, expiry=True):
        b, runtime = self.binding, self.runtime
        need(runtime is not None and not runtime.closed, 'stop')
        pump = runtime.preview_pumps.get(b['session_id'])
        need(pump is not None and not pump.stopped.is_set(), 'stop')
        session = self.store.get_session(b['session_id'])
        need(session['state'] == 'recording', 'stop')
        runner = runtime.runners.get(b['session_id'])
        need(runner is not None and not getattr(runner, 'closed', True), 'stop')
        need(session['site_id'] == b['site_id'] and session['camera_id'] == b['camera_id']
             and session['generation'] == b['media_generation'] and runner is not None
             and runner.generation == b['experiment_generation'], 'generation_changed')
        now = self._now()
        need(now >= capture['rgb_times']['available_at'], 'clock_refused')
        if expiry:
            need(now < capture['rgb_times']['valid_until'], 'expired')
        return now

    def _charge(self, amount):
        need(type(amount) is int and amount >= 0
             and self.retained_bytes + amount <= self.policy['max_retained_bytes'], 'capacity_exhausted')
        # Conservative closure accounting includes metadata/ledger headroom.
        # Never refund a reservation after partial/uncertain durable writes.
        self.retained_bytes += amount
        self.store.check_capacity(amount)

    def _raw(self, capture, ordinal, family, data, received_at):
        self._current(capture, expiry=False)
        need(isinstance(data, bytes) and 0 < len(data) <= (MAX_NPZ if family == 'npz' else MAX_CALIBRATION), 'refused')
        meta = dict(site_id=self.site_id, camera_id=self.camera_id, capture_epoch=capture['capture_epoch'],
            stream=UNVALIDATED_STREAM if family == 'npz' else CALIBRATION_STREAM,
            capture_id=capture['capture_id'], ordinal=ordinal, read_completed_at=received_at,
            format='unvalidated_npz' if family == 'npz' else 'raw_json',
            clock_mapping_status='unverified', candidate_only=True)
        self._charge(len(data) + len(encoded(meta)) + 65536)
        row = self.store.add_media(self.binding['session_id'], data,
            'application/x-npz' if family == 'npz' else 'application/json', meta,
            'assessment-raw-' + sha(encoded([capture['capture_id'], ordinal, family])))
        self._sync_media(row)
        self._current(capture, expiry=False)
        return row

    def _sync_media(self, row):
        parent = self.store.media_path(self.binding['session_id'], row['id']).parent
        sync_directory(parent)
        # Also retain the recording-directory entry created by the original
        # RGB writer; fsyncing only that directory doesn't persist its name.
        sync_directory(parent.parent)

    def _close_window(self, reason):
        if self._window_attempted or self.binding is None or self._last_original is None:
            return
        payload = dict(schema=WINDOW_SCHEMA, version=1, binding=deepcopy(self.binding),
            last_capture_id=self._last, capture_count=self.capture_count,
            max_captures=self.policy['max_captures'], retained_bytes=self.retained_bytes,
            max_retained_bytes=self.policy['max_retained_bytes'], last_original=deepcopy(self._last_original),
            reason=reason, closed_at=self._now())
        self._window_attempted = True
        self.store.append_event(self.binding['session_id'], WINDOW_KIND, payload, critical=True)
        self._window_closed = True

    def __call__(self, jpeg, original_header):
        if not self._lock.acquire(False):
            return None
        capture = binding = None
        started = None
        attempts = []
        try:
            # No I/O of any kind follows a retired/failed window. Exact cached
            # retry remains available but never refreshes receipt timestamps.
            receipt = _receipt(jpeg, original_header)
            if self.binding is not None:
                cid = 'assessment-' + sha(encoded([self.binding,
                    [receipt['session'], receipt['timing_generation'], receipt['sequence']]]))
                cached = self._records.get(cid)
                if cached is not None:
                    need(cached[0] == sha(encoded([sha(jpeg), original_header])), 'capture_identity_changed')
                    return deepcopy(cached[1])
            if self.state in {'incomplete', 'exhausted', 'closed'}:
                return None
            resolved = self._resolve(jpeg, original_header)
            if resolved is None:
                return None
            binding, capture, receipt = resolved
            need(self.binding is None or self.binding == binding, 'generation_changed')
            if self._last_original is not None:
                need((receipt['session'], receipt['timing_generation']) ==
                     (self._last_original['capture_epoch'], self._last_original['timing_generation']), 'epoch_changed')
            self.binding = deepcopy(binding)
            self._current(capture)
            need(self.capture_count < self.policy['max_captures'], 'capacity_exhausted')
            self.state = 'active'
            self.capture_count += 1
            self._last = capture['capture_id']
            self._last_original = dict(capture_epoch=receipt['session'],
                timing_generation=receipt['timing_generation'], sequence=receipt['sequence'])
            start = dict(schema=START_SCHEMA, version=1, binding=deepcopy(binding),
                capture=deepcopy(capture), tap_started_at=self._now())
            self._charge(len(jpeg) + len(encoded(start)) + 65536)
            self._records[capture['capture_id']] = (sha(encoded([sha(jpeg), original_header])), None)
            started = self.store.append_event(binding['session_id'], START_KIND, start)
            association_ref = None
            reason = 'refused'
            for ordinal in range(1, self.policy['max_npz_reads_per_capture'] + 1):
                attempt = dict(ordinal=ordinal, npz_media_ref=None, calibration_media_ref=None,
                    npz_read_completed_at=None, calibration_read_completed_at=None, result='refused')
                attempts.append(attempt)
                self._current(capture)
                # Full possible next raw object must have actual Store headroom.
                self.store.check_capacity(MAX_NPZ + 65536)
                try:
                    raw, _ = self.client._read('/frame.npz')
                except Exception:
                    reason = attempt['result'] = 'http_unavailable'
                    break
                attempt['npz_read_completed_at'] = self._now()
                original = self._raw(capture, ordinal, 'npz', raw, attempt['npz_read_completed_at'])
                attempt['npz_media_ref'] = media_ref(original)
                self._current(capture)
                try:
                    calibration_raw, _ = self.client._read('/calibration')
                except Exception:
                    reason = attempt['result'] = 'http_unavailable'
                    break
                attempt['calibration_read_completed_at'] = self._now()
                calibration_original = self._raw(capture, ordinal, 'calibration', calibration_raw,
                    attempt['calibration_read_completed_at'])
                attempt['calibration_media_ref'] = media_ref(calibration_original)
                self._current(capture)
                try:
                    checked = validate_match(jpeg, original_header, raw, calibration_raw)
                except Exception as error:
                    reason = str(error) if str(error) in {'behind', 'ahead', 'epoch_changed', 'device_mismatch', 'nv12_mismatch'} else 'refused'
                    attempt['result'] = reason
                    if reason == 'behind' and ordinal < self.policy['max_npz_reads_per_capture']:
                        continue
                    break
                # Include duplicate valid NPZ, lossless depth/PNG views and all
                # metadata/association headroom. Decode runs outside Store lock;
                # every unchanged Store write checks actual quota independently.
                self._charge(len(raw) + 512000 + 2 * (256000 + 4096) + 6 * 65536)
                self._current(capture)
                bundle = BundleIngestor(self.store, site_id=self.site_id, camera_id=self.camera_id).ingest(
                    binding['session_id'], raw, checked['calibration'])
                self._sync_media(bundle)
                self._current(capture)
                attempt['npz_media_ref'] = media_ref(bundle)
                attempt['result'] = 'exact'
                association = dict(schema=ASSOCIATION_SCHEMA, version=1, binding=deepcopy(binding),
                    capture=deepcopy(capture), started_event_id=started, selected_attempt_ordinal=ordinal,
                    bundle_media_ref=media_ref(bundle), calibration_original_media_ref=media_ref(calibration_original),
                    source_metadata=checked['source_metadata'], calibration_sha256=sha(encoded(checked['calibration'])),
                    association_created_at=self._now(),
                    depth_clock=dict(status='unassessed', mapping=None, exposure_error_bound_s=None), **AUTHORITY)
                self._current(capture)
                original = self.store.add_media(binding['session_id'], encoded(association), 'application/json',
                    dict(stream=ASSOCIATION_STREAM, site_id=self.site_id, camera_id=self.camera_id,
                         capture_id=capture['capture_id'], frame_id=capture['rgb_frame_id'], rgb_media_id=capture['rgb_media_ref']['id']),
                    'assessment-association-' + capture['capture_id'])
                self._sync_media(original)
                self._current(capture)
                association_ref = media_ref(original)
                reason = 'exact'
                break
            terminal = self._terminal(capture, started, attempts,
                'retained' if association_ref is not None else 'miss', reason, association_ref)
            if self.capture_count == self.policy['max_captures']:
                self.state = 'exhausted'
                self._close_window('exhausted')
            return terminal
        except Exception as error:
            reason = str(error) if str(error) in {'stop', 'expired', 'generation_changed', 'epoch_changed',
                'capacity_exhausted', 'capture_identity_changed', 'clock_refused', 'recording_unavailable'} else 'retention_unconfirmed'
            self.reason = reason
            # Expiry is a measured miss only when all existing durable writes
            # are confirmed. Stop/generation, quota and unknown ACK are incomplete.
            measured_expiry = reason == 'expired' and capture is not None and started is not None
            self.state = 'active' if measured_expiry else 'incomplete'
            if attempts and attempts[-1]['result'] == 'refused':
                attempts[-1]['result'] = 'expired' if measured_expiry else 'refused'
            terminal = None
            if capture is not None and started is not None and capture['capture_id'] not in self._terminal_attempts:
                try:
                    terminal = self._terminal(capture, started, attempts,
                        'miss' if measured_expiry else 'incomplete', reason, None)
                except Exception:
                    self.state, self.reason = 'incomplete', 'retention_unconfirmed'
            if self.state == 'incomplete' or self.capture_count >= self.policy['max_captures']:
                if measured_expiry and self.state != 'incomplete':
                    self.state = 'exhausted'
                try:
                    self._close_window('stop' if reason == 'stop' else ('incomplete' if self.state == 'incomplete' else 'exhausted'))
                except Exception:
                    self.state, self.reason = 'incomplete', 'retention_unconfirmed'
            return terminal
        finally:
            self._lock.release()

    def _terminal(self, capture, started, attempts, status, reason, association_ref):
        payload = dict(schema=TERMINAL_SCHEMA, version=1, binding=deepcopy(self.binding),
            capture_id=capture['capture_id'], started_event_id=started, attempts=deepcopy(attempts),
            status=status, reason=reason, association_media_ref=deepcopy(association_ref),
            processing_completed_at=self._now())
        # An exception can mean a durable append with lost ACK. Never append a
        # different settlement or manufacture a confirmed retry in that case.
        need(capture['capture_id'] not in self._terminal_attempts, 'retention_unconfirmed')
        self._terminal_attempts.add(capture['capture_id'])
        self.store.append_event(self.binding['session_id'], TERMINAL_KIND, payload, critical=True)
        self._records[capture['capture_id']] = (self._records[capture['capture_id']][0], deepcopy(payload))
        return payload

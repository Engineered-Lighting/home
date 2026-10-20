#!/usr/bin/env python3
"""One OAK owner: RGB H264 RTSP and authenticated latest lossless RGBD bundles.

GET /health, /calibration, /frame.npz, /frame.jpg. LAN requests require Authorization:
Bearer $VICTORIA_CAMERA_TOKEN. NPZ contains depth_mm:uint16, left/right:uint8,
rgb_nv12:uint8 and metadata_json:uint8 UTF-8 JSON. Never use allow_pickle=True.
Depth is aligned to the LEFT rectified camera, NOT to RGB. EEPROM calibration
and output dimensions permit consumers to project it. RGB is NV12 640x360.
Each response is latest-only, not a recording; stale frames return HTTP 503.
"""
import argparse
import base64
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import math
import os
import queue
import re
import signal
import subprocess
import stat
import sys
import threading
import time
import uuid


JPEG_WIDTH, JPEG_HEIGHT = 640, 360
JPEG_MAX_BYTES = 256 * 1024
CAUSAL_SEMANTICS = 'depthai_device_clock_exposure_end_v1'
CAUSAL_DEADLINE_SECONDS = 2


def _unique_json(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate timing JSON field')
        result[name] = value
    return result


def decode_timing_json(raw):
    if not isinstance(raw, bytes) or len(raw) > 8192:
        raise ValueError('Timing input exceeds bounds')
    def reject_constant(_):
        raise ValueError('Nonfinite timing JSON')
    return json.loads(raw, object_pairs_hook=_unique_json, parse_constant=reject_constant)


def read_protected_json(path, owner_uid):
    """Read a small installed input; never follow a writable/symlink path."""
    from pathlib import Path
    if sys.platform != 'linux' or type(owner_uid) is not int or owner_uid < 0:
        raise ValueError('Explicit Linux evidence owner required')
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute installed timing path required')
    for directory in reversed(path.parents):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner_uid)
                or info.st_mode & 0o022):
            raise ValueError('Unprotected timing directory')
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner_uid
            or before.st_mode & 0o022 or before.st_nlink != 1):
        raise ValueError('Unprotected timing file')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        actual = os.fstat(source.fileno())
        if (actual.st_dev, actual.st_ino, actual.st_uid, actual.st_mode, actual.st_nlink) != (
                before.st_dev, before.st_ino, before.st_uid, before.st_mode, before.st_nlink):
            raise ValueError('Timing file changed while opening')
        raw = source.read(8193)
    return decode_timing_json(raw)


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is not None


@dataclass(frozen=True)
class CausalConfig:
    version: int
    semantics: str
    depthai_version: str
    device_id: str
    validation_id: str
    timestamp_quantization_ns: int

    def __post_init__(self):
        if (type(self.version) is not int or self.version != 1 or self.semantics != CAUSAL_SEMANTICS
                or self.depthai_version != '3.10.0' or not _identity(self.device_id)
                or not _identity(self.validation_id) or type(self.timestamp_quantization_ns) is not int
                or not 1000 <= self.timestamp_quantization_ns <= 1_000_000):
            raise ValueError('Commissioned pinned device semantics required')


@dataclass(frozen=True)
class HostClockEvidence:
    version: int
    semantics: str
    host_boot_id: str
    validation_id: str
    checked_monotonic_ns: int
    checked_unix_ns: int
    valid_until_monotonic_ns: int
    uncertainty_seconds: float

    def __post_init__(self):
        if not isinstance(self.host_boot_id, str) or str(uuid.UUID(self.host_boot_id)) != self.host_boot_id:
            raise ValueError('Current kernel boot UUID required')
        if (type(self.version) is not int or self.version != 1
                or self.semantics != 'commissioned_camera_host_clock_v1'
                or not _identity(self.host_boot_id) or not _identity(self.validation_id)
                or any(type(value) is not int or value < 0 for value in (
                    self.checked_monotonic_ns, self.checked_unix_ns, self.valid_until_monotonic_ns))
                or self.checked_unix_ns == 0
                or not 0 < self.valid_until_monotonic_ns - self.checked_monotonic_ns <= 5_000_000_000
                or isinstance(self.uncertainty_seconds, bool)
                or not isinstance(self.uncertainty_seconds, (int, float))
                or not math.isfinite(self.uncertainty_seconds) or not 0 <= self.uncertainty_seconds <= 1):
            raise ValueError('Fresh commissioned host clock evidence required')


@dataclass(frozen=True)
class HostPoint:
    before_ns: int
    boot_ns: int
    unix_ns: int
    after_ns: int

    def __post_init__(self):
        if (any(type(value) is not int or value < 0 for value in (
                self.before_ns, self.boot_ns, self.unix_ns, self.after_ns))
                or not 0 <= self.after_ns - self.before_ns <= 10_000_000 or self.unix_ns == 0):
            raise ValueError('Invalid or delayed host bracket')


def host_point():
    # BOOTTIME includes suspend; its offset from monotonic must stay compatible
    # with all bracketing intervals, including the post-encoding interval.
    return HostPoint(time.monotonic_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                     time.time_ns(), time.monotonic_ns())


def check_host_clock(evidence, point, boot_id):
    if (not isinstance(evidence, HostClockEvidence) or evidence.host_boot_id != boot_id
            or not evidence.checked_monotonic_ns <= point.before_ns <= point.after_ns < evidence.valid_until_monotonic_ns):
        raise ValueError('Expired or wrong-boot host clock')
    lower = evidence.checked_unix_ns + point.before_ns - evidence.checked_monotonic_ns
    upper = evidence.checked_unix_ns + point.after_ns - evidence.checked_monotonic_ns
    radius = evidence.uncertainty_seconds * 1e9
    if not lower - radius <= point.unix_ns <= upper + radius:
        raise ValueError('Host wall clock changed')


def check_no_suspend(*points):
    if (any((later != earlier and later.before_ns < earlier.after_ns) or later.boot_ns < earlier.boot_ns
            for earlier, later in zip(points, points[1:]))
            or max(point.boot_ns - point.after_ns for point in points)
            > min(point.boot_ns - point.before_ns for point in points)):
        raise ValueError('Host suspend or clock discontinuity')


def timestamp_ns(value):
    if not isinstance(value, timedelta) or value < timedelta(0):
        raise ValueError('Device timestamp unavailable')
    return ((value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds) * 1000


# Script runs on the same OAK as the existing camera. Only small nonce/clock
# messages cross this new channel; the existing diagnostic RGB queue supplies
# the selected immutable preview. Clock.now is raw device time in Script:
# https://docs.luxonis.com/software-v3/depthai/depthai-components/nodes/script
# END is the pinned ImgFrame default, not host-synchronized getTimestamp():
# https://github.com/luxonis/depthai-core/blob/v3.10.0/src/pipeline/datatype/ImgFrame.cpp
CAUSAL_SCRIPT = '''
import json
while True:
    message = node.io['challenge'].get()
    data = bytes(message.getData())
    if len(data) != 32:
        continue
    nonce = data.decode('ascii')
    if any(c not in '0123456789abcdef' for c in nonce):
        continue
    sampled = Clock.now()
    device_ns = ((sampled.days * 86400 + sampled.seconds) * 1000000 + sampled.microseconds) * 1000
    payload = json.dumps({'nonce': nonce, 'device_ns': device_ns}).encode('ascii')
    receipt = Buffer(len(payload))
    receipt.setData(payload)
    node.io['receipt'].send(receipt)
'''


def sample_clock_pair(source_now, *, monotonic_ns=time.monotonic_ns, wall_ns=time.time_ns):
    """Raw bracketed host readings; never a source-sync or UTC-error receipt."""
    source_before = monotonic_ns()
    source_stamp = source_now()
    source_after = monotonic_ns()
    wall_before = monotonic_ns()
    unix_ns = wall_ns()
    wall_after = monotonic_ns()
    if not isinstance(source_stamp, timedelta):
        raise ValueError('SDK clock must return a timedelta')
    source_ns = ((source_stamp.days * 86400 + source_stamp.seconds) * 1_000_000
                 + source_stamp.microseconds) * 1000
    source_seconds = source_stamp.total_seconds()
    stamps = (source_before, source_after, wall_before, wall_after)
    if (any(type(value) is not int or value < 0 for value in stamps)
            or tuple(sorted(stamps)) != stamps or wall_after - source_before > 50_000_000
            or isinstance(source_seconds, bool) or not isinstance(source_seconds, (float, int))
            or not math.isfinite(source_seconds) or source_seconds < 0
            or type(unix_ns) is not int or unix_ns <= 0):
        raise ValueError('Invalid or delayed clock sample')
    return {'source_before_mono_ns': source_before, 'source_after_mono_ns': source_after,
            'source_seconds': source_seconds, 'source_ns': source_ns,
            'source_quantization_ns': 1000, 'wall_before_mono_ns': wall_before,
            'wall_after_mono_ns': wall_after, 'unix_ns': unix_ns}


def clock_offset_intervals(samples):
    """Measured offset intervals under a constant-offset hypothesis only.

    Disjoint intervals indicate that hypothesis does not fit these readings;
    they do not distinguish a step from drift or establish any UTC accuracy.
    No configured or inferred drift allowance is promoted into evidence.
    """
    if not samples:
        return None
    source_intervals = [(s['source_ns'] - s['source_after_mono_ns'] - s['source_quantization_ns'],
                         s['source_ns'] - s['source_before_mono_ns'] + s['source_quantization_ns'])
                        for s in samples]
    wall_intervals = [(s['unix_ns'] - s['wall_after_mono_ns'],
                       s['unix_ns'] - s['wall_before_mono_ns']) for s in samples]
    def intersect(intervals):
        lower, upper = max(i[0] for i in intervals), min(i[1] for i in intervals)
        return [lower, upper] if lower <= upper else None
    return {'hypothesis': 'constant_offset_no_drift_allowance',
            'source_offset_ns': intersect(source_intervals),
            'wall_offset_ns': intersect(wall_intervals),
            'validated_clock_accuracy': False}


@dataclass(frozen=True)
class Preview:
    session: str
    sequence: int
    source_timestamp_seconds: float
    received_monotonic_seconds: float
    nv12: bytes
    exposure_duration_seconds: float | None = None
    timing_generation: int = 0


@dataclass(frozen=True)
class CausalFrame:
    preview: Preview
    nonce: str
    challenge_device_ns: int
    frame_device_ns: int
    sent: HostPoint
    received: HostPoint
    timestamp_quantization_ns: int
    preview_sha256: str
    host_clock: HostClockEvidence


def causal_timing(frame, point, boot_id):
    """Bound just this exposure with causality; never extrapolate to a session."""
    if not isinstance(frame, CausalFrame):
        raise ValueError('Exact-frame receipt required')
    preview, clock = frame.preview, frame.host_clock
    check_no_suspend(frame.sent, frame.received, point)
    for sampled in (frame.sent, frame.received, point):
        check_host_clock(clock, sampled, boot_id)
    if (re.fullmatch(r'[a-f0-9]{32}', frame.nonce) is None
            or any(type(value) is not int or value < 0 for value in (
                frame.challenge_device_ns, frame.frame_device_ns, frame.timestamp_quantization_ns,
                preview.sequence, preview.timing_generation))
            or not 1000 <= frame.timestamp_quantization_ns <= 1_000_000
            or frame.frame_device_ns <= frame.challenge_device_ns + frame.timestamp_quantization_ns
            or not isinstance(preview.nv12, bytes) or len(preview.nv12) != JPEG_WIDTH * JPEG_HEIGHT * 3 // 2
            or hashlib.sha256(preview.nv12).hexdigest() != frame.preview_sha256
            or preview.source_timestamp_seconds != frame.frame_device_ns / 1e9
            or preview.received_monotonic_seconds != frame.received.after_ns / 1e9):
        raise ValueError('Causal frame identity or bytes mismatch')
    exposure = preview.exposure_duration_seconds
    if (isinstance(exposure, bool) or not isinstance(exposure, (int, float))
            or not math.isfinite(exposure) or exposure <= 0):
        raise ValueError('Exposure duration unavailable')
    h0, h1 = frame.sent.before_ns, frame.received.after_ns
    midpoint = (h0 + h1) / 2e9
    uncertainty = ((h1 - h0) / 2e9 + exposure + frame.timestamp_quantization_ns / 1e9
                   + clock.uncertainty_seconds)
    if (not 0 < uncertainty <= 1 or h1 - h0 > CAUSAL_DEADLINE_SECONDS * 1e9
            or not 0 <= point.after_ns / 1e9 - midpoint <= 3 - uncertainty):
        raise ValueError('Causal frame is stale or uncertainty exceeds budget')
    observed_unix = clock.checked_unix_ns + round((h0 + h1) / 2 - clock.checked_monotonic_ns)
    if not 0 <= (point.unix_ns - observed_unix) / 1e9 <= 3 - uncertainty:
        raise ValueError('Wall observation age outside bounds')
    return {'schema': 3, 'session': preview.session, 'sequence': preview.sequence,
            'timing_generation': preview.timing_generation, 'width': JPEG_WIDTH, 'height': JPEG_HEIGHT,
            'quality': 85, 'source_clock': 'depthai_device_monotonic',
            'monotonic_clock_scope': 'python_monotonic_on_camera_host',
            'timestamp_semantics': 'causal_exact_frame_exposure_end',
            'challenge_nonce': frame.nonce, 'challenge_device_ns': frame.challenge_device_ns,
            'frame_device_ns': frame.frame_device_ns, 'host_send_monotonic_ns': h0,
            'host_receive_monotonic_ns': h1, 'timestamp_quantization_ns': frame.timestamp_quantization_ns,
            'preview_sha256': frame.preview_sha256, 'observed_monotonic_seconds': midpoint,
            'observed_at_unix_ns': observed_unix, 'uncertainty_seconds': uncertainty,
            'validation_id': 'causal:' + frame.nonce, 'exposure_duration_seconds': exposure,
            'host_boot_id': clock.host_boot_id, 'host_clock_validation_id': clock.validation_id,
            'host_clock_uncertainty_seconds': clock.uncertainty_seconds}


def preview_timing(preview, mapping, now, unix_ns):
    """Apply a commissioned source-clock map; never use bundle packaging time.

    A map is deliberately session-bound and short-lived. Its validation receipt
    must establish source timestamp semantics and the end-to-end uncertainty,
    including camera synchronization, host clock offset and exposure offset.
    Merely measuring HTTP receipt or packaging time cannot establish this map.
    """
    if not isinstance(mapping, dict):
        raise ValueError('Clock mapping must be an object')
    if (mapping.get('session') != preview.session or
            type(mapping.get('generation', 0)) is not int or
            mapping.get('generation', 0) != preview.timing_generation or
            mapping.get('source_clock') != 'depthai_host_monotonic' or
            mapping.get('semantics') != 'validated_rgb_observation' or
            not isinstance(mapping.get('validation_id'), str) or
            not mapping['validation_id'].strip()):
        raise ValueError('Unvalidated preview clock mapping')
    names = ('source_reference_seconds', 'host_reference_seconds',
             'uncertainty_seconds', 'valid_until_monotonic_seconds')
    if any(isinstance(mapping.get(n), bool) or
           not isinstance(mapping.get(n), (int, float)) or
           not math.isfinite(mapping[n]) for n in names):
        raise ValueError('Invalid clock mapping bounds')
    unix_reference = mapping.get('unix_reference_ns')
    if type(unix_reference) is not int or unix_reference <= 0:
        raise ValueError('Invalid UNIX reference')
    reference = mapping['host_reference_seconds']
    uncertainty = mapping['uncertainty_seconds']
    exposure = preview.exposure_duration_seconds
    if (isinstance(exposure, bool) or not isinstance(exposure, (int, float))
            or not math.isfinite(exposure) or not 0 < exposure <= uncertainty):
        raise ValueError('Missing exposure interval or insufficient mapping uncertainty')
    if (not 0 <= uncertainty <= 1 or not reference <= now < mapping['valid_until_monotonic_seconds']
            or not 0 < mapping['valid_until_monotonic_seconds'] - reference <= 300):
        raise ValueError('Expired or unbounded clock mapping')
    if not math.isfinite(preview.source_timestamp_seconds):
        raise ValueError('Invalid source timestamp')
    source_delta = preview.source_timestamp_seconds - mapping['source_reference_seconds']
    observed_monotonic = reference + source_delta
    age = now - observed_monotonic
    # Include uncertainty in the conservative age bound and reject future data.
    if (age < -uncertainty or age + uncertainty > 3 or
            preview.received_monotonic_seconds > now or
            observed_monotonic - preview.received_monotonic_seconds > uncertainty):
        raise ValueError('Preview is stale or from the future')
    mapped_now = unix_reference + round((now - reference) * 1_000_000_000)
    if abs(unix_ns - mapped_now) / 1_000_000_000 > uncertainty:
        raise ValueError('Host wall clock changed after validation')
    return {
        'schema': 2, 'session': preview.session, 'sequence': preview.sequence,
        'width': JPEG_WIDTH, 'height': JPEG_HEIGHT, 'quality': 85,
        'source_clock': 'depthai_host_monotonic',
        'source_timestamp_seconds': preview.source_timestamp_seconds,
        'observed_monotonic_seconds': observed_monotonic,
        'monotonic_clock_scope': 'python_monotonic_on_camera_host',
        'observed_at_unix_ns': unix_reference + round(source_delta * 1_000_000_000),
        'timestamp_semantics': 'commissioned_source_frame_timestamp_mapping',
        'uncertainty_seconds': uncertainty, 'validation_id': mapping['validation_id'],
        'timing_generation': preview.timing_generation,
        'exposure_duration_seconds': preview.exposure_duration_seconds,
    }


def encode_preview(nv12):
    """Encode the fixed preview in the request worker, never the capture loop."""
    import numpy as np
    from PIL import Image
    if len(nv12) != JPEG_WIDTH * JPEG_HEIGHT * 3 // 2:
        raise ValueError('Invalid NV12 preview size')
    raw = np.frombuffer(nv12, dtype=np.uint8)
    y = raw[:JPEG_WIDTH * JPEG_HEIGHT].reshape(JPEG_HEIGHT, JPEG_WIDTH).astype(np.int32) - 16
    uv = raw[JPEG_WIDTH * JPEG_HEIGHT:].reshape(JPEG_HEIGHT // 2, JPEG_WIDTH // 2, 2)
    u = uv[:, :, 0].repeat(2, 0).repeat(2, 1).astype(np.int32) - 128
    v = uv[:, :, 1].repeat(2, 0).repeat(2, 1).astype(np.int32) - 128
    # BT.601 limited-range NV12, explicit rather than accepting arbitrary images.
    rgb = np.stack(((298 * y + 409 * v + 128) >> 8,
                    (298 * y - 100 * u - 208 * v + 128) >> 8,
                    (298 * y + 516 * u + 128) >> 8), axis=2).clip(0, 255).astype(np.uint8)
    output = io.BytesIO()
    Image.fromarray(rgb).save(output, format='JPEG', quality=85)
    data = output.getvalue()
    if len(data) > JPEG_MAX_BYTES:
        raise ValueError('Encoded JPEG exceeds byte limit')
    return data


def encode_bundle(arrays, metadata):
    import numpy as np
    if arrays['depth_mm'].dtype != np.uint16:
        raise ValueError('Depth must remain uint16 metric data')
    output = io.BytesIO()
    np.savez_compressed(output, **arrays, metadata_json=np.frombuffer(
        json.dumps(metadata, separators=(',', ':')).encode(), dtype=np.uint8))
    return output.getvalue()


class State:
    def __init__(self, stale_seconds, jpeg_clock_map=None, *, causal_config=None,
                 host_clock_path=None, evidence_uid=None, _boot_id=None, _point=host_point):
        self.lock = threading.Lock()
        self.jpeg_lock = threading.Lock()
        self.jpeg_clock_map = jpeg_clock_map
        self.causal_config = causal_config
        self.host_clock_path, self.evidence_uid, self.causal_point = host_clock_path, evidence_uid, _point
        self.causal_ready = threading.Event()
        self.host_boot_id = None
        if causal_config is not None:
            if (not isinstance(causal_config, CausalConfig) or jpeg_clock_map or not host_clock_path
                    or type(evidence_uid) is not int or evidence_uid < 0):
                raise ValueError('Explicit mutually exclusive causal commissioning inputs required')
            if _boot_id is None:
                with open('/proc/sys/kernel/random/boot_id', encoding='ascii') as source:
                    _boot_id = source.read(38).strip()
            if str(uuid.UUID(_boot_id)) != _boot_id:
                raise ValueError('Current kernel boot identity required')
            self.host_boot_id = _boot_id
        self.stale_seconds = stale_seconds
        self.reset()

    def reset(self):
        with self.lock:
            self.session = str(uuid.uuid4())
            self.bundle = None
            self.preview = None
            self.jpeg_cache = None
            self.updated = 0.0
            self.encoded_updated = 0.0
            self.calibration = None
            self.error = 'Starting camera'
            self.clock_pairs = deque(maxlen=8)
            self.clock_sample_generation = 0
            self.clock_sample_error = 'No samples'
            self.causal_sender = None
            self.causal_pending = None
            self.causal_preview = None
            self.causal_seen = None
            self.causal_ready.set()

    def sample_clocks(self, source_now):
        # SDK clock read only: no second device owner, I/O or disk writes.
        with self.lock:
            session = self.session
        try:
            sample = sample_clock_pair(source_now)
        except Exception:
            sample = None
        with self.lock:
            if session != self.session:
                return
            previous = self.clock_pairs[-1] if self.clock_pairs else None
            if (sample is None or (previous and (
                    sample['source_seconds'] <= previous['source_seconds']
                    or sample['source_before_mono_ns'] <= previous['wall_after_mono_ns']
                    or sample['unix_ns'] <= previous['unix_ns']))):
                self._invalidate_timing_locked('Clock sampling discontinuity')
                return
            self.clock_pairs.append(sample)
            self.clock_sample_error = None

    def _invalidate_timing_locked(self, reason):
        self.clock_pairs.clear()
        self.clock_sample_generation += 1
        self.jpeg_cache = None
        self.clock_sample_error = reason
        self.causal_pending = self.causal_preview = None
        self.causal_ready.set()

    def receive_causal_ack(self, raw):
        """A device ACK supplies d0 only; receiving it never establishes h1."""
        try:
            if not isinstance(raw, bytes) or len(raw) > 256:
                return
            message = json.loads(raw, object_pairs_hook=_unique_json)
            if (not isinstance(message, dict) or set(message) != {'nonce', 'device_ns'}
                    or type(message['device_ns']) is not int or message['device_ns'] < 0):
                return
            with self.lock:
                pending = self.causal_pending
                if pending is None or message['nonce'] != pending['nonce']:
                    return
                if pending['device_ns'] is not None:
                    # Repeated/conflicting acknowledgement cannot amend proof.
                    self.causal_pending = None
                    self.causal_ready.set()
                    return
                pending['device_ns'] = message['device_ns']
        except (ValueError, TypeError, UnicodeError):
            return

    def receive_causal_rgb(self, frame):
        """Observe existing diagnostic RGB; copy pixels only for one challenge."""
        if self.causal_config is None:
            return
        try:
            sequence = frame.getSequenceNum()
            end = timestamp_ns(frame.getTimestampDevice())
            if type(sequence) is not int or sequence < 0:
                raise ValueError('Invalid sequence')
            with self.lock:
                previous = self.causal_seen
                if previous and (sequence < previous[0] or end < previous[1]
                                 or (sequence == previous[0]) != (end == previous[1])):
                    self._invalidate_timing_locked('Raw RGB source discontinuity')
                self.causal_seen = sequence, end
                pending = self.causal_pending
                if pending is None or pending['device_ns'] is None:
                    return
                if end <= pending['device_ns'] + self.causal_config.timestamp_quantization_ns:
                    return
                session, generation = self.session, self.clock_sample_generation
            if frame.getWidth() != JPEG_WIDTH or frame.getHeight() != JPEG_HEIGHT or frame.getType().name != 'NV12':
                raise ValueError('Unexpected challenged preview format')
            nv12 = bytes(frame.getData())
            received = self.causal_point()  # After this exact preview reaches owner.
            preview = Preview(session, sequence, end / 1e9, received.after_ns / 1e9, nv12,
                              frame_exposure_seconds(frame), generation)
            result = CausalFrame(preview, pending['nonce'], pending['device_ns'], end, pending['sent'], received,
                                 self.causal_config.timestamp_quantization_ns, hashlib.sha256(nv12).hexdigest(), pending['clock'])
            causal_timing(result, received, self.host_boot_id)
            with self.lock:
                if (self.causal_pending is not pending or self.session != session
                        or self.clock_sample_generation != generation):
                    return
                self.causal_pending = None
                self.causal_preview = result
                self.jpeg_cache = None
                self.causal_ready.set()
        except (ValueError, TypeError, OSError, AttributeError):
            with self.lock:
                self.causal_pending = None
                self.causal_ready.set()

    def _causal_jpeg(self):
        """Called with encoder permit; no second owner, archive, or background demand."""
        clock = HostClockEvidence(**read_protected_json(self.host_clock_path, self.evidence_uid))
        point = self.causal_point()
        check_host_clock(clock, point, self.host_boot_id)
        with self.lock:
            candidate = self.causal_preview
            if candidate and candidate.host_clock != clock:
                candidate = None
            if candidate:
                try:
                    causal_timing(candidate, point, self.host_boot_id)
                except ValueError:
                    candidate = None
            if candidate is None:
                if self.causal_sender is None or self.causal_pending is not None:
                    raise ValueError('Causal channel unavailable or busy')
                nonce = uuid.uuid4().hex
                pending = {'nonce': nonce, 'device_ns': None, 'sent': point, 'clock': clock,
                           'session': self.session, 'generation': self.clock_sample_generation}
                self.causal_pending = pending
                self.causal_preview = None
                self.jpeg_cache = None
                self.causal_ready.clear()
                sender = self.causal_sender
            else:
                pending = None
        if pending is not None:
            try:
                sender(nonce.encode('ascii'))
                self.causal_ready.wait(CAUSAL_DEADLINE_SECONDS)
                with self.lock:
                    candidate = self.causal_preview
                    if (candidate is None or candidate.nonce != nonce or candidate.preview.session != self.session
                            or candidate.preview.timing_generation != self.clock_sample_generation):
                        raise ValueError('No matching challenged frame')
            finally:
                with self.lock:
                    if self.causal_pending is pending:
                        self.causal_pending = None
        metadata = causal_timing(candidate, self.causal_point(), self.host_boot_id)
        with self.lock:
            cache = self.jpeg_cache
        jpeg = cache[1] if cache and cache[0] is candidate else encode_preview(candidate.preview.nv12)
        if not isinstance(jpeg, bytes) or not 4 <= len(jpeg) <= JPEG_MAX_BYTES:
            raise ValueError('Invalid encoded preview')
        finished = self.causal_point()
        metadata = causal_timing(candidate, finished, self.host_boot_id)
        if HostClockEvidence(**read_protected_json(self.host_clock_path, self.evidence_uid)) != clock:
            raise ValueError('Host clock receipt replaced or revoked during encoding')
        with self.lock:
            if (candidate is not self.causal_preview or candidate.preview.session != self.session
                    or candidate.preview.timing_generation != self.clock_sample_generation
                    or not self.updated or not self.encoded_updated
                    or not 0 <= finished.after_ns / 1e9 - self.updated < self.stale_seconds
                    or not 0 <= finished.after_ns / 1e9 - self.encoded_updated < self.stale_seconds):
                raise ValueError('Camera changed or stopped during encoding')
            self.jpeg_cache = (candidate, jpeg)
        metadata['jpeg_sha256'] = hashlib.sha256(jpeg).hexdigest()
        return 200, 'image/jpeg', jpeg, {'X-Victoria-Frame-Metadata': base64.urlsafe_b64encode(
            json.dumps(metadata, separators=(',', ':'), allow_nan=False).encode()).decode()}

    def publish_bundle(self, bundle, rgb, nv12, received):
        """Publish one immutable frame and revoke maps across source resets."""
        sequence = rgb['sequence']
        source = rgb['host_monotonic_timestamp_seconds']
        with self.lock:
            previous = self.preview
            if (type(sequence) is not int or sequence < 0 or
                    isinstance(source, bool) or not isinstance(source, (int, float)) or
                    not math.isfinite(source) or source < 0):
                self._invalidate_timing_locked('Invalid RGB source identity')
                self.preview = None
            else:
                exposure = rgb.get('exposure_duration_seconds')
                duplicate = (previous and sequence == previous.sequence
                             and source == previous.source_timestamp_seconds
                             and nv12 == previous.nv12 and exposure == previous.exposure_duration_seconds)
                if not duplicate:
                    if previous and (sequence <= previous.sequence or source <= previous.source_timestamp_seconds):
                        self._invalidate_timing_locked('RGB source regression or conflicting reuse')
                    self.preview = Preview(self.session, sequence, source, received, nv12,
                                           exposure, self.clock_sample_generation)
            self.bundle = bundle
            self.updated = received
            self.error = None

    def response(self, path, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            age = now - self.updated if self.updated else None
            video_age = now - self.encoded_updated if self.encoded_updated else None
            healthy = (age is not None and age < self.stale_seconds and
                       video_age is not None and video_age < self.stale_seconds)
            if path == '/timing':
                fresh = bool(self.clock_pairs and
                             0 <= now - self.clock_pairs[-1]['wall_after_mono_ns'] / 1e9 <= 5)
                body = {'schema': 1, 'session': self.session,
                        'generation': self.clock_sample_generation,
                        'semantics': 'raw_host_clock_pairs_not_validated_observation',
                        'source_clock': 'depthai_host_monotonic',
                        'python_clock': 'monotonic_ns', 'utc_error_validated': False,
                        'source_sync_validated': False, 'fresh': fresh,
                        'samples': list(self.clock_pairs) if fresh else [],
                        'offset_intervals': clock_offset_intervals(self.clock_pairs) if fresh else None,
                        'preview': ({'sequence': self.preview.sequence,
                                     'source_timestamp_seconds': self.preview.source_timestamp_seconds,
                                     'received_monotonic_seconds': self.preview.received_monotonic_seconds,
                                     'exposure_duration_seconds': self.preview.exposure_duration_seconds,
                                     'timing_generation': self.preview.timing_generation}
                                    if self.preview else None),
                        'error': self.clock_sample_error}
                return (200 if fresh else 503), 'application/json', json.dumps(body).encode()
            if path == '/health':
                body = {'healthy': healthy, 'session': self.session,
                        'timing_generation': self.clock_sample_generation,
                        'bundle_age_seconds': age, 'encoder_age_seconds': video_age,
                        'error': self.error}
                return (200 if healthy else 503), 'application/json', json.dumps(body).encode()
            if path == '/calibration' and self.calibration is not None:
                return 200, 'application/json', json.dumps(self.calibration).encode()
            if path == '/frame.npz' and healthy:
                return 200, 'application/octet-stream', self.bundle
            return (503 if path in ('/frame.npz', '/calibration') else 404), 'text/plain', b'Unavailable\n'

    def jpeg_response(self, now=None, unix_ns=None):
        """Latest only; at most one encoder, no waiting request queue or archive."""
        failure = (503, 'text/plain', b'Preview unavailable\n', {})
        if not self.jpeg_lock.acquire(False):
            return failure
        try:
            if self.causal_config is not None:
                return self._causal_jpeg()
            live_clock = now is None and unix_ns is None
            now = time.monotonic() if now is None else now
            unix_ns = time.time_ns() if unix_ns is None else unix_ns
            with self.lock:
                preview = self.preview
                cache = self.jpeg_cache
                generation = self.clock_sample_generation
                healthy = (self.updated and self.encoded_updated and
                           0 <= now - self.updated < self.stale_seconds and
                           0 <= now - self.encoded_updated < self.stale_seconds)
            if (preview is None or preview.timing_generation != generation
                    or not healthy or not self.jpeg_clock_map):
                return failure
            # Read a bounded local commissioning receipt; no caller-chosen path.
            with open(self.jpeg_clock_map, 'rb') as source:
                raw = source.read(8193)
            if len(raw) > 8192:
                return failure
            mapping = json.loads(raw)
            metadata = preview_timing(preview, mapping, now, unix_ns)
            if cache is not None and cache[0] is preview:
                jpeg = cache[1]
                # A refreshed commissioning receipt must not rewrite an
                # already served frame's observation time or provenance.
                mapping = cache[3]
                metadata = preview_timing(preview, mapping, now, unix_ns)
            else:
                jpeg = encode_preview(preview.nv12)
            # Encoding can outlive a reconnect. Never return the old session.
            finished = time.monotonic() if live_clock else now
            with self.lock:
                if (self.session != preview.session or self.clock_sample_generation != generation or not self.updated or
                        not 0 <= finished - self.updated < self.stale_seconds or
                        not 0 <= finished - self.encoded_updated < self.stale_seconds):
                    return failure
                self.jpeg_cache = (preview, jpeg, metadata, mapping)
            # Timing is checked again after work; tests can use fixed clocks.
            preview_timing(preview, mapping, finished,
                           time.time_ns() if live_clock else unix_ns)
            encoded_metadata = base64.urlsafe_b64encode(
                json.dumps(metadata, separators=(',', ':'), allow_nan=False).encode()).decode()
            return 200, 'image/jpeg', jpeg, {'X-Victoria-Frame-Metadata': encoded_metadata}
        except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError, AttributeError):
            return failure
        finally:
            self.jpeg_lock.release()


def serve(state, bind, port, token):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def do_GET(self):
            self.connection.settimeout(5)
            supplied = self.headers.get('Authorization', '')
            if ((self.path in ('/frame.jpg', '/timing') and not token) or
                    (token and not hmac.compare_digest(supplied, 'Bearer ' + token))):
                self.send_error(401)
                return
            headers = {}
            if self.path == '/frame.jpg':
                status, content_type, body, headers = state.jpeg_response()
            else:
                status, content_type, body = state.response(self.path)
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def log_message(self, *args):
            pass

    # Bound concurrent requests so slow consumers cannot create unlimited threads.
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(8)

        def process_request(self, request, client_address):
            if not self.slots.acquire(False):
                self.shutdown_request(request)
                return
            super().process_request(request, client_address)

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

    server = Server((bind, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def frame_metadata(frame):
    metadata = {'sequence': frame.getSequenceNum(),
            'device_timestamp_seconds': frame.getTimestampDevice().total_seconds(),
            'host_monotonic_timestamp_seconds': frame.getTimestamp().total_seconds(),
            'width': frame.getWidth(), 'height': frame.getHeight(),
            'type': str(frame.getType()),
            'exposure_duration_seconds': frame_exposure_seconds(frame)}
    transform = frame.getTransformation()
    metadata['transformation_valid'] = transform.isValid()
    if transform.isValid():
        metadata['intrinsics'] = transform.getIntrinsicMatrix()
        metadata['source_to_output_matrix'] = transform.getMatrix()
        metadata['source_size'] = transform.getSourceSize()
        metadata['source_distortion_coefficients'] = transform.getDistortionCoefficients()
    return metadata


def frame_exposure_seconds(frame):
    """Preserve SDK exposure when available; missing metadata stays unknown."""
    try:
        value = frame.getExposureTime().total_seconds()
        return value if (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value > 0) else None
    except Exception:
        # Optional metadata cannot interrupt local video/depth capture.
        return None


def watchdog_snapshot(now, last_group, last_video, stale_seconds, queued_packets):
    """Describe freshness independently before Pipeline teardown resets USB."""
    ages = {'depth_group_age_seconds': max(0.0, now - last_group),
            'publisher_write_age_seconds': max(0.0, now - last_video)}
    stale = [name for name, age in ages.items() if age > stale_seconds]
    return dict(ages, stale=stale, queued_video_packets=queued_packets)


def synchronization_metadata(frames, threshold_ms=20):
    """Expose measured timestamp skew; never claim hardware exposure sync."""
    timestamps = {name: frame['device_timestamp_seconds'] for name, frame in frames.items()}
    skew_ms = 1000 * (max(timestamps.values()) - min(timestamps.values()))
    return {'method': 'device_timestamp_group', 'hardware_synchronized': False,
            'threshold_ms': threshold_ms, 'measured_skew_ms': skew_ms,
            'within_threshold': skew_ms <= threshold_ms + 1e-6}


def capture_session(args, state, stop):
    import depthai as dai
    depth_fps = min(15, args.fps)
    state.reset()
    ffmpeg = None
    writer_stop = threading.Event()
    writer_error = []
    try:
        with dai.Pipeline() as pipeline:
            pipeline.setAutoCalibrationMode(dai.Pipeline.AutoCalibrationMode.OFF)
            device = pipeline.getDefaultDevice()
            if state.causal_config is not None and (
                    dai.__version__ != state.causal_config.depthai_version
                    or device.getDeviceId() != state.causal_config.device_id):
                raise ValueError('Causal timing configuration does not match SDK/device')
            calibration = device.readCalibration()
            with state.lock:
                state.calibration = {'session': state.session, 'depthai': dai.__version__,
                    'device_id': device.getDeviceId(),
                    'eeprom': calibration.eepromToJson(),
                    'depth_alignment': 'rectified_left', 'depth_units': 'millimetres',
                    'invalid_depth': 0, 'stereo_size': [640, 400],
                    'rgb_preview_size': [640, 360], 'rgb_stream_size': [1920, 1080],
                    'nominal_scaled_intrinsics': {
                        'left_640x400': calibration.getCameraIntrinsics(dai.CameraBoardSocket.CAM_B, 640, 400),
                        'right_640x400': calibration.getCameraIntrinsics(dai.CameraBoardSocket.CAM_C, 640, 400),
                        'rgb_640x360': calibration.getCameraIntrinsics(dai.CameraBoardSocket.CAM_A, 640, 360),
                        'rgb_1920x1080': calibration.getCameraIntrinsics(dai.CameraBoardSocket.CAM_A, 1920, 1080)},
                    'intrinsics_caveat': 'Nominal matrices assume aspect-preserving center crop. '
                        'For NPZ projection use each frame transformation intrinsics, accounting for '
                        'rectification and actual sensor crop. Depth is not RGB aligned; use EEPROM '
                        'extrinsics. RTSP has no per-frame transform; validate its crop before fusion.'}
            rgb_cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A,
                sensorResolution=(1920, 1080), sensorFps=args.fps)
            rgb_video = rgb_cam.requestOutput((1920, 1080), type=dai.ImgFrame.Type.NV12,
                resizeMode=dai.ImgResizeMode.CROP, fps=args.fps)
            rgb_preview = rgb_cam.requestOutput((640, 360), type=dai.ImgFrame.Type.NV12,
                # Keep candidates at sensor cadence: downsampling to 15 fps
                # leaves >20 ms gaps as RGB/stereo clock phases drift.
                resizeMode=dai.ImgResizeMode.CROP, fps=args.fps)
            left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B, sensorFps=depth_fps)
            right = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C, sensorFps=depth_fps)
            left_out = left.requestOutput((640, 400), type=dai.ImgFrame.Type.GRAY8, fps=depth_fps)
            right_out = right.requestOutput((640, 400), type=dai.ImgFrame.Type.GRAY8, fps=depth_fps)
            stereo = pipeline.create(dai.node.StereoDepth)
            stereo.build(left_out, right_out, dai.node.StereoDepth.PresetMode.DEFAULT)
            stereo.setDepthAlign(dai.CameraBoardSocket.CAM_B)
            stereo.setLeftRightCheck(True)
            stereo.initialConfig.postProcessing.temporalFilter.enable = False
            stereo.initialConfig.postProcessing.decimationFilter.decimationFactor = 1
            sync = pipeline.create(dai.node.Sync)
            sync.setSyncThreshold(timedelta(milliseconds=20))
            sync.setSyncAttempts(-1)
            diagnostic_queues = {}
            for name, output in [('rgb', rgb_preview), ('depth', stereo.depth),
                                 ('left', stereo.rectifiedLeft), ('right', stereo.rectifiedRight)]:
                sync.inputs[name].setBlocking(False)
                sync.inputs[name].setMaxSize(2)
                output.link(sync.inputs[name])
                diagnostic_queues[name] = output.createOutputQueue(maxSize=1, blocking=False)
            depth_queue = sync.out.createOutputQueue(maxSize=1, blocking=False)
            causal_input = causal_receipts = None
            if state.causal_config is not None:
                # Demand-only small messages. Reuse the RGB diagnostic queue;
                # never place Script on the RGB encoder or depth/Frigate path.
                challenge = pipeline.create(dai.node.Script)
                challenge.setScript(CAUSAL_SCRIPT)
                challenge.inputs['challenge'].setBlocking(False)
                challenge.inputs['challenge'].setMaxSize(1)
                causal_input = challenge.inputs['challenge'].createInputQueue(maxSize=1, blocking=False)
                causal_receipts = challenge.outputs['receipt'].createOutputQueue(maxSize=1, blocking=False)
            encoder = pipeline.create(dai.node.VideoEncoder)
            encoder.setDefaultProfilePreset(args.fps, dai.VideoEncoderProperties.Profile.H264_MAIN)
            encoder.setBitrateKbps(args.bitrate_kbps)
            encoder.setKeyframeFrequency(args.fps)
            encoder.setNumBFrames(0)
            rgb_video.link(encoder.input)
            encoded_queue = encoder.bitstream.createOutputQueue(maxSize=60, blocking=True)
            ffmpeg = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'warning',
                '-fflags', '+genpts', '-r', str(args.fps), '-f', 'h264', '-i', 'pipe:0',
                '-an', '-c:v', 'copy', '-f', 'rtsp', '-rtsp_transport', 'tcp', args.rtsp_url],
                stdin=subprocess.PIPE, bufsize=0)
            packets = queue.Queue(maxsize=60)

            def write_video():
                try:
                    while not writer_stop.is_set():
                        try:
                            packet = packets.get(timeout=.5)
                        except queue.Empty:
                            continue
                        view = memoryview(packet)
                        while view and not writer_stop.is_set():
                            written = ffmpeg.stdin.write(view)
                            if not written:
                                raise RuntimeError('FFmpeg pipe closed')
                            view = view[written:]
                        with state.lock:
                            state.encoded_updated = time.monotonic()
                except Exception as exc:
                    writer_error.append(exc)

            threading.Thread(target=write_video, daemon=True).start()
            pipeline.start()
            if causal_input is not None:
                def send_causal(raw):
                    message = dai.Buffer(len(raw))
                    message.setData(raw)
                    causal_input.send(message)
                with state.lock:
                    state.causal_sender = send_causal
            device.setIrLaserDotProjectorIntensity(0.0)
            device.setIrFloodLightIntensity(0.0)
            logging.info('Camera started; USB speed %s', device.getUsbSpeed())
            started = time.monotonic()
            last_group = started
            last_bundle = 0.0
            measured_start = started
            measured_frames = 0
            measured_device_start = None
            input_observed = {}
            last_diagnostic = started
            last_clock_sample = float('-inf')
            while not stop.is_set():
                now = time.monotonic()
                if now - last_clock_sample >= 1:
                    state.sample_clocks(dai.Clock.now)
                    last_clock_sample = now
                if causal_receipts is not None:
                    acknowledgement = causal_receipts.tryGet()
                    if acknowledgement is not None:
                        state.receive_causal_ack(bytes(acknowledgement.getData()))
                for name, diagnostic_queue in diagnostic_queues.items():
                    frame = diagnostic_queue.tryGet()
                    if frame is not None:
                        if name == 'rgb' and state.causal_config is not None:
                            state.receive_causal_rgb(frame)
                        input_observed[name] = (now, frame.getTimestampDevice().total_seconds(),
                                                frame.getSequenceNum())
                if now - last_diagnostic >= 10:
                    logging.info('Sync inputs: %s', json.dumps({name: {
                        'age': round(now - values[0], 3), 'device_time': values[1],
                        'sequence': values[2]} for name, values in input_observed.items()}, sort_keys=True))
                    last_diagnostic = now
                if ffmpeg.poll() is not None or writer_error:
                    raise RuntimeError('RTSP publisher exited or pipe failed')
                for _ in range(30):
                    encoded = encoded_queue.tryGet()
                    if encoded is None:
                        break
                    measured_frames += 1
                    device_time = encoded.getTimestampDevice().total_seconds()
                    if measured_device_start is None:
                        measured_device_start = device_time
                    if now - measured_start >= 60:
                        logging.info('Encoder cadence: %.1f frames/s wall, %.3fs device span across %d frames',
                            measured_frames / (now - measured_start), device_time - measured_device_start, measured_frames)
                        measured_start, measured_frames, measured_device_start = now, 0, None
                    packets.put_nowait(bytes(encoded.getData()))
                group = depth_queue.tryGet()
                if group is not None:
                    last_group = now
                    if now - last_bundle >= 1.0 / args.bundle_fps:
                        frames = {name: group[name] for name in ('rgb', 'depth', 'left', 'right')}
                        metadata = {'schema': 1, 'session': state.session,
                            'captured_unix_ns': time.time_ns(),
                            'captured_unix_ns_semantics': 'bundle_packaging_time_not_sensor_exposure',
                            'depth_alignment': 'rectified_left',
                            'depth_units': 'millimetres', 'invalid_depth': 0,
                            'frames': {name: frame_metadata(frame) for name, frame in frames.items()}}
                        metadata['synchronization'] = synchronization_metadata(metadata['frames'])
                        arrays = {'depth_mm': frames['depth'].getFrame(),
                                  'left': frames['left'].getFrame(), 'right': frames['right'].getFrame(),
                                  'rgb_nv12': frames['rgb'].getFrame()}
                        bundle = encode_bundle(arrays, metadata)
                        # JPEG conversion remains in the bounded HTTP worker.
                        state.publish_bundle(bundle, metadata['frames']['rgb'],
                                             arrays['rgb_nv12'].tobytes(), now)
                        last_bundle = now
                with state.lock:
                    last_video = state.encoded_updated or started
                watchdog = watchdog_snapshot(now, last_group, last_video,
                                             args.stale_seconds, packets.qsize())
                if watchdog['stale']:
                    # Log before leaving the device context: teardown itself can
                    # cause USB disconnect/re-enumeration in the kernel journal.
                    logging.error('Stale watchdog before device teardown: %s',
                                  json.dumps(watchdog, sort_keys=True))
                    raise RuntimeError('Camera or publisher stale watchdog fired')
                time.sleep(.005)
    finally:
        with state.lock:
            state.causal_sender = None
            state.causal_pending = state.causal_preview = None
            state.causal_ready.set()
        writer_stop.set()
        if ffmpeg is not None:
            ffmpeg.terminate()
            try:
                ffmpeg.wait(timeout=3)
            except subprocess.TimeoutExpired:
                ffmpeg.kill()
                ffmpeg.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default=os.environ.get('HTTP_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('HTTP_PORT', '8090')))
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--bundle-fps', type=float, default=5)
    parser.add_argument('--bitrate-kbps', type=int, default=4000)
    parser.add_argument('--stale-seconds', type=float, default=15)
    parser.add_argument('--jpeg-clock-map', default=os.environ.get('VICTORIA_JPEG_CLOCK_MAP'),
                        help='Session-bound commissioned JSON clock-map receipt; JPEG fails closed without it')
    parser.add_argument('--jpeg-timing-mode', choices=('mapped', 'causal'), default='mapped',
                        help='Causal exact-frame mode is opt-in and requires installed commissioning evidence')
    parser.add_argument('--jpeg-causal-config', help='Protected pinned device/semantics JSON, not a readiness flag')
    parser.add_argument('--jpeg-host-clock', help='Protected fresh commissioned camera-host UTC evidence JSON')
    parser.add_argument('--jpeg-evidence-uid', type=int, help='Explicit Linux owner UID for both causal evidence inputs')
    parser.add_argument('--rtsp-url', default=os.environ.get('RTSP_URL', 'rtsp://127.0.0.1:8554/den/rgb'))
    args = parser.parse_args()
    if not (0 < args.bundle_fps <= args.fps <= 30) or args.stale_seconds <= 0:
        parser.error('Require 0 < bundle-fps <= fps <= 30 and positive stale-seconds')
    causal_config = None
    if args.jpeg_timing_mode == 'causal':
        if (sys.platform != 'linux' or args.jpeg_clock_map or not args.jpeg_causal_config
                or not args.jpeg_host_clock or args.jpeg_evidence_uid is None):
            parser.error('Causal mode requires Linux, explicit config/host-clock/UID, and no session map')
        causal_config = CausalConfig(**read_protected_json(args.jpeg_causal_config, args.jpeg_evidence_uid))
    elif args.jpeg_causal_config or args.jpeg_host_clock or args.jpeg_evidence_uid is not None:
        parser.error('Causal evidence inputs require explicit causal timing mode')
    token = os.environ.get('VICTORIA_CAMERA_TOKEN', '')
    if args.bind not in ('127.0.0.1', '::1', 'localhost') and len(token) < 24:
        parser.error('LAN binding requires VICTORIA_CAMERA_TOKEN of at least 24 characters')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    state = State(args.stale_seconds, args.jpeg_clock_map, causal_config=causal_config,
                  host_clock_path=args.jpeg_host_clock, evidence_uid=args.jpeg_evidence_uid)
    server = serve(state, args.bind, args.port, token)
    failures = 0
    try:
        while not stop.is_set():
            try:
                capture_session(args, state, stop)
            except Exception:
                logging.exception('Capture session failed; reconnecting')
                with state.lock:
                    state.error = 'Capture disconnected; reconnecting'
                    state.updated = 0
                failures += 1
                if failures >= 5:
                    raise
                stop.wait(min(2 ** failures, 20))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()

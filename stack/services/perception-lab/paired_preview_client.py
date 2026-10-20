"""Opt-in exact pairs through the incumbent commissioned preview capture turn.

No device, owner, polling thread, model or journal is created here. The existing
Chrony client still decides whether the original JPEG/header is accepted. Raw
depth retention is private measurement material, not geometric qualification.
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
import time

from chrony_preview_client import ChronyPreviewClient
from live_acquisition_owner import PAIR_MAX_BYTES, pair_transport
from recorder import validate_npz

POLICY_KEYS = {'schema', 'version', 'enabled', 'max_pairs'}


def policy(value):
    if (not isinstance(value, dict) or set(value) != POLICY_KEYS
            or value['schema'] != 'home-lab-paired-preview-1'
            or type(value['version']) is not int or value['version'] != 1
            or value['enabled'] is not True or type(value['max_pairs']) is not int
            or not 1 <= value['max_pairs'] <= 24):
        raise ValueError('closed bounded paired preview policy required')
    return deepcopy(value)


def _json(raw):
    def unique(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError('duplicate pair field')
            result[key] = item
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('finite pair JSON required')))


def encoded(value):
    # Private originals preserve native integer nanoseconds exactly.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_pair(jpeg, header, bundle, calibration):
    """Validate the exact pair before any numeric bytes or RGB are offered."""
    if (not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= 256*1024
            or not isinstance(header, str) or not 0 < len(header) <= 8192
            or not isinstance(bundle, bytes) or not 0 < len(bundle) <= 4*1024**2
            or not isinstance(calibration, dict) or len(encoded(calibration)) > 24576):
        raise ValueError('bounded original pair required')
    from home_shared.protocol import camera_frame
    camera_frame(jpeg, header + '='*((-len(header)) % 4))
    receipt = _json(base64.b64decode(header + '='*((-len(header)) % 4), altchars=b'-_', validate=True))
    if receipt.get('schema') != 3:
        raise ValueError('causal original paired RGB required')
    validate_npz(bundle, 4*1024**2)
    import numpy as np
    with np.load(io.BytesIO(bundle), allow_pickle=False) as arrays:
        required = {'depth_mm': ((400, 640), 'uint16'), 'left': ((400, 640), 'uint8'),
                    'right': ((400, 640), 'uint8'), 'rgb_nv12': ((540, 640), 'uint8')}
        if set(arrays.files) != set(required) | {'metadata_json'}:
            raise ValueError('exact numerical pair members required')
        for name, (shape, dtype) in required.items():
            if arrays[name].shape != shape or arrays[name].dtype != np.dtype(dtype):
                raise ValueError('numerical pair shape mismatch')
        raw = arrays['metadata_json']
        if raw.dtype != np.uint8 or raw.ndim != 1 or len(raw) > 32768:
            raise ValueError('bounded original component metadata required')
        metadata = _json(raw.tobytes())
    frames = metadata.get('frames', {})
    rgb, depth = frames.get('rgb', {}), frames.get('depth', {})
    tolerance = receipt['timestamp_quantization_ns']/1e9 + 1e-6
    numeric = lambda value: type(value) in (int, float) and math.isfinite(value)
    if (metadata.get('schema') != 1 or metadata.get('session') != receipt['session']
            or metadata.get('depth_alignment') != 'rectified_left'
            or metadata.get('depth_units') != 'millimetres' or metadata.get('invalid_depth') != 0
            or metadata.get('captured_unix_ns_semantics') != 'bundle_packaging_time_not_sensor_exposure'
            or set(frames) != {'rgb', 'depth', 'left', 'right'}
            or type(rgb.get('sequence')) is not int or rgb['sequence'] != receipt['sequence']
            or not numeric(rgb.get('device_timestamp_seconds'))
            or abs(rgb['device_timestamp_seconds'] - receipt['frame_device_ns']/1e9) > tolerance
            or (rgb.get('width'), rgb.get('height')) != (640, 360)
            or (depth.get('width'), depth.get('height')) != (640, 400)
            or type(depth.get('sequence')) is not int or depth['sequence'] < 0
            or not numeric(depth.get('device_timestamp_seconds'))
            or calibration.get('session') != receipt['session']
            or calibration.get('depth_alignment') != 'rectified_left'
            or calibration.get('depth_units') != 'millimetres'):
        raise ValueError('exact RGB numerical pair binding required')
    for frame in frames.values():
        if (type(frame.get('sequence')) is not int or frame['sequence'] < 0
                or not numeric(frame.get('device_timestamp_seconds'))
                or not numeric(frame.get('host_monotonic_timestamp_seconds'))):
            raise ValueError('original component identities required')
    return {'receipt': receipt, 'source_metadata': metadata,
            'observed_at': float(Decimal(receipt['observed_at_unix_ns'])/Decimal(1_000_000_000)),
            'uncertainty_seconds': receipt['uncertainty_seconds'] + receipt['host_clock_uncertainty_seconds'],
            'fingerprint': sha(encoded([sha(jpeg), header, sha(bundle), sha(encoded(calibration))])),
            'identity': encoded([receipt['session'], receipt['timing_generation'], receipt['sequence']]).decode()}


def sync_directory(path):
    """Fail closed if the platform cannot acknowledge media directory durability."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class PairedPreviewClient(ChronyPreviewClient):
    """One accepted response, consumed once by the same recording preview pump."""
    def __init__(self, *, transport=pair_transport, clock=time.time):
        if not callable(transport) or not callable(clock):
            raise ValueError('fixed paired transport and clock required')
        self.transport, self.clock = transport, clock
        self._pair = None
        self._state = threading.Lock()

    def fetch(self, pilot, path, token):
        if path != '/frame.jpg':
            return super().fetch(pilot, path, token)
        raw = self.transport(token, timeout=2.0)
        received = self.clock()
        if not isinstance(raw, bytes) or not 0 < len(raw) <= PAIR_MAX_BYTES:
            raise ValueError('bounded exact pair response required')
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'jpeg_b64', 'header', 'bundle_b64', 'calibration'}
                or value['schema'] != 'home-camera-pair-1' or type(value['version']) is not int or value['version'] != 1):
            raise ValueError('closed exact pair response required')
        jpeg = base64.b64decode(value['jpeg_b64'], validate=True)
        bundle = base64.b64decode(value['bundle_b64'], validate=True)
        checked_pair(jpeg, value['header'], bundle, value['calibration'])
        if type(received) not in (int, float) or not math.isfinite(received) or received < 0:
            raise ValueError('actual original pair receipt clock required')
        with self._state:
            self._pair = {'jpeg': jpeg, 'header': value['header'], 'bundle': bundle,
                          'calibration': deepcopy(value['calibration']), 'received_at': received}
        return {'content-type': 'image/jpeg', 'x-victoria-frame-metadata': value['header']}, jpeg

    def __call__(self):
        with self._state:
            self._pair = None
        try:
            # The inherited method owns the existing shared capture turn and
            # unchanged Chrony/health/clock accept_once, including its refusals.
            result = super().__call__()
            with self._state:
                if self._pair is None or result != (self._pair['jpeg'], self._pair['header']):
                    raise ValueError('accepted exact pair unavailable')
            return result
        except Exception:
            with self._state:
                self._pair = None
            raise

    def take_pair(self, jpeg, header):
        with self._state:
            pair, self._pair = self._pair, None
        if pair is None or (jpeg, header) != (pair['jpeg'], pair['header']):
            raise ValueError('one accepted exact pair required')
        return pair

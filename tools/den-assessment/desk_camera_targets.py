"""Private frozen camera-target candidates from the sole incumbent OAK owner.

No model or device owner is created. The opt-in operator factory uses only the
existing Victoria loopback reader. Hashes bind originals; they do not qualify
projection, clocks, correspondence accuracy, registration or person geometry.
"""
from copy import deepcopy
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import zipfile

LAB = Path(__file__).resolve().parents[2] / 'stack/services/perception-lab'
MAX_BUNDLE = 4 * 1024**2
MAX_CALIBRATION = 24576
MAX_PREVIEW = 1024**2
STORE = 'desk-camera-originals'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def parsed(raw, maximum):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, 'bounded_original_required')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_original_field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('finite_original_required')))


def sync_directory(path):
    """Fail closed where directory durability is unsupported (including Windows)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def lab_modules():
    if LAB.is_dir() and str(LAB) not in sys.path:
        sys.path.insert(0, str(LAB))
    import room_mapper
    import head3d
    return room_mapper, head3d


def checked_npz_metadata(raw):
    """Bound NPY-header allocations before the incumbent decoder sees them."""
    import numpy as np
    expected = {'depth_mm.npy': ((400, 640), np.dtype('uint16')),
        'left.npy': ((400, 640), np.dtype('uint8')), 'right.npy': ((400, 640), np.dtype('uint8')),
        'rgb_nv12.npy': ((540, 640), np.dtype('uint8')), 'metadata_json.npy': (None, np.dtype('uint8'))}
    metadata = None
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(len(members) == 5 and {item.filename for item in members} == set(expected)
            and sum(item.file_size for item in members) <= MAX_BUNDLE
            and all(item.file_size <= 2 * 1024**2 for item in members), 'bounded_original_npz_members_required')
        for info in members:
            with archive.open(info) as stream:
                version = np.lib.format.read_magic(stream)
                require(version in ((1, 0), (2, 0)), 'bounded_known_npy_version_required')
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                shape, fortran, dtype = reader(stream, max_header_size=10000)
                expected_shape, expected_dtype = expected[info.filename]
                require(fortran is False and dtype == expected_dtype, 'exact_original_array_dtype_required')
                if expected_shape is None:
                    require(type(shape) is tuple and len(shape) == 1 and type(shape[0]) is int
                            and 0 < shape[0] <= 65536, 'bounded_metadata_array_required')
                else:
                    require(shape == expected_shape, 'exact_original_array_shape_required')
                size = math.prod(shape) * dtype.itemsize
                require(info.file_size - stream.tell() == size, 'exact_declared_array_bytes_required')
                if info.filename == 'metadata_json.npy':
                    metadata = parsed(stream.read(size), 65536)
    return metadata


def incumbent_reader():
    """Explicit operator entry on Victoria; no browser-selected routes/secrets."""
    require(sys.platform.startswith('linux'), 'victoria_local_operator_required')
    room_mapper, _ = lab_modules()
    from preview_client import LocalBundleClient
    client = LocalBundleClient(lambda: room_mapper.camera_token(room_mapper.CAMERA_ENV))
    def read():
        raw, _ = client._read('/frame.npz')
        calibration, _ = client._read('/calibration')
        return raw, calibration
    return read


class DeskCameraTargets:
    def __init__(self, directory, fetch, *, clock=time.time, monotonic=time.monotonic,
                 sync=sync_directory, max_snapshots=16, max_points=32):
        self.directory = Path(directory)
        require(self.directory.is_absolute() and self.directory.parent.is_dir()
            and not self.directory.parent.is_symlink() and not self.directory.exists(), 'fresh_private_original_directory_required')
        require(callable(fetch) and callable(clock) and callable(monotonic) and callable(sync), 'trusted_camera_ports_required')
        require(type(max_snapshots) is int and 1 <= max_snapshots <= 16
            and type(max_points) is int and 1 <= max_points <= 32, 'bounded_camera_actions_required')
        self.fetch, self.clock, self.monotonic, self.sync = fetch, clock, monotonic, sync
        self.max_snapshots, self.max_points = max_snapshots, max_points
        self.snapshots, self.points = {}, {}
        self.snapshot_attempts = self.point_attempts = 0
        self.directory.mkdir(mode=0o700)
        self.sync(self.directory.parent)

    def _write(self, name, data):
        path = self.directory / name
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())

    @staticmethod
    def _metadata(bundle):
        meta = bundle['meta']
        require(type(meta) is dict and type(meta.get('session')) is str
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', meta['session']), 'capture_epoch_required')
        require(type(meta.get('frames')) is dict and set(meta['frames']) == {'rgb', 'depth', 'left', 'right'},
                'complete_original_component_metadata_required')
        for name, frame in meta['frames'].items():
            require(type(frame) is dict and type(frame.get('sequence')) is int and frame['sequence'] >= 0,
                    'original_component_sequence_required')
            for key in ('device_timestamp_seconds', 'host_monotonic_timestamp_seconds'):
                number = frame.get(key)
                require(type(number) in (int, float) and math.isfinite(number) and number >= 0,
                        'original_component_clock_required')
            require(frame.get('transformation_valid') is True and type(frame.get('intrinsics')) is list,
                    'original_component_intrinsics_required')
            dimensions = (640, 360) if name == 'rgb' else (640, 400)
            require(type(frame.get('width')) is int and type(frame.get('height')) is int
                    and (frame['width'], frame['height']) == dimensions, 'fixed_original_metadata_dimensions_required')
        require(len(encoded(meta)) <= 65536, 'bounded_original_metadata_required')
        return meta

    def snapshot(self):
        require(self.snapshot_attempts < self.max_snapshots, 'snapshot_budget_exhausted')
        self.snapshot_attempts += 1
        started = self.monotonic()
        raw, calibration_raw = self.fetch()
        require(type(raw) is bytes and 0 < len(raw) <= MAX_BUNDLE, 'bounded_original_bundle_required')
        calibration = parsed(calibration_raw, MAX_CALIBRATION)
        metadata = checked_npz_metadata(raw)
        mapper, _ = lab_modules()
        bundle = mapper.decode_bundle(raw)
        meta = self._metadata(bundle)
        require(meta == metadata, 'exact_original_metadata_required')
        require(type(calibration) is dict and calibration.get('session') == meta['session']
            and calibration.get('depth_alignment') == 'rectified_left'
            and calibration.get('depth_units') == 'millimetres', 'matching_original_calibration_required')
        model = mapper.rgbd_model(meta, calibration)
        require(model['rgb_size'] == (640, 360) and model['depth_size'] == (640, 400), 'fixed_original_dimensions_required')
        from PIL import Image
        preview = io.BytesIO()
        Image.fromarray(mapper.nv12_to_rgb(bundle['rgb_nv12'])).save(preview, format='PNG')
        preview = preview.getvalue()
        require(0 < len(preview) <= MAX_PREVIEW, 'bounded_frozen_preview_required')
        received = self.clock()
        require(type(received) in (int, float) and math.isfinite(received) and received >= 0, 'receipt_clock_required')
        identity = sha(encoded(['desk-camera-snapshot-1', sha(raw), sha(calibration_raw)]))
        snapshot_id = 'snapshot-' + identity
        if snapshot_id in self.snapshots:
            return self._snapshot_reply(self.snapshots[snapshot_id])
        original = {'schema': 'home-desk-camera-snapshot-original-1', 'version': 1, 'id': snapshot_id,
            'source_frame': 'rectified_left', 'rgb_size': [640, 360], 'depth_size': [640, 400],
            'bundle_sha256': sha(raw), 'calibration_sha256': sha(calibration_raw),
            'calibration_content_id': mapper.calibration_id(calibration), 'original_metadata': deepcopy(meta),
            'preview_sha256': sha(preview), 'received_at': received,
            'receipt_time_role': 'operator_first_receipt_not_sensor_exposure',
            'camera_uncertainty_m': None, 'qualified': False, 'operational_geometry': None}
        ref = {'store_id': STORE, 'receipt_id': snapshot_id, 'digest': sha(encoded(original))}
        require(0 <= self.monotonic() - started <= 8, 'snapshot_deadline_exceeded')
        self._write(snapshot_id + '.npz', raw)
        self._write(snapshot_id + '.calibration.json', calibration_raw)
        self._write(snapshot_id + '.png', preview)
        self._write(snapshot_id + '.json', encoded(original))
        self.sync(self.directory)
        require(0 <= self.monotonic() - started <= 8, 'snapshot_retention_deadline_exceeded')
        entry = {'original': original, 'ref': ref, 'bundle': bundle, 'model': model, 'preview': preview}
        self.snapshots[snapshot_id] = entry
        return self._snapshot_reply(entry)

    @staticmethod
    def _snapshot_reply(entry):
        return {'status': 'frozen_candidate', 'snapshot_id': entry['original']['id'],
            'snapshot_ref': deepcopy(entry['ref']), 'width': 640, 'height': 360,
            'preview_base64': base64.b64encode(entry['preview']).decode('ascii'), 'preview_mime': 'image/png',
            'capture_epoch': entry['original']['original_metadata']['session'],
            'qualified': False, 'camera_uncertainty_m': None}

    def point(self, snapshot_id, click_id, u, v, reference):
        require(type(snapshot_id) is str and snapshot_id in self.snapshots, 'frozen_snapshot_unavailable')
        require(type(click_id) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', click_id), 'click_identity_required')
        require(type(u) is int and type(v) is int and 0 <= u < 640 and 0 <= v < 360, 'frozen_rgb_pixel_required')
        require(type(reference) is dict and set(reference) == {'role', 'room_measurement'}
            and reference['role'] in ('fit', 'check'), 'independent_reference_required')
        measurement = reference['room_measurement']
        require(type(measurement) is dict and set(measurement) == {'wall_distances_m', 'height_above_floor_m', 'uncertainty_m'}
            and type(measurement['wall_distances_m']) is list and len(measurement['wall_distances_m']) == 2,
            'independent_room_measurement_required')
        for number, upper in [(n, 100) for n in measurement['wall_distances_m']] + [
            (measurement['height_above_floor_m'], 20), (measurement['uncertainty_m'], 2)]:
            require(type(number) in (int, float) and math.isfinite(number) and 0 <= number <= upper,
                    'finite_independent_measurement_required')
        fingerprint = (snapshot_id, u, v, sha(encoded(reference)))
        if click_id in self.points:
            entry = self.points[click_id]
            require(entry['fingerprint'] == fingerprint, 'click_identity_reused_with_changed_body')
            return deepcopy(entry['reply'])
        require(self.point_attempts < self.max_points, 'point_budget_exhausted')
        self.point_attempts += 1
        started = self.monotonic()
        reference_received = self.clock()
        require(type(reference_received) in (int, float) and math.isfinite(reference_received)
                and reference_received >= 0, 'reference_receipt_clock_required')
        entry = self.snapshots[snapshot_id]
        _, head3d = lab_modules()
        radius = head3d.PATCH_RADIUS_PX
        lifter = head3d.DepthLifter(entry['bundle']['depth_mm'], entry['model'], rgb_roi=[u, v, u, v])
        measured = lifter.lift(u, v, radius_px=radius, min_support=3)
        found = measured.get('status') == 'measured'
        original = {'schema': 'home-desk-camera-point-original-1', 'version': 1,
            'id': 'point-' + sha(encoded([snapshot_id, click_id, u, v, reference])), 'click_id': click_id,
            'snapshot_ref': deepcopy(entry['ref']), 'snapshot_id': snapshot_id,
            'source_frame': 'rectified_left', 'rgb_pixel': [u, v],
            'independent_reference': deepcopy(reference), 'reference_received_at': reference_received,
            'reference_time_role': 'operator_entry_not_sensor_exposure',
            'status': 'candidate' if found else 'unavailable',
            'reason': 'independent_camera_error_unavailable' if found else measured.get('reason', 'depth_unavailable'),
            'camera_xyz_m': deepcopy(measured['xyz']) if found else None,
            'camera_uncertainty_m': None, 'depth_spread_m': measured.get('spread_m'),
            'depth_support': measured.get('support', 0), 'spread_role': 'diagnostic_not_error_bound',
            'lift_method': 'head3d.DepthLifter.lift', 'lift_parameters': {'radius_px': radius, 'min_support': 3},
            'original_metadata': deepcopy(entry['original']['original_metadata']),
            'bundle_sha256': entry['original']['bundle_sha256'],
            'calibration_sha256': entry['original']['calibration_sha256'],
            'calibration_content_id': entry['original']['calibration_content_id'],
            'qualified': False, 'operational_geometry': None}
        ref = {'store_id': STORE, 'receipt_id': original['id'], 'digest': sha(encoded(original))}
        require(0 <= self.monotonic() - started <= 2, 'point_deadline_exceeded')
        self._write(original['id'] + '.json', encoded(original))
        self.sync(self.directory)
        require(0 <= self.monotonic() - started <= 2, 'point_retention_deadline_exceeded')
        reply = {'status': original['status'], 'reason': original['reason'], 'camera_original_ref': ref,
            'camera_xyz_m': deepcopy(original['camera_xyz_m']), 'camera_uncertainty_m': None,
            'depth_spread_m': original['depth_spread_m'], 'depth_support': original['depth_support'], 'qualified': False}
        self.points[click_id] = {'fingerprint': fingerprint, 'original': original, 'ref': ref, 'reply': reply}
        return deepcopy(reply)

    def resolve_point(self, ref):
        require(type(ref) is dict and set(ref) == {'store_id', 'receipt_id', 'digest'}, 'exact_point_reference_required')
        for entry in self.points.values():
            if entry['ref'] == ref:
                return deepcopy(entry['original'])
        raise ValueError('point_original_not_in_this_guide')

"""Authored frozen NPZ -> actual lift -> existing guide HTTP, no devices/models.

Windows tests stub only directory fsync; file fsync and exclusive writes are
real. This proves ordering/refusal, not Linux directory durability or accuracy.
"""
from copy import deepcopy
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
import desk_camera_targets as targets
import test_room_mapper as camera_fixtures


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


guide = module('camera_target_guide', HERE / 'desk-geometry-guide.py')
distinct = module('camera_target_distinct_fixtures', HERE / 'test_desk_distinct_parts.py')


def bundle(*, depth=2000, epoch='epoch-1', modify=None):
    meta = camera_fixtures.metadata()
    meta['session'] = epoch
    meta['captured_unix_ns'] = 1792173344000000123
    meta['captured_unix_ns_semantics'] = 'bundle_packaging_time_not_sensor_exposure'
    for index, frame in enumerate(meta['frames'].values()):
        frame['sequence'] = 700 + index
        frame['device_timestamp_seconds'] = 100 + index * .001
        frame['host_monotonic_timestamp_seconds'] = 1000 + index * .001
    if modify is not None:
        modify(meta)
    output = io.BytesIO()
    np.savez(output, depth_mm=np.full((400, 640), depth, np.uint16),
        left=np.zeros((400, 640), np.uint8), right=np.zeros((400, 640), np.uint8),
        rgb_nv12=np.full((540, 640), 128, np.uint8),
        metadata_json=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))
    return output.getvalue(), meta


def reference(u=320, v=180, role='fit'):
    # Independent authored pinhole truth for a two-metre plane, not a result
    # read back from the tested lifter. Small sampling residuals remain visible.
    return {'role': role, 'room_measurement': {'wall_distances_m': [
        (u - 312.6) / 485.78 * 2 + .0375 + 2, (v - 182.76) / 485.47 * 2 + 3],
        'height_above_floor_m': 2.5, 'uncertainty_m': .001}}


class CameraTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='authored-desk-camera-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.raw, self.meta = bundle()
        self.calibration_raw = (json.dumps(camera_fixtures.CALIBRATION, indent=2) + '\n').encode()
        self.calls, self.syncs = [], []
        self.port = self.make_port('originals')

    def make_port(self, name, **options):
        def fetch():
            self.calls.append('one_original_pair')
            return self.raw, self.calibration_raw
        return targets.DeskCameraTargets(self.base / name, fetch, clock=lambda: 1100,
            sync=lambda path: self.syncs.append(Path(path)), **options)

    def test_snapshot_retains_exact_originals_and_metadata_before_frozen_preview(self):
        snapshot = self.port.snapshot()
        self.assertEqual(self.calls, ['one_original_pair'])
        sid = snapshot['snapshot_id']
        self.assertEqual((self.base / 'originals' / (sid + '.npz')).read_bytes(), self.raw)
        self.assertEqual((self.base / 'originals' / (sid + '.calibration.json')).read_bytes(), self.calibration_raw)
        original = json.loads((self.base / 'originals' / (sid + '.json')).read_bytes())
        self.assertEqual(original['original_metadata'], self.meta)
        self.assertEqual(original['original_metadata']['captured_unix_ns'], 1792173344000000123)
        self.assertNotEqual(original['original_metadata']['frames']['rgb']['sequence'], original['original_metadata']['frames']['depth']['sequence'])
        self.assertEqual(original['calibration_sha256'], hashlib.sha256(self.calibration_raw).hexdigest())
        self.assertEqual(snapshot['snapshot_ref']['digest'], targets.sha(targets.encoded(original)))
        self.assertEqual(self.syncs[-1], self.base / 'originals')
        self.assertEqual(snapshot['preview_mime'], 'image/png')
        self.assertFalse(snapshot['qualified']); self.assertIsNone(snapshot['camera_uncertainty_m'])
        self.assertIsNone(original['operational_geometry'])

    def test_point_uses_only_frozen_bundle_and_retains_locked_reference_before_reply(self):
        snapshot = self.port.snapshot()
        point = self.port.point(snapshot['snapshot_id'], 'click-1', 320, 180, reference())
        self.assertEqual(point['status'], 'candidate')
        self.assertAlmostEqual(point['camera_xyz_m'][2], 2)
        self.assertGreater(point['depth_support'], 0)
        self.assertIsNone(point['camera_uncertainty_m'])
        original = self.port.resolve_point(point['camera_original_ref'])
        self.assertEqual(original['rgb_pixel'], [320, 180])
        self.assertEqual(original['independent_reference'], reference())
        self.assertEqual(original['reference_received_at'], 1100)
        self.assertEqual(original['original_metadata'], self.meta)
        self.assertEqual(original['spread_role'], 'diagnostic_not_error_bound')
        self.assertEqual(self.calls, ['one_original_pair'])
        self.assertEqual(self.syncs[-1], self.base / 'originals')
        self.assertEqual(json.loads((self.base / 'originals' / (original['id'] + '.json')).read_bytes()), original)
        self.assertEqual(self.port.point(snapshot['snapshot_id'], 'click-1', 320, 180, reference()), point)
        for changed in ((321, 180, reference()), (320, 180, reference(role='check'))):
            with self.assertRaises(ValueError):
                self.port.point(snapshot['snapshot_id'], 'click-1', *changed)

    def test_missing_depth_has_retained_refusal_and_never_zero_error_or_point(self):
        self.raw, _ = bundle(depth=0)
        snapshot = self.port.snapshot()
        point = self.port.point(snapshot['snapshot_id'], 'missing-1', 320, 180, reference())
        self.assertEqual(point['status'], 'unavailable')
        self.assertIsNone(point['camera_xyz_m']); self.assertIsNone(point['camera_uncertainty_m'])
        self.assertEqual(self.port.resolve_point(point['camera_original_ref'])['status'], 'unavailable')

    def test_epoch_calibration_and_original_component_clock_fail_closed(self):
        self.calibration_raw = targets.encoded(dict(camera_fixtures.CALIBRATION, session='other-epoch'))
        with self.assertRaisesRegex(ValueError, 'matching_original_calibration'):
            self.port.snapshot()
        self.assertEqual(self.port.snapshots, {})
        self.calibration_raw = targets.encoded(camera_fixtures.CALIBRATION)
        self.raw, _ = bundle(epoch='other-epoch')
        with self.assertRaises(ValueError): self.port.snapshot()
        self.raw = b'not-an-npz'
        with self.assertRaises((ValueError, targets.zipfile.BadZipFile)): self.port.snapshot()
        self.raw, _ = bundle(modify=lambda meta: meta['frames']['rgb'].pop('host_monotonic_timestamp_seconds'))
        with self.assertRaisesRegex(ValueError, 'original_component_clock'): self.port.snapshot()
        self.assertEqual(self.port.snapshots, {})

    def test_action_caps_changed_click_wrong_snapshot_and_pixel_are_bounded(self):
        port = self.make_port('small', max_snapshots=1, max_points=1)
        snapshot = port.snapshot()
        with self.assertRaisesRegex(ValueError, 'snapshot_budget'): port.snapshot()
        point = port.point(snapshot['snapshot_id'], 'click-1', 320, 180, reference())
        self.assertEqual(port.point(snapshot['snapshot_id'], 'click-1', 320, 180, reference()), point)
        with self.assertRaisesRegex(ValueError, 'point_budget'):
            port.point(snapshot['snapshot_id'], 'click-2', 320, 180, reference())
        for sid, u, v in (('foreign-snapshot', 320, 180), (snapshot['snapshot_id'], -1, 180),
                          (snapshot['snapshot_id'], 640, 180), (snapshot['snapshot_id'], 320, True)):
            with self.assertRaises(ValueError): port.point(sid, 'bad-click', u, v, reference())

    def test_npy_header_and_metadata_dimensions_refuse_before_array_or_model_allocation(self):
        altered = io.BytesIO()
        with targets.zipfile.ZipFile(io.BytesIO(self.raw)) as original, targets.zipfile.ZipFile(altered, 'w') as archive:
            for name in original.namelist():
                if name == 'depth_mm.npy':
                    header = io.BytesIO()
                    np.lib.format.write_array_header_1_0(header,
                        {'descr': '<u2', 'fortran_order': False, 'shape': (2**40, 2**40)})
                    archive.writestr(name, header.getvalue())
                else:
                    archive.writestr(name, original.read(name))
        self.raw = altered.getvalue()
        mapper, _ = targets.lab_modules()
        with mock.patch.object(mapper, 'decode_bundle', side_effect=AssertionError('must not allocate')) as decode:
            with self.assertRaisesRegex(ValueError, 'exact_original_array_shape'): self.port.snapshot()
            decode.assert_not_called()
        self.raw, _ = bundle(modify=lambda meta: meta['frames']['depth'].update(width=2**40))
        with mock.patch.object(mapper, 'rgbd_model', side_effect=AssertionError('must not allocate rays')) as model:
            with self.assertRaisesRegex(ValueError, 'fixed_original_metadata_dimensions'): self.port.snapshot()
            model.assert_not_called()

    def test_directory_sync_and_elapsed_deadline_fail_before_publication(self):
        def fail(_): raise OSError('authored-directory-sync-failure')
        self.port.sync = fail
        with self.assertRaises(OSError): self.port.snapshot()
        self.assertEqual(self.port.snapshots, {})
        port = self.make_port('late', monotonic=iter((0, 9)).__next__)
        with self.assertRaisesRegex(ValueError, 'snapshot_deadline'): port.snapshot()
        self.assertEqual(port.snapshots, {})

    def camera_rows(self):
        snapshot = self.port.snapshot(); rows = []
        for index, (u, v) in enumerate(((110, 70), (530, 70), (110, 290), (530, 290), (320, 180))):
            independent = reference(u, v, 'fit' if index < 4 else 'check')
            point = self.port.point(snapshot['snapshot_id'], 'point-' + str(index), u, v, independent)
            self.assertEqual(point['status'], 'candidate')
            rows.append(dict(independent, camera_xyz_m=point['camera_xyz_m'], camera_uncertainty_m=None,
                             camera_original_ref=point['camera_original_ref'], rgb_pixel=[u, v]))
        return {'schema': 'home-desk-camera-targets-1', 'version': 1, 'source_frame': 'rectified_left', 'points': rows}

    def test_fit_reports_unknown_independent_error_and_requires_fixed_original_resolver(self):
        value = distinct.survey(); value['camera'] = self.camera_rows()
        plain = guide.prepare_candidate(value)['camera_registration']
        self.assertEqual(plain['original_resolution'], 'fixed_original_resolver_required')
        candidate = guide.prepare_candidate(value, camera_original_resolver=self.port.resolve_point)
        fit = candidate['camera_registration']
        self.assertEqual(fit['original_resolution'], 'resolved_this_guide')
        self.assertIsNone(fit['camera_uncertainty_m']); self.assertNotIn('error_bound_m', fit['fit'])
        self.assertLess(max(fit['independent_check_errors_m']), .02)
        self.assertFalse(fit['qualified']); self.assertIsNone(candidate['operational_geometry'])
        self.assertTrue(all(row['camera_m'] is None for row in fit['point_uncertainties']))
        for field, replacement in (('camera_xyz_m', [0, 0, 2]), ('rgb_pixel', [319, 180]), ('role', 'check'),
                                    ('camera_uncertainty_m', 0)):
            changed = deepcopy(value); changed['camera']['points'][0][field] = replacement
            with self.assertRaises(ValueError):
                guide.prepare_candidate(changed, camera_original_resolver=self.port.resolve_point)

    def test_foreign_rehashed_reference_cannot_become_an_original_of_this_guide(self):
        value = distinct.survey(); value['camera'] = self.camera_rows()
        row = value['camera']['points'][0]
        original = self.port.resolve_point(row['camera_original_ref'])
        original['camera_xyz_m'][0] += .1
        row['camera_original_ref']['digest'] = targets.sha(targets.encoded(original))
        row['camera_xyz_m'] = original['camera_xyz_m']
        with self.assertRaisesRegex(ValueError, 'not_in_this_guide'):
            guide.prepare_candidate(value, camera_original_resolver=self.port.resolve_point)

    def test_one_resolved_pixel_cannot_be_reissued_as_an_independent_check(self):
        value = distinct.survey(); value['camera'] = self.camera_rows()
        first = self.port.resolve_point(value['camera']['points'][0]['camera_original_ref'])
        independent = reference(110, 70, 'check')
        point = self.port.point(first['snapshot_id'], 'new-check-click', 110, 70, independent)
        value['camera']['points'][-1] = dict(independent, camera_xyz_m=point['camera_xyz_m'],
            camera_uncertainty_m=None, camera_original_ref=point['camera_original_ref'], rgb_pixel=[110, 70])
        with self.assertRaisesRegex(ValueError, 'fit_check_original_pixel_reused'):
            guide.prepare_candidate(value, camera_original_resolver=self.port.resolve_point)

    def test_actual_guide_http_snapshot_click_prepare_fencing_and_stop(self):
        server = guide.Guide(self.base / 'candidate.json', seconds=20, camera_targets=self.port)
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        self.addCleanup(lambda: (server.stopped.set(), thread.join(2)))
        def request(route, body, **override):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            headers = {'Origin': server.origin, 'X-Desk-Nonce': server.nonce, 'Content-Type': 'application/json'}
            headers.update(override)
            connection.request('POST', route, targets.encoded(body), headers)
            response = connection.getresponse(); value = json.loads(response.read()); status = response.status
            connection.close(); return status, value
        for body in ({'upstream': 'http://untrusted.invalid'}, {'path': '/frame.jpg'}):
            self.assertEqual(request('/camera/snapshot', body)[0], 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(request('/camera/snapshot', {}, Origin='http://foreign.invalid')[0], 409)
        status, snapshot = request('/camera/snapshot', {})
        self.assertEqual(status, 200, snapshot)
        rows = []
        for index, (u, v) in enumerate(((110, 70), (530, 70), (110, 290), (530, 290), (320, 180))):
            independent = reference(u, v, 'fit' if index < 4 else 'check')
            status, point = request('/camera/point', dict(snapshot_id=snapshot['snapshot_id'], click_id='http-' + str(index),
                                                        u=u, v=v, reference=independent))
            self.assertEqual(status, 200, point)
            rows.append(dict(independent, camera_xyz_m=point['camera_xyz_m'], camera_uncertainty_m=None,
                             camera_original_ref=point['camera_original_ref'], rgb_pixel=[u, v]))
        value = distinct.survey()
        value['camera'] = dict(schema='home-desk-camera-targets-1', version=1, source_frame='rectified_left', points=rows)
        changed = deepcopy(value); changed['camera']['points'][0]['room_measurement']['height_above_floor_m'] += .1
        self.assertEqual(request('/prepare', changed)[0], 409)
        self.assertFalse((self.base / 'candidate.json').exists())
        self.assertEqual(request('/prepare', value)[0], 200)
        saved = json.loads((self.base / 'candidate.json').read_bytes())
        self.assertEqual(saved['camera_registration']['original_resolution'], 'resolved_this_guide')
        self.assertFalse(saved['qualified']); self.assertFalse(any(saved['authority'].values()))
        self.assertEqual(request('/stop', {})[0], 200)
        thread.join(2); self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()

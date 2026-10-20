import io
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
import numpy as np
import test_paired_runtime as fixtures
from bundle_ingest import BundleIngestor, calibration_content_id
from depth_clock import DepthClock
from registered_depth import RegisteredDepth
from spatial_validation import mount_signature

CLOCK = {'id':'clock-commissioning-1', 'drift_ppm':50, 'max_delta_s':1, 'semantics_bound_s':.002,
         'semantics_receipt':'stereo-rgb-semantics-receipt'}
FLOOR = [[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]


class DepthClockTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def bundle(self, *, depth_device_s=50.25, session='capture1', value=1234, sequence=7):
        meta = {'schema':1, 'session':session, 'depth_alignment':'rectified_left', 'depth_units':'millimetres',
                'invalid_depth':0, 'captured_unix_ns':100_500_000_000,
                'captured_unix_ns_semantics':'bundle_packaging_time_not_sensor_exposure',
                'frames':{name:{'sequence':sequence, 'device_timestamp_seconds':depth_device_s if name!='rgb' else depth_device_s-.004,
                    'host_monotonic_timestamp_seconds':10, 'transformation_valid':False} for name in ('rgb','depth','left','right')}}
        arrays = {'depth_mm':np.full((400,640), value, dtype=np.uint16), 'left':np.zeros((400,640), dtype=np.uint8),
                  'right':np.zeros((400,640), dtype=np.uint8), 'rgb_nv12':np.zeros((540,640), dtype=np.uint8),
                  'metadata_json':np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)}
        output = io.BytesIO(); np.savez_compressed(output, **arrays)
        calibration = {'session':session, 'depth_alignment':'rectified_left', 'depth_units':'millimetres',
                       'eeprom':{'device':'fixture'}, 'stereo_size':[640,400]}
        return BundleIngestor(self.store, site_id='victoria', camera_id='den').ingest(self.session['id'], output.getvalue(), calibration)

    def rgb(self, *, epoch='capture1', frame_device_ns=50_200_000_000, observed_ns=100_400_000_000, **changes):
        receipt = {'schema':3, 'session':epoch, 'sequence':41, 'timing_generation':0,
                   'timestamp_semantics':'causal_exact_frame_exposure_end', 'frame_device_ns':frame_device_ns,
                   'observed_at_unix_ns':observed_ns, 'uncertainty_seconds':.003,
                   'timestamp_quantization_ns':1000, 'validation_id':'causal:'+'a'*32}
        meta = {'site_id':'victoria', 'camera_id':'den', 'capture_epoch':epoch, 'frame_id':f'{epoch}-0-41',
                'sequence':41, 'stream':'rgb', 'width':640, 'height':360, 'format':'jpeg',
                'observed_at':observed_ns/1e9, 'available_at':100.5, 'valid_until':observed_ns/1e9+2.99,
                'uncertainty_seconds':.004, 'clock_mapping_status':'verified', 'clock_mapping_id':receipt['validation_id'],
                'source_timing_receipt':receipt, **changes}
        return self.store.add_media(self.session['id'], b'synthetic-causal-jpeg', 'image/jpeg', meta)

    def calibration(self, bundle, **changes):
        plane = np.full((400,640), 1234, dtype=np.uint16)
        return {'site_id':'victoria', 'camera_id':'den', 'id':bundle['metadata']['calibration_content_id'],
                'status':'validated', 'mount_revision':'den-shelf-1', 'validation_receipt':'sha256:fixture',
                'mount_signature':mount_signature(plane), 'frame':'rectified_left', 'width':640, 'height':400,
                'error_bound_m':.03, 'intrinsics':{'fx':450, 'fy':450, 'cx':319.5, 'cy':199.5}, **changes}

    def clock(self, **kwargs):
        return DepthClock(self.store, site_id='victoria', camera_id='den', clock=CLOCK, **kwargs)

    def test_device_clock_offset_maps_depth_to_rgb_exposure_time(self):
        bundle, rgb = self.bundle(), self.rgb()
        self.store.clock = lambda:100.6
        item = self.clock().derive(self.session['id'], bundle['id'], rgb['id'])
        meta = item['metadata']
        self.assertAlmostEqual(meta['observed_at'], 100.45)
        self.assertAlmostEqual(meta['uncertainty_seconds'], .004+50e-6*.05+1e-6+1e-6+.002)
        self.assertEqual(meta['clock_mapping_status'], 'verified')
        self.assertTrue(meta['clock_mapping_id'].startswith('depth-clock-v1:'))
        self.assertEqual(meta['frame_id'], 'capture1-depth-7')
        self.assertAlmostEqual(meta['valid_until'], meta['observed_at']+3-meta['uncertainty_seconds'])
        self.assertEqual((meta['available_at'], meta['session_time_ms']), (100.6, 450))
        self.assertEqual(meta['calibration_content_id'], calibration_content_id(bundle['metadata']['calibration']))
        self.assertEqual(meta['calibration_id'], bundle['metadata']['calibration_id'])
        self.assertEqual((meta['geometric_validity'], meta['geometric_status_reason']), ('unvalidated', 'calibration_not_validated'))
        self.assertNotIn('depth_validation_receipt', meta)
        self.assertEqual(item['source_key'], 'depth-mapped-v1:'+bundle['id'])
        self.assertEqual(self.store.media_path(self.session['id'], item['id']).read_bytes(),
                         np.full((400,640), 1234, dtype='<u2').tobytes())
        # The original bundle and its receipt-time views are never relabelled.
        self.assertEqual(self.store.get_media(self.session['id'], bundle['id'])['metadata']['clock_mapping_status'], 'unverified')
        # One mapping per bundle: a later retry never refreshes availability.
        self.store.clock = lambda:101
        self.assertEqual(self.clock().derive(self.session['id'], bundle['id'], rgb['id'])['metadata']['available_at'], 100.6)

    def test_real_resolution_preview_clock_maps_once_and_rejects_double_rounded_metadata(self):
        from preview_ingest import PreviewIngestor
        from config import Settings
        from store import Store
        import live_capture_fixtures as original
        stamp = 1800000000000000123
        expected = float(Decimal(stamp) / Decimal(1_000_000_000))
        self.assertNotEqual(expected, stamp / 1e9, 'fixture must exercise the double-rounding boundary')
        work = tempfile.TemporaryDirectory(prefix='real-resolution-depth-clock-')
        self.addCleanup(work.cleanup)
        now = [expected - .2]
        source = Store(Settings(storage_root=Path(work.name), min_free_bytes=0), clock=lambda: now[0])
        self.addCleanup(source.close)
        session, _ = source.create_session('den', 'detection', 'Authored clock', 'precise-clock-request')
        source.transition(session['id'], 'starting')
        source.transition(session['id'], 'recording')
        now[0] = expected + .2
        jpeg, header, raw_bundle, calibration = original.inputs(observed_ns=stamp)
        frame = PreviewIngestor(source, site_id='victoria', camera_id='den',
            clock_validation_id='authored-clock', clock_uncertainty_s=.001).ingest(session['id'], jpeg, header)
        rgb = source.get_media(session['id'], frame['media_ids'][0])
        self.assertEqual(rgb['metadata']['observed_at'], expected)
        bundle = BundleIngestor(source, site_id='victoria', camera_id='den').ingest(
            session['id'], raw_bundle, calibration)
        mapper = DepthClock(source, site_id='victoria', camera_id='den', clock=CLOCK)
        derived = mapper.derive(session['id'], bundle['id'], rgb['id'])
        self.assertEqual(derived['metadata']['clock_mapping']['rgb_frame_device_ns'],
                         rgb['metadata']['source_timing_receipt']['frame_device_ns'])
        self.assertLess(derived['metadata']['observed_at'], expected)

        # A distinct source bundle avoids the existing exact cached retry path.
        _, _, raw2, calibration2 = original.inputs(sequence=8, observed_ns=stamp)
        second = BundleIngestor(source, site_id='victoria', camera_id='den').ingest(
            session['id'], raw2, calibration2)
        forged = deepcopy(rgb['metadata'])
        forged['observed_at'] = stamp / 1e9
        bad = source.add_media(session['id'], jpeg, 'image/jpeg', forged)
        with self.assertRaisesRegex(ValueError, 'same-session causal RGB exposure receipt required'):
            mapper.derive(session['id'], second['id'], bad['id'])

    def test_geometry_is_validated_only_for_matching_content_and_unmoved_mount(self):
        bundle, rgb = self.bundle(), self.rgb()
        self.store.clock = lambda:100.6
        item = self.clock(calibration=self.calibration(bundle)).derive(self.session['id'], bundle['id'], rgb['id'])
        meta = item['metadata']
        self.assertEqual((meta['geometric_validity'], meta['depth_validation_receipt'], meta['mount_revision']),
                         ('validated', 'sha256:fixture', 'den-shelf-1'))
        for changes, reason in (({'id':'other-content'}, 'calibration_content_changed'),
                                ({'mount_signature':None}, 'mount_signature_missing'),
                                ({'mount_signature':mount_signature(np.full((400,640), 3000, dtype=np.uint16))}, 'mount_changed')):
            with self.subTest(reason=reason):
                other = self.bundle(depth_device_s=50.3, sequence=8+len(reason))
                derived = self.clock(calibration=self.calibration(bundle, **changes)).derive(self.session['id'], other['id'], rgb['id'])
                self.assertEqual((derived['metadata']['geometric_validity'], derived['metadata']['geometric_status_reason']),
                                 ('unvalidated', reason))
        with self.assertRaises(ValueError):
            self.clock(calibration=self.calibration(bundle, status='fitted'))

    def test_camera_restart_keeps_calibration_content_binding(self):
        bundle, rgb = self.bundle(), self.rgb()
        calibration = self.calibration(bundle)
        restarted, rgb2 = self.bundle(session='capture2', depth_device_s=3.0), self.rgb(epoch='capture2', frame_device_ns=2_990_000_000)
        self.store.clock = lambda:100.6
        derived = self.clock(calibration=calibration).derive(self.session['id'], restarted['id'], rgb2['id'])
        self.assertEqual(derived['metadata']['geometric_validity'], 'validated')
        self.assertNotEqual(restarted['metadata']['calibration_id'], bundle['metadata']['calibration_id'])

    def test_unmappable_inputs_are_refused(self):
        bundle = self.bundle()
        self.store.clock = lambda:100.6
        cases = [(self.rgb(epoch='capture2'), 'same-session'),
                 (self.rgb(frame_device_ns=48_000_000_000), 'too far'),
                 (self.rgb(clock_mapping_status='unverified'), 'same-session'),
                 (self.rgb(source_timing_receipt=None), 'same-session'),
                 (self.rgb(observed_ns=96_000_000_000), 'stale'),
                 (self.rgb(observed_ns=102_000_000_000), 'future')]
        for rgb, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.clock().derive(self.session['id'], bundle['id'], rgb['id'])
        for clock in ({**CLOCK, 'semantics_receipt':''}, {**CLOCK, 'max_delta_s':5}, {**CLOCK, 'drift_ppm':float('nan')}, None):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                DepthClock(self.store, site_id='victoria', camera_id='den', clock=clock)
        # A client-posted NPZ carries a different key shape and is never mapped.
        posted = self.store.add_media(self.session['id'], self.store.media_path(self.session['id'], bundle['id']).read_bytes(),
            'application/x-npz', bundle['metadata'], '["capture1","rgbd",7]')
        with self.assertRaisesRegex(ValueError, 'server-ingested'):
            self.clock().derive(self.session['id'], posted['id'], self.rgb()['id'])

    def test_mapped_validated_depth_is_measured_after_specialist_latency(self):
        bundle, rgb = self.bundle(), self.rgb()
        calibration = self.calibration(bundle)
        self.store.clock = lambda:100.6
        item = self.clock(calibration=calibration).derive(self.session['id'], bundle['id'], rgb['id'])
        meta = item['metadata']
        frame = {k: meta[k] for k in ('site_id','camera_id','frame_id','capture_epoch','observed_at')}
        frame.update(session_id=self.session['id'], media_ids=[item['id']])
        # The RGB detection completes 1.5 s after its exposure.
        result = RegisteredDepth(self.store).surface(frame, [300,180,340,260], calibration, roi_frame='rectified_left',
            as_of=rgb['metadata']['observed_at']+1.5, reference_observed_at=meta['observed_at']-.02,
            valid_until=rgb['metadata']['valid_until'], floor_matrix=FLOOR)
        self.assertEqual(result['status'], 'measured')
        self.assertAlmostEqual(result['depth_m'], 1.234)
        self.assertEqual(result['media_ids'], [item['id']])


if __name__ == '__main__': unittest.main()

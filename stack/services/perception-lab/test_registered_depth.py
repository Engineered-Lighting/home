import unittest
import uuid
import numpy as np
import test_paired_runtime as fixtures
from registered_depth import RegisteredDepth

# Camera x right, y down, z forward -> map x, y (floor) and z up.
FLOOR = [[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]


class RegisteredDepthTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown
    frame = fixtures.PairedRuntimeTests.frame

    def source(self, values=None, *, derived=True, **changes):
        frame = self.frame()
        values = np.full((20, 20), 2000, dtype='<u2') if values is None else values
        height, width = values.shape
        meta = {k: frame[k] for k in ('site_id', 'camera_id', 'frame_id', 'capture_epoch', 'observed_at')}
        meta.update(format='depth_u16_le', width=width, height=height, units='millimetres',
                    invalid_value=0, alignment_frame='rectified_left', geometric_validity='validated',
                    depth_validation_receipt='synthetic-test-only', clock_mapping_status='verified',
                    clock_mapping_id='synthetic-clock', calibration_id='session-digest',
                    calibration_content_id='synthetic-cal', mount_revision='mount-test',
                    derivation='depth-clock-v1', source_media_id=uuid.uuid4().hex,
                    available_at=frame['observed_at']+.05, valid_until=frame['observed_at']+2.9)
        # Only the server-side clock mapper uses this key; HTTP samples cannot.
        key = 'depth-mapped-v1:'+meta['source_media_id'] if derived else None
        meta.update(changes)
        item = self.store.add_media(self.session['id'], values.tobytes(), 'application/octet-stream', meta, key)
        frame['media_ids'] = [item['id']]
        cal = dict(site_id=frame['site_id'], camera_id=frame['camera_id'], id='synthetic-cal',
                   width=width, height=height, frame='rectified_left', mount_revision='mount-test',
                   validation_receipt='synthetic-test-only', error_bound_m=.02,
                   intrinsics=dict(fx=100, fy=100, cx=(width-1)/2, cy=(height-1)/2))
        return frame, cal, item

    def read(self, frame, cal, roi=(0, 0, 20, 20), *, latency=.1, reference=None, valid_until=None, **kwargs):
        reference = frame['observed_at'] if reference is None else reference
        return RegisteredDepth(self.store).surface(frame, list(roi), cal, roi_frame='rectified_left',
            as_of=reference+latency, reference_observed_at=reference,
            valid_until=reference+3 if valid_until is None else valid_until, floor_matrix=FLOOR, **kwargs)

    def test_numerical_bytes_to_horizontal_support_uncertainty(self):
        frame, cal, _ = self.source()
        result = self.read(frame, cal)
        self.assertEqual(result['xyz_m'], [0, 0, 2])
        self.assertEqual(result['support'], 196)
        # Support spans x in [-0.13, 0.13] m at 2 m; the vertical span is not horizontal.
        self.assertAlmostEqual(result['horizontal_support_radius_m'], .13)
        self.assertAlmostEqual(result['vertical_support_extent_m'], .26)
        self.assertAlmostEqual(result['uncertainty_m'], .15)
        self.assertEqual(result['uncertainty_kind'], 'horizontal_support_extent_plus_sensor_error')
        self.assertTrue(result['surface_only'])
        self.assertEqual(result['front_surface_bias'], 'toward_camera')
        self.assertEqual(result['media_ids'], frame['media_ids'])
        self.assertEqual(result['calibration_version'], 'synthetic-cal')

    def test_vertical_body_extent_is_not_horizontal_uncertainty(self):
        frame, cal, _ = self.source(np.full((40, 20), 2000, dtype='<u2'))
        result = self.read(frame, cal, (5, 0, 15, 40))
        self.assertAlmostEqual(result['vertical_support_extent_m'], .54)
        self.assertAlmostEqual(result['uncertainty_m'], .07+.02)

    def test_specialist_latency_uses_rgb_observation_and_source_expiry(self):
        frame, cal, _ = self.source()
        # 1.5 s specialist latency: age is measured against the RGB observation.
        result = self.read(frame, cal, latency=1.5, reference=frame['observed_at']-.01, valid_until=frame['observed_at']+2)
        self.assertEqual(result['status'], 'measured')
        self.assertAlmostEqual(result['age_s'], 0)
        self.assertAlmostEqual(result['skew_s'], .01)
        self.assertEqual(result['valid_until'], frame['observed_at']+2)
        self.assertEqual(result['available_at'], frame['observed_at']-.01+1.5)
        # Never beyond three seconds of the source observation or the depth record.
        self.assertEqual(self.read(frame, cal, valid_until=frame['observed_at']+99)['valid_until'], frame['observed_at']+2.9)
        self.assertEqual(self.read(frame, cal, latency=2.95, valid_until=frame['observed_at']+99)['reason'],
                         'outside_valid_observation_interval')

    def test_age_skew_and_availability_are_causal_and_configurable(self):
        frame, cal, _ = self.source()
        self.assertEqual(self.read(frame, cal, reference=frame['observed_at']+.06)['reason'], 'unsynchronized')
        self.assertEqual(self.read(frame, cal, reference=frame['observed_at']+.06, max_skew_s=.1)['status'], 'measured')
        self.assertEqual(self.read(frame, cal, reference=frame['observed_at']+.06, max_skew_s=.1, max_age_s=.05)['reason'],
                         'depth_stale_or_clock_invalid')
        self.assertEqual(self.read(frame, cal, latency=.01)['reason'], 'depth_not_available_at_cutoff')

    def test_invalid_occluded_and_discontinuous_abstain(self):
        for values, kwargs, reason in (
            (np.zeros((20,20), dtype='<u2'), {}, 'insufficient_valid_support'),
            (np.full((20,20), 2000, dtype='<u2'), {'visible':False}, 'occluded_surface'),
            (np.tile(np.array([1000]*10+[3000]*10, dtype='<u2'), (20,1)), {}, 'depth_discontinuity')):
            with self.subTest(reason=reason):
                frame, cal, _ = self.source(values)
                self.assertEqual(self.read(frame, cal, **kwargs)['reason'], reason)

    def test_uncommissioned_mismatched_or_client_supplied_metadata_refused(self):
        for changes in ({'geometric_validity':'unvalidated'}, {'clock_mapping_status':'unverified'},
                        {'alignment_frame':'rgb'}, {'calibration_content_id':'other'}, {'width':19},
                        {'mount_revision':'moved'}, {'derivation':None}, {'derived':False}):
            with self.subTest(changes=changes):
                frame, cal, _ = self.source(**changes)
                with self.assertRaises(ValueError): self.read(frame, cal)
        frame, cal, _ = self.source()
        with self.assertRaises(ValueError):
            RegisteredDepth(self.store).surface(frame, [0,0,20,20], cal, roi_frame='rgb', as_of=frame['observed_at'],
                reference_observed_at=frame['observed_at'], valid_until=frame['observed_at']+1, floor_matrix=FLOOR)
        # The session digest may change on camera restart; only content binds.
        frame, cal, _ = self.source(calibration_id='another-camera-session')
        self.assertEqual(self.read(frame, cal)['status'], 'measured')

    def test_corrupted_registered_data_refused(self):
        frame, cal, item = self.source()
        self.store.media_path(self.session['id'], item['id']).write_bytes(bytes(800))
        with self.assertRaisesRegex(ValueError, 'integrity'): self.read(frame, cal)


if __name__ == '__main__': unittest.main()

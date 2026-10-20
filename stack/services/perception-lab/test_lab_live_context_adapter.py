"""Original-shaped Lab events and generated stereo references; no external I/O."""
from copy import deepcopy
import unittest

from lab_live_context_adapter import LabLiveContextAdapter, MISSING_QUALIFICATION
from test_room_facts import seated, site_map
from test_room_mapper import CALIBRATION, bundle_bytes


SESSION = {'id': 'lab-session', 'site_id': 'victoria', 'camera_id': 'den'}


def event(event_id=12, *, observed=100., x=.3):
    record, depth = seated(x=x)
    result = {'schema_version': 1, 'coordinate_space': 'source_pixels', 'frame_id': 'rgb-source-10', **record}
    payload = {'site_id': 'victoria', 'camera_id': 'den', 'arm': 'a', 'generation': 1,
        'status': 'completed', 'observed_at': observed, 'available_at': observed+.1,
        'valid_until': observed+3, 'capture_epoch': 'epoch-1', 'frame_id': 'rgb-source-10',
        'evidence_roots': ['original-camera-root'], 'result': result,
        'arm_snapshot': {'frame': {'id': 'rgb-source-10', 'width': 640, 'height': 360}, 'model': 'pinned-model'}}
    return {'id': event_id, 'kind': 'perception.output', 'session_id': SESSION['id'], 'payload': payload}, depth


def geometry(depth, *, stamp=10.):
    return {'bundle': bundle_bytes(depth, stamp=stamp), 'calibration': deepcopy(CALIBRATION),
        'site_map': site_map(), 'frame_metadata': {'stream': 'rgb', 'frame_id': 'rgb-source-10',
            'capture_epoch': 'epoch-1', 'source_timing_receipt': {'frame_device_ns': 10_000_000_000}}}


class LabLiveContextAdapterTests(unittest.TestCase):
    def adapter(self, **kwargs):
        return LabLiveContextAdapter(SESSION, arm='a', generation=1, **kwargs)

    def test_real_shaped_rgb_pose_remains_usable_without_depth_or_qualification(self):
        original, _ = event()
        result = self.adapter().consume(original, received_at=100.2)
        self.assertEqual(result['image']['person_count'], 1)
        self.assertEqual(result['image']['pose_instance_count'], 1)
        self.assertGreaterEqual(result['image']['supported_joint_count'], 10)
        self.assertEqual(result['lineage']['frame_id'], original['payload']['frame_id'])
        self.assertEqual(result['lineage']['capture_epoch'], 'epoch-1')
        self.assertEqual(result['lineage']['evidence_roots'], ['original-camera-root'])
        self.assertEqual(result['lineage']['valid_until'], 103)
        self.assertEqual(result['lineage']['first_consumer_received_at'], 100.2)
        self.assertIsNone(result['operational_geometry'])
        self.assertFalse(result['beam_authority'])
        self.assertIn('registered_depth_candidate_unavailable', result['missing_qualification'])

    def test_actual_existing_stereo_measurement_moves_without_acquiring_authority(self):
        first, depth = event()
        adapter = self.adapter()
        a = adapter.consume(first, received_at=100.2, geometry=geometry(depth))
        second, moved_depth = event(13, observed=101., x=.6)
        b = adapter.consume(second, received_at=101.2, geometry=geometry(moved_depth))
        self.assertEqual(a['metric']['status'], 'candidate', a['metric'])
        self.assertEqual(a['metric']['frame'], 'z_up_metric_floor0')
        self.assertAlmostEqual(a['metric']['torso_m'][0], .3, delta=.08)
        self.assertGreater(b['metric']['torso_m'][0]-a['metric']['torso_m'][0], .15)
        self.assertIsNotNone(a['metric']['body_points_m']['hand_left'])
        self.assertIsNotNone(a['metric']['body_points_m']['hand_right'])
        self.assertIsNone(a['metric']['uncertainty_m'])
        self.assertFalse(a['metric']['qualified'])
        self.assertEqual(a['missing_qualification'], list(MISSING_QUALIFICATION))
        self.assertIsNone(a['operational_geometry'])

    def test_duplicate_cannot_refresh_and_mutated_identity_rejects(self):
        original, _ = event()
        adapter = self.adapter()
        adapter.consume(original, received_at=100.2)
        duplicate = adapter.consume(original, received_at=200.)
        self.assertEqual(duplicate['status'], 'duplicate')
        self.assertIsNone(duplicate['image'])
        changed = deepcopy(original)
        changed['payload']['valid_until'] += 1
        with self.assertRaisesRegex(ValueError, 'lab_event_identity_conflict'):
            adapter.consume(changed, received_at=100.3)

    def test_stale_future_wrong_scope_and_generation_reject(self):
        original, _ = event()
        for name, received, changes in (
                ('expiry_exclusive', 103, {}), ('future', 100.05, {}),
                ('wrong_site', 100.2, {'site_id': 'la'}), ('wrong_arm', 100.2, {'arm': 'b'}),
                ('wrong_generation', 100.2, {'generation': 2}),
                ('missing_capture_epoch', 100.2, {'capture_epoch': ''})):
            with self.subTest(name):
                row = deepcopy(original)
                row['payload'].update(changes)
                with self.assertRaises(ValueError):
                    self.adapter().consume(row, received_at=received)

    def test_late_observation_and_budget_never_replace_current_source(self):
        original, _ = event()
        adapter = self.adapter(max_events=2)
        adapter.consume(original, received_at=100.2)
        older, _ = event(13, observed=99.)
        self.assertEqual(adapter.consume(older, received_at=100.3)['status'], 'late')
        third, _ = event(14, observed=101.)
        with self.assertRaisesRegex(ValueError, 'lab_candidate_event_budget_exhausted'):
            adapter.consume(third, received_at=101.2)

    def test_model_result_from_another_rgb_frame_cannot_supply_candidates(self):
        original, _ = event()
        original['payload']['result']['frame_id'] = 'another-rgb-frame'
        with self.assertRaisesRegex(ValueError, 'lab_event_binding_refused'):
            self.adapter().consume(original, received_at=100.2)

    def test_existing_bundle_must_match_frame_epoch_calibration_mount_and_skew(self):
        original, depth = event()
        cases = {}
        wrong = geometry(depth); wrong['frame_metadata']['frame_id'] = 'different-rgb'
        cases['metric_source_epoch_or_frame_mismatch'] = wrong
        wrong = geometry(depth); wrong['calibration']['session'] = 'different-epoch'
        cases['metric_source_epoch_or_frame_mismatch_epoch'] = wrong
        cases['registered_depth_outside_existing_pair_window'] = geometry(depth, stamp=10.31)
        wrong = geometry(depth); wrong['frame_metadata']['source_timing_receipt'] = {}
        cases['original_rgb_device_receipt_required'] = wrong
        wrong = geometry(depth); wrong['calibration']['eeprom']['changed'] = True
        cases['calibration_differs_from_saved_map'] = wrong
        cases['saved_camera_mount_not_verified'] = geometry(depth*0)
        for name, inputs in cases.items():
            with self.subTest(name):
                result = self.adapter().consume(original, received_at=100.2, geometry=inputs)
                self.assertEqual(result['image']['person_count'], 1)
                self.assertEqual(result['metric']['status'], 'unavailable')
                self.assertIsNone(result['operational_geometry'])
                self.assertIn(result['metric']['reason'], result['missing_qualification'])


if __name__ == '__main__':
    unittest.main()

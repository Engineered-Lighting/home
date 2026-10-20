"""Adversarial Interface A cases using actual composition and external I/O fakes."""
from copy import deepcopy
import base64
import json
import threading
import time
import unittest

import live_capture_fixtures as capture
from journal import digest
import test_live_spatial as spatial_tests


class LiveSpatialAdversarialTests(unittest.TestCase):
    setUp = spatial_tests.LiveSpatialTests.setUp
    control_clock = spatial_tests.LiveSpatialTests.control_clock
    snapshot = spatial_tests.LiveSpatialTests.snapshot
    prepare = spatial_tests.LiveSpatialTests.prepare
    construct = spatial_tests.LiveSpatialTests.construct
    current_spatial = spatial_tests.LiveSpatialTests.current_spatial
    retained_snapshot = spatial_tests.LiveSpatialTests.retained_snapshot
    advance = spatial_tests.LiveSpatialTests.advance

    def camera_pair(self, token, *, timeout):
        pair = json.loads(spatial_tests.LiveSpatialTests.camera_pair(self, token, timeout=timeout))
        mutation = getattr(self, 'camera_mutation', None)
        if mutation == 'clock':
            receipt = capture.decoded_header(pair['header'])
            receipt['host_boot_id'] = '00000000-0000-0000-0000-000000000002'
            pair['header'] = base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()
        elif mutation == 'registration':
            pair['calibration']['eeprom']['synthetic_device'] = 'changed-uncommissioned-device'
        elif mutation == 'mount':
            metadata = capture.frame_metadata(sequence=self.sequence, depth_sequence=41 + self.sequence - 7,
                depth_device_s=50.19 + self.sequence - 7, rgb_device_s=50.2 + self.sequence - 7)
            pair['bundle_b64'] = base64.b64encode(capture.bundle(metadata, value=2500)).decode()
        return json.dumps(pair).encode()

    def test_first_unavailable_work_then_supported_work_opens_its_own_head(self):
        self.prepare()
        self.joint_scores[9] = 0
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        initial = self.current_spatial()[0]
        self.assertIsNone(initial['subject_ref'])
        self.assertEqual(initial['parts']['work']['status'], 'unobservable')
        self.joint_scores[9] = .9
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        supplied = [p for p in self.current_spatial() if p['parts']['work']['status'] == 'supplied']
        self.assertEqual(len(supplied), 1, cycle)
        self.assertEqual(supplied[0]['person_ref'], initial['person_ref'])
        self.assertIsNotNone(supplied[0]['subject_ref'])
        self.assertIsNone(supplied[0]['head']['supersedes'])

    def test_new_unavailable_work_head_never_exposes_the_old_supported_work(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.joint_scores[9] = 0
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        current = self.current_spatial()
        self.assertEqual(len(current), 1, cycle)
        self.assertEqual(current[0]['person_ref'], first['person_ref'])
        self.assertEqual(current[0]['subject_ref'], first['subject_ref'])
        self.assertEqual(current[0]['head']['supersedes']['id'], first['id'])
        self.assertEqual(current[0]['parts']['work']['status'], 'unobservable')
        self.assertNotIn('shape', current[0]['parts']['work'])
        self.assertNotEqual(current[0]['id'], first['id'])

    def _invalid_capture_cannot_transfer_identity(self, mutation):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.camera_mutation = mutation
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(cycle['detector'], 'not_run', cycle)
        self.assertEqual(self.current_spatial(), [])
        self.camera_mutation = None
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        recovered = self.current_spatial()[0]
        self.assertNotEqual(recovered['person_ref']['track_epoch'], first['person_ref']['track_epoch'])
        self.assertNotEqual(recovered['subject_ref'], first['subject_ref'])
        self.assertGreater(recovered['source_generation'], first['source_generation'])

    def test_uncommissioned_registration_retires_geometry_and_anonymous_identity(self):
        self._invalid_capture_cannot_transfer_identity('registration')

    def test_changed_mount_retires_geometry_and_anonymous_identity(self):
        self._invalid_capture_cannot_transfer_identity('mount')

    def test_changed_source_clock_retires_geometry_and_anonymous_identity(self):
        self._invalid_capture_cannot_transfer_identity('clock')

    def _repin_manifest(self):
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])

    def test_missing_body_measurement_bound_cannot_create_a_qualified_identity(self):
        self.prepare()
        self.live_config['manifest']['sources'][capture.SOURCE]['part_policies']['body'] = None
        self._repin_manifest()
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        self.assertEqual(self.current_spatial(), [], cycle)
        self.assertFalse(any(item['payload'].get('status') == 'supplied' for item in self.snapshot()['items']))

    def test_unqualified_torso_anchor_depth_cannot_establish_person_or_work_identity(self):
        self.prepare()
        bound = self.live_config['manifest']['sources'][capture.SOURCE]['part_policies']['body']
        bound['components_m']['depth'] = .001
        bound['bound_at_observation_m'] = .131
        self._repin_manifest()
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        self.assertEqual(self.current_spatial(), [], cycle)
        self.assertFalse(any(item['payload'].get('person_ref') is not None for item in self.snapshot()['items']))

    def test_revoked_native_call_cannot_publish_after_permission_returns(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        entered, release = threading.Event(), threading.Event()
        sdk = self.pose_worker.model
        def blocked_sdk(image, bboxes):
            entered.set()
            release.wait(2)
            return sdk(image, bboxes)
        self.pose_worker.model = blocked_sdk
        self.addCleanup(release.set)
        self.advance()
        cycles = []
        worker = threading.Thread(target=lambda: cycles.append(graph.step()), daemon=True)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.permitted = False
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(cycles[0]['status'], 'failed', cycles)
        self.assertEqual(cycles[0]['spatial'], 'unavailable', cycles)
        self.permitted = True
        release.set()
        until = time.monotonic() + 2
        while graph.status()['inflight'] and time.monotonic() < until:
            time.sleep(.01)
        result = graph.close()
        self.assertEqual(result['status'], 'stopped', result)
        self.assertTrue(result['buffers_cleared'])
        self.assertEqual(self.retained_snapshot()['items'], [])
        self.assertEqual(graph.step()['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()

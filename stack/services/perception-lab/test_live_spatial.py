"""Interface A: actual camera/model/spatial graph to the public local journal."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from PIL import Image

import live_capture_fixtures as capture
import live_spatial_fixtures as spatial
import test_live_acquisition as acquisition_tests
from journal import Journal, PREFIX, digest
from live_test_helpers import LiveHttp
from producer_development import run_development_session
from test_live_capture_policy2 import rotating_inputs


class LiveSpatialTests(unittest.TestCase):
    setUp = acquisition_tests.ProductionAcquisitionTests.setUp
    control_clock = acquisition_tests.ProductionAcquisitionTests.control_clock
    snapshot = acquisition_tests.ProductionAcquisitionTests.snapshot

    def prepare(self):
        (self.live_config, self.policy, self.method, self.journal_grant,
         self.spatial_policy, self.spatial_method) = spatial.commission(
            self.live_config, self.policy, self.method, self.journal_grant, self.refs)
        self.config.update(schema='home-production-acquisition-2', version=2,
                           spatial_method_ref=deepcopy(self.spatial_method))
        self.local_grant['configuration_digest'] = digest(self.config)
        self.local_grant['operations'].append('spatial')
        self.boxes[:] = [[220, 40, 420, 350]]
        self.joints, self.joint_scores = deepcopy(spatial.JOINTS), [.9] * 17
        self.depth_value = 1234
        self.camera_down = False
        self.calibration_change = False
        self.changed_pixels = False
        self.missing_wrist_depth = False
        def pose_sdk(image, bboxes):
            self.trace.append('pose')
            self.sdk_calls.append(('pose', image.copy(), deepcopy(bboxes)))
            return [deepcopy(self.joints) for _ in bboxes], [list(self.joint_scores) for _ in bboxes]
        self.pose_worker.model = pose_sdk

    def camera_pair(self, token, *, timeout):
        self.trace.append('capture')
        if getattr(self, 'camera_down', False):
            raise OSError('synthetic external camera unavailable')
        jpeg, header, _, calibration = rotating_inputs(self.sequence, nonce=format(self.sequence, 'x') * 32)
        if getattr(self, 'changed_pixels', False):
            encoded = io.BytesIO()
            Image.new('RGB', (640, 360), (23, 177, 81)).save(encoded, format='JPEG', quality=85)
            jpeg = encoded.getvalue()
            receipt = capture.decoded_header(header)
            receipt['jpeg_sha256'] = hashlib.sha256(jpeg).hexdigest()
            header = base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()
        if getattr(self, 'calibration_change', False):
            calibration['eeprom']['synthetic_device'] = 'changed-extrinsic-calibration'
        meta = capture.frame_metadata(sequence=self.sequence, depth_sequence=41 + self.sequence - 7,
            depth_device_s=50.19 + self.sequence - 7, rgb_device_s=50.2 + self.sequence - 7)
        bundle = capture.bundle(meta, value=self.depth_value)
        if getattr(self, 'missing_wrist_depth', False):
            bundle = spatial.bundle_with_missing_wrist_depth(meta, value=self.depth_value)
        return json.dumps({'schema': 'home-camera-pair-1', 'version': 1,
            'jpeg_b64': base64.b64encode(jpeg).decode(), 'header': header,
            'bundle_b64': base64.b64encode(bundle).decode(), 'calibration': calibration}).encode()

    def construct(self, **options):
        from live_acquisition import create_production_acquisition
        self.graph = create_production_acquisition(self.config, live_config=self.live_config,
            capture_policy=self.policy, capture_method=self.method, spatial_policy=self.spatial_policy,
            journal_path=Path(self.work.name) / 'synthetic-spatial.sqlite', journal_grants=[self.journal_grant],
            publication_token=capture.TOKEN, detector=self.detector, pose=self.pose_worker,
            local_authority=lambda: deepcopy(self.local_grant) if self.permitted else None,
            household_enabled=lambda: self.enabled, occupancy=self.occupancy,
            clock=lambda: self.now, monotonic=lambda: self.mono, camera_dependencies=self.camera,
            control_clock=self.control_clock, rss=lambda: 64 * 1024**2, **options)
        self.addCleanup(self.graph.close)
        return self.graph

    def current_spatial(self):
        return [item['payload'] for item in self.snapshot()['items']
                if item['payload'].get('method_ref') == self.spatial_method]

    def retained_snapshot(self):
        """Read the retained journal after producer shutdown through its protocol."""
        journal = Journal(Path(self.work.name) / 'synthetic-spatial.sqlite', grants=[self.journal_grant],
            profile='home-live-shadow-1', live_config=self.live_config, clock=lambda: self.now,
            live_receipt_resolver=lambda *_: None)
        server = LiveHttp(journal)
        try:
            status, body = server.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room', token=capture.TOKEN)
            self.assertEqual(status, 200, body)
            return body
        finally:
            server.close()
            journal.close()

    def advance(self):
        self.sequence += 1
        self.now += 1
        self.mono += 1

    def repin_spatial_policy(self):
        """An intentional new test method needs new manifest and grant digests."""
        old = deepcopy(self.spatial_method)
        self.spatial_method['digest'] = digest(self.spatial_policy)
        source = self.live_config['manifest']['sources'][capture.SOURCE]
        for key in ('method_refs', 'association_method_refs'):
            source[key] = [deepcopy(self.spatial_method) if item == old else item for item in source[key]]
        self.config['spatial_method_ref'] = deepcopy(self.spatial_method)
        self.local_grant['configuration_digest'] = digest(self.config)
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])

    def assertVectorAlmostEqual(self, actual, expected, places=5):
        self.assertEqual(len(actual), len(expected))
        for value, wanted in zip(actual, expected):
            self.assertAlmostEqual(value, wanted, places=places)

    def test_real_factory_lifts_fictional_pose_depth_into_independent_work_bounds(self):
        self.prepare()
        cycle = self.construct().step()
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'], cycle)
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        outputs = self.current_spatial()
        self.assertEqual(len(outputs), 1, cycle)
        result = outputs[0]
        self.assertEqual(result['status'], 'supplied', result)
        self.assertEqual(result['association']['person_status'], 'qualified')
        self.assertEqual(result['association']['subject_status'], 'qualified')
        work = result['parts']['work']
        self.assertEqual(work['basis'], 'inferred')
        self.assertVectorAlmostEqual(work['shape']['center_m'], spatial.WORK_CENTER)
        self.assertVectorAlmostEqual(work['shape']['half_extents_m'], spatial.WORK_EXTENTS)
        self.assertEqual(result['sensor_roots'], ['camera:synthetic-oak'])
        self.assertEqual(result['observed_at'], capture.NOW)
        self.assertEqual(result['available_at'], self.now)
        self.assertEqual(result['clock']['original_value'], '1800000000000000000')
        self.assertEqual(result['registration'], self.live_config['manifest']['sources'][capture.SOURCE]['registration'])
        self.assertEqual(result['head']['position']['capture_seq'], 7)
        self.assertEqual(set(item['part_id'] for item in result['parts']['body']['shape']['items']), set(spatial.BODY_IDS))
        self.assertEqual(result['parts']['protection']['shape']['items'][0]['part_id'], 'head_exclusion')
        receipt_ref = next(ref for ref in result['original_receipt_refs'] if ref['component_id'] == 'spatial')
        original = self.graph.spatial.original_receipt(receipt_ref['store_id'], receipt_ref['receipt_id'])
        self.assertEqual(original['schema'], 'home-original-spatial-measurement-1')
        self.assertEqual(len(original['joints']), 17)
        for index in (9, 10):
            self.assertEqual(original['joints'][index]['status'], 'measured')
            self.assertGreaterEqual(original['joints'][index]['support'], 3)
            self.assertLessEqual(original['joints'][index]['spread_m'], .03)
        self.assertFalse(cycle['focused_output'])

    def test_moving_estimate_keeps_person_and_subject_without_activity_or_dwell(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.joints = [[x + 10, y] for x, y in self.joints]
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        second = self.current_spatial()[0]
        self.assertEqual(first['person_ref'], second['person_ref'])
        self.assertEqual(first['subject_ref'], second['subject_ref'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['parts']['work']['shape'], second['parts']['work']['shape'])
        self.assertVectorAlmostEqual(second['parts']['work']['shape']['center_m'], [1.02468, 3.234, 1.45064])
        self.assertEqual(second['head']['supersedes']['id'], first['id'])
        self.assertFalse(any(key in second for key in ('activity', 'engagement', 'episode_id', 'probability')))

    def test_unobserved_wrist_cannot_be_filled_or_remove_an_opaque_limb(self):
        self.prepare()
        self.joint_scores[9] = 0
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        result = self.current_spatial()[0]
        for name in ('work', 'body', 'hand_left'):
            self.assertEqual(result['parts'][name]['status'], 'unobservable', (name, result))
            self.assertNotIn('shape', result['parts'][name])
        self.assertEqual(result['parts']['hand_right']['status'], 'supplied', result)
        self.assertEqual(result['parts']['head']['status'], 'supplied', result)
        self.assertEqual(result['parts']['protection']['status'], 'supplied', result)

    def test_multiple_people_retire_old_binding_and_recovery_uses_new_identity(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.boxes.append([430, 40, 620, 350])
        self.advance()
        cycle = graph.step()
        self.assertFalse(cycle['focused_output'])
        self.assertFalse(any(item['payload'].get('status') == 'supplied' for item in self.snapshot()['items']), cycle)
        self.boxes.pop()
        self.advance()
        self.assertEqual(graph.step()['spatial'], 'completed')
        recovered = self.current_spatial()[0]
        self.assertNotEqual(first['person_ref']['track_epoch'], recovered['person_ref']['track_epoch'])
        self.assertNotEqual(first['subject_ref'], recovered['subject_ref'])
        self.assertGreater(recovered['source_generation'], first['source_generation'])

    def test_missing_commissioned_work_bound_withholds_only_unsupported_part(self):
        self.prepare()
        self.live_config['manifest']['sources'][capture.SOURCE]['part_policies']['work'] = None
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        result = self.current_spatial()[0]
        self.assertEqual(result['parts']['work']['status'], 'unobservable')
        self.assertEqual(result['parts']['work']['reason'], 'missing_measurement_bound')
        self.assertEqual(result['parts']['body']['status'], 'supplied')

    def test_duplicate_original_pair_cannot_publish_new_spatial_identity_or_refresh(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.mono += .6
        cycle = graph.step()
        self.assertEqual(cycle['reason'], 'duplicate_capture', cycle)
        self.assertEqual(self.current_spatial(), [first])
        self.assertEqual(self.trace, ['capture', 'detector', 'pose', 'capture'])

    def test_local_authority_loss_withdraws_spatial_support(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        self.permitted = False
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['reason'], 'local_acquisition_revoked')
        self.assertEqual(self.retained_snapshot()['items'], [])
        self.assertTrue(graph.status()['closed'])

    def test_source_gap_removes_current_geometry_and_cannot_transfer_identity(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.camera_down = True
        self.advance()
        self.assertEqual(graph.step()['status'], 'failed')
        self.assertEqual(self.current_spatial(), [])
        self.camera_down = False
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        second = self.current_spatial()[0]
        self.assertNotEqual(second['person_ref']['track_epoch'], first['person_ref']['track_epoch'])
        self.assertNotEqual(second['subject_ref'], first['subject_ref'])

    def test_part_expiry_cannot_be_extended_by_waiting_or_repeated_snapshot(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.now = first['parts']['work']['valid_until']
        # Other parts may retain a longer bundle horizon. Public reads retain
        # the work part's original exclusive expiry, never renew its authority.
        for _ in range(2):
            current = self.current_spatial()[0]
            self.assertEqual(current, first)
            self.assertFalse(current['parts']['work']['observed_at'] <= self.now < current['parts']['work']['valid_until'])
        self.now = first['valid_until']
        self.assertEqual(self.current_spatial(), [])
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'])

    def test_body_axis_is_ambiguous_and_never_copied_into_head_or_eye_gaze(self):
        self.prepare()
        self.joints[5], self.joints[6] = [230, 110], [410, 110]
        source = self.live_config['manifest']['sources'][capture.SOURCE]
        source['part_policies']['body_forward']['angular_error_rad'] = 1.2
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        result = self.current_spatial()[0]
        body_forward = result['parts']['body_forward']
        self.assertEqual(body_forward['status'], 'supplied', result)
        self.assertEqual(body_forward['shape']['front_back'], 'ambiguous')
        self.assertEqual(result['parts']['head_forward']['status'], 'unobservable')
        self.assertNotIn('shape', result['parts']['head_forward'])
        self.assertNotIn('gaze', result)

    def test_undercovering_torso_bound_is_unavailable_instead_of_an_opaque_sliver(self):
        self.prepare()
        self.spatial_policy['anatomy']['torso_radius_m'] = .001
        self.repin_spatial_policy()
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        result = self.current_spatial()[0]
        self.assertEqual(result['parts']['body']['status'], 'unobservable')
        self.assertNotIn('shape', result['parts']['body'])
        self.assertEqual(result['parts']['head']['status'], 'supplied')
        self.assertEqual(result['parts']['protection']['status'], 'supplied')

    def test_unobserved_ankle_cannot_publish_a_partial_body_as_complete_protection(self):
        self.prepare()
        self.joint_scores[15] = 0
        cycle = self.construct().step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        result = self.current_spatial()[0]
        self.assertEqual(result['parts']['body']['status'], 'unobservable')
        self.assertNotIn('shape', result['parts']['body'])
        self.assertEqual(result['parts']['work']['status'], 'supplied')
        self.assertEqual(result['parts']['protection']['status'], 'supplied')

    def test_changed_calibration_retires_geometry_before_new_models_and_identity(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        first = self.current_spatial()[0]
        self.calibration_change = True
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(cycle['detector'], 'not_run')
        self.assertEqual(self.current_spatial(), [])
        self.calibration_change = False
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        self.assertNotEqual(self.current_spatial()[0]['person_ref']['track_epoch'], first['person_ref']['track_epoch'])

    def test_changed_pixels_under_same_original_identity_withdraw_without_model_rerun(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['spatial'], 'completed')
        self.changed_pixels = True
        self.now += .1
        self.mono += .6
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(self.current_spatial(), [])
        self.assertEqual(self.trace, ['capture', 'detector', 'pose', 'capture'])

    def test_model_availability_cannot_refresh_expired_part_observation(self):
        self.prepare()
        pose = self.pose_worker.model
        def delayed_pose(image, bboxes):
            result = pose(image, bboxes)
            self.now += 1.1
            return result
        self.pose_worker.model = delayed_pose
        cycle = self.construct().step()
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'], cycle)
        for result in self.current_spatial():
            self.assertEqual(result['observed_at'], capture.NOW)
            self.assertGreaterEqual(result['available_at'], capture.NOW + 1.3)
            self.assertTrue(all(part['status'] == 'unobservable' for part in result['parts'].values()), result)
        self.assertFalse(cycle['focused_output'])

    def test_missing_actual_depth_cannot_be_filled_from_valid_pose_pixels(self):
        self.prepare()
        self.missing_wrist_depth = True
        cycle = self.construct().step()
        self.assertEqual(cycle['capture'], 'qualified', cycle)
        self.assertEqual(cycle['pose'], 'completed', cycle)
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        result = self.current_spatial()[0]
        for name in ('work', 'body', 'hand_left', 'hand_right'):
            self.assertEqual(result['parts'][name]['status'], 'unobservable', (name, result))
            self.assertNotIn('shape', result['parts'][name])
        self.assertEqual(result['parts']['head']['status'], 'supplied')
        self.assertEqual(result['parts']['protection']['status'], 'supplied')
        ref = next(ref for ref in result['original_receipt_refs'] if ref['component_id'] == 'spatial')
        original = self.graph.spatial.original_receipt(ref['store_id'], ref['receipt_id'])
        for index in (9, 10):
            self.assertEqual(original['joints'][index]['status'], 'unobservable')
            self.assertEqual(original['joints'][index]['support'], 0)

    def test_actual_v2_graph_runs_in_development_launcher_with_real_commissioning_tool(self):
        self.prepare()
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is required for the real commissioning kit')
        record = Path(self.work.name) / 'synthetic-owner-record.json'
        kit = Path(__file__).resolve().parents[3] / 'tools' / 'den-commissioning' / 'commission.mjs'
        subprocess.run([node, str(kit), 'template', '--output', str(record)],
                       check=True, capture_output=True, timeout=10)
        graph = self.construct()
        run_config = {'schema': 'home-producer-development-1', 'version': 1,
            'session_ref': deepcopy(self.config['session_ref']), 'source_id': self.config['source_id'],
            'configuration_digest': digest(self.config), 'max_runtime_s': 5, 'max_cycles': 1, 'interval_s': .5,
            'commissioning_record': str(record), 'commissioning_freeze': None, 'measurement_summary': None}
        report = run_development_session(graph, run_config, clock=lambda: self.mono,
            sleep=lambda _: None, stop_requested=lambda: False, node_runtime=node)
        self.assertEqual(report['status'], 'development_complete', report)
        self.assertEqual(report['commissioning']['status'], 'incomplete')
        self.assertEqual(report['missing_stages'], ['interaction'])
        self.assertEqual(report['counts']['spatial_completed_results'], 1)
        self.assertEqual(report['graph_counters']['captures'], 1)
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'])
        self.assertEqual(report['cleanup']['status'], 'stopped')
        self.assertEqual(report['cleanup']['withdrawal'], 'recorded')
        self.assertTrue(report['cleanup']['buffers_cleared'])
        self.assertTrue(graph.status()['closed'])
        self.assertFalse(graph.status()['inflight'])
        self.assertEqual(self.retained_snapshot()['items'], [])
        self.assertFalse(report['operational_geometry'])
        self.assertFalse(report['focused_output'])
        self.assertFalse(report['physical_authority'])


if __name__ == '__main__': unittest.main()

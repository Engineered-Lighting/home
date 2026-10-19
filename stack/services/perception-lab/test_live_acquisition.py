"""Actual non-recording factory with fake external camera/model/HA I/O."""
from copy import deepcopy
import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import live_capture_fixtures as capture_fixtures
from frame_ring import CaptureTurns
from journal import PREFIX, digest as wire_digest
from occupancy_reader import OccupancyReader
from reasoning import digest as model_digest
from test_live_acquisition_models import model_fixtures
from test_live_capture_policy2 import policy2_fixture, rotating_inputs


class ProductionAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='synthetic-production-acquisition-')
        self.addCleanup(self.work.cleanup)
        self.now, self.mono = capture_fixtures.NOW + .2, 100.0
        self.permitted, self.enabled = True, True
        self.sequence = 7
        self.trace = []
        self.live_config, self.policy, self.method, _, self.journal_grant = policy2_fixture()
        self.detector, self.pose_worker, self.sdk_calls, self.boxes = model_fixtures()
        predict, pose_sdk = self.detector.model.predict, self.pose_worker.model
        def detector_sdk(**kwargs):
            self.trace.append('detector')
            return predict(**kwargs)
        def pose_sdk_port(image, bboxes):
            self.trace.append('pose')
            return pose_sdk(image, bboxes)
        self.detector.model.predict, self.pose_worker.model = detector_sdk, pose_sdk_port
        self.refs = {'detector': {'id': self.detector.manifest['profile_id'], 'digest': model_digest(self.detector.manifest)},
                     'pose': {'id': self.pose_worker.manifest['profile_id'], 'digest': model_digest(self.pose_worker.manifest)}}
        self.config = {'schema': 'home-production-acquisition-1', 'version': 1,
            'session_ref': deepcopy(capture_fixtures.SCOPE), 'source_id': capture_fixtures.SOURCE,
            'revision': capture_fixtures.REVISION, 'local_grant_id': 'synthetic-local-acquisition-grant',
            'model_refs': deepcopy(self.refs), 'limits': {'interval_s': .5, 'max_cycles': 4,
                'max_runtime_s': 30, 'stage_timeout_s': 2, 'memory_limit_mb': 512},
            'person_threshold': .5, 'max_people': 2, 'occupancy_entity': 'binary_sensor.synthetic_person_occupancy',
            'camera_derived_entities': ['binary_sensor.synthetic_person_occupancy']}
        self.local_grant = {'schema': 'home-local-acquisition-grant-1', 'version': 1,
            'id': self.config['local_grant_id'], 'session_ref': deepcopy(self.config['session_ref']),
            'source_id': capture_fixtures.SOURCE, 'camera_id': capture_fixtures.CAMERA,
            'configuration_digest': wire_digest(self.config), 'model_refs': deepcopy(self.refs),
            'operations': ['capture', 'detector', 'pose'], 'not_before': capture_fixtures.NOW,
            'valid_until': capture_fixtures.NOW + 60, 'generation': 1}
        def ha_client():
            return {'observed_at': self.now, 'states': {self.config['occupancy_entity']: 'on'}}, None
        self.occupancy = OccupancyReader(ha_client, self.config['occupancy_entity'],
            clock=lambda: self.now, monotonic=time.monotonic)
        self.occupancy.start()
        self.addCleanup(lambda: self.occupancy.stop(timeout=1))
        until = time.monotonic() + 1
        while self.occupancy.reading()['state'] != 'occupied' and time.monotonic() < until:
            time.sleep(.01)
        self.assertEqual(self.occupancy.reading()['state'], 'occupied')
        self.turns = CaptureTurns(threading.Lock(), monotonic=lambda: self.mono)
        ntp = SimpleNamespace(server_ip='192.0.2.1', local_clock_quantization_ns=1000,
            drift_slew_ppm=1000, timeout_seconds=.5, valid_for_seconds=1, max_uncertainty_seconds=1)
        pilot_config = SimpleNamespace(ntp=ntp)
        def accept_once(config, token, *, transport, publisher_factory):
            transport('/frame.jpg', token)
            return {'status': 'accepted'}
        self.pilot = SimpleNamespace(CONFIG='synthetic-fixed-config', load_config=lambda _: pilot_config,
            fetch=lambda *_: ({'content-type': 'application/json'}, b'{"healthy":true}'),
            accept_once=accept_once, read_token=lambda: 'synthetic-camera-token')
        self.camera = {'turns': self.turns,
            'runtime_factory': lambda: (self.pilot, lambda *_: None, lambda *_: None),
            'pair_transport': self.camera_pair}

    def camera_pair(self, token, *, timeout):
        self.trace.append('capture')
        jpeg, header, bundle, calibration = rotating_inputs(self.sequence, nonce=format(self.sequence, 'x') * 32)
        return json.dumps({'schema': 'home-camera-pair-1', 'version': 1,
            'jpeg_b64': base64.b64encode(jpeg).decode(), 'header': header,
            'bundle_b64': base64.b64encode(bundle).decode(), 'calibration': calibration}).encode()

    def control_clock(self):
        policy = self.live_config['manifest']['sources'][capture_fixtures.SOURCE]['control_clock']
        return {'observed_at': self.now, 'clock': {'status': 'commissioned', 'mapping_id': policy['mapping_id'],
            'mapping_digest': policy['mapping_digest'], 'source_epoch': policy['source_epoch'],
            'original_unit': 'unix_ns', 'original_value': str(round(self.now * 1e9)), 'uncertainty_s': .001}}

    def construct(self, **options):
        from live_acquisition import create_production_acquisition
        self.graph = create_production_acquisition(self.config, live_config=self.live_config,
            capture_policy=self.policy, capture_method=self.method,
            journal_path=Path(self.work.name) / 'synthetic.sqlite', journal_grants=[self.journal_grant],
            publication_token=capture_fixtures.TOKEN, detector=self.detector, pose=self.pose_worker,
            local_authority=lambda: deepcopy(self.local_grant) if self.permitted else None,
            household_enabled=lambda: self.enabled, occupancy=self.occupancy,
            clock=lambda: self.now, monotonic=lambda: self.mono, camera_dependencies=self.camera,
            control_clock=self.control_clock, rss=lambda: 64 * 1024**2, **options)
        self.addCleanup(self.graph.close)
        return self.graph

    def snapshot(self):
        connection = http.client.HTTPConnection(*self.graph.address, timeout=2)
        try:
            connection.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room',
                               headers={'Authorization': 'Bearer ' + capture_fixtures.TOKEN})
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 200, body)
            return body
        finally:
            connection.close()

    def test_factory_is_idle_then_runs_capture_detector_pose_and_public_journal_read(self):
        graph = self.construct()
        self.assertEqual(self.trace, [])
        cycle = graph.step()
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'], cycle)
        items = self.snapshot()['items']
        self.assertEqual(len(items), 1, cycle)
        payload = items[0]['payload']
        self.assertEqual(payload['status'], 'unobservable')
        self.assertIsNone(payload['person_ref'])
        self.assertTrue(all(part['status'] == 'unobservable' for part in payload['parts'].values()))
        self.assertEqual(payload['observed_at'], capture_fixtures.NOW)
        self.assertEqual(payload['clock']['original_value'], '1800000000000000000')
        self.assertEqual(payload['sensor_roots'], ['camera:synthetic-oak'])
        self.assertEqual(payload['head']['position']['capture_seq'], 7)
        self.assertEqual(self.sdk_calls[1][2], [[10, 20, 50, 90]])
        self.assertEqual((cycle['spatial'], cycle['interaction']), ('missing', 'missing'))
        self.assertFalse(cycle['operational_geometry'])
        self.assertFalse(cycle['focused_output'])

    def test_empty_success_is_distinct_from_detector_failure_and_never_poses_full_frame(self):
        self.boxes.clear()
        cycle = self.construct().step()
        self.assertEqual(cycle['status'], 'observed', cycle)
        self.assertEqual((cycle['detector'], cycle['pose']), ('completed', 'completed'))
        self.assertEqual(self.trace, ['capture', 'detector'])
        self.assertEqual(len(self.snapshot()['items']), 1)

    def test_detector_failure_withdraws_capture_without_empty_or_pose_fallback(self):
        def broken(**_):
            self.trace.append('detector_failed')
            raise RuntimeError('synthetic detector SDK failure')
        self.detector.model.predict = broken
        cycle = self.construct().step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(cycle['pose'], 'not_run')
        self.assertEqual(self.trace, ['capture', 'detector_failed'])
        self.assertEqual(self.snapshot()['items'], [])

    def test_local_revocation_before_capture_is_independent_of_journal_permission(self):
        graph = self.construct()
        self.permitted = False
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'blocked', cycle)
        self.assertEqual(cycle['reason'], 'local_acquisition_revoked')
        self.assertEqual(self.trace, [])
        self.assertTrue(graph.status()['closed'])

    def test_local_revocation_during_detector_prevents_pose_and_later_delivery(self):
        predict = self.detector.model.predict
        def revoke(**kwargs):
            result = predict(**kwargs)
            self.permitted = False
            return result
        self.detector.model.predict = revoke
        cycle = self.construct().step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(cycle['pose'], 'not_run')
        self.assertEqual(self.trace, ['capture', 'detector'])
        self.assertTrue(self.graph.status()['closed'])
        self.assertFalse(self.graph.status()['inflight'])

    def test_household_switch_is_required_even_when_local_source_grant_is_valid(self):
        graph = self.construct()
        self.enabled = False
        result = graph.step()
        self.assertEqual(result['status'], 'blocked', result)
        self.assertEqual(self.trace, [])
        self.assertTrue(graph.status()['closed'])

    def test_existing_camera_chat_turn_blocks_without_stealing_or_releasing_it(self):
        graph = self.construct()
        self.turns.acquire()
        try:
            result = graph.step()
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(result['reason'], 'camera_capture_busy')
            self.assertEqual(self.trace, [])
            self.assertTrue(self.turns.locked())
        finally:
            self.turns.release()
        self.assertEqual(self.snapshot()['items'], [])

    def test_later_adapter_receives_exact_capture_lineage_and_model_availability(self):
        seen = []
        def spatial(context, *, authorized, deadline, cancel):
            self.assertTrue(authorized())
            self.assertFalse(cancel.is_set())
            seen.append(deepcopy(context))
            return {'status': 'unobservable', 'reason': 'not_commissioned'}
        self.local_grant['operations'].append('spatial')
        cycle = self.construct(stages={'spatial': spatial}).step()
        self.assertEqual(cycle['status'], 'observed', cycle)
        self.assertEqual(len(seen), 1)
        context = seen[0]
        capture = self.snapshot()['items'][0]
        self.assertEqual(context['capture_payload'], capture['payload'])
        self.assertEqual(context['publication_receipt']['cursor'], capture['cursor'])
        self.assertEqual(context['person_association'], 'unresolved')
        self.assertEqual(context['detector']['model_ref'], self.refs['detector'])
        self.assertEqual(context['pose']['model_ref'], self.refs['pose'])
        self.assertEqual(context['detector']['frame_id'], context['capture']['id'])
        self.assertEqual(context['pose']['frame_id'], context['capture']['id'])
        self.assertEqual(context['detector_available_at'], self.now)
        self.assertEqual(context['pose_available_at'], self.now)
        self.assertEqual(context['availability_clock'], 'utc_epoch_seconds')
        rgb = next(value for value in context['original_receipts'] if value['component_id'] == 'rgb')
        self.assertEqual(rgb['rgb_receipt']['validation_id'], 'causal:' + '7' * 32)
        self.assertEqual(rgb['rgb_receipt']['host_clock_validation_id'], 'synthetic-host-clock-7')
        self.assertFalse(cycle['operational_geometry'])
        self.assertFalse(cycle['focused_output'])

    def test_fresh_capture_rate_and_duplicate_input_cannot_redispatch_models(self):
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'observed')
        first = self.snapshot()['items'][0]
        self.assertEqual(graph.step()['reason'], 'rate_limited')
        self.mono += 1
        self.now += 1
        again = graph.step()
        self.assertEqual(again['reason'], 'duplicate_capture', again)
        self.assertEqual(self.trace, ['capture', 'detector', 'pose', 'capture'])
        current = self.snapshot()['items'][0]
        self.assertEqual(current, first)

    def test_unloaded_resident_model_refuses_construction_before_any_camera_use(self):
        self.detector.model = None
        with self.assertRaises(ValueError):
            self.construct()
        self.assertEqual(self.trace, [])

    def test_journal_publication_denial_cannot_become_model_execution_authority(self):
        self.journal_grant['operations'] = ['read']
        self.journal_grant['live_bindings'][0]['capabilities'] = ['read']
        cycle = self.construct().step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(self.trace, ['capture'])
        self.assertEqual((cycle['detector'], cycle['pose']), ('not_run', 'not_run'))
        self.assertEqual(self.snapshot()['items'], [])

    def test_local_acquisition_continues_during_real_la_pump_failures_and_fact_log_pause(self):
        from frame_ring import FrameRing
        from la_live_perceiver import PerceiverPump
        from qwen_transport import QwenNotExecuted
        from test_frame_ring import Frames
        from test_qwen_live import PROFILE, TRANSPORT_CONFIG
        class FactLog:
            writable_now = True
            def writable(self): return self.writable_now
            def maintain(self): pass
            def snapshot(self): return {'last_failure': 'synthetic_fact_log_unwritable'}
        class Transport:
            config = deepcopy(TRANSPORT_CONFIG)
            connected = False
            mode = 'loss'
            def lookup(self, reference):
                if not self.connected: raise OSError('synthetic LA unavailable')
                return {'request_id': reference['request_id'], 'state': 'not_found',
                        'engine_epoch': 'synthetic-la-engine', 'artifact_sha256': 'a' * 64}
            def __call__(self, envelope):
                raise QwenNotExecuted('transport_not_connected' if self.mode == 'loss' else 'admission_closed')
            def close(self): pass
        transport, log = Transport(), FactLog()
        ring = FrameRing(clock=lambda: self.now)
        frames = Frames()
        pump = PerceiverPump(site_id=capture_fixtures.SCOPE['site_id'], camera_id=capture_fixtures.CAMERA,
            fact_log=log, transport=transport, profile=deepcopy(PROFILE), frames=ring,
            clock=lambda: self.now, monotonic=lambda: self.mono, local_time=lambda _: (19, 40),
            enabled=lambda: True, active=lambda: True, occupied=self.occupancy)
        self.addCleanup(pump.stop)
        def fill_la_ring():
            current = self.now
            for offset in (2.5, 1.3, .1):
                ring.offer(*frames(current - offset))
        graph = self.construct()
        first = pump.tick()
        self.assertEqual(first['reason'], 'engine_epoch_unavailable')
        self.assertEqual(graph.step()['status'], 'observed')
        self.assertEqual(self.snapshot()['items'][0]['payload']['head']['position']['capture_seq'], 7)
        transport.connected = True
        for sequence, expected in ((8, 'transport_not_connected'), (9, 'admission_closed'), (10, 'fact_log_unwritable')):
            self.sequence = sequence
            self.now = capture_fixtures.NOW + sequence - 7 + .2
            self.mono = 100.0 + sequence - 7
            fill_la_ring()
            if sequence == 9: transport.mode = 'backoff'
            if sequence == 10: log.writable_now = False
            la_result = pump.tick()
            self.assertEqual(la_result['reason'], expected, la_result)
            if sequence == 9:
                self.assertEqual(pump.tick()['reason'], 'admission_closed_backoff')
            local = graph.step()
            self.assertEqual(local['status'], 'observed', local)
            items = self.snapshot()['items']
            self.assertEqual(len(items), 1)
            payload = items[0]['payload']
            self.assertEqual(payload['head']['position']['capture_seq'], sequence)
            self.assertEqual(payload['observed_at'], capture_fixtures.NOW + sequence - 7)
            self.assertEqual(payload['sensor_roots'], ['camera:synthetic-oak'])
            self.assertEqual(payload['parent_refs'], [])
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'] * 4)

    def test_changed_local_grant_generation_stops_instead_of_inheriting_old_session(self):
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'observed')
        self.local_grant['generation'] = 2
        self.mono += 1
        self.now += 1
        self.sequence = 8
        result = graph.step()
        self.assertEqual(result['status'], 'blocked', result)
        self.assertEqual(result['reason'], 'local_acquisition_revoked')
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'])
        self.assertTrue(graph.status()['closed'])

    def test_capture_expiry_during_actual_detector_prevents_pose_and_later_delivery(self):
        seen = []
        def spatial(context, **_):
            seen.append(context)
            return {'status': 'unobservable'}
        self.local_grant['operations'].append('spatial')
        predict = self.detector.model.predict
        def late(**kwargs):
            result = predict(**kwargs)
            self.now = capture_fixtures.NOW + 3
            return result
        self.detector.model.predict = late
        graph = self.construct(stages={'spatial': spatial})
        result = graph.step()
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['reason'], 'capture_expired')
        self.assertEqual(result['pose'], 'not_run')
        self.assertEqual(seen, [])
        self.assertEqual(self.trace, ['capture', 'detector'])
        self.assertEqual(self.snapshot()['items'], [])

    def test_mutating_callers_configuration_cannot_change_frozen_session_authority(self):
        graph = self.construct()
        original = wire_digest(self.config)
        self.config['model_refs']['detector']['digest'] = 'f' * 64
        self.config['limits']['max_cycles'] = 0
        self.config['person_threshold'] = 0
        result = graph.step()
        self.assertEqual(result['status'], 'observed', result)
        self.assertEqual(graph.status()['configuration_digest'], original)
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'])


if __name__ == '__main__': unittest.main()

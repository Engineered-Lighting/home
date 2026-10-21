"""Actual RAM source/Store/PairedRunner/ASGI; external inputs are authored.

No household originals, camera, model, host, native fixture or physical I/O.
"""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import hashlib
import threading
import time
import unittest
from unittest.mock import patch

import test_apartment_viewing_metric_source as source_fixture
import test_apartment_desk_preview as preview_fixture
import apartment_desk_preview as preview


class DeskPreviewSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_fixture.MetricSourceTests.setUpClass()
        cls.addClassCleanup(source_fixture.MetricSourceTests.doClassCleanups)

    def setUp(self):
        self.f = source_fixture.MetricSourceTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def build(self, *, optin='valid', two_people=False, zero_people=False, finalize=True):
        if getattr(self, '_built', False):
            # Each private-policy mode is a distinct actual Store/session.
            # Never mutate the incumbent idempotency key to rerun one request.
            self.f = source_fixture.MetricSourceTests('runTest')
            self.f.setUp()
            self.addCleanup(self.f.doCleanups)
        self._built = True
        f = self.f
        alignment, bootstrap, originals, config = f.approximate_configuration()
        base = f.factory()
        self.candidate = preview_fixture.configured_candidate(alignment.pack)
        self.candidate_bytes = preview_fixture.encoded(self.candidate)
        candidate_path = bootstrap.PRIVATE_PATH + '/authored-desk-preview/candidate.json'
        policy = dict(schema='home-apartment-desk-preview-source-1', version=1, enabled=True,
                      candidate=dict(path=candidate_path, sha256=hashlib.sha256(self.candidate_bytes).hexdigest()))
        if optin == 'bad_pin':
            policy['candidate']['sha256'] = '0' * 64
        by_path = {config['files'][role]['path']: raw for role, raw in originals.items()}
        by_path.update({bootstrap.DESK_PREVIEW_POLICY_PATH: preview_fixture.encoded(policy),
                        candidate_path: self.candidate_bytes})
        def lstat(path):
            self.assertEqual(path, bootstrap.DESK_PREVIEW_POLICY_PATH)
            if optin == 'absent':
                raise FileNotFoundError
            return object()
        # Only the existing private-file port is authored. The actual loader,
        # byte pin, binder and RAM owner remain in the exercised path.
        with patch.object(bootstrap.os, 'lstat', side_effect=lstat), \
                patch.object(bootstrap, '_read_private', side_effect=lambda path: by_path[path]):
            configured = bootstrap.load_factory(config, client=base.client)
        configured.clock = base.clock
        token = 'authored-desk-preview-' + 'd' * 40
        f.store.settings = replace(f.store.settings, token_grants=({
            'subject': 'viewer', 'site_id': 'victoria',
            'token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'capabilities': ['read']},))
        runtime = f.build(metric_factory=configured)
        source = runtime.apartment_viewing_sessions[f.session['id']]
        event = preview_fixture.add_shoulders(f.event)
        if two_people:
            event['payload']['result']['objects'].append({'label': 'person', 'instance_id': 'second-person'})
        if zero_people:
            event['payload']['result'].update(objects=[], pose_instances=[])
        original = runtime.executor
        def execute(request):
            value = original(request)
            value['result'] = deepcopy(event['payload']['result'])
            value['result']['frame_id'] = request['frame']['frame_id']
            return value
        runtime.executor = runtime.runners[f.session['id']].executor = execute
        f.anchors(runtime)
        self.wait_outputs(2)
        before = list(f.reads)
        if finalize:
            source.metric.finalize()
        self.assertEqual(f.reads, before, 'projection must not acquire another NPZ')
        self.assertEqual(f.reads.count('/frame.npz'), 2)
        self.assertLessEqual(source.status()['resident_bytes'], 8388608)
        return runtime, source, configured, token

    def wait_outputs(self, count):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            rows = self.f.store.list_events(self.f.session['id'], 0, 100)
            outputs = [row for row in rows if row['kind'] == 'perception.output']
            if len(outputs) >= count:
                return outputs
            time.sleep(.005)
        self.fail('actual paired output did not settle')

    def test_private_optin_actual_ram_source_and_authenticated_live_publish_same_frame_preview(self):
        runtime, source, configured, token = self.build()
        self.assertTrue(configured.capability['virtual_target_preview']['provisioned'])
        self.assertIs(configured.desk_preview.retained_byte_allocations[0], self.candidate_bytes)
        from app import create_app, PREFIX
        from fastapi.testclient import TestClient
        headers = {'Authorization': 'Bearer ' + token, 'X-Home-Lab-Site': 'victoria'}
        with TestClient(create_app(self.f.store.settings, runtime, self.f.store)) as client:
            path = PREFIX + '/sessions/' + self.f.session['id']
            response = client.get(path + '/live', headers=headers)
            self.assertEqual(response.status_code, 200)
            arm = response.json()['arms']['B']
            packet = arm['virtual_target_preview']
            self.assertTrue(packet['available'], packet)
            self.assertEqual(packet['observation'], arm['observation'])
            self.assertEqual(packet['frame'], arm['approximate_room_pose']['frame'])
            self.assertEqual(packet['target']['point_m'], self.candidate['target']['centre_m'])
            self.assertEqual(packet['person_point']['joint_ids'], [5, 6])
            self.assertEqual(packet['provenance']['configured_candidate_sha256'],
                             hashlib.sha256(self.candidate_bytes).hexdigest())
            self.assertTrue(all(packet[key] is False for key in preview.NO_AUTHORITY))
            mid = arm['frame']['media_id']
            descriptor = client.get(path + '/media/' + mid + '/descriptor',
                params={'generation': self.f.session['generation']}, headers=headers)
            self.assertEqual(descriptor.status_code, 200)
            self.assertEqual(descriptor.json()['sha256'], packet['frame']['media_sha256'])
            self.assertLessEqual(packet['valid_until'], packet['frame']['original_times']['valid_until'])
        self.assertEqual(self.f.store.list_media(self.f.session['id'])['items'], [])
        runtime.stop_session(self.f.session)
        self.assertEqual(source.status()['resident_bytes'], 0)

    def test_new_observation_original_and_receipt_expiry_and_stop_withdraw_without_renewal(self):
        runtime, source, configured, _ = self.build()
        arm = runtime.live(self.f.session)['arms']['B']
        packet, observation = arm['virtual_target_preview'], arm['observation']
        self.assertTrue(packet['available'], packet)
        changed = {**observation, 'frame_id': 'newer-original-frame'}
        self.assertFalse(source.metric.preview_snapshot(changed, session_state='recording')['available'])
        self.assertFalse(source.metric.preview_snapshot(observation, session_state='stopped')['available'])
        tracker = source.metric.projection
        received = tracker.received
        tracker.monotonic = lambda: received + .5
        first = source.metric.preview_snapshot(observation, session_state='recording')
        tracker.monotonic = lambda: received + 1.5
        later = source.metric.preview_snapshot(observation, session_state='recording')
        self.assertTrue(first['available']); self.assertTrue(later['available'])
        self.assertEqual(first['valid_until'], later['valid_until'])
        self.assertLess(later['timing']['remaining_receipt_hold_s'], first['timing']['remaining_receipt_hold_s'])
        tracker.monotonic = lambda: received + 2
        self.assertFalse(source.metric.preview_snapshot(observation, session_state='recording')['available'])
        # Isolate the original RGB/model deadline from the receipt-hold gate.
        tracker.monotonic = lambda: received + .5
        self.f.now = packet['valid_until']
        expired = source.metric.preview_snapshot(observation, session_state='recording')
        self.assertFalse(expired['available'])
        self.assertEqual(expired['reason'], 'desk_preview_original_expired')
        runtime.stop_session(self.f.session)
        self.assertFalse(source.metric.preview_snapshot(observation, session_state='recording')['available'])
        self.assertEqual(source.status()['resident_bytes'], 0)

    def test_stop_during_actual_projection_cannot_publish_late_preview_or_keep_buffers(self):
        runtime, source, _, _ = self.build(finalize=False)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = source.metric.projection.ingest
        def delayed(*args, **kwargs):
            entered.set(); release.wait(2)
            return original(*args, **kwargs)
        source.metric.projection.ingest = delayed
        worker = threading.Thread(target=source.metric.finalize)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            runtime.stop_session(self.f.session)
        finally:
            release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(source.status()['resident_bytes'], 0)
        self.assertIsNone(source.metric.preview_packet)
        self.assertFalse(source.metric.preview_snapshot({}, session_state='recording')['available'])

    def test_ambiguity_and_actual_new_jpeg_fallback_never_reuse_old_target(self):
        runtime, source, _, _ = self.build(two_people=True)
        arm = runtime.live(self.f.session)['arms']['B']
        self.assertTrue(arm['approximate_room_pose']['renderable'])
        self.assertFalse(arm['virtual_target_preview']['available'])
        self.assertEqual(arm['virtual_target_preview']['reason'], 'desk_preview_single_person_required')
        runtime.stop_session(self.f.session)
        self.assertEqual(source.status()['resident_bytes'], 0)
        runtime.close()
        runtime, source, _, _ = self.build()
        arm = runtime.live(self.f.session)['arms']['B']
        self.assertTrue(arm['virtual_target_preview']['available'])
        # The incumbent fallback may publish a current JPEG, but cannot borrow
        # the prior NPZ/shoulder target for its new original observation.
        previous = arm['observation']
        source.metric.factory.clock.begin = lambda _: (_ for _ in ()).throw(ValueError('receiver_clock_anchor_changed'))
        self.f.now += 1
        capture = source_fixture.viewing.capture
        header = capture.header(self.f.jpeg, sequence=17, observed_ns=int(self.f.now * 1e9))
        runtime.preview_pumps[self.f.session['id']].consume(self.f.jpeg, header)
        self.wait_outputs(4)
        latest = runtime.live(self.f.session)['arms']['B']
        self.assertNotEqual(latest['observation']['frame_id'], previous['frame_id'])
        self.assertFalse(latest['virtual_target_preview']['available'])
        self.assertEqual(self.f.reads.count('/frame.npz'), 2)
        runtime.stop_session(self.f.session)
        self.assertEqual(source.status()['resident_bytes'], 0)

    def test_parent_refusal_reason_reaches_the_preview_instead_of_awaiting(self):
        import spatial_validation
        import test_apartment_viewing_metric_successor as successor
        changed = {'status': 'changed', 'compared_cells': 64, 'changed_cells': 40}
        cases = (('camera_epoch', dict(), 'approximate_camera_epoch_changed'),
                 ('mount', dict(), 'approximate_mount_signature_changed'),
                 ('zero_people', dict(zero_people=True), 'approximate_observed_instance_unavailable'))
        for name, options, reason in cases:
            with self.subTest(name=name):
                retired = patch.object(source_fixture.MetricSourceTests, 'approximate_configuration',
                    lambda f: successor.configuration(f, successor=False)) if name == 'camera_epoch' else nullcontext()
                moved = patch.object(spatial_validation, 'check_mount', return_value=changed) if name == 'mount' else nullcontext()
                with retired, moved:
                    runtime, source, _, _ = self.build(**options)
                arm = runtime.live(self.f.session)['arms']['B']
                self.assertEqual(arm['approximate_room_pose']['reason'], reason)
                packet = arm['virtual_target_preview']
                self.assertFalse(packet['available']); self.assertEqual(packet['reason'], reason)
                self.assertEqual(source.metric.preview_snapshot(arm.get('observation'), session_state='recording')['reason'], reason)
                runtime.stop_session(self.f.session); self.assertEqual(source.status()['resident_bytes'], 0); runtime.close()
        runtime, source, _, _ = self.build()
        arm = runtime.live(self.f.session)['arms']['B']; self.assertTrue(arm['virtual_target_preview']['available'])
        tracker = source.metric.projection; received = tracker.received
        tracker.monotonic = lambda: received + 2
        packet = runtime.live(self.f.session)['arms']['B']['virtual_target_preview']
        self.assertFalse(packet['available']); self.assertEqual(packet['reason'], 'approximate_receipt_expired')
        runtime.stop_session(self.f.session)
        self.assertEqual(source.metric.preview_snapshot({}, session_state='recording')['reason'], 'desk_preview_source_stopped')
        self.assertEqual(source.status()['resident_bytes'], 0)

    def test_absent_and_invalid_private_optin_keep_ordinary_approximate_viewing(self):
        for mode in ('absent', 'bad_pin'):
            with self.subTest(mode=mode):
                runtime, source, configured, _ = self.build(optin=mode)
                arm = runtime.live(self.f.session)['arms']['B']
                self.assertTrue(arm['approximate_room_pose']['renderable'])
                self.assertNotIn('virtual_target_preview', arm)
                self.assertIsNone(configured.desk_preview)
                if mode == 'absent':
                    self.assertNotIn('virtual_target_preview', configured.capability)
                else:
                    status = configured.capability['virtual_target_preview']
                    self.assertFalse(status['provisioned'])
                    self.assertEqual(status['reason'], 'desk_preview_policy_invalid')
                runtime.stop_session(self.f.session)
                self.assertEqual(source.status()['resident_bytes'], 0)
                runtime.close()


if __name__ == '__main__':
    unittest.main()

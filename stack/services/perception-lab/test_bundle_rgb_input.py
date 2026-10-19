"""Actual factory/Store/RegisteredImage; fictional camera and receiver-clock I/O."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import test_existing_api_assessment_factory as old
import live_capture_fixtures as capture
from live_detector_app import validate_config, build_runtime
from preview_client import LocalBundleClient, PreviewPump
from registered_image import RegisteredImage

ROOT = Path(__file__).resolve().parents[3]
POLICY = dict(schema='home-lab-bundled-rgb-input-policy-1', version=1,
    max_captures=8, max_retained_bytes=41943040)


class BundledRgbFactoryTests(unittest.TestCase):
    setUp = old.ExistingApiAssessmentFactoryTests.setUp

    def configure(self):
        self.config.pop('existing_api_assessment')
        self.config.update(schema_version=4, bundled_rgb_input=deepcopy(POLICY))
        self.config['bundled_rgb_input']['max_captures'] = getattr(self, 'max_captures', 8)
        self.jpeg = capture.jpeg()
        self.anchor_a = capture.header(self.jpeg, sequence=10, host_clock_uncertainty_seconds=.39,
            uncertainty_seconds=.410001)
        self.anchor_b = capture.header(self.jpeg, sequence=16,
            observed_ns=1800000000500000000, host_clock_uncertainty_seconds=.39,
            uncertainty_seconds=.410001, frame_device_ns=50_700_000_000,
            host_send_monotonic_ns=10_500_000_000, host_receive_monotonic_ns=10_520_000_000,
            observed_monotonic_seconds=10.51)
        meta = capture.frame_metadata(sequence=13, rgb_device_s=50.45, depth_device_s=50.44)
        nv12 = np.full((540, 640), 128, np.uint8)
        nv12[:360] = 235  # Literal BT.601 limited-range white -> [254,254,254].
        output = io.BytesIO()
        np.savez_compressed(output, depth_mm=np.full((400, 640), 1234, np.uint16),
            left=np.zeros((400, 640), np.uint8), right=np.zeros((400, 640), np.uint8),
            rgb_nv12=nv12, metadata_json=np.frombuffer(json.dumps(meta).encode(), np.uint8))
        self.bundle = output.getvalue()
        self.raw_calibration = json.dumps(self.calibration).encode()

    def build_bundled(self):
        self.configure()
        self.assertEqual(validate_config(self.config), self.config)
        self.entered, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.seen = []
        self.tap_inputs = []
        def anchor_tap(jpeg, header):
            self.tap_inputs.append((jpeg, header))
        runtime = build_runtime(self.store, SimpleNamespace(camera_id='den'), self.config,
            transport_factory=lambda *_: lambda *_: None, lease=SimpleNamespace(ready=lambda: True),
            la_live=SimpleNamespace(running=lambda: True, offer_preview=anchor_tap))
        self.addCleanup(runtime.close)
        runtime.recorders['den'] = old._Recorder()
        def execute(request):
            image, metadata = RegisteredImage(self.store)(request['frame'])
            rows = self.store.list_media(self.session['id'])['items']
            support = next(r for r in rows if r['metadata'].get('stream') == 'bundled_rgb_input_original'
                and r['metadata'].get('status') == 'retained')
            original = json.loads(self.store.media_path(self.session['id'], support['id']).read_bytes())
            self.seen.append((image[0, 0].tolist(), request, original, metadata))
            self.entered.set(); self.release.wait(2)
            profile = self.profiles[request['arm']]
            return dict(request_id=request['request_id'], engine_epoch=profile['engine_epoch'],
                artifact_sha256=profile['artifact_sha256'], terminal=True, state='completed',
                result={**dict(schema_version=1, coordinate_space='source_pixels',
                    frame_id=request['frame']['frame_id'], objects=[], boxes=[]), **getattr(self, 'model_result', {})})
        runtime.executor = execute
        def dormant(pump):
            pump.thread = threading.Thread(target=lambda: None)
            pump.thread.start(); pump.thread.join()
        if getattr(self, 'start_via_controller', False):
            from controller import Controller
            self.store.transition(self.session['id'], 'stopped')
            self.controller = Controller(self.store, runtime)
            def starting_pump(pump):
                self.assertEqual(self.store.recording_sessions(), [])
                # Existing recorder start completes before Controller promotes
                # the recording. Input instance construction itself is inert.
                source = next(reversed(runtime.bundled_rgb_sessions.values()))
                self.assertEqual(source.capture_count, 0)
                with self.assertRaisesRegex(ValueError, 'bundled input is not current'):
                    pump.consume(self.jpeg, self.anchor_a)
                self.assertEqual(source.capture_count, 0)
                dormant(pump)
            with patch.object(PreviewPump, 'start', starting_pump):
                self.session = self.controller.start(dict(camera_id='den', experiment_id='placement-same',
                    name='Actual controller', request_id='bundled-controller-start'))
            self.assertEqual(self.session['state'], 'recording', self.session)
            self.assertIn(self.session['id'], runtime.runners)
        else:
            with patch.object(PreviewPump, 'start', dormant):
                outcome = runtime.start_session(self.session)
            self.assertEqual(outcome['inference'], 'ready', outcome)
        self.now = capture.NOW + .05
        return runtime

    def clock_context(self):
        # Execute the unchanged pinned public clock checks, doubling only local
        # protected-file/kernel clock reads. No actual clock/camera operation.
        name = '_bundled_authored_camera_clock'
        spec = importlib.util.spec_from_file_location(name,
            ROOT / 'artifacts/den-connected-prototype-20261016/runtime-evidence/installed-oak-capture-20261016.py')
        camera = importlib.util.module_from_spec(spec); sys.modules[name] = camera; spec.loader.exec_module(camera)
        evidence = dict(version=1, semantics='commissioned_camera_host_clock_v1',
            host_boot_id='00000000-0000-0000-0000-000000000001',
            validation_id='synthetic-host-clock-validation', checked_monotonic_ns=10_400_000_000,
            checked_unix_ns=1800000000390000000, valid_until_monotonic_ns=11_400_000_000,
            uncertainty_seconds=.39)
        shift = int(getattr(self, 'time_shift', 0)*10**9)
        for key in ('checked_monotonic_ns', 'checked_unix_ns', 'valid_until_monotonic_ns'):
            evidence[key] += shift
        points = iter([camera.HostPoint(10_580_000_000+shift, 10_580_000_000+shift,
            1800000000570000000+shift, 10_580_000_100+shift), camera.HostPoint(10_600_000_000+shift,
            10_600_000_000+shift, 1800000000590000000+shift, 10_600_000_100+shift)])
        camera.host_point = lambda: next(points)
        camera.read_protected_json = lambda *_: deepcopy(evidence)
        pilot = SimpleNamespace(CONFIG=Path('/authored/private/host-clock-config.json'),
            load_config=lambda _: SimpleNamespace(output_path='/authored/private/host-clock.json', uid=1000),
            _boot_id=lambda: evidence['host_boot_id'])
        return camera, pilot

    def consume(self, runtime, header):
        return runtime.preview_pumps[self.session['id']].consume(self.jpeg, header)

    def test_actual_factory_retains_exact_bundle_rgb_before_delayed_model_offer(self):
        runtime = self.build_bundled()
        def read(_client, path):
            self.reads.append(path)
            if path == '/frame.npz': self.now = capture.NOW + .59; return self.bundle, ''
            if path == '/calibration': return self.raw_calibration, ''
            raise AssertionError('unexpected external route')
        with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
             patch('bundle_rgb_input.sync_directory'), \
             patch.object(LocalBundleClient, '_read', autospec=True, side_effect=read):
            self.consume(runtime, self.anchor_a)
            self.assertEqual(self.reads, [])
            self.assertFalse(self.seen)
            self.now = capture.NOW + .55
            self.consume(runtime, self.anchor_b)
            self.assertTrue(self.entered.wait(2), runtime.bundled_rgb_sessions[self.session['id']].status())
            self.assertEqual(self.reads, ['/frame.npz', '/calibration'])
            self.assertEqual(self.tap_inputs, [(self.jpeg, self.anchor_a), (self.jpeg, self.anchor_b)])
            self.assertTrue(all(v[0] == [254, 254, 254] for v in self.seen))
            for _, request, support, metadata in self.seen:
                self.assertEqual(request['frame']['media_ids'], [support['rgb_media_ref']['id']])
                self.assertEqual(support['binding']['original_family'], 'existing-api-bundled-rgb-1')
                # Half the 500ms anchor gap plus the complete .410001s header bound.
                self.assertAlmostEqual(support['timing']['ordinal_uncertainty_seconds'], .660001)
                self.assertLessEqual(metadata['uncertainty_seconds'], 1)
                self.assertNotIn('source_timing_receipt', metadata)
                self.assertIsNone(support['operational_geometry'])
            self.release.set()

    def test_actual_controller_start_constructs_inert_input_then_recording_offers(self):
        self.start_via_controller = True
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.build_bundled(); self.release.set()
            source = runtime.bundled_rgb_sessions[self.session['id']]
            self.assertIsNone(source.binding)
            self.assertFalse(self.reads); self.assertFalse(self.tap_inputs)
            self.consume(runtime, self.anchor_a); self.now = capture.NOW + .55
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                result = self.consume(runtime, self.anchor_b)
            self.assertTrue(self.entered.wait(2), source.status())
            self.assertTrue(result['media_ids'])
            self.assertEqual(self.reads, ['/frame.npz', '/calibration'])
            self.assertEqual(self.controller.stop(self.session['id'])['state'], 'stopped')

    def camera_read(self, _client, path):
        self.reads.append(path)
        if path == '/frame.npz': self.now = capture.NOW + .59 + getattr(self, 'time_shift', 0); return self.bundle, ''
        if path == '/calibration': return self.raw_calibration, ''
        raise AssertionError('unexpected external route')

    def seeded(self):
        runtime = self.build_bundled()
        self.consume(runtime, self.anchor_a)
        self.now = capture.NOW + .55
        return runtime

    def test_unknown_terminal_ack_keeps_incomplete_and_never_offers_or_retries_reads(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            original = self.store.append_event
            def lost(sid, kind, payload, **kwargs):
                value = original(sid, kind, payload, **kwargs)
                if kind == 'assessment.bundle_rgb.terminal' and payload['status'] == 'retained':
                    raise ValueError('authored_unknown_terminal_ACK')
                return value
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read), \
                 patch.object(self.store, 'append_event', side_effect=lost):
                with self.assertRaisesRegex(ValueError, 'unknown_terminal_ACK'):
                    self.consume(runtime, self.anchor_b)
            self.assertFalse(self.seen)
            self.assertTrue(runtime.bundled_rgb_sessions[self.session['id']].status()['incomplete'])
            self.assertTrue(runtime.preview_pumps[self.session['id']].stopped.is_set())
            with patch.object(LocalBundleClient, '_read', side_effect=AssertionError('read after unknown ACK')):
                self.assertEqual(self.consume(runtime, self.anchor_b)['state'], 'refused')
            self.assertEqual(self.reads, ['/frame.npz', '/calibration'])

    def test_stop_during_npz_refuses_before_calibration_or_offer(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            def changed(client, path):
                value = self.camera_read(client, path)
                runtime.preview_pumps[self.session['id']].stop()
                return value
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=changed):
                self.consume(runtime, self.anchor_b)
            self.assertFalse(self.seen)
            self.assertEqual(self.reads, ['/frame.npz'])
            self.assertTrue(runtime.bundled_rgb_sessions[self.session['id']].status()['incomplete'])

    def test_generation_change_during_npz_refuses_current_successor(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            def changed(client, path):
                value = self.camera_read(client, path)
                runtime.runners[self.session['id']].generation += 1
                return value
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=changed):
                self.consume(runtime, self.anchor_b)
            self.assertFalse(self.seen)
            self.assertEqual(self.reads, ['/frame.npz'])
            self.assertTrue(runtime.bundled_rgb_sessions[self.session['id']].status()['incomplete'])

    def test_missing_or_expired_receiver_clock_never_becomes_an_exposure(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            with patch('bundle_rgb_input.clock_context', side_effect=OSError('missing protected clock')), \
                 patch.object(LocalBundleClient, '_read', side_effect=AssertionError('read without clock')):
                self.consume(runtime, self.anchor_b)
            self.assertFalse(self.seen)
            terminals = [e for e in self.store.list_events(self.session['id'])
                if e['kind'] == 'assessment.bundle_rgb.terminal']
            terminal = terminals[-1]['payload']
            self.assertEqual(terminal['status'], 'refused')
            self.assertEqual(terminal['reason'], 'qualified_receiver_clock_unavailable')
            row = self.store.get_media(self.session['id'], terminal['support_media_ref']['id'])
            body = json.loads(self.store.media_path(self.session['id'], row['id']).read_bytes())
            self.assertIsNone(body['receiver_clock']); self.assertIsNone(body['timing'])
            self.assertIsNone(body['selected_bundled_frame_ref'])
            self.assertEqual(len(self.tap_inputs), 2)  # B remains separate eligible semantic evidence.

    def test_capacity_failure_precedes_camera_reads_and_stops_owned_pump(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            with patch.object(self.store, 'check_capacity', side_effect=ValueError('quota_full')), \
                 patch.object(LocalBundleClient, '_read', side_effect=AssertionError('read after quota failure')):
                with self.assertRaisesRegex(ValueError, 'quota_full'):
                    self.consume(runtime, self.anchor_b)
            self.assertFalse(self.seen)
            self.assertTrue(runtime.bundled_rgb_sessions[self.session['id']].status()['incomplete'])
            self.assertTrue(runtime.preview_pumps[self.session['id']].stopped.is_set())

    def test_budget_retains_closure_cost_without_spending_unused_maximum_http_size(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            self.release.set()
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                self.consume(runtime, self.anchor_b)
            # These tiny compressed authored inputs plus fixed 1.1 MiB derived
            # planes/metadata fit comfortably; unused 4 MiB HTTP allowances are
            # reservations, not permanently retained objects.
            self.assertLess(runtime.bundled_rgb_sessions[self.session['id']].status()['retained_bytes'], 5*1024**2)

    def test_exact_retry_preserves_first_availability_and_identity_conflict_refuses(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded()
            self.release.set()
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                first = self.consume(runtime, self.anchor_b)
            original = self.store.get_media(self.session['id'], first['media_ids'][0])
            self.now += .1
            with patch.object(LocalBundleClient, '_read', side_effect=AssertionError('retry read')):
                again = self.consume(runtime, self.anchor_b)
                self.assertEqual(again['media_ids'], first['media_ids'])
                changed = json.loads(base64.urlsafe_b64decode(self.anchor_b))
                changed['validation_id'] = 'conflicting-identity'
                bad = base64.urlsafe_b64encode(json.dumps(changed).encode()).decode()
                self.consume(runtime, bad)
            self.assertEqual(self.store.get_media(self.session['id'], original['id'])['metadata']['available_at'],
                original['metadata']['available_at'])
            self.assertEqual(runtime.bundled_rgb_sessions[self.session['id']].status()['capture_count'], 2)
            self.assertEqual(runtime.bundled_rgb_sessions[self.session['id']].status()['reason'], 'bundled_original_identity_changed')

    def test_final_allowed_pair_offers_then_owned_real_pump_stops_without_third_fetch(self):
        self.max_captures = 2
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.build_bundled()
            self.release.set()
            headers = iter([self.anchor_a, self.anchor_b]); fetches = []
            def fetch():
                fetches.append(1)
                self.now = capture.NOW + (.05 if len(fetches) == 1 else .55)
                return self.jpeg, next(headers)
            pump = PreviewPump(fetch, runtime.preview_pumps[self.session['id']].consume, lambda: True)
            runtime.preview_pumps[self.session['id']] = pump
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                pump.start(); pump.thread.join(3)
            self.assertFalse(pump.thread.is_alive())
            self.assertEqual(len(fetches), 2)
            self.assertTrue(self.entered.wait(2), (runtime.bundled_rgb_sessions[self.session['id']].status(), pump.counters()))
            self.assertTrue(runtime.bundled_rgb_sessions[self.session['id']].status()['window_closed'])

    def test_clock_anchors_never_replace_selected_model_and_graph_rgb(self):
        from graph import graph
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded(); self.release.set()
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                result = self.consume(runtime, self.anchor_b)
            self.assertTrue(self.entered.wait(2))
            rgb = self.store.get_media(self.session['id'], result['media_ids'][0])
            anchors = [m for m in self.store.list_media(self.session['id'])['items']
                if m['metadata'].get('stream') == 'bundled_rgb_clock_anchor']
            self.assertEqual(len(anchors), 2)
            self.assertTrue(all(a['id'] != rgb['id'] for a in anchors))
            before = graph(self.store, self.session['id'])
            node = next(n for n in before['sources'] if n['id'] == 'rgb_camera')
            self.assertEqual(node['native']['media_id'], rgb['id'])
            self.assertTrue(all(v[1]['frame']['frame_id'] == rgb['metadata']['frame_id'] for v in self.seen))
            # The next independently eligible JPEG is B semantic evidence only;
            # unavailable qualified receipt must not replace the earlier C PNG.
            self.now = capture.NOW + .7
            next_header = capture.header(self.jpeg, sequence=20,
                observed_ns=1800000000600000000, host_clock_uncertainty_seconds=.39,
                uncertainty_seconds=.410001, frame_device_ns=50_800_000_000,
                host_send_monotonic_ns=10_600_000_000, host_receive_monotonic_ns=10_620_000_000,
                observed_monotonic_seconds=10.61)
            with patch('bundle_rgb_input.clock_context', side_effect=OSError('unavailable')):
                self.consume(runtime, next_header)
            self.assertEqual(self.tap_inputs[-1], (self.jpeg, next_header))
            after = graph(self.store, self.session['id'])
            node = next(n for n in after['sources'] if n['id'] == 'rgb_camera')
            self.assertEqual(node['native']['media_id'], rgb['id'])
            self.now = rgb['metadata']['valid_until'] + .01
            self.assertEqual(runtime.live(self.session)['arms']['B']['status'], 'stale')

    def test_two_recordings_have_independent_anchors_budget_and_stop_fences(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded(); self.release.set()
            first_sid = self.session['id']; first_source = runtime.bundled_rgb_sessions[first_sid]
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                self.consume(runtime, self.anchor_b)
            self.assertTrue(self.entered.wait(2))
            runtime.stop_session(self.session)
            self.store.transition(first_sid, 'stopped')
            second, _ = self.store.create_session('den', 'placement-same', 'Second', 'second-bundled')
            self.store.transition(second['id'], 'starting'); self.store.transition(second['id'], 'recording')
            self.session = self.store.get_session(second['id'])
            # New recording originals are genuinely later; no old lease/frame
            # can cross the session boundary even with identical source epoch.
            self.now = capture.NOW + 1.05
            def dormant(pump):
                pump.thread = threading.Thread(target=lambda: None)
                pump.thread.start(); pump.thread.join()
            with patch.object(PreviewPump, 'start', dormant):
                outcome = runtime.start_session(self.session)
            self.assertEqual(outcome['inference'], 'ready', outcome)
            second_source = runtime.bundled_rgb_sessions[second['id']]
            self.assertIsNot(first_source, second_source)
            self.assertIsNone(second_source.previous)
            self.assertFalse(second_source.stopped.is_set())
            self.assertEqual(second_source.capture_count, 0)
            self.assertEqual(second_source.records, {})
            # An old callback cannot bind or write into the successor recording.
            self.assertIsNone(first_source.ingest(second['id'], self.jpeg, self.anchor_b))
            self.assertEqual(second_source.capture_count, 0)
            self.assertEqual(self.store.list_media(second['id'])['items'], [])
            # Use fresh original A/B receipt times with the same actual runtime.
            def shifted(header):
                body = json.loads(base64.urlsafe_b64decode(header))
                body['observed_at_unix_ns'] += 1_000_000_000
                for key in ('challenge_device_ns', 'frame_device_ns', 'host_send_monotonic_ns', 'host_receive_monotonic_ns'):
                    body[key] += 1_000_000_000
                body['observed_monotonic_seconds'] += 1
                return base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
            self.consume(runtime, shifted(self.anchor_a))
            self.assertEqual(second_source.capture_count, 1)
            self.assertEqual(len(self.reads), 2)  # Second A does not read NPZ.
            self.assertIsNotNone(second_source.previous)
            self.time_shift = 1
            with np.load(io.BytesIO(self.bundle), allow_pickle=False) as archive:
                values = {k:archive[k].copy() for k in archive.files}
            meta = json.loads(values['metadata_json'].tobytes())
            for component in meta['frames'].values():
                component['device_timestamp_seconds'] += 1
                component['host_monotonic_timestamp_seconds'] += 1
            values['metadata_json'] = np.frombuffer(json.dumps(meta).encode(), np.uint8)
            output = io.BytesIO(); np.savez_compressed(output, **values); self.bundle = output.getvalue()
            self.now = capture.NOW + 1.55; self.entered.clear(); self.seen.clear()
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.camera_read):
                result = self.consume(runtime, shifted(self.anchor_b))
            self.assertTrue(self.entered.wait(2), second_source.status())
            self.assertEqual(second_source.capture_count, 2)
            self.assertEqual(len(self.reads), 4)
            self.assertTrue(result['media_ids'])
            self.assertTrue(all(v[1]['session_id'] == second['id'] for v in self.seen))

    def test_optional_anchor_callback_false_is_refusal_not_model_evidence(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.build_bundled()
            source = runtime.bundled_rgb_sessions[self.session['id']]
            source.anchor_tap = lambda *_: False
            self.consume(runtime, self.anchor_a)
            row = next(r for r in self.store.list_media(self.session['id'])['items']
                if r['metadata'].get('stream') == 'bundled_rgb_input_original')
            body = json.loads(self.store.media_path(self.session['id'], row['id']).read_bytes())
            self.assertEqual(body['la_anchor_tap'], dict(status='refused', reason='optional_anchor_callback_refused'))

    def test_old_inflight_capture_cannot_write_or_offer_into_new_recording(self):
        with patch('bundle_rgb_input.sync_directory'):
            runtime = self.seeded(); self.release.set()
            prior = self.session; old_source = runtime.bundled_rgb_sessions[prior['id']]
            successor = []
            def stopped_then_new(client, path):
                value = self.camera_read(client, path)
                runtime.stop_session(prior); self.store.transition(prior['id'], 'stopped')
                new, _ = self.store.create_session('den', 'placement-same', 'Next', 'inflight-successor')
                self.store.transition(new['id'], 'starting'); self.store.transition(new['id'], 'recording')
                new = self.store.get_session(new['id']); successor.append(new)
                def dormant(pump):
                    pump.thread = threading.Thread(target=lambda: None)
                    pump.thread.start(); pump.thread.join()
                with patch.object(PreviewPump, 'start', dormant): runtime.start_session(new)
                return value
            with patch('bundle_rgb_input.clock_context', side_effect=self.clock_context), \
                 patch.object(LocalBundleClient, '_read', autospec=True, side_effect=stopped_then_new):
                # Retaining the old terminal after final Stop may itself refuse;
                # this remains incomplete, never a new-session media write.
                from store import LabError
                with self.assertRaisesRegex(LabError, 'session is not recording'):
                    self.consume(runtime, self.anchor_b)
            self.assertEqual(self.reads, ['/frame.npz'])
            self.assertFalse(self.seen)
            self.assertTrue(old_source.incomplete)
            new = successor[0]; source = runtime.bundled_rgb_sessions[new['id']]
            self.assertIsNot(source, old_source)
            self.assertEqual(source.capture_count, 0); self.assertIsNone(source.previous)
            self.assertEqual(source.records, {})
            self.assertEqual(self.store.list_media(new['id'])['items'], [])
            self.assertEqual(len(self.tap_inputs), 2)  # No successor tap from old in-flight work.


if __name__ == '__main__': unittest.main()

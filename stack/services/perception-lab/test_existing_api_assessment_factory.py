"""Actual Lab factory/Store/runner; camera/model/process I/O is authored."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config import Settings
from store import Store
from chrony_preview_client import ChronyPreviewClient
from live_detector_app import build_runtime, validate_config
from preview_client import LocalBundleClient, PreviewPump
from test_ha_provisioning import live_config
import live_capture_fixtures as capture

POLICY = dict(schema='home-lab-existing-api-assessment-policy-1', version=1,
    max_captures=24, max_npz_reads_per_capture=2, max_retained_bytes=41943040)


class _Recorder:
    def start(self, _): return {'state': 'recording'}
    def stop(self, _): return {'state': 'stopped'}


class ExistingApiAssessmentFactoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='authored-existing-api-')
        self.addCleanup(self.directory.cleanup)
        self.now = capture.NOW - .5
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0,
            cameras=({'id': 'den'},)), clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.session, _ = self.store.create_session('den', 'placement-same', 'Authored', 'factory-existing-api')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.session = self.store.get_session(self.session['id'])
        self.config = live_config(schema_version=3, existing_api_assessment=deepcopy(POLICY))
        self.config['receiver_clock']['uncertainty_seconds'] = .0001
        self.profiles = {arm: deepcopy(body['profile']) for arm, body in self.config['arms'].items()}
        self.order, self.reads = [], []
        self.jpeg, self.header, self.bundle, self.calibration = capture.inputs(
            preview_sha256=hashlib.sha256(bytes(540 * 640)).hexdigest())
        self.raw_calibration = json.dumps(self.calibration, indent=1).encode()
        if sys.platform == 'win32':
            sync = patch('assessment_existing_api.sync_directory')
            sync.start(); self.addCleanup(sync.stop)

    def build(self, *, la_error=False, config=None):
        def prior(*_):
            self.order.append('existing_LA_tap')
            if la_error: raise ValueError('authored LA tap failure')
        runtime = build_runtime(self.store, SimpleNamespace(camera_id='den'), config or self.config,
            transport_factory=lambda *_: lambda *_: None, lease=SimpleNamespace(ready=lambda: True),
            la_live=SimpleNamespace(running=lambda: True, offer_preview=prior))
        self.addCleanup(runtime.close)
        runtime.recorders['den'] = _Recorder()
        # Keep the actual RegisteredPairs resolver and its recipe sampling,
        # family grants and activation metadata. Only external worker readiness
        # is doubled by the explicit lease passed to build_runtime above.
        def execute(request):
            profile = self.profiles[request['arm']]
            return dict(request_id=request['request_id'], engine_epoch=profile['engine_epoch'],
                artifact_sha256=profile['artifact_sha256'], terminal=True, state='completed',
                result=dict(schema_version=1, coordinate_space='source_pixels',
                    frame_id=request['frame']['frame_id'], objects=[], boxes=[]))
        runtime.executor = execute
        def dormant(pump):
            pump.thread = threading.Thread(target=lambda: None)
            pump.thread.start(); pump.thread.join()
        with patch.object(PreviewPump, 'start', dormant):
            outcome = runtime.start_session(self.session)
        self.assertEqual(outcome['inference'], 'ready', outcome)
        self.now = capture.NOW + .2
        offer = runtime.offer
        def offered(*args):
            result = offer(*args)
            self.order.append('normal_model_offer')
            return result
        runtime.offer = offered
        return runtime

    def read(self, _client, path):
        self.reads.append(path)
        self.order.append(path)
        self.assertIn('normal_model_offer', self.order)
        self.assertIn('existing_LA_tap', self.order)
        if path == '/frame.npz': return self.bundle, ''
        if path == '/calibration': return self.raw_calibration, ''
        raise AssertionError('unregistered camera route')

    def consume(self, runtime):
        with patch.object(LocalBundleClient, '_read', autospec=True, side_effect=self.read):
            return runtime.preview_pumps[self.session['id']].consume(self.jpeg, self.header)

    def test_config3_is_explicit_and_config1_2_field_acceptance_is_preserved(self):
        self.assertEqual(validate_config(self.config), self.config)
        self.assertEqual(validate_config(live_config()), live_config())
        paired = live_config(schema_version=2, paired_preview=dict(
            schema='home-lab-paired-preview-1', version=1, enabled=True, max_pairs=8))
        self.assertEqual(validate_config(paired), paired)
        wrong = [live_config(existing_api_assessment=POLICY),
            {**paired, 'existing_api_assessment': POLICY},
            {**self.config, 'paired_preview': paired['paired_preview']},
            {key: value for key, value in self.config.items() if key != 'existing_api_assessment'},
            {**self.config, 'existing_api_assessment': {**POLICY, 'max_captures': 25}}]
        for body in wrong:
            with self.subTest(body=body):
                with self.assertRaises(ValueError): validate_config(body)

    def test_real_factory_keeps_chrony_and_model_offer_before_private_retention(self):
        runtime = self.build()
        self.assertIs(type(runtime.preview_clients['den']), ChronyPreviewClient)
        self.assertFalse(getattr(runtime, 'paired_preview_sources', {}))
        self.consume(runtime)
        self.assertLess(self.order.index('normal_model_offer'), self.order.index('/frame.npz'))
        rows = self.store.list_media(self.session['id'])['items']
        associations = [r for r in rows if r['metadata'].get('stream') == 'assessment_existing_api_association']
        self.assertEqual(len(associations), 1, runtime.existing_api_assessment.status())
        self.assertFalse(any(r['metadata'].get('stream') == 'paired_preview_original' for r in rows))
        body = json.loads(self.store.media_path(self.session['id'], associations[0]['id']).read_bytes())
        self.assertTrue(body['candidate_only']); self.assertIsNone(body['operational_geometry'])
        self.assertEqual(body['depth_clock']['status'], 'unassessed')

    def test_existing_LA_tap_failure_cannot_skip_assessment_or_model_offer(self):
        runtime = self.build(la_error=True)
        self.consume(runtime)
        self.assertEqual(self.order[:3], ['normal_model_offer', 'existing_LA_tap', '/frame.npz'])
        events = self.store.list_events(self.session['id'])
        terminals = [e for e in events if e['kind'] == 'assessment.capture.terminal']
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]['payload']['status'], 'retained')

    def test_stop_during_numeric_read_prevents_next_camera_request(self):
        runtime = self.build()
        def stop_after_npz(client, path):
            result = self.read(client, path)
            runtime.preview_pumps[self.session['id']].stop()
            return result
        with patch.object(LocalBundleClient, '_read', autospec=True, side_effect=stop_after_npz):
            runtime.preview_pumps[self.session['id']].consume(self.jpeg, self.header)
        self.assertEqual(self.reads, ['/frame.npz'])
        with patch.object(LocalBundleClient, '_read', side_effect=AssertionError('read after Stop')):
            runtime.existing_api_assessment(self.jpeg, self.header)

    def test_real_resolution_timestamp_uses_exact_admitted_integer_conversion(self):
        # Actual8eb used int/float before scaling and differs at this boundary.
        # Retention must consume the reviewed upstream fix, not accept a second
        # observation time for the same original capture in a private adapter.
        stamp = 1800000000000000120
        precise = float(Decimal(stamp) / Decimal(1000000000))
        self.assertNotEqual(stamp / 1e9, precise)
        self.jpeg, self.header, self.bundle, self.calibration = capture.inputs(
            observed_ns=stamp, preview_sha256=hashlib.sha256(bytes(540 * 640)).hexdigest())
        self.raw_calibration = json.dumps(self.calibration, indent=1).encode()
        runtime = self.build()
        self.consume(runtime)
        rows = self.store.list_media(self.session['id'])['items']
        rgb = next(row for row in rows if row['metadata'].get('stream') == 'rgb')
        self.assertEqual(rgb['metadata']['source_timing_receipt']['observed_at_unix_ns'], stamp)
        self.assertEqual(rgb['metadata']['observed_at'], precise)
        self.assertEqual(sum(row['metadata'].get('stream') == 'assessment_existing_api_association'
                             for row in rows), 1, runtime.existing_api_assessment.status())


if __name__ == '__main__': unittest.main()

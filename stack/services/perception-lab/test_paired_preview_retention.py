"""Real factory/Store/preview pump with authored camera and model external I/O.

The Windows runner stubs only directory fsync, whose failure is separately
covered. There is no camera, host, model, install or physical writer operation.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config import Settings
from store import Store, LabError
from live_detector_app import build_runtime, validate_config
from preview_client import PreviewPump
from paired_preview_client import PairedPreviewClient, checked_pair
from bundle_ingest import calibration_content_id
from depth_clock import DepthClock
from test_ha_provisioning import live_config
from test_paired_preview_client import authored_pair, wire, commissioned_double
import live_capture_fixtures as capture

BLOCK = {'schema': 'home-lab-paired-preview-1', 'version': 1, 'enabled': True, 'max_pairs': 3}


class Recorder:
    def start(self, session):return {'state': 'recording'}
    def stop(self, session):return {'state': 'stopped'}


class PairedPreviewRetentionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='authored-paired-preview-')
        self.addCleanup(self.directory.cleanup)
        self.now = capture.NOW-.5
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0,
            cameras=({'id': 'den'},)), clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.session, _ = self.store.create_session('den', 'detection', 'Authored', 'paired-preview-request')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.now = capture.NOW+.2
        self.session = self.store.get_session(self.session['id'])
        self.config = live_config(schema_version=2, paired_preview=deepcopy(BLOCK))
        validate_config(self.config)
        self.runtime = build_runtime(self.store, SimpleNamespace(camera_id='den'), self.config,
            transport_factory=lambda *_: lambda *_: None, lease=SimpleNamespace(ready=lambda: True),
            experiment_id='placement-best')
        self.addCleanup(self.runtime.close)
        self.runtime.recorders['den'] = Recorder()
        self.runtime.resolve_profiles = lambda *_: {'state': 'ready',
            'profiles': {arm: row['profile'] for arm, row in self.config['arms'].items()}}
        self.calls, self.dispatches = [], []
        self.pair = authored_pair()
        self.client = self.runtime.preview_clients['den']
        self.client.transport = lambda token, timeout: wire(self.pair)
        self.acceptance = patch('chrony_preview_client.commissioned_runtime',
            return_value=commissioned_double(self.calls))
        self.acceptance.start(); self.addCleanup(self.acceptance.stop)
        self.sync = patch('paired_preview_client.sync_directory')
        self.sync.start(); self.addCleanup(self.sync.stop)
        self.runtime.executor = self.execute
        def dormant(pump):
            pump.thread = threading.Thread(target=lambda: None)
            pump.thread.start(); pump.thread.join()
        with patch.object(PreviewPump, 'start', dormant):
            self.runtime.start_session(self.session)
        self.pump = self.runtime.preview_pumps[self.session['id']]

    def execute(self, request):
        # This assertion runs in the actual worker executor after real offer.
        originals = [m for m in self.store.list_media(self.session['id'])['items']
            if m['metadata'].get('stream') == 'paired_preview_original']
        if len(originals) != 1:
            raise AssertionError('exact pair must be retained BEFORE model offer')
        with self.store.media_path(self.session['id'], originals[0]['id']).open('rb') as stream:
            original = json.load(stream)
        if original['rgb_frame_id'] != request['frame']['frame_id']:
            raise AssertionError('offered RGB changed after exact pair retention')
        bundle = self.store.get_media(self.session['id'], original['bundle_media_id'])
        if bundle['metadata']['source_metadata']['frames']['rgb']['sequence'] != original['rgb_receipt']['sequence']:
            raise AssertionError('retained bundle has another RGB sequence')
        self.dispatches.append(deepcopy(request))
        p = self.config['arms'][request['arm']]['profile']
        return {'request_id': request['request_id'], 'engine_epoch': p['engine_epoch'],
            'artifact_sha256': p['artifact_sha256'], 'terminal': True, 'state': 'completed',
            'result': {'schema_version': 1, 'coordinate_space': 'source_pixels',
                'frame_id': request['frame']['frame_id'], 'boxes': [], 'objects': []}}

    def settled(self):
        until = time.monotonic()+3
        while time.monotonic() < until:
            runner = self.runtime.runners[self.session['id']]
            if len(self.dispatches) == 2 and all(v is None for v in runner.snapshot()['active'].values()):return
            time.sleep(.005)
        self.fail('bounded authored model double did not settle')

    def direct(self, pair=None):
        value = self.pair if pair is None else pair
        return self.runtime.ingest_paired_preview(self.session['id'], value['jpeg'], value['header'], value)

    def originals(self):
        return [m for m in self.store.list_media(self.session['id'])['items']
            if m['metadata'].get('stream') == 'paired_preview_original']

    def test_actual_factory_pump_retains_same_pair_before_both_model_offers(self):
        self.assertIsInstance(self.client, PairedPreviewClient)
        self.assertFalse(self.runtime.bundle_pumps)
        self.assertFalse(self.runtime.bundle_clients)
        self.assertTrue(self.pump._attempt())
        self.settled()
        original = self.originals()[0]
        with self.store.media_path(self.session['id'], original['id']).open('rb') as stream:
            body = json.load(stream)
        self.assertEqual(body['rgb_receipt']['observed_at_unix_ns'], capture.NOW*1000000000)
        self.assertEqual(body['source_metadata']['frames']['depth']['sequence'], 41)
        self.assertEqual(body['depth_clock_status'], 'unavailable')
        self.assertIsNone(body['depth_clock_mapping'])
        self.assertEqual(body['geometric_validity'], 'unvalidated')
        self.assertIsNone(body['operational_geometry'])
        self.assertEqual(self.dispatches[0]['frame'], self.dispatches[1]['frame'])
        # No commissioned mapping is invented just because a raw pair exists.
        from service_runtime import BundleSynchronizedAssociation
        matcher = BundleSynchronizedAssociation.__new__(BundleSynchronizedAssociation)
        matcher.depth_clock = None
        self.assertEqual(matcher._own_bundle_depth(self.session['id'], {}), (None, 'depth_clock_not_commissioned'))

    def test_existing_exact_matcher_derives_distinct_original_depth_time(self):
        _, cp, _, _, _ = capture.setup()
        self.runtime.paired_depth_clocks['den'] = deepcopy(cp['depth_clock'])
        self.assertTrue(self.pump._attempt())
        self.settled()
        with self.store.media_path(self.session['id'], self.originals()[0]['id']).open('rb') as stream:
            original = json.load(stream)
        self.assertEqual(original['depth_clock_status'], 'commissioned_mapping')
        self.assertAlmostEqual(original['depth_clock_mapping']['observed_at'], capture.NOW-.01)
        from service_runtime import BundleSynchronizedAssociation
        matcher = BundleSynchronizedAssociation.__new__(BundleSynchronizedAssociation)
        matcher.store = self.store
        matcher.depth_clock = DepthClock(self.store, site_id='victoria', camera_id='den', clock=cp['depth_clock'])
        frame = self.dispatches[0]['frame']
        depth_id, reason = matcher._own_bundle_depth(self.session['id'], frame)
        self.assertIsNone(reason)
        depth = self.store.get_media(self.session['id'], depth_id)
        self.assertEqual(depth['metadata']['sequence'], 41)
        self.assertAlmostEqual(depth['metadata']['observed_at'], capture.NOW-.01)
        self.assertEqual(depth['metadata']['geometric_validity'], 'unvalidated')
        first_available = depth['metadata']['available_at']
        self.now += .1
        self.assertEqual(matcher._own_bundle_depth(self.session['id'], frame), (depth_id, None))
        self.assertEqual(self.store.get_media(self.session['id'], depth_id)['metadata']['available_at'], first_available)

    def test_wrong_exact_pair_never_retains_or_offers(self):
        wrong = authored_pair(sequence=8)
        wrong['header'], wrong['jpeg'] = self.pair['header'], self.pair['jpeg']
        with self.assertRaisesRegex(ValueError, 'pair binding'):
            self.direct(wrong)
        self.assertFalse(self.originals()); self.assertFalse(self.dispatches)

    def test_retention_failure_prevents_original_rgb_offer(self):
        source = self.runtime.paired_preview_sources['den']
        with patch.object(source, 'ingest', side_effect=OSError('authored retention failure')):
            with self.assertRaises(OSError):self.direct()
        self.assertFalse(self.dispatches)

    def test_directory_durability_failure_never_offers_and_exact_retry_keeps_original(self):
        with patch('paired_preview_client.sync_directory', side_effect=OSError('authored directory sync failure')):
            with self.assertRaises(OSError):self.direct()
        first = self.originals()[0]
        self.assertFalse(self.dispatches)
        self.now += .1
        retry = deepcopy(self.pair); retry['received_at'] = self.now
        result = self.direct(retry)
        self.settled()
        self.assertEqual(result['paired_original_media_id'], first['id'])
        self.assertEqual(self.originals()[0]['created_at'], first['created_at'])

    def test_stop_during_retention_prevents_offer(self):
        source = self.runtime.paired_preview_sources['den']; original = source.ingest
        def stop(*args):
            item = original(*args)
            self.runtime.stop_session(self.session)
            return item
        with patch.object(source, 'ingest', side_effect=stop):
            with self.assertRaisesRegex(ValueError, 'stopped switched or expired'):
                self.direct()
        self.assertFalse(self.dispatches)

    def test_generation_switch_during_retention_prevents_offer(self):
        source = self.runtime.paired_preview_sources['den']; original = source.ingest
        def switch(*args):
            item = original(*args)
            self.runtime.runners[self.session['id']].generation += 1
            return item
        with patch.object(source, 'ingest', side_effect=switch):
            with self.assertRaisesRegex(ValueError, 'stopped switched or expired'):
                self.direct()
        self.assertFalse(self.dispatches)

    def test_slow_retention_consumes_original_lease_without_refresh(self):
        source = self.runtime.paired_preview_sources['den']; original = source.ingest
        def slow(*args):
            item = original(*args); self.now += 3; return item
        with patch.object(source, 'ingest', side_effect=slow):
            with self.assertRaisesRegex(ValueError, 'stopped switched or expired'):
                self.direct()
        self.assertFalse(self.dispatches)
        item = next(m for m in self.store.list_media(self.session['id'])['items'] if m['metadata'].get('stream')=='rgb')
        checked = checked_pair(self.pair['jpeg'], self.pair['header'], self.pair['bundle'], self.pair['calibration'])
        self.assertEqual(item['metadata']['valid_until'], capture.NOW+3-checked['uncertainty_seconds'])

    def test_pair_budget_counts_originals_exact_retry_does_not_refresh(self):
        self.runtime.paired_preview_policy['max_pairs'] = 1
        first = self.direct(); self.settled()
        before = deepcopy(self.originals()[0])
        self.now += .1
        duplicate = deepcopy(self.pair); duplicate['received_at'] = self.now
        self.assertEqual(self.direct(duplicate)['paired_original_media_id'], first['paired_original_media_id'])
        self.assertEqual(self.originals()[0], before)
        with self.assertRaisesRegex(ValueError, 'session budget exhausted'):
            self.direct(authored_pair(sequence=8))
        self.assertEqual(len(self.dispatches), 2)

    def test_real_preview_thread_stops_after_last_allowed_pair_without_extra_fetch(self):
        self.runtime.paired_preview_policy['max_pairs'] = 1
        fetches = []
        def fetch(token, timeout):
            fetches.append((token, timeout))
            return wire(self.pair)
        self.client.transport = fetch
        sid = self.session['id']
        pump = PreviewPump(self.client,
            lambda jpeg, header:self.runtime.ingest_paired_preview(sid, jpeg, header,
                self.client.take_pair(jpeg, header)),
            lambda:not self.runtime.closed and self.store.get_session(sid)['state']=='recording')
        self.runtime.preview_pumps[sid] = pump
        pump.start()
        pump.thread.join(timeout=3)
        self.assertFalse(pump.thread.is_alive(), 'bounded exhausted preview thread did not stop')
        self.settled()
        self.assertEqual(len(fetches), 1)
        self.assertEqual(len(self.originals()), 1)
        self.assertEqual(len(self.dispatches), 2)
        self.assertEqual(pump.status, 'stopped')
        state = self.runtime.paired_preview_state[sid]
        self.assertEqual((state['status'], state['pairs_consumed'], state['max_pairs']), ('exhausted', 1, 1))
        self.assertEqual(self.store.get_session(sid)['state'], 'recording')
        self.assertFalse(self.runtime.runners[sid].closed)
        with self.assertRaisesRegex(ValueError, 'session budget exhausted'):
            self.direct(authored_pair(sequence=8))

    def test_capacity_projection_includes_all_derivatives_before_writes(self):
        check = self.store.check_capacity; reservations = []
        def limited(incoming=0, measured_staging=None):
            reservations.append(incoming)
            if incoming > 1024:raise LabError('authored capacity refusal', 507, 'quota_exceeded')
            return check(incoming, measured_staging)
        with patch.object(self.store, 'check_capacity', side_effect=limited):
            with self.assertRaises(LabError):self.direct()
        self.assertGreater(reservations[0], len(self.pair['jpeg'])+len(self.pair['bundle'])+640*400*2)
        self.assertFalse(self.originals()); self.assertFalse(self.dispatches)

    def test_private_successor_is_opt_in_and_legacy_factory_unchanged(self):
        legacy = live_config()
        self.assertEqual(validate_config(legacy), legacy)
        for bad in (live_config(paired_preview=BLOCK), live_config(schema_version=2),
                    live_config(schema_version=2, paired_preview={**BLOCK, 'max_pairs': 25})):
            with self.assertRaises(ValueError):validate_config(bad)
        old = build_runtime(self.store, SimpleNamespace(camera_id='den'), legacy,
            transport_factory=lambda *_: lambda *_: None, lease=SimpleNamespace(ready=lambda: False),
            experiment_id='placement-best')
        try:
            self.assertFalse(old.paired_preview_sources)
            self.assertFalse(old.bundle_sources)
            self.assertNotIsInstance(old.preview_clients['den'], PairedPreviewClient)
        finally:old.close()


if __name__ == '__main__':
    unittest.main()

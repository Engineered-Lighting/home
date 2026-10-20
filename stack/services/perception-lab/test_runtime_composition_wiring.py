"""Managed runtime composition: spatial scheduling, caption startup checks and
saved Qwen analysis, each inert until provisioned."""
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import test_paired_runtime as fixtures
import test_room_context as maps
from test_depth_association import CLOCK, REGISTRATION, calibration, frames, rgb_box, scene
from test_ha_provisioning import live_config
from test_managed_readiness import Host
from bundle_ingest import BundleIngestor
from live_detector_app import build_runtime, spatial_provisioning, validate_config
from preview_client import LocalBundleClient
from service_runtime import BundleSynchronizedAssociation, ServiceRuntime
from spatial_consumer import SpatialConsumer
from spatial_validation import mount_signature

STEREO_A = {'a': {'localization': 'stereo', 'room_relations': True}, 'b': {'localization': 'none', 'room_relations': False}}


def settle(predicate, seconds=5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(.01)
    return False


class Recorder:
    def start(self, session):
        return {'state': 'recording'}

    def stop(self, session):
        return {'state': 'stopped'}


class SpatialFixture:
    """A recorded OAK bundle, its own preview RGB frame and a provisioned file."""

    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.store.settings = replace(self.store.settings, cameras=({'id': 'den'},))
        self.runtimes = []

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close(timeout=2)
        fixtures.PairedRuntimeTests.tearDown(self)

    def bundle(self):
        sid = self.session['id']
        self.depth = scene()
        meta = {'schema': 1, 'session': 'capture1', 'depth_alignment': 'rectified_left', 'depth_units': 'millimetres',
                'invalid_depth': 0, 'captured_unix_ns': 100_500_000_000,
                'captured_unix_ns_semantics': 'bundle_packaging_time_not_sensor_exposure', 'frames': frames()}
        arrays = {'depth_mm': self.depth, 'left': np.zeros((400, 640), np.uint8), 'right': np.zeros((400, 640), np.uint8),
                  'rgb_nv12': np.zeros((540, 640), np.uint8), 'metadata_json': np.frombuffer(json.dumps(meta).encode(), np.uint8)}
        output = io.BytesIO()
        np.savez_compressed(output, **arrays)
        cal = {'session': 'capture1', 'depth_alignment': 'rectified_left', 'depth_units': 'millimetres', 'eeprom': {'d': 1}}
        return BundleIngestor(self.store, site_id='victoria', camera_id='den').ingest(sid, output.getvalue(), cal)

    def preview(self, *, sequence=7, device_ns=50_206_000_000, observed=100.4):
        """The causal preview JPEG record (sequence 7 is the bundle's own RGB frame)."""
        receipt = {'schema': 3, 'session': 'capture1', 'timestamp_semantics': 'causal_exact_frame_exposure_end',
                   'frame_device_ns': device_ns, 'observed_at_unix_ns': round(observed*1e9), 'uncertainty_seconds': .003,
                   'timestamp_quantization_ns': 1000, 'validation_id': 'causal:'+'b'*32}
        return self.store.add_media(self.session['id'], b'synthetic-causal-jpeg-%d' % sequence, 'image/jpeg', {
            'site_id': 'victoria', 'camera_id': 'den', 'capture_epoch': 'capture1', 'frame_id': f'capture1-0-{sequence}',
            'sequence': sequence, 'stream': 'rgb', 'format': 'jpeg', 'width': 640, 'height': 360,
            'observed_at': observed, 'available_at': observed+.05, 'valid_until': observed+2.99, 'uncertainty_seconds': .004,
            'clock_mapping_status': 'verified', 'clock_mapping_id': receipt['validation_id'], 'source_timing_receipt': receipt})

    def spatial_file(self, content_id='content', **changes):
        room = maps.RoomContextTests()
        room.setUp()
        cal = calibration(content_id, mount_signature=mount_signature(scene()))
        value = {'schema_version': 1, 'association_revision': 'assoc-1', 'calibration': cal,
                 'registration': {**room.registration, 'matrix': REGISTRATION, 'calibration_version': cal['id'],
                                  'mount_revision': 'mount-1'},
                 'room_map': room.map, 'depth_clock': dict(CLOCK)}
        self.homography = deepcopy(room.homography)
        value.update(changes)
        return value

    def runtime(self, value, policy=STEREO_A):
        value, reasons = spatial_provisioning(value, site_id='victoria', camera_id='den')
        consumer = SpatialConsumer(self.store, value['calibration'], value['registration'], value['room_map'],
                                   association_revision=value['association_revision'])
        associator = BundleSynchronizedAssociation(self.store, value, site_id='victoria', camera_id='den', unavailable=reasons)
        runtime = ServiceRuntime(self.store, {'den': Recorder()},
                                 lambda *_: {'state': 'ready', 'profiles': self.profiles, 'spatial_policy': deepcopy(policy)},
                                 lambda request: None, spatial_sources={'den': consumer}, spatial_associators={'den': associator})
        self.runtimes.append(runtime)
        return runtime

    def output(self, runtime, rgb, *, arm='a', boxes=None, poses=None, valid_until=103.39, available=101.0):
        frame_id = rgb['metadata']['frame_id']
        box = {**rgb_box(), 'label': 'person', 'confidence': .9, 'instance_id': frame_id+':0'}
        result = {'schema_version': 1, 'frame_id': frame_id, 'coordinate_space': 'source_pixels',
                  'boxes': [box] if boxes is None else boxes}
        if poses is not None:
            result['pose_instances'] = poses
        return self.store.append_event(self.session['id'], 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'capture_epoch': 'capture1', 'frame_id': frame_id,
            'observed_at': rgb['metadata']['observed_at'], 'available_at': available, 'valid_until': valid_until,
            'media_ids': [rgb['id']], 'status': 'completed', 'arm': arm,
            'generation': runtime.runners[self.session['id']].generation, 'result': result})

    def events(self, kind):
        return [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind'] == kind]

    def wake(self, runtime, predicate):
        runtime.spatial_ready.set()
        self.assertTrue(settle(predicate), runtime.live(self.session)['spatial_status'])


class SpatialSchedulerTests(SpatialFixture, unittest.TestCase):
    def test_bundle_own_rgb_frame_localizes_through_the_scheduler_without_extending_freshness(self):
        sid = self.session['id']
        bundle = self.bundle()
        rgb = self.preview()
        runtime = self.runtime(self.spatial_file(bundle['metadata']['calibration_content_id']))
        self.assertEqual(runtime.start_session(self.session)['inference'], 'ready')
        self.assertTrue(runtime.spatial_thread.is_alive())
        self.store.clock = lambda: 101.0
        event_id = self.output(runtime, rgb)
        self.wake(runtime, lambda: runtime.live(self.session)['spatial_status']['counts']['published'] == 1)
        status = runtime.live(self.session)['spatial_status']
        self.assertEqual((status['state'], status['arms'], status['last_reason']), ('active', {'a': 'stereo'}, None))
        self.assertEqual(status['counts']['measured'], 1)
        [sampling] = self.events('room.sampling')
        self.assertEqual((sampling['arms'], sampling['source_event_ids'], sampling['measured']), (['a'], [event_id], 1))
        [observation] = self.events('room.observation')
        self.assertEqual(observation['arm'], 'a')
        # The depth record is the bundle's own sync group mapped on this frame.
        [mapped] = [m for m in self.store.list_media(sid)['items'] if m['metadata'].get('derivation') == 'depth-clock-v1']
        self.assertEqual(mapped['metadata']['clock_mapping']['rgb_media_id'], rgb['id'])
        self.assertAlmostEqual(mapped['metadata']['observed_at']-rgb['metadata']['observed_at'], .004, places=6)
        # Freshness: never beyond the specialist output or the 3 s bound.
        self.assertLessEqual(observation['valid_until'], 103.39)
        self.assertLessEqual(observation['valid_until'], rgb['metadata']['observed_at']+3)
        # One output is associated once: a later wake republishes nothing.
        runtime.spatial_ready.set()
        time.sleep(.2)
        self.assertEqual(len(self.events('room.sampling')), 1)

    def test_nearest_but_different_bundle_abstains_instead_of_pairing_another_instant(self):
        sid = self.session['id']
        bundle = self.bundle()
        # The preview came from a later sync group (194 ms after the bundle's RGB).
        rgb = self.preview(sequence=13, device_ns=50_400_000_000, observed=100.594)
        runtime = self.runtime(self.spatial_file(bundle['metadata']['calibration_content_id']))
        runtime.start_session(self.session)
        self.store.clock = lambda: 101.0
        self.output(runtime, rgb)
        self.wake(runtime, lambda: runtime.live(self.session)['spatial_status']['counts']['published'] == 1)
        status = runtime.live(self.session)['spatial_status']
        self.assertEqual((status['last_reason'], status['counts']['measured'], status['counts']['abstained']),
                         ('bundle_rgb_frame_not_recorded', 0, 1))
        [sampling] = self.events('room.sampling')
        self.assertEqual([a['reason'] for a in sampling['abstentions']], ['no_depth_frame_in_window'])
        self.assertEqual(self.events('room.observation'), [])
        self.assertFalse(any(m['metadata'].get('derivation') == 'depth-clock-v1' for m in self.store.list_media(sid)['items']))

    def test_expired_and_superseded_outputs_are_never_published_late(self):
        bundle = self.bundle()
        rgb = self.preview()
        runtime = self.runtime(self.spatial_file(bundle['metadata']['calibration_content_id']))
        runtime.start_session(self.session)
        self.store.clock = lambda: 104.0
        self.output(runtime, rgb, available=101.0)
        self.wake(runtime, lambda: runtime.live(self.session)['spatial_status']['counts']['expired'] == 1)
        self.assertEqual(self.events('room.sampling'), [])
        # Two outputs of one arm between ticks: only the newest is associated.
        self.store.clock = lambda: 101.0
        with runtime.lock:  # hold the scheduler until both outputs exist
            first = self.output(runtime, rgb, boxes=[])
            second = self.output(runtime, rgb, boxes=[])
            runtime.spatial_ready.set()
        self.wake(runtime, lambda: runtime.live(self.session)['spatial_status']['counts']['published'] == 1)
        status = runtime.live(self.session)['spatial_status']
        self.assertEqual(status['counts']['superseded'], 1)
        [sampling] = self.events('room.sampling')
        self.assertEqual(sampling['source_event_ids'], [second])
        self.assertNotEqual(first, second)
        self.assertEqual([a['reason'] for a in sampling['abstentions']], ['no_person_region'])

    def test_scheduler_stops_with_the_session_and_the_runtime(self):
        bundle = self.bundle()
        rgb = self.preview()
        runtime = self.runtime(self.spatial_file(bundle['metadata']['calibration_content_id']))
        runtime.start_session(self.session)
        runtime.stop_session(self.session)
        self.assertEqual(runtime.live(self.session)['spatial_status']['state'], 'stopped')
        self.store.clock = lambda: 101.0
        self.output(runtime, rgb)
        runtime.spatial_ready.set()
        time.sleep(.2)
        self.assertEqual(self.events('room.sampling'), [])
        thread = runtime.spatial_thread
        self.assertTrue(runtime.close(timeout=2)['drained'])
        self.assertFalse(thread.is_alive())
        runtime.spatial_tick()  # a closed runtime associates nothing
        self.assertEqual(self.events('room.sampling'), [])

    def test_floor_projection_arm_uses_the_homography_and_needs_it(self):
        self.bundle()
        rgb = self.preview()
        policy = {'a': {'localization': 'none', 'room_relations': False}, 'b': {'localization': 'floor_projection', 'room_relations': True}}
        value = self.spatial_file()
        value['registration']['floor_homography'] = self.homography
        runtime = self.runtime(value, policy)
        runtime.start_session(self.session)
        self.store.clock = lambda: 101.0
        instance = rgb['metadata']['frame_id']+':0'
        box = {'x': 300, 'y': 100, 'w': 40, 'h': 200, 'label': 'person', 'confidence': .9, 'instance_id': instance}
        poses = [{'instance_id': instance, 'joints': [{'name': n, 'pixel': [310+k*20, 290], 'status': 'supported'}
                                                      for k, n in enumerate(('left_ankle', 'right_ankle'))]}]
        self.output(runtime, rgb, arm='b', boxes=[box], poses=poses)
        self.wake(runtime, lambda: runtime.live(self.session)['spatial_status']['counts']['published'] == 1)
        [observation] = self.events('room.observation')
        self.assertEqual((observation['arm'], observation['result']['quality']['method']), ('b', 'rgb_floor_contact'))
        self.assertLessEqual(observation['valid_until'], 103.39)

    def test_floor_projection_without_a_homography_is_refused_at_activation(self):
        policy = {'a': {'localization': 'none', 'room_relations': False}, 'b': {'localization': 'floor_projection', 'room_relations': True}}
        runtime = self.runtime(self.spatial_file(), policy)
        runtime.start_session(self.session)
        self.assertIsNone(runtime.spatial_thread)
        self.assertEqual(self.events('spatial.unavailable')[0]['arms'],
                         {'b': {'localization': 'floor_projection', 'reason': 'floor_homography_not_provisioned'}})
        self.assertEqual(runtime.live(self.session)['spatial_status']['state'], 'not_provisioned')

    def test_unprovisioned_spatial_is_recorded_and_never_crashes(self):
        runtime = ServiceRuntime(self.store, {'den': Recorder()},
                                 lambda *_: {'state': 'ready', 'profiles': self.profiles, 'spatial_policy': STEREO_A},
                                 lambda request: None, spatial_unavailable='spatial_not_provisioned')
        self.runtimes.append(runtime)
        self.assertEqual(runtime.capabilities()['spatial'], {'state': 'not_provisioned', 'reason': 'spatial_not_provisioned'})
        self.assertEqual(runtime.start_session(self.session)['inference'], 'ready')
        self.assertIsNone(runtime.spatial_thread)
        [event] = self.events('spatial.unavailable')
        self.assertEqual(event['arms'], {'a': {'localization': 'stereo', 'reason': 'spatial_not_provisioned'}})
        self.assertEqual(runtime.live(self.session)['spatial_status']['refused'], event['arms'])
        # Placement-style policies request nothing and record nothing.
        plain = ServiceRuntime(self.store, {'den': Recorder()}, lambda *_: {'state': 'ready', 'profiles': self.profiles},
                               lambda request: None)
        self.runtimes.append(plain)
        self.assertEqual(plain.live(self.session)['spatial_status'], {'state': 'not_requested'})
        with self.assertRaises(ValueError):
            ServiceRuntime(self.store, {}, None, None, spatial_associators={'den': object()})
        with self.assertRaises(ValueError):
            ServiceRuntime(self.store, {}, None, None, spatial_unavailable='Not a code')

    def test_provisioned_capability_reports_each_localization(self):
        runtime = self.runtime(self.spatial_file())
        self.assertEqual(runtime.capabilities()['spatial'], {'state': 'provisioned', 'reason': None, 'localizations': {
            'stereo': {'available': True, 'reason': None},
            'floor_projection': {'available': False, 'reason': 'floor_homography_not_provisioned'}}})

    def test_bundle_ingest_runs_outside_the_runtime_lock(self):
        seen = []
        class Source:
            def ingest(inner, sid, data, calibration):
                seen.append(runtime.lock._is_owned())
                return {'id': 'bundle'}
        runtime = ServiceRuntime(self.store, {'den': Recorder()}, None, None, bundle_sources={'den': Source()})
        self.runtimes.append(runtime)
        done = []
        # Decoding runs in the pump thread; live reads and other sources are not
        # held behind it.
        worker = threading.Thread(target=lambda: done.append(runtime.ingest_bundle(self.session['id'], b'x', {})))
        worker.start()
        worker.join(2)
        self.assertEqual((done, seen), ([{'id': 'bundle'}], [False]))
        runtime.close(timeout=1)
        with self.assertRaisesRegex(ValueError, 'shutting down'):
            runtime.ingest_bundle(self.session['id'], b'x', {})


class SpatialProvisioningTests(SpatialFixture, unittest.TestCase):
    def test_file_schema_bindings_and_freshness_only_tighten(self):
        value, reasons = spatial_provisioning(self.spatial_file(), site_id='victoria', camera_id='den')
        self.assertEqual(reasons, {'stereo': None, 'floor_projection': 'floor_homography_not_provisioned'})
        without = self.spatial_file()
        without.pop('depth_clock')
        self.assertEqual(spatial_provisioning(without, site_id='victoria', camera_id='den')[1]['stereo'],
                         'depth_clock_not_commissioned')
        self.assertEqual(spatial_provisioning(self.spatial_file(max_skew_s=.02), site_id='victoria', camera_id='den')[0]['max_skew_s'], .02)
        bad = [self.spatial_file(extra=1), self.spatial_file(schema_version=2), self.spatial_file(max_skew_s=.2),
               self.spatial_file(max_age_s=2), self.spatial_file(association_revision=''),
               self.spatial_file(depth_clock={**CLOCK, 'semantics_bound_s': .5})]
        unvalidated = self.spatial_file()
        unvalidated['calibration']['status'] = 'measured'
        no_receipt = self.spatial_file()
        no_receipt['calibration']['reprojection'] = {'p95_px': 1.0}
        moved = self.spatial_file()
        moved['registration']['mount_revision'] = 'mount-2'
        bad += [unvalidated, no_receipt, moved]
        for value in bad:
            with self.subTest(keys=sorted(value)), self.assertRaises(Exception):
                spatial_provisioning(value, site_id='victoria', camera_id='den')
        with self.assertRaisesRegex(ValueError, 'another camera'):
            spatial_provisioning(self.spatial_file(), site_id='victoria', camera_id='kitchen')

    def build(self, experiment_id, served=None, **extra):
        served = self.spatial_file() if served is None else served
        def read(path):
            if path == '/private/spatial.json':
                if isinstance(served, Exception):
                    raise served
                return deepcopy(served)
            raise AssertionError(path)
        with patch('live_detector_app.private_json', side_effect=read):
            runtime = build_runtime(self.store, SimpleNamespace(camera_id='den'), live_config(**extra),
                                    transport_factory=lambda *_: (lambda *a: None), lease=SimpleNamespace(ready=lambda: False),
                                    experiment_id=experiment_id)
        self.runtimes.append(runtime)
        return runtime

    def test_placement_recipes_never_ingest_bundles_and_depth_recipes_do(self):
        for experiment in ('placement-same', 'placement-best'):
            runtime = self.build(experiment, spatial_file='/private/spatial.json')
            self.assertEqual((runtime.bundle_sources, runtime.bundle_clients, runtime.spatial_sources), ({}, {}, {}))
            self.assertEqual(runtime.capabilities()['spatial']['state'], 'not_provisioned')
        runtime = self.build('depth')
        self.assertIsInstance(runtime.bundle_sources['den'], BundleIngestor)
        self.assertIsInstance(runtime.bundle_clients['den'], LocalBundleClient)
        self.assertEqual(runtime.capabilities()['spatial'], {'state': 'not_provisioned', 'reason': 'spatial_not_provisioned'})
        runtime = self.build('depth', spatial_file='/private/spatial.json')
        self.assertIsInstance(runtime.spatial_sources['den'], SpatialConsumer)
        self.assertEqual(runtime.capabilities()['spatial']['localizations']['stereo'], {'available': True, 'reason': None})
        # A per-session re-read that fails leaves localization unavailable only.
        runtime = self.build('depth', PermissionError(), spatial_file='/private/spatial.json')
        self.assertEqual(runtime.capabilities()['spatial'], {'state': 'not_provisioned', 'reason': 'spatial_provisioning_invalid'})
        self.assertIsInstance(runtime.bundle_sources['den'], BundleIngestor)

    def test_live_config_accepts_only_an_absolute_spatial_path(self):
        self.assertEqual(validate_config(live_config(spatial_file='/private/spatial.json'))['spatial_file'], '/private/spatial.json')
        for invalid in ('spatial.json', 7):
            with self.assertRaises(ValueError):
                validate_config(live_config(spatial_file=invalid))

    def test_managed_startup_fails_closed_on_an_invalid_spatial_file(self):
        from live_detector_app import check_spatial_provisioning
        settings, source = SimpleNamespace(site_id='victoria'), SimpleNamespace(camera_id='den')
        self.assertIsNone(check_spatial_provisioning(settings, source, live_config()))
        with patch('live_detector_app.private_json', return_value=self.spatial_file()):
            self.assertEqual(check_spatial_provisioning(settings, source, live_config(spatial_file='/private/spatial.json')),
                             {'stereo': None, 'floor_projection': 'floor_homography_not_provisioned'})
        with patch('live_detector_app.private_json', return_value=self.spatial_file(max_skew_s=.5)):
            with self.assertRaises(ValueError):
                check_spatial_provisioning(settings, source, live_config(spatial_file='/private/spatial.json'))


GRANT = {'id': 'grant', 'a': {'caption_evidence': False}, 'b': {'caption_evidence': True}}
SAME = {'id': 'same', 'a': {}, 'b': {'caption_evidence': False}}


def policy(value):
    return SimpleNamespace(readiness=lambda: {'available': True, 'reason': None, 'historical_caption': value})


class CaptionStartupTests(unittest.TestCase):
    def check(self, enabled, config, factory, binding=('/state/shared-caption.json', 'rev-1')):
        from managed_detector_app import check_caption_startup
        side = binding if isinstance(binding, Exception) else None
        with patch('caption_history.refresh_publisher_binding', return_value=binding, side_effect=side):
            return check_caption_startup([SAME, GRANT], enabled, config, factory)

    def test_a_granting_recipe_fails_closed_without_opt_in_policy_or_source(self):
        with self.assertRaisesRegex(ValueError, 'grant grants captions without provisioned caption history'):
            self.check({'same', 'grant'}, {}, policy('granted'))
        for factory in (policy('not_granted'), SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, 'without household caption egress'):
                self.check({'same', 'grant'}, {'caption_history': True}, factory)
        with self.assertRaisesRegex(ValueError, 'grant grants captions without a provisioned caption source'):
            self.check({'same', 'grant'}, {'caption_history': True}, policy('granted'), binding=ValueError('no refresh'))
        self.assertEqual(self.check({'same', 'grant'}, {'caption_history': True}, policy('granted')),
                         {'source': {'provisioned': True, 'reason': None}, 'granted_recipes': ['grant']})

    def test_withheld_recipes_start_and_report_source_state(self):
        self.assertEqual(self.check({'same'}, {}, policy('not_granted')),
                         {'source': {'provisioned': False, 'reason': 'caption_history_not_provisioned'}, 'granted_recipes': []})
        self.assertEqual(self.check({'same'}, {'caption_history': True}, None, binding=ValueError('no refresh')),
                         {'source': {'provisioned': False, 'reason': 'caption_source_unavailable'}, 'granted_recipes': []})
        # A recipe outside the enabled set is never checked at startup.
        self.assertEqual(self.check({'same'}, {}, None)['granted_recipes'], [])

    def test_managed_runtime_checks_enabled_recipes_and_advertises_caption_readiness(self):
        from managed_detector_app import create_managed_runtime
        catalog = json.loads(Path(__file__).with_name('experiments.json').read_text())
        same = next(r for r in catalog['experiments'] if r['id'] == 'placement-same')
        same['b']['caption_evidence'] = True
        original = Path.read_text
        read = lambda path, *a, **k: json.dumps(catalog) if path.name == 'experiments.json' else original(path, *a, **k)
        hosts = lambda: {'a': Host(), 'b': Host()}
        state = {'value': 'granted'}
        factory = SimpleNamespace(readiness=lambda: {'available': True, 'reason': None, 'historical_caption': state['value']})
        with patch('pathlib.Path.read_text', read):
            with self.assertRaisesRegex(ValueError, 'placement-same grants captions without provisioned caption history'):
                create_managed_runtime(None, None, {}, hosts())
            with patch('caption_history.refresh_publisher_binding', return_value=('/state/shared-caption.json', 'rev-1')):
                runtime = create_managed_runtime(None, None, {'caption_history': True}, hosts(), reasoning_factory=factory)
        caps = runtime.capabilities()
        self.assertEqual(caps['caption'], {'available': True, 'reason': None, 'granted_recipes': ['placement-same']})
        self.assertIn({'id': 'caption_history', 'state': 'available', 'reason': None}, caps['gates'])
        # A later policy change is reported live, not cached from startup.
        state['value'] = 'not_granted'
        self.assertEqual(runtime.capabilities()['caption']['reason'], 'household_caption_egress_not_granted')

    def test_default_managed_runtime_reports_captions_and_spatial_unavailable(self):
        from managed_detector_app import create_managed_runtime
        runtime = create_managed_runtime(None, None, {}, {'a': Host(), 'b': Host()})
        caps = runtime.capabilities()
        self.assertEqual(caps['caption'], {'available': False, 'reason': 'caption_history_not_provisioned', 'granted_recipes': []})
        self.assertEqual(caps['spatial'], {'state': 'not_provisioned', 'reason': 'spatial_not_provisioned'})
        self.assertIn({'id': 'spatial', 'state': 'unavailable', 'reason': 'spatial_not_provisioned'}, caps['gates'])
        spatial = create_managed_runtime(None, None, {}, {'a': Host(), 'b': Host()},
                                         spatial_readiness={'stereo': None, 'floor_projection': 'floor_homography_not_provisioned'})
        self.assertEqual(spatial.capabilities()['spatial']['localizations']['floor_projection'],
                         {'available': False, 'reason': 'floor_homography_not_provisioned'})
        # Placement stays the only live setup: provisioning spatial binds no depth recipe.
        self.assertEqual({r['id'] for r in spatial.catalog if r['status'] != 'blocked'}, {'placement-same'})


class SavedQwenCompositionTests(unittest.TestCase):
    """The managed composition builds saved analysis from the live config keys."""
    from test_managed_pair import ManagedAnalysisTests as _base
    setUp, tearDown, saved_session = _base.setUp, _base.tearDown, _base.saved_session
    settle, analyse = _base.settle, _base.analyse

    def transport(self, config):
        class Transport:
            def __init__(inner):
                inner.sent, inner.lookups = [], []
            def __call__(inner, envelope):
                inner.sent.append(envelope)
                return {'request_id': envelope['request_id'], 'body_sha256': envelope['body_sha256'], 'terminal': True,
                        'state': 'completed', 'engine_epoch': 'epoch1', 'artifact_sha256': 'a' * 64,
                        'response': {'model': 'qwen3-vl-30b', 'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{}'}}]}}
            def lookup(inner, reference):
                inner.lookups.append(reference)
                return {'request_id': reference['request_id'], 'body_sha256': reference['body_sha256'], 'terminal': False,
                        'state': 'not_found', 'engine_epoch': 'epoch1', 'artifact_sha256': 'a' * 64}
            def close(inner):
                pass
        self.made.append(Transport())
        self.configs.append(config)
        return self.made[-1]

    def test_provisioned_keys_reach_jobs_through_the_managed_runtime(self):
        from controller import Controller
        from managed_detector_app import create_managed_runtime
        from test_managed_pair import PROFILE, TRANSPORT
        self.made, self.configs = [], []
        self.runtime = create_managed_runtime(self.store, None, {'qwen_profile': PROFILE, 'qwen_transport': TRANSPORT},
                                              self.clients, transport_factory=self.transport)
        self.controller = Controller(self.store, self.runtime)
        caps = self.runtime.capabilities()
        self.assertEqual((caps['analysis']['available'], caps['analysis']['reason']), (True, None))
        self.assertIn({'id': 'saved_analysis', 'state': 'available', 'reason': None}, caps['gates'])
        self.assertEqual(self.configs[0]['deadline_seconds'], 15)
        job = self.analyse('composed1')
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'completed')
        self.assertEqual([j['id'] for j in self.runtime.list_jobs(self.saved['id'])], [job['id']])
        self.assertTrue(self.made[0].sent)

    def test_absent_keys_leave_saved_analysis_unavailable(self):
        from controller import Controller
        from managed_detector_app import create_managed_runtime
        self.runtime = create_managed_runtime(self.store, None, {}, self.clients)
        self.controller = Controller(self.store, self.runtime)
        self.assertIsNone(self.runtime.analysis)
        caps = self.runtime.capabilities()
        self.assertEqual((caps['analysis']['available'], caps['analysis']['reason']), (False, 'qwen_admission_not_provisioned'))
        job = self.analyse('absent1')
        self.assertEqual((job['state'], job['error']), ('blocked', 'qwen_admission_not_provisioned'))


class QwenConfigTests(unittest.TestCase):
    def test_validate_config_accepts_exactly_the_saved_analysis_schema(self):
        from test_managed_pair import PROFILE, TRANSPORT
        for profile, transport in ((PROFILE, TRANSPORT), ('/private/qwen-profile.json', '/private/qwen-transport.json')):
            value = validate_config(live_config(qwen_profile=profile, qwen_transport=transport))
            self.assertEqual((value['qwen_profile'], value['qwen_transport']), (profile, transport))
        invalid = [dict(qwen_profile=PROFILE), dict(qwen_transport=TRANSPORT),
                   dict(qwen_profile={**PROFILE, 'adapter_revision': 'lab-qwen-cutoff-1'}, qwen_transport=TRANSPORT),
                   dict(qwen_profile={**PROFILE, 'extra': 'x'}, qwen_transport=TRANSPORT),
                   dict(qwen_profile=PROFILE, qwen_transport={**TRANSPORT, 'deadline_seconds': 20}),
                   dict(qwen_profile=PROFILE, qwen_transport={**TRANSPORT, 'endpoint': 'https://x'}),
                   dict(qwen_profile='qwen-profile.json', qwen_transport=TRANSPORT),
                   dict(qwen_profile=PROFILE, qwen_transport=None)]
        for extra in invalid:
            with self.subTest(keys=sorted(extra)), self.assertRaises(ValueError):
                validate_config(live_config(**extra))

    def test_unmanaged_live_app_refuses_saved_analysis_keys(self):
        from live_detector_app import create_live_detector_app
        from test_managed_pair import PROFILE, TRANSPORT
        config = live_config(qwen_profile=PROFILE, qwen_transport=TRANSPORT)
        with patch.dict(os.environ, {'PERCEPTION_LAB_LIVE_CONFIG': '/private/live.json'}), \
                patch('live_detector_app.private_json', return_value=config), \
                patch('capture_app.load_provisioning', side_effect=AssertionError('must refuse first')):
            with self.assertRaisesRegex(ValueError, 'requires the managed controller'):
                create_live_detector_app()


if __name__ == '__main__':
    unittest.main()

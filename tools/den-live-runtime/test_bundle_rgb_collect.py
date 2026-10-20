"""Same factory/Store and authenticated read routes; external I/O is authored.

This module is prepared by the consumer owner. The coordinator runs gates.
"""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / 'stack/services/perception-lab', ROOT / 'stack/services/home-context',
               ROOT / 'tools/shared-home'):
    if str(folder) not in sys.path: sys.path.append(str(folder))

import test_bundle_rgb_input as factory_fixture
import test_lab_assessment_collect as collection_fixture
from app import create_app
from lab_assessment_collect import collect_existing, read_results, read_verified_attempts, main, encoded
from lab_assessment_candidates import BUNDLED_MODE, BUNDLED_START, BUNDLED_TERMINAL, BUNDLED_WINDOW, AUTHORITY
from preview_client import LocalBundleClient


class BundledRgbCollectionTests(unittest.TestCase):
    def make(self, *, refused=False, pose=False, flat_pose=False):
        f = factory_fixture.BundledRgbFactoryTests('runTest')
        self.addCleanup(f.doCleanups)
        f.setUp()
        # configure() applies this fixture attribute after copying POLICY.
        f.max_captures = 2
        if pose:
            rotation = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
            f.calibration['eeprom'] = {'cameraData': [[0, {'extrinsics': {'toCameraSocket': -1}}],
                [1, {'extrinsics': {'toCameraSocket': 0, 'rotationMatrix': deepcopy(rotation),
                    'translation': {'x': -3.75, 'y': 0., 'z': 0.}}}]],
                'stereoRectificationData': {'leftCameraSocket': 1, 'rightCameraSocket': 2,
                    'rectifiedRotationLeft': deepcopy(rotation)}}
            # arm_snapshot requires all 17 ordered COCO slots. Only the visible
            # nose is authored as supported; missing anatomy stays uncertain.
            f.model_result = dict(objects=[{'label': 'person', 'instance_id': 'authored-person'}],
                boxes=[{'label': 'person', 'instance_id': 'authored-person',
                        'x': 260., 'y': 80., 'w': 120., 'h': 260.}],
                pose_instances=[{'layout': 'coco17', 'instance_id': 'authored-person', 'joints': [
                    {'joint_id': i, 'pixel': [320., 150.] if i == 0 else None,
                     'score': .9 if i == 0 else None, 'status': 'supported' if i == 0 else 'uncertain'}
                    for i in range(17)]}])
            if flat_pose:
                # Public overlay primitive for the same single authored nose;
                # all other anatomy remains explicitly unsupported.
                f.model_result.update(keypoints=[{'x':320.,'y':150.,'score':.9,'joint_id':0,'instance_id':'authored-person'}],
                    skeleton_edges=[])
        runtime = f.build_bundled()
        self.assertEqual(f.config['bundled_rgb_input']['max_captures'], 2)
        f.store.append_event(f.session['id'], 'workers.ready', {'receipts': {'b': {
            'state': 'ready', 'session_id': f.session['id'], 'engine_epoch': f.profiles['b']['engine_epoch'],
            'profile': deepcopy(f.profiles['b'])}}, 'fixture_evidence': 'authored_external_lifecycle_double'})
        if refused:
            # A valid, retained NPZ original whose RGB sequence is outside A/B.
            import numpy as np
            with np.load(io.BytesIO(f.bundle), allow_pickle=False) as archive:
                values = {key: archive[key].copy() for key in archive.files}
            metadata = json.loads(values['metadata_json'].tobytes())
            metadata['frames']['rgb']['sequence'] = 20
            values['metadata_json'] = np.frombuffer(json.dumps(metadata).encode(), np.uint8)
            output = io.BytesIO(); np.savez_compressed(output, **values)
            f.bundle = output.getvalue()
        def read(_client, path):
            f.reads.append(path)
            if path == '/frame.npz':
                f.now = factory_fixture.capture.NOW + .59
                return f.bundle, ''
            if path == '/calibration': return f.raw_calibration, ''
            raise AssertionError('unexpected camera route')
        with patch('bundle_rgb_input.clock_context', side_effect=f.clock_context), \
             patch('bundle_rgb_input.sync_directory'), \
             patch.object(LocalBundleClient, '_read', autospec=True, side_effect=read):
            f.consume(runtime, f.anchor_a)
            f.now = factory_fixture.capture.NOW + .55
            f.consume(runtime, f.anchor_b)
        f.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(runtime.runners[f.session['id']].snapshot()['active'].values()):
            time.sleep(.005)
        self.assertFalse(any(runtime.runners[f.session['id']].snapshot()['active'].values()),
                         'authored model did not become terminal within its fixture budget')
        events = f.store.list_events(f.session['id'])
        terminals = [e for e in events if e['kind'] == BUNDLED_TERMINAL]
        closures = [e for e in events if e['kind'] == BUNDLED_WINDOW]
        self.assertEqual(len(terminals), 2, 'fixture must retain both capture terminals before collecting')
        self.assertEqual(len(closures), 1, 'fixture must exhaust its two-capture source window')
        self.assertEqual(closures[0]['payload']['reason'], 'exhausted')
        settings = replace(f.store.settings, token_grants=({'subject': 'authored-candidate-reader',
            'site_id': 'victoria', 'token_sha256': hashlib.sha256(collection_fixture.TOKEN.encode()).hexdigest(),
            'capabilities': ['read']},))
        api = collection_fixture.ActualReadAPI(create_app(settings=settings, runtime=runtime, store=f.store))
        self.f, self.runtime, self.api = f, runtime, api
        self.output = Path(f.directory.name) / 'bundled-collection.jsonl'
        return f

    def collect(self, *, api=None, seconds=2):
        with patch('lab_whole_stack_trial.sync_parent') if sys.platform == 'win32' else nullcontext():
            return collect_existing(api or self.api, session_id=self.f.session['id'], output_file=self.output,
                clock=lambda: self.f.now, seconds=seconds, max_attempts=1, collection_mode=BUNDLED_MODE)

    def rows(self):
        return [json.loads(line) for line in self.output.read_bytes().splitlines()]

    def write(self, rows):
        self.output.write_bytes(b''.join(encoded(row) + b'\n' for row in rows))

    def test_actual_factory_read_routes_preserve_no_pose_attempt_and_all_originals(self):
        f = self.make()
        store_id, runner = f.store.instance_id, self.runtime.runners[f.session['id']]
        with patch('store.Store.__init__', side_effect=AssertionError('second Store')):
            result = self.collect()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['missing_prediction_attempts'], 1)
        self.assertEqual(result['producer_refused_captures'], 1)  # Retained warmup refusal.
        self.assertEqual(result['capture_starts_without_model'], 0)
        self.assertEqual(f.store.instance_id, store_id)
        self.assertIs(self.runtime.runners[f.session['id']], runner)
        self.assertEqual(f.store.get_session(f.session['id'])['state'], 'recording')
        self.assertEqual(f.reads, ['/frame.npz', '/calibration'])
        self.assertTrue(all(method == 'GET' for method, _ in self.api.calls))
        report = read_results(self.output)
        self.assertTrue(report['complete'])
        self.assertEqual(report['missing_source_media_records'], 0)
        self.assertFalse(report['ordinary_lighting_replay'])
        verified = read_verified_attempts(self.output)
        self.assertEqual(verified['binding']['schema'], 'home-lab-assessment-binding-3')
        attempt = verified['attempts'][0]
        self.assertEqual(attempt['schema'], 'home-lab-assessment-attempt-3')
        self.assertEqual(attempt['reason'], 'no_pose_prediction')
        self.assertTrue(all(attempt[k] == value for k, value in AUTHORITY.items()))
        self.assertTrue(all(part['status'] == 'unavailable' for part in attempt['parts'].values()))

    def test_actual_pose_uses_the_selected_npz_depth_and_keeps_unknown_authority(self):
        self.make(pose=True)
        result = self.collect()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['candidate_attempts'], 1)
        verified = read_verified_attempts(self.output)
        candidate = verified['attempts'][0]
        self.assertEqual(candidate['status'], 'candidate', candidate['reason'])
        nose = candidate['instances'][0]['joints'][0]
        self.assertEqual(nose['pixel'], [320., 150.])
        self.assertEqual(nose['status'], 'candidate')
        self.assertAlmostEqual(nose['camera_xyz_m'][2], 1.234)
        self.assertGreaterEqual(nose['depth_support'], 3)
        self.assertIsNone(nose['error_bound_m'])
        self.assertEqual(candidate['media_records']['rgb']['mime_type'], 'image/png')
        self.assertEqual(candidate['original_frame_record'], candidate['media_records']['rgb'])
        self.assertEqual(candidate['depth_clock']['status'], 'unassessed')
        self.assertIsNone(candidate['depth_clock']['exposure_error_bound_s'])
        self.assertIsNone(candidate['person_ref']); self.assertIsNone(candidate['subject_ref'])
        self.assertTrue(all(candidate[k] == value for k, value in AUTHORITY.items()))
        self.assertTrue(all(part['status'] == 'unavailable' for part in candidate['parts'].values()))
        # Exact file verification also reproduces the nominal sample. An edited
        # XYZ value cannot survive merely by updating the aggregate counts.
        rows = self.rows()
        stored = next(row['value']['candidate'] for row in rows if row['kind'] == 'attempt')
        stored['instances'][0]['joints'][0]['camera_xyz_m'][2] += .01
        self.write(rows)
        with self.assertRaises(ValueError): read_results(self.output)

    def test_proved_source_refusal_is_complete_without_manufacturing_model_output(self):
        f = self.make(refused=True)
        result = self.collect()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['producer_refused_captures'], 2)
        self.assertEqual(result['attempts'], 0)
        self.assertEqual(result['capture_starts_without_model'], 0)
        self.assertFalse(any(e['kind'] == 'perception.output' for e in f.store.list_events(f.session['id'])))
        report = read_results(self.output)
        self.assertEqual(report['producer_refused_captures'], 2)
        self.assertEqual(report['missing_source_media_records'], 0)
        rows = self.rows()
        rows[-1]['value']['producer_refused_captures'] = 0
        self.write(rows)
        with self.assertRaises(ValueError): read_results(self.output)

    def test_complete_refusal_cannot_omit_its_raw_original(self):
        self.make(refused=True)
        self.assertTrue(self.collect()['complete'])
        rows = self.rows()
        raw = next(row for row in rows if row['kind'] == 'media'
            and row['value']['record']['mime_type'] == 'application/x-npz')
        rows.remove(raw); self.write(rows)
        with self.assertRaises(ValueError): read_results(self.output)

    def test_missing_terminal_stays_incomplete_and_retains_source_start(self):
        self.make(refused=True)
        original = self.api
        class MissingTerminal:
            def __call__(_, method, suffix, body=None):
                value = original(method, suffix, body)
                if '/events?' in suffix:
                    value['items'] = [e for e in value['items'] if e['kind'] != BUNDLED_TERMINAL]
                return value
            read_media = original.read_media
        result = self.collect(api=MissingTerminal(), seconds=.3)
        self.assertFalse(result['complete'])
        self.assertEqual(result['missing_capture_terminals'], 2)
        self.assertEqual(result['producer_refused_captures'], 0)
        self.assertTrue(any(row['kind'] == 'assessment_original'
            and row['value']['event']['kind'] == BUNDLED_START for row in self.rows()))
        self.assertFalse(read_results(self.output)['complete'])

    def test_attempt_budget_without_source_closure_cannot_report_complete(self):
        self.make()
        original = self.api
        class MissingClosure:
            def __call__(_, method, suffix, body=None):
                value = original(method, suffix, body)
                if '/events?' in suffix:
                    value['items'] = [e for e in value['items'] if e['kind'] != BUNDLED_WINDOW]
                return value
            read_media = original.read_media
        result = self.collect(api=MissingClosure())
        self.assertEqual(result['attempts'], 1)
        self.assertFalse(result['complete'])
        self.assertFalse(read_results(self.output)['complete'])

    def test_prepared_entry_does_not_construct_client_or_store(self):
        with patch('lab_assessment_collect.FixedLabReadClient.__init__', side_effect=AssertionError('client opened')), \
             patch('store.Store.__init__', side_effect=AssertionError('Store opened')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(main(['--collection-mode', BUNDLED_MODE]), 0)
        prepared = json.loads(output.getvalue())
        self.assertEqual(prepared['collection_mode'], BUNDLED_MODE)
        self.assertEqual(prepared['network_requests'], 0)


if __name__ == '__main__': unittest.main()

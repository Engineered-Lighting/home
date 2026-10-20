"""Whole-stack projection uses original Lab records without source/model I/O."""
from copy import deepcopy
import json
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace
import unittest

from lab_live_stack_context import project_lab_live_stack, FAMILIES, MAX_EVENTS
import qwen_scene
import vjepa_scene


class LabLiveStackContextTests(unittest.TestCase):
    def setUp(self):
        self.session = {'id': 'authored-session', 'site_id': 'victoria', 'camera_id': 'den'}
        self.common = {**self.session, 'session_id': self.session['id'],
            'observed_at': 100.0, 'available_at': 100.5, 'valid_until': 104.0,
            'evidence_roots': ['camera:original-epoch:frame-7'], 'capture_epoch': 'original-epoch',
            'frame_id': 'original-epoch-0-7', 'arm': 'b', 'generation': 1}
        self.common.pop('id')

    def event(self, kind, payload, identity=1, created=100.5):
        return {'id': identity, 'session_id': self.session['id'], 'kind': kind,
                'created_at': created, 'payload': deepcopy(payload)}

    def perception(self, **changes):
        payload = {**self.common, 'status': 'completed', 'request_id': 'original-request',
            'model_version': 'detector-pose-v1', 'artifact_sha256': 'a' * 64,
            'engine_epoch': 'original-model-epoch', 'media_ids': ['original-rgb'],
            'result': {'schema_version': 1, 'coordinate_space': 'source_pixels',
                'frame_id': self.common['frame_id'], 'objects': [{'label': 'person',
                'confidence': .9, 'instance_id': 'one-person'}],
                'pose_instances': [{'layout': 'coco17', 'instance_id': 'one-person',
                    'joints': [{'joint_id': 0, 'pixel': [100, 50], 'score': .8, 'status': 'supported'}]}]},
            'arm_snapshot': {'frame': {'id': self.common['frame_id'], 'width': 640, 'height': 360}}}
        payload.update(changes)
        return payload

    def ha(self):
        payload = {key: value for key, value in self.common.items() if key not in ('arm', 'generation')}
        payload.update(snapshot_key='ha:original-source:snapshot-1', snapshot_digest='b' * 64,
            evidence_roots=['ha:original-source:snapshot-1'], ha={
            'derived.computer_active': {'state': 'on', 'status': 'known'},
            'derived.computer_input_recency': {'state': 'under_15s', 'status': 'known'},
            'sensor.window_title': {'state': 'PRIVATE_WINDOW_MARKER'},
            'sensor.logged_user': {'state': 'PRIVATE_PERSON_MARKER'}})
        return payload

    def qwen(self, *, external=False, observed=100):
        facts = {key: values[-1] for key, values in qwen_scene.FIELDS.items()}
        facts.update(people_visible='one', subject_posture='sitting', held_object='none',
                     activity_hypothesis='computer_work', visible_basis=['seated_posture'], change_vs_prior='no_prior')
        return qwen_scene.first_receipt(qwen_scene.build_observation(
            site_id='victoria', camera_id='den',
            session_id='original-daily-qwen-session' if external else self.session['id'],
            request_id='c' * 64,
            frames=[{'media_id': 'media-1', 'frame_id': 'frame-1', 'observed_at': observed - 1},
                    {'media_id': 'media-2', 'frame_id': 'frame-2', 'observed_at': observed,
                     'capture_epoch': 'original-epoch'}],
            generated_at=observed + .25, prompt_version=qwen_scene.PROMPT_VERSION,
            context={}, body_sha256='d' * 64, engine_epoch='original-qwen-epoch',
            artifact_sha256='a' * 64, content=json.dumps(facts), finish_reason='stop', prior_supplied=False),
            observed + .5)

    def temporal(self):
        request = {'request_id': 'e' * 64, 'profile': {
            'engine_epoch': 'original-temporal-epoch', 'artifact_sha256': 'f' * 64},
            'single_person': True, 'previous_request_id': None, 'person_box': [1, 1, 100, 100]}
        result = {'version': 1, 'schema': vjepa_scene.RESULT_SCHEMA,
            'request_id': request['request_id'], 'engine_epoch': 'original-temporal-epoch',
            'artifact_sha256': 'f' * 64,
            'posture': {key: 1.0 if key == 'sitting' else 0.0 for key in vjepa_scene.POSTURES},
            'reading': .1, 'zero_shot': {'computer_work': .8, 'phone_use': .1, 'reading': .1},
            'zero_shot_status': 'supplied', 'motion': .01, 'scene_drift': None,
            'drift_reference': None, 'timings_ms': {key: 1 for key in vjepa_scene.TIMINGS}}
        return vjepa_scene.first_receipt(vjepa_scene.build_observation(
            site_id='victoria', camera_id='den', session_id=self.session['id'], request=request,
            frames=[{'media_id': 'clip-' + str(i), 'frame_id': 'frame-' + str(i),
                     'capture_epoch': 'original-epoch', 'observed_at': 96.5 + i * .5} for i in range(8)],
            box_input={'event_ids': [1], 'frames': 8}, generated_at=100.25, result=result,
            body_sha256='a' * 64, response_sha256='b' * 64), 100.5)

    def project(self, events=(), **options):
        return project_lab_live_stack(self.session, events=list(events), cutoff=options.pop('cutoff', 101),
                                      generation=1, **options)

    def test_missing_families_are_visible_and_never_create_authority(self):
        result = self.project(configured={'temporal_encoder': False, 'devices': True})
        self.assertEqual(set(result['families']), set(FAMILIES))
        self.assertEqual(result['families']['temporal_encoder']['state'], 'not_configured')
        self.assertEqual(result['families']['devices']['state'], 'unavailable')
        self.assertIsNone(result['operational_geometry'])
        for key in ('identity_authority', 'engagement_authority', 'beam_authority'):
            self.assertIs(result[key], False)

    def test_real_original_output_schema_projects_image_coordinates_and_original_lineage(self):
        original = self.event('perception.output', self.perception())
        before = deepcopy(original)
        result = self.project([original])['families']['detector_pose']
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['data']['pose_instances'][0]['joints'][0]['pixel'], [100, 50])
        self.assertEqual(result['data']['coordinate_space'], 'source_pixels')
        self.assertEqual(result['lineage']['evidence_roots'], ['camera:original-epoch:frame-7'])
        self.assertEqual(result['lineage']['engine_epoch'], 'original-model-epoch')
        self.assertEqual(result['lineage']['media_ids'], ['original-rgb'])
        self.assertEqual(original, before)

    def test_ha_closed_facts_reuse_existing_projection_and_drop_content_entities(self):
        result = self.project([self.event('ha.observation', self.ha())])
        device = result['families']['devices']
        self.assertEqual(device['data']['facts'], {'computer_active': 'on', 'computer_input_recency': 'under_15s'})
        encoded = json.dumps(result)
        for marker in ('PRIVATE_WINDOW_MARKER', 'PRIVATE_PERSON_MARKER', 'sensor.window_title', 'sensor.logged_user'):
            self.assertNotIn(marker, encoded)
        self.assertEqual(device['lineage']['snapshot_digest'], 'b' * 64)

    def test_expiry_is_exclusive_and_repeat_projection_never_refreshes_original_times(self):
        event = self.event('perception.output', self.perception())
        current = self.project([event], cutoff=103.999)['families']['detector_pose']
        expired = self.project([event], cutoff=104)['families']['detector_pose']
        self.assertEqual(current['state'], 'current')
        self.assertEqual(expired['state'], 'stale')
        self.assertEqual(current['lineage'], expired['lineage'])
        self.assertEqual(expired['lineage']['available_at'], 100.5)

    def test_future_availability_and_other_generation_do_not_become_current(self):
        future = self.event('perception.output', self.perception(available_at=102), identity=1)
        old = self.event('perception.output', self.perception(generation=2), identity=2)
        result = self.project([future, old])['families']['detector_pose']
        self.assertIsNone(result['data'])
        self.assertEqual(result['state'], 'unavailable')

    def test_later_refused_attempt_keeps_prior_evidence_lease_and_reports_refusal_separately(self):
        good = self.event('perception.output', self.perception(), identity=1)
        refused = self.event('perception.delivery', {**self.common, 'status': 'rejected',
            'request_id': 'next-request', 'reason': 'PRIVATE_FAILURE_MARKER', 'available_at': 100.8}, identity=2)
        result = self.project([good, refused])['families']['detector_pose']
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['lineage']['request_id'], 'original-request')
        self.assertEqual(result['latest_attempt']['status'], 'rejected')
        self.assertNotIn('PRIVATE_FAILURE_MARKER', json.dumps(result))
        result = self.project([good, refused], cutoff=104)['families']['detector_pose']
        self.assertEqual(result['state'], 'stale')

    def test_external_qwen_preserves_original_daily_session_and_correlation(self):
        result = self.project(qwen_receipts=[self.qwen(external=True)])['families']['qwen_scene']
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['lineage']['session_id'], 'original-daily-qwen-session')
        self.assertEqual(result['lineage']['session_relation'], 'external_same_camera')
        self.assertFalse(result['data']['independent_confirmation'])
        self.assertEqual(result['data']['latest']['facts']['activity_hypothesis'], 'computer_work')

    def test_new_invalid_qwen_head_does_not_fall_back_to_old_valid_interpretation(self):
        good = self.qwen()
        bad = self.qwen(observed=100.1)
        bad.update(status='invalid', facts=None)
        result = self.project([self.event('qwen.observation', good, 1),
                               self.event('qwen.observation', bad, 2)])['families']['qwen_scene']
        self.assertEqual(result['state'], 'invalid')
        self.assertIsNone(result['data'])

    def test_vjepa_uses_existing_bucket_projection_not_embeddings_or_raw_probabilities(self):
        payload = self.temporal()
        payload['embedding'] = ['PRIVATE_EMBEDDING_MARKER']
        result = self.project([self.event('vjepa.observation', payload)])['families']['temporal_encoder']
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['data']['latest']['facts']['posture']['top'], 'sitting')
        self.assertEqual(result['data']['calibration'], 'unvalidated')
        encoded = json.dumps(result)
        self.assertNotIn('PRIVATE_EMBEDDING_MARKER', encoded)
        self.assertNotIn('zero_shot_status', encoded)

    def test_jev_is_interpretation_and_raw_diagnostics_or_instructions_are_excluded(self):
        payload = {**self.common, 'status': 'ready', 'request_id': 'jev-original-request',
            'judgment': {'activity': 'computer_work', 'probability': .8, 'confidence': .6,
                'diagnostics': {'request': 'PRIVATE_PROMPT_MARKER'}, 'question': 'PRIVATE_QUESTION_MARKER'}}
        result = self.project([self.event('reasoning.output', payload)])['families']['reasoning']
        self.assertEqual(result['data']['activity'], 'computer_work')
        self.assertFalse(result['data']['engagement_authority'])
        self.assertEqual(result['data']['confidence_kind'], 'provider_reported_uncalibrated')
        self.assertNotIn('PRIVATE_', json.dumps(result))

    def actual_reasoning_output(self):
        # Exercise the actual incumbent producer method, not a hand-written
        # projection-shaped approximation. No pool, provider or model is started.
        from live_reasoning import LiveReasoning
        from reasoning import ACTIVITIES
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE events(id INTEGER, session_id TEXT, kind TEXT, payload TEXT)')
        self.session['started_at'] = 90.25
        runner = LiveReasoning.__new__(LiveReasoning)
        runner.session, runner.sid, runner.policy = deepcopy(self.session), self.session['id'], {}
        runner.store = SimpleNamespace(db=connection)
        request = {'request_id': 'actual-jev-request', 'site_id': 'victoria', 'arm': 'b',
            'generation': 1, 'cutoff': 100.5, 'deadline': 110,
            'records': [{'source_kind': 'perception.output', 'objects': [{'label': 'person'}],
                         'observed_at': 100, 'valid_until': 104}]}
        response = {'status': 'ready', 'judgment': {'activity': 'computer_work', 'confidence': .6,
            'probabilities': {key: 1.0 if key == 'computer_work' else 0.0 for key in ACTIVITIES}},
            'provenance': {}}
        return runner._output(request, response, 100.75)

    def test_actual_jev_producer_relative_clocks_reach_projection_with_saved_session_origin(self):
        payload = self.actual_reasoning_output()
        original = deepcopy(payload)
        self.assertNotIn('observed_at', payload)
        self.assertNotIn('available_at', payload)
        result = self.project([self.event('reasoning.output', payload, created=100.75)])['families']['reasoning']
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['data']['activity'], 'computer_work')
        self.assertEqual(result['lineage']['observed_at'], 100)
        self.assertEqual(result['lineage']['available_at'], 100.75)
        self.assertEqual(result['lineage']['valid_until'], 104)
        self.assertEqual(result['lineage']['original_relative_clock'], {
            'source_end_s': 9.75, 'available_at_s': 10.5, 'time_basis': 'session_relative_seconds',
            'session_started_at': 90.25, 'mapping': 'lab_session_start_plus_original_offset'})
        self.assertEqual(payload, original)

    def test_late_event_receipt_cannot_freshen_actual_jev_source_clocks(self):
        payload = self.actual_reasoning_output()
        result = self.project([self.event('reasoning.output', payload, created=200)], cutoff=201)['families']['reasoning']
        self.assertEqual(result['state'], 'stale')
        self.assertEqual(result['lineage']['observed_at'], 100)
        self.assertEqual(result['lineage']['available_at'], 100.75)
        self.assertEqual(result['lineage']['event_first_received_at'], 200)

    def test_actual_jev_relative_clock_requires_original_session_start_and_causal_offsets(self):
        payload = self.actual_reasoning_output()
        for changes in ({'started_at': None}, {'started_at': '90.25'}):
            with self.subTest(changes=changes):
                session = {**self.session, **changes}
                result = project_lab_live_stack(session, events=[self.event('reasoning.output', payload)],
                    cutoff=101, generation=1)['families']['reasoning']
                self.assertEqual(result['state'], 'unavailable')
                self.assertIsNone(result['data'])
        for changes in ({'available_at_s': 1}, {'source_end_s': -1}, {'source_end_s': True}):
            with self.subTest(changes=changes):
                result = self.project([self.event('reasoning.output', {**payload, **changes})])['families']['reasoning']
                self.assertEqual(result['state'], 'unavailable')
                self.assertIsNone(result['data'])

    def test_depth_and_calibration_availability_do_not_qualify_geometry_or_exposure_time(self):
        metadata = {'site_id': 'victoria', 'camera_id': 'den', 'stream': 'rgbd_bundle',
            'capture_epoch': 'original-epoch', 'clock_mapping_status': 'unverified',
            'calibration_id': 'a' * 64, 'calibration_content_id': 'b' * 64,
            'calibration': {'private': 'PRIVATE_CALIBRATION_MARKER'}, 'path': 'PRIVATE_PATH_MARKER',
            'depth_alignment': 'rectified_left', 'rgb_aligned': False,
            'source_metadata': {'frames': {'depth': {'sequence': 7, 'device_timestamp_seconds': 12.5,
                                                   'host_monotonic_timestamp_seconds': 15.5}}}}
        item = {'id': 'original-depth', 'session_id': self.session['id'], 'created_at': 100.5,
                'sha256': 'c' * 64, 'metadata': metadata}
        result = self.project(media=[item])
        depth = result['families']['depth']
        self.assertEqual(depth['state'], 'available')
        self.assertEqual(depth['data']['geometric_validity'], 'unvalidated')
        self.assertEqual(depth['lineage']['original_source_frames']['depth']['device_timestamp_seconds'], 12.5)
        self.assertNotIn('observed_at', depth['lineage'])
        self.assertEqual(result['families']['calibration']['data']['calibration_content_id'], 'b' * 64)
        self.assertNotIn('PRIVATE_', json.dumps(result))
        self.assertIsNone(result['operational_geometry'])

    def test_capture_retains_original_clock_as_decimal_string_and_late_old_frame_cannot_replace_new(self):
        meta = {**self.common, 'stream': 'rgb', 'clock_mapping_status': 'verified',
            'source_clock': 'depthai_device_monotonic', 'source_timestamp_seconds': 12.5,
            'source_timing_receipt': {'observed_at_unix_ns': 1792173344000000120,
                                     'frame_device_ns': 12500000000}}
        latest = {'id': 'new-rgb', 'created_at': 100.5, 'metadata': meta}
        old = {'id': 'late-old-rgb', 'created_at': 100.8, 'metadata': {**meta, 'observed_at': 99.5}}
        result = self.project(media=[latest, old])['families']['capture']
        self.assertEqual(result['lineage']['media_id'], 'new-rgb')
        self.assertEqual(result['lineage']['original_capture_clock']['observed_at_unix_ns'], '1792173344000000120')

    def test_original_nanoseconds_remain_exact_after_actual_node_json_parse(self):
        meta = {**self.common, 'stream': 'rgb', 'clock_mapping_status': 'verified',
            'source_timing_receipt': {'observed_at_unix_ns': 1792173344000000120,
                                     'frame_device_ns': 9007199254740993}}
        result = self.project(media=[{'id': 'browser-clock-rgb', 'created_at': 100.5, 'metadata': meta}])
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required to verify the actual browser-number boundary')
        script = r"""
let body = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => body += chunk);
process.stdin.on('end', () => {
  const clock = JSON.parse(body).families.capture.lineage.original_capture_clock;
  if (typeof clock.observed_at_unix_ns !== 'string' ||
      typeof clock.frame_device_ns !== 'string' ||
      clock.observed_at_unix_ns !== '1792173344000000120' ||
      clock.frame_device_ns !== '9007199254740993' ||
      BigInt(clock.observed_at_unix_ns) !== 1792173344000000120n ||
      BigInt(clock.frame_device_ns) !== 9007199254740993n) process.exit(1);
  process.stdout.write(JSON.stringify(clock));
});
"""
        finished = subprocess.run([node, '-e', script], input=json.dumps(result), text=True,
                                  capture_output=True, timeout=10, check=True)
        parsed = json.loads(finished.stdout)
        self.assertEqual(parsed, {'observed_at_unix_ns': '1792173344000000120',
                                  'frame_device_ns': '9007199254740993'})

    def test_original_nanosecond_fields_require_nonnegative_integers(self):
        for key in ('observed_at_unix_ns', 'frame_device_ns'):
            for bad in (1792173344000000120.0, 1.0, True, -1, '1792173344000000120'):
                with self.subTest(key=key, bad=bad):
                    meta = {**self.common, 'stream': 'rgb', 'clock_mapping_status': 'verified',
                            'source_timing_receipt': {key: bad}}
                    with self.assertRaisesRegex(ValueError, 'original_capture_clock_integer_required'):
                        self.project(media=[{'id': 'bad-clock-rgb', 'created_at': 100.5, 'metadata': meta}])

    def test_scope_changed_identity_and_bounds_refuse(self):
        wrong = self.event('ha.observation', {**self.ha(), 'site_id': 'echo'})
        with self.assertRaisesRegex(ValueError, 'source_scope_mismatch'):
            self.project([wrong])
        event = self.event('ha.observation', self.ha())
        with self.assertRaisesRegex(ValueError, 'identity_conflict'):
            self.project([event, {**event, 'created_at': 100.6}])
        with self.assertRaisesRegex(ValueError, 'bounded_bound_lab_stack_required'):
            self.project([event] * (MAX_EVENTS + 1))
        external = self.qwen(external=True)
        external['camera_id'] = 'other-camera'
        with self.assertRaisesRegex(ValueError, 'same_camera_qwen_receipt_required'):
            self.project(qwen_receipts=[external])


if __name__ == '__main__':
    unittest.main()

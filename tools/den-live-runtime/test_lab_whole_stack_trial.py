"""Bounded whole-pipeline oracle using only authored HTTP/clock doubles."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_path = Path(__file__).with_name('lab_whole_stack_trial.py')
_spec = importlib.util.spec_from_file_location('lab_whole_stack_trial', _path)
trial = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = trial
_spec.loader.exec_module(trial)

REQUEST = '6f25d210-a0c7-494e-bcda-4aa9529ca32c'
PROFILE = {'profile_id': 'yolo26s-rtmpose-cpu-v1', 'model_version': 'detector-pose-v1',
           'artifact_sha256': 'c' * 64, 'engine_epoch': 'la-live-authored'}


class Clock:
    now = 100.0

    def sleep(self, seconds):
        self.now += seconds


class Lab:
    """Actual public-route shapes; no completed facts injected into a journal."""
    def __init__(self):
        self.clock = Clock()
        self.calls, self.events, self.retained = [], [], []
        self.session = None
        self.stop_at = None
        self.mutate = lambda event: event
        self.drop_jev_output = False
        self.create_timeout = False
        self.active = False
        self.budget_changed = False
        self.cleanup_uncertain = False

    def append(self, kind, payload):
        event = {'id': len(self.events) + 1, 'session_id': 'actual-shaped-session',
                 'kind': kind, 'created_at': self.clock.now, 'payload': payload}
        event = self.mutate(event)
        if event is not None:
            self.events.append(event)

    def generate(self):
        if self.session['state'] != 'recording':
            return
        n = self.clock.now
        if not self.events:
            self.append('experiment.activated', {'generation': 1, 'profiles': {'b': PROFILE}})
            self.append('workers.ready', {'request_id': 'pair-1'})
            self.append('perception.output', {'arm': 'a', 'generation': 1,
                'status': 'failed', 'reason': 'cuda_allocation_failed'})
        self.append('perception.output', {'arm': 'b', 'generation': 1,
            'frame_id': f'capture-0-{n}', 'capture_epoch': 'capture', 'request_id': f'detector-{n}',
            'artifact_sha256': PROFILE['artifact_sha256'], 'engine_epoch': PROFILE['engine_epoch'],
            'model_version': PROFILE['model_version'], 'media_ids': [f'rgb-{n}'],
            'observed_at': n - .2, 'available_at': n - .1, 'valid_until': n + 1,
            'status': 'completed', 'current': True, 'result': {
                'frame_id': f'capture-0-{n}', 'boxes': [{'label': 'person', 'confidence': .9}],
                'pose_instances': [{'joints': [{'status': 'supported', 'index': 0}]}],
                'processing': {'shared_source_request': f'detector-{n}',
                    'detector': {'artifact_sha256': trial.DETECTOR_SHA},
                    'pose': {'artifact_sha256': trial.POSE_SHA, 'model_invoked': True, 'crop_count': 1}}}})
        self.append('ha.observation', {'site_id': 'victoria', 'camera_id': 'den',
            'quality': {'source_id': 'victoria-ha-lab'},
            'ha': {k: {'state': v, 'status': 'known'} for k, v in {
                'sensor.formd_t1_sessionstate': 'Unlocked', 'sensor.formd_t1_lastactive': 'original-time',
                'derived.computer_active': 'on', 'derived.computer_input_recency': 'under_15s'}.items()},
            'observed_at': n - .3, 'available_at': n - .15, 'valid_until': n + 1,
            'snapshot_key': f'ha:pc:{n}', 'snapshot_digest': 'a' * 64, 'evidence_roots': [f'ha:pc:{n}']})
        pc_id = str(self.events[-1]['id'])
        # The most recent HA row is another source, as on the actual installation.
        self.append('ha.observation', {'quality': {'source_id': 'la-owner-presence'},
            'ha': {'person.owner': {'state': 'home', 'status': 'known'}},
            'observed_at': n - .2, 'available_at': n - .1, 'valid_until': n + 1})
        if n == 102:
            self.evidence_ids = [pc_id]
            self.packet = {'model': trial.JEV_VERSION, 'state': {'schema': trial.JEV_WIRE,
                'devices': {'pc_input': 'under_15s', 'pc_active': 'on'}},
                'questions': {'activity': {'type': 'choice'}}}
            self.append('reasoning.request', {'arm': 'b', 'generation': 1, 'request_id': 'jev-1',
                'available_at': n, 'status': 'admitted_before_transport', 'request': self.packet,
                'packet_sha256': trial.sha(self.packet), 'evidence_ids': self.evidence_ids,
                'wire_version': trial.JEV_WIRE})
        if n == 103:
            self.append('reasoning.delivery', {'arm': 'b', 'generation': 1, 'request_id': 'jev-1',
                'state': 'completed', 'status': 'abstain', 'current': True, 'available_at': n})
            if not self.drop_jev_output:
                self.append('reasoning.output', {'site_id': 'victoria', 'arm': 'b', 'generation': 1,
                    'request_id': 'jev-1', 'mode': 'live', 'run_id': 'jevB',
                    'status': 'abstain', 'provider_status': 'abstain', 'fresh_on_arrival': True,
                    'source_end_s': 2.5, 'available_at_s': 3, 'valid_until': 104,
                    'judgment': {'provider': 'JEV', 'model_version': trial.JEV_VERSION,
                                 'activity': 'insufficient_evidence'},
                    'provenance': {'response_version': trial.JEV_VERSION, 'request_id': 'jev-1',
                        'session_id': self.session['id'], 'arm': 'b', 'generation': 1,
                        'input_digest': trial.sha(self.packet), 'evidence_ids': self.evidence_ids,
                        'observed_at': 102, 'wire_version': trial.JEV_WIRE}})

    def retain(self, kind, value):
        self.retained.append((kind, deepcopy(value)))

    def __call__(self, method, suffix, body=None):
        self.calls.append((method, suffix, deepcopy(body)))
        if suffix == '/capabilities':
            return {'site_id': 'victoria', 'fixture': False, 'recording_allowed': True,
                'worker_mode': 'on_demand_bounded', 'storage': {'max_session_seconds': 300},
                'experiments': [{'id': 'placement-best', 'status': 'ready'}],
                'reasoning': {'available': True, 'mode': 'explicit_bounded_session',
                    'session_seconds': 300, 'max_session_calls': 600,
                    'max_calls_per_minute': 121 if self.budget_changed else 120}}
        if suffix == '/sessions' and method == 'GET':
            if self.active:
                return {'items': [{'state': 'indeterminate'}]}
            return {'items': [deepcopy(self.session)] if self.session else []}
        if suffix == '/sessions' and method == 'POST':
            assert self.retained[0][0] == 'plan', 'identity must be durable before create'
            assert self.retained[0][1]['request'] == body
            self.session = {'id': 'actual-shaped-session', 'site_id': 'victoria', 'generation': 0,
                'camera_id': 'den', 'experiment_id': 'placement-best', 'request_id': body['request_id'],
                'state': 'recording', 'started_at': self.clock.now, 'reasoning_enabled': True}
            if self.create_timeout:
                raise TimeoutError('Unknown create response')
            return deepcopy(self.session)
        if suffix.endswith('/stop'):
            self.stop_at = self.clock.now
            self.session['state'] = 'stopped'
            self.append('workers.cleanup', {'request_id': 'pair-1', 'state': 'stopped',
                'local_drain': 'pending' if self.cleanup_uncertain else 'drained',
                'workers': {'a': {'state': 'failed'}, 'b': {'state': 'stopped'}}})
            return deepcopy(self.session)
        if suffix.endswith('/live'):
            self.generate()
            return {'session': deepcopy(self.session), 'server_time': self.clock.now,
                    'site_id': 'victoria', 'session_id': self.session['id']}
        if '/events?' in suffix:
            after = int(suffix.split('after_id=')[1].split('&')[0])
            rows = [e for e in self.events if e['id'] > after][:100]
            return {'items': deepcopy(rows), 'after_id': rows[-1]['id'] if rows else after}
        if '/descriptor?' in suffix:
            mid = suffix.split('/media/')[1].split('/')[0]
            n = float(mid[4:])
            return {'id': mid, 'media_id': mid, 'session_id': self.session['id'], 'generation': 0,
                'site_id': 'victoria', 'mime_type': 'image/jpeg', 'sha256': 'e' * 64,
                'metadata': {'frame_id': f'capture-0-{n}', 'capture_epoch': 'capture', 'stream': 'rgb',
                    'site_id': 'victoria', 'camera_id': 'den', 'observed_at': n - .2,
                    'available_at': n - .15, 'valid_until': n + .5, 'clock_mapping_status': 'verified',
                    'sequence': 123, 'timing_generation': 0, 'source_timing_receipt': {
                        'observed_at_unix_ns': 1792208115779811123, 'session': 'capture',
                        'sequence': 123, 'timing_generation': 0, 'jpeg_sha256': 'e' * 64}}}
        if suffix.endswith('/graph'):
            return {'session_id': self.session['id'], 'site_id': 'victoria',
                'session': deepcopy(self.session), 'horizon_event_id': len(self.events),
                'sources': [{'id': 'qwen_live', 'status': 'not_provisioned', 'provisioned': False},
                            {'id': 'temporal', 'status': 'not_in_baseline', 'provisioned': True}]}
        raise AssertionError((method, suffix))


class WholeStackTrialTests(unittest.TestCase):
    def run_lab(self, lab=None, retain=None):
        lab = lab or Lab()
        result = trial.run_trial(lab, trial.Plan(REQUEST), retain or lab.retain,
                                 monotonic=lambda: lab.clock.now, sleep=lab.clock.sleep)
        return lab, result

    def test_la_pose_pc_and_actual_abstaining_jev_pass_full_30_seconds_despite_a_failure(self):
        lab, result = self.run_lab()
        self.assertTrue(result['passed'], result)
        self.assertEqual(lab.stop_at, 130)
        self.assertEqual((result['model_generation'], result['media_generation']), (1, 0))
        self.assertTrue(result['event_window']['complete'])
        self.assertTrue(result['cleanup']['confirmed'])
        self.assertEqual(result['jev'][0]['activity'], 'insufficient_evidence')
        self.assertTrue(result['useful_current_jev'])
        self.assertEqual(result['jev'][0]['observed_at'], 102.5)
        self.assertEqual(result['jev'][0]['pc_context']['pc_input'], 'under_15s')
        self.assertEqual([c[1] for c in lab.calls if c[0] == 'POST'],
                         ['/sessions', '/sessions/actual-shaped-session/stop'])
        self.assertEqual(len([c for c in lab.calls if '/media/' in c[1]]), 1)
        self.assertTrue(all('/media/' not in c[1] or '/descriptor?' in c[1] for c in lab.calls))
        self.assertEqual(result['representative_capture']['source_observed_at_unix_ns'], 1792208115779811123)
        self.assertEqual(result['family_statuses'][0]['status'], 'not_provisioned')

    def test_arm_a_only_or_wrong_b_model_never_passes(self):
        for change in ('a_only', 'wrong_model'):
            with self.subTest(change=change):
                lab = Lab()
                def mutate(e):
                    if e['kind'] == 'perception.output' and e['payload'].get('arm') == 'b':
                        e['payload']['arm' if change == 'a_only' else 'artifact_sha256'] = 'a' if change == 'a_only' else 'f' * 64
                    return e
                lab.mutate = mutate
                _, result = self.run_lab(lab)
                self.assertFalse(result['passed'])
                self.assertEqual(lab.stop_at, 130)

    def test_expired_pc_head_cannot_pass_and_other_ha_source_does_not_replace_pc(self):
        lab = Lab()
        def mutate(e):
            if e['kind'] == 'ha.observation' and e['payload']['quality']['source_id'] == 'victoria-ha-lab':
                e['payload']['valid_until'] = lab.clock.now
            return e
        lab.mutate = mutate
        _, result = self.run_lab(lab)
        self.assertFalse(result['passed'])
        self.assertTrue(result['pc_facts'])
        self.assertTrue(all(not p['fresh'] for p in result['pc_facts']))

    def test_request_and_delivery_without_original_jev_output_do_not_prove_model(self):
        lab = Lab()
        lab.drop_jev_output = True
        _, result = self.run_lab(lab)
        self.assertFalse(result['passed'])
        self.assertEqual(result['jev'], [])

    def test_wrong_jev_packet_response_version_or_provider_refused(self):
        for field in ('hash', 'model', 'request', 'generation', 'code_gate'):
            with self.subTest(field=field):
                lab = Lab()
                def mutate(e):
                    if e['kind'] == 'reasoning.output':
                        p = e['payload']
                        if field == 'hash': p['provenance']['input_digest'] = 'f' * 64
                        if field == 'model': p['judgment']['model_version'] = 'unknown-model'
                        if field == 'request': p['request_id'] = 'other-request'
                        if field == 'generation': p['generation'] = 2
                        if field == 'code_gate': p['judgment']['provider'] = 'code_gate'
                    return e
                lab.mutate = mutate
                _, result = self.run_lab(lab)
                self.assertFalse(result['passed'])
                self.assertEqual(result['jev'], [])

    def test_pc_snapshot_must_reach_the_actual_jev_request_not_just_exist_in_window(self):
        lab = Lab()
        def mutate(e):
            if e['kind'] == 'reasoning.request':
                e['payload']['request']['state']['devices']['pc_input'] = 'unknown'
                e['payload']['packet_sha256'] = trial.sha(e['payload']['request'])
            return e
        lab.mutate = mutate
        _, result = self.run_lab(lab)
        self.assertTrue(any(p['fresh'] for p in result['pc_facts']))
        self.assertFalse(result['passed'])
        self.assertEqual(result['jev'], [])

    def test_capture_descriptor_original_binding_mismatch_refuses_then_stops(self):
        lab = Lab()
        def api(method, suffix, body=None):
            result = lab(method, suffix, body)
            if '/descriptor?' in suffix:
                result['generation'] = 999
            return result
        result = trial.run_trial(api, trial.Plan(REQUEST), lab.retain,
            monotonic=lambda: lab.clock.now, sleep=lab.clock.sleep)
        self.assertFalse(result['passed'])
        self.assertIsNotNone(lab.stop_at)
        self.assertEqual(result['error'], 'original_rgb_descriptor_binding_mismatch')

    def test_late_real_jev_completion_is_invoked_but_not_current_support(self):
        lab = Lab()
        def mutate(e):
            if e['kind'] == 'reasoning.output':
                e['payload'].update(status='stale', fresh_on_arrival=False, valid_until=103)
            return e
        lab.mutate = mutate
        _, result = self.run_lab(lab)
        self.assertTrue(result['passed'])
        self.assertFalse(result['useful_current_jev'])

    def test_uncertain_create_reconciles_same_id_stops_once_never_creates_again(self):
        lab = Lab()
        lab.create_timeout = True
        _, result = self.run_lab(lab)
        self.assertFalse(result['passed'])
        self.assertEqual(result['session_id'], lab.session['id'])
        self.assertEqual(len([c for c in lab.calls if c[:2] == ('POST', '/sessions')]), 1)
        self.assertIsNotNone(lab.stop_at)

    def test_active_indeterminate_or_changed_budget_prevents_create(self):
        for field in ('active', 'budget_changed'):
            lab = Lab()
            setattr(lab, field, True)
            _, result = self.run_lab(lab)
            self.assertFalse(result['passed'])
            self.assertFalse(any(c[0] == 'POST' for c in lab.calls))

    def test_drain_without_confirmation_remains_bounded_and_incomplete(self):
        lab = Lab()
        lab.cleanup_uncertain = True
        _, result = self.run_lab(lab)
        self.assertFalse(result['passed'])
        self.assertFalse(result['cleanup']['confirmed'])
        self.assertEqual(lab.clock.now, 175)

    def test_retention_loss_stops_owned_session_and_never_claims_complete(self):
        lab = Lab()
        def retain(kind, value):
            lab.retain(kind, value)
            if kind == 'response' and '/events?' in value['suffix']:
                raise OSError('authored disk failure')
        _, result = self.run_lab(lab, retain)
        self.assertIsNotNone(lab.stop_at)
        self.assertFalse(result['passed'])
        self.assertFalse(result['event_window']['complete'])

    def test_prepared_cli_performs_no_network_and_no_filesystem_write(self):
        output = io.StringIO()
        with patch.object(trial, 'LabHTTP', side_effect=AssertionError('network')), patch.object(trial, 'PrivateWindow', side_effect=AssertionError('write')), patch('sys.stdout', output):
            self.assertEqual(trial.main(['--request-id', REQUEST]), 0)
        self.assertEqual(json.loads(output.getvalue())['network_requests'], 0)

    def test_private_originals_preserve_ns_and_refuse_overwrite_and_overflow(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / 'artifacts') as temp, patch.object(trial, 'sync_parent'):
            path = str(Path(temp) / 'original-window.jsonl')
            archive = trial.PrivateWindow(path, max_bytes=256)
            original = {'original_ns': 1792208115779811123}
            archive('event', original)
            with self.assertRaises(FileExistsError): trial.PrivateWindow(path)
            with self.assertRaises(ValueError): archive('large', {'value': 'x' * 300})
            receipt = archive.finalize()
            self.assertEqual(receipt['status'], 'incomplete')
            self.assertEqual(json.loads(Path(path).read_text())['value'], original)


if __name__ == '__main__':
    unittest.main()

"""Producer-only launcher seam, using the actual owner commissioning CLI."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from producer_development import run_development_session


class FakeGraph:
    """External graph boundary only; production graph tests live separately."""
    def __init__(self):
        self.calls, self.closed = 0, []
        self.identity = {'site_id': 'synthetic:site', 'room_id': 'synthetic:room',
                         'session_id': 'synthetic:session', 'domain_epoch': 'synthetic:domain'}

    def status(self):
        return {'schema': 'home-acquisition-status-1', 'version': 1,
                'session_ref': self.identity, 'source_id': 'synthetic:oak',
                'configuration_digest': 'a' * 64,
                'stage_presence': {'spatial': False, 'interaction': False},
                'closed': bool(self.closed), 'inflight': False,
                'counters': {'cycles': self.calls, 'captures': self.calls,
                             'detector_runs': self.calls, 'pose_runs': self.calls,
                             'refusals': 0, 'source_gaps': 0}}

    def step(self):
        self.calls += 1
        return {'schema': 'home-acquisition-cycle-1', 'version': 1, 'status': 'observed',
                'reason': 'paired_capture_observed', 'capture': 'qualified',
                'detector': 'completed', 'pose': 'completed', 'spatial': 'missing',
                'interaction': 'missing', 'operational_geometry': False, 'focused_output': False}

    def close(self, reason='development_complete'):
        self.closed.append(reason)
        return {'status': 'stopped', 'reason': reason, 'withdrawal': 'recorded', 'buffers_cleared': True}


class ProducerDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='synthetic-producer-launcher-')
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.node = shutil.which('node')
        if self.node is None:
            self.skipTest('Node is required for the actual commissioning kit')
        kit = Path(__file__).resolve().parents[3] / 'tools' / 'den-commissioning' / 'commission.mjs'
        self.record = self.root / 'synthetic-record.json'
        subprocess.run([self.node, str(kit), 'template', '--output', str(self.record)],
                       check=True, capture_output=True, timeout=10)
        self.now = 0.0
        self.graph = FakeGraph()
        self.config = {'schema': 'home-producer-development-1', 'version': 1,
                       'session_ref': {'site_id': 'synthetic:site', 'room_id': 'synthetic:room',
                                       'session_id': 'synthetic:session', 'domain_epoch': 'synthetic:domain'},
                       'source_id': 'synthetic:oak', 'configuration_digest': 'a' * 64,
                       'max_runtime_s': 5, 'max_cycles': 3, 'interval_s': .5,
                       'commissioning_record': str(self.record),
                       'commissioning_freeze': None, 'measurement_summary': None}

    def sleep(self, seconds):
        self.now += seconds

    def run_session(self, **kwargs):
        return run_development_session(self.graph, self.config, clock=lambda: self.now,
                                       sleep=self.sleep, stop_requested=lambda: False,
                                       node_runtime=self.node, **kwargs)

    def test_real_incomplete_kit_allows_only_bounded_development_and_missing_stages(self):
        report = self.run_session()
        self.assertEqual(report['status'], 'development_complete')
        self.assertEqual(self.graph.calls, 3)
        self.assertEqual(self.graph.closed, ['development_complete'])
        self.assertEqual(report['commissioning']['status'], 'incomplete')
        self.assertEqual(report['missing_stages'], ['spatial', 'interaction'])
        self.assertEqual(report['counts']['cycles'], 3)
        self.assertFalse(report['operational_geometry'])
        self.assertFalse(report['focused_output'])
        self.assertEqual(report['runtime_qualification'], 'not_assessed')
        self.assertFalse(report['physical_authority'])
        serialized = json.dumps(report)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn('commissioning_record', serialized)

    def test_configuration_cannot_label_another_room_graph_as_this_session(self):
        self.graph.identity['room_id'] = 'synthetic:other-room'
        report = self.run_session()
        self.assertEqual(report['status'], 'refused')
        self.assertEqual(report['reason'], 'acquisition_identity_mismatch')
        self.assertEqual(self.graph.calls, 0)
        self.assertEqual(len(self.graph.closed), 1)

    def test_graph_metrics_are_kept_separate_and_unconfirmed_cleanup_is_visible(self):
        def close(reason='development_complete'):
            self.graph.closed.append(reason)
            return {'status': 'unconfirmed', 'reason': 'model_cancellation_pending',
                    'withdrawal': 'not_recorded', 'buffers_cleared': False}
        self.graph.close = close
        report = self.run_session()
        self.assertEqual(report['status'], 'cleanup_unconfirmed')
        self.assertFalse(report['cleanup']['buffers_cleared'])
        self.assertEqual(report['graph_counters']['captures'], 3)
        self.assertEqual(report['counts']['qualified_capture_results'], 3)

    def test_invalid_owner_record_refuses_before_acquisition_but_still_closes(self):
        self.record.write_text('{"schema":"not-an-owner-record"}', encoding='utf-8')
        report = self.run_session()
        self.assertEqual(report['status'], 'refused')
        self.assertEqual(report['reason'], 'commissioning_record_refused')
        self.assertEqual(self.graph.calls, 0)
        self.assertTrue(self.graph.closed)

    def test_runtime_limit_and_owner_stop_are_respected_without_invented_measurements(self):
        self.config['max_runtime_s'] = .75
        report = self.run_session()
        self.assertEqual(self.graph.calls, 2)
        self.assertEqual(report['reason'], 'development_runtime_limit')
        self.assertIsNone(report['measurement_aggregate'])
        self.assertEqual(report['cycle_start_gap_s'], {'samples': 1, 'max': .5})
        self.assertNotIn('capture_rate_hz', report)

    def test_closed_cycle_rejects_raw_rows_and_cleanup_runs_after_failure(self):
        original = self.graph.step
        def leaking_cycle():
            return {**original(), 'raw_pose': 'synthetic-private-data'}
        self.graph.step = leaking_cycle
        report = self.run_session()
        self.assertEqual(report['status'], 'refused')
        self.assertEqual(report['reason'], 'acquisition_cycle_invalid')
        self.assertNotIn('synthetic-private-data', json.dumps(report))
        self.assertTrue(self.graph.closed)

    def test_stop_and_invalid_limits_do_not_acquire(self):
        report = run_development_session(self.graph, self.config, clock=lambda: self.now,
            sleep=self.sleep, stop_requested=lambda: True, node_runtime=self.node)
        self.assertEqual(report['reason'], 'development_stop_requested')
        self.assertEqual(self.graph.calls, 0)
        self.assertEqual(report['missing_stages'], ['spatial', 'interaction'])
        self.graph = FakeGraph()
        self.config['max_cycles'] = 25
        report = self.run_session()
        self.assertEqual(report['reason'], 'development_config_invalid')
        self.assertEqual(self.graph.calls, 0)
        self.assertTrue(self.graph.closed)

    def test_owner_aggregate_is_checked_by_actual_kit_not_replaced_with_loop_metrics(self):
        # The real tool refuses a bad freeze even though the valid development
        # record itself is merely incomplete. Skipping aggregate invocation fails.
        freeze, summary = self.root / 'bad-freeze.json', self.root / 'owner-summary.json'
        freeze.write_text(json.dumps({'record': json.loads(self.record.read_text(encoding='utf-8'))}), encoding='utf-8')
        summary.write_text('{}', encoding='utf-8')
        self.config.update(commissioning_freeze=str(freeze), measurement_summary=str(summary))
        report = self.run_session()
        self.assertEqual(report['reason'], 'commissioning_aggregate_refused')
        self.assertEqual(self.graph.calls, 0)
        self.assertEqual(report['measurement_aggregate']['status'], 'refused')

    def test_owner_record_cannot_bind_another_room_even_when_record_is_incomplete(self):
        record = json.loads(self.record.read_text(encoding='utf-8'))
        record['survey']['pins']['room_id'] = 'synthetic:other-room'
        self.record.write_text(json.dumps(record), encoding='utf-8')
        report = self.run_session()
        self.assertEqual(report['reason'], 'commissioning_scope_mismatch')
        self.assertEqual(self.graph.calls, 0)

    def test_actual_aggregate_retains_missing_independent_measurements(self):
        # Fictional complete inputs exercise structural plumbing only. No bound
        # or reference here is a real Den measurement or production setting.
        record = json.loads(self.record.read_text(encoding='utf-8'))
        record.update(record_id='synthetic:record', story_id='synthetic:reading')
        req = record['requirements']
        req.update(independent_of_predictions=True, defined_at='2026-10-14T00:00:00Z')
        req['geometry']['task_extent_m'] = [.4, .3, .1]
        for key in req['geometry']:
            if key != 'task_extent_m':
                req['geometry'][key] = True if key == 'facing_required' else .5
        req['protection'] = {key: .1 for key in req['protection']}
        req['service'] = {key: 'a' * 64 if key.endswith('digest') else 1 for key in req['service']}
        req['runtime'] = {key: 1 if key.startswith('projected') else 100 for key in req['runtime']}
        pins = record['survey']['pins']
        for key in pins:
            pins[key] = 'a' * 64 if key.endswith('digest') else (
                1 if key in ('registration_revision', 'clock_mapping_revision') else 'synthetic:' + key)
        pins.update(site_id='synthetic:site', room_id='synthetic:room')
        bounds = record['survey']['geometry_bounds']
        for key in bounds:
            bounds[key] = 'a' * 64 if key.endswith('digest') else True if key == 'front_back_resolved' else .01
        for index, position in enumerate(record['survey']['positions']):
            position['position_id'] = 'synthetic:position' + str(index)
            for reference in position['references'].values():
                reference.update(status='available', uncertainty_m=.01,
                                 reference_digest='a' * 64, applicability_digest='a' * 64)
        for index, fixture in enumerate(record['survey']['fixtures']):
            fixture.update(fixture_id='synthetic:fixture' + str(index),
                           survey_digest='a' * 64, optical_envelope_digest='a' * 64)
        record['survey']['nominal_profile'].update(profile_id='synthetic:profile',
            example_id='synthetic:example', brightness_fraction=.5, cct_kelvin=3500)
        record['evaluation'].update(development_partition_id='synthetic:dev',
            held_out_partition_id='synthetic:held', development_episode_ids=['synthetic:dev1'],
            held_out_episode_ids=['synthetic:held1'], confuser_ids=['synthetic:unused-book'],
            episode_attempts=[{'episode_id': 'synthetic:dev1', 'attempt_ids': ['synthetic:attempt1']},
                              {'episode_id': 'synthetic:held1', 'attempt_ids': ['synthetic:attempt2']}],
            independent_annotation_protocol_digest='a' * 64)
        self.record.write_text(json.dumps(record), encoding='utf-8')
        kit = Path(__file__).resolve().parents[3] / 'tools' / 'den-commissioning' / 'commission.mjs'
        freeze_path, summary_path = self.root / 'freeze.json', self.root / 'aggregate.json'
        frozen = subprocess.run([self.node, str(kit), 'freeze', '--record', str(self.record),
            '--state-dir', str(self.root / 'ledger'), '--output', str(freeze_path)],
            capture_output=True, timeout=10)
        self.assertEqual(frozen.returncode, 0, frozen.stdout)
        freeze = json.loads(freeze_path.read_text(encoding='utf-8'))
        summary = {'schema': 'den-commissioning-measurement-summary-1',
            'freeze_digest': freeze['freeze_digest'], 'assessment_digest': 'b' * 64,
            'assessment_partition_ref': freeze['assessment_partition_refs'][0],
            'units': {'distance': 'm', 'time': 's'}, 'duration_s': 1,
            'source_validity': None, 'work_center_error_m': None,
            'reference_uncertainty_m': dict.fromkeys(('task', 'body', 'head', 'hands', 'protection')),
            'containment': dict.fromkeys(('task', 'body', 'head', 'hands', 'protection')),
            'latency_s': {'observation_to_available': None, 'publication_to_decision': None}}
        summary_path.write_text(json.dumps(summary), encoding='utf-8')
        self.config.update(commissioning_freeze=str(freeze_path), measurement_summary=str(summary_path))
        report = self.run_session()
        self.assertEqual(report['status'], 'development_complete')
        aggregate = report['measurement_aggregate']
        self.assertEqual(aggregate['status'], 'incomplete_owner_aggregate')
        self.assertEqual(aggregate['origin'], 'owner_supplied_not_recomputed')
        self.assertIsNone(aggregate['work_center_error_m'])
        self.assertIn('work_center_error_m', aggregate['missing_groups'])
        self.assertFalse(aggregate['physical_authority'])


if __name__ == '__main__':
    unittest.main()

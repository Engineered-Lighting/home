"""Actual bundled factory/package6/composition/supervisor2, authored external I/O."""
from copy import deepcopy
import json
import shutil
import unittest

from journal import digest
import lab_live_runtime as runtime
import lab_stereo_session_package as package
import test_lab_stereo_mount as mounted_fixture


class LabPollBudgetCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = mounted_fixture.PollBudgetStereoMountTests()
        cls.f.setUp()
        cls.addClassCleanup(cls.f.doCleanups)
        cls.inputs = deepcopy(cls.f.inputs)
        cls.inputs['runtime']['node_runtime'] = shutil.which('node')
        cls.inputs.update(schema='home-den-lab-session-inputs-6', version=6)
        cls.inputs['stereo'].update(schema='home-den-stereo-package-inputs-3', version=3)
        cls.inputs['runtime'].pop('max_cycles')
        cls.inputs['runtime'].update(max_poll_attempts=40, max_capture_originals=4)

    def test_a_package6_binds_exact_budgets_and_refuses_crossed_or_command_profiles(self):
        before = deepcopy(self.inputs)
        result = package.prepare_stereo_session_package(before, now=self.f.source.f.now)
        self.assertEqual(result['status'], 'prepared', result)
        self.assertEqual((result['schema'], result['version']), ('home-den-lab-session-package-6', 6))
        mount, boot = result['files']['mount.json'], result['files']['bootstrap.json']
        self.assertEqual((mount['version'], boot['version'], mount['stereo_source']['version']), (7, 6, 4))
        self.assertEqual((mount['runtime']['version'], mount['runtime']['session']['version']), (2, 2))
        self.assertEqual(mount['producer'], before['producer'])
        self.assertEqual(mount['runtime']['session']['max_capture_originals'], before['producer']['limits']['max_capture_originals'])
        self.assertIsNone(mount['command_capture']); self.assertEqual(boot['command_capture_digest'], digest(None))
        self.assertFalse(result['activation_ready'])
        self.assertIn('operational_cadence_and_resource_validation_required', result['activation_blockers'])
        self.assertEqual(before, self.inputs)
        for edit in (lambda v: v['runtime'].update(max_capture_originals=3),
                     lambda v: v['runtime'].update(max_poll_attempts=41),
                     lambda v: v['runtime'].update(max_cycles=8),
                     lambda v: v.update(command_capture={}),
                     lambda v: v.update(schema='home-den-lab-session-inputs-5', version=5)):
            changed = deepcopy(self.inputs); edit(changed)
            refused = package.prepare_stereo_session_package(changed, now=self.f.source.f.now)
            self.assertEqual(refused['status'], 'refused', refused)
            self.assertEqual(refused['files'], {})

    def test_b_actual_factory_empty_polls_then_one_capture_uses_package_supervisor_budget_and_stop(self):
        source = self.f.source
        producer = deepcopy(source.producer)
        producer['limits'].update(max_poll_attempts=6, max_capture_originals=1)
        producer['after_id'] = source.f.store.list_events(source.f.session['id'])[-1]['id']
        inputs = deepcopy(self.inputs); inputs['producer'] = producer
        inputs['runtime'].update(max_poll_attempts=6, max_capture_originals=1)
        inputs['preflight']['configuration_digest'] = digest(runtime.lab_graph_configuration(producer, session=source.f.session))
        prepared = package.prepare_stereo_session_package(inputs, now=source.f.now)
        self.assertEqual(prepared['status'], 'prepared', prepared)
        composition = self.f.compose(producer)
        composition.monotonic = lambda: source.f.now - mounted_fixture.NOW
        source.composition = composition
        supervisor = runtime.compose_lab_supervisor(prepared['files']['mount.json']['runtime'], composition,
            supervisor_preflight=lambda _: {'status': 'ready'})
        self.assertEqual(supervisor.config, prepared['files']['mount.json']['runtime']['session'])
        self.assertEqual(supervisor.config['max_poll_attempts'], 6)
        self.assertEqual(supervisor.config['max_capture_originals'], 1)
        graph = supervisor.graph_factory(); source.graph = graph
        self.assertEqual(graph.config, composition.acquisition_config)
        recorder = supervisor.recorder_factory(graph)
        self.assertEqual(recorder.status()['status'], 'ready')
        graph.journal.attach_recorder(recorder.record)
        supervisor.graph, supervisor.recorder = graph, recorder
        self.addCleanup(supervisor.stop)
        self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(graph.status()['counters']['capture_originals'], 0)
        source.f.append_pair(sequence=20, observed_at=mounted_fixture.NOW+.579, expect_output=False)
        source.f.append_pair(sequence=30, observed_at=mounted_fixture.NOW+.817, expect_output=False)
        event, _ = source.f.append_pair(sequence=40, observed_at=mounted_fixture.NOW+.955)
        result = graph.step()
        rows = composition.bundled_source.prepared[event['id']]['result']['rows']
        raw_depth = rows['bundle'][0]['metadata']['source_metadata']['frames']['depth']['device_timestamp_seconds']
        raw_b = rows['anchor_b'][0]['metadata']['source_timing_receipt']['frame_device_ns'] / 1e9
        commissioning = composition.bundled_source.depth_clock.clock
        self.assertEqual(commissioning, self.f.qualified['depth_clock_policy'])
        self.assertLessEqual(abs(raw_depth - raw_b), commissioning['max_delta_s'])
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(graph.status()['counters']['poll_attempts'], 3)
        original = next(iter(graph.join.originals.values()))
        uncertainty = original['clock_original']['clock']['uncertainty_s']
        depth = composition.bundled_source.selected[event['id']]['rows']['mapped_depth'][0]['metadata']
        self.assertLessEqual(uncertainty, .1)
        self.assertLessEqual(depth['observed_at'], event['payload']['observed_at'])
        self.assertLessEqual(event['payload']['observed_at'] - depth['observed_at']
                             + uncertainty + depth['uncertainty_seconds'], .1)
        self.assertEqual(source.f.runtime.bundled_rgb_sessions[source.f.session['id']].capture_count, 5)
        self.assertEqual(len(composition.desk_capability.media_archive.media_refs()), 8)
        self.assertEqual(graph.step()['reason'], 'lab_capture_original_budget_exhausted')
        self.assertIsNone(supervisor.worker)
        self.assertIsNone(supervisor.rpc)
        self.assertIsNone(supervisor.thread)
        self.assertEqual(supervisor.cycles, 0)
        stopped = supervisor.stop(reason='authored_budget_composition_stop')
        self.assertEqual(stopped['status'], 'stopped', stopped)
        self.assertEqual(stopped['recording']['status'], 'drained')
        drain = stopped['drain']
        self.assertEqual(drain['session_ref'], composition.scope)
        self.assertEqual(drain['configuration_digest'], graph.configuration_digest)
        self.assertEqual(drain['recorder_status'], 'drained')
        # The existing cleanup protocol calls this settled when no worker
        # existed. This is no claim that a worker consumed or replayed the cut.
        self.assertEqual(drain['worker_status'], 'settled')
        self.assertIsNotNone(drain['withdrawal_receipt'])
        self.assertEqual(drain['terminal_cursor'], drain['withdrawal_receipt']['cursor'])
        self.assertEqual(supervisor.result['finalization']['status'], 'stopped')
        self.assertTrue(supervisor.result['finalization']['buffers_cleared'])
        self.assertFalse(stopped['acceptance_eligible'])
        self.assertFalse(stopped['physical_authority'])
        self.assertLessEqual(recorder.path.stat().st_size,
                             prepared['files']['mount.json']['runtime']['archive_limits']['max_bytes'])
        records = [json.loads(line)['record'] for line in recorder.path.read_bytes().splitlines()]
        self.assertEqual(records[-1]['kind'], 'session_end')
        self.assertFalse([row for row in records if row['kind'].startswith('worker_')])
        self.assertTrue(composition.cancel.is_set())
        self.assertEqual(source.f.store.get_session(source.f.session['id'])['state'], 'recording')


if __name__ == '__main__': unittest.main()

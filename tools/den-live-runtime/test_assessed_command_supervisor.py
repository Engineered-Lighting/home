"""Supervisor3 scheduling fences; authored ports and a real bounded recorder.

These tests do not start a native fixture, model or actual Journal worker.
Protected package/factory and connected acceptance have separate gates.
"""
from copy import deepcopy
from pathlib import Path
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_runtime as fixture
from supervisor import SessionSupervisor, SupervisorRefusal, compose_supervisor


class AssessedCommandSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RecorderAndSupervisorTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def inputs(self):
        old, graph, trace = self.f.supervisor()
        config = deepcopy(old.config)
        config.update(schema='home-live-session-config-3', version=3,
                      max_poll_attempts=2, max_capture_originals=1, max_runtime_s=2)
        del config['max_cycles']
        return config, old, graph, trace

    @staticmethod
    def make(config, old, **kwargs):
        return SessionSupervisor(config, graph_factory=old.graph_factory,
            recorder_factory=old.recorder_factory, worker_factory=old.worker_factory,
            preflight=old.preflight, **kwargs)

    def test_source_only_profile3_preserves_readiness_poll_budget_and_drain(self):
        config, old, graph, trace = self.inputs()
        owner = self.make(config, old)
        self.addCleanup(owner.stop)
        owner.start()
        owner.thread.join(4)
        self.assertFalse(owner.thread.is_alive())
        status = owner.status()
        self.assertEqual(status['status'], 'stopped', status)
        self.assertEqual(status['reason'], 'session_poll_budget_exhausted')
        self.assertEqual(status['poll_budget'], {'max_poll_attempts': 2, 'max_capture_originals': 1})
        self.assertEqual(trace.count('source_step'), 2)
        self.assertLess(trace.index('recorder_attached'), trace.index('worker_ready'))
        self.assertLess(trace.index('worker_ready'), trace.index('source_step'))
        self.assertLess(trace.index('withdraw'), trace.index('worker_drain'))
        self.assertEqual(graph.drain['terminal_cursor'], 7)
        self.assertFalse(status['acceptance_eligible'])
        self.assertFalse(status['physical_authority'])

    def test_wrong_command_owner_never_starts_worker_or_source(self):
        config, old, _, trace = self.inputs()
        def wrong_owner(graph, recorder):
            self.assertEqual(recorder.status()['status'], 'ready')
            trace.append('wrong_command_owner')
            return object()
        owner = self.make(config, old, command_factory=wrong_owner)
        self.addCleanup(owner.stop)
        owner.start()
        owner.thread.join(4)
        self.assertFalse(owner.thread.is_alive())
        self.assertIn('wrong_command_owner', trace)
        self.assertNotIn('worker_ready', trace)
        self.assertNotIn('source_step', trace)
        self.assertEqual(owner.status()['cycles'], 0)
        self.assertEqual(owner.status()['status'], 'incomplete')

    def test_closed_caps_and_profile2_command_refusal_precede_effects(self):
        config, old, _, _ = self.inputs()
        for edit in (lambda c: c.update(max_poll_attempts=241),
                     lambda c: c.update(max_capture_originals=25),
                     lambda c: c.update(max_poll_attempts=True),
                     lambda c: c.update(max_cycles=1),
                     lambda c: c.update(schema='home-live-session-config-2')):
            changed = deepcopy(config); edit(changed)
            with self.assertRaises(SupervisorRefusal):
                self.make(changed, old)
        previous = deepcopy(config)
        previous.update(schema='home-live-session-config-2', version=2)
        with self.assertRaises(SupervisorRefusal):
            self.make(previous, old, command_factory=lambda *_: None)

    def test_runtime3_requires_matching_session3_and_is_inert(self):
        session, old, _, trace = self.inputs()
        session['mode'] = 'development'
        config = dict(schema='home-live-runtime-config-3', version=3, session=session,
            worker_config=None, node_runtime=str(Path(__file__).resolve()),
            archive_file=str(Path(self.f.work.name)/'unused-runtime3-archive'),
            archive_limits={'max_records': 32, 'max_bytes': 65536, 'min_free_bytes': 1},
            source_bundle_digest='b'*64)
        before = deepcopy(config)
        owner = compose_supervisor(config, graph_factory=old.graph_factory, preflight=old.preflight)
        self.assertEqual(owner.status()['status'], 'idle')
        self.assertEqual(before, config)
        self.assertEqual(trace, [])
        self.assertFalse(Path(config['archive_file']).exists())
        for edit in (lambda c: c['session'].update(schema='home-live-session-config-2', version=2),
                     lambda c: c.update(schema='home-live-runtime-config-2', version=2)):
            crossed = deepcopy(config); edit(crossed)
            with self.assertRaises(SupervisorRefusal):
                compose_supervisor(crossed, graph_factory=old.graph_factory, preflight=old.preflight)

    def test_terminal_results_consume_exact_matching_replay_profile(self):
        config, old, _, _ = self.inputs()
        for version, replay_version in ((3, 2), (2, 1), (1, 1)):
            chosen = deepcopy(config)
            chosen.update(schema='home-live-session-config-' + str(version), version=version)
            if version == 1:
                chosen['max_cycles'] = chosen.pop('max_poll_attempts')
                del chosen['max_capture_originals']
            owner = self.make(chosen, old)
            owner.state = 'stopped'
            owner.worker = SimpleNamespace(node_runtime='authored-unused-node')
            owner.recorder = SimpleNamespace(path=Path(self.f.work.name)/'authored-unused-archive',
                                             status=lambda: {'status': 'drained'})
            result = dict(schema='home-live-ordinary-replay-result-' + str(replay_version),
                          version=replay_version, status='reproduced', physical_authority=False)
            with patch('supervisor.subprocess.run', return_value=SimpleNamespace(stdout=json.dumps(result))) as run:
                self.assertEqual(owner.read_results()['ordinary_replay'], result)
                self.assertEqual(run.call_count, 1)
            crossed = dict(result, schema='home-live-ordinary-replay-result-' + str(3-replay_version),
                           version=3-replay_version)
            with patch('supervisor.subprocess.run', return_value=SimpleNamespace(stdout=json.dumps(crossed))):
                self.assertEqual(owner.read_results()['ordinary_replay']['reason'], 'ordinary_replay_unavailable')


if __name__ == '__main__':
    unittest.main()

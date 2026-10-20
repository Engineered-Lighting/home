"""Explicit supervisor2 limits over the existing recorder/lifecycle ports."""
from copy import deepcopy
import time
import unittest

import test_runtime as fixture
from supervisor import SessionSupervisor, SupervisorRefusal, compose_supervisor


class PollBudgetSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RecorderAndSupervisorTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_supervisor2_counts_empty_source_turns_and_reports_explicit_poll_exhaustion(self):
        legacy, graph, trace = self.f.supervisor()
        config = deepcopy(legacy.config)
        config.update(schema='home-live-session-config-2', version=2, max_poll_attempts=2,
            max_capture_originals=1, max_runtime_s=2)
        del config['max_cycles']
        owner = SessionSupervisor(config, graph_factory=legacy.graph_factory, recorder_factory=legacy.recorder_factory,
            worker_factory=legacy.worker_factory, preflight=legacy.preflight)
        owner.start()
        self.addCleanup(owner.stop)
        owner.thread.join(4)
        self.assertFalse(owner.thread.is_alive())
        status = owner.status()
        self.assertEqual(status['status'], 'stopped', status)
        self.assertEqual(status['reason'], 'session_poll_budget_exhausted')
        self.assertEqual(trace.count('source_step'), 2)
        self.assertEqual(status['poll_budget'], {'max_poll_attempts': 2, 'max_capture_originals': 1})
        self.assertLess(trace.index('recorder_attached'), trace.index('source_step'))
        self.assertLess(trace.index('withdraw'), trace.index('worker_drain'))

    def test_crossed_versions_invalid_caps_and_commands_refuse_before_factories(self):
        legacy, _, _ = self.f.supervisor()
        good = deepcopy(legacy.config)
        good.update(schema='home-live-session-config-2', version=2, max_poll_attempts=3, max_capture_originals=1)
        del good['max_cycles']
        def forbidden(*_args, **_kwargs): raise AssertionError('factory_before_start')
        for edit in (lambda c: c.update(max_poll_attempts=241), lambda c: c.update(max_capture_originals=25),
                     lambda c: c.update(max_poll_attempts=True), lambda c: c.update(max_cycles=1),
                     lambda c: c.update(schema='home-live-session-config-1', version=1)):
            value = deepcopy(good); edit(value)
            with self.assertRaises(SupervisorRefusal):
                SessionSupervisor(value, graph_factory=forbidden, recorder_factory=forbidden,
                    worker_factory=forbidden, preflight=forbidden)
        with self.assertRaises(SupervisorRefusal):
            SessionSupervisor(good, graph_factory=forbidden, recorder_factory=forbidden,
                worker_factory=forbidden, preflight=forbidden, command_factory=forbidden)
        base = dict(schema='home-live-runtime-config-2', version=2, session=legacy.config,
            node_runtime=str(fixture.Path(__file__).resolve()), archive_file=str(fixture.Path(self.f.work.name)/'unused'),
            archive_limits={'max_records': 32, 'max_bytes': 65536, 'min_free_bytes': 1},
            source_bundle_digest='b'*64, worker_config=None)
        with self.assertRaises(SupervisorRefusal): compose_supervisor(base, graph_factory=forbidden, preflight=forbidden)


if __name__ == '__main__': unittest.main()

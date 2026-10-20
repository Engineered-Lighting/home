"""Controlled source/worker coordination, preserving the old default schedule."""
import sys
import time
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_runtime as runtime_cases
from supervisor import SessionSupervisor


class SourceHandoffTests(unittest.TestCase):
    setUp=runtime_cases.RecorderAndSupervisorTests.setUp
    recorder=runtime_cases.RecorderAndSupervisorTests.recorder
    supervisor=runtime_cases.RecorderAndSupervisorTests.supervisor

    def completed(self,supervisor):
        deadline=time.monotonic()+4
        while supervisor.state not in {'stopped','incomplete','refused'} and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn(supervisor.state,{'stopped','incomplete','refused'})

    def test_no_next_source_step_precedes_a_durable_handoff(self):
        old,graph,trace=self.supervisor()
        old.config.update(max_cycles=2,max_runtime_s=3,interval_s=1)
        def handoff(source,worker,outcome,*,cancel,deadline):
            self.assertIs(source,graph)
            self.assertGreater(deadline,time.monotonic())
            trace.append('accepted_handoff')
            return {'status':'settled'}
        selected=SessionSupervisor(old.config,graph_factory=old.graph_factory,recorder_factory=old.recorder_factory,
            worker_factory=old.worker_factory,preflight=old.preflight,after_source_step=handoff,fixed_period=True)
        selected.start();self.completed(selected)
        events=[event for event in trace if event in {'source_step','accepted_handoff'}]
        self.assertEqual(events,['source_step','accepted_handoff','source_step','accepted_handoff'])
        self.assertEqual(selected.state,'stopped')

    def test_unconfirmed_handoff_stops_incomplete_before_another_capture(self):
        old,graph,trace=self.supervisor()
        old.config.update(max_cycles=2,max_runtime_s=3,interval_s=1)
        selected=SessionSupervisor(old.config,graph_factory=old.graph_factory,recorder_factory=old.recorder_factory,
            worker_factory=old.worker_factory,preflight=old.preflight,
            after_source_step=lambda *args,**kwargs:{'status':'unconfirmed'},fixed_period=True)
        selected.start();self.completed(selected)
        self.assertEqual(trace.count('source_step'),1)
        self.assertEqual(selected.state,'incomplete')
        self.assertEqual(selected.reason,'source_worker_handoff_unconfirmed')

    def test_fixed_period_is_an_explicit_controlled_only_policy(self):
        old,_,_=self.supervisor()
        for changed in ({'mode':'qualified'},{'mode':'development'}):
            with self.assertRaises(ValueError):
                SessionSupervisor({**old.config,**changed},graph_factory=old.graph_factory,
                    recorder_factory=old.recorder_factory,worker_factory=old.worker_factory,preflight=old.preflight,
                    after_source_step=lambda *args,**kwargs:{'status':'settled'},fixed_period=True)

    def test_late_success_cannot_complete_the_session(self):
        old,_,trace=self.supervisor()
        old.config.update(max_cycles=2,max_runtime_s=.04,interval_s=1)
        def late(*args,**kwargs):
            time.sleep(.06)
            return {'status':'settled'}
        selected=SessionSupervisor(old.config,graph_factory=old.graph_factory,recorder_factory=old.recorder_factory,
            worker_factory=old.worker_factory,preflight=old.preflight,after_source_step=late,fixed_period=True)
        selected.start();self.completed(selected)
        self.assertEqual(trace.count('source_step'),1)
        self.assertEqual(selected.state,'incomplete')
        self.assertEqual(selected.reason,'source_worker_handoff_unconfirmed')

if __name__=='__main__':unittest.main()

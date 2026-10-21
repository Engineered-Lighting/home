"""Focused optional-pump safety contracts; authored inputs, zero host I/O."""
import threading
import unittest
from preview_client import PreviewPump
import test_apartment_viewing_metric_admission as baseline


class PumpWakeGuards(unittest.TestCase):
    def test_idle_overrun_during_retry_wait_skips_late_retry(self):
        now=[0.];captures=[];drains=[]
        def drain():drains.append(now[0]);now[0]=.9
        def fetch():
            captures.append(now[0])
            if len(captures)==1:raise ValueError('authored_capture_refusal')
            pump.stop();return b'jpeg','metadata'
        pump=PreviewPump(fetch,lambda *_:None,lambda:True,clock=lambda:now[0],
                         idle_drain=drain,retry_after=.1)
        def wait(timeout):
            if len(captures)==1 and not drains:
                now[0]=.55;pump.wake_event.set();return True
            now[0]+=timeout;return False
        pump.wake_event.wait=wait;pump._run()
        self.assertEqual(captures,[.5,1.0])
        self.assertEqual(len(drains),1)
        self.assertEqual(pump.counters()['retries'],0)

    def test_idle_overrun_skips_missed_slot_without_late_extra_fetch(self):
        now=[0.];captures=[];drains=[]
        def drain():drains.append(now[0]);now[0]=.9
        pump=PreviewPump(lambda:captures.append(now[0]) or (b'jpeg','metadata'),
                         lambda *_:pump.stop(),lambda:True,clock=lambda:now[0],idle_drain=drain)
        pump.wake_event.set()
        def wait(timeout):
            if pump.wake_event.is_set():return True
            now[0]+=timeout;return False
        pump.wake_event.wait=wait;pump._run()
        self.assertEqual(captures,[1.0])
        self.assertEqual(len(drains),1)
        self.assertEqual(pump.counters()['skipped_slots'],1)
        self.assertEqual(pump.counters()['attempts'],1)

    def test_pending_wake_at_due_capture_drains_before_fetch_on_same_grid(self):
        calls=[];clock_calls=[]
        def clock():
            clock_calls.append(1)
            return 0. if len(clock_calls)==1 else .5
        pump=PreviewPump(lambda:calls.append('capture') or (b'jpeg','metadata'),
                         lambda *_:(calls.append('consume'),pump.stop()),lambda:True,
                         clock=clock,idle_drain=lambda:calls.append('drain'))
        pump.wake_event.set();pump._run()
        self.assertEqual(calls,['drain','capture','consume'])
        self.assertEqual(pump.counters()['attempts'],1)

    def test_default_nonmetric_pump_keeps_original_wait_and_no_idle_event(self):
        calls=[]
        pump=PreviewPump(lambda:(_ for _ in ()).throw(AssertionError('capture')),
                         lambda *_:None,lambda:True,clock=lambda:0)
        self.assertIsNone(pump.wake_event);self.assertIsNone(pump.idle_drain)
        pump.stopped.wait=lambda timeout:calls.append(timeout) or True
        pump._run()
        self.assertEqual(calls,[.5]);self.assertEqual(pump.counters()['attempts'],0)

    def test_stop_wakes_wait_and_never_drains_or_captures(self):
        work=[]
        pump=PreviewPump(lambda:work.append('capture'),lambda *_:work.append('consume'),
                         lambda:True,clock=lambda:0,idle_drain=lambda:work.append('drain'))
        def wait(_):pump.stop();return True
        pump.wake_event.wait=wait;pump._run()
        self.assertEqual(work,[]);self.assertEqual(pump.counters()['attempts'],0)

    def test_inactive_wake_is_ignored_without_capture_or_drain(self):
        work=[];now=[0.];waits=[]
        pump=PreviewPump(lambda:work.append('capture'),lambda *_:work.append('consume'),
                         lambda:False,clock=lambda:now[0],idle_drain=lambda:work.append('drain'))
        def wait(timeout):
            waits.append(timeout)
            if len(waits)==1:now[0]=.25;return True
            if len(waits)==2:now[0]+=.25;return False
            pump.stop();return True
        pump.wake_event.wait=wait;pump._run()
        self.assertEqual(work,[]);self.assertEqual(pump.counters()['attempts'],0)

    def test_coalesced_event_and_drain_error_do_not_add_camera_work(self):
        now=[0.];calls=[];waits=[]
        def drain():calls.append('drain');raise ValueError('private=do-not-record')
        pump=PreviewPump(lambda:calls.append('capture') or (b'jpeg','metadata'),lambda *_:calls.append('consume'),
                         lambda:True,clock=lambda:now[0],idle_drain=drain)
        for _ in range(100):pump.wake_event.set()
        def wait(timeout):
            waits.append(timeout)
            if len(waits)==1:return pump.wake_event.is_set()
            if len(waits)==2:now[0]=.5;return False
            pump.stop();return True
        pump.wake_event.wait=wait;pump._run()
        self.assertEqual(calls,['drain','capture','consume'])
        self.assertEqual(pump.counters()['failures'],{'idle:ValueError':1})
        self.assertEqual(pump.counters()['attempts'],1)


class MetricWakeGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline.MetricAdmissionTests.setUpClass()
        cls.addClassCleanup(baseline.MetricAdmissionTests.doClassCleanups)
    def setUp(self):
        self.h=baseline.MetricAdmissionTests('runTest');self.h.setUp()
        self.addCleanup(self.h.doCleanups)
    def ready(self):
        h=self.h;runtime=h.build()
        h.consume(.5,.59);h.outputs(2)
        h.capture_index=1;h.consume(1,1.59);h.consume(1.5,1.59)
        h.capture_index=2;h.consume(2,2.59);h.consume(2.5,2.59);h.outputs(4)
        self.assertIsNotNone(h.source.metric.lease)
        self.assertTrue(h.pump.wake_event.wait(1))
        h.pump.fetch=lambda:(_ for _ in ()).throw(AssertionError('idle wake cannot fetch'))
        return runtime
    def run_wake_only(self):
        h=self.h;waits=[];h.pump.clock=lambda:h.f.now
        def wait(_):
            waits.append(1)
            if len(waits)==1:return True
            h.pump.stop();return True
        h.pump.wake_event.wait=wait;h.pump._run()
        self.assertEqual(h.pump.counters()['attempts'],0)
    def test_expired_original_wake_cannot_publish_or_renew(self):
        runtime=self.ready();h=self.h
        deadline=h.source.metric.lease.descriptor['retirement_deadline']
        h.f.now=deadline
        self.run_wake_only()
        packet=runtime.live(h.f.session)['arms']['B']['virtual_target_preview']
        self.assertFalse(packet['available']);self.assertIsNone(h.source.metric.lease)
        runtime.stop_session(h.f.session)
        self.assertEqual(h.source.status()['resident_bytes'],0)
    def test_stop_during_idle_projection_discards_late_result_and_buffers(self):
        runtime=self.ready();h=self.h;project=h.source.metric.projection.ingest;threads=[]
        def stop_during(*args,**kwargs):
            threads.append(threading.get_ident())
            h.pump.stop();h.source.stop('controlled_stop_during_projection')
            return project(*args,**kwargs)
        h.source.metric.projection.ingest=stop_during
        self.run_wake_only()
        self.assertEqual(threads,[threading.get_ident()])
        self.assertIsNone(h.source.metric.preview_packet)
        self.assertEqual(h.source.status()['resident_bytes'],0)
        self.assertFalse(h.source.metric.preview_snapshot({},session_state='recording')['available'])


if __name__=='__main__':unittest.main(verbosity=2)

"""Actual pump _run/_wait_until with deterministic clock/wait external ports.

The 750ms model time is authored virtual time, not a wall-clock/model benchmark.
All source TTLs/proofs remain the immutable campaign's original values.
"""
import json
from pathlib import Path
import threading
import unittest
import test_apartment_viewing_metric_admission as baseline

TRACE_PATH=None  # Optional caller-owned evidence path; normal tests write nothing.


class PumpGridFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline.MetricAdmissionTests.setUpClass()
        cls.addClassCleanup(baseline.MetricAdmissionTests.doClassCleanups)

    def setUp(self):
        self.h=baseline.MetricAdmissionTests('runTest');self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def test_real_grid_wakes_for_current_completion_and_retires_at_original_expiry(self):
        h=self.h;runtime=h.build()
        h.consume(0,.59);h.consume(.5,.59);h.outputs(2)
        h.capture_index=1;h.consume(1.5,1.59)
        self.assertIsNone(h.source.metric.lease)
        h.consume(2,2.09);h.capture_index=2
        runner=runtime.runners[h.f.session['id']]
        entered=threading.Event();release=threading.Event();lock=threading.Lock();starts=[]
        self.addCleanup(release.set)
        model=runner.executor
        def delayed(request):
            with lock:
                starts.append(request['arm'])
                if len(starts)==2:entered.set()
            if not release.wait(2):raise AssertionError('authored model not released')
            return model(request)
        runtime.executor=runner.executor=delayed
        h.consume(2.5,2.59);self.assertTrue(entered.wait(1))
        lease=h.source.metric.lease;self.assertIsNotNone(lease)
        completion=1800000003.34;until=lease.image_metadata['valid_until']
        self.assertLess(completion,until)
        wake=getattr(h.pump,'wake_event',None)
        if wake is not None:wake.clear()
        real_wake_wait=wake.wait if wake is not None else None
        h.pump.clock=lambda:h.f.now
        calls=[];captures=[];preview={};projection_threads=[]
        ingest=h.source.metric.projection.ingest
        def project(*args,**kwargs):
            projection_threads.append(threading.get_ident())
            return ingest(*args,**kwargs)
        h.source.metric.projection.ingest=project
        def fetch():
            offset=round(h.f.now-1800000000,2)
            captures.append(offset)
            self.assertIn(offset,(3.09,3.59))
            observed=3 if offset==3.09 else 3.5
            return h.f.originals['anchor_b'],h.original_header(observed)
        h.pump.fetch=fetch
        def wait(timeout):
            deadline=h.f.now+timeout;calls.append(round(deadline-1800000000,2))
            if len(calls)==1:
                self.assertAlmostEqual(deadline,1800000003.09,places=6)
                h.f.now=deadline;return False
            if len(calls)==2:
                self.assertAlmostEqual(deadline,1800000003.59,places=6)
                h.f.now=completion;release.set();rows=h.outputs(4)
                exact=[r for r in rows if r['payload']['frame_id']==lease.image_metadata['frame_id']]
                self.assertEqual(len(exact),2)
                self.assertTrue(all(r['payload']['current'] for r in exact))
                preview['before_wake']=runtime.live(h.f.session)['arms']['B']['virtual_target_preview']
                if wake is not None:
                    self.assertTrue(real_wake_wait(1),'actual paired completion must notify this exact pump')
                    return True
                h.f.now=deadline;return False
            if wake is not None and len(calls)==3:
                self.assertAlmostEqual(deadline,1800000003.59,places=6)
                preview['after_wake']=runtime.live(h.f.session)['arms']['B']['virtual_target_preview']
                self.assertLess(h.f.now,until)
                h.f.now=deadline;return False
            h.pump.stopped.set();return True
        # Only the waiting/clock port is deterministic. Actual _run keeps its
        # fixed-grid/skip/attempt/drain order, and model threads set REAL Event.
        if wake is None:h.pump.stopped.wait=wait
        else:wake.wait=wait
        h.pump._run()
        preview['after_original_expiry']=runtime.live(h.f.session)['arms']['B']['virtual_target_preview']
        trace={'schema':'authored-metric-real-pump-grid-1','completion_delay_s':.75,
            'actual_grid_loop':True,'virtual_wait_port':True,'wall_sleep_latency_claim':False,
            'original_input_valid_until':until,'model_completed_at':completion,
            'mapped_rgb_valid_until_before_proof_intersection':lease.descriptor['timing']['valid_until'],
            'remaining_original_s_at_completion':until-completion,'wait_deadlines':calls,
            'capture_slots':captures,'preview':preview,'projection_threads':projection_threads,
            'pump_thread':threading.get_ident(),'sampling_hz':runner.sampling_hz,
            'metric_status':h.source.metric.status(),'manual_finalize_calls':0,
            'original_ttl_mutations':0,'host_operations':0}
        runtime.stop_session(h.f.session)
        trace['resident_bytes_after_stop']=h.source.status()['resident_bytes']
        if TRACE_PATH is not None:
            with Path(TRACE_PATH).open('x') as stream:json.dump(trace,stream,sort_keys=True,indent=2)
        available=preview.get('after_wake',preview['before_wake'])
        self.assertTrue(available['available'],
            'current model completed with %.6fs original time, but no pump drain produced preview; captures=%r, final=%s'
            %(until-completion,captures,trace['metric_status']['reason']))
        self.assertFalse(preview['after_original_expiry']['available'],'same original must retire without TTL renewal')
        self.assertEqual(captures,[3.09,3.59],'completion wake must never add a capture')
        self.assertEqual(projection_threads,[threading.get_ident()],'only the pump loop may project')


if __name__=='__main__':unittest.main(verbosity=2)

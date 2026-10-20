import threading
import unittest
import test_paired_runtime as fixtures
import test_analysis as analysis_fixtures
from analysis import SequentialAnalysis
from analysis_jobs import AnalysisJobs
from controller import Controller
from service_runtime import ServiceRuntime
from analysis_inputs import RegisteredAnalysisInputs


class AnalysisJobTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    settle=fixtures.PairedRuntimeTests.settle
    inputs=analysis_fixtures.AnalysisTests.inputs
    response=analysis_fixtures.AnalysisTests.response

    def test_fast_runtime_completion_is_not_overwritten_by_queued(self):
        class FastRuntime:
            def run_job(inner,job):
                self.store.update_job(job["id"],"completed",{"proof":"fixture"})
                return {"state":"queued"}
        controller=Controller(self.store,FastRuntime())
        result=controller.job({"kind":"analysis","request_id":"fast","session_id":self.session["id"]})
        self.assertEqual(result["state"],"completed")
        self.assertEqual(result["result"],{"proof":"fixture"})

    def test_registered_loader_rejects_unmapped_frames_and_excludes_labels(self):
        loader=RegisteredAnalysisInputs(self.store)
        with self.assertRaisesRegex(ValueError,"verified clock"):
            loader({"session_id":self.session["id"]})
        meta={"site_id":"victoria","camera_id":"den","stream":"rgb","capture_epoch":"e1","frame_id":"f1",
              "observed_at":102,"available_at":103,"uncertainty_seconds":.01,"clock_mapping_id":"mapping1","clock_mapping_status":"verified"}
        self.store.add_media(self.session["id"],b"fixture rgb","image/jpeg",meta)
        job,_=self.inputs()
        self.store.append_event(job["session_id"],"annotation",{**meta,"label":"secret truth"})
        self.store.append_event(job["session_id"],"perception.output",{**meta,"status":"completed","valid_until":110,"arm":"a","generation":1,"result":{"objects":["book"],"human_label":"secret truth"}})
        frames,observations=loader(job)
        self.assertEqual(len(frames),1)
        self.assertEqual(len(observations),1)
        self.assertNotIn("human_label",observations[0])

    def test_background_analysis_publishes_durable_completed_job(self):
        job,frames=self.inputs()
        analysis=SequentialAnalysis(self.store,self.profiles["a"],self.response)
        jobs=AnalysisJobs(self.store,analysis,lambda job:(frames,[]))
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None,analysis_jobs=jobs)
        controller=Controller(self.store,runtime)
        try:
            result=controller.job({"kind":"analysis","request_id":"async","session_id":self.session["id"],"parameters":{"mode":"causal"}})
            self.settle(lambda:self.store.get_job(result["id"])["state"]=="completed")
            self.assertEqual(len(controller.detail(self.session["id"])["outputs"]),6)
        finally:
            jobs.close()

    def test_missing_inputs_fail_without_dispatch_or_permanent_deletion_block(self):
        job,_=self.inputs()
        calls=[]
        analysis=SequentialAnalysis(self.store,self.profiles["a"],lambda r:calls.append(r))
        def missing(_): raise ValueError("private media path must not reach the UI")
        jobs=AnalysisJobs(self.store,analysis,missing)
        try:
            jobs.submit(job)
            self.settle(lambda:self.store.get_job(job["id"])["state"]=="failed")
            result=self.store.get_job(job["id"])
            self.assertEqual(result["result"]["reason"],"analysis_inputs_unavailable")
            self.assertFalse(result["result"]["dispatched"])
            self.assertEqual(calls,[])
            self.assertIsNone(self.store.db.execute("SELECT 1 FROM lab_analysis_steps").fetchone())
            session=self.store.get_session(self.session["id"])
            self.store.delete_session(session["id"],session["revision"])
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)
            jobs.pool.shutdown(wait=True)

    def test_cancel_waits_for_inflight_step_and_queue_is_bounded(self):
        _,frames=self.inputs()
        started=threading.Event()
        release=threading.Event()
        calls=[]
        def execute(request):
            calls.append(request)
            started.set()
            if not release.wait(2):
                raise TimeoutError()
            return self.response(request)
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],execute),lambda job:(frames,[]))
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None,analysis_jobs=jobs)
        controller=Controller(self.store,runtime)
        try:
            first=controller.job({"kind":"analysis","request_id":"first","session_id":self.session["id"]})
            self.assertTrue(started.wait(1))
            second=controller.job({"kind":"analysis","request_id":"second","session_id":self.session["id"]})
            third=controller.job({"kind":"analysis","request_id":"third","session_id":self.session["id"]})
            self.assertEqual(third["state"],"blocked")
            self.assertEqual(controller.cancel_job(second["id"])["state"],"cancelled")
            self.assertEqual(controller.cancel_job(first["id"])["state"],"indeterminate")
            release.set()
            self.settle(lambda:self.store.get_job(first["id"])["state"]=="cancelled")
            self.assertEqual(len(calls),1)
        finally:
            release.set()
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_job_recovery_reports_interrupted_without_resuming(self):
        job,frames=self.inputs()
        calls=[]
        def timeout(request):
            calls.append(request)
            raise TimeoutError()
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],timeout),lambda _:(frames,[]))
        try:
            jobs.submit(job)
            self.settle(lambda:jobs.work[job["id"]][0].done())
            self.assertEqual(self.store.get_job(job["id"])["state"],"indeterminate")
            self.assertEqual(jobs.reconcile(job["id"],lambda r,_:self.response(r))["state"],"reconciled")
            current=self.store.get_job(job["id"])
            self.assertEqual(current["state"],"interrupted")
            self.assertFalse(current["result"]["resumed"])
            self.assertEqual(len(calls),1)
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)


    def run_to_end(self, jobs, job, state):
        jobs.submit(job)
        self.settle(lambda:self.store.get_job(job["id"])["state"]==state)
        return self.store.get_job(job["id"])

    def test_plan_refusal_and_terminal_admission_failure_are_known_job_failures(self):
        job,frames=self.inputs()
        calls=[]
        def execute(request):
            calls.append(request)
            return {"request_id":request["request_id"],"terminal":True,"state":"queue_expired","reason":None,
                    "engine_epoch":None,"artifact_sha256":None,"result":None}
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],execute),lambda _:(frames,[]))
        try:
            qjev,_=self.store.create_job("analysis","with-qjev",{"run_ids":["qBase","qJev"]},self.session["id"])
            result=self.run_to_end(jobs,qjev,"failed")
            self.assertEqual((result["error"],result["result"]["dispatched"]),("qwen_assisted_jev_not_implemented",False))
            self.assertEqual(calls,[])
            result=self.run_to_end(jobs,job,"failed")
            self.assertEqual((result["error"],result["result"]["dispatched"]),("analysis_admission_queue_expired",True))
            self.assertEqual(len(calls),1)
            # Known failures never block cancellation or deletion.
            session=self.store.get_session(self.session["id"])
            self.store.delete_session(session["id"],session["revision"])
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_live_gate_interrupts_before_dispatch_and_progress_is_visible_while_running(self):
        job,frames=self.inputs()
        gate=["paired_recording_active"]
        seen=[]
        def execute(request):
            seen.append(self.store.get_job(request["job_id"])["result"])
            return self.response(request)
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],execute),lambda _:(frames,[]),gate=lambda:gate[0])
        try:
            result=self.run_to_end(jobs,job,"interrupted")
            self.assertEqual((result["error"],result["result"]["dispatched"]),("paired_recording_active",False))
            self.assertEqual(seen,[])
            gate[0]=None
            second,_=self.store.create_job("analysis","after-recording",{},self.session["id"])
            result=self.run_to_end(jobs,second,"completed")
            self.assertEqual(result["result"]["steps_done"],6)
            self.assertEqual(seen[0]["progress"],{"steps_total":6,"steps_done":0,"dispatched":False,"reused":0,"run_id":"qBase","window_cutoff_s":5})
            self.assertEqual(seen[-1]["progress"]["steps_done"],5)
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_startup_and_job_start_reconcile_by_lookup_without_resending(self):
        job,frames=self.inputs()
        calls=[]
        def timeout(request):
            calls.append(request)
            raise TimeoutError()
        SequentialAnalysis(self.store,self.profiles["a"],timeout).run(job,frames,[])
        self.store.update_job(job["id"],"indeterminate",{"state":"indeterminate"},"analysis_completion_unproven")
        lookups=[]
        def lookup(request,reference):
            lookups.append(request["request_id"])
            return self.response(request)
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],self.response),lambda _:(frames,[]),lookup=lookup)
        try:
            self.assertTrue(jobs.start_reconcile())
            self.settle(lambda:self.store.get_job(job["id"])["state"]=="interrupted")
            self.assertEqual(self.store.get_job(job["id"])["error"],"completion_recovered_explicit_resume_required")
            self.assertEqual(len(lookups),1)
            self.assertEqual(len(calls),1)
            second,_=self.store.create_job("analysis","after-recovery",{},self.session["id"])
            self.assertEqual(self.run_to_end(jobs,second,"completed")["result"]["steps_done"],6)
            self.assertEqual(len(lookups),1)
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_unresolved_uncertainty_fails_the_next_job_without_dispatch(self):
        job,frames=self.inputs()
        def timeout(_): raise TimeoutError()
        SequentialAnalysis(self.store,self.profiles["a"],timeout).run(job,frames,[])
        calls=[]
        def lookup(request,reference):
            raise TimeoutError()
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],calls.append),lambda _:(frames,[]),lookup=lookup)
        try:
            second,_=self.store.create_job("analysis","blocked-by-uncertainty",{},self.session["id"])
            result=self.run_to_end(jobs,second,"failed")
            self.assertEqual((result["error"],result["result"]["dispatched"]),("analysis_reconciliation_pending",False))
            self.assertEqual(calls,[])
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_operator_maintenance_job_reconciles_by_lookup_only(self):
        job,frames=self.inputs()
        def timeout(_): raise TimeoutError()
        SequentialAnalysis(self.store,self.profiles["a"],timeout).run(job,frames,[])
        self.store.update_job(job["id"],"indeterminate",{"state":"indeterminate"},"analysis_completion_unproven")
        def lookup(request,reference):
            return {"request_id":request["request_id"],"terminal":True,"state":"failed","reason":"engine_stopped",
                    "engine_epoch":"engine1","artifact_sha256":"a"*64,"result":None}
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],timeout),lambda _:(frames,[]),lookup=lookup)
        try:
            maintenance,_=self.store.create_job("diagnostic","reconcile-1",{"operation":"analysis_reconcile","job_id":job["id"]})
            self.assertEqual(jobs.submit_maintenance(maintenance,job["id"]),{"state":"queued"})
            self.settle(lambda:self.store.get_job(maintenance["id"])["state"]=="completed")
            result=self.store.get_job(maintenance["id"])["result"]
            self.assertEqual(result["results"][job["id"]]["outcomes"],{"failed":1})
            self.assertFalse(result["resumed"])
            self.assertEqual((self.store.get_job(job["id"])["state"],self.store.get_job(job["id"])["error"]),("failed","analysis_step_failed"))
            wrong,_=self.store.create_job("diagnostic","reconcile-2",{"operation":"analysis_reconcile","job_id":maintenance["id"]})
            jobs.submit_maintenance(wrong,maintenance["id"])
            self.settle(lambda:self.store.get_job(wrong["id"])["state"]=="failed")
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_close_reports_an_inflight_step_as_not_drained(self):
        job,frames=self.inputs()
        started,release=threading.Event(),threading.Event()
        def execute(request):
            started.set()
            release.wait(2)
            return self.response(request)
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],execute),lambda _:(frames,[]))
        try:
            jobs.submit(job)
            self.assertTrue(started.wait(1))
            self.assertTrue(jobs.active(self.session["id"]))
            self.assertFalse(jobs.close())
            release.set()
            self.assertTrue(jobs.close(timeout=2))
            self.assertEqual(self.store.get_job(job["id"])["state"],"cancelled")
        finally:
            release.set()
            jobs.pool.shutdown(wait=True)

    def test_bounded_close_stops_transport_polling_and_cancels_queued_lookups(self):
        job,frames=self.inputs()
        started,stopped=threading.Event(),threading.Event()
        def execute(request):
            started.set()
            if stopped.wait(5):
                raise TimeoutError()  # the transport's poll loop ended unproven
            return self.response(request)
        jobs=AnalysisJobs(self.store,SequentialAnalysis(self.store,self.profiles["a"],execute),lambda _:(frames,[]),
                          lookup=lambda request,reference:None,stop=stopped.set)
        try:
            jobs.submit(job)
            self.assertTrue(started.wait(2))
            self.assertTrue(jobs.start_reconcile())
            queued=next(iter(jobs.maintenance))
            self.assertFalse(jobs.close())
            self.assertTrue(queued.cancelled())
            self.assertFalse(stopped.is_set())  # an unbounded close never interrupts the step
            jobs.close(timeout=.05)
            self.assertTrue(stopped.is_set())
            self.settle(lambda:self.store.get_job(job["id"])["state"]=="indeterminate")
            self.assertEqual(self.store.db.execute("SELECT state FROM lab_analysis_steps").fetchone()[0],"indeterminate")
        finally:
            stopped.set()
            jobs.pool.shutdown(wait=True)

if __name__ == "__main__":
    unittest.main()

import unittest
import threading
import test_paired_runtime as fixtures
from service_runtime import ServiceRuntime


class CompositionTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    frame=fixtures.PairedRuntimeTests.frame
    receipt=fixtures.PairedRuntimeTests.receipt
    settle=fixtures.PairedRuntimeTests.settle

    def test_shutdown_waits_for_local_worker_without_releasing_unknown_execution(self):
        started=threading.Event()
        release=threading.Event()
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        def execute(request):
            started.set()
            release.wait(2)
            raise TimeoutError("fixture remote completion unknown")
        runtime.executor=execute
        runtime.start_session(self.session)
        self.runners.append(runtime.runners[self.session["id"]])
        runtime.offer(self.session["id"],self.frame())
        self.assertTrue(started.wait(1))
        self.assertFalse(runtime.close(timeout=0)["drained"])
        self.assertFalse(runtime.capabilities()["recording_allowed"])
        with self.assertRaises(ValueError):
            runtime.offer(self.session["id"],self.frame())
        release.set()
        self.assertTrue(runtime.close(timeout=2)["drained"])
        states=[r[0] for r in self.store.db.execute("SELECT state FROM paired_requests")]
        self.assertTrue(all(s=="indeterminate" for s in states))
        # Local thread completion makes SQLite safe to close, not remote work safe
        # to retry. Terminal proof is still absent and reservations remain.
        self.assertEqual(self.store.get_session(self.session["id"])["state"],"recording")

    def runtime(self,resolver):
        class Recorder:
            starts=0
            stops=0
            def start(inner,session):
                inner.starts+=1
                return {"state":"recording"}
            def stop(inner,session):
                inner.stops+=1
                return {"state":"stopped"}
        recorder=Recorder()
        runtime=ServiceRuntime(self.store,{"den":recorder},resolver,lambda request:self.receipt(request))
        return runtime,recorder

    def test_live_reports_server_time_and_per_arm_delivery_cadence(self):
        from controller import Controller
        now=[100.0];self.store.clock=lambda:now[0]
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        both=threading.Barrier(2,timeout=2)
        def execute(request):
            if request["frame"]["frame_id"]=="f3":
                both.wait();now[0]=104.5  # both arms dispatched, then completed late
            return self.receipt(request)
        runtime.executor=execute
        runtime.start_session(self.session)
        runner=runtime.runners[self.session["id"]];self.runners.append(runner)
        for frame_id,at,valid in (("f1",100.0,101.0),("f2",101.0,102.0),("f3",104.0,104.2)):
            now[0]=at
            runtime.offer(self.session["id"],self.frame(frame_id,observed_at=at-.4,available_at=at,valid_until=valid))
            self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        now[0]=105.0
        live=Controller(self.store,runtime).live(self.session["id"])
        self.assertEqual(live["server_time"],105.0)
        self.assertIsNone(live["acquisition"])
        for arm in ("A","B"):
            delivery=live["arms"][arm]["delivery"]
            self.assertAlmostEqual(delivery.pop("source_age_s"),1.4)
            self.assertAlmostEqual(delivery.pop("output_age_s"),.5)
            self.assertEqual(delivery,{"fresh_10s":2,"fresh_30s":2,"completed_30s":3,"max_gap_s":3.5,
                "last_offered_at":104.0,"last_dispatched_at":104.0,"last_completed_at":104.5})
            # Freshness semantics are unchanged: the late result is shown stale.
            self.assertEqual(live["arms"][arm]["status"],"stale")
        runtime.close()

    def test_caption_pump_is_recording_owned_guarded_and_never_requests_inference(self):
        import time
        from dataclasses import replace
        from caption_history import CaptionHistory
        from store import LabError
        self.store.settings=replace(self.store.settings,cameras=({"id":"den"},))
        reads=[]
        def reader(path,site,camera,now):
            reads.append(now)
            return dict(status="current",site_id="victoria",camera_id="den",processing_site_id="echo",model="qwen3-vl-30b",
                request_id="r",frame_id="f",observed_at=95,generated_at=96,valid_until=200,timestamp_semantics="fixture",
                uncertainty_seconds=0,text="A person is reading a book.")
        history=CaptionHistory(self.store,"unused","den",reader=reader)
        for invalid in ({"kitchen":history},{"den":object()}):
            with self.assertRaises(ValueError):ServiceRuntime(self.store,{},None,None,caption_sources=invalid)
        runtime,_=self.runtime(lambda *_:{"state":"blocked"})
        runtime=ServiceRuntime(self.store,runtime.recorders,runtime.resolve_profiles,None,caption_sources={"den":history})
        self.assertEqual(runtime.live(self.session)["caption_status"],"not_provisioned")
        runtime.start_session(self.session)
        pump=runtime.caption_pumps[self.session["id"]]
        try:
            deadline=time.monotonic()+10
            while runtime.caption_states.get(self.session["id"])!="available":
                if time.monotonic()>deadline:self.fail("caption pump did not poll")
                time.sleep(.02)
            live=runtime.live(self.session)
            self.assertEqual((live["caption_status"],live["caption_poll_state"]),("current","available"))
            with self.assertRaisesRegex(LabError,"source reader still active"):
                runtime.delete_session(self.store.get_session(self.session["id"]),1)
            # Recorder-owned termination stops polling without any UI.
            self.store.transition(self.session["id"],"stopping")
            self.assertEqual(runtime.reap_finished_sessions()["closed_sessions"],[self.session["id"]])
            pump.thread.join(5)
            self.assertFalse(pump.thread.is_alive())
            count=len(reads)
            self.assertEqual(len([e for e in self.store.list_events(self.session["id"]) if e["kind"]=="caption.receipt"]),1)
        finally:
            self.assertTrue(runtime.close(timeout=5)["drained"])
        self.assertEqual(len(reads),count)

    def test_live_without_admitted_pair_still_reports_server_time(self):
        runtime,_=self.runtime(lambda *_:{"state":"blocked"})
        runtime.start_session(self.session)
        live=runtime.live(self.session)
        self.assertEqual((live["state"],live["server_time"],live["acquisition"]),("unavailable",100.0,None))

    def test_capture_continues_when_optional_pair_not_admitted(self):
        runtime,recorder=self.runtime(lambda *_:{"state":"blocked"})
        result=runtime.start_session(self.session)
        self.assertEqual(result["state"],"recording")
        self.assertEqual(result["inference"],"unavailable")
        self.assertEqual(runtime.offer(self.session["id"],self.frame())["state"],"unavailable")
        self.assertEqual(runtime.stop_session(self.session)["state"],"stopped")
        self.assertEqual((recorder.starts,recorder.stops),(1,1))

    def test_source_owned_stop_closes_pending_work_without_ui_or_losing_reservation(self):
        from controller import Controller
        release=threading.Event();calls=[]
        runtime,recorder=self.runtime(lambda *_:{'state':'ready','profiles':self.profiles})
        def execute(request):
            calls.append(request)
            release.wait(2)
            return self.receipt(request)
        runtime.executor=execute
        runtime.start_session(self.session)
        runner=runtime.runners[self.session['id']];self.runners.append(runner)
        try:
            runtime.offer(self.session['id'],self.frame('active'))
            self.settle(lambda:len(calls)==2)
            runtime.offer(self.session['id'],self.frame('pending'))
            self.store.transition(self.session['id'],'stopped','source_ended')
            Controller(self.store,runtime).expire()
            self.assertTrue(runner.snapshot()['closed'])
            self.assertTrue(all(runner.snapshot()['active'].values()))
            self.assertEqual(recorder.stops,0)
            release.set()
            self.settle(lambda:all(v is None for v in runner.snapshot()['active'].values()))
            self.assertEqual(len(calls),2)
            deliveries=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='perception.delivery']
            self.assertTrue(all(not value['current'] for value in deliveries))
        finally:
            release.set();runtime.close()

    def test_registered_pair_executes_and_unbound_switch_preserves_active_pair(self):
        import json
        from pathlib import Path
        from profile_resolver import RegisteredPairs,recipe_digest
        recipe=json.loads(Path(__file__).with_name("experiments.json").read_text())["experiments"][0]
        binding={"recipe_sha256":recipe_digest(recipe),"profiles":self.profiles}
        resolver=RegisteredPairs(site_id="victoria",camera_ids=["den"],bindings={recipe["id"]:binding},
            readiness=lambda *_:{"state":"ready",**binding})
        runtime,recorder=self.runtime(resolver)
        session={**self.session,"experiment_id":recipe["id"]}
        result=runtime.start_session(session)
        self.assertEqual(result["inference"],"ready")
        old=runtime.runners[session["id"]];self.runners.append(old)
        runtime.offer(session["id"],self.frame())
        self.settle(lambda:all(v is None for v in old.snapshot()["active"].values()))
        other=next(key for key in resolver.recipes if key!=recipe["id"])
        result=runtime.switch(session,{"experiment_id":other,"request_id":"unbound"})
        self.assertEqual(result,{"state":"blocked","reason":"experiment_pair_not_provisioned"})
        self.assertIs(runtime.runners[session["id"]],old)
        self.assertFalse(old.snapshot()["closed"])
        self.assertEqual(recorder.starts,1)
        runtime.close()

    def test_pair_switch_uses_new_identity_without_invalidating_recorded_media(self):
        runtime,recorder=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        runtime.start_session(self.session)
        self.runners.append(runtime.runners[self.session["id"]])
        runtime.offer(self.session["id"],self.frame())
        self.settle(lambda: all(v is None for v in self.runners[-1].snapshot()["active"].values()))
        result=runtime.switch(self.session,{"experiment_id":"pose-added","request_id":"switch1"})
        self.assertEqual(result["state"],"applied")
        self.runners.append(runtime.runners[self.session["id"]])
        runtime.offer(self.session["id"],self.frame())
        self.settle(lambda: all(v is None for v in self.runners[-1].snapshot()["active"].values()))
        outputs=[e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
        self.assertEqual(len(outputs),4)
        self.assertEqual({e["payload"]["generation"] for e in outputs},{1,2})
        self.assertEqual(self.store.get_session(self.session["id"])["generation"],0)
        self.assertEqual(recorder.starts,1)

        history=[e["payload"] for e in self.store.list_events(self.session["id"])
                 if e["kind"]=="experiment.activated"]
        self.assertEqual([e["generation"] for e in history],[1,2])
        self.assertEqual(history[1]["experiment_id"],"pose-added")
        self.assertEqual(history[1]["profiles"]["a"]["artifact_sha256"],"a"*64)

    def test_unadmitted_switch_preserves_current_pair(self):
        ready={"state":"ready","profiles":self.profiles}
        runtime,_=self.runtime(lambda _,experiment:ready if experiment=="detection" else {"state":"blocked"})
        runtime.start_session(self.session)
        old=runtime.runners[self.session["id"]]
        self.runners.append(old)
        outcome=runtime.switch(self.session,{"experiment_id":"unknown","request_id":"s1"})
        self.assertEqual(outcome["state"],"blocked")
        self.assertFalse(old.closed)
        self.assertIs(runtime.runners[self.session["id"]],old)

    def test_malformed_replacement_does_not_close_old_pair_or_advance_generation(self):
        runtime,recorder=self.runtime(lambda _,experiment:{"state":"ready","profiles":self.profiles if experiment=="detection" else {"a":{}}})
        runtime.start_session(self.session)
        old=runtime.runners[self.session["id"]]
        self.runners.append(old)
        revision=self.store.get_session(self.session["id"])["revision"]
        outcome=runtime.switch(self.session,{"experiment_id":"pose-added","request_id":"badpair"})
        self.assertEqual(outcome["state"],"blocked")
        self.assertFalse(old.closed)
        self.assertEqual(self.store.get_session(self.session["id"])["revision"],revision)
        self.assertEqual(self.store.db.execute("SELECT generation FROM lab_experiment_state").fetchone()[0],1)
        runtime.offer(self.session["id"],self.frame())
        self.settle(lambda:all(v is None for v in old.snapshot()["active"].values()))
        self.assertEqual(recorder.starts,1)

    def test_failed_revision_commit_preserves_old_pair(self):
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        runtime.start_session(self.session)
        old=runtime.runners[self.session["id"]]
        self.runners.append(old)
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_revision_failure BEFORE UPDATE OF experiment_id ON sessions BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        outcome=runtime.switch(self.session,{"experiment_id":"pose-added","request_id":"failedcommit"})
        self.assertEqual(outcome["state"],"blocked")
        self.assertFalse(old.closed)
        self.assertIs(runtime.runners[self.session["id"]],old)
        self.assertEqual(self.store.db.execute("SELECT generation FROM lab_experiment_state").fetchone()[0],1)
        self.assertEqual(self.store.get_session(self.session["id"])["experiment_id"],"detection")
        self.assertEqual(len([e for e in self.store.list_events(self.session["id"])
                             if e["kind"]=="experiment.activated"]),1)


if __name__ == "__main__":
    unittest.main()


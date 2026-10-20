import json
import unittest
import test_paired_runtime as fixtures
from analysis import SequentialAnalysis, NotDispatched, EngineEpochEnded
from controller import Controller
from analysis_result import render_result, SCHEMA_VERSION


class AnalysisTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def inputs(self):
        self.store.transition(self.session["id"],"stopped")
        with self.store.transaction() as db:
            db.execute("UPDATE sessions SET ended_at=110 WHERE id=?",(self.session["id"],))
        job,_=self.store.create_job("analysis","analysis1",{"mode":"causal"},self.session["id"])
        frames=[{"media_id":self.media["id"],"session_id":self.session["id"],"site_id":"victoria","observed_at":102,"available_at":102}]
        return job,frames

    def response(self,request):
        return {"request_id":request["request_id"],"terminal":True,"state":"completed","engine_epoch":"engine1","artifact_sha256":"a"*64,"result":{"activity":"unknown"}}

    def test_same_windows_sequential_runs_and_replay_does_not_repeat_inference(self):
        job,frames=self.inputs()
        calls=[]
        def execute(request):
            calls.append(request)
            return self.response(request)
        analysis=SequentialAnalysis(self.store,self.profiles["a"],execute)
        self.assertEqual(analysis.run(job,frames,[])["state"],"completed")
        self.assertEqual([r["run_id"] for r in calls],["qBase","qBase","qA","qA","qB","qB"])
        self.assertEqual(calls[0]["frames"],calls[2]["frames"])
        self.assertEqual(calls[0]["window"],calls[4]["window"])
        analysis.run(job,frames,[])
        self.assertEqual(len(calls),6)
        changed=analysis.run(job,[{**frames[0],"observed_at":103}],[])
        self.assertEqual((changed["state"],changed["reason"]),("failed","analysis_inputs_changed"))
        self.assertEqual(len(calls),6)

    def test_labels_other_arms_and_future_delivery_do_not_leak(self):
        job,frames=self.inputs()
        analysis=SequentialAnalysis(self.store,self.profiles["a"],self.response)
        base={"id":"obs","site_id":"victoria","session_id":self.session["id"],"arm":"a","generation":1,"observed_at":102,"available_at":103,"valid_until":120,"objects":["book"],"human_label":"private-truth"}
        records=[base,{**base,"id":"label","kind":"annotation"},{**base,"id":"late","available_at":108}]
        plan=analysis.plan(job,frames,records)
        self.assertEqual(plan[0]["evidence"],[])
        self.assertEqual([r["id"] for r in plan[2]["evidence"]],["obs"])
        self.assertNotIn("human_label",plan[2]["evidence"][0])
        self.assertEqual(plan[4]["evidence"],[])
        shared={**base,"arm":None,"ha":{"screen":"on"}}
        planned=analysis.plan(job,frames,[shared])
        self.assertEqual(planned[0]["ha"],planned[2]["ha"])
        self.assertEqual(planned[0]["ha"][0]["ha"],{"screen":"on"})
        self.assertNotIn("human_label",planned[0]["ha"][0])

    def test_recorded_ha_ablation_withholds_ha_from_every_saved_run(self):
        # Only specialist evidence may differ between qBase, qA and qB. An HA
        # ablation recorded for one live arm therefore withholds HA from all.
        self.store.append_event(self.session['id'],'experiment.activated',{
            'site_id':'victoria','camera_id':'den','generation':1,'effective_at':100,
            'experiment_id':'fixture-ablation','ha_evidence':{'a':False,'b':True}})
        job,frames=self.inputs()
        record={'id':'ha1','kind':'observation','site_id':'victoria','session_id':self.session['id'],
                'arm':None,'observed_at':102,'available_at':103,'valid_until':120,
                'ha':{'screen':'on'},'evidence_roots':['ha:source:1']}
        analysis=SequentialAnalysis(self.store,self.profiles['a'],self.response)
        plan=analysis.plan(job,frames,[record])
        self.assertEqual({r['run_id'] for r in plan},{'qBase','qA','qB'})
        for request in plan:
            self.assertEqual(request['ha'],[])
            self.assertFalse(request['ha_evidence_included'])
            self.assertEqual(request['ha_evidence_policy'],{'recorded':{'a':False,'b':True},'applied_to_every_run':False})
        self.assertEqual(plan[4]['frames'],plan[2]['frames'])

    def test_common_ha_policy_gives_baseline_and_arms_identical_ha(self):
        self.store.append_event(self.session['id'],'experiment.activated',{
            'site_id':'victoria','camera_id':'den','generation':1,'effective_at':100,
            'experiment_id':'fixture-common','ha_evidence':{'a':True,'b':True}})
        job,frames=self.inputs()
        record={'id':'ha1','kind':'observation','site_id':'victoria','session_id':self.session['id'],
                'arm':None,'observed_at':102,'available_at':103,'valid_until':120,
                'ha':{'screen':'on'},'evidence_roots':['ha:source:1']}
        plan=SequentialAnalysis(self.store,self.profiles['a'],self.response).plan(job,frames,[record])
        by_window={}
        for request in plan:
            by_window.setdefault(request['window']['cutoff'],{})[request['run_id']]=request
        for runs in by_window.values():
            self.assertEqual(runs['qBase']['ha'],runs['qA']['ha'])
            self.assertEqual(runs['qA']['ha'],runs['qB']['ha'])
            self.assertEqual(runs['qBase']['frames'],runs['qB']['frames'])
            self.assertTrue(all(r['ha_evidence_included'] for r in runs.values()))
        self.assertEqual(plan[0]['ha'][0]['evidence_roots'],['ha:source:1'])

    def test_uncertain_execution_stops_all_later_steps_and_survives_restart(self):
        job,frames=self.inputs()
        calls=[]
        def execute(request):
            calls.append(request)
            raise TimeoutError("unknown completion")
        analysis=SequentialAnalysis(self.store,self.profiles["a"],execute)
        self.assertEqual(analysis.run(job,frames,[])["state"],"indeterminate")
        restarted=SequentialAnalysis(self.store,self.profiles["a"],execute)
        self.assertEqual(restarted.run(job,frames,[])["state"],"indeterminate")
        self.assertEqual(len(calls),1)

    def test_recorded_arm_history_is_used_without_changing_shared_video_windows(self):
        job,frames=self.inputs()
        self.store.append_event(self.session['id'],'experiment.activated',{
            'site_id':'victoria','camera_id':'den','generation':1,'effective_at':100,
            'experiment_id':'history','reasoning_windows':{'a':0,'b':20}})
        records=[{'id':arm+str(t),'site_id':'victoria','session_id':self.session['id'],
                  'arm':arm,'generation':1,'observed_at':t,'available_at':t,'valid_until':120,
                  'objects':['book']} for arm in ('a','b') for t in (101,104)]
        analysis=SequentialAnalysis(self.store,self.profiles['a'],self.response)
        plan=analysis.plan(job,frames,records)
        a=next(r for r in plan if r['run_id']=='qA')
        b=next(r for r in plan if r['run_id']=='qB')
        self.assertEqual([r['id'] for r in a['evidence']],['a104'])
        self.assertEqual([r['id'] for r in b['evidence']],['b101','b104'])
        self.assertEqual(a['evidence_window_seconds'],0)
        self.assertEqual(b['evidence_window_seconds'],5)
        self.assertEqual(a['evidence_window_source'],'recorded_activation')
        self.assertEqual(a['frames'],b['frames'])
        self.assertEqual(a['window'],b['window'])
        self.assertEqual(plan[0]['evidence'],[])

    def test_switch_isolates_generation_clips_prediction_and_marks_replay_gap(self):
        job,frames=self.inputs()
        for generation,at in ((1,100),(2,107)):
            self.store.append_event(self.session["id"],"experiment.activated",{
                "site_id":"victoria","camera_id":"den","generation":generation,
                "effective_at":at,"experiment_id":"recipe-"+str(generation)})
        base={"site_id":"victoria","session_id":self.session["id"],"arm":"a",
            "generation":1,"observed_at":102,"available_at":103,"valid_until":120,"objects":["book"]}
        records=[{**base,"id":"old"},{**base,"id":"new","generation":2,"observed_at":108,"available_at":109},
                 {**base,"id":"late-old","observed_at":108,"available_at":109}]
        def execute(request):
            return {**self.response(request),"result":{"schema_version":SCHEMA_VERSION,
                "activity":"reading","evidence_refs":[frames[0]["media_id"]]}}
        analysis=SequentialAnalysis(self.store,self.profiles["a"],execute)
        plan=analysis.plan(job,frames,records)
        self.assertEqual([r["generation"] for r in plan],[1,2,1,2,1,2])
        self.assertEqual([r["id"] for r in plan[2]["evidence"]],["old"])
        self.assertEqual([r["id"] for r in plan[3]["evidence"]],["new"])
        self.assertEqual(plan[0]["frames"],plan[2]["frames"])
        self.assertEqual(plan[0]["judgment_end_s"],7)
        self.assertEqual(analysis.run(job,frames,records)["state"],"completed")
        at_six=self.store.outputs_at(self.session["id"],6000)
        self.assertTrue(all(e["payload"]["prediction"]["end"]==7 for e in at_six))
        self.assertTrue(all(e["payload"]["generation"]==1 for e in at_six))
        at_eight=self.store.outputs_at(self.session["id"],8000)
        self.assertTrue(all(e["payload"]["status"]=="stale" for e in at_eight))

    def test_switch_without_new_output_does_not_reuse_old_generation(self):
        job,frames=self.inputs()
        for generation,at in ((1,100),(2,104)):
            self.store.append_event(self.session["id"],"experiment.activated",{
                "site_id":"victoria","camera_id":"den","generation":generation,
                "effective_at":at,"experiment_id":"recipe"})
        old={"id":"old","site_id":"victoria","session_id":self.session["id"],"arm":"a",
            "generation":1,"observed_at":102,"available_at":103,"valid_until":120,"objects":["book"]}
        plan=SequentialAnalysis(self.store,self.profiles["a"],self.response).plan(job,frames,[old])
        self.assertEqual(plan[2]["generation"],2)
        self.assertEqual(plan[2]["evidence"],[])

    def test_multiple_legacy_generations_require_history_instead_of_guessing(self):
        job,frames=self.inputs()
        record={"site_id":"victoria","session_id":self.session["id"],"arm":"a","generation":1}
        analysis=SequentialAnalysis(self.store,self.profiles["a"],self.response)
        with self.assertRaisesRegex(ValueError,"activation history"):
            analysis.plan(job,frames,[record,{**record,"generation":2}])

    def test_typed_outputs_replay_score_after_cutoff_without_fake_confidence(self):
        job,frames=self.inputs()
        def execute(request):
            return {**self.response(request),"result":{"schema_version":SCHEMA_VERSION,
                "activity":"reading","evidence_refs":[frames[0]["media_id"]]}}
        analysis=SequentialAnalysis(self.store,self.profiles["a"],execute)
        self.assertEqual(analysis.run(job,frames,[])["state"],"completed")
        sid=self.session["id"]
        self.assertEqual(self.store.outputs_at(sid,4000),[])
        replay=self.store.outputs_at(sid,6000)
        self.assertEqual(len(replay),3)
        for event in replay:
            output=event["payload"]
            self.assertEqual(output["judgment"]["activity"],"reading")
            self.assertIsNone(output["judgment"]["confidence"])
            self.assertEqual(output["prediction"]["start"],5)
            self.assertEqual(output["prediction"]["end"],10)
        self.store.save_annotation(sid,"label",{"at_ms":0,"end_ms":10000,
            "label":"reading","origin":"human","reviewed":True},0,"owner")
        rows=Controller(self.store).findings(sid)["runs"]
        self.assertEqual(len(rows),3)
        for row in rows:
            self.assertEqual(row["metrics"]["coverage"],.5)
            self.assertEqual(row["metrics"]["exact_match_at_coverage"],1)
            self.assertEqual(row["metrics"]["latency_kind"],"offline_not_live")
        final=self.store.outputs_at(sid,10000)
        self.assertTrue(all("prediction" not in e["payload"] for e in final))

    def test_terminal_invalid_answer_does_not_reserve_engine_as_uncertain(self):
        job,frames=self.inputs()
        analysis=SequentialAnalysis(self.store,self.profiles["a"],self.response)
        self.assertEqual(analysis.run(job,frames,[])["state"],"completed")
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output"]
        self.assertEqual(len(outputs),6)
        self.assertTrue(all(e["status"]=="invalid_output" and "prediction" not in e for e in outputs))
        self.assertFalse(self.store.db.execute("SELECT 1 FROM lab_analysis_steps WHERE state='indeterminate'").fetchone())

    def test_result_schema_refuses_unknown_references_and_invented_confidence(self):
        job,frames=self.inputs()
        req=SequentialAnalysis(self.store,self.profiles["a"],self.response).plan(job,frames,[])[0]
        valid={"schema_version":SCHEMA_VERSION,"activity":"reading","evidence_refs":[frames[0]["media_id"]]}
        for invalid in ({**valid,"confidence":.99},{**valid,"evidence_refs":["human-label"]},
                        {**valid,"activity":"authorized_owner"},{**valid,"evidence_refs":[]}):
            self.assertEqual(render_result(req,invalid)["status"],"invalid_output")
        abstain=render_result(req,{**valid,"activity":"insufficient_evidence","evidence_refs":[]})
        self.assertEqual(abstain["status"],"abstain")
        self.assertEqual(abstain["prediction"]["activities"],[])

    def test_reconciliation_publishes_once_without_restarting_analysis(self):
        job,frames=self.inputs()
        calls=[]
        def uncertain(request):
            calls.append(request)
            raise TimeoutError()
        analysis=SequentialAnalysis(self.store,self.profiles["a"],uncertain)
        analysis.run(job,frames,[])
        self.assertEqual(analysis.reconcile(job["id"],lambda request,_reference:self.response(request)),
                         {"state":"reconciled","steps":1,"resumed":False,"outcomes":{"completed":1}})
        self.assertEqual(len(calls),1)
        self.assertEqual(analysis.reconcile(job["id"],lambda *_:self.fail("duplicate lookup"))["steps"],0)
        outputs=[e for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output"]
        self.assertEqual(len(outputs),1)
        analysis.executor=lambda r:(calls.append(r) or self.response(r))
        self.assertEqual(analysis.run(job,frames,[])["state"],"completed")
        self.assertEqual(len(calls),6)  # The uncertain step was never sent again.

    def test_reconciliation_unknown_wrong_engine_and_failed_commit_retain_reservation(self):
        job,frames=self.inputs()
        def uncertain(_): raise TimeoutError()
        analysis=SequentialAnalysis(self.store,self.profiles["a"],uncertain)
        analysis.run(job,frames,[])
        for lookup in (lambda *_:None,lambda r,_:{**self.response(r),"engine_epoch":"another"},
                       lambda r,_:{**self.response(r),"terminal":False,"state":"running"},
                       lambda r,_:{**self.response(r),"state":"exploded"}):
            self.assertEqual(analysis.reconcile(job["id"],lookup)["state"],"indeterminate")
        original=self.store._event
        def failed(*args,**kwargs): raise OSError("fixture disk failure")
        self.store._event=failed
        try:
            self.assertEqual(analysis.reconcile(job["id"],lambda r,_:self.response(r))["state"],"indeterminate")
        finally:
            self.store._event=original
        self.assertEqual(self.store.db.execute("SELECT state FROM lab_analysis_steps").fetchone()[0],"indeterminate")
        self.assertEqual(analysis.reconcile(job["id"],lambda r,_:self.response(r))["steps"],1)


    def terminal(self, request, state, reason=None):
        return {"request_id":request["request_id"],"terminal":True,"state":state,"engine_epoch":None,
                "artifact_sha256":None,"reason":reason,"result":None}

    def uncertain_step(self, job, frames):
        def uncertain(_): raise TimeoutError()
        self.assertEqual(SequentialAnalysis(self.store,self.profiles["a"],uncertain).run(job,frames,[])["state"],"indeterminate")

    def test_terminal_non_completed_proof_is_a_known_failure_that_releases_the_engine(self):
        _,frames=self.inputs()
        states=("rejected","failed","cancelled","queue_expired","not_admitted")
        for state in states:
            job,_=self.store.create_job("analysis","terminal-"+state,{"run_ids":["qBase"]},self.session["id"])
            calls=[]
            def execute(request):
                calls.append(request)
                return self.terminal(request,state,"capacity")
            result=SequentialAnalysis(self.store,self.profiles["a"],execute).run(job,frames,[])
            self.assertEqual((result["state"],result["reason"],result["detail"],result["dispatched"]),
                             ("failed","analysis_admission_"+state,"capacity",True))
            self.assertEqual(len(calls),1)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM lab_analysis_steps WHERE state IN ('dispatching','indeterminate')").fetchone())
        job,_=self.store.create_job("analysis","after-terminal",{"run_ids":["qBase"]},self.session["id"])
        self.assertEqual(SequentialAnalysis(self.store,self.profiles["a"],self.response).run(job,frames,[])["state"],"completed")
        failed=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output" and e["payload"]["status"]=="failed"]
        self.assertEqual({e["failure"]["state"] for e in failed},set(states))
        self.assertTrue(all("judgment" not in e and "prediction" not in e for e in failed))

    def test_unproven_and_unknown_terminal_states_stay_indeterminate(self):
        _,frames=self.inputs()
        proofs=(lambda r:{**self.terminal(r,"queue_expired"),"terminal":False},
                lambda r:self.terminal(r,"vanished"),
                lambda r:{**self.terminal(r,"rejected"),"request_id":"0"*64},
                lambda r:{**self.terminal(r,"rejected"),"reason":"Free text; not a code"})
        for index,proof in enumerate(proofs):
            job,_=self.store.create_job("analysis","unknown-%d"%index,{"run_ids":["qBase"]},self.session["id"])
            self.assertEqual(SequentialAnalysis(self.store,self.profiles["a"],proof).run(job,frames,[])["state"],"indeterminate")
            self.assertEqual(self.store.db.execute("SELECT state FROM lab_analysis_steps WHERE job_id=?",(job["id"],)).fetchone()[0],"indeterminate")
            with self.store.transaction() as db:  # fixture-only release between cases
                db.execute("UPDATE lab_analysis_steps SET state='fixture_released' WHERE job_id=?",(job["id"],))

    def test_plan_refusals_fail_without_dispatch_and_qjev_has_an_explicit_reason(self):
        _,frames=self.inputs()
        calls=[]
        analysis=SequentialAnalysis(self.store,self.profiles["a"],calls.append)
        cases={"qwen_assisted_jev_not_implemented":{"run_ids":["qBase","qA","qB","qJev"]},
               "invalid_analysis_runs":{"run_ids":["qC"]},
               "invalid_analysis_mode":{"mode":"future"},
               "analysis_parameters_invalid":{"artifact_ids":["model"]}}
        for reason,parameters in cases.items():
            job,_=self.store.create_job("analysis","plan-"+reason,parameters,self.session["id"])
            self.assertEqual(analysis.run(job,frames,[]),{"state":"failed","reason":reason,"dispatched":False})
        empty,_=self.store.create_job("analysis","plan-empty",{},self.session["id"])
        self.assertEqual(analysis.run(empty,[],[])["reason"],"analysis_window_without_frames")
        self.assertEqual(calls,[])
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM lab_analysis_steps").fetchone())

    def test_proven_non_dispatch_releases_the_step_and_later_jobs_run(self):
        job,frames=self.inputs()
        class Executor:
            def __init__(inner):
                inner.sent=[]
            def prepare(inner,request):
                return {"request_id":request["request_id"],"body":{"images":"fixture"},"body_sha256":"b"*64,"deadline":115}
            def send(inner,envelope):
                inner.sent.append(envelope)
                raise NotDispatched("profile_mismatch")
        executor=Executor()
        result=SequentialAnalysis(self.store,self.profiles["a"],executor).run(job,frames,[])
        self.assertEqual((result["state"],result["reason"],result["detail"],result["dispatched"]),
                         ("failed","analysis_step_not_dispatched","profile_mismatch",False))
        self.assertEqual(len(executor.sent),1)
        # Nothing left Victoria, so no step row remains to hold erasure.
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM lab_analysis_steps").fetchone())
        class Broken(Executor):
            def prepare(inner,request):
                raise NotDispatched("registered_rgb_integrity_failed")
        second,_=self.store.create_job("analysis","prepare-fails",{},self.session["id"])
        result=SequentialAnalysis(self.store,self.profiles["a"],Broken()).run(second,frames,[])
        self.assertEqual((result["reason"],result["detail"]),("analysis_step_not_dispatched","registered_rgb_integrity_failed"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM lab_analysis_steps WHERE job_id=?",(second["id"],)).fetchone()[0],0)
        third,_=self.store.create_job("analysis","after-not-dispatched",{},self.session["id"])
        self.assertEqual(SequentialAnalysis(self.store,self.profiles["a"],self.response).run(third,frames,[])["state"],"completed")

    def test_session_whose_only_analysis_was_never_sent_can_be_erased(self):
        job,frames=self.inputs()
        class Offline:
            def prepare(inner,request):
                return {"request_id":request["request_id"],"body":{},"body_sha256":"b"*64,"deadline":115}
            def send(inner,envelope):
                raise NotDispatched("transport_not_connected")
        result=SequentialAnalysis(self.store,self.profiles["a"],Offline()).run(job,frames,[])
        self.assertEqual((result["reason"],result["detail"],result["dispatched"]),
                         ("analysis_step_not_dispatched","transport_not_connected",False))
        self.store.update_job(job["id"],result["state"],result,result["reason"])
        self.store.delete_session(self.session["id"],self.store.get_session(self.session["id"])["revision"],erase_records=True)
        erased=self.store.db.execute("SELECT state FROM session_erasures WHERE session_id=?",(self.session["id"],)).fetchone()
        self.assertIsNotNone(erased)

    def test_another_jobs_unproven_step_fails_a_new_job_without_dispatch(self):
        job,frames=self.inputs()
        self.uncertain_step(job,frames)
        second,_=self.store.create_job("analysis","blocked-second",{},self.session["id"])
        calls=[]
        result=SequentialAnalysis(self.store,self.profiles["a"],calls.append).run(second,frames,[])
        self.assertEqual((result["state"],result["reason"],result["dispatched"]),("failed","analysis_reconciliation_pending",False))
        self.assertEqual(calls,[])

    def test_engine_restart_resolves_an_unproven_step_as_ended_unknown(self):
        job,frames=self.inputs()
        self.uncertain_step(job,frames)
        # The operator provisions the restarted engine; the step keeps its own epoch.
        rotated_profile={**self.profiles["a"],"engine_epoch":"engine2"}
        rotated=SequentialAnalysis(self.store,rotated_profile,lambda r:{**self.response(r),"engine_epoch":"engine2"})
        def same_epoch(request,reference):
            raise EngineEpochEnded("engine1")
        self.assertEqual(rotated.reconcile(job["id"],same_epoch)["state"],"indeterminate")
        seen=[]
        def lookup(request,reference):
            seen.append(request["profile"]["engine_epoch"])
            raise EngineEpochEnded("engine2")
        self.assertEqual(rotated.reconcile(job["id"],lookup),{"state":"reconciled","steps":1,"resumed":False,"outcomes":{"ended_unknown":1}})
        self.assertEqual(seen,["engine1"])
        state,receipt=self.store.db.execute("SELECT state,receipt FROM lab_analysis_steps").fetchone()
        self.assertEqual((state,json.loads(receipt)["engine_epoch_ended"],json.loads(receipt)["current_engine_epoch"]),
                         ("ended_unknown","engine1","engine2"))
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output"]
        self.assertEqual([(o["status"],o["failure"]["state"]) for o in outputs],[("unknown","engine_epoch_ended")])
        self.assertNotIn("judgment",outputs[0])
        second,_=self.store.create_job("analysis","after-rotation",{},self.session["id"])
        self.assertEqual(rotated.run(second,frames,[])["state"],"completed")

    def test_rotated_controller_profile_still_accepts_the_recorded_epochs_completion(self):
        job,frames=self.inputs()
        self.uncertain_step(job,frames)
        rotated=SequentialAnalysis(self.store,{**self.profiles["a"],"engine_epoch":"engine2"},self.response)
        self.assertEqual(rotated.reconcile(job["id"],lambda r,_:self.response(r))["outcomes"],{"completed":1})
        output=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output"][0]
        self.assertEqual(output["profile"]["engine_epoch"],"engine1")

    def test_live_work_gate_stops_before_the_next_step(self):
        job,frames=self.inputs()
        calls,blocked=[],[]
        def execute(request):
            calls.append(request)
            blocked.append("paired_recording_active")
            return self.response(request)
        result=SequentialAnalysis(self.store,self.profiles["a"],execute).run(job,frames,[],gate=lambda:blocked[0] if blocked else None)
        self.assertEqual((result["state"],result["reason"],result["dispatched"],result["steps_done"],result["resumed"]),
                         ("interrupted","paired_recording_active",True,1,False))
        self.assertEqual(len(calls),1)

    def test_progress_reports_each_step_and_the_final_summary(self):
        job,frames=self.inputs()
        seen=[]
        SequentialAnalysis(self.store,self.profiles["a"],self.response).run(job,frames,[],progress=seen.append)
        self.assertEqual([p.get("run_id") for p in seen[:-1]],["qBase","qBase","qA","qA","qB","qB"])
        self.assertEqual([p["steps_done"] for p in seen],[0,1,2,3,4,5,6])
        self.assertEqual(seen[-1],{"steps_total":6,"steps_done":6,"dispatched":True,"reused":0})

    def test_a_new_job_reuses_completed_windows_and_resends_only_the_rest(self):
        _,frames=self.inputs()
        first,_=self.store.create_job("analysis","reuse-first",{"run_ids":["qBase"]},self.session["id"])
        refused,_=self.store.create_job("analysis","reuse-refused",{"run_ids":["qA"]},self.session["id"])
        calls=[]
        def execute(request):
            calls.append(request)
            return self.response(request)
        analysis=SequentialAnalysis(self.store,self.profiles["a"],execute)
        self.assertEqual(analysis.run(first,frames,[])["state"],"completed")
        result=SequentialAnalysis(self.store,self.profiles["a"],lambda r:self.terminal(r,"rejected","capacity")).run(refused,frames,[])
        self.assertEqual(result["reason"],"analysis_admission_rejected")
        calls.clear()
        # The engine restarted in between: its instance is not part of the window.
        rotated=SequentialAnalysis(self.store,{**self.profiles["a"],"engine_epoch":"engine2"},
                                   lambda r:calls.append(r) or {**self.response(r),"engine_epoch":"engine2"})
        second,_=self.store.create_job("analysis","reuse-second",{},self.session["id"])
        result=rotated.run(second,frames,[])
        self.assertEqual((result["state"],result["steps_done"],result["reused"],result["dispatched"]),("completed",6,2,True))
        # Completed qBase windows are not resent; the refused qA window is asked again under a new request id.
        self.assertEqual([r["run_id"] for r in calls],["qA","qA","qB","qB"])
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output" and e["payload"]["job_id"]==second["id"]]
        reused=[o for o in outputs if "reused_from" in o]
        self.assertEqual(sorted(o["run_id"] for o in outputs),["qA","qA","qB","qB","qBase","qBase"])
        self.assertEqual([(o["run_id"],o["reused_from"]["job_id"],o["profile"]["engine_epoch"]) for o in reused],
                         [("qBase",first["id"],"engine1")]*2)
        original=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output" and e["payload"]["job_id"]==first["id"]]
        strip=lambda o:{k:v for k,v in o.items() if k not in {"job_id","request_id","processing_completed_at","reused_from"}}
        self.assertEqual([strip(o) for o in reused],[strip(o) for o in original])
        # Re-running the finished job counts its reused steps as done.
        self.assertEqual(rotated.run(second,frames,[])["steps_done"],6)
        self.assertEqual(len(calls),4)

    def test_requests_are_bound_to_the_current_engine_epoch_from_the_executor(self):
        job,frames=self.inputs()
        class Executor:
            def __init__(inner,epoch):
                inner.epoch,inner.sent=epoch,[]
            def current_profile(inner,request):
                if inner.epoch is None:
                    raise NotDispatched("qwen_engine_epoch_unavailable")
                return {**request["profile"],"engine_epoch":inner.epoch}
            def prepare(inner,request):
                return {"request_id":request["request_id"],"body":{},"body_sha256":"b"*64,"profile":request["profile"]}
            def send(inner,envelope):
                inner.sent.append(envelope)
                return {"request_id":envelope["request_id"],"terminal":True,"state":"completed","engine_epoch":inner.epoch,
                        "artifact_sha256":"a"*64,"result":None}
        offline=Executor(None)
        result=SequentialAnalysis(self.store,self.profiles["a"],offline).run(job,frames,[])
        self.assertEqual((result["state"],result["reason"],result["detail"],result["dispatched"]),
                         ("failed","analysis_step_not_dispatched","qwen_engine_epoch_unavailable",False))
        self.assertEqual(offline.sent,[])
        second,_=self.store.create_job("analysis","current-epoch",{},self.session["id"])
        executor=Executor("engine7")
        self.assertEqual(SequentialAnalysis(self.store,self.profiles["a"],executor).run(second,frames,[])["state"],"completed")
        self.assertEqual({e["profile"]["engine_epoch"] for e in executor.sent},{"engine7"})
        rows=self.store.db.execute("SELECT id,engine,request FROM lab_analysis_steps WHERE job_id=?",(second["id"],)).fetchall()
        from reasoning import digest
        for request_id,engine,request in rows:
            request=json.loads(request)
            self.assertEqual((engine,request["profile"]["engine_epoch"]),("engine7","engine7"))
            self.assertEqual(request_id,digest({k:v for k,v in request.items() if k!="request_id"}))

if __name__ == "__main__":
    unittest.main()

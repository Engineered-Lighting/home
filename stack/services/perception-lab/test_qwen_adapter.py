import base64
import io
import json
import unittest
from analysis import SequentialAnalysis, NotDispatched
from analysis_inputs import RegisteredAnalysisInputs
from analysis_result import SCHEMA_VERSION
from qwen_adapter import QwenAdapter, RegisteredAnalysisMedia, MODEL, PROMPT_VERSION, MAX_OUTPUT_TOKENS
from reasoning import digest
import test_registered_image as fixtures


class QwenTests(unittest.TestCase):
    setUp = fixtures.RegisteredImageTests.setUp
    tearDown = fixtures.RegisteredImageTests.tearDown
    image = fixtures.RegisteredImageTests.image

    def frame(self):
        return {"session_id":self.session["id"], "site_id":"victoria", "camera_id":"den",
            "frame_id":"source1", "capture_epoch":"capture1", "observed_at":102}

    def jpeg(self, **changes):
        from PIL import Image
        encoded=io.BytesIO()
        Image.new("RGB",(64,36),(200,30,10)).save(encoded,format="JPEG",quality=90)
        frame=self.frame()
        meta={k:frame[k] for k in ("site_id","camera_id","frame_id","capture_epoch","observed_at")}
        item=self.store.add_media(self.session["id"],encoded.getvalue(),"image/jpeg",dict(meta,stream="rgb",width=64,height=36,**changes))
        return item,encoded.getvalue()

    def prepare(self):
        _,self.stored=self.jpeg(available_at=103, clock_mapping_status="verified", clock_mapping_id="fixture",
            uncertainty_seconds=.01)
        self.store.transition(self.session["id"],"stopped")
        with self.store.transaction() as db:
            db.execute("UPDATE sessions SET ended_at=110 WHERE id=?",(self.session["id"],))
        job,_=self.store.create_job("analysis","qwen-fixture",{},self.session["id"])
        frames,observations=RegisteredAnalysisInputs(self.store)(job)
        self.profile={"model_version":MODEL, "served_model":"qwen3-vl-30b", "artifact_sha256":"a"*64,
            "engine_epoch":"fixture1", "runtime_revision":"fixture-runtime", "adapter_revision":PROMPT_VERSION}
        return job,frames,observations

    def proof(self, envelope, **changes):
        parts=envelope["body"]["messages"][1]["content"]
        frame=json.loads(parts[1]["text"])
        answer={"schema_version":SCHEMA_VERSION,"activity":"reading","evidence_refs":[frame["media_id"]]}
        return {"request_id":envelope["request_id"],"body_sha256":envelope["body_sha256"],
            "terminal":True,"state":"completed","engine_epoch":"fixture1","artifact_sha256":"a"*64,
            "response":{"model":"qwen3-vl-30b","choices":[{"finish_reason":"stop",
                "message":{"role":"assistant","content":json.dumps(answer)}}]},**changes}

    def test_stored_jpeg_is_sent_unchanged_to_all_three_saved_runs(self):
        job,frames,obs=self.prepare()
        calls=[]
        def transport(envelope):
            calls.append(envelope)
            return self.proof(envelope,usage={"prompt_tokens":900,"completion_tokens":30})
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),transport,clock=lambda:500.0,deadline_seconds=12)
        self.assertEqual(calls,[])
        result=SequentialAnalysis(self.store,self.profile,adapter).run(job,frames,obs)
        self.assertEqual(result["state"],"completed")
        self.assertEqual(len(calls),6)
        self.assertEqual(calls[0]["body"],calls[2]["body"])
        self.assertEqual(calls[0]["body"],calls[4]["body"])
        for call in calls:
            body=call["body"]
            self.assertFalse(body["stream"])
            self.assertEqual(body["max_tokens"],MAX_OUTPUT_TOKENS)
            self.assertEqual((call["deadline"],call["task"],call["prompt_version"]),(512.0,"lab_saved_activity",PROMPT_VERSION))
            self.assertEqual(call["body_sha256"],digest(body))
            url=body["messages"][1]["content"][2]["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/jpeg;base64,"))
            self.assertEqual(base64.b64decode(url.split(",",1)[1]),self.stored)
        outputs=self.store.outputs_at(self.session["id"],6000)
        self.assertTrue(all(e["payload"]["judgment"]["activity"]=="reading" for e in outputs))
        self.assertTrue(all(e["payload"]["processing"]["prompt_version"]==PROMPT_VERSION for e in outputs))
        self.assertTrue(all(e["payload"]["processing"]["usage"]=={"prompt_tokens":900,"completion_tokens":30} for e in outputs))
        self.assertTrue(all(e["payload"]["processing"]["image_encoding"]=="stored_jpeg_integrity_checked" for e in outputs))
        # Stored results replay without any further transport call.
        self.store.outputs_at(self.session["id"],9000)
        self.assertEqual(len(calls),6)

    def test_png_samples_are_not_selected_or_sent(self):
        frame,item=self.image(available_at=103, clock_mapping_status="verified", clock_mapping_id="fixture",
            uncertainty_seconds=.01)
        self.store.transition(self.session["id"],"stopped")
        job,_=self.store.create_job("analysis","png-only",{},self.session["id"])
        with self.assertRaisesRegex(ValueError,"JPEG"):
            RegisteredAnalysisInputs(self.store)(job)
        request={"session_id":self.session["id"],"site_id":"victoria"}
        with self.assertRaises(NotDispatched) as refused:
            RegisteredAnalysisMedia(self.store)(request,{"media_id":item["id"],"observed_at":102,"available_at":103})
        self.assertEqual(refused.exception.reason,"stored_rgb_jpeg_required")

    def test_human_labels_other_arm_and_caption_context_never_enter_prompt(self):
        job,frames,obs=self.prepare()
        record={"id":"evidence1","site_id":"victoria","session_id":self.session["id"],
            "arm":"a","generation":1,"observed_at":102,"available_at":103,"valid_until":120,
            "objects":["book"],"human_label":"secret human answer","historical_caption":{"activity":"reading"}}
        calls=[]
        def transport(envelope):
            calls.append(envelope)
            return self.proof(envelope)
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),transport)
        SequentialAnalysis(self.store,self.profile,adapter).run(job,frames,[record])
        self.assertEqual(len(calls),6)
        for call in calls:
            self.assertNotIn("secret human answer",json.dumps(call))
            self.assertNotIn("historical_caption",json.dumps(call))
        evidence=lambda i:json.loads(calls[i]["body"]["messages"][1]["content"][0]["text"])["observations"]
        self.assertEqual(evidence(0),[])
        self.assertEqual(evidence(2)[0]["objects"],["book"])
        self.assertEqual(evidence(4),[])

    def test_unproven_transport_receipt_stays_reserved(self):
        job,frames,obs=self.prepare()
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),lambda e:self.proof(e,body_sha256="wrong"))
        result=SequentialAnalysis(self.store,self.profile,adapter).run(job,frames,obs)
        self.assertEqual(result["state"],"indeterminate")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM lab_analysis_steps WHERE state='indeterminate'").fetchone()[0],1)

    def test_terminal_admission_failure_releases_the_step(self):
        job,frames,obs=self.prepare()
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),
            lambda e:self.proof(e,state="queue_expired",response=None,engine_epoch=None,artifact_sha256=None,reason="Not A Code!"))
        result=SequentialAnalysis(self.store,self.profile,adapter).run(job,frames,obs)
        self.assertEqual((result["state"],result["reason"],result["detail"]),("failed","analysis_admission_queue_expired",None))
        self.assertEqual(self.store.db.execute("SELECT state FROM lab_analysis_steps").fetchone()[0],"failed")

    def test_truncated_answer_has_terminal_but_invalid_output(self):
        job,frames,obs=self.prepare()
        def transport(envelope):
            proof=self.proof(envelope)
            proof["response"]["choices"][0]["finish_reason"]="length"
            return proof
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),transport)
        self.assertEqual(SequentialAnalysis(self.store,self.profile,adapter).run(job,frames,obs)["state"],"completed")
        self.assertTrue(all(e["payload"]["status"]=="invalid_output" for e in self.store.outputs_at(self.session["id"],6000)))

    def test_mismatched_model_and_request_do_not_dispatch(self):
        job,frames,obs=self.prepare()
        calls=[]
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),calls.append)
        request=SequentialAnalysis(self.store,self.profile,adapter).plan(job,frames,obs)[0]
        request["human_label"]="private truth"
        with self.assertRaisesRegex(ValueError,"binding"):
            adapter(request)
        self.assertEqual(calls,[])
        for changed in ({"model_version":"Qwen3.6"},{"adapter_revision":"lab-qwen-cutoff-1"}):
            with self.assertRaises(ValueError):
                QwenAdapter({**self.profile,**changed},None,None)
        with self.assertRaises(ValueError):
            QwenAdapter(self.profile,None,None,deadline_seconds=0)

    def test_registered_source_corruption_never_reaches_transport(self):
        job,frames,obs=self.prepare()
        self.store.media_path(self.session["id"],frames[0]["media_id"]).write_bytes(b"bad")
        calls=[]
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),calls.append)
        request=SequentialAnalysis(self.store,self.profile,adapter).plan(job,frames,obs)[0]
        with self.assertRaisesRegex(ValueError,"integrity"):
            adapter(request)
        result=SequentialAnalysis(self.store,self.profile,adapter).run(job,frames,obs)
        self.assertEqual((result["reason"],result["detail"],result["dispatched"]),
                         ("analysis_step_not_dispatched","registered_rgb_integrity_failed",False))
        self.assertEqual(calls,[])
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM lab_analysis_steps").fetchone())

    def test_reconcile_only_sends_recorded_identifiers_not_media_or_inference(self):
        job,frames,obs=self.prepare()
        captured=[]
        def transport(envelope):
            captured.append(envelope)
            raise TimeoutError()
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),transport)
        analysis=SequentialAnalysis(self.store,self.profile,adapter)
        self.assertEqual(analysis.run(job,frames,obs)["state"],"indeterminate")
        # Media deletion after dispatch cannot prevent an identity lookup.
        self.store.media_path(self.session["id"],frames[0]["media_id"]).write_bytes(b"gone")
        def lookup(reference):
            self.assertNotIn("body",reference)
            self.assertNotIn("data:image",json.dumps(reference))
            self.assertEqual(reference,{k:v for k,v in captured[0].items() if k!="body"})
            return self.proof(captured[0])
        self.assertEqual(analysis.reconcile(job["id"],lambda request,_:adapter.reconcile(request,lookup,None))["state"],"indeterminate")
        result=analysis.reconcile(job["id"],lambda request,reference:adapter.reconcile(request,lookup,reference))
        self.assertEqual(result["steps"],1)
        self.assertEqual(len(captured),1)

    def test_lookup_after_engine_restart_ends_the_step_without_a_judgment(self):
        job,frames,obs=self.prepare()
        captured=[]
        def transport(envelope):
            captured.append(envelope)
            raise TimeoutError()
        adapter=QwenAdapter(self.profile,RegisteredAnalysisMedia(self.store),transport)
        analysis=SequentialAnalysis(self.store,self.profile,adapter)
        analysis.run(job,frames,obs)
        def not_found(epoch):
            return lambda reference:{"request_id":reference["request_id"],"body_sha256":reference["body_sha256"],
                "terminal":False,"state":"not_found","engine_epoch":epoch,"artifact_sha256":"a"*64}
        same=analysis.reconcile(job["id"],lambda request,reference:adapter.reconcile(request,not_found("fixture1"),reference))
        self.assertEqual(same["state"],"indeterminate")
        ended=analysis.reconcile(job["id"],lambda request,reference:adapter.reconcile(request,not_found("fixture2"),reference))
        self.assertEqual(ended["outcomes"],{"ended_unknown":1})
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="analysis.output"]
        self.assertEqual([o["status"] for o in outputs],["unknown"])


    def test_profile_must_be_exactly_what_the_la_consumer_accepts(self):
        self.prepare()
        for changed in ({"runtime_revision":"vllm 0.11.0+cu128"},{"notes":"extra"},{"engine_epoch":""},{"engine_epoch":7}):
            with self.assertRaises(ValueError):
                QwenAdapter({**self.profile,**changed},None,None)
        missing=dict(self.profile)
        del missing["runtime_revision"]
        with self.assertRaises(ValueError):
            QwenAdapter(missing,None,None)
        QwenAdapter({**self.profile,"engine_epoch":"vllm/epoch:2026-09-28"},None,None)

    def test_current_engine_epoch_comes_from_an_identity_only_probe(self):
        job,frames,obs=self.prepare()
        stale={**self.profile,"engine_epoch":"provisioned-bootstrap"}
        class Transport:
            def __init__(inner,epoch="fixture1",artifact="a"*64,fail=False):
                inner.epoch,inner.artifact,inner.fail,inner.lookups,inner.sent=epoch,artifact,fail,[],[]
            def lookup(inner,reference):
                inner.lookups.append(reference)
                if inner.fail:
                    raise TimeoutError()
                return {"version":1,"request_id":reference["request_id"],"body_sha256":reference["body_sha256"],"terminal":False,
                        "state":"not_found","engine_epoch":inner.epoch,"artifact_sha256":inner.artifact,"reason":None}
            def __call__(inner,envelope):
                inner.sent.append(envelope)
                return {**self.proof(envelope),"engine_epoch":inner.epoch}
        transport=Transport()
        adapter=QwenAdapter(stale,RegisteredAnalysisMedia(self.store),transport,clock=lambda:500.0)
        result=SequentialAnalysis(self.store,stale,adapter).run(job,frames,obs)
        self.assertEqual(result["state"],"completed")
        self.assertEqual(len(transport.lookups),1)
        probe=transport.lookups[0]
        self.assertNotIn("body",probe)
        self.assertNotIn(probe["request_id"],{e["request_id"] for e in transport.sent})
        self.assertEqual({e["profile"]["engine_epoch"] for e in transport.sent},{"fixture1"})
        self.assertTrue(all({k:v for k,v in e["profile"].items() if k!="engine_epoch"}=={k:v for k,v in stale.items() if k!="engine_epoch"}
                            for e in transport.sent))
        for reason,changed in (("qwen_artifact_mismatch",{"artifact":"b"*64}),("qwen_engine_epoch_unavailable",{"fail":True}),
                               ("qwen_engine_epoch_unavailable",{"epoch":"bad epoch!"})):
            other,_=self.store.create_job("analysis","probe-"+reason+str(len(changed)),{},self.session["id"])
            refused=Transport(**changed)
            adapter=QwenAdapter(stale,RegisteredAnalysisMedia(self.store),refused,clock=lambda:500.0)
            result=SequentialAnalysis(self.store,stale,adapter).run(other,frames,obs)
            self.assertEqual((result["reason"],result["detail"],result["dispatched"]),("analysis_step_not_dispatched",reason,False))
            self.assertEqual(refused.sent,[])
        # Only the engine instance is free; every other pinned field is refused.
        request=SequentialAnalysis(self.store,stale,adapter).plan(job,frames,obs)[0]
        adapter=QwenAdapter(stale,RegisteredAnalysisMedia(self.store),transport)
        from analysis import _stamp
        with self.assertRaisesRegex(ValueError,"binding"):
            adapter.prepare(_stamp(request,{**stale,"runtime_revision":"other"}))
        self.assertEqual(adapter.prepare(_stamp(request,{**stale,"engine_epoch":"e9"}))["profile"]["engine_epoch"],"e9")


if __name__ == '__main__':
    unittest.main()

import json
import threading
import unittest

import test_paired_runtime as fixtures
from jev_provider import JevProvider, ProviderFailure
from live_reasoning import LiveReasoning
from reasoning import ACTIVITIES
from controller import Controller
from service_runtime import ServiceRuntime
from store import LabError


class LiveReasoningTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    settle = fixtures.PairedRuntimeTests.settle

    def setup_reasoner(self, provider=None):
        self.now = 100
        self.store.clock = lambda: self.now
        ServiceRuntime(self.store, {}, None, None)
        self.store.db.execute("INSERT INTO lab_experiment_state VALUES(?,?,?)", (self.session["id"], "detection", 1))
        self.store.db.commit()
        self.calls = []
        def transport(body, token, timeout):
            packet = json.loads(body)
            self.calls.append(packet)
            return json.dumps({"model":"jev-1.13.0", "answers":{"activity":{
                "type":"choice", "choice":"reading", "confidence":.7,
                "probabilities":{k:1 if k == "reading" else 0 for k in ACTIVITIES}}}}).encode()
        real = JevProvider(model_selector="jev-1.13", response_version="jev-1.13.0",
            authorize=lambda *_:{"allowed":True,"scope":"household_structured","revision":"fixture","generation":1,"expires_at":200},
            credential=lambda:"fixture", transport=transport, clock=lambda:self.now)
        self.reasoner = LiveReasoning(self.store, self.session["id"], provider or real)
        return self.reasoner

    def tearDown(self):
        if hasattr(self, "reasoner"):
            self.reasoner.close()
            self.reasoner.pool.shutdown(wait=True)
        fixtures.PairedRuntimeTests.tearDown(self)

    def records(self, cutoff=100):
        return [{"id":arm+str(cutoff), "site_id":"victoria", "session_id":self.session["id"],
            "arm":arm, "generation":1, "observed_at":cutoff, "available_at":cutoff,
            "valid_until":120, "objects":[{"label":"book"}], "annotations":["never send"]} for arm in ("a","b")]

    def states(self):
        with self.store.lock:
            return [r[0] for r in self.store.db.execute("SELECT state FROM lab_reasoning_requests ORDER BY rowid")]

    def test_real_provider_seam_to_replay_and_scoring_contract(self):
        runner = self.setup_reasoner()
        ids = runner.offer(self.records(), generation=1, cutoff=100)
        self.settle(lambda:self.states() == ["completed","completed"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual({p["state"]["evidence"][0]["arm"] for p in self.calls}, {"a","b"})
        self.assertNotIn("never send", json.dumps(self.calls))
        outputs = Controller(self.store).detail(self.session["id"], at_ms=0)["outputs"]
        self.assertEqual({x["payload"]["run_id"] for x in outputs}, {"jevA","jevB"})
        self.assertEqual({x["payload"]["request_id"] for x in outputs}, set(ids))
        for item in outputs:
            value = item["payload"]
            self.assertEqual(value["prediction"]["available_at"], 0)
            self.assertEqual(value["judgment"]["confidence"], .7)
            self.assertEqual(value["judgment"]["probability"], 1)
        self.assertTrue(all(x["payload"]["status"] == "stale" for x in self.store.outputs_at(self.session["id"],21000)))

    def test_per_arm_history_reaches_provider_and_persisted_request(self):
        runner=self.setup_reasoner()
        records=self.records(99)+self.records(85)
        runner.offer(records,generation=1,cutoff=100,windows={"a":0,"b":20})
        self.settle(lambda:self.states()==["completed","completed"])
        packets={p["state"]["evidence"][0]["arm"]:p for p in self.calls}
        self.assertEqual([r["observed_at"] for r in packets["a"]["state"]["evidence"]],[99])
        self.assertEqual([r["observed_at"] for r in packets["b"]["state"]["evidence"]],[85,99])
        rows=self.store.db.execute("SELECT arm,payload FROM lab_reasoning_requests").fetchall()
        self.assertEqual({r[0]:json.loads(r[1])["window_seconds"] for r in rows},{"a":0,"b":20})

    def test_windows_zero_and_twenty_get_identical_shared_caption_within_validity_only(self):
        runner=self.setup_reasoner()
        records=self.records(99)+self.records(85)
        caption={"id":"c1","kind":"observation","site_id":"victoria","camera_id":"den","session_id":self.session["id"],
            "generation":1,"observed_at":70,"available_at":95,"valid_until":100.5,"source_kind":"caption.receipt",
            "evidence_roots":["caption:k"],"historical_caption":{"activity":"reading"},"local_text":"never send"}
        shared={arm:[{**caption,"arm":arm}] for arm in ("a","b")}
        runner.offer(records,generation=1,cutoff=100,windows={"a":0,"b":20},shared_context=shared)
        self.settle(lambda:self.states()==["completed","completed"])
        packets={p["state"]["evidence"][0]["arm"]:p for p in self.calls}
        captions={arm:[r for r in p["state"]["evidence"] if r.get("source_kind")=="caption.receipt"] for arm,p in packets.items()}
        self.assertEqual({arm:[{k:v for k,v in r.items() if k!="arm"} for r in rows] for arm,rows in captions.items()}["a"],
                         [{k:v for k,v in r.items() if k!="arm"} for r in captions["b"]])
        self.assertEqual(len(captions["a"]),1)
        self.assertEqual([r["observed_at"] for r in packets["a"]["state"]["evidence"]],[70,99])
        self.assertNotIn("never send",json.dumps(self.calls))
        rows=self.store.db.execute("SELECT payload FROM lab_reasoning_requests ORDER BY rowid").fetchall()
        self.assertTrue(all(json.loads(r[0])["shared_context"][0]["id"]=="c1" for r in rows))
        # An expired or later-available caption is never attached, even with retained history.
        self.now=101
        for changes in ({"valid_until":101},{"available_at":101.5}):
            ids=runner.offer(self.records(101),generation=1,cutoff=101,shared_context={"a":[{**caption,**changes,"arm":"a"}]})
            payloads=[json.loads(self.store.db.execute("SELECT payload FROM lab_reasoning_requests WHERE id=?",(i,)).fetchone()[0]) for i in ids]
            self.assertTrue(all("shared_context" not in p for p in payloads))
        with self.assertRaises(ValueError):
            runner.offer(records,generation=1,cutoff=101,shared_context={"c":[]})

    def test_fresh_ha_cannot_extend_old_visual_judgment(self):
        runner=self.setup_reasoner()
        records=self.records(99)
        for r in records:r['valid_until']=101
        records.append({**records[0],'id':'ha','source_kind':'ha.observation',
            'objects':[],'ha':{'computer_active':'on'},'observed_at':100,'valid_until':110})
        request={'request_id':'r','site_id':'victoria','arm':'a','generation':1,
            'records':records,'cutoff':100,'deadline':115}
        result={'status':'ready','judgment':{'activity':'reading','confidence':.8,
            'probabilities':{'reading':1}},'provenance':{}}
        output=runner._output(request,result,102)
        self.assertEqual(output['valid_until'],101)
        self.assertEqual(output['status'],'stale')

    def provider(self, transport, **kw):
        return JevProvider(model_selector="jev-1.13", response_version="jev-1.13.0",
            authorize=lambda *_:{"allowed":True,"scope":"household_structured","revision":"fixture","generation":1,"expires_at":200},
            credential=lambda:"fixture", transport=transport, clock=lambda:self.now, **kw)

    def delivery(self):
        return [(e["payload"]["arm"],e["payload"]["state"],e["payload"]["status"]) for e in self.store.list_events(self.session["id"]) if e["kind"]=="reasoning.delivery"]

    def test_pre_send_rejection_is_failed_and_does_not_hold_the_arm(self):
        def transport(body,*_):
            self.calls.append(json.loads(body))
            return json.dumps({"model":"jev-1.13.0","answers":{"activity":{"type":"choice","choice":"reading",
                "confidence":.7,"probabilities":{k:float(k=="reading") for k in ACTIVITIES}}}}).encode()
        runner=self.setup_reasoner()
        runner.provider=self.provider(transport,allow_recent_history=True)
        runner.offer(self.records(),generation=1,cutoff=100,windows={"a":20,"b":10})
        self.settle(lambda:sorted(self.states())==["completed","failed"] and not runner.futures)
        failed=json.loads(self.store.db.execute("SELECT result FROM lab_reasoning_requests WHERE state='failed'").fetchone()[0])
        self.assertEqual(failed,{"status":"failed","reason":"request_invalid"})
        self.assertEqual(sorted(self.delivery()),[("a","failed","failed"),("b","completed","ready")])
        self.assertEqual([p["state"]["evidence"][0]["arm"] for p in self.calls],["b"])
        self.now=101
        runner.offer(self.records(101),generation=1,cutoff=101)
        self.settle(lambda:self.states().count("completed")==3 and not runner.futures)
        self.assertEqual(len(self.calls),3)

    def test_provider_refusal_is_failed_with_its_status_and_the_arm_keeps_dispatching(self):
        answer = json.dumps({"model":"jev-1.13.0","answers":{"activity":{"type":"choice","choice":"reading",
            "confidence":.7,"probabilities":{k:float(k=="reading") for k in ACTIVITIES}}}}).encode()
        refused = []
        def transport(body,*_):
            packet = json.loads(body)
            self.calls.append(packet)
            if packet["state"]["evidence"][0]["arm"] == "a" and not refused:
                refused.append(1)
                raise ProviderFailure("provider_rejected_422", http_status=422)
            return answer
        runner = self.setup_reasoner()
        runner.provider = self.provider(transport)
        runner.offer(self.records(), generation=1, cutoff=100)
        self.settle(lambda:sorted(self.states())==["completed","failed"] and not runner.futures)
        row = self.store.db.execute("SELECT arm,result FROM lab_reasoning_requests WHERE state='failed'").fetchone()
        self.assertEqual((row[0], json.loads(row[1])), ("a", {"status":"failed","reason":"provider_rejected_422","http_status":422}))
        self.now = 101
        runner.offer(self.records(101), generation=1, cutoff=101)
        self.settle(lambda:self.states().count("completed")==3 and not runner.futures)
        self.assertEqual(len(self.calls), 4, "the refused arm dispatches again")

    def test_unproven_http_error_stays_indeterminate_with_its_status_and_holds_the_arm(self):
        def unavailable(*_):
            raise ProviderFailure("provider_http_error", indeterminate=True, http_status=503)
        runner = self.setup_reasoner(self.provider(unavailable))
        runner.offer(self.records(), generation=1, cutoff=100, arms=("a",))
        self.settle(lambda:self.states()==["indeterminate"] and not runner.futures)
        self.assertEqual(json.loads(self.store.db.execute("SELECT result FROM lab_reasoning_requests").fetchone()[0]),
                         {"status":"indeterminate","reason":"provider_http_error","http_status":503})
        self.now = 101
        runner.offer(self.records(101), generation=1, cutoff=101, arms=("a",))
        self.assertEqual(self.states(), ["indeterminate","pending"], "an unknown outcome still holds its arm")

    def test_transport_uncertainty_after_send_stays_indeterminate(self):
        def lost(*_):raise TimeoutError("fixture")
        runner=self.setup_reasoner(self.provider(lost))
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:self.states()==["indeterminate","indeterminate"] and not runner.futures)
        self.assertEqual({d[1:] for d in self.delivery()},{("indeterminate","indeterminate")})

    def test_late_answer_is_saved_as_stale_for_replay_without_recommendation(self):
        runner=self.setup_reasoner()
        records=self.records()
        for record in records:record['valid_until']=101
        result={'status':'ready','judgment':{'activity':'reading','confidence':.7,
            'probabilities':{'reading':1}},'provenance':{}}
        output=runner._output({'records':records,'cutoff':100,'deadline':115,
            'request_id':'late','site_id':'victoria','arm':'a','generation':1},result,102)
        self.assertEqual(output['status'],'stale')
        self.assertFalse(output['fresh_on_arrival'])
        self.assertEqual(output['valid_until'],101)
        self.assertIsNone(output['recommendation'])
        self.assertEqual(output['policy_result']['reason'],'stale_judgment')

    def test_one_active_latest_pending_and_superseded_generation(self):
        release = threading.Event()
        calls = []
        class Slow:
            def evaluate(inner, records, **kw):
                calls.append(kw)
                release.wait(2)
                return {"status":"ready", "provenance":{}, "judgment":{
                    "activity":"reading", "confidence":1, "model_version":"jev-1.13.0",
                    "probabilities":{"reading":1}}}
        runner = self.setup_reasoner(Slow())
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now = 101
        runner.offer(self.records(101),generation=1,cutoff=101)
        self.now = 102
        runner.offer(self.records(102),generation=1,cutoff=102)
        self.assertEqual(self.states().count("pending"), 2)
        self.assertEqual(self.states().count("replaced"), 2)
        self.store.db.execute("UPDATE lab_experiment_state SET generation=2")
        self.store.db.commit()
        release.set()
        self.settle(lambda:self.states().count("completed")==2 and self.states().count("expired")==2)
        self.assertEqual(len(calls),2)
        self.assertEqual(self.store.outputs_at(self.session["id"],2000), [])

    def lifecycle(self):
        return [e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="reasoning.lifecycle"]

    def slow(self):
        release=threading.Event()
        calls=[]
        class Slow:
            def evaluate(inner, records, **kw):
                calls.append(kw)
                release.wait(2)
                return {"status":"ready","provenance":{},"judgment":{"activity":"reading","confidence":1,
                    "model_version":"jev-1.13.0","probabilities":{"reading":1}}}
        return Slow(),release,calls

    def test_replaced_and_expired_pending_requests_leave_bounded_lifecycle_receipts(self):
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        first=runner.offer(self.records(101),generation=1,cutoff=101)
        self.assertEqual(self.lifecycle(),[])
        self.now=102
        second=runner.offer(self.records(102),generation=1,cutoff=102)
        replaced=self.lifecycle()
        self.assertEqual(replaced,[{"request_id":first[i],"arm":arm,"generation":1,"state":"replaced",
            "reason":"superseded_by_newer_request","at":102,"available_at":102,"replaced_by":second[i]}
            for i,arm in enumerate(("a","b"))])
        self.store.db.execute("UPDATE lab_experiment_state SET generation=2")
        self.store.db.commit()
        release.set()
        self.settle(lambda:len(self.lifecycle())==4 and not runner.futures)
        expired=self.lifecycle()[2:]
        self.assertEqual(sorted((e["request_id"],e["state"],e["reason"]) for e in expired),
            sorted((rid,"expired","experiment_generation_changed") for rid in second))
        # Admission and dispatch are unchanged: no extra provider call.
        self.assertEqual(len(calls),2)
        self.assertEqual(sorted(self.states()),["completed","completed","expired","expired","replaced","replaced"])
        for event in self.lifecycle():
            self.assertLess(len(json.dumps(event)),512)
            self.assertNotIn("records",event)

    def test_deadline_and_stopped_session_expiry_reasons_are_controlled(self):
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        ids=runner.offer(self.records(101),generation=1,cutoff=101)
        self.now=117  # past the pending requests' cutoff+15 deadline
        release.set()
        self.settle(lambda:len(self.lifecycle())==2 and not runner.futures)
        self.assertEqual({(e["request_id"],e["reason"]) for e in self.lifecycle()},{(i,"deadline_passed_before_dispatch") for i in ids})

    def test_session_not_recording_expires_pending_with_its_reason(self):
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        runner.offer(self.records(101),generation=1,cutoff=101)
        self.store.transition(self.session["id"],"stopping")
        release.set()
        self.settle(lambda:len(self.lifecycle())==2 and not runner.futures)
        self.assertEqual({e["reason"] for e in self.lifecycle()},{"session_not_recording"})

    def test_close_cancels_pending_with_the_callers_reason_or_the_generic_one(self):
        self.setup_reasoner()
        for reason,expected in (("reasoning_disabled","reasoning_disabled"),("arbitrary text","reasoning_closed")):
            provider,release,calls=self.slow()
            self.now=100
            runner=self.reasoner=LiveReasoning(self.store,self.session["id"],provider)
            runner.offer(self.records(),generation=1,cutoff=100)
            self.settle(lambda:len(calls)==2)
            self.now=101
            ids=runner.offer(self.records(101),generation=1,cutoff=101)
            before=len(self.lifecycle())
            runner.close(reason=reason)
            cancelled=self.lifecycle()[before:]
            self.assertEqual([(e["request_id"],e["state"],e["reason"]) for e in cancelled],[(i,"cancelled",expected) for i in ids])
            release.set()
            runner.pool.shutdown(wait=True)
            with self.store.transaction() as db:
                db.execute("DELETE FROM lab_reasoning_requests")

    def test_runtime_disable_and_stop_name_the_cancellation(self):
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runtime=ServiceRuntime(self.store,{},None,None,reasoning_factory=lambda *_:runner)
        runtime.reasoners[self.session["id"]]=runner
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        runner.offer(self.records(101),generation=1,cutoff=101)
        runtime.set_reasoning(self.store.get_session(self.session["id"]),False)
        self.assertEqual({e["reason"] for e in self.lifecycle()},{"reasoning_disabled"})
        release.set()

    def test_local_dispatch_failure_is_recorded_and_still_raised(self):
        runner=self.setup_reasoner()
        def refuse(*_args,**_kw):raise RuntimeError("fixture pool closed")
        runner.pool.submit=refuse
        with self.assertRaises(RuntimeError):
            runner.offer(self.records(),generation=1,cutoff=100)
        self.assertEqual([(e["arm"],e["state"],e["reason"]) for e in self.lifecycle()],[("a","failed","local_dispatch_failed")])
        self.assertEqual(self.states()[0],"failed")

    def fail_receipts(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_receipt BEFORE INSERT ON events WHEN NEW.kind='reasoning.lifecycle' BEGIN SELECT RAISE(ABORT,'fixture'); END")

    def test_failed_cancellation_receipt_never_blocks_close_or_stop(self):
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runtime=ServiceRuntime(self.store,{},None,None,reasoning_factory=lambda *_:runner)
        runtime.reasoners[self.session["id"]]=runner
        class Runner:
            closed=False
            def close(inner):inner.closed=True
        other=runtime.runners[self.session["id"]]=Runner()
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        runner.offer(self.records(101),generation=1,cutoff=101)
        self.fail_receipts()
        # No receipt can be written, yet the cancellation commits and Stop goes on
        # to close the paired runner and ask the recorder (none here) to stop.
        outcome=runtime.stop_session(self.store.get_session(self.session["id"]))
        self.assertEqual(outcome,{"state":"indeterminate","reason":"recorder_not_reconciled"})
        self.assertTrue(other.closed)
        self.assertEqual(self.states().count("cancelled"),2)
        self.assertEqual(self.states().count("pending"),0)
        self.assertEqual(self.lifecycle(),[])
        release.set()

    def test_failed_dispatch_receipt_keeps_the_failed_state_and_the_original_error(self):
        runner=self.setup_reasoner()
        def refuse(*_args,**_kw):raise RuntimeError("fixture pool closed")
        runner.pool.submit=refuse
        self.fail_receipts()
        with self.assertRaisesRegex(RuntimeError,"fixture pool closed"):
            runner.offer(self.records(),generation=1,cutoff=100)
        self.assertEqual(self.states()[0],"failed")
        self.assertEqual(self.lifecycle(),[])

    def test_failed_replacement_receipt_does_not_change_admission(self):
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        runner.offer(self.records(101),generation=1,cutoff=101)
        self.fail_receipts()
        self.now=102
        ids=runner.offer(self.records(102),generation=1,cutoff=102)
        self.assertEqual(len(ids),2)
        self.assertEqual((self.states().count("replaced"),self.states().count("pending")),(2,2))
        self.assertEqual(self.lifecycle(),[])
        release.set()

    def test_controller_restart_cancels_pending_with_receipts_except_for_erased_sessions(self):
        from store import Store
        from live_reasoning import LIFECYCLE_REASONS
        self.assertIn("controller_restart",LIFECYCLE_REASONS["cancelled"])
        self.assertNotIn("controller_restart",LiveReasoning.CLOSE_REASONS)
        provider,release,calls=self.slow()
        runner=self.setup_reasoner(provider)
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:len(calls)==2)
        self.now=101
        pending=runner.offer(self.records(101),generation=1,cutoff=101)
        # A second session whose erasure is pending keeps no new records.
        erased={"id":"erased-session"}
        with self.store.transaction() as db:
            db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?,?,?,?,NULL,NULL)",("gone",erased["id"],"a",1,"pending","{}"))
            db.execute("INSERT INTO session_erasures(session_id,state,requested_at) VALUES(?,?,?)",(erased["id"],"pending",self.now))
        # Simulated crash: recovery runs on the same ledger without close().
        self.store.recover()
        cancelled=[e for e in self.lifecycle() if e["reason"]=="controller_restart"]
        self.assertEqual(sorted((e["request_id"],e["state"],e["arm"]) for e in cancelled),sorted((i,"cancelled",a) for i,a in zip(pending,"ab")))
        with self.store.lock:
            self.assertEqual(self.store.db.execute("SELECT state FROM lab_reasoning_requests WHERE id='gone'").fetchone()[0],"cancelled")
            self.assertIsNone(self.store.db.execute("SELECT 1 FROM events WHERE session_id=?",(erased["id"],)).fetchone())
        self.assertNotIn("pending",self.states())
        release.set()

    def test_unknown_completion_blocks_restart_and_deletion(self):
        class Unknown:
            def evaluate(inner, *args, **kw):
                raise ProviderFailure("timeout",indeterminate=True)
        runner = self.setup_reasoner(Unknown())
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:self.states()==["indeterminate","indeterminate"])
        runner.close()
        replacement = LiveReasoning(self.store,self.session["id"],Unknown())
        self.reasoner = replacement
        self.now = 101
        replacement.offer(self.records(101),generation=1,cutoff=101)
        self.assertEqual(self.states(),["indeterminate","indeterminate","pending","pending"])
        replacement.close()
        self.store.transition(self.session["id"],"stopping")
        self.store.transition(self.session["id"],"stopped")
        session=self.store.get_session(self.session["id"])
        with self.assertRaisesRegex(LabError,"reconcile reasoning"):
            self.store.delete_session(session["id"],session["revision"])

    def test_commit_failure_does_not_release_durable_reservation(self):
        runner = self.setup_reasoner()
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_failure BEFORE INSERT ON events WHEN NEW.kind='reasoning.output' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        runner.offer(self.records(),generation=1,cutoff=100)
        runner.pool.shutdown(wait=True)
        self.assertEqual(self.states(),["dispatching","dispatching"])
        self.assertEqual(self.store.outputs_at(self.session["id"],0),[])

    def test_terminal_recovery_is_bound_atomic_and_never_publishes_or_dispatches(self):
        class Unknown:
            def evaluate(inner,*args,**kw): raise ProviderFailure("timeout",indeterminate=True)
        runner=self.setup_reasoner(Unknown())
        ids=runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:self.states()==["indeterminate","indeterminate"] and not runner.futures)
        calls=[]
        def proof(reference):
            calls.append(reference)
            self.assertNotIn("records",reference)
            return {**reference,"terminal":True,"state":"completed","receipt_id":"receipt-1",
                "judgment":{"activity":"reading"},"secret":"not retained"}
        for invalid in (lambda r:{**proof(r),"arm":"wrong"},
                        lambda r:{**proof(r),"request_sha256":"0"*64},lambda r:None):
            self.assertEqual(runner.reconcile(ids[0],invalid)["state"],"indeterminate")
            self.assertEqual(self.states(),["indeterminate","indeterminate"])
        original=self.store._event
        def failed(*args,**kwargs): raise OSError("fixture disk full")
        self.store._event=failed
        try: self.assertEqual(runner.reconcile(ids[0],proof)["state"],"indeterminate")
        finally: self.store._event=original
        self.assertEqual(self.states(),["indeterminate","indeterminate"])
        self.assertEqual(runner.reconcile(ids[0],proof),{"state":"completed","reconciled":True,"resumed":False})
        self.assertEqual(runner.reconcile(ids[0],lambda _:self.fail("duplicate lookup"))["reconciled"],False)
        self.assertEqual(self.store.outputs_at(self.session["id"],0),[])
        receipt=json.loads(self.store.db.execute("SELECT result FROM lab_reasoning_requests WHERE id=?",(ids[0],)).fetchone()[0])
        self.assertNotIn("secret",receipt)
        self.assertNotIn("judgment",receipt)

    def test_fast_provider_still_obeys_one_hz_per_arm(self):
        runner = self.setup_reasoner()
        runner.offer(self.records(),generation=1,cutoff=100)
        self.settle(lambda:self.states()==["completed","completed"])
        runner.offer(self.records(),generation=1,cutoff=100)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.states().count("pending"),2)
        self.now=101
        runner._pump("a")
        runner._pump("b")
        self.settle(lambda:self.states().count("completed")==4)
        self.assertEqual(len(self.calls),4)


if __name__ == "__main__":
    unittest.main()

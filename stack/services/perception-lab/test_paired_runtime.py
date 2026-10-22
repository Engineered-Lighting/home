from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from config import Settings
from store import Store
from runtime import PairedRunner


class PairedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Settings(storage_root=Path(self.directory.name),min_free_bytes=0),clock=lambda:100.0)
        self.session,_ = self.store.create_session("den","detection","Fixture","request1")
        self.store.transition(self.session["id"],"starting")
        self.store.transition(self.session["id"],"recording")
        self.media=self.store.add_media(self.session["id"],b"synthetic-frame-bytes","image/jpeg",{"fixture":True})
        self.profiles = {arm:{"profile_id":arm,"artifact_sha256":"a"*64,"model_version":"fixture-1","engine_epoch":"engine1"} for arm in ("a","b")}
        self.runners = []

    def tearDown(self):
        for runner in self.runners:
            runner.close()
            runner.pool.shutdown(wait=True)
        self.store.close()
        self.directory.cleanup()

    def runner(self, executor):
        runner=PairedRunner(self.store,self.session["id"],self.profiles,executor,clock=lambda:100.0)
        self.runners.append(runner)
        return runner

    def frame(self, identity="frame1", **kw):
        return {"frame_id":identity,"capture_epoch":"capture1","site_id":"victoria","camera_id":"den",
                "session_id":self.session["id"],"observed_at":99.0,"available_at":99.1,"valid_until":101.0,
                "media_ids":[self.media["id"]],**kw}

    def receipt(self, request, **kw):
        return {"request_id":request["request_id"],"engine_epoch":"engine1","artifact_sha256":"a"*64,
                "terminal":True,"state":"completed","result":{"objects":[]},**kw}

    def settle(self, predicate):
        deadline=time.monotonic()+2
        while not predicate():
            if time.monotonic()>deadline:
                self.fail("bounded fixture work did not settle")
            time.sleep(.005)

    def test_both_arms_share_frame_and_completed_duplicate_does_not_dispatch(self):
        calls=[]
        def execute(request):
            calls.append(request)
            return self.receipt(request)
        runner=self.runner(execute)
        runner.offer(self.frame())
        self.settle(lambda: len(calls)==2 and all(v is None for v in runner.snapshot()["active"].values()))
        runner.offer(self.frame())
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]["frame"],calls[1]["frame"])
        with self.assertRaises(ValueError): runner.offer(self.frame(observed_at=98))
        with self.assertRaises(ValueError): runner.offer(self.frame("wrong",site_id="echo"))

    def test_la_only_never_dispatches_or_fabricates_an_a_observation(self):
        calls = []
        def execute(request):
            calls.append(request)
            return self.receipt(request)
        runner = PairedRunner(self.store, self.session['id'], self.profiles, execute,
                             clock=lambda: 100., execution_arms=('b',))
        self.runners.append(runner)
        runner.offer(self.frame())
        self.settle(lambda: len(calls) == 1 and not any(runner.snapshot()['active'].values()))
        self.assertEqual([r['arm'] for r in calls], ['b'])
        self.assertEqual(runner.snapshot()['execution_arms'], ['b'])
        self.assertEqual({r[0] for r in self.store.db.execute('SELECT arm FROM paired_requests')}, {'b'})
        self.assertEqual(calls[0]['frame']['valid_until'], self.frame()['valid_until'])

    def test_unknown_or_empty_execution_scope_is_refused(self):
        for arms in ((), ('a',), ('a', 'b', 'c'), ('b', 'b')):
            with self.subTest(arms=arms), self.assertRaises(ValueError):
                PairedRunner(self.store, self.session['id'], self.profiles, lambda _: None, execution_arms=arms)

    def test_runner_prepared_before_recording_uses_committed_start_for_replay(self):
        # Controller prepares the runner while the session is still starting.
        self.store.transition(self.session['id'],'stopped')
        self.session,_=self.store.create_session('den','detection','Startup','startup-request')
        self.store.transition(self.session['id'],'starting')
        runner=self.runner(lambda request:self.receipt(request,result={
            'schema_version':1,'frame_id':'frame1','coordinate_space':'source_pixels','objects':[],'boxes':[]}))
        self.assertIsNone(runner.session['started_at'])
        self.store.transition(self.session['id'],'recording')
        self.media=self.store.add_media(self.session['id'],b'fixture','image/jpeg',{
            'site_id':'victoria','camera_id':'den','frame_id':'frame1','capture_epoch':'capture1',
            'width':640,'height':360,'observed_at':100.,
            'clock_mapping_status':'verified','clock_mapping_id':'fixture-clock'})
        runner.offer(self.frame(observed_at=100.,available_at=100.))
        self.settle(lambda:len([e for e in self.store.list_events(self.session['id'])
            if e['kind'] in {'perception.output','perception.indeterminate'}])==2)
        events=self.store.list_events(self.session['id'])
        outputs=[e['payload'] for e in events if e['kind']=='perception.output']
        self.assertEqual(len(outputs),2)
        self.assertFalse(any(e['kind']=='perception.indeterminate' for e in events))
        for output in outputs:
            self.assertEqual(output['available_at_s'],0.)
            self.assertEqual(output['source_end_s'],0.)
        self.assertTrue(all(v is None for v in runner.snapshot()['active'].values()))

    def test_source_sampling_is_per_arm_durable_and_out_of_order_frames_do_not_reset_it(self):
        calls=[]
        def execute(request):
            calls.append(request)
            return self.receipt(request)
        def new_runner():
            runner=PairedRunner(self.store,self.session['id'],self.profiles,execute,
                clock=self.store.clock,sampling_hz={'a':1,'b':2})
            self.runners.append(runner)
            return runner
        runner=new_runner()
        for i,observed in enumerate((98,98.25,98.5,97)):
            runner.offer(self.frame('sample'+str(i),observed_at=observed))
            self.settle(lambda:all(v is None for v in runner.snapshot()['active'].values()))
        self.assertEqual([(c['arm'],c['frame']['observed_at']) for c in calls if c['arm']=='a'],[('a',98)])
        self.assertEqual([c['frame']['observed_at'] for c in calls if c['arm']=='b'],[98,98.5])
        drops=[e for e in self.store.list_events(self.session['id']) if e['kind']=='perception.dropped']
        self.assertEqual(len(drops),5)
        self.assertTrue(all(e['payload']['reason']=='source_sampling_cadence' for e in drops))
        from controller import Controller
        findings=Controller(self.store).findings(self.session['id'])
        coverage={row['arm']:row for row in findings['specialist_delivery']}
        self.assertEqual(coverage['a']['offered'],4)
        self.assertEqual(coverage['a']['completed_fraction'],.25)
        self.assertEqual(coverage['b']['fresh_completed_fraction'],.5)
        self.assertEqual(coverage['a']['drop_reasons']['source_sampling_cadence'],3)
        self.assertEqual(findings['runs'],[])
        runner.close();runner.pool.shutdown(wait=True)
        runner=new_runner()
        runner.offer(self.frame('after-restart',observed_at=98.75))
        self.assertEqual(len(calls),3)
        runner.offer(self.frame('next-epoch',observed_at=98.75,capture_epoch='new-epoch'))
        self.settle(lambda:len(calls)==5 and all(v is None for v in runner.snapshot()['active'].values()))
        self.assertEqual({c['sampling_hz'] for c in calls},{1,2})

    def test_latest_pending_replaces_old_without_hiding_drop(self):
        release=threading.Event()
        started={arm:threading.Event() for arm in ("a","b")}
        calls=[]
        def execute(request):
            calls.append(request)
            started[request["arm"]].set()
            if not release.wait(2): raise RuntimeError("fixture timeout")
            return self.receipt(request)
        runner=self.runner(execute)
        runner.offer(self.frame("first"))
        self.assertTrue(all(event.wait(1) for event in started.values()))
        runner.offer(self.frame("second"))
        runner.offer(self.frame("third"))
        release.set()
        self.settle(lambda: len(calls)==4 and all(v is None for v in runner.snapshot()["active"].values()))
        self.assertEqual({c["frame"]["frame_id"] for c in calls},{"first","third"})
        drops=[e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.dropped"]
        self.assertEqual(len(drops),2)

    def test_completed_specialists_are_visible_to_review_without_activity_truth(self):
        from controller import Controller
        runner=self.runner(lambda request:self.receipt(request))
        runner.offer(self.frame())
        self.settle(lambda: all(v is None for v in runner.snapshot()["active"].values()))
        outputs=Controller(self.store).detail(self.session["id"])["outputs"]
        self.assertEqual(len(outputs),2)
        for event in outputs:
            value=event["payload"]
            self.assertEqual(value["capture_epoch"],"capture1")
            self.assertEqual(value["media_ids"],[self.media["id"]])
            self.assertNotIn("prediction",value)
            self.assertEqual(value["result"],{"objects":[]})
        runner.offer(self.frame())
        self.assertEqual(len(Controller(self.store).detail(self.session["id"])["outputs"]),2)

    def test_failed_output_commit_keeps_reservation_until_reconciled(self):
        calls=[]
        original=self.store._event
        def fault(db,sid,kind,payload):
            if kind == "perception.output":
                raise RuntimeError("fixture interrupted output commit")
            return original(db,sid,kind,payload)
        self.store._event=fault
        def execute(request):
            calls.append(request)
            return self.receipt(request)
        runner=self.runner(execute)
        runner.offer(self.frame())
        self.settle(lambda:len([e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.indeterminate"])==2)
        self.assertTrue(all(runner.snapshot()["active"].values()))
        self.assertFalse(any(e["kind"]=="perception.delivery" for e in self.store.list_events(self.session["id"])))
        self.store._event=original
        for request in calls:
            runner.reconcile(request["request_id"],self.receipt(request))
        self.assertEqual(len([e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]),2)
        self.assertTrue(all(v is None for v in runner.snapshot()["active"].values()))

    def test_timeout_blocks_next_request_until_matching_terminal_proof(self):
        calls=[]
        def execute(request):
            calls.append(request)
            raise TimeoutError("possibly running remotely")
        runner=self.runner(execute)
        runner.offer(self.frame())
        self.settle(lambda: len([e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.indeterminate"])==2)
        runner.offer(self.frame("next"))
        self.assertEqual(len(calls),2)
        self.assertTrue(all(runner.snapshot()["active"].values()))
        with self.assertRaises(ValueError): runner.reconcile(calls[0]["request_id"],self.receipt(calls[0],engine_epoch="other"))
        runner.reconcile(calls[0]["request_id"],self.receipt(calls[0]))
        self.settle(lambda:len(calls)==3)
        self.assertIsNotNone(runner.snapshot()["active"][calls[1]["arm"]])

    def test_expired_frame_is_recorded_as_expired_not_executed(self):
        calls=[]
        runner=self.runner(lambda request:calls.append(request))
        runner.offer(self.frame(valid_until=99.5))
        self.assertEqual(calls,[])
        expired=[e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.expired"]
        self.assertEqual(len(expired),2)

    def test_expired_frame_dispatches_only_the_arm_b_preview_request_with_original_output_times(self):
        from unittest.mock import patch
        calls=[]
        with patch("apartment_viewing_source.development_preview",return_value=10.):
            runner=self.runner(lambda request:calls.append(request) or self.receipt(request))
            runner.offer(self.frame(valid_until=99.5))
            self.settle(lambda:len(calls)==1 and runner.snapshot()["active"]["b"] is None)
        # Arm B carries a longer dispatch deadline (the LA ledger records it);
        # everything this controller reports keeps the original 99.5.
        self.assertEqual(calls[0]["arm"],"b");self.assertEqual(calls[0]["frame"]["valid_until"],109.1)
        self.assertEqual(calls[0]["preview_timing"],{"policy":"development","original_valid_until":99.5,"hold_s":10.})
        expired=[e["payload"]["arm"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.expired"]
        self.assertEqual(expired,["a"])
        output=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
        self.assertEqual(len(output),1);self.assertEqual(output[0]["valid_until"],99.5);self.assertFalse(output[0]["current"])
        from delivery_metrics import specialist_delivery
        arm_b=[g for g in specialist_delivery(self.store,self.session["id"]) if g["arm"]=="b"][0]
        self.assertEqual((arm_b["completed"],arm_b["fresh_completed"]),(1,0),"freshness is judged by the original deadline")
        calls.clear();strict=self.runner(lambda request:calls.append(request))
        strict.offer(self.frame("strict-frame",valid_until=99.5))
        self.assertEqual(calls,[])

    def test_sampling_display_hold_never_extends_reading_source_expiry(self):
        # The viewing arm's slower cadence may hold its presentation, but the
        # source supporting a reading decision keeps its original lease.
        def execute(request):
            return self.receipt(request, result={
                'objects': [{'label': 'person', 'instance_id': 'one', 'confidence': .9}],
                'boxes': [{'label': 'person', 'instance_id': 'one', 'confidence': .9,
                           'x': 100., 'y': 60., 'w': 100., 'h': 230.}]})
        runner = PairedRunner(self.store, self.session['id'], self.profiles, execute,
            clock=self.store.clock, experiment_generation=1, sampling_hz={'a': 1, 'b': 1})
        self.runners.append(runner)
        for index, (original_until,qualified) in enumerate(((100.2,True),(99.75,True),(100.2,False))):
            with self.subTest(original_until=original_until):
                frame_id = 'source-lease-' + str(index)
                self.media = self.store.add_media(self.session['id'],
                    b'synthetic-frame-' + str(index).encode(), 'image/jpeg',
                    {'reading_timing_qualified': qualified})
                runner.offer(self.frame(frame_id, capture_epoch='capture-' + str(index),
                                        valid_until=original_until,
                                        reading_timing_qualified=True))  # untrusted field cannot override original
                self.settle(lambda: all(v is None for v in runner.snapshot()['active'].values()))
                outputs = [e['payload'] for e in self.store.list_events(self.session['id'])
                    if e['kind'] == 'perception.output' and e['payload']['frame_id'] == frame_id]
                self.assertEqual(len(outputs), 2)
                for output in outputs:
                    self.assertTrue(output['current'], 'the presentation hold remains compatible')
                    self.assertAlmostEqual(output['valid_until'], original_until + .5)
                    binding = output['reading_binding']
                    if original_until > self.store.clock() and qualified:
                        self.assertEqual(binding['status'], 'available')
                        self.assertEqual(binding['valid_until'], original_until)
                    else:
                        self.assertEqual(binding['status'], 'unavailable')
                        self.assertEqual(binding['reason'], 'sole_person_source_not_current')

    def test_executor_cannot_mutate_persisted_request_or_source(self):
        calls=[]
        def execute(request):
            calls.append(request)
            request["frame"]["media_ids"].append("unexpected")
            return self.receipt(request)
        runner=self.runner(execute)
        frame=self.frame()
        runner.offer(frame)
        self.settle(lambda:len(calls)==2 and all(v is None for v in runner.snapshot()["active"].values()))
        self.assertEqual(frame["media_ids"],[self.media["id"]])
        runner.offer(frame) # unchanged original digest is accepted as duplicate

    def test_unregistered_media_cannot_be_offered_to_an_executor(self):
        from store import LabError
        calls=[]
        runner=self.runner(lambda request:calls.append(request))
        with self.assertRaises(LabError):
            runner.offer(self.frame(media_ids=["unknown-object"]))
        self.assertEqual(calls,[])

    BASELINE_LEDGER="""CREATE TABLE paired_requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL,
                generation INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, updated_at REAL NOT NULL)"""

    def test_dispatch_time_is_kept_beside_an_unchanged_ledger_for_rollback(self):
        with self.store.transaction() as db:
            db.execute(self.BASELINE_LEDGER)
            db.execute("INSERT INTO paired_requests VALUES('old','other','a',0,'d','{}','completed',NULL,1)")
        runner=self.runner(lambda request:self.receipt(request))
        self.runner(lambda request:self.receipt(request)).close()  # idempotent setup
        runner.offer(self.frame())
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        columns=[r[1] for r in self.store.db.execute("PRAGMA table_info(paired_requests)")]
        self.assertEqual(columns,["id","session_id","arm","generation","digest","payload","state","result","updated_at"])
        # The earlier controller's positional insert still fits after this build ran.
        with self.store.transaction() as db:
            db.execute("INSERT INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)",("rolled-back","other","b",0,"d","{}","offered",None,2))
        rows=self.store.db.execute("SELECT arm,generation,dispatched_at FROM paired_dispatch WHERE session_id=?",(self.session["id"],)).fetchall()
        self.assertEqual(sorted(tuple(r) for r in rows),[("a",0,100.0),("b",0,100.0)])
        self.assertEqual({e["payload"]["dispatched_at"] for e in self.store.list_events(self.session["id"])
                          if e["kind"]=="perception.delivery"},{100.0})

    def test_request_dispatched_by_a_rolled_back_controller_reconciles_without_dispatch_time(self):
        import json
        request={"request_id":"r"*64,"arm":"a","generation":0,"session_id":self.session["id"],"profile":self.profiles["a"],"frame":self.frame()}
        with self.store.transaction() as db:
            db.execute(self.BASELINE_LEDGER)
            db.execute("INSERT INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)",
                       (request["request_id"],self.session["id"],"a",0,"d",json.dumps(request),"dispatching",None,100.0))
        runner=self.runner(lambda request:self.receipt(request))
        self.assertEqual(runner.snapshot()["active"]["a"],request["request_id"])
        runner.reconcile(request["request_id"],self.receipt(request))
        delivery,=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.delivery"]
        self.assertEqual(delivery["state"],"completed")
        self.assertNotIn("dispatched_at",delivery)

    def test_session_erasure_removes_dispatch_times_and_sweeps_rollback_orphans(self):
        runner=self.runner(lambda request:self.receipt(request))
        runner.offer(self.frame())
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        runner.close()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM paired_dispatch").fetchone()[0],2)
        session=self.store.transition(self.session["id"],"stopped")
        self.store.delete_session(session["id"],session["revision"],erase_records=True)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM paired_dispatch").fetchone()[0],0)
        # An erasure completed by a controller that predates this table leaves
        # its dispatch times behind; the next runner removes them.
        with self.store.transaction() as db:
            db.execute("INSERT INTO paired_dispatch VALUES('orphan',?,'a',0,100.0)",(session["id"],))
        self.session,_=self.store.create_session("den","detection","Next","request2")
        self.store.transition(self.session["id"],"starting")
        self.store.transition(self.session["id"],"recording")
        self.runner(lambda request:self.receipt(request))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM paired_dispatch").fetchone()[0],0)

    def test_one_exact_storage_scan_admits_both_arms_offer_writes(self):
        scans=[]
        original=self.store.storage_usage
        def counted():
            scans.append(1);return original()
        release=threading.Event()
        runner=self.runner(lambda request:release.wait(2) and self.receipt(request))
        self.store.storage_usage=counted
        try:
            runner.offer(self.frame("first"))
            self.assertEqual(len(scans),1)
            runner.offer(self.frame("second"))
            runner.offer(self.frame("third"))  # replaces a pending frame: drop events too
            self.assertEqual(len(scans),3)
            self.assertEqual(len([e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.dropped"]),2)
        finally:
            release.set()
            self.store.storage_usage=original

    def test_offer_fails_closed_before_writing_when_batch_exceeds_quota(self):
        from store import LabError
        runner=self.runner(lambda request:self.receipt(request))
        with patch.object(self.store,"storage_usage",return_value=self.store.settings.quota_bytes-6*(65536+1024)+1):
            with self.assertRaises(LabError):
                runner.offer(self.frame())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM paired_requests").fetchone()[0],0)

    def test_unproven_completion_uses_one_lookup_and_plain_errors_use_none(self):
        from runtime import CompletionUnproven
        lookups=[]
        def execute(request):
            if request["arm"]=="b":
                raise TimeoutError("no lookup offered")
            def lookup():
                lookups.append(request["request_id"]);return self.receipt(request)
            raise CompletionUnproven(lookup)
        runner=self.runner(execute)
        runner.reconcile_delay=.01
        runner.offer(self.frame())
        self.settle(lambda:runner.snapshot()["active"]["a"] is None and len(lookups)==1)
        self.assertIsNotNone(runner.snapshot()["active"]["b"])
        states=dict(self.store.db.execute("SELECT arm,state FROM paired_requests").fetchall())
        self.assertEqual(states,{"a":"completed","b":"indeterminate"})
        deliveries=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.delivery"]
        self.assertEqual([(d["arm"],d["proof_source"]) for d in deliveries],[("a","identity_lookup")])

    def recorded_waits(self, runner):
        waits=[]
        class Recorded(threading.Event):
            def wait(self, timeout=None):
                waits.append(timeout);return super().wait(0)
        runner.stopping=Recorded()
        runner.reconcile_delay=.01
        return waits

    def lookup_unproven(self):
        return {e["payload"]["arm"]:e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.lookup_unproven"}

    def test_lookup_proven_not_run_is_tried_again_after_a_doubling_delay(self):
        from runtime import CompletionUnproven, LookupNotPerformed
        lookups=[]
        def execute(request):
            if request["arm"]=="b":
                raise ValueError("plain failure offers no lookup")
            def lookup():
                lookups.append(request["request_id"])
                if len(lookups)<3: raise LookupNotPerformed("ingress_busy")
                return self.receipt(request)
            raise CompletionUnproven(lookup)
        runner=self.runner(execute)
        waits=self.recorded_waits(runner)
        runner.offer(self.frame())
        self.settle(lambda:runner.snapshot()["active"]["a"] is None)
        self.assertEqual(len(lookups),3)
        self.assertEqual(waits,[.01,.02,.04])
        states=dict(self.store.db.execute("SELECT arm,state FROM paired_requests").fetchall())
        self.assertEqual(states,{"a":"completed","b":"indeterminate"})
        self.assertEqual(self.lookup_unproven(),{})

    def test_lookups_are_bounded_and_any_other_failure_ends_them(self):
        from runtime import CompletionUnproven, LookupNotPerformed
        lookups={"a":0,"b":0}
        def execute(request):
            def lookup():
                lookups[request["arm"]]+=1
                if request["arm"]=="a": raise LookupNotPerformed("ingress_busy")
                raise ValueError("remote_specialist_completion_unproven")  # the lookup may have run
            raise CompletionUnproven(lookup)
        runner=self.runner(execute)
        waits=self.recorded_waits(runner)
        runner.offer(self.frame())
        self.settle(lambda:len(self.lookup_unproven())==2)
        self.assertEqual(lookups,{"a":5,"b":1})
        self.assertEqual(sorted(waits),sorted([.01,.02,.04,.08,.16,.01]))
        unproven=self.lookup_unproven()
        self.assertEqual((unproven["a"]["attempts"],unproven["a"]["reason"]),(5,"lookup_not_performed"))
        self.assertEqual((unproven["b"]["attempts"],unproven["b"]["reason"]),(1,"terminal_proof_missing"))
        self.assertEqual({r[0] for r in self.store.db.execute("SELECT state FROM paired_requests")},{"indeterminate"})
        self.assertTrue(all(runner.snapshot()["active"].values()))

    def test_close_during_lookup_backoff_stops_further_lookups(self):
        from runtime import CompletionUnproven, LookupNotPerformed
        lookups=[]
        def execute(request):
            if request["arm"]=="b":
                raise ValueError("plain failure offers no lookup")
            def lookup():
                lookups.append(1);runner.close()
                raise LookupNotPerformed("ingress_busy")
            raise CompletionUnproven(lookup)
        runner=self.runner(execute)
        self.recorded_waits(runner)
        runner.offer(self.frame())
        self.settle(lambda:len(lookups)==1 and runner.stopping.is_set())
        runner.pool.shutdown(wait=True)
        self.assertEqual(len(lookups),1)
        self.assertEqual(self.lookup_unproven(),{})
        self.assertEqual({r[0] for r in self.store.db.execute("SELECT state FROM paired_requests")},{"indeterminate"})

    def test_delivery_metrics_coalesce_reasons_and_report_cadence(self):
        from delivery_metrics import specialist_delivery
        clock=[100.0]
        runner=PairedRunner(self.store,self.session["id"],self.profiles,lambda request:self.receipt(request),clock=lambda:clock[0])
        self.runners.append(runner)
        for i,at in enumerate((100.0,100.5,101.5)):
            clock[0]=at
            runner.offer(self.frame("f"+str(i),observed_at=at-.5,available_at=at,valid_until=at+1))
            self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        clock[0]=102.0
        runner.offer(self.frame("late",observed_at=100.0,available_at=100.0,valid_until=101.0))
        rejected=self.frame("rejected",observed_at=101.9,available_at=102.0,valid_until=103.0)
        runner.executor=lambda request:self.receipt(request,state="rejected",result={"reason":"pose_crowd_budget_exceeded"})
        runner.offer(rejected)
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        rows={row["arm"]:row for row in specialist_delivery(self.store,self.session["id"])}
        a=rows["a"]
        self.assertEqual(a["terminal_reasons"],{"expired":{"expired_before_dispatch":1},"rejected":{"pose_crowd_budget_exceeded":1}})
        self.assertEqual(a["drop_reasons"],{})
        self.assertEqual(a["completion_interval"],{"count":2,"p50_ms":500.0,"p95_ms":1000.0,"max_ms":1000.0})
        self.assertEqual(a["source_to_controller"],{"count":3,"p50_ms":500.0,"p95_ms":500.0,"max_ms":500.0})


if __name__ == "__main__": unittest.main()

import threading
import unittest
import test_service_runtime as fixtures
from live_inputs import live_records


class LiveInputTests(unittest.TestCase):
    setUp=fixtures.CompositionTests.setUp
    tearDown=fixtures.CompositionTests.tearDown
    runtime=fixtures.CompositionTests.runtime
    receipt=fixtures.CompositionTests.receipt
    frame=fixtures.CompositionTests.frame
    settle=fixtures.CompositionTests.settle

    def observation(self,kind="perception.output",**changes):
        value={"site_id":"victoria","camera_id":"den","generation":1,"arm":"a",
            "observed_at":99,"available_at":100,"valid_until":110,"status":"completed",
            "result":{"objects":["book"],"human_label":"private truth"},**changes}
        self.store.append_event(self.session["id"],kind,value)

    def test_only_current_causal_outputs_and_identical_shared_ha_enter(self):
        self.observation()
        self.observation(generation=2)
        self.observation(available_at=101)
        self.observation(site_id="echo")
        self.observation(valid_until=99)
        self.observation(status="rejected")
        self.observation(kind="annotation",label="reading")
        self.observation(kind="ha.observation",ha={"screen":"on"})
        records=live_records(self.store,self.session,1,100)
        self.assertEqual(len(records),3)
        self.assertEqual(records[0]["objects"],["book"])
        self.assertNotIn("human_label",records[0])
        self.assertEqual(records[1]["ha"],records[2]["ha"])
        self.assertEqual(records[1]["evidence_roots"],records[2]["evidence_roots"])
        self.assertEqual({records[1]["arm"],records[2]["arm"]},{"a","b"})

    def test_specialist_completion_flows_to_recording_owned_reasoner(self):
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        offers=[]
        class Reasoner:
            def __init__(self):
                self.lock=threading.RLock(); self.futures=set()
            def offer(self,records,**kw):
                offers.append((records,kw,threading.current_thread())); return []
            def close(self): pass
        runtime.reasoning_factory=lambda *_:Reasoner()
        runtime.set_reasoning(self.session,True)
        runtime.start_session(self.session)
        try:
            runtime.offer(self.session["id"],self.frame())
            self.settle(lambda:bool(offers))  # Real scheduler tick, no UI polling.
            records,config,_=offers[0]
            self.assertEqual({r["arm"] for r in records},{"a","b"})
            self.assertEqual(config["generation"],1)
            # The 1 s scheduler tick may offer concurrently; live() itself must never offer.
            before=len(offers)
            runtime.live(self.session)
            self.assertNotIn(threading.current_thread(),[t for *_,t in offers[before:]])
        finally:
            self.assertTrue(runtime.close(timeout=2)["drained"])
        before=len(offers)
        runtime.reasoning_tick()
        self.assertEqual(len(offers),before)

    def test_reasoning_off_does_not_start_provider_or_stop_specialists(self):
        runtime,recorder=self.runtime(lambda *_:{'state':'ready','profiles':self.profiles})
        calls=[]
        runtime.reasoning_factory=lambda *_:calls.append('unexpected')
        runtime.start_session(self.session)
        try:
            self.observation()
            runtime.reasoning_tick()
            self.assertEqual(calls,[])
            self.assertEqual(recorder.stops,0)
            self.assertFalse(runtime.runners[self.session['id']].snapshot()['closed'])
            runtime.set_reasoning(self.session,False)
            self.assertEqual(recorder.stops,0)
        finally: runtime.close()

    def test_history_read_keeps_source_expiry_without_future_or_generation_leak(self):
        self.observation(observed_at=92,available_at=93,valid_until=95)
        self.observation(observed_at=92,available_at=101,valid_until=110)
        self.observation(generation=2)
        self.assertEqual(live_records(self.store,self.session,1,100),[])
        records=live_records(self.store,self.session,1,100,retain_history=True)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['valid_until'],95)

    def test_kind_bounded_read_matches_the_whole_session_query(self):
        import random
        import live_inputs
        store=self.store
        class Legacy:
            # The same connection, issuing the pre-index (whole session) statement.
            def __init__(inner,db):inner.db=db
            def execute(inner,sql,*args):return inner.db.execute(sql.replace(" INDEXED BY events_session_kind_id",""),*args)
            def __getattr__(inner,name):return getattr(inner.db,name)
        rng=random.Random(7)
        kinds=("perception.output","ha.observation","room.observation","perception.dropped","reasoning.delivery","annotation")
        for _ in range(400):
            observed=rng.uniform(80,101)
            self.observation(kind=rng.choice(kinds),arm=rng.choice("ab"),generation=rng.choice((1,1,2)),
                observed_at=observed,available_at=observed+rng.uniform(0,2),valid_until=observed+rng.uniform(0,6),
                ha={"screen":"on"})
        for cutoff,window,history in ((100,10,False),(100,10,True),(99.5,0,True),(101,20,True),(101,120,True)):
            current=live_records(store,self.session,1,cutoff,window_seconds=window,retain_history=history)
            db=store.db
            store.db=Legacy(db)
            try:expected=live_records(store,self.session,1,cutoff,window_seconds=window,retain_history=history)
            finally:store.db=db
            self.assertEqual(current,expected)
            self.assertTrue(current)
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:live_inputs.live_records(store,self.session,1,100)
        finally:store.db.set_trace_callback(None)
        read=[sql for sql in statements if "FROM events" in sql]
        self.assertEqual(len(read),1)
        self.assertIn("INDEXED BY events_session_kind_id",read[0])
        with store.lock:
            plan=" ".join(str(r[3]) for r in store.db.execute("EXPLAIN QUERY PLAN "+read[0]))
        self.assertIn("events_session_kind_id (session_id=? AND kind=?)",plan)

    def test_oversized_window_fails_without_truncating_evidence(self):
        for _ in range(513):
            self.observation()
        with self.assertRaisesRegex(ValueError,"bound"):
            live_records(self.store,self.session,1,100)

    def test_runtime_withholds_ha_per_arm_and_rejects_old_generation(self):
        runtime,_=self.runtime(lambda *_:{'state':'ready','profiles':self.profiles,
                                          'ha_evidence':{'a':False,'b':True}})
        offers=[]
        class Reasoner:
            def __init__(self):
                self.lock=threading.RLock(); self.futures=set()
            def offer(self, records, **kw):
                offers.append(records); return []
            def close(self): pass
        runtime.start_session(self.session)
        runtime.reasoning_factory=lambda *_:Reasoner()
        try:
            self.observation(kind='ha.observation',ha={'screen':'on'})
            records=live_records(self.store,self.session,1,100)
            runtime.offer_reasoning(self.session['id'],records,generation=1,cutoff=100)
            self.assertEqual([r['arm'] for r in offers[0]],['b'])
            self.assertEqual(len(records),2)
            with self.assertRaises(ValueError):
                runtime.offer_reasoning(self.session['id'],records,generation=2,cutoff=100)
        finally: runtime.close()


if __name__ == '__main__':
    unittest.main()

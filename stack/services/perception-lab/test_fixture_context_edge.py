"""No-network ASGI controls for the one new fixture read route."""
import asyncio
from copy import deepcopy
import hashlib
import json
import threading
import time
import unittest

from fixture_context_edge import FixtureObserverEdge, binding_digest, PATH


def fixture_binding():
    return {"schema":"home-fixture-observer-binding-1","version":1,
        "home_scope":{"site_id":"victoria","room_id":"den","session_id":"owner-session","domain_epoch":"epoch-1"},
        "fixture":{"fixture_id":"fixture-A","device_id":"00"*16,"boot_generation":"1","session_id":"2",
                   "context_digest":"aa"*32,"pins":{"fixture":"bb"*32}},
        "manifest_ref":{"id":"observer-test-1","digest":"cc"*32}}


class ObserverEdgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.binding=fixture_binding();self.calls=[];self.fallback=[]
        self.value={"schema":"home-fixture-execution-inspection-1","version":1,
                    "scope":deepcopy(self.binding["fixture"]),"physical_permission":False}
        async def fallback(scope,receive,send):
            self.fallback.append(scope["path"])
            await send({"type":"http.response.start","status":418,"headers":[]})
            await send({"type":"http.response.body","body":b"{}"})
        def snapshot():
            self.calls.append(True);return deepcopy(self.value)
        self.edge=FixtureObserverEdge(fallback,binding=self.binding,
            token_sha256=hashlib.sha256(b"observer-only").hexdigest(),snapshot=snapshot,timeout_s=.1)

    async def request(self,*,path=PATH,method="GET",query=b"site_id=victoria&room_id=den",changes=(),body=b"",extra=()):
        headers={b"authorization":b"Bearer observer-only",b"x-home-fixture-binding":self.edge.digest.encode(),
                 b"x-home-fixture-actor":b"owner-1"}
        for key,value in changes:
            if value is None:headers.pop(key,None)
            else:headers[key]=value
        messages=[]
        async def receive():return {"type":"http.request","body":body,"more_body":False}
        async def send(message):messages.append(message)
        await self.edge({"type":"http","path":path,"raw_path":path.encode(),"method":method,
                         "query_string":query,"headers":[*headers.items(),*extra]},receive,send)
        return messages[0]["status"],json.loads(messages[-1]["body"])

    async def test_exact_read_returns_fixed_binding_and_same_journal_snapshot(self):
        status,value=await self.request()
        self.assertEqual(status,200);self.assertEqual(value["binding"],self.binding)
        self.assertEqual(value["inspection"],self.value);self.assertEqual(self.calls,[True])

    async def test_wrong_tokens_binding_actor_cookie_or_body_never_read_journal(self):
        vectors=[[(b"authorization",b"Bearer lab-token")],[(b"authorization",b"Bearer submit-token")],
                 [(b"x-home-fixture-binding",b"0"*64)],[(b"x-home-fixture-actor",None)],[(b"cookie",b"browser=secret")]]
        for changes in vectors:
            status,_=await self.request(changes=changes);self.assertNotEqual(status,200)
        self.assertNotEqual((await self.request(body=b"{}"))[0],200)
        self.assertFalse(self.calls)

    async def test_scope_query_duplicates_unknown_fields_and_producer_routes_refuse(self):
        for query in (b"site_id=la&room_id=den",b"site_id=victoria&room_id=den&upstream=http://bad",
                      b"site_id=victoria&room_id=den&site_id=victoria",b"site_id=victoria"):
            self.assertNotEqual((await self.request(query=query))[0],200)
        self.assertEqual((await self.request(method="POST"))[0],404)
        self.assertEqual((await self.request(path="/api/lab/operation"))[0],418)
        self.assertEqual(self.fallback,["/api/lab/operation"])
        self.assertFalse(self.calls)

    async def test_duplicate_critical_header_and_wrong_snapshot_scope_refuse(self):
        self.assertNotEqual((await self.request(extra=[(b"authorization",b"Bearer observer-only")]))[0],200)
        self.value["scope"]["boot_generation"]="2"
        self.assertNotEqual((await self.request())[0],200)

    async def test_generation_change_during_read_cannot_return_old_scope(self):
        def changed():
            self.edge.invalidate();return deepcopy(self.value)
        self.edge.snapshot=changed
        self.assertNotEqual((await self.request())[0],200)

    async def test_timed_out_read_is_unconfirmed_and_does_not_reenter_callback(self):
        started=threading.Event()
        def slow():
            self.calls.append(True);started.set();time.sleep(.03);return deepcopy(self.value)
        self.edge.snapshot=slow;self.edge.timeout_s=.005
        self.assertNotEqual((await self.request())[0],200)
        self.assertTrue(started.is_set())
        self.assertNotEqual((await self.request())[0],200)
        self.assertEqual(self.calls,[True])

    async def test_cancelled_request_seals_read_capability_until_after_thread_finishes(self):
        started=threading.Event();release=threading.Event();finished=threading.Event()
        def slow():
            self.calls.append(True);started.set()
            try:
                release.wait(2);return deepcopy(self.value)
            finally:finished.set()
        self.edge.snapshot=slow;self.edge.timeout_s=.5
        pending=asyncio.create_task(self.request())
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set())
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
            self.assertTrue(self.edge.failed)
            self.assertEqual(self.edge.active,0)
            self.assertFalse(finished.is_set())
            # The original thread still runs. Six replacement reads may not
            # create hidden work after the abandoned request released its slot.
            for _ in range(6):self.assertEqual((await self.request())[0],503)
            self.edge.invalidate()
            self.assertEqual((await self.request())[0],503)
            self.assertEqual(self.calls,[True])
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):await pending
        for _ in range(100):
            if finished.is_set():break
            await asyncio.sleep(.001)
        self.assertTrue(finished.is_set())
        self.assertEqual((await self.request())[0],503)
        self.assertEqual(self.calls,[True])

    async def test_four_cancelled_threads_cannot_be_replaced_past_resource_cap(self):
        release=threading.Event();counters={"started":0,"running":0,"peak":0,"finished":0}
        guard=threading.Lock()
        def slow():
            with guard:
                counters["started"]+=1;counters["running"]+=1
                counters["peak"]=max(counters["peak"],counters["running"])
            try:
                release.wait(2);return deepcopy(self.value)
            finally:
                with guard:counters["running"]-=1;counters["finished"]+=1
        self.edge.snapshot=slow;self.edge.timeout_s=.5
        pending=[asyncio.create_task(self.request()) for _ in range(4)]
        try:
            for _ in range(100):
                with guard:started=counters["started"]
                if started==4:break
                await asyncio.sleep(.001)
            self.assertEqual(started,4)
            self.assertEqual(self.edge.active,4)
            self.assertEqual((await self.request())[0],429)
            for task in pending:task.cancel()
            outcomes=await asyncio.gather(*pending,return_exceptions=True)
            self.assertTrue(all(isinstance(result,asyncio.CancelledError) for result in outcomes))
            self.assertTrue(self.edge.failed);self.assertEqual(self.edge.active,0)
            for _ in range(6):self.assertEqual((await self.request())[0],503)
            with guard:
                self.assertEqual(counters,{"started":4,"running":4,"peak":4,"finished":0})
        finally:
            release.set()
            for task in pending:
                if not task.done():task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
        for _ in range(100):
            with guard:finished=counters["finished"]
            if finished==4:break
            await asyncio.sleep(.001)
        self.assertEqual(finished,4)
        self.assertEqual((await self.request())[0],503)
        with guard:self.assertEqual(counters["started"],4)

    def test_binding_is_closed_and_does_not_accept_version_coercion_or_request_hosts(self):
        for key,value in (("version",True),("upstream","http://bad")):
            bad={**self.binding,key:value}
            with self.assertRaises(ValueError):binding_digest(bad)


if __name__=="__main__":unittest.main()

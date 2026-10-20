import threading
import time
import unittest
from preview_client import LocalPreviewClient, LocalBundleClient, PreviewPump
import test_preview_ingest as fixtures


class ClientTests(unittest.TestCase):
    def test_fixed_local_origin_auth_bounded_read_and_close(self):
        calls=[]
        class Response:
            status=200
            def getheader(self,name,default=None):
                return {"Content-Type":"image/jpeg","Content-Length":"4","X-Victoria-Frame-Metadata":"fixture"}.get(name,default)
            def read1(self,n): return b"jpeg"
        class Connection:
            sock=None
            def request(self,*args,**kw): calls.append((args,kw))
            def getresponse(self): return Response()
            def close(self): calls.append("closed")
        def factory(*args,**kw):
            self.assertEqual(args,("127.0.0.1",8090))
            self.assertEqual(kw,{"timeout":3})
            return Connection()
        client=LocalPreviewClient(lambda:"fixture-token",connection_factory=factory)
        self.assertEqual(calls,[])
        self.assertEqual(client(),(b"jpeg","fixture"))
        self.assertEqual(calls[0][0],("GET","/frame.jpg"))
        self.assertEqual(calls[0][1]["headers"]["Authorization"],"Bearer fixture-token")
        self.assertEqual(calls[-1],"closed")

    def test_transport_errors_do_not_expose_secrets(self):
        def fail(*args,**kwargs): raise ValueError("private-token-private-url")
        with self.assertRaisesRegex(ValueError,"^camera_preview_unavailable$"):
            LocalPreviewClient(lambda:"token",connection_factory=fail)()

    def test_bundle_uses_only_registered_binary_and_calibration_routes(self):
        paths=[]; closed=[]
        class Response:
            status=200
            def __init__(self,path):
                self.data=b'npz' if path=='/frame.npz' else b'{"session":"epoch1"}'
                self.mime='application/octet-stream' if path=='/frame.npz' else 'application/json'
            def getheader(self,name,default=None):
                return {'Content-Length':str(len(self.data)),'Content-Type':self.mime}.get(name,default)
            def read1(self,n):
                data,self.data=self.data[:n],self.data[n:]; return data
        class Connection:
            sock=None
            def request(self,method,path,headers):
                self.path=path; paths.append(path)
            def getresponse(self): return Response(self.path)
            def close(self): closed.append(True)
        client=LocalBundleClient(lambda:'fixture',connection_factory=lambda *a,**k:Connection())
        self.assertEqual(client(),(b'npz',{'session':'epoch1'}))
        self.assertEqual(paths,['/frame.npz','/calibration'])
        self.assertEqual(len(closed),2)
        with self.assertRaisesRegex(ValueError,'unregistered'):
            client._read('/arbitrary')

    def test_oversized_binary_response_is_rejected_before_body_read(self):
        reads=[]
        class Response:
            status=200
            def getheader(self,name,default=None):
                return {'Content-Length':str(4*1024**2+1),'Content-Type':'application/octet-stream'}.get(name,default)
            def read1(self,n): reads.append(n); return b''
        class Connection:
            sock=None
            def request(self,*a,**k): pass
            def getresponse(self): return Response()
            def close(self): pass
        with self.assertRaisesRegex(ValueError,'^camera_bundle_unavailable$'):
            LocalBundleClient(lambda:'fixture',connection_factory=lambda *a,**k:Connection())()
        self.assertEqual(reads,[])

    def test_shutdown_drops_inflight_preview_and_has_no_backlog(self):
        entered=threading.Event(); release=threading.Event(); calls=[]; received=[]
        def fetch():
            calls.append(1); entered.set(); release.wait(2); return b"jpeg","meta"
        pump=PreviewPump(fetch,lambda *args:received.append(args),lambda:True)
        pump.start()
        try:
            self.assertTrue(entered.wait(1))
            pump.stop()
            self.assertTrue(pump.thread.is_alive())
            release.set(); pump.thread.join(1)
            self.assertFalse(pump.thread.is_alive())
            self.assertEqual(received,[])
            self.assertEqual(len(calls),1)
        finally:
            release.set(); pump.stop(); pump.thread.join(2)


class PumpDeliveryTests(unittest.TestCase):
    def run_pump(self,fetch,consume=lambda *_:None,*,until,interval=None):
        pump=PreviewPump(fetch,consume,lambda:True)
        if interval is not None: pump.interval=interval  # fixture speed only
        pump.start()
        try:
            deadline=time.monotonic()+5
            while not until(pump) and time.monotonic()<deadline: time.sleep(.01)
        finally:
            pump.stop();pump.thread.join(3)
        return pump

    def test_fetch_time_counts_toward_interval_without_exceeding_two_hertz(self):
        # Windows monotonic ticks ~15.6 ms and a loaded host can oversleep, so every gap is
        # bounded below (minus clock resolution) and compensation must show in at least one gap.
        for work,low,high in ((.3,.49,.75),(.65,.64,1.0)):
            starts=[]
            def fetch():
                starts.append(time.monotonic());time.sleep(work);return b'jpeg','meta'
            self.run_pump(fetch,until=lambda _:len(starts)>=3)
            gaps=[b-a for a,b in zip(starts,starts[1:])]
            # Old fixed sleep: >= interval + work. Never faster than 0.5 s apart.
            self.assertTrue(all(gap>=low-.02 for gap in gaps),gaps)
            self.assertLess(min(gaps),high,gaps)

    def test_counters_categorize_failures_without_content(self):
        from store import LabError
        outcomes=[ValueError('Commissioned camera capture busy'),None,ValueError('token=abc123'),
                  OSError('/private/path refused'),None]
        def fetch():
            outcome=outcomes.pop(0) if outcomes else None
            if outcome is not None: raise outcome
            return b'jpeg','meta'
        consumed=[]
        def consume(*_):
            consumed.append(1)
            if len(consumed)==1: raise LabError('Lab storage limit reached; existing sessions retained',507,'quota_exceeded')
        pump=self.run_pump(fetch,consume,interval=.01,until=lambda p:p.counters()['attempts']>=6)
        counters=pump.counters()
        self.assertGreaterEqual(counters['ok'],2)
        self.assertEqual(counters['failures'],{'fetch:commissioned_camera_capture_busy':1,'ingest:quota_exceeded':1,
            'fetch:ValueError':1,'fetch:OSError':1})
        self.assertEqual(counters['attempts'],counters['ok']+4)
        self.assertGreaterEqual(counters['last_fetch_ms'],0)
        self.assertNotIn('last_phases_ms',counters)
        self.assertNotIn('abc123',str(counters))

    def test_phase_timings_are_exposed_when_the_client_records_them(self):
        class Fetch:
            last_phases_ms={'jpeg_request':3.0}
            def __call__(self):return b'jpeg','meta'
        pump=self.run_pump(Fetch(),interval=.01,until=lambda p:p.counters()['ok']>=1)
        self.assertEqual(pump.counters()['last_phases_ms'],{'jpeg_request':3.0})


class PollingIntegrationTests(unittest.TestCase):
    setUp=fixtures.PreviewTests.setUp
    tearDown=fixtures.PreviewTests.tearDown
    runtime=fixtures.PreviewTests.runtime
    receipt=fixtures.PreviewTests.receipt
    settle=fixtures.PreviewTests.settle
    source=fixtures.PreviewTests.source
    sample=fixtures.PreviewTests.sample

    def test_recording_polls_source_and_archives_without_browser(self):
        source=self.source()
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        runtime.preview_sources={"den":source}
        calls=[]
        def fetch(): calls.append(1); return self.sample()
        runtime.preview_clients={"den":fetch}
        self.assertEqual(calls,[])
        runtime.start_session(self.session)
        try:
            def complete():
                return len([e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"])==2
            self.settle(complete)
            self.assertGreaterEqual(len(calls),1)
            acquisition=runtime.live(self.session)["acquisition"]
            self.assertGreaterEqual(acquisition["ok"],1)
            self.assertGreaterEqual(acquisition["attempts"],acquisition["ok"])
        finally:
            self.assertTrue(runtime.close(timeout=2)["drained"])
        self.assertFalse(runtime.preview_pumps[self.session["id"]].thread.is_alive())

    def test_preview_media_and_paired_offer_share_one_exact_scan(self):
        release=threading.Event()
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        runtime.preview_sources={"den":self.source()}
        runtime.executor=lambda request:release.wait(2) and self.receipt(request)
        runtime.start_session(self.session)
        self.runners.append(runtime.runners[self.session["id"]])
        scans=[];original=self.store.storage_usage
        self.store.storage_usage=lambda:scans.append(1) or original()
        try:
            result=runtime.ingest_preview(self.session["id"],*self.sample())
            self.assertEqual(result["inference"]["state"],"offered")
            self.assertEqual(len(scans),1)
            kinds=[e["kind"] for e in self.store.list_events(self.session["id"])]
            self.assertEqual(kinds.count("perception.offered"),2)
        finally:
            release.set();self.store.storage_usage=original
            runtime.close(timeout=2)


if __name__=='__main__':
    unittest.main()

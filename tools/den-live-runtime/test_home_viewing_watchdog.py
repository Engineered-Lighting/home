"""Authored callbacks only; no host, network, browser, camera or model IO."""
import threading
import time
import tempfile
from pathlib import Path
import hashlib
import sqlite3
import json
import unittest
import ast
import io
from types import SimpleNamespace
import urllib.error
import urllib.request

import home_viewing_watchdog as watchdog_module

from home_viewing_watchdog import Watchdog, LedgerCleanupProbe, read_private_json, write_binding, binding

REQUEST = '2f217859-efb1-48f8-9193-2695d3a46401'
BINDING = dict(schema='home-viewing-watchdog-binding-1', version=1,
    request_id=REQUEST, site_id='victoria', camera_id='den',
    experiment_id='apartment-viewing', session_id='home-owned', generation=0)


class Clock:
    def __init__(self):
        self.at = 0.
        self.condition = threading.Condition()
    def now(self):
        with self.condition: return self.at
    def wait(self, seconds):
        with self.condition:
            until = self.at + seconds
            self.condition.wait_for(lambda: self.at >= until, timeout=.05)
    def advance(self, seconds):
        with self.condition:
            self.at += seconds
            self.condition.notify_all()


def settle(predicate):
    until = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= until: raise AssertionError('bounded local worker did not settle')
        time.sleep(.002)


class API:
    def __init__(self, clock):
        self.clock, self.calls, self.stopped = clock, [], False
        self.row = dict(id='home-owned', generation=0, request_id=REQUEST,
            site_id='victoria', camera_id='den', experiment_id='apartment-viewing', state='recording')
    def __call__(self, method, suffix, body=None):
        self.calls.append((self.clock.now(), method, suffix, body))
        if method == 'POST':
            assert suffix == '/sessions/home-owned/stop'
            assert body == {'request_id': REQUEST}
            self.stopped = True; self.row['state'] = 'stopped'
            return dict(self.row)
        if suffix == '/sessions': return {'site_id':'victoria', 'items':[dict(self.row)]}
        if suffix == '/capabilities':
            return {'site_id':'victoria','last_worker_cleanup':dict(session_id='home-owned',state='stopped',
                workers={a:dict(state='stopped') for a in ('a','b')})}
        if suffix.endswith('/live'): return {'site_id':'victoria','session_id':'home-owned','session':dict(self.row),'arms':{}}
        if '/events?' in suffix:
            after = int(suffix.split('after_id=')[1].split('&')[0])
            rows = [dict(id=1, session_id='home-owned', kind='workers.cleanup', payload=dict(
                request_id='worker-owner',state='stopped',local_drain='drained',workers={a:dict(state='stopped') for a in ('a','b')}))] if self.stopped and after < 1 else []
            return {'items':rows,'after_id':rows[-1]['id'] if rows else after}
        raise AssertionError((method,suffix))


def reservation_proof(binding):
    return dict(schema='home-viewing-reservation-release-1',version=1,
        request_id=binding['request_id'],session_id=binding['session_id'],generation=binding['generation'],
        status='released_by_terminal_receipts',unresolved=False,evidence_sha256=['a'*64],
        release_scopes=['owned_jobs','paired','reasoning','remote'],
        source_contract_sha256='b'*64)


def installed_image_reader(clock, api, watchdog):
    """Execute the actual CLI retrieval seam with authored IO ports only."""
    tree=ast.parse(Path(watchdog_module.__file__).read_text())
    main=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main')
    reader=next(node for node in main.body if isinstance(node,ast.FunctionDef) and node.name=='image_reader')
    namespace=dict(vars(watchdog_module),time=SimpleNamespace(monotonic=clock.now),watchdog=watchdog,
        evidence_api=api,whole=SimpleNamespace(ORIGIN='https://authored.invalid',PREFIX='/api/perception-lab/v1',urllib=urllib))
    exec(compile(ast.Module(body=[reader],type_ignores=[]),'<actual CLI image_reader>','exec'),namespace)
    return namespace['image_reader']


class LeasedImageAPI(API):
    # Captured live response had 0.1334371 seconds left (1792352544.4259245 - 1792352544.2924874).
    remaining=.1334371
    def __init__(self,clock):
        super().__init__(clock);self.raw=b'authored exact image';self.token='authored-token-not-secret';self.opener=self
        self.range_limit=None;self.headers_patch={};self.raw_patch=lambda value:value
        self.observation=dict(schema='home-lab-arm-observation-1',arm='b',session_id='home-owned',frame_id='frame-one',
            capture_epoch='capture-one',media_ids=['view_one'],site_id='victoria',camera_id='den',valid_until=self.remaining)
        self.frame=dict(id='frame-one',media_id='view_one',generation=0,width=640,height=360,site_id='victoria')
        self.descriptor=dict(id='view_one',session_id='home-owned',generation=0,site_id='victoria',size=len(self.raw),
            size_bytes=len(self.raw),mime_type='image/jpeg',content_type='image/jpeg',
            sha256=hashlib.sha256(self.raw).hexdigest(),metadata=dict(frame_id='frame-one',capture_epoch='capture-one',
                site_id='victoria',camera_id='den',valid_until=self.remaining))
    def __call__(self,method,suffix,body=None):
        if '/descriptor?' in suffix:
            self.calls.append((self.clock.now(),method,suffix,body))
            self.require_fresh(suffix)
            return dict(self.descriptor)
        result=super().__call__(method,suffix,body)
        if suffix.endswith('/live') and not self.stopped:
            result.update(server_time=self.clock.now(),arms={'B':dict(frame=self.frame,frame_id='frame-one',observation=self.observation)})
        return result
    def require_fresh(self,url):
        if self.stopped or self.clock.now()>=self.remaining:
            raise urllib.error.HTTPError(url,410,'Gone',{},io.BytesIO(b'{"error":{"code":"media_unavailable"}}'))
    def open(self,request,timeout):
        self.calls.append((self.clock.now(),'GET',request.full_url,dict(request.headers)))
        self.require_fresh(request.full_url)
        first,last=(int(value) for value in request.get_header('Range').split('=')[1].split('-'))
        if self.range_limit is not None and last-first+1>self.range_limit:
            raise urllib.error.HTTPError(request.full_url,416,'range limit',{},io.BytesIO(b'{"error":{"code":"range_required"}}'))
        raw=self.raw_patch(self.raw[first:last+1]);total=len(self.raw);digest=hashlib.sha256(self.raw).hexdigest();patch=self.headers_patch
        class Response:
            status=206
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,maximum):return raw[:maximum]
            def getheader(self,key,default=None):
                return {'Content-Encoding':'identity','Content-Type':'image/jpeg',
                    'Content-Length':str(len(raw)),'Content-Range':f'bytes {first}-{last}/{total}',
                    'ETag':'"'+digest+'"',**patch}.get(key,default)
        return Response()


class WatchdogTests(unittest.TestCase):
    def test_actual_collector_marks_ordinary_410_missed_without_early_stop_or_false_image_pass(self):
        clock=Clock();api=LeasedImageAPI(clock);api.remaining=-1;kept=[]
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,
            retain=lambda kind,value:kept.append((kind,value)),image_reader=lambda owner,frame:reader(owner,frame),max_images=1)
        reader=installed_image_reader(clock,api,watchdog)
        self.addCleanup(lambda:clock.advance(200));watchdog.arm()
        settle(lambda:watchdog.snapshot().get('missed_images',0)>0 or watchdog.finished.is_set())
        self.assertFalse(api.stopped,'ordinary retired image must not end the viewing window')
        self.assertEqual(watchdog.snapshot()['missed_images'],1)
        self.assertIsNone(watchdog.snapshot()['evidence_error'])
        self.assertFalse(watchdog.snapshot()['image_evidence_complete'])
        self.assertEqual(watchdog.snapshot()['media_http'][0]['status'],410)
        clock.advance(89);settle(lambda:watchdog.finished.is_set())
        self.assertEqual(watchdog.snapshot()['stop_started_at'],89)

    def test_actual_reader_uses_finite_ranges_and_rejects_scope_digest_headers_and_auth(self):
        clock=Clock();api=LeasedImageAPI(clock);api.remaining=100;api.range_limit=65536
        api.raw=b'x'*98000;api.descriptor.update(size=len(api.raw),size_bytes=len(api.raw),sha256=hashlib.sha256(api.raw).hexdigest())
        owner=SimpleNamespace(deadline=90)
        self.assertEqual(installed_image_reader(clock,api,owner)(BINDING,api.frame)['bytes'],api.raw)
        ranges=[c for c in api.calls if c[2].startswith('https:')]
        self.assertEqual(len(ranges),2)
        for failure in ('scope','sha','range','etag'):
            with self.subTest(failure=failure):
                other=LeasedImageAPI(clock)
                if failure=='scope':other.descriptor['session_id']='foreign'
                if failure=='sha':other.raw_patch=lambda value:b'z'*len(value)
                if failure=='range':other.headers_patch={'Content-Range':'bytes 1-2/3'}
                if failure=='etag':other.headers_patch={'ETag':'"'+'0'*64+'"'}
                with self.assertRaises(ValueError):installed_image_reader(clock,other,SimpleNamespace(deadline=90))(BINDING,other.frame)
                if failure=='scope':self.assertFalse([c for c in other.calls if c[2].startswith('https:')])

    def test_media_http_auth_error_is_bounded_diagnostic_and_fatal_without_secret_echo(self):
        clock=Clock();api=LeasedImageAPI(clock)
        def denied(url):raise urllib.error.HTTPError(url,401,'do not echo',{},io.BytesIO(b'{"error":{"code":"unauthorized","message":"do-not-retain-secret"}}'))
        api.require_fresh=denied
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,
            retain=lambda *args:None,image_reader=lambda owner,frame:reader(owner,frame),max_images=1)
        reader=installed_image_reader(clock,api,watchdog)
        self.addCleanup(lambda:clock.advance(200));watchdog.arm();settle(lambda:api.stopped)
        result=watchdog.snapshot()
        self.assertEqual(result.get('media_http',[{}])[0].get('status'),401)
        self.assertEqual(result['media_http'][0]['error_code'],'unauthorized')
        self.assertTrue(result['media_http'][0]['route'].endswith('/descriptor?generation=0'))
        self.assertNotIn('do-not-retain-secret',json.dumps(result))
        self.assertIsNotNone(result['evidence_error'])

    def test_reader_returning_after_hard_deadline_cannot_begin_body_read_and_budget_never_overflows(self):
        clock=Clock();api=LeasedImageAPI(clock);api.remaining=200;reads=[0];original_open=api.open
        def late_open(request,timeout):
            response=original_open(request,timeout);original_read=response.read
            def read(maximum):reads[0]+=1;return original_read(maximum)
            response.read=read;clock.advance(90)
            return response
        api.open=late_open
        with self.assertRaises(watchdog_module.SampleUnavailable):
            installed_image_reader(clock,api,SimpleNamespace(deadline=90))(BINDING,api.frame)
        self.assertEqual(reads[0],0,'an open completing after90 must not start a body read')
        clock=Clock();api=LeasedImageAPI(clock);budget=watchdog_module.MediaBudget();budget.bytes=watchdog_module.CAP-len(api.raw)+1
        with self.assertRaises(watchdog_module.SampleUnavailable) as raised:
            installed_image_reader(clock,api,SimpleNamespace(deadline=90,media_budget=budget))(BINDING,api.frame)
        self.assertTrue(raised.exception.permanent)
        self.assertLessEqual(budget.bytes,watchdog_module.CAP)
        self.assertFalse([c for c in api.calls if c[2].startswith('https:')])

    def test_actual_collector_fetches_133ms_original_before_retention_latency(self):
        clock=Clock();api=LeasedImageAPI(clock);delayed=[False];kept=[]
        def retain(kind,value):
            if kind=='live' and not delayed[0]:delayed[0]=True;clock.advance(.2)
            kept.append((kind,value))
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,
            retain=retain,image_reader=lambda owner,frame:reader(owner,frame),max_images=1)
        reader=installed_image_reader(clock,api,watchdog)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot()['images']==1 or watchdog.finished.is_set())
        self.assertEqual(watchdog.snapshot()['images'],1,'133ms original expired during metadata retention: '+str(watchdog.snapshot()['evidence_error']))
        self.assertFalse(api.stopped,'metadata latency must not abort an otherwise healthy viewing window')
        self.assertEqual(next(v['bytes'] for kind,v in kept if kind=='image'),api.raw)
        clock.advance(89-clock.now());settle(lambda:watchdog.finished.is_set())
        self.assertEqual(watchdog.snapshot()['stop_started_at'],89)

    def test_prearmed_deadline_stops_only_home_session_and_confirms_scoped_cleanup(self):
        clock=Clock();api=API(clock)
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot()['binding'] is not None)
        self.assertFalse(any(call[1]=='POST' for call in api.calls))
        clock.advance(89);settle(lambda:watchdog.snapshot()['finished'])
        result=watchdog.snapshot()
        self.assertEqual(result['outcome'],'cleanup_confirmed')
        self.assertEqual(result['stop_started_at'],89)
        self.assertEqual(result['stop_initiate_at'],89)
        self.assertEqual(result['stop_deadline'],90)
        self.assertTrue(result['stop_deadline_met'])
        self.assertEqual([(c[1],c[2]) for c in api.calls if c[1]=='POST'],[('POST','/sessions/home-owned/stop')])
        self.assertEqual(result['ram_status'],'retired_by_verified_source_contract_and_media_withdrawal')
        with self.assertRaises(ValueError):watchdog.arm()

    def test_blocked_retention_cannot_delay_stop_or_resume_evidence_reads_after_deadline(self):
        clock=Clock();api=API(clock);evidence=API(clock);entered=threading.Event();release=threading.Event()
        def retain(kind,value):
            entered.set();release.wait(2)
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,
            retain=retain,evidence_api=evidence)
        self.addCleanup(release.set);self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();self.assertTrue(entered.wait(1))
        clock.advance(90);settle(lambda:watchdog.snapshot()['finished'])
        self.assertTrue(api.stopped,'Stop must not await fsync/retention')
        release.set();settle(lambda:not watchdog.collector.is_alive())
        self.assertFalse([c for c in evidence.calls if c[0]>=90],'late retention must not resume the collection lane')

    def test_blocked_stop_or_probe_cannot_overrun_cleanup_or_later_claim_confirmation(self):
        for blocked in ('stop','probe'):
            with self.subTest(blocked=blocked):
                clock=Clock();api=API(clock);entered=threading.Event();release=threading.Event()
                def port(method,suffix,body=None):
                    if blocked=='stop' and method=='POST':entered.set();release.wait(2)
                    return api(method,suffix,body)
                def probe(owner):
                    if blocked=='probe':entered.set();release.wait(2)
                    return reservation_proof(owner)
                watchdog=Watchdog(port,lambda:dict(BINDING),probe,monotonic=clock.now,wait=clock.wait)
                watchdog.arm();settle(lambda:watchdog.snapshot()['binding'] is not None)
                clock.advance(90);self.assertTrue(entered.wait(1))
                clock.advance(30)
                try:
                    settle(lambda:watchdog.snapshot()['finished'])
                    self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_unconfirmed')
                finally:release.set()
                settle(lambda:not watchdog.stop_thread.is_alive())
                self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_unconfirmed')

    def test_blocked_retention_still_checks_withdrawal_of_the_original_already_seen(self):
        clock=Clock();api=API(clock);entered=threading.Event();release=threading.Event();withdrawn=[False]
        frame=dict(id='frame-one',media_id='view_one',generation=0,site_id='victoria')
        observation=dict(schema='home-lab-arm-observation-1',arm='b',session_id='home-owned',frame_id='frame-one',
            capture_epoch='capture-one',media_ids=['view_one'],site_id='victoria',camera_id='den')
        def port(method,suffix,body=None):
            if '/descriptor?' in suffix:
                if withdrawn[0]:
                    error=ValueError('media_unavailable');error.status=410;raise error
                return {'still':'available'}
            value=api(method,suffix,body)
            if suffix.endswith('/live') and not api.stopped:
                value['arms']={'B':dict(frame=frame,frame_id='frame-one',observation=observation)}
            return value
        def retain(kind,value):entered.set();release.wait(2)
        watchdog=Watchdog(port,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,retain=retain)
        self.addCleanup(release.set);self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();self.assertTrue(entered.wait(1));clock.advance(89);settle(lambda:api.stopped)
        settle(lambda:watchdog.snapshot().get('cleanup_reason')=='observed_media_not_withdrawn' or watchdog.finished.is_set())
        self.assertFalse(watchdog.finished.is_set(),'seen original must be checked even when its archive write blocks')
        withdrawn[0]=True;clock.advance(.25);settle(lambda:watchdog.finished.is_set())
        self.assertEqual(watchdog.snapshot()['media_withdrawn'],['view_one'])

    def test_blocked_binding_read_cannot_resume_acquisition_after_hard_deadline(self):
        clock=Clock();api=API(clock);entered=threading.Event();release=threading.Event();reads=[]
        def load():
            if threading.current_thread().name=='home-viewing-evidence':entered.set();release.wait(2)
            return dict(BINDING)
        def evidence(method,suffix,body=None):
            reads.append((clock.now(),threading.current_thread().name,method,suffix))
            return api(method,suffix,body)
        watchdog=Watchdog(api,load,lambda _:None,monotonic=clock.now,wait=clock.wait,
            evidence_api=evidence,retain=lambda *args:None)
        self.addCleanup(release.set);self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();self.assertTrue(entered.wait(1));settle(lambda:watchdog.snapshot().get('binding_verified'))
        clock.advance(90);settle(lambda:api.stopped);release.set();settle(lambda:not watchdog.collector.is_alive())
        self.assertFalse([r for r in reads if r[0]>=90 and r[1]=='home-viewing-evidence'])

    def test_full_binding_must_match_authenticated_original_request_before_any_stop(self):
        clock=Clock();api=API(clock);api.row['request_id']='00000000-0000-4000-8000-000000000000'
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot()['binding'] is not None)
        clock.advance(90);time.sleep(.02);clock.advance(30);settle(lambda:watchdog.snapshot()['finished'])
        self.assertFalse([c for c in api.calls if c[1]=='POST'],'an unverified supplied SID cannot authorize Stop')

    def test_lost_ack_is_resolved_only_by_original_uuid_and_generation_zero_is_pinned(self):
        clock=Clock();api=API(clock);pending={**BINDING,'session_id':None,'generation':None}
        watchdog=Watchdog(api,lambda:pending,reservation_proof,monotonic=clock.now,wait=clock.wait)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot().get('binding_verified'))
        self.assertEqual(watchdog.snapshot()['binding']['session_id'],'home-owned')
        self.assertEqual(watchdog.snapshot()['binding']['generation'],0)
        clock.advance(90);settle(lambda:watchdog.snapshot()['finished'])
        self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_confirmed')
        self.assertTrue(all(c[2]!='/sessions' or c[1]=='GET' for c in api.calls))

    def test_no_binding_or_ambiguous_original_request_never_stops_another_session(self):
        for ambiguous in (False,True):
            with self.subTest(ambiguous=ambiguous):
                clock=Clock();api=API(clock)
                def port(method,suffix,body=None):
                    value=api(method,suffix,body)
                    if suffix=='/sessions':value['items'].append({**api.row,'id':'other-owned'})
                    return value
                pending={**BINDING,'session_id':None,'generation':None}
                watchdog=Watchdog(port,lambda:pending if ambiguous else None,reservation_proof,
                    monotonic=clock.now,wait=clock.wait)
                watchdog.arm();clock.advance(120);settle(lambda:watchdog.snapshot()['finished'])
                self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_unconfirmed')
                self.assertFalse(any(c[1]=='POST' for c in api.calls))

    def test_binding_arriving_after_deadline_only_cleans_up_and_does_not_extend_window(self):
        clock=Clock();api=API(clock);holder=[None]
        watchdog=Watchdog(api,lambda:holder[0],reservation_proof,monotonic=clock.now,wait=clock.wait)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();clock.advance(100);holder[0]=dict(BINDING)
        settle(lambda:watchdog.snapshot()['finished'])
        self.assertEqual(watchdog.snapshot()['stop_deadline'],90)
        self.assertEqual(watchdog.snapshot()['cleanup_deadline'],120)
        self.assertEqual(watchdog.snapshot()['stop_started_at'],100)
        self.assertFalse(watchdog.snapshot()['stop_deadline_met'])

    def test_unknown_or_wrong_scoped_reservations_and_missing_worker_release_refuse_confirmation(self):
        for missing in ('probe','scope','worker','drain'):
            with self.subTest(missing=missing):
                clock=Clock();api=API(clock)
                def port(method,suffix,body=None):
                    value=api(method,suffix,body)
                    if missing=='worker' and suffix=='/capabilities':value['last_worker_cleanup']['workers']['b']['state']='indeterminate'
                    if missing=='drain' and '/events?' in suffix:
                        for row in value['items']:row['payload']['local_drain']='pending'
                    return value
                def probe(owner):
                    if missing=='probe':return None
                    value=reservation_proof(owner)
                    if missing=='scope':value['session_id']='foreign'
                    return value
                watchdog=Watchdog(port,lambda:dict(BINDING),probe,monotonic=clock.now,wait=clock.wait)
                watchdog.arm();settle(lambda:watchdog.snapshot().get('binding_verified'))
                clock.advance(90);settle(lambda:api.stopped);clock.advance(30);settle(lambda:watchdog.snapshot()['finished'])
                self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_unconfirmed')

    def test_original_request_reconciliation_does_not_retry_a_lost_stop_that_is_already_terminal(self):
        clock=Clock();api=API(clock)
        def port(method,suffix,body=None):
            value=api(method,suffix,body)
            if method=='POST':raise TimeoutError('authored acknowledgement lost')
            return value
        watchdog=Watchdog(port,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot().get('binding_verified'))
        clock.advance(90);settle(lambda:watchdog.snapshot()['finished'])
        self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_confirmed')
        self.assertEqual(len([c for c in api.calls if c[1]=='POST']),1)

    def test_binding_file_is_private_atomic_exclusive_and_closed_typed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'binding.json'
            self.assertIsNone(read_private_json(path))
            write_binding(path,BINDING)
            self.assertEqual(read_private_json(path),BINDING)
            with self.assertRaises(FileExistsError):write_binding(path,BINDING)
            self.assertFalse(list(path.parent.glob('*.pending-*')))
            for invalid in ({**BINDING,'generation':True},{**BINDING,'request_id':'not-a-uuid'},
                            {**BINDING,'session_id':None},{**BINDING,'extra':'not-allowed'}):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):binding(invalid)

    def test_retention_failure_stops_early_without_a_new_session_and_marks_evidence_incomplete(self):
        clock=Clock();api=API(clock)
        def retain(*args):raise OSError('authored disk full')
        watchdog=Watchdog(api,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,retain=retain)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot()['finished'])
        self.assertTrue(api.stopped)
        self.assertEqual(watchdog.snapshot()['evidence_error'],'OSError')
        self.assertEqual(watchdog.snapshot()['stop_started_at'],0)

    def test_sampled_original_requires_digest_and_descriptor_withdrawal_before_cleanup(self):
        clock=Clock();api=API(clock);kept=[];withdrawn=[False]
        raw=b'authored exact frame';digest=hashlib.sha256(raw).hexdigest()
        observation=dict(schema='home-lab-arm-observation-1',arm='b',session_id='home-owned',frame_id='frame-one',
            capture_epoch='capture-one',media_ids=['view_one'],site_id='victoria',camera_id='den')
        frame=dict(id='frame-one',media_id='view_one',generation=0,width=640,height=360,site_id='victoria')
        def port(method,suffix,body=None):
            if '/descriptor?' in suffix:
                if withdrawn[0]:
                    error=ValueError('media_unavailable');error.status=410;raise error
                return {'still':'available'}
            value=api(method,suffix,body)
            if suffix.endswith('/live') and not api.stopped:
                value['arms']={'B':dict(frame=frame,frame_id='frame-one',observation=observation)}
            return value
        def image(owner,selected):
            return dict(bytes=raw,type='image/jpeg',descriptor=dict(id='view_one',session_id='home-owned',generation=0,site_id='victoria',
                sha256=digest,metadata=dict(frame_id='frame-one',capture_epoch='capture-one',site_id='victoria',camera_id='den')))
        watchdog=Watchdog(port,lambda:dict(BINDING),reservation_proof,monotonic=clock.now,wait=clock.wait,
            retain=lambda kind,value:kept.append((kind,value)),image_reader=image,max_images=1)
        self.addCleanup(lambda:clock.advance(200))
        watchdog.arm();settle(lambda:watchdog.snapshot()['images']==1)
        clock.advance(90);settle(lambda:api.stopped)
        self.assertFalse(watchdog.snapshot()['finished'],'a still-readable original cannot be declared withdrawn')
        withdrawn[0]=True;clock.advance(1);settle(lambda:watchdog.snapshot()['finished'])
        self.assertEqual(watchdog.snapshot()['outcome'],'cleanup_confirmed')
        self.assertEqual(next(v['bytes'] for kind,v in kept if kind=='image'),raw)
        self.assertFalse(watchdog.snapshot()['complete_image_replay'])


class LedgerProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        root=Path(self.directory.name).resolve();self.path=root/'lab.sqlite3'
        self.db=sqlite3.connect(self.path);self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE sessions(id TEXT,request_id TEXT,site_id TEXT,camera_id TEXT,experiment_id TEXT,generation INTEGER,state TEXT);
            CREATE TABLE paired_requests(id TEXT,session_id TEXT,state TEXT,payload TEXT,result TEXT);
            CREATE TABLE remote_specialist_dispatch(request_id TEXT,reference TEXT);
            CREATE TABLE lab_reasoning_requests(id TEXT,session_id TEXT,state TEXT);
            CREATE TABLE jobs(id TEXT,session_id TEXT,state TEXT);
        ''')
        self.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',('home-owned',REQUEST,'victoria','den','apartment-viewing',0,'stopped'))
        self.db.commit();self.pins={}
        for name in LedgerCleanupProbe.SOURCES:
            source=root/name;source.write_bytes(b'authored source fixture; not installed acceptance')
            self.pins[str(source)]=hashlib.sha256(source.read_bytes()).hexdigest()
        self.probe=LedgerCleanupProbe(self.path,self.pins)

    def remote(self, *, terminal=True, state='completed'):
        profile=dict(artifact_sha256='c'*64,engine_epoch='engine-one')
        request=dict(session_id='home-owned',profile=profile)
        receipt=dict(request_id='remote-one',terminal=terminal,state=state,**profile)
        self.db.execute('INSERT INTO paired_requests VALUES(?,?,?,?,?)',('remote-one','home-owned',state,json.dumps(request),json.dumps(receipt)))
        self.db.execute('INSERT INTO remote_specialist_dispatch VALUES(?,?)',('remote-one',json.dumps(dict(request_id='remote-one',session_id='home-owned'))))
        self.db.commit()

    def test_actual_zero_inventory_and_affirmative_remote_receipt_are_read_only(self):
        self.remote();before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        proof=self.probe(BINDING)
        self.assertEqual(proof['status'],'observed_zero')
        self.assertEqual(proof['counts'],dict(owned_jobs=0,paired=0,reasoning=0,remote=0))
        self.assertFalse(proof['unresolved'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)

    def test_scoped_pending_provider_and_job_reservations_remain_unresolved(self):
        self.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)',('paid-one','home-owned','indeterminate'))
        self.db.execute('INSERT INTO jobs VALUES(?,?,?)',('job-one','home-owned','running'))
        self.db.execute('INSERT INTO jobs VALUES(?,?,?)',('foreign-job','unrelated','running'));self.db.commit()
        proof=self.probe(BINDING)
        self.assertEqual((proof['counts']['reasoning'],proof['counts']['owned_jobs']),(1,1))
        self.assertEqual(proof['status'],'unresolved')
        self.assertTrue(proof['unresolved'])

    def test_missing_remote_terminal_proof_is_never_assumed_released(self):
        self.remote(terminal=False)
        self.assertEqual(self.probe(BINDING)['counts']['remote'],1)

    def test_nonterminal_pair_cannot_be_erased_by_worker_process_exit(self):
        self.remote(state='indeterminate')
        proof=self.probe(BINDING)
        self.assertEqual((proof['counts']['paired'],proof['counts']['remote']),(1,1))

    def test_expired_before_dispatch_is_terminal_without_a_remote_reservation(self):
        self.db.execute('INSERT INTO paired_requests VALUES(?,?,?,?,?)',('expired-one','home-owned','expired','{}','{}'))
        self.db.commit()
        self.assertEqual(self.probe(BINDING)['counts']['paired'],0)

    def test_owned_dispatch_with_missing_or_foreign_reference_scope_remains_unresolved(self):
        self.remote()
        for reference in ({'request_id':'remote-one'},dict(request_id='remote-one',session_id='foreign')):
            with self.subTest(reference=reference):
                self.db.execute('UPDATE remote_specialist_dispatch SET reference=?',(json.dumps(reference),));self.db.commit()
                proof=self.probe(BINDING)
                self.assertEqual(proof['counts']['remote'],1)
                self.assertTrue(proof['unresolved'])

    def test_missing_inventory_changed_source_or_foreign_session_refuses_zero_claim(self):
        with self.assertRaisesRegex(ValueError,'scope'):self.probe({**BINDING,'request_id':'00000000-0000-4000-8000-000000000000'})
        self.db.execute('DROP TABLE lab_reasoning_requests');self.db.commit()
        with self.assertRaisesRegex(ValueError,'inventory'):self.probe(BINDING)
        Path(next(iter(self.pins))).write_bytes(b'changed fixture source')
        with self.assertRaisesRegex(ValueError,'source_contract_changed'):self.probe(BINDING)


if __name__=='__main__':unittest.main()

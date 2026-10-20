import asyncio
import base64
import hashlib
import io
import json
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
import numpy as np
import test_paired_runtime as fixtures
from remote_specialist import RemoteSpecialist
from specialist_registry import RegisteredSpecialists
from runtime import PairedRunner


class RemoteTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    settle=fixtures.PairedRuntimeTests.settle

    def execute(self, transport):
        profile={**self.profiles['a'],'device_id':'approved-remote','runtime_revision':'fixture-v1'}
        adapter=RemoteSpecialist(self.store,profile,transport,lambda *_:True)
        frame={'site_id':'victoria','session_id':self.session['id'],'camera_id':'den',
            'capture_epoch':'c1','frame_id':'f1','observed_at':99,'available_at':99,'valid_until':110}
        image=io.BytesIO();Image.new('RGB',(20,20),(255,128,0)).save(image,format='PNG')
        media=self.store.add_media(self.session['id'],image.getvalue(),'image/png',{
            **frame,'width':20,'height':20,'stream':'rgb','session_time_s':0})
        frame['media_ids']=[media['id']]
        runner=PairedRunner(self.store,self.session['id'],{'a':profile,'b':profile},
                            RegisteredSpecialists(self.store,[adapter]),clock=self.store.clock)
        self.runners.append(runner);runner.offer(frame)
        return adapter,runner,profile

    def proof(self,envelope):
        return {'request_id':envelope['request_id'],'body_sha256':envelope['body_sha256'],
            'artifact_sha256':envelope['profile']['artifact_sha256'],'engine_epoch':envelope['profile']['engine_epoch'],
            'state':'completed','terminal':True,'result':{'objects':[]}}

    def test_both_arms_send_identical_lossless_pixels_and_complete(self):
        calls=[]
        def transport(envelope):
            calls.append(envelope);return self.proof(envelope)
        _,runner,_=self.execute(transport)
        self.settle(lambda:len(calls)==2 and all(v is None for v in runner.snapshot()['active'].values()))
        images=[c['body']['image'] for c in calls]
        self.assertEqual(images[0],images[1])
        with Image.open(io.BytesIO(base64.b64decode(images[0]['data_base64']))) as image:
            pixels=np.ascontiguousarray(np.asarray(image)[:,:,::-1])
        self.assertEqual(pixels[0,0].tolist(),[0,128,255])
        self.assertEqual(hashlib.sha256(pixels.tobytes()).hexdigest(),images[0]['bgr_sha256'])
        self.assertEqual(len([e for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output']),2)

    def test_expiry_during_local_image_preparation_never_dispatches(self):
        calls=[]
        def expired_image(_image,frame):
            self.store.clock=lambda:frame['valid_until']+1
            raise ValueError('source expired while preparing')
        with patch('registered_image.RegisteredImage.__call__',expired_image):
            _,runner,_=self.execute(lambda envelope:calls.append(envelope))
            self.settle(lambda:all(v is None for v in runner.snapshot()['active'].values()))
        self.assertEqual(calls,[])
        states=[r[0] for r in self.store.db.execute('SELECT state FROM paired_requests')]
        self.assertIn('cancelled',states)
        self.assertNotIn('indeterminate',states)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM remote_specialist_dispatch').fetchone()[0],0)

    def test_expiry_after_successful_decode_never_dispatches(self):
        from registered_image import RegisteredImage
        original=RegisteredImage.__call__
        calls=[]
        def slow_image(image,frame):
            value=original(image,frame)
            self.store.clock=lambda:frame['valid_until']+1
            return value
        with patch('registered_image.RegisteredImage.__call__',slow_image):
            _,runner,_=self.execute(lambda envelope:calls.append(envelope))
            self.settle(lambda:all(v is None for v in runner.snapshot()['active'].values()))
        self.assertEqual(calls,[])
        self.assertFalse(self.store.db.execute("SELECT 1 FROM paired_requests WHERE state='indeterminate'").fetchone())

    def test_only_the_arm_b_preview_request_is_sent_after_its_original_deadline(self):
        from registered_image import RegisteredImage
        original=RegisteredImage.__call__
        calls=[]
        def slow_image(image,frame):
            value=original(image,frame)
            self.store.clock=lambda:111  # past the original 110, inside the preview's 99+20
            return value
        with patch('registered_image.RegisteredImage.__call__',slow_image),                patch('apartment_viewing_source.development_preview',return_value=20.):
            _,runner,_=self.execute(lambda envelope:calls.append(envelope) or self.proof(envelope))
            self.settle(lambda:all(v is None for v in runner.snapshot()['active'].values()))
        # The unchanged specialist and LA checks see only the extended dispatch deadline.
        self.assertEqual([c['body']['request']['arm'] for c in calls],['b'])
        request=calls[0]['body']['request']
        self.assertEqual(request['preview_timing'],{'policy':'development','original_valid_until':110,'hold_s':20.})
        self.assertEqual(request['frame']['valid_until'],119)
        output=json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='perception.output'").fetchone()[0])
        self.assertEqual(output['valid_until'],110)
        states=dict(self.store.db.execute('SELECT arm,state FROM paired_requests').fetchall())
        self.assertEqual(states,{'a':'cancelled','b':'completed'})

    def test_stop_during_preprocessing_cancels_without_network_dispatch(self):
        from registered_image import RegisteredImage
        original=RegisteredImage.__call__
        calls=[]
        def stop_during_decode(image,frame):
            value=original(image,frame)
            if self.store.get_session(self.session['id'])['state']=='recording':
                self.store.transition(self.session['id'],'stopping','fixture_stop')
            return value
        with patch('registered_image.RegisteredImage.__call__',stop_during_decode):
            _,runner,_=self.execute(lambda envelope:calls.append(envelope))
            self.settle(lambda:all(v is None for v in runner.snapshot()['active'].values()))
        self.assertEqual(calls,[])
        self.assertFalse(self.store.db.execute("SELECT 1 FROM paired_requests WHERE state='indeterminate'").fetchone())

    def test_uncertain_completion_reconciles_without_resending_media(self):
        calls=[]
        def transport(envelope):
            calls.append(envelope);raise TimeoutError('fixture')
        adapter,runner,profile=self.execute(transport)
        self.settle(lambda:len(calls)==2 and all(r[0]=='indeterminate' for r in self.store.db.execute('SELECT state FROM paired_requests')))
        references=[]
        def lookup(reference):
            references.append(reference);return self.proof(reference)
        receipt=adapter.reconcile(calls[0]['request_id'],lookup)
        runner.reconcile(calls[0]['request_id'],receipt)
        self.assertNotIn('body',references[0])
        self.assertEqual(len(calls),2)
        self.store.clock=lambda:200
        with self.assertRaises(ValueError):
            adapter(calls[1]['body']['request'])
        self.assertEqual(len(calls),2)

    def test_mismatched_completion_digest_retains_reservations(self):
        def transport(envelope):
            return {**self.proof(envelope),'body_sha256':'0'*64}
        _,runner,_=self.execute(transport)
        self.settle(lambda:all(r[0]=='indeterminate' for r in self.store.db.execute('SELECT state FROM paired_requests')))
        self.assertTrue(all(runner.snapshot()['active'].values()))
        self.assertFalse(any(e['kind']=='perception.output' for e in self.store.list_events(self.session['id'])))


class Transport:
    """Execute and optional identity lookup callables; counts both."""
    def __init__(self,execute,lookup=None):
        self.execute,self.calls,self.references=execute,[],[]
        if lookup is not None:
            def recorded(reference):
                self.references.append(reference);return lookup(reference)
            self.lookup=recorded
    def __call__(self,envelope):
        self.calls.append(envelope);return self.execute(envelope)


class DeliveryReliabilityTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    settle=fixtures.PairedRuntimeTests.settle
    proof=RemoteTests.proof

    def build(self,transport,*,authorize=lambda *_:True,delay=.01):
        profiles={arm:{**self.profiles[arm],'device_id':'approved-'+arm,'runtime_revision':'fixture-v1'} for arm in ('a','b')}
        adapters=[RemoteSpecialist(self.store,profiles[arm],transport,authorize) for arm in ('a','b')]
        runner=PairedRunner(self.store,self.session['id'],profiles,RegisteredSpecialists(self.store,adapters),clock=self.store.clock)
        runner.reconcile_delay=delay
        self.runners.append(runner)
        return adapters,runner

    def frame(self,frame_id='f1'):
        frame={'site_id':'victoria','session_id':self.session['id'],'camera_id':'den',
            'capture_epoch':'c1','frame_id':frame_id,'observed_at':99,'available_at':99,'valid_until':110}
        image=io.BytesIO();Image.new('RGB',(20,20),(255,128,0)).save(image,format='PNG')
        media=self.store.add_media(self.session['id'],image.getvalue(),'image/png',{
            **frame,'width':20,'height':20,'stream':'rgb','session_time_s':0})
        return {**frame,'media_ids':[media['id']]}

    def rows(self):
        return [dict(r) for r in self.store.db.execute('SELECT arm,state,result FROM paired_requests ORDER BY rowid')]

    def idle(self,runner):
        return lambda:all(v is None for v in runner.snapshot()['active'].values())

    def kinds(self,kind):
        return [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']==kind]

    def assertRejected(self,runner,reason,frames=1):
        self.settle(lambda:len([r for r in self.rows() if r['state']=='rejected'])==2*frames and self.idle(runner)())
        for row in self.rows():
            self.assertEqual(json.loads(row['result'])['result'],{'reason':reason})
        self.assertFalse(self.kinds('perception.indeterminate'))

    def test_unauthorized_media_is_a_terminal_rejection_that_frees_the_arm(self):
        transport=Transport(lambda envelope:self.proof(envelope))
        _,runner=self.build(transport,authorize=lambda phase,_:phase!='prepare_media')
        runner.offer(self.frame())
        self.assertRejected(runner,'media_not_authorized')
        runner.offer(self.frame('f2'))
        self.assertRejected(runner,'media_not_authorized',frames=2)
        self.assertEqual(transport.calls,[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM remote_specialist_dispatch').fetchone()[0],0)

    def test_unauthorized_dispatch_is_rejected_before_the_network(self):
        transport=Transport(lambda envelope:self.proof(envelope))
        _,runner=self.build(transport,authorize=lambda phase,_:phase!='dispatch')
        runner.offer(self.frame())
        self.assertRejected(runner,'dispatch_not_authorized')
        self.assertEqual(transport.calls,[])

    def test_registered_media_integrity_failure_is_rejected_without_dispatch(self):
        transport=Transport(lambda envelope:self.proof(envelope))
        _,runner=self.build(transport)
        frame=self.frame()
        self.store.media_path(self.session['id'],frame['media_ids'][0]).write_bytes(b'tampered')
        runner.offer(frame)
        self.assertRejected(runner,'registered_media_unavailable')
        self.assertEqual(transport.calls,[])

    def test_oversized_encoding_is_rejected_without_dispatch(self):
        from remote_specialist import _Oversize
        transport=Transport(lambda envelope:self.proof(envelope))
        _,runner=self.build(transport)
        with patch('remote_specialist.RemoteSpecialist._encode',side_effect=_Oversize('fixture')):
            runner.offer(self.frame())
            self.assertRejected(runner,'encoded_frame_exceeds_bound')
        self.assertEqual(transport.calls,[])

    def test_exact_worker_pre_execution_rejection_is_terminal_not_indeterminate(self):
        from remote_transport import NotExecuted
        def busy(envelope): raise NotExecuted('ingress_busy')
        transport=Transport(busy)
        _,runner=self.build(transport)
        runner.offer(self.frame())
        self.assertRejected(runner,'worker_ingress_busy')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM remote_specialist_dispatch').fetchone()[0],2)
        transport.execute=lambda envelope:self.proof(envelope)
        runner.offer(self.frame('f2'))
        self.settle(lambda:len(self.kinds('perception.output'))==2 and self.idle(runner)())
        self.assertEqual(len(transport.calls),4)

    def test_unlisted_rejection_reason_is_not_accepted_as_proof(self):
        from remote_transport import NotExecuted
        def invented(envelope): raise NotExecuted('worker_said_so')
        _,runner=self.build(Transport(invented))
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.indeterminate'))==2)
        self.assertEqual({r['state'] for r in self.rows()},{'indeterminate'})
        self.assertTrue(all(runner.snapshot()['active'].values()))

    def test_unproven_completion_reconciles_once_by_identity_lookup(self):
        def lost(envelope): raise TimeoutError('fixture response lost')
        transport=Transport(lost,lambda reference:self.proof(reference))
        _,runner=self.build(transport)
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.output'))==2 and self.idle(runner)())
        self.assertEqual(len(transport.calls),2)
        self.assertEqual(len(transport.references),2)
        self.assertTrue(all('body' not in r and 'image' not in json.dumps(r) for r in transport.references))
        self.assertEqual(len(self.kinds('perception.indeterminate')),2)
        self.assertEqual({d['proof_source'] for d in self.kinds('perception.delivery')},{'identity_lookup'})
        self.assertEqual({r['state'] for r in self.rows()},{'completed'})

    def test_failed_lookup_leaves_arm_indeterminate_without_retry(self):
        def lost(envelope): raise TimeoutError('fixture response lost')
        def unavailable(reference): raise ValueError('remote_specialist_completion_unproven')
        transport=Transport(lost,unavailable)
        _,runner=self.build(transport)
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.lookup_unproven'))==2)
        runner.offer(self.frame('f2'))
        self.assertEqual(len(transport.calls),2)
        self.assertEqual(len(transport.references),2)
        self.assertTrue(all(runner.snapshot()['active'].values()))
        self.assertEqual(sorted(r['state'] for r in self.rows()),['indeterminate','indeterminate','offered','offered'])

    def test_busy_lookup_reply_is_looked_up_again_never_resent(self):
        from remote_transport import NotExecuted
        def lost(envelope): raise TimeoutError('fixture response lost')
        busy=[]
        def lookup(reference):
            busy.append(reference['request_id'])
            seen=busy.count(reference['request_id'])
            if seen<3: raise NotExecuted(('ingress_busy','proxy_busy')[seen-1])  # worker, then LA front
            return self.proof(reference)
        transport=Transport(lost,lookup)
        _,runner=self.build(transport)
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.output'))==2 and self.idle(runner)())
        self.assertEqual(len(transport.calls),2)
        self.assertEqual(len(transport.references),6)
        self.assertTrue(all('body' not in r for r in transport.references))
        self.assertFalse(self.kinds('perception.lookup_unproven'))
        self.assertEqual({d['proof_source'] for d in self.kinds('perception.delivery')},{'identity_lookup'})

    def test_other_exact_lookup_rejections_end_the_lookups(self):
        from remote_transport import NotExecuted
        def lost(envelope): raise TimeoutError('fixture response lost')
        reasons=iter(['unauthorized','admission_closed'])
        def lookup(reference): raise NotExecuted(next(reasons))
        transport=Transport(lost,lookup)
        _,runner=self.build(transport)
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.lookup_unproven'))==2)
        self.assertEqual(len(transport.references),2)
        self.assertEqual({(e['attempts'],e['reason']) for e in self.kinds('perception.lookup_unproven')},{(1,'terminal_proof_missing')})
        self.assertEqual({r['state'] for r in self.rows()},{'indeterminate'})

    def test_close_before_lookup_delay_skips_reconciliation(self):
        entered=threading.Event()
        def lost(envelope):
            entered.set();raise TimeoutError('fixture response lost')
        transport=Transport(lost,lambda reference:self.proof(reference))
        _,runner=self.build(transport,delay=30)
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.indeterminate'))==2)
        runner.close();runner.pool.shutdown(wait=True)
        self.assertEqual(transport.references,[])
        self.assertEqual({r['state'] for r in self.rows()},{'indeterminate'})

    def test_both_arms_share_one_decode_with_identical_image_bytes(self):
        from registered_image import RegisteredImage
        original=RegisteredImage.__call__;decodes=[]
        def counted(image,frame):
            decodes.append(frame['frame_id']);return original(image,frame)
        transport=Transport(lambda envelope:self.proof(envelope))
        with patch('registered_image.RegisteredImage.__call__',counted):
            _,runner=self.build(transport)
            runner.offer(self.frame())
            self.settle(lambda:len(transport.calls)==2 and self.idle(runner)())
        self.assertEqual(decodes,['f1'])
        first,second=[c['body']['image'] for c in transport.calls]
        self.assertEqual(first,second)
        self.assertNotEqual(transport.calls[0]['profile'],transport.calls[1]['profile'])

    def test_delivery_event_carries_dispatch_and_transport_timing_only(self):
        transport=Transport(lambda envelope:self.proof(envelope))
        _,runner=self.build(transport)
        runner.offer(self.frame())
        self.settle(lambda:len(self.kinds('perception.output'))==2 and self.idle(runner)())
        for delivery in self.kinds('perception.delivery'):
            self.assertEqual(delivery['dispatched_at'],100.0)
            self.assertGreater(delivery['body_bytes'],0)
            self.assertGreaterEqual(delivery['encode_ms'],0)
            self.assertGreaterEqual(delivery['transport_ms'],0)
            self.assertNotIn('proof_source',delivery)
        for output in self.kinds('perception.output'):
            self.assertFalse({'dispatched_at','encode_ms','body_bytes','transport_ms'}&set(output))
        stored=[json.loads(r['result']) for r in self.rows()]
        self.assertTrue(all('delivery_timing' not in r for r in stored))

    def test_local_failure_without_unstarted_proof_stays_uncertain(self):
        adapters,runner=self.build(Transport(lambda envelope:self.proof(envelope)),authorize=lambda *_:False)
        frame=self.frame()
        request={'frame':frame,'arm':'a','generation':runner.generation,'profile':adapters[0].profile,
                 'session_id':self.session['id'],'request_id':'0'*64}
        with self.assertRaisesRegex(ValueError,'no proof of unstarted remote dispatch'):
            adapters[0](request)


class Bridge:
    """In-process HTTP exchange with an ASGI app on a loop thread; no network.
    A reply slower than `wait` fails like the controller's socket timeout."""
    sock=None
    def __init__(self,app,loop,wait,log):
        self.app,self.loop,self.wait,self.log=app,loop,wait,log
    def request(self,method,path,body=None,headers=None):
        self.path=path
        scope={'type':'http','scheme':'https','method':method,'path':path,'query_string':b'',
               'headers':[(k.lower().encode(),v.encode()) for k,v in headers.items()]}
        async def exchange():
            sent=[];chunks=[body]
            async def receive():
                if chunks: return {'type':'http.request','body':chunks.pop(),'more_body':False}
                await asyncio.Event().wait()
            async def send(message): sent.append(message)
            await self.app(scope,receive,send)
            return sent
        self.future=asyncio.run_coroutine_threadsafe(exchange(),self.loop)
    def getresponse(self):
        sent=self.future.result(self.wait)
        self.status=sent[0]['status']
        self.headers={k.decode():v.decode() for k,v in sent[0]['headers']}
        self.body=b''.join(m.get('body',b'') for m in sent[1:])
        self.log.append((self.path,self.status))
        return self
    def getheader(self,name,default=None): return self.headers.get(name.lower(),default)
    def read1(self,n):
        data,self.body=self.body[:n],self.body[n:]
        return data
    def close(self): pass


class SlowWorkerIngressTests(unittest.TestCase):
    """The real RemoteHTTP ingress keeps its slot until an orphaned execution ends."""
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    proof=RemoteTests.proof
    frame=DeliveryReliabilityTests.frame
    kinds=DeliveryReliabilityTests.kinds

    def until(self,predicate,seconds=10):
        deadline=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>deadline: self.fail('bounded fixture work did not settle')
            time.sleep(.005)

    def test_lookup_refused_by_a_busy_ingress_is_repeated_until_terminal_proof(self):
        from remote_http import RemoteHTTP
        from remote_transport import RemoteTransport
        proof=self.proof
        class Receiver:
            def __init__(self): self.release,self.executed,self.proofs=threading.Event(),[],{}
            def execute(self,value,*,source_site):
                self.executed.append(value['request_id'])
                self.release.wait(10)  # outlives the controller's response wait
                self.proofs[value['request_id']]=proof(value)
                return self.proofs[value['request_id']]
            def lookup(self,value,*,source_site): return self.proofs[value['request_id']]
        receiver=Receiver()
        app=RemoteHTTP(receiver,[{'site_id':'victoria','cameras':['den'],'token_sha256':hashlib.sha256(b'x'*32).hexdigest()}])
        loop=asyncio.new_event_loop()
        thread=threading.Thread(target=loop.run_forever,daemon=True);thread.start()
        log=[]
        try:
            slow=RemoteTransport('https://worker.example',lambda:'x'*32,connection_factory=lambda *a,**k:Bridge(app,loop,.2,log))
            profiles={arm:{**self.profiles[arm],'device_id':'approved-'+arm,'runtime_revision':'fixture-v1'} for arm in ('a','b')}
            adapters=[RemoteSpecialist(self.store,profiles['a'],slow,lambda *_:True),
                      RemoteSpecialist(self.store,profiles['b'],Transport(lambda envelope:self.proof(envelope)),lambda *_:True)]
            runner=PairedRunner(self.store,self.session['id'],profiles,RegisteredSpecialists(self.store,adapters),clock=self.store.clock)
            runner.reconcile_delay=.1
            self.runners.append(runner)
            runner.offer(self.frame())
            self.until(lambda:('/v1/specialist/lookup',429) in log)
            self.assertTrue(app.busy)
            receiver.release.set()
            self.until(lambda:runner.snapshot()['active']['a'] is None)
        finally:
            receiver.release.set()
            loop.call_soon_threadsafe(loop.stop);thread.join(5);loop.close()
        self.assertEqual(len(receiver.executed),1)
        self.assertNotIn('/v1/specialist/execute',[path for path,_ in log])  # its reply never arrived
        self.assertEqual(log[-1],('/v1/specialist/lookup',200))
        self.assertEqual({status for _,status in log[:-1]},{429})
        a,=[d for d in self.kinds('perception.delivery') if d['arm']=='a']
        self.assertEqual((a['state'],a['proof_source']),('completed','identity_lookup'))
        self.assertEqual(len(self.kinds('perception.indeterminate')),1)
        self.assertFalse(self.kinds('perception.lookup_unproven'))


if __name__=='__main__':unittest.main()

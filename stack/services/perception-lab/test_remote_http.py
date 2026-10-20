import asyncio
import hashlib
import json
import unittest
from remote_http import RemoteHTTP
from remote_transport import RemoteTransport


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        outer=self
        class Receiver:
            def execute(self,value,*,source_site): outer.calls.append(('execute',source_site));return {'terminal':True}
            def lookup(self,value,*,source_site): outer.calls.append(('lookup',source_site));return {'terminal':True}
        self.app=RemoteHTTP(Receiver(),[{'site_id':'victoria','cameras':['den'],
            'token_sha256':hashlib.sha256(b'x'*32).hexdigest()}])

    def call(self,body=None,*,site='victoria',camera='den',token=b'x'*32,scheme='https',path='/v1/specialist/execute'):
        async def run():
            responses=[]
            async def receive(): return {'type':'http.request','body':body if body is not None else json.dumps({'site_id':site,'camera_id':camera}).encode()}
            async def send(value): responses.append(value)
            await self.app({'type':'http','scheme':scheme,'method':'POST','path':path,
                'headers':[(b'authorization',b'Bearer '+token),(b'content-type',b'application/json')]},receive,send)
            return responses[0]['status']
        return asyncio.run(run())

    def test_fixed_operations_and_credential_bound_site_camera(self):
        self.assertEqual(self.call(),200)
        self.assertEqual(self.call(path='/v1/specialist/lookup'),200)
        for args,status in [({'site':'echo'},403),({'camera':'bedroom'},403),({'token':b'z'*32},401),
                            ({'scheme':'http'},403),({'path':'/v1/load-model'},404)]:
            self.assertEqual(self.call(**args),status)
        self.assertEqual(self.calls,[('execute','victoria'),('lookup','victoria')])

    def test_duplicate_keys_and_oversized_body_never_execute(self):
        self.assertEqual(self.call(body=b'{"site_id":"echo","site_id":"victoria","camera_id":"den"}'),409)
        self.assertEqual(self.call(body=b'x'*(6*1024**2+1)),413)
        self.assertEqual(self.calls,[])

    def test_client_redirect_is_not_followed_or_retried(self):
        calls=[]
        class Response:
            status=302
        class Connection:
            def request(self,*args,**kwargs): calls.append(args)
            def getresponse(self): return Response()
            def close(self): calls.append('closed')
        client=RemoteTransport('https://worker.example',lambda:'x'*32,
                               connection_factory=lambda *a,**k:Connection())
        with self.assertRaisesRegex(ValueError,'^remote_specialist_completion_unproven$'): client({})
        self.assertEqual(calls,[('POST','/v1/specialist/execute'),'closed'])

    def test_repeated_handler_cancellation_keeps_slot_until_worker_finishes(self):
        import threading
        entered=threading.Event();release=threading.Event()
        def execute(value,*,source_site):
            entered.set();release.wait(3);return {'terminal':True}
        self.app.receiver.execute=execute
        async def run():
            async def receive(): return {'type':'http.request','body':b'{"site_id":"victoria","camera_id":"den"}'}
            async def send(value): pass
            task=asyncio.create_task(self.app({'type':'http','scheme':'https','method':'POST',
                'path':'/v1/specialist/execute','headers':[(b'authorization',b'Bearer '+b'x'*32),
                (b'content-type',b'application/json')]},receive,send))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                task.cancel();await asyncio.sleep(0);task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(self.app.busy)
                worker=self.app.execution_task
                release.set();await worker;await asyncio.sleep(0)
                self.assertFalse(self.app.busy)
            finally: release.set()
        asyncio.run(run())

    def test_revocation_during_upload_prevents_execution(self):
        async def run():
            responses=[]
            async def receive():
                self.app.replace_grants([])
                return {'type':'http.request','body':b'{"site_id":"victoria","camera_id":"den"}'}
            async def send(value):responses.append(value)
            await self.app({'type':'http','scheme':'https','method':'POST','path':'/v1/specialist/execute',
                'headers':[(b'authorization',b'Bearer '+b'x'*32),(b'content-type',b'application/json')]},receive,send)
            self.assertEqual(responses[0]['status'],403)
        asyncio.run(run())
        self.assertEqual(self.calls,[])

    def test_shutdown_and_revocation_during_execution_preserve_store_until_drain(self):
        import threading
        entered=threading.Event();release=threading.Event();closed=[]
        class Store:
            def close(self):closed.append(True)
        self.app.receiver.store=Store();self.app.owns_store=True
        def execute(value,*,source_site):
            entered.set();release.wait(3);return {'terminal':True,'result':'private-fixture'}
        self.app.receiver.execute=execute
        async def run():
            responses=[]
            async def receive():return {'type':'http.request','body':b'{"site_id":"victoria","camera_id":"den"}'}
            async def send(value):responses.append(value)
            task=asyncio.create_task(self.app({'type':'http','scheme':'https','method':'POST',
                'path':'/v1/specialist/execute','headers':[(b'authorization',b'Bearer '+b'x'*32),
                (b'content-type',b'application/json')]},receive,send))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                self.app.replace_grants([])
                self.assertFalse(await self.app.shutdown(timeout=.01))
                self.assertEqual(closed,[])
                self.assertFalse(self.app.accepting)
                release.set();await task
                self.assertEqual(responses[0]['status'],403)
                self.assertNotIn('private-fixture',str(responses))
                self.assertTrue(await self.app.shutdown(timeout=1))
                self.assertEqual(closed,[True])
            finally:release.set()
        asyncio.run(run())

    def test_only_exact_profile_lookup_miss_is_marked_admitted(self):
        from remote_receiver import ExecutionNotFound
        def reply(path,error):
            def operation(value,*,source_site):raise error
            setattr(self.app.receiver,path.rsplit('/',1)[1],operation)
            async def run():
                responses=[]
                async def receive():return {'type':'http.request','body':b'{"site_id":"victoria","camera_id":"den"}'}
                async def send(value):responses.append(value)
                await self.app({'type':'http','scheme':'https','method':'POST','path':path,
                    'headers':[(b'authorization',b'Bearer '+b'x'*32),(b'content-type',b'application/json')]},receive,send)
                return responses[0]['status'],json.loads(responses[1]['body'])
            return asyncio.run(run())
        from remote_http import READINESS_REPLY
        # The declared constant is what staged-tree checks read; it must be what is sent.
        self.assertEqual(READINESS_REPLY,{'error':'request_unavailable_or_indeterminate','profile_admitted':True})
        self.assertEqual(reply('/v1/specialist/lookup',ExecutionNotFound('miss')),(409,READINESS_REPLY))
        for path,error in (('/v1/specialist/lookup',ValueError('source or worker not admitted')),
                           ('/v1/specialist/execute',ExecutionNotFound('duplicate reference differs'))):
            self.assertEqual(reply(path,error),(409,{'error':'request_unavailable_or_indeterminate'}))
        self.assertFalse(self.app.busy)

    def test_asgi_lifespan_acknowledges_startup_and_shutdown(self):
        async def run():
            messages=iter([{'type':'lifespan.startup'},{'type':'lifespan.shutdown'}]);responses=[]
            async def receive():return next(messages)
            async def send(value):responses.append(value)
            await self.app({'type':'lifespan'},receive,send)
            self.assertEqual([r['type'] for r in responses],['lifespan.startup.complete','lifespan.shutdown.complete'])
        asyncio.run(run())
        self.assertFalse(self.app.accepting)


if __name__=='__main__': unittest.main()

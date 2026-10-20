import asyncio
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import sys
import threading
import unittest
from types import SimpleNamespace
from remote_transport import NotExecuted, RemoteTransport


class Canned:
    """One fixed HTTP response; records requests. No network."""
    sock=None
    def __init__(self,status,body,content_type='application/json',send_error=None):
        self.status,self.body,self.content_type,self.send_error=status,body,content_type,send_error
        self.requests=[]
    def request(self,*args,**kwargs):
        self.requests.append(args)
        if self.send_error: raise self.send_error
    def getresponse(self): return self
    def getheader(self,name,default=None): return {'Content-Type':self.content_type}.get(name,default)
    def read1(self,n):
        data,self.body=self.body[:n],self.body[n:]
        return data
    def close(self): pass


def post(response,*,lookup=False):
    transport=RemoteTransport('https://worker.example',lambda:'x'*32,connection_factory=lambda *a,**k:response)
    return transport.lookup({'request_id':'r'}) if lookup else transport({'request_id':'r'})


def asgi(app,*,status_only=False,body=b'{"site_id":"victoria","camera_id":"den"}',token=b'x'*32,
         content_type=b'application/json',path='/v1/specialist/execute'):
    async def run():
        sent=[]
        chunks=[body]
        async def receive():
            return {'type':'http.request','body':chunks.pop(0) if chunks else b'','more_body':False}
        async def send(value): sent.append(value)
        await app({'type':'http','scheme':'https','method':'POST','path':path,
            'headers':[(b'authorization',b'Bearer '+token),(b'content-type',content_type)]},receive,send)
        return sent[0]['status'],b''.join(m.get('body',b'') for m in sent[1:])
    return asyncio.run(run())


class RealHttpResponseTests(unittest.TestCase):
    def test_complete_keepalive_response_is_read_once(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body=b'{"terminal":true,"state":"completed"}'
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            # Loopback fixture only. Production still mandates HTTPS and scoped CA.
            transport=RemoteTransport('https://127.0.0.1:'+str(server.server_port),lambda:'a'*64,
                                      connection_factory=http.client.HTTPConnection)
            self.assertEqual(transport.lookup({'request_id':'fixture'}),{'terminal':True,'state':'completed'})
        finally:server.shutdown();server.server_close();thread.join(2)


class PreExecutionRejectionTests(unittest.TestCase):
    def assertUnproven(self,response,**kw):
        with self.assertRaises(ValueError) as caught: post(response,**kw)
        self.assertNotIsInstance(caught.exception,NotExecuted)
        self.assertEqual(str(caught.exception),'remote_specialist_completion_unproven')

    def test_only_exact_pre_execution_bodies_prove_non_execution(self):
        for status,body,reason in [(429,{'error':'ingress_busy'},'ingress_busy'),(429,{'reason':'proxy_busy'},'proxy_busy'),
                (401,{'error':'unauthorized'},'unauthorized'),(401,{'reason':'unauthorized'},'unauthorized'),
                (413,{'error':'body_too_large'},'body_too_large'),(413,{'reason':'body_too_large'},'body_too_large'),
                (415,{'error':'json_required'},'json_required'),(415,{'reason':'json_required'},'json_required'),
                (503,{'error':'admission_closed'},'admission_closed'),(503,{'reason':'supervisor_unavailable'},'supervisor_unavailable')]:
            with self.assertRaises(NotExecuted) as caught: post(Canned(status,json.dumps(body).encode()))
            self.assertEqual(caught.exception.reason,reason)

    def test_ambiguous_statuses_timeouts_and_variants_stay_unproven(self):
        for status,body in [(408,{'reason':'body_timeout'}),(409,{'error':'request_unavailable_or_indeterminate'}),
                (503,{'error':'worker_unavailable_or_indeterminate'}),(503,{'reason':'operation_unproven'}),
                (403,{'error':'authorization_changed'}),(403,{'error':'source_not_authorized'}),(400,{'reason':'invalid_request'}),
                (429,{'error':'ingress_busy','extra':1}),(429,{'error':'proxy_busy'}),(503,{'reason':'admission_closed'}),
                (401,{'error':['unauthorized']}),(429,{})]:
            self.assertUnproven(Canned(status,json.dumps(body).encode()))
        self.assertUnproven(Canned(429,b'{"error":"ingress_busy"}',content_type='text/plain'))
        self.assertUnproven(Canned(429,b'not json'))
        self.assertUnproven(Canned(200,b'{"state":"completed"}',send_error=BrokenPipeError()))
        class Lost(Canned):
            def getresponse(self): raise TimeoutError('fixture')
        self.assertUnproven(Lost(200,b''))

    def test_early_rejection_after_interrupted_upload_is_read_not_resent(self):
        response=Canned(429,b'{"error":"ingress_busy"}',send_error=ConnectionResetError())
        with self.assertRaises(NotExecuted):post(response)
        self.assertEqual(len(response.requests),1)

    def test_lookup_rejection_is_typed_but_never_execution_proof(self):
        with self.assertRaises(NotExecuted):post(Canned(429,b'{"error":"ingress_busy"}'),lookup=True)


class IngressContractTests(unittest.TestCase):
    """The exact bodies come from the real ingress code, so drift fails here."""

    def worker(self):
        from remote_http import RemoteHTTP
        class Receiver:
            def execute(self,value,*,source_site): raise AssertionError('executed')
            lookup=execute
        return RemoteHTTP(Receiver(),[{'site_id':'victoria','cameras':['den'],
            'token_sha256':hashlib.sha256(b'x'*32).hexdigest()}])

    def test_worker_ingress_pre_execution_responses(self):
        app=self.worker();app.busy=True
        cases=[asgi(app)]
        app.busy=False
        cases+=[asgi(app,token=b'z'*32),asgi(app,content_type=b'text/plain'),asgi(app,body=b'x'*(6*1024**2+1))]
        app.accepting=False
        cases.append(asgi(app))
        for status,body in cases:
            with self.assertRaises(NotExecuted):post(Canned(status,body))

    def front(self,forward):
        from lifecycle_http import LifecycleHTTP
        app=LifecycleHTTP(SimpleNamespace(launcher=SimpleNamespace(forward=forward)),
            controller_token='c'*32,execution_token='x'*32)
        app.accepting=True
        return app

    def test_la_front_pre_execution_and_passthrough_responses(self):
        calls=[]
        def busy_worker(path,raw):
            calls.append(path);return 429,b'{"error":"ingress_busy"}'
        app=self.front(busy_worker)
        cases=[asgi(app)]
        self.assertEqual(calls,['/v1/specialist/execute'])
        app.proxy_active=2
        cases.append(asgi(app))
        app.proxy_active=0
        cases+=[asgi(app,token=b'z'*32),asgi(app,content_type=b'text/plain'),asgi(app,body=b'x'*(6*1024**2+1))]
        app.accepting=False
        cases.append(asgi(app))
        self.assertEqual(len(calls),1)
        for status,body in cases:
            with self.assertRaises(NotExecuted):post(Canned(status,body))

    def test_la_front_forward_timeout_after_body_is_not_proof(self):
        # Python 3.11+: the front's asyncio.TimeoutError handler also catches a
        # loopback socket timeout raised after the worker received the body.
        def slow_worker(path,raw): raise TimeoutError('timed out')
        status,body=asgi(self.front(slow_worker))
        if sys.version_info>=(3,11):
            # Same bytes as a genuine upload timeout, hence no 408 allowlist entry.
            self.assertEqual((status,json.loads(body)),(408,{'reason':'body_timeout'}))
        with self.assertRaises(ValueError) as caught: post(Canned(status,body))
        self.assertNotIsInstance(caught.exception,NotExecuted)


if __name__=='__main__':unittest.main()

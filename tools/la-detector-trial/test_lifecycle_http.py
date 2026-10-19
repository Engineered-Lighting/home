import json
from types import SimpleNamespace
import unittest
from lifecycle_http import LifecycleHTTP

TOKEN='c'*40
EXECUTION='e'*40


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls=[]
        def start(request_id,session_id,variant='detector'):
            self.calls.append(('start',request_id,session_id));return {'state':'starting'}
        def forward(path,body):
            self.calls.append(('proxy',path,body));return 409,b'{"error":"unknown_request"}'
        self.core=SimpleNamespace(start=start,launcher=SimpleNamespace(forward=forward))
        self.app=LifecycleHTTP(self.core,controller_token=TOKEN,execution_token=EXECUTION)
        self.app.accepting=True

    async def request(self,path,body,*,token=TOKEN,scheme='https'):
        messages=[]
        async def receive():return {'type':'http.request','body':body,'more_body':False}
        async def send(value):messages.append(value)
        await self.app({'type':'http','scheme':scheme,'method':'POST','path':path,'query_string':b'',
            'headers':[(b'authorization',('Bearer '+token).encode()),(b'content-type',b'application/json')]},receive,send)
        return messages[0]['status'],json.loads(messages[1]['body'])

    async def test_start_accepts_only_versioned_fixed_contract(self):
        body={'version':1,'request_id':'request','session_id':'session'}
        status,_=await self.request('/v1/lifecycle/start',json.dumps(body).encode())
        self.assertEqual(status,202)
        self.assertEqual(self.calls,[('start','request','session')])
        body['command']='arbitrary'
        self.assertEqual((await self.request('/v1/lifecycle/start',json.dumps(body).encode()))[0],404)
        self.assertEqual(len(self.calls),1)

    async def test_auth_tls_and_duplicate_fields_reject_before_start(self):
        body=b'{"version":1,"request_id":"one","request_id":"two","session_id":"session"}'
        for kwargs,expected in (({'token':EXECUTION},401),({'scheme':'http'},403),({},400)):
            self.assertEqual((await self.request('/v1/lifecycle/start',body,**kwargs))[0],expected)
        self.assertEqual(self.calls,[])

    async def test_proxy_has_separate_credential_and_exact_paths(self):
        path='/v1/specialist/lookup'
        self.assertEqual((await self.request(path,b'{}'))[0],401)
        self.assertEqual((await self.request(path,b'{}',token=EXECUTION))[0],409)
        self.assertEqual(self.calls,[('proxy',path,b'{}')])
        self.assertEqual((await self.request('/v1/arbitrary',b'{}',token=EXECUTION))[0],401)


if __name__=='__main__':unittest.main()

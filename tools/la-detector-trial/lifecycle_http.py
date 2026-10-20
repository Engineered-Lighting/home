"""Narrow TLS-only lifecycle and fixed detector proxy ASGI interface."""
import asyncio
import hmac
import json
import re
from lifecycle import LifecycleError


class LifecycleHTTP:
    def __init__(self, lifecycle, *, controller_token, execution_token):
        if any(not isinstance(v,str) or not 32<=len(v)<=512 for v in (controller_token,execution_token)):
            raise ValueError('dedicated bounded credentials required')
        self.lifecycle=lifecycle
        self.controller_token=controller_token
        self.execution_token=execution_token
        self.accepting=False
        self.proxy_active=0

    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.lifespan(receive,send)
        if scope['type']!='http':return
        async def reply(status,value):
            data=value if isinstance(value,bytes) else json.dumps(value).encode()
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'no-store')]})
            await send({'type':'http.response.body','body':data})
        if not self.accepting:return await reply(503,{'reason':'supervisor_unavailable'})
        if scope.get('scheme')!='https':return await reply(403,{'reason':'tls_required'})
        path,method=scope.get('path',''),scope.get('method')
        proxy=path in ('/v1/specialist/execute','/v1/specialist/lookup')
        headers={}
        for key,value in scope.get('headers',[]):
            key=key.lower()
            if key in headers:return await reply(400,{'reason':'duplicate_header'})
            headers[key]=value
        token=self.execution_token if proxy else self.controller_token
        if not hmac.compare_digest(headers.get(b'authorization',b''),('Bearer '+token).encode()):
            return await reply(401,{'reason':'unauthorized'})
        if scope.get('query_string'):return await reply(400,{'reason':'query_denied'})
        async def body():
            chunks=[];size=0;limit=6*1024**2 if proxy else 1024
            while True:
                message=await receive()
                if message['type']!='http.request':raise LifecycleError('body_unavailable',400)
                piece=message.get('body',b'');size+=len(piece)
                if size>limit:raise LifecycleError('body_too_large',413)
                chunks.append(piece)
                if not message.get('more_body'):break
            return b''.join(chunks)
        def pairs(items):
            value={}
            for key,item in items:
                if key in value:raise LifecycleError('duplicate_field',400)
                value[key]=item
            return value
        try:
            if method=='POST':
                if headers.get(b'content-type',b'').split(b';')[0]!=b'application/json' or headers.get(b'content-encoding',b'identity')!=b'identity':
                    raise LifecycleError('json_required',415)
                raw=await asyncio.wait_for(body(),timeout=2)
                if proxy:
                    if self.proxy_active>=2:raise LifecycleError('proxy_busy',429)
                    self.proxy_active+=1
                    try:
                        status,data=await asyncio.to_thread(self.lifecycle.launcher.forward,path,raw)
                    finally:self.proxy_active-=1
                    return await reply(status,data)
                value=json.loads(raw,object_pairs_hook=pairs)
                if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1:
                    raise LifecycleError('invalid_request',400)
                if path=='/v1/lifecycle/start' and set(value) in ({'version','request_id','session_id'},{'version','request_id','session_id','variant'}):
                    result=await asyncio.to_thread(self.lifecycle.start,value['request_id'],value['session_id'],value.get('variant','detector'))
                    return await reply(202,result)
                if path=='/v1/lifecycle/stop' and set(value)=={'version','request_id','engine_epoch'}:
                    result=await asyncio.to_thread(self.lifecycle.stop,value['request_id'],value['engine_epoch'])
                    return await reply(200,result)
            elif method=='GET' and path.startswith('/v1/lifecycle/requests/'):
                request_id=path.removeprefix('/v1/lifecycle/requests/')
                result=await asyncio.to_thread(self.lifecycle.status,request_id)
                return await reply(200,result)
            raise LifecycleError('route_not_found',404)
        except LifecycleError as error:return await reply(error.status,{'reason':error.reason})
        except (ValueError,UnicodeError):return await reply(400,{'reason':'invalid_request'})
        except asyncio.TimeoutError:return await reply(408,{'reason':'body_timeout'})
        except Exception:return await reply(503,{'reason':'operation_unproven'})

    async def lifespan(self,receive,send):
        stop=asyncio.Event()
        async def monitor():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(self.lifecycle.tick)
                except Exception:
                    self.accepting=False
                    return
                try:await asyncio.wait_for(stop.wait(),timeout=1)
                except asyncio.TimeoutError:pass
        task=None
        while True:
            message=await receive()
            if message['type']=='lifespan.startup':
                self.accepting=True;task=asyncio.create_task(monitor())
                await send({'type':'lifespan.startup.complete'})
            elif message['type']=='lifespan.shutdown':
                self.accepting=False;stop.set()
                if task:await task
                drained=await asyncio.to_thread(self.lifecycle.close)
                await send({'type':'lifespan.shutdown.complete'} if drained else
                           {'type':'lifespan.shutdown.failed','message':'worker state unresolved; ledger retained'})
                return

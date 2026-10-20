"""Narrow ASGI worker ingress. Provision TLS, grants, receiver and admission.

No standalone startup entrypoint; importing this module opens no listener.
"""
import asyncio
import hashlib
import hmac
import json
from copy import deepcopy
from reasoning import canonical


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate JSON key')
        result[key]=value
    return result


# Declared, not only sent: a supervisor parses a staged tree for this exact
# reply before launching it, so an older worker is refused, not never ready.
READINESS_REPLY={'error':'request_unavailable_or_indeterminate','profile_admitted':True}


class RemoteHTTP:
    def __init__(self, receiver, grants, *, owns_store=False):
        self.receiver=receiver
        self.grant_revision=0
        self.replace_grants(grants)
        self.owns_store=owns_store
        self.accepting=True
        self.idle=asyncio.Event()
        self.idle.set()
        self.busy=False
        self.execution_task=None

    def replace_grants(self,grants):
        """Trusted provisioning operation; no HTTP route can modify grants."""
        grants=deepcopy(tuple(grants))
        seen=set()
        for grant in grants:
            token=grant.get('token_sha256','')
            if (len(token)!=64 or any(c not in '0123456789abcdef' for c in token)
                    or token in seen or grant.get('site_id') not in {'victoria','echo'}
                    or not isinstance(grant.get('cameras'),list) or not grant['cameras']
                    or any(not isinstance(c,str) or not c for c in grant['cameras'])):
                raise ValueError('unique scoped worker grants required')
            seen.add(token)
        self.grants=grants
        self.grant_revision+=1

    async def shutdown(self, timeout=30):
        self.accepting=False
        try:
            await asyncio.wait_for(self.idle.wait(),timeout)
        except TimeoutError:
            return False  # Keep ledger ownership while execution is unresolved.
        if self.owns_store:
            self.receiver.store.close()
            self.owns_store=False
        return True

    async def _lifespan(self,receive,send):
        while True:
            message=await receive()
            if message['type']=='lifespan.startup':
                await send({'type':'lifespan.startup.complete'})
            elif message['type']=='lifespan.shutdown':
                complete=await self.shutdown()
                await send({'type':'lifespan.shutdown.complete'} if complete else
                           {'type':'lifespan.shutdown.failed','message':'worker still active; ledger retained'})
                return

    async def __call__(self,scope,receive,send):
        async def reply(status,value):
            data=canonical(value).encode()
            await send({'type':'http.response.start','status':status,'headers':[
                (b'content-type',b'application/json'),(b'content-length',str(len(data)).encode()),
                (b'cache-control',b'no-store')]})
            await send({'type':'http.response.body','body':data})
        if scope.get('type')=='lifespan': return await self._lifespan(receive,send)
        if scope.get('type')!='http': return
        if not self.accepting: return await reply(503,{'error':'admission_closed'})
        path=scope.get('path')
        if scope.get('scheme')!='https': return await reply(403,{'error':'tls_required'})
        if scope.get('method')!='POST' or path not in {'/v1/specialist/execute','/v1/specialist/lookup'} or scope.get('query_string'):
            return await reply(404,{'error':'route_not_available'})
        headers={}
        for key,value in scope.get('headers',[]):
            key=key.lower()
            if key in headers: return await reply(400,{'error':'duplicate_header'})
            headers[key]=value
        auth=headers.get(b'authorization',b'')
        if not auth.startswith(b'Bearer ') or not 32<=len(auth[7:])<=512:
            return await reply(401,{'error':'unauthorized'})
        token=hashlib.sha256(auth[7:]).hexdigest()
        grant=next((g for g in self.grants if hmac.compare_digest(g['token_sha256'],token)),None)
        if grant is None: return await reply(401,{'error':'unauthorized'})
        grant_revision=self.grant_revision
        if headers.get(b'content-type',b'').split(b';')[0]!=b'application/json' or headers.get(b'content-encoding',b'identity')!=b'identity':
            return await reply(415,{'error':'json_required'})
        if self.busy: return await reply(429,{'error':'ingress_busy'})
        # Bounds ingress concurrency, not GPU resource residency/admission.
        self.busy=True
        self.idle.clear()
        task=None
        try:
            maximum=6*1024**2 if path.endswith('/execute') else 32768
            body=bytearray()
            async def read_body():
                while True:
                    message=await receive()
                    if message['type']!='http.request': raise ValueError()
                    body.extend(message.get('body',b''))
                    if len(body)>maximum: return False
                    if not message.get('more_body',False): break
                return True
            # Jetson's provisioned Python is 3.10; asyncio.timeout starts in
            # 3.11. Preserve one deadline for the whole body using wait_for.
            if not await asyncio.wait_for(read_body(),timeout=10):
                return await reply(413,{'error':'body_too_large'})
            value=json.loads(body,object_pairs_hook=unique_object,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if grant_revision!=self.grant_revision: return await reply(403,{'error':'authorization_changed'})
            if not self.accepting: return await reply(503,{'error':'admission_closed'})
            if not isinstance(value,dict) or value.get('site_id')!=grant['site_id'] or value.get('camera_id') not in grant['cameras']:
                return await reply(403,{'error':'source_not_authorized'})
            operation=self.receiver.execute if path.endswith('/execute') else self.receiver.lookup
            task=asyncio.create_task(asyncio.to_thread(operation,value,source_site=grant['site_id']))
            self.execution_task=task
            # Disconnect/cancellation never means execution ended. Keep the
            # ingress slot until its local thread terminates; durable receiver
            # state independently prevents reexecution after restart.
            try:
                proof=await asyncio.shield(task)
            except asyncio.CancelledError:
                try: await asyncio.shield(task)
                finally: raise
            if grant_revision!=self.grant_revision: return await reply(403,{'error':'authorization_changed'})
            return await reply(200,proof)
        except (ValueError,KeyError,TypeError,TimeoutError) as error:
            # Readiness probes need a positive exact-profile answer; a lookup miss
            # stays 409 and is never proof that an execution did not occur.
            admitted=path.endswith('/lookup') and getattr(error,'profile_admitted',False) is True
            return await reply(409,READINESS_REPLY if admitted else {'error':'request_unavailable_or_indeterminate'})
        except Exception:
            return await reply(503,{'error':'worker_unavailable_or_indeterminate'})
        finally:
            def release(completed=None):
                if completed is not None and not completed.cancelled():
                    completed.exception()  # Consume any orphaned error, without logging payloads.
                self.busy=False
                self.execution_task=None
                self.idle.set()
            if task is not None and not task.done():
                task.add_done_callback(release)
            else:
                release()

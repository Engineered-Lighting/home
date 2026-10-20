"""No-retry HTTPS client for the two fixed remote specialist operations."""
import http.client
import json
import time
from urllib.parse import urlsplit
from reasoning import canonical
from remote_http import unique_object

# Exact bodies that RemoteHTTP (remote_http.py) and the LA LifecycleHTTP front
# (tools/la-detector-trial/lifecycle_http.py) emit only before any receiver
# operation starts. 408 body_timeout is absent: on Python 3.11+ the LA front also
# maps a loopback forward socket timeout, after the worker read the body, to it.
PRE_EXECUTION={(429,'error','ingress_busy'),(429,'reason','proxy_busy'),
    (401,'error','unauthorized'),(401,'reason','unauthorized'),
    (413,'error','body_too_large'),(413,'reason','body_too_large'),
    (415,'error','json_required'),(415,'reason','json_required'),
    (503,'error','admission_closed'),(503,'reason','supervisor_unavailable')}


class NotExecuted(ValueError):
    """An exact worker response proves this request never reached execution."""
    def __init__(self,reason):
        super().__init__('remote_specialist_not_executed')
        self.reason=reason


class RemoteTransport:
    def __init__(self, origin, credential, *, connection_factory=http.client.HTTPSConnection):
        parsed=urlsplit(origin)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {'','/'} or parsed.query or parsed.fragment):
            raise ValueError('fixed HTTPS worker origin required')
        self.host,self.port=parsed.hostname,parsed.port or 443
        self.credential,self.connection_factory=credential,connection_factory

    def __call__(self,envelope): return self._post('/v1/specialist/execute',envelope,6*1024**2)

    def lookup(self,reference): return self._post('/v1/specialist/lookup',reference,32768)

    def _post(self,path,value,maximum):
        connection=None
        rejected=None
        try:
            body=canonical(value).encode()
            if len(body)>maximum: raise ValueError()
            token=self.credential()
            if not isinstance(token,str) or not 32<=len(token)<=512 or any(c in token for c in '\r\n'):
                raise ValueError()
            connection=self.connection_factory(self.host,self.port,timeout=15)
            deadline=time.monotonic()+15
            partial=False
            try:
                connection.request('POST',path,body=body,headers={'Authorization':'Bearer '+token,
                    'Content-Type':'application/json','Accept':'application/json','Accept-Encoding':'identity'})
            except OSError:
                # An ingress may answer and close before reading the body. Read
                # that same response once; only a pre-execution rejection counts.
                partial=True
            response=connection.getresponse()
            if (response.status not in {200}|{s for s,_,_ in PRE_EXECUTION}
                    or response.getheader('Content-Type','').split(';')[0]!='application/json'
                    or response.getheader('Content-Encoding','identity')!='identity'):
                raise ValueError()
            result=bytearray()
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0: raise TimeoutError()
                if connection.sock is not None: connection.sock.settimeout(remaining)
                chunk=response.read1(min(8192,256*1024+1-len(result)))
                if not chunk: break
                result.extend(chunk)
                if len(result)>256*1024: raise ValueError()
            value=json.loads(result,object_pairs_hook=unique_object)
            if not isinstance(value,dict): raise ValueError()
            if response.status==200 and not partial:
                return value
            (field,reason),=value.items()
            if not isinstance(reason,str) or (response.status,field,reason) not in PRE_EXECUTION:
                raise ValueError()
            rejected=reason
        except Exception:
            # No status, timeout or connection loss proves terminal execution.
            raise ValueError('remote_specialist_completion_unproven') from None
        finally:
            if connection is not None: connection.close()
        raise NotExecuted(rejected)

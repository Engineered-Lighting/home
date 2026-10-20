"""Optional exact-route observer of the SAME Victoria hardware report journal.

The configured credential can only read one fixture/home binding. It cannot
publish reports, submit execution, operate Lab or select a journal/upstream.
"""
import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
import re
import threading
from urllib.parse import parse_qsl

PATH="/api/home-context/v1/fixture-execution"
ID=re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
HEX=re.compile(r"[a-f0-9]{64}\Z")
HOME_FIELDS={"site_id","room_id","session_id","domain_epoch"}
FIXTURE_FIELDS={"fixture_id","device_id","boot_generation","session_id","context_digest","pins"}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")


def binding_digest(binding):
    if (type(binding) is not dict or set(binding)!={"schema","version","home_scope","fixture","manifest_ref"}
        or binding["schema"]!="home-fixture-observer-binding-1" or type(binding["version"]) is not int or binding["version"]!=1
        or type(binding["home_scope"]) is not dict or set(binding["home_scope"])!=HOME_FIELDS
        or any(type(v) is not str or not ID.fullmatch(v) for v in binding["home_scope"].values())
        or type(binding["fixture"]) is not dict or set(binding["fixture"])!=FIXTURE_FIELDS):
        raise ValueError("exact observer binding required")
    f=binding["fixture"];m=binding["manifest_ref"]
    if (type(f["fixture_id"]) is not str or not ID.fullmatch(f["fixture_id"])
        or type(f["device_id"]) is not str or not re.fullmatch(r"[a-f0-9]{32}",f["device_id"])
        or any(type(f[k]) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}",f[k]) or int(f[k])>=2**64 for k in ("boot_generation","session_id"))
        or not HEX.fullmatch(f["context_digest"] or "") or type(f["pins"]) is not dict or not f["pins"]
        or len(f["pins"])>16 or any(type(k) is not str or not ID.fullmatch(k) or type(v) is not str or not HEX.fullmatch(v) for k,v in f["pins"].items())
        or type(m) is not dict or set(m)!={"id","digest"} or type(m["id"]) is not str or not ID.fullmatch(m["id"])
        or type(m["digest"]) is not str or not HEX.fullmatch(m["digest"])):
        raise ValueError("observer fixture/profile/manifest binding required")
    return hashlib.sha256(canonical(binding)).hexdigest()


class FixtureObserverEdge:
    def __init__(self,app,*,binding,token_sha256,snapshot,timeout_s=2):
        self.digest=binding_digest(binding)
        if (type(token_sha256) is not str or not HEX.fullmatch(token_sha256) or not callable(snapshot)
            or type(timeout_s) not in (int,float) or not 0<timeout_s<=3):
            raise ValueError("fixed observer read capability required")
        self.app=app;self.binding=deepcopy(binding);self.token_sha256=token_sha256;self.snapshot=snapshot
        self.timeout_s=timeout_s;self.generation=1;self.failed=False;self.active=0;self.lock=threading.Lock()

    def invalidate(self):
        with self.lock:self.generation+=1

    async def __call__(self,scope,receive,send):
        # Unknown context routes stay with the existing restrictive context/Lab
        # dispatcher; this wrapper adds one literal route only.
        if scope.get("type")!="http" or scope.get("path")!=PATH:
            return await self.app(scope,receive,send)
        status=503;value={"schema":"home-fixture-observer-error-1","version":1,"error":"observer_unavailable"}
        admitted=False
        try:
            if scope.get("method")!="GET" or scope.get("raw_path",PATH.encode())!=PATH.encode():
                status=404;raise ValueError("observer_route_denied")
            headers={}
            for key,raw in scope.get("headers",[]):
                key=key.decode("ascii").lower();text=raw.decode("ascii")
                if key in headers:raise ValueError("observer_duplicate_header")
                headers[key]=text
            auth=headers.get("authorization","")
            if (not auth.startswith("Bearer ") or len(auth)>4096 or
                not hmac.compare_digest(hashlib.sha256(auth[7:].encode()).hexdigest(),self.token_sha256)):
                status=401;raise ValueError("observer_credential_denied")
            if headers.get("x-home-fixture-binding")!=self.digest:
                status=409;raise ValueError("observer_binding_mismatch")
            if not ID.fullmatch(headers.get("x-home-fixture-actor","")):
                status=403;raise ValueError("observer_server_actor_required")
            if "cookie" in headers or "transfer-encoding" in headers or headers.get("content-length","0")!="0":
                status=400;raise ValueError("observer_body_or_browser_credential_denied")
            raw_query=scope.get("query_string",b"").decode("ascii")
            pairs=parse_qsl(raw_query,keep_blank_values=True,strict_parsing=True)
            query=dict(pairs)
            if (len(pairs)!=2 or set(query)!={"site_id","room_id"} or
                any(query[k]!=self.binding["home_scope"][k] for k in query)):
                status=403;raise ValueError("observer_scope_denied")
            message=await asyncio.wait_for(receive(),self.timeout_s)
            if message.get("type")!="http.request" or message.get("body",b"") or message.get("more_body",False):
                status=400;raise ValueError("observer_body_denied")
            with self.lock:
                if self.failed:raise ValueError("observer_prior_read_unconfirmed")
                if self.active>=4:
                    status=429;raise ValueError("observer_busy")
                generation=self.generation;self.active+=1;admitted=True
            try:
                inspection=await asyncio.wait_for(asyncio.to_thread(self.snapshot),self.timeout_s)
            except asyncio.CancelledError:
                # Cancelling the await cannot stop an already running snapshot
                # thread. Seal this capability before releasing its admission
                # slot, exactly as for a timed-out unconfirmed read. Propagate
                # cancellation rather than manufacturing an HTTP response.
                with self.lock:self.failed=True
                raise
            except asyncio.TimeoutError:
                with self.lock:self.failed=True
                raise ValueError("observer_read_timeout_unconfirmed") from None
            with self.lock:
                if self.generation!=generation:raise ValueError("observer_generation_changed")
            if (type(inspection) is not dict or inspection.get("schema")!="home-fixture-execution-inspection-1"
                or type(inspection.get("version")) is not int or inspection["version"]!=1
                or inspection.get("physical_permission") is not False
                or any(inspection.get("scope",{}).get(k)!=v for k,v in self.binding["fixture"].items())):
                raise ValueError("observer_snapshot_binding_mismatch")
            value={"schema":"home-fixture-observer-response-1","version":1,"binding":deepcopy(self.binding),"inspection":inspection}
            if len(canonical(value))>65536:raise ValueError("observer_response_bound")
            status=200
        except Exception:
            value={"schema":"home-fixture-observer-error-1","version":1,"error":"observer_read_refused"}
        finally:
            if admitted:
                with self.lock:self.active-=1
        raw=canonical(value)
        await send({"type":"http.response.start","status":status,"headers":[
            (b"content-type",b"application/json"),(b"cache-control",b"private, no-store"),
            (b"x-content-type-options",b"nosniff"),(b"x-home-fixture-binding",self.digest.encode())]})
        await send({"type":"http.response.body","body":raw})

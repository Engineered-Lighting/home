"""Issuer-bound private preference ingress; never a browser or legacy route."""
import asyncio
from dataclasses import dataclass, field
import hmac
import json
import re
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .errors import DomainError
from .personal_memory_contract import PreferenceProposalRequest, PreferenceConfirmation
from .personal_memory_service import PersonalMemoryService
from .personal_memory_storage import restore_preference_record

PREFIX = "/internal/personal-memory/v1/"


@dataclass(frozen=True, slots=True)
class PersonalMemoryBinding:
    issuer_id: str
    credential: bytes = field(repr=False)

    def __post_init__(self):
        if (self.issuer_id not in ("home-assistant:echo","home-assistant:victoria") or
            type(self.credential) is not bytes or re.fullmatch(b"[a-f0-9]{64}",self.credential) is None):
            raise ValueError("dedicated issuer-bound preference credential required")


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError("duplicate field")
        result[key]=value
    return result


def create_personal_memory_ingress(*, binding=None, service=None):
    if ((binding is None)!=(service is None) or binding is not None and
        (type(binding) is not PersonalMemoryBinding or type(service) is not PersonalMemoryService)):
        raise TypeError("complete governed preference ingress required")
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    active = 0

    def reply(status,value):
        return JSONResponse(jsonable_encoder(value),status_code=status,headers={"Cache-Control":"no-store"})

    @app.post(PREFIX+"{operation}")
    async def handle(operation: str, request: Request):
        nonlocal active
        if operation not in ("read","propose","confirm","outcome"):
            return reply(404,{"error":"not_found"})
        if binding is None: return reply(503,{"error":"personal_memory_disabled"})
        if request.scope.get("scheme")!="https": return reply(403,{"error":"secure_transport_required"})
        if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
            return reply(403,{"error":"service_transport_required"})
        if any(name.startswith("x-authenticated-") for name in request.headers):
            return reply(400,{"error":"identity_headers_forbidden"})
        auth = request.headers.getlist("authorization")
        if len(auth)!=1 or not hmac.compare_digest(auth[0].encode("latin-1"),b"Bearer "+binding.credential):
            return reply(401,{"error":"unauthorized"})
        if request.url.query or request.headers.get("content-encoding") is not None:
            return reply(400,{"error":"invalid_request"})
        if request.headers.getlist("content-type") != ["application/json"]:
            return reply(415,{"error":"json_required"})
        lengths=request.headers.getlist("content-length")
        if len(lengths)>1 or lengths and (not re.fullmatch(r"[0-9]{1,10}",lengths[0]) or int(lengths[0])>4096):
            return reply(413,{"error":"body_too_large"})
        if active>=2: return reply(429,{"error":"personal_memory_busy"})
        active+=1
        dispatched=False
        try:
            async with asyncio.timeout(10):
                body=bytearray()
                async for chunk in request.stream():
                    if len(chunk)>4096-len(body): return reply(413,{"error":"body_too_large"})
                    body.extend(chunk)
                value=json.loads(body.decode("utf8"),object_pairs_hook=_unique)
                keys={"version","subject","session_commitment","request"}
                if operation=="confirm": keys.add("gesture_id")
                if type(value) is not dict or set(value)!=keys or type(value["version"]) is not int or value["version"]!=1:
                    raise ValueError("invalid envelope")
                subject,commitment=value["subject"],value["session_commitment"]
                if (type(subject) is not str or not 1<=len(subject)<=64 or subject!=subject.strip() or
                    any(ord(c)<32 or ord(c)==127 for c in subject) or type(commitment) is not str or
                    not re.fullmatch(r"[a-f0-9]{64}",commitment)):
                    raise ValueError("invalid session")
                session=dict(issuer_id=binding.issuer_id,subject=subject,session_commitment=commitment)
                if operation=="read":
                    if value["request"]!={}: raise ValueError("read has no caller-selected scope")
                    parsed=None
                else:
                    model=PreferenceProposalRequest if operation=="propose" else PreferenceConfirmation
                    parsed=restore_preference_record(model,value["request"])
                gesture=None
                if operation=="confirm":
                    raw=value["gesture_id"]
                    if type(raw) is not str or len(raw)!=36 or str(UUID(raw))!=raw: raise ValueError("invalid gesture")
                    gesture=UUID(raw)
                dispatched=True
                if operation=="read": result=await service.read(session)
                elif operation=="confirm": result=await service.confirm(session,parsed,gesture_id=gesture)
                else: result=await getattr(service,operation)(session,parsed)
                return reply(200,{"version":1,"result":result})
        except DomainError as error:
            return reply(error.status_code,{"error":error.code})
        except Exception:
            return reply(503 if dispatched else 422,{"error":"personal_memory_outcome_unknown" if dispatched else "invalid_request"})
        finally: active-=1
    return app

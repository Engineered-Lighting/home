"""Private lighting ingress for the Echo BFF; never a browser or legacy route."""
import asyncio
import hmac
import json
import re
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .errors import DomainError
from .lighting_authority import LightingConsentConfirmation
from .lighting_contract import LightingProposalRequest, from_wire
from .lighting_review import LightingActionConfirmation
from .lighting_service import LightingService
from .personal_memory_api import PersonalMemoryBinding

PREFIX = "/internal/lighting/v1/"
OPERATIONS = ("status", "propose", "confirm", "outcome", "consent-propose", "consent-confirm", "consent-outcome")
MAX_BODY = 4096


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _operation_id(value):
    raw = value.get("operation_id") if type(value) is dict else None
    if type(raw) is not str or len(raw) != 36 or str(UUID(raw)) != raw:
        raise ValueError("invalid lighting operation")
    return UUID(raw)


def create_lighting_ingress(*, binding, service):
    if (type(binding) is not PersonalMemoryBinding or binding.issuer_id != "home-assistant:echo"
            or type(service) is not LightingService):
        raise TypeError("separately provisioned Echo lighting ingress required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    active = 0

    def reply(status, value):
        return JSONResponse(jsonable_encoder(value), status_code=status, headers={"Cache-Control": "no-store"})

    @app.post(PREFIX + "{operation}")
    async def handle(operation: str, request: Request):
        nonlocal active
        if operation not in OPERATIONS:
            return reply(404, {"error": "not_found"})
        if request.scope.get("scheme") != "https":
            return reply(403, {"error": "secure_transport_required"})
        if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
            return reply(403, {"error": "service_transport_required"})
        if any(name.startswith("x-authenticated-") for name in request.headers):
            return reply(400, {"error": "identity_headers_forbidden"})
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("latin-1"), b"Bearer " + binding.credential):
            return reply(401, {"error": "unauthorized"})
        if request.url.query or request.headers.get("content-encoding") is not None:
            return reply(400, {"error": "invalid_request"})
        if request.headers.getlist("content-type") != ["application/json"]:
            return reply(415, {"error": "json_required"})
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths[0]) or int(lengths[0]) > MAX_BODY):
            return reply(413, {"error": "body_too_large"})
        if active >= 2:
            return reply(429, {"error": "lighting_busy"})
        active += 1
        dispatched = False
        try:
            # Two homes, each allowed 10 s to act and 3 s to verify, plus lookups.
            async with asyncio.timeout(45):
                body = bytearray()
                async for chunk in request.stream():
                    if len(chunk) > MAX_BODY - len(body):
                        return reply(413, {"error": "body_too_large"})
                    body.extend(chunk)
                value = json.loads(body.decode("utf8"), object_pairs_hook=_unique)
                if (type(value) is not dict or set(value) != {"version", "subject", "session_commitment", "request"}
                        or type(value["version"]) is not int or value["version"] != 1):
                    raise ValueError("invalid envelope")
                subject, commitment, supplied = value["subject"], value["session_commitment"], value["request"]
                if (type(subject) is not str or not 1 <= len(subject) <= 64 or subject != subject.strip()
                        or any(ord(c) < 32 or ord(c) == 127 for c in subject) or type(commitment) is not str
                        or not re.fullmatch(r"[a-f0-9]{64}", commitment) or type(supplied) is not dict):
                    raise ValueError("invalid session")
                session = dict(issuer_id=binding.issuer_id, subject=subject, session_commitment=commitment)
                if operation == "status":
                    if supplied != {}:
                        raise ValueError("status has no caller-selected scope")
                    parsed = None
                elif operation == "propose":
                    parsed = from_wire(LightingProposalRequest, supplied)
                elif operation == "confirm":
                    parsed = from_wire(LightingActionConfirmation, supplied)
                elif operation == "consent-confirm":
                    parsed = from_wire(LightingConsentConfirmation, supplied)
                else:
                    if set(supplied) != {"version", "operation_id"} or supplied["version"] != 1:
                        raise ValueError("invalid lookup")
                    parsed = _operation_id(supplied)
                dispatched = True
                if operation == "status":
                    result = await service.status(session)
                elif operation.startswith("consent-"):
                    result = await getattr(service, operation.replace("-", "_"))(session, parsed)
                else:
                    result = await getattr(service, operation)(session, parsed)
                return reply(200, {"version": 1, "result": result})
        except DomainError as error:
            return reply(error.status_code, {"error": error.code})
        except Exception:
            return reply(503 if dispatched else 422,
                         {"error": "lighting_outcome_unknown" if dispatched else "invalid_request"})
        finally:
            active -= 1
    return app

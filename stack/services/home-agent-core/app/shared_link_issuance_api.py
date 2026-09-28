"""Private coordinator issuance boundary, disabled until separately provisioned.

The coordinator credential may assert already authenticated per-home sessions;
it must never be distributed to a browser or used as a generic BFF credential.
Trusted Victoria handoff and restore admission are deployment prerequisites.
"""
import asyncio
from dataclasses import dataclass, field
import hmac
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import ServiceIdentity
from .shared_link_commitments import SharedLinkCeremonyContext
from .shared_link_issuance_service import SharedLinkIssuanceService

BEGIN_PATH = "/internal/shared-identity/v1/link-issuance"
RECOVER_PATH = "/internal/shared-identity/v1/link-issuance-outcome"
MAX_BODY = 2048
TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class LinkIssuanceBinding:
    credential: bytes = field(repr=False)

    def __post_init__(self):
        if type(self.credential) is not bytes or re.fullmatch(b"[0-9a-f]{64}", self.credential) is None:
            raise ValueError("dedicated coordinator credential required")


class IssuanceRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    subject: str = Field(min_length=1, max_length=64, repr=False)
    context: SharedLinkCeremonyContext = Field(repr=False)

    @field_validator("subject")
    @classmethod
    def exact_subject(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid subject")
        return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate field")
        result[key] = value
    return result


def create_link_issuance_ingress(*, binding=None, service=None):
    if ((binding is None) != (service is None) or binding is not None and
        (type(binding) is not LinkIssuanceBinding or type(service) is not SharedLinkIssuanceService)):
        raise TypeError("dedicated issuance dependencies required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    active = 0

    def reply(status, payload):
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    @app.post(BEGIN_PATH)
    @app.post(RECOVER_PATH)
    async def handle(request: Request):
        nonlocal active
        if binding is None: return reply(503, {"error": "issuance_disabled"})
        if request.scope.get("scheme") != "https": return reply(403, {"error": "secure_transport_required"})
        if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
            return reply(403, {"error": "service_transport_required"})
        if any(name.startswith("x-authenticated-") for name in request.headers):
            return reply(400, {"error": "identity_headers_forbidden"})
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("latin-1"), b"Bearer "+binding.credential):
            return reply(401, {"error": "unauthorized"})
        if request.url.query or request.headers.get("content-encoding") is not None:
            return reply(400, {"error": "invalid_request"})
        if request.headers.getlist("content-type") != ["application/json"]:
            return reply(415, {"error": "json_required"})
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths[0]) or int(lengths[0]) > MAX_BODY):
            return reply(413, {"error": "body_too_large"})
        if active >= 2: return reply(429, {"error": "issuance_busy"})
        active += 1
        accepted = False
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                body = bytearray()
                async for chunk in request.stream():
                    if len(chunk) > MAX_BODY-len(body): return reply(413, {"error": "body_too_large"})
                    body.extend(chunk)
                envelope = json.loads(body.decode("utf-8"), object_pairs_hook=_unique)
                if (not isinstance(envelope, dict) or set(envelope) != {"version", "request"} or
                    type(envelope["version"]) is not int or envelope["version"] != 1):
                    raise ValueError("invalid envelope")
                value = IssuanceRequest.model_validate_json(json.dumps(envelope["request"]))
                identity = ServiceIdentity(value.subject)
                accepted = True
                operation = service.recover if request.url.path == RECOVER_PATH else service.begin
                result = await operation(identity, value.context)
                # The resolved principal/person/binding and private beginning
                # stay within Core. Only the admitted receipt leaves this API.
                return reply(200, {"version": 1, "issuance": result.issuance.model_dump(mode="json")})
        except Exception:
            return reply(503 if accepted else 422, {"error": "issuance_outcome_unknown" if accepted else "invalid_request"})
        finally:
            active -= 1

    return app

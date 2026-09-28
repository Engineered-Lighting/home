"""Disabled separate session-revocation ingress, never mounted on legacy Core."""
import asyncio
from dataclasses import dataclass, field
import hmac
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .shared_auth_proof import Issuer
from .shared_link_session_revocation import SharedLinkSessionRevocation, SharedLinkSessionRevocationDatabase

PATH = "/internal/shared-identity/v1/session-revocations"
TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class SessionRevocationBinding:
    issuer_id: Issuer
    credential: bytes = field(repr=False)

    def __post_init__(self):
        if (self.issuer_id not in ("home-assistant:echo", "home-assistant:victoria") or
            type(self.credential) is not bytes or re.fullmatch(b"[0-9a-f]{64}", self.credential) is None):
            raise ValueError("invalid session ingress binding")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate field")
        result[key] = value
    return result


def create_session_revocation_ingress(*, binding=None, database=None):
    if (binding is None) != (database is None): raise ValueError("complete session ingress binding required")
    if binding is not None and (type(binding) is not SessionRevocationBinding or
        type(database) is not SharedLinkSessionRevocationDatabase or database.issuer_id != binding.issuer_id):
        raise ValueError("dedicated issuer-bound session adapter required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    active = 0

    def reply(status, code):
        return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})

    @app.post(PATH)
    async def revoke(request: Request):
        nonlocal active
        if binding is None: return reply(503, "session_ingress_disabled")
        if request.scope.get("scheme") != "https": return reply(403, "secure_transport_required")
        if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
            return reply(403, "service_transport_required")
        if any(name.startswith("x-authenticated-") for name in request.headers):
            return reply(400, "identity_headers_forbidden")
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1 or not hmac.compare_digest(authorization[0].encode("latin-1"), b"Bearer "+binding.credential):
            return reply(401, "unauthorized")
        if request.url.query or request.headers.get("content-encoding") is not None:
            return reply(400, "invalid_request")
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            return reply(415, "json_required")
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths[0]) or int(lengths[0]) > 1024):
            return reply(413, "body_too_large")
        if active >= 2: return reply(429, "session_ingress_busy")
        active += 1
        dispatched = False
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                body = bytearray()
                async for chunk in request.stream():
                    if len(chunk) > 1024-len(body): return reply(413, "body_too_large")
                    body.extend(chunk)
                envelope = json.loads(body.decode("utf-8"), object_pairs_hook=_unique)
                if (not isinstance(envelope, dict) or set(envelope) != {"version", "revocation"} or
                    type(envelope["version"]) is not int or envelope["version"] != 1):
                    return reply(422, "invalid_revocation")
                value = SharedLinkSessionRevocation.model_validate_json(json.dumps(envelope["revocation"]))
                dispatched = True
                receipt = await database.revoke(value)
                return JSONResponse({"version": 1, "revocation": receipt.model_dump(mode="json")}, headers={"Cache-Control": "no-store"})
        except (ValueError, UnicodeError):
            return reply(503, "revocation_outcome_unknown") if dispatched else reply(422, "invalid_revocation")
        except Exception:
            return reply(503, "revocation_outcome_unknown")
        finally: active -= 1

    return app

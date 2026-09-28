"""Separate, disabled-by-default proof ingress; never mount on legacy Core.

The dedicated BFF proof issuer authenticates HA. This service authenticates that
issuer's credential, not the human. No browser identity headers are authoritative.
No production entry point or deployment configuration enables this factory yet.
"""
import asyncio
from dataclasses import dataclass, field
import hmac
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from starlette.requests import ClientDisconnect

from .shared_auth_proof import FreshAuthSubmission, SharedAuthProofDatabase, SharedLinkProofDatabase, Issuer

PATH = "/internal/shared-identity/v1/auth-proofs"
LOOKUP_PATH = "/internal/shared-identity/v1/auth-proof-outcome"
MAX_BODY = 4096
REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class ProofIngressBinding:
    issuer_id: Issuer
    credential: bytes = field(repr=False)

    def __post_init__(self):
        if (self.issuer_id not in ("home-assistant:echo", "home-assistant:victoria") or
            not isinstance(self.credential, bytes) or
            re.fullmatch(b"[0-9a-f]{64}", self.credential) is None):
            raise ValueError("invalid proof ingress binding")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def create_proof_ingress(*, binding: ProofIngressBinding | None = None,
                         database: SharedAuthProofDatabase | None = None) -> FastAPI:
    if (binding is None) != (database is None):
        raise ValueError("proof ingress requires both binding and database")
    if binding is not None and database.issuer_id != binding.issuer_id:
        raise ValueError("proof database issuer mismatch")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    in_flight = 0

    def reply(status, code):
        return JSONResponse({"error": code}, status_code=status,
                            headers={"Cache-Control": "no-store"})

    @app.post(PATH)
    async def issue(request: Request):
        return await process(request, lookup=False)

    # The older standalone proof kernel has no challenge-bound outcome lookup.
    # Keep that factory's route surface unchanged, including its disabled form.
    if type(database) is SharedLinkProofDatabase:
        @app.post(LOOKUP_PATH)
        async def inspect(request: Request):
            return await process(request, lookup=True)

    async def process(request: Request, *, lookup: bool):
        nonlocal in_flight
        if binding is None:
            return reply(503, "proof_ingress_disabled")
        # TLS termination must be provisioned explicitly. Caller-supplied
        # Forwarded/X-Forwarded-Proto headers do not establish this ASGI scheme.
        if request.scope.get("scheme") != "https":
            return reply(403, "secure_transport_required")
        if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
            return reply(403, "service_transport_required")
        if any(name.startswith("x-authenticated-") for name in request.headers):
            return reply(400, "identity_headers_forbidden")
        supplied = request.headers.getlist("authorization")
        if len(supplied) != 1 or not hmac.compare_digest(
            supplied[0].encode("latin-1"), b"Bearer " + binding.credential):
            return reply(401, "unauthorized")
        if request.url.query or request.headers.get("content-encoding") is not None:
            return reply(400, "invalid_request")
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            return reply(415, "json_required")
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or (lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths[0]) or int(lengths[0]) > MAX_BODY)):
            return reply(413, "body_too_large")
        if in_flight >= 2:
            return reply(429, "proof_ingress_busy")
        in_flight += 1
        dispatched = False
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                body = bytearray()
                async for chunk in request.stream():
                    if len(chunk) > MAX_BODY - len(body):
                        return reply(413, "body_too_large")
                    body.extend(chunk)
                envelope = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
                if (not isinstance(envelope, dict) or set(envelope) != {"version", "proof"} or
                    type(envelope["version"]) is not int or envelope["version"] != 1):
                    return reply(422, "invalid_proof")
                # Strict JSON mode accepts RFC3339/UUID wire strings while still
                # rejecting coerced numbers, unknown fields and issuer claims.
                submission = FreshAuthSubmission.model_validate_json(json.dumps(envelope["proof"]))
                dispatched = True
                receipt = await database.inspect(submission) if lookup else await database.issue(submission)
                if receipt is None or receipt.issuer_id != binding.issuer_id:
                    return reply(503, "proof_outcome_unknown")
                return JSONResponse({"version": 1, "proof": receipt.model_dump(mode="json")},
                                    headers={"Cache-Control": "no-store"})
        except (ValueError, UnicodeError, ValidationError, ClientDisconnect):
            return reply(503, "proof_outcome_unknown") if dispatched else reply(422, "invalid_proof")
        except DBAPIError as error:
            state = getattr(error.orig, "sqlstate", None)
            if state == "42501":
                return reply(403, "proof_authority_unavailable")
            if state == "22023":
                return reply(422, "invalid_proof")
            if state in ("23505", "40001", "40P01"):
                return reply(409, "proof_conflict")
            return reply(503, "proof_outcome_unknown")
        except Exception:
            # A lost commit acknowledgement must not become a new proof ID.
            # Return no raw SQL, credentials, submitted subject or timestamps.
            return reply(503, "proof_outcome_unknown")
        finally:
            in_flight -= 1

    return app

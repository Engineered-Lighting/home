"""Separate private Echo-BFF ingress factory; no runtime entry point enables it.

The dedicated credential authenticates the BFF's session assertion, not a human.
BFF integration must enforce fresh session authentication and origin/CSRF for
both operations. Never mount this on legacy Core or a public browser listener.
"""
import asyncio
from dataclasses import dataclass, field
import hmac
import json
import re
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import ServiceIdentity
from .shared_link_review_service import SharedLinkReviewService
from .shared_link_review_assembly import SharedLinkReviewAssembly, ReviewEvidence

REVIEW_PATH = "/internal/shared-identity/v1/link-review"
CONFIRM_PATH = "/internal/shared-identity/v1/link-confirmation"
OUTCOME_PATH = "/internal/shared-identity/v1/link-outcome"
PREPARE_PATH = "/internal/shared-identity/v1/link-review-prepare"
MAX_BODY = 2048


@dataclass(frozen=True, slots=True)
class LinkReviewBinding:
    credential: bytes = field(repr=False)

    def __post_init__(self):
        if type(self.credential) is not bytes or re.fullmatch(b"[0-9a-f]{64}", self.credential) is None:
            raise ValueError("dedicated Echo BFF credential required")


class ReviewRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    subject: str = Field(min_length=1, max_length=64, repr=False)
    ceremony_id: UUID = Field(repr=False)
    session_commitment: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)

    @field_validator("subject")
    @classmethod
    def exact_subject(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid subject")
        return value


class ConfirmationRequest(ReviewRequest):
    gesture_id: UUID = Field(repr=False)
    reviewed_digest: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class PreparationRequest(ReviewEvidence):
    subject: str = Field(min_length=1, max_length=64, repr=False)

    @field_validator("subject")
    @classmethod
    def exact_subject(cls, value):
        return ReviewRequest.exact_subject(value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate field")
        result[key] = value
    return result


def create_link_review_ingress(*, binding=None, service=None, assembly=None):
    if ((binding is None) != (service is None) or
        binding is not None and (type(binding) is not LinkReviewBinding or type(service) is not SharedLinkReviewService)):
        raise TypeError("dedicated review ingress dependencies required")
    if assembly is not None and (type(assembly) is not SharedLinkReviewAssembly or not assembly.uses_review(service)):
        raise TypeError("same review service required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    in_flight = 0

    def reply(status, payload):
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    @app.post(REVIEW_PATH)
    @app.post(CONFIRM_PATH)
    @app.post(OUTCOME_PATH)
    @app.post(PREPARE_PATH)
    async def handle(request: Request):
        nonlocal in_flight
        if binding is None: return reply(503, {"error": "linking_disabled"})
        preparing = request.url.path == PREPARE_PATH
        if preparing and assembly is None: return reply(503, {"error": "linking_disabled"})
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
        max_body = 8192 if preparing else MAX_BODY
        if len(lengths) > 1 or lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths[0]) or int(lengths[0]) > max_body):
            return reply(413, {"error": "body_too_large"})
        if in_flight >= 2: return reply(429, {"error": "linking_busy"})
        in_flight += 1
        accepted = False
        try:
            async with asyncio.timeout(10):
                body = bytearray()
                async for chunk in request.stream():
                    if len(chunk) > max_body-len(body): return reply(413, {"error": "body_too_large"})
                    body.extend(chunk)
                envelope = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
                if (not isinstance(envelope, dict) or set(envelope) != {"version", "request"} or
                    type(envelope["version"]) is not int or envelope["version"] != 1):
                    raise ValueError("invalid request")
                confirming = request.url.path == CONFIRM_PATH
                model = PreparationRequest if preparing else ConfirmationRequest if confirming else ReviewRequest
                value = model.model_validate_json(json.dumps(envelope["request"]))
                identity = ServiceIdentity(value.subject)  # Credential is provisioned to Echo only.
                accepted = True
                if preparing:
                    result = await assembly.prepare(identity, ReviewEvidence.model_validate(value.model_dump(exclude={"subject"})))
                elif confirming:
                    result = await service.confirm(identity, value.ceremony_id, value.session_commitment,
                        gesture_id=value.gesture_id, reviewed_digest=value.reviewed_digest)
                elif request.url.path == OUTCOME_PATH:
                    result = await service.outcome(identity, value.ceremony_id, value.session_commitment)
                else:
                    result = await service.review(identity, value.ceremony_id, value.session_commitment)
                return reply(200, result)
        except Exception:
            # Do not reveal whether another owner's ceremony exists, or leak
            # database errors. Failure after approval may be indeterminate.
            return reply(503 if accepted else 422, {"error": "linking_unavailable" if accepted else "invalid_request"})
        finally:
            in_flight -= 1

    return app

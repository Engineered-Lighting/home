"""Signed client for one home's light-only Home Assistant endpoint.

Mirrors ``ha-config/home_agent_edge/lighting.py``: an HMAC-SHA256 per-home
secret over a context prefix, the exact path and the exact body. This client
never retries an execute. Anything that leaves the result uncertain (network
error, timeout, server error or an unexpected reply) raises ``OutcomeUnknown``
so the caller looks the operation up instead of sending it again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import UUID

import httpx

from .lighting_contract import Inventory, LightOperation, from_wire

SIGNATURE_HEADER = "X-Home-Agent-Lighting-Signature"
SIGNATURE_CONTEXT = b"home-agent-lighting:v1\n"
INVENTORY_PATH = "/api/home_agent_edge/lighting/v1/inventory"
EXECUTE_PATH = "/api/home_agent_edge/lighting/v1/execute"
OUTCOME_PATH = "/api/home_agent_edge/lighting/v1/outcome"
MAX_RESPONSE = 32 * 1024
TIMEOUT_S = 20.0  # the endpoint allows 10 s for the call and 3 s to verify the state
EXECUTE_STATES = frozenset({"succeeded", "failed", "indeterminate", "dispatching"})
OUTCOME_STATES = EXECUTE_STATES | {"absent"}
# Definite refusals: the endpoint did not act on this request.
REFUSALS = frozenset({"operation_not_allowed", "operation_not_supported", "allowlist_changed", "request_expired",
                      "request_conflict", "request_withdrawn", "signature_invalid", "invalid_request", "body_too_large"})


class OutcomeUnknown(Exception):
    """The request may or may not have acted. Look it up; never resend."""


class LightingRefused(Exception):
    """The endpoint definitely did not act on this request."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sign(secret: bytes, path: str, body: bytes) -> str:
    return hmac.new(secret, SIGNATURE_CONTEXT + path.encode() + b"\n" + body, hashlib.sha256).hexdigest()


class EdgeLightingClient:
    def __init__(self, *, site_id: str, base_url: str, secret: bytes, client: httpx.AsyncClient,
                 now_ms=lambda: int(time.time() * 1000)) -> None:
        url = httpx.URL(base_url)
        if (site_id not in ("echo", "victoria") or url.scheme != "https" or not url.host or url.userinfo
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("an https origin for the home's lighting endpoint is required")
        if type(secret) is not bytes or len(secret) != 32 or not isinstance(client, httpx.AsyncClient) or not callable(now_ms):
            raise ValueError("a dedicated 32-byte lighting secret and client are required")
        self.site_id = site_id
        self._origin = str(url.copy_with(path="/"))[:-1]
        self._secret, self._client, self._now = secret, client, now_ms

    def __repr__(self) -> str:
        return f"EdgeLightingClient(site_id={self.site_id!r})"

    async def _post(self, path: str, value: dict) -> tuple[int, dict]:
        body = json.dumps(value, separators=(",", ":")).encode()
        response = await self._client.post(self._origin + path, content=body, timeout=TIMEOUT_S,
            headers={"content-type": "application/json", SIGNATURE_HEADER: sign(self._secret, path, body)})
        if len(response.content) > MAX_RESPONSE:
            raise ValueError("oversized lighting response")
        parsed = response.json()
        if type(parsed) is not dict:
            raise ValueError("unexpected lighting response")
        return response.status_code, parsed

    def _base(self) -> dict:
        return {"version": 1, "site_id": self.site_id, "issued_at": self._now()}

    async def inventory(self) -> Inventory:
        status, value = await self._post(INVENTORY_PATH, self._base())
        if status != 200:
            raise LightingRefused(str(value.get("error", "inventory_unavailable")))
        inventory = from_wire(Inventory, value)
        if inventory.site_id != self.site_id:
            raise ValueError("inventory from another home")
        return inventory

    async def execute(self, operation: LightOperation, *, request_id: UUID, index: int, revision: str,
                      expires_at_ms: int) -> str:
        if type(operation) is not LightOperation or operation.site_id != self.site_id or type(request_id) is not UUID:
            raise TypeError("a frozen operation for this home is required")
        request = {**self._base(), "revision": revision, "request_id": str(request_id), "operation_index": index,
                   "entity_id": operation.entity_id, "operation": operation.operation,
                   "brightness": operation.brightness, "expires_at": expires_at_ms}
        try:
            status, value = await self._post(EXECUTE_PATH, request)
        except Exception as exc:  # the request may have arrived and acted
            raise OutcomeUnknown("execute delivery unknown") from exc
        if status == 200:
            if (set(value) == {"version", "request_id", "operation_index", "status"} and value["version"] == 1
                    and value["request_id"] == str(request_id) and value["operation_index"] == index
                    and value["status"] in EXECUTE_STATES):
                return value["status"]
            raise OutcomeUnknown("unexpected execute reply")
        code = value.get("error")
        if status in (400, 401, 409, 413) and code in REFUSALS:
            raise LightingRefused(code)
        raise OutcomeUnknown("execute outcome unknown")

    async def outcome(self, *, request_id: UUID, index: int) -> str:
        if type(request_id) is not UUID:
            raise TypeError("operation identity required")
        status, value = await self._post(OUTCOME_PATH, {**self._base(), "request_id": str(request_id),
                                                        "operation_index": index})
        if (status != 200 or set(value) != {"version", "request_id", "operation_index", "status"}
                or value["request_id"] != str(request_id) or value["operation_index"] != index
                or value["status"] not in OUTCOME_STATES):
            raise OutcomeUnknown("outcome lookup failed")
        return value["status"]

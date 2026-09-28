"""Light-only actuation endpoint for explicit cross-home lighting.

Home Agent Core asks this Home Assistant to switch an allowlisted light only
after the owner explicitly confirmed a frozen proposal. This module is the
narrow boundary on the Home Assistant side:

* Every request is signed with a per-home secret (HMAC-SHA256 over the path and
  the exact body) and carries an ``issued_at`` within a short skew window. The
  secret authorizes nothing else: it is not a Home Assistant token.
* Only ``light.*`` entities in the configured allowlist, and only on, off or a
  bounded brightness. No scenes, scripts, groups or other domains.
* The allowlist has a revision. A request built against another revision is
  refused, so a proposal never acts on a list the owner did not review.
* Execution is idempotent per ``(request_id, operation_index)``. The record is
  written as ``dispatching`` before the service call. A crash or an ambiguous
  call leaves it ``indeterminate`` and it is never sent again; callers look the
  outcome up instead of retrying.

The pure logic here has no Home Assistant imports so it can be tested alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

SITES = ("echo", "victoria")
ENTITY = re.compile(r"^light\.[a-z0-9_]{1,64}$")
SECRET = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
OPERATIONS = ("on", "off", "brightness")
MAX_BODY = 2048
MAX_ENTITIES = 64
MAX_OPERATIONS = 16
SKEW_MS = 60_000
MAX_EXPIRY_MS = 120_000
CALL_TIMEOUT_S = 10.0
SIGNATURE_HEADER = "X-Home-Agent-Lighting-Signature"
SIGNATURE_CONTEXT = b"home-agent-lighting:v1\n"
DEFAULT_LEDGER_PATH = "/config/.storage/home_agent_edge_lighting.sqlite"
LEDGER_RETENTION_MS = 30 * 24 * 60 * 60 * 1000

INVENTORY_URL = "/api/home_agent_edge/lighting/v1/inventory"
EXECUTE_URL = "/api/home_agent_edge/lighting/v1/execute"
OUTCOME_URL = "/api/home_agent_edge/lighting/v1/outcome"

EXECUTE_KEYS = frozenset({"version", "site_id", "issued_at", "revision", "request_id", "operation_index",
                          "entity_id", "operation", "brightness", "expires_at"})
OUTCOME_KEYS = frozenset({"version", "site_id", "issued_at", "request_id", "operation_index"})
INVENTORY_KEYS = frozenset({"version", "site_id", "issued_at"})


class LightingRejected(Exception):
    """A request that must not act; ``status`` is the HTTP status to return."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class LightingPolicy:
    site_id: str
    secret: bytes = b""
    entities: tuple[str, ...] = ()
    ledger_path: Path = Path(DEFAULT_LEDGER_PATH)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "LightingPolicy":
        if not isinstance(config, Mapping) or set(config) - {"site_id", "secret", "entities", "ledger_path"}:
            raise ValueError("invalid lighting configuration")
        site, secret, entities = config.get("site_id"), config.get("secret"), config.get("entities")
        if site not in SITES or not isinstance(secret, str) or not SECRET.fullmatch(secret):
            raise ValueError("lighting requires a site and a dedicated 64-hex secret")
        if (not isinstance(entities, (list, tuple)) or not 1 <= len(entities) <= MAX_ENTITIES
                or len(set(entities)) != len(entities)
                or not all(isinstance(e, str) and ENTITY.fullmatch(e) for e in entities)):
            raise ValueError("lighting requires a unique allowlist of light entities")
        raw = config.get("ledger_path", DEFAULT_LEDGER_PATH)
        # Home Assistant runs on POSIX; also accept a native absolute path for tests.
        if not isinstance(raw, str) or not (raw.startswith("/") or Path(raw).is_absolute()):
            raise ValueError("lighting ledger path must be absolute")
        return cls(site, bytes.fromhex(secret), tuple(sorted(entities)), Path(raw))

    @property
    def revision(self) -> str:
        """Identifies the reviewed allowlist, not the lights' current states."""
        material = json.dumps(["home-agent-lighting:v1:allowlist", self.site_id, list(self.entities)],
                              separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()

    def __repr__(self) -> str:  # never expose the secret in logs or tracebacks
        return f"LightingPolicy(site_id={self.site_id!r}, entities={len(self.entities)})"


def sign(secret: bytes, path: str, body: bytes) -> str:
    return hmac.new(secret, SIGNATURE_CONTEXT + path.encode() + b"\n" + body, hashlib.sha256).hexdigest()


class LightingLedger:
    """Durable per-operation dispatch record; SQLite, one writer."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS lighting_dispatch (
            request_id TEXT NOT NULL, operation_index INTEGER NOT NULL, digest TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('dispatching','succeeded','failed','indeterminate')),
            updated_at INTEGER NOT NULL, PRIMARY KEY (request_id, operation_index))""")
        # Anything still dispatching was interrupted mid-call: its effect is unknown.
        self._db.execute("UPDATE lighting_dispatch SET state='indeterminate' WHERE state='dispatching'")

    def reserve(self, request_id: str, index: int, digest: str, now: int) -> str | None:
        """Claim a new dispatch. Returns None when claimed, else the recorded state."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT digest,state FROM lighting_dispatch WHERE request_id=? AND operation_index=?",
                                       (request_id, index)).fetchone()
                if row is None:
                    self._db.execute("INSERT INTO lighting_dispatch VALUES (?,?,?,'dispatching',?)",
                                     (request_id, index, digest, now))
                    self._db.execute("DELETE FROM lighting_dispatch WHERE updated_at < ?", (now - LEDGER_RETENTION_MS,))
                    self._db.execute("COMMIT")
                    return None
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        if row[0] != digest:
            raise LightingRejected(409, "request_conflict")
        return row[1]

    def complete(self, request_id: str, index: int, state: str, now: int) -> None:
        if state not in ("succeeded", "failed", "indeterminate"):
            raise ValueError("invalid dispatch state")
        with self._lock:
            self._db.execute("UPDATE lighting_dispatch SET state=?,updated_at=? WHERE request_id=? AND operation_index=? "
                             "AND state='dispatching'", (state, now, request_id, index))

    def lookup(self, request_id: str, index: int) -> str:
        with self._lock:
            row = self._db.execute("SELECT state FROM lighting_dispatch WHERE request_id=? AND operation_index=?",
                                   (request_id, index)).fetchone()
        return row[0] if row else "absent"

    def close(self) -> None:
        with self._lock:
            self._db.close()


class LightingEndpoint:
    """Signed inventory, execute and outcome operations for one home."""

    def __init__(self, policy: LightingPolicy, ledger: LightingLedger, *,
                 states: Callable[[str], Mapping[str, Any] | None],
                 call_service: Callable[[str, dict[str, Any]], Awaitable[None]],
                 now: Callable[[], int] = lambda: int(time.time() * 1000),
                 run: Callable[..., Awaitable[Any]] | None = None) -> None:
        self._policy, self._ledger, self._states, self._call, self._now = policy, ledger, states, call_service, now
        self._run = run or (lambda fn, *args: _direct(fn, *args))
        self._seen: dict[str, int] = {}

    def _authenticate(self, path: str, body: bytes, signature: str | None, keys: frozenset[str]) -> dict[str, Any]:
        if len(body) > MAX_BODY:
            raise LightingRejected(413, "body_too_large")
        expected = sign(self._policy.secret, path, body)
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise LightingRejected(401, "signature_invalid")
        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicates)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LightingRejected(400, "invalid_request") from exc
        now = self._now()
        if (not isinstance(value, dict) or set(value) != keys or value.get("version") != 1
                or value.get("site_id") != self._policy.site_id or type(value.get("issued_at")) is not int
                or abs(now - value["issued_at"]) > SKEW_MS):
            raise LightingRejected(400, "invalid_request")
        # Reject an exact replay of a signed request inside the skew window.
        self._seen = {k: t for k, t in self._seen.items() if t > now - 2 * SKEW_MS}
        if expected in self._seen or len(self._seen) >= 4096:
            raise LightingRejected(409, "replayed_request")
        self._seen[expected] = now
        return value

    def _light(self, entity_id: str) -> dict[str, Any]:
        state = self._states(entity_id)
        if not state or state.get("state") not in ("on", "off"):
            return {"entity_id": entity_id, "name": (state or {}).get("name") or entity_id, "state": "unavailable",
                    "brightness_pct": None}
        raw = state.get("brightness")
        pct = round(raw * 100 / 255) if isinstance(raw, (int, float)) and state["state"] == "on" else None
        return {"entity_id": entity_id, "name": state.get("name") or entity_id, "state": state["state"], "brightness_pct": pct}

    async def inventory(self, body: bytes, signature: str | None) -> dict[str, Any]:
        self._authenticate(INVENTORY_URL, body, signature, INVENTORY_KEYS)
        return {"version": 1, "site_id": self._policy.site_id, "revision": self._policy.revision,
                "lights": [self._light(e) for e in self._policy.entities]}

    async def outcome(self, body: bytes, signature: str | None) -> dict[str, Any]:
        value = self._authenticate(OUTCOME_URL, body, signature, OUTCOME_KEYS)
        request_id, index = _operation_key(value)
        state = await self._run(self._ledger.lookup, request_id, index)
        return {"version": 1, "request_id": request_id, "operation_index": index, "status": state}

    async def execute(self, body: bytes, signature: str | None) -> dict[str, Any]:
        value = self._authenticate(EXECUTE_URL, body, signature, EXECUTE_KEYS)
        request_id, index = _operation_key(value)
        entity, operation, brightness, now = value["entity_id"], value["operation"], value["brightness"], self._now()
        if (entity not in self._policy.entities or operation not in OPERATIONS
                or (operation == "brightness") != (type(brightness) is int)
                or operation == "brightness" and not 1 <= brightness <= 100):
            raise LightingRejected(400, "operation_not_allowed")
        if value["revision"] != self._policy.revision:
            raise LightingRejected(409, "allowlist_changed")
        if type(value["expires_at"]) is not int or not now < value["expires_at"] <= now + MAX_EXPIRY_MS:
            raise LightingRejected(409, "request_expired")
        digest = hashlib.sha256(json.dumps({k: value[k] for k in sorted(EXECUTE_KEYS - {"issued_at"})},
                                           separators=(",", ":")).encode()).hexdigest()
        prior = await self._run(self._ledger.reserve, request_id, index, digest, now)
        if prior is not None:
            # Already dispatched once: report the record, never send it again.
            return {"version": 1, "request_id": request_id, "operation_index": index, "status": prior}
        if self._light(entity)["state"] == "unavailable":
            state = "failed"
        else:
            service = "turn_off" if operation == "off" else "turn_on"
            data: dict[str, Any] = {"entity_id": entity}
            if operation == "brightness":
                data["brightness_pct"] = brightness
            try:
                await asyncio.wait_for(self._call(service, data), CALL_TIMEOUT_S)
                state = "succeeded"
            except asyncio.CancelledError:
                await self._run(self._ledger.complete, request_id, index, "indeterminate", self._now())
                raise
            except Exception:
                # Timeout or error after the call was attempted: Home Assistant may
                # still have applied it, so the outcome is unknown, not failed.
                state = "indeterminate"
        await self._run(self._ledger.complete, request_id, index, state, self._now())
        return {"version": 1, "request_id": request_id, "operation_index": index, "status": state}


def _operation_key(value: Mapping[str, Any]) -> tuple[str, int]:
    request_id, index = value.get("request_id"), value.get("operation_index")
    if not isinstance(request_id, str) or not UUID.fullmatch(request_id) or type(index) is not int \
            or not 0 <= index < MAX_OPERATIONS:
        raise LightingRejected(400, "invalid_request")
    return request_id, index


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


async def _direct(fn: Callable[..., Any], *args: Any) -> Any:
    return fn(*args)

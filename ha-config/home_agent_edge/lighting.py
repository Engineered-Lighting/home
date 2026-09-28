"""Light-only actuation endpoint for explicit cross-home lighting.

Home Agent Core asks this Home Assistant to switch an allowlisted light only
after the owner explicitly confirmed a frozen proposal. This module is the
narrow boundary on the Home Assistant side:

* Every request is signed with a per-home secret (HMAC-SHA256 over the path and
  the exact body) and carries an ``issued_at`` within a short skew window. The
  secret authorizes nothing else: it is not a Home Assistant token.
* Only ``light.*`` entities in the configured allowlist, and only on, off or a
  bounded brightness (on dimmable lights). No scenes, scripts or other domains.
  Light groups are refused, so one allowlisted entity never switches others.
* The allowlist has a revision. A request built against another revision is
  refused, so a proposal never acts on a list the owner did not review.
* Execution is idempotent per ``(request_id, operation_index)``. The record is
  written as ``dispatching`` before the service call and reports ``succeeded``
  only once the light's state shows the change. A crash, an error or an
  unconfirmed state leaves it ``indeterminate`` and it is never sent again;
  callers look the outcome up instead of retrying. Looking up an operation that
  never arrived records it as ``absent``, which is final: a late execute for
  that key is refused.

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
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
OPERATIONS = ("on", "off", "brightness")
MAX_BODY = 2048
MAX_ENTITIES = 64
MAX_OPERATIONS = 16
SKEW_MS = 60_000
# Proposals expire after 60 s; the extra minute absorbs clock skew between hosts.
MAX_EXPIRY_MS = 120_000
CALL_TIMEOUT_S = 10.0
VERIFY_S = 3.0
VERIFY_STEP_S = 0.2
BRIGHTNESS_TOLERANCE = 2
MAX_SEEN = 4096
TOMBSTONE = "absent"
# Home Assistant color modes that include a brightness channel.
DIMMABLE_MODES = frozenset({"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"})
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
            state TEXT NOT NULL CHECK (state IN ('dispatching','succeeded','failed','indeterminate','absent')),
            updated_at INTEGER NOT NULL, PRIMARY KEY (request_id, operation_index))""")
        # Anything still dispatching was interrupted mid-call: its effect is unknown.
        self._db.execute("UPDATE lighting_dispatch SET state='indeterminate' WHERE state='dispatching'")

    def _transaction(self, work: Callable[[], Any]) -> Any:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = work()
                self._db.execute("COMMIT")
                return result
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _row(self, request_id: str, index: int) -> tuple[str, str] | None:
        return self._db.execute("SELECT digest,state FROM lighting_dispatch WHERE request_id=? AND operation_index=?",
                                (request_id, index)).fetchone()

    def reserve(self, request_id: str, index: int, digest: str, now: int) -> str | None:
        """Claim a new dispatch. Returns None when claimed, else the recorded state."""
        def work() -> tuple[str, str] | None:
            row = self._row(request_id, index)
            if row is None:
                self._db.execute("INSERT INTO lighting_dispatch VALUES (?,?,?,'dispatching',?)",
                                 (request_id, index, digest, now))
                self._db.execute("DELETE FROM lighting_dispatch WHERE updated_at < ?", (now - LEDGER_RETENTION_MS,))
            return row
        row = self._transaction(work)
        if row is None:
            return None
        if row[1] == TOMBSTONE:
            raise LightingRejected(409, "request_withdrawn")
        if row[0] != digest:
            raise LightingRejected(409, "request_conflict")
        return row[1]

    def complete(self, request_id: str, index: int, state: str, now: int) -> str:
        """Record the result of a claimed dispatch and return the stored state."""
        if state not in ("succeeded", "failed", "indeterminate"):
            raise ValueError("invalid dispatch state")

        def work() -> str:
            self._db.execute("UPDATE lighting_dispatch SET state=?,updated_at=? WHERE request_id=? AND operation_index=? "
                             "AND state='dispatching'", (state, now, request_id, index))
            row = self._row(request_id, index)
            return row[1] if row else "indeterminate"
        return self._transaction(work)

    def settle(self, request_id: str, index: int, now: int) -> str:
        """Return the recorded state. An unknown key is recorded as absent for good."""
        def work() -> str:
            row = self._row(request_id, index)
            if row is not None:
                return row[1]
            self._db.execute("INSERT INTO lighting_dispatch VALUES (?,?,?,?,?)",
                             (request_id, index, TOMBSTONE, TOMBSTONE, now))
            return TOMBSTONE
        return self._transaction(work)

    def lookup(self, request_id: str, index: int) -> str | None:
        with self._lock:
            row = self._row(request_id, index)
        return row[1] if row else None

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
        if (not isinstance(signature, str) or not SIGNATURE.fullmatch(signature)
                or not hmac.compare_digest(signature.encode(), expected.encode())):
            raise LightingRejected(401, "signature_invalid")
        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicates)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LightingRejected(400, "invalid_request") from exc
        now = self._now()
        if (not isinstance(value, dict) or set(value) != keys or type(value.get("version")) is not int
                or value["version"] != 1 or value.get("site_id") != self._policy.site_id
                or type(value.get("issued_at")) is not int or abs(now - value["issued_at"]) > SKEW_MS):
            raise LightingRejected(400, "invalid_request")
        # Refuse an exact replay for as long as its issue time is still accepted.
        self._seen = {k: issued for k, issued in self._seen.items() if issued + SKEW_MS >= now}
        if expected in self._seen:
            raise LightingRejected(409, "replayed_request")
        if len(self._seen) >= MAX_SEEN:
            raise LightingRejected(503, "replay_cache_full")
        self._seen[expected] = value["issued_at"]
        return value

    def _light(self, entity_id: str) -> dict[str, Any]:
        state = self._states(entity_id) or {}
        name = state.get("name") or entity_id
        dimmable = bool(DIMMABLE_MODES.intersection(state.get("supported_color_modes") or ()))
        # Light groups (group platform, Hue rooms and zones) switch member lights
        # that are not on the allowlist, so they are never actuated.
        if state.get("entity_id") or state.get("is_hue_group"):
            return {"entity_id": entity_id, "name": name, "state": "unsupported", "brightness_pct": None,
                    "dimmable": False}
        if state.get("state") not in ("on", "off"):
            return {"entity_id": entity_id, "name": name, "state": "unavailable", "brightness_pct": None,
                    "dimmable": dimmable}
        raw = state.get("brightness")
        pct = round(raw * 100 / 255) if isinstance(raw, (int, float)) and state["state"] == "on" else None
        return {"entity_id": entity_id, "name": name, "state": state["state"], "brightness_pct": pct,
                "dimmable": dimmable}

    def _applied(self, entity_id: str, operation: str, brightness: int | None) -> bool:
        light = self._light(entity_id)
        if operation == "off":
            return light["state"] == "off"
        if operation == "on":
            return light["state"] == "on"
        return (light["state"] == "on" and light["brightness_pct"] is not None
                and abs(light["brightness_pct"] - brightness) <= BRIGHTNESS_TOLERANCE)

    async def inventory(self, body: bytes, signature: str | None) -> dict[str, Any]:
        self._authenticate(INVENTORY_URL, body, signature, INVENTORY_KEYS)
        return {"version": 1, "site_id": self._policy.site_id, "revision": self._policy.revision,
                "lights": [self._light(e) for e in self._policy.entities]}

    async def outcome(self, body: bytes, signature: str | None) -> dict[str, Any]:
        value = self._authenticate(OUTCOME_URL, body, signature, OUTCOME_KEYS)
        request_id, index = _operation_key(value)
        state = await self._run(self._ledger.settle, request_id, index, self._now())
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
        light = self._light(entity)
        if light["state"] == "unsupported":
            raise LightingRejected(400, "operation_not_allowed")
        if operation == "brightness" and not light["dimmable"]:
            raise LightingRejected(400, "operation_not_supported")
        digest = hashlib.sha256(json.dumps({k: value[k] for k in sorted(EXECUTE_KEYS - {"issued_at"})},
                                           separators=(",", ":")).encode()).hexdigest()
        # Once accepted, finish and record the operation even if the caller disconnects.
        task = asyncio.ensure_future(self._dispatch(request_id, index, digest, entity, operation, brightness, now))
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        state = await asyncio.shield(task)
        return {"version": 1, "request_id": request_id, "operation_index": index, "status": state}

    async def _dispatch(self, request_id: str, index: int, digest: str, entity: str, operation: str,
                        brightness: int | None, now: int) -> str:
        prior = await self._run(self._ledger.reserve, request_id, index, digest, now)
        if prior is not None:
            # Already dispatched once: report the record, never send it again.
            return prior
        if self._light(entity)["state"] == "unavailable":
            return await self._run(self._ledger.complete, request_id, index, "failed", self._now())
        service = "turn_off" if operation == "off" else "turn_on"
        data: dict[str, Any] = {"entity_id": entity}
        if operation == "brightness":
            data["brightness_pct"] = brightness
        try:
            await asyncio.wait_for(self._call(service, data), CALL_TIMEOUT_S)
            # A returned service call is not proof: skipped or unacknowledged
            # devices still return. Report success only once the state shows it.
            state = "indeterminate"
            deadline = time.monotonic() + VERIFY_S
            while True:
                if self._applied(entity, operation, brightness):
                    state = "succeeded"
                    break
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(VERIFY_STEP_S)
        except asyncio.CancelledError:
            await self._run(self._ledger.complete, request_id, index, "indeterminate", self._now())
            raise
        except Exception:
            # Timeout or error after the call was attempted: Home Assistant may
            # still have applied it, so the outcome is unknown, not failed.
            state = "indeterminate"
        return await self._run(self._ledger.complete, request_id, index, state, self._now())


def _operation_key(value: Mapping[str, Any]) -> tuple[str, int]:
    request_id, index = value.get("request_id"), value.get("operation_index")
    if (not isinstance(request_id, str) or not UUID.fullmatch(request_id) or type(index) is not int
            or not 0 <= index < MAX_OPERATIONS):
        raise LightingRejected(400, "invalid_request")
    return request_id, index


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


async def _direct(fn: Callable[..., Any], *args: Any) -> Any:
    return fn(*args)

"""Fixed read/intent edge for the existing TLS controller; no Lab authority.

The trusted composition root wraps the incumbent ASGI app with this capability.
Only its supervisor can supply the journal address. No request selects a host,
credential, executable, model or worker. Unrelated requests retain the Lab app.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import re
import socket
import threading
import time
from urllib.parse import parse_qsl, unquote, urlsplit

PREFIX = "/api/home-context/v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
SCOPE = ("site_id", "room_id", "session_id", "domain_epoch")
MAX_BODY = 64 * 1024
MAX_RESPONSE = 1024 * 1024


class EdgeRefusal(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def binding_digest(binding):
    if (not isinstance(binding, dict) or set(binding) != {"scope", "manifest_ref"}
            or not isinstance(binding["scope"], dict) or set(binding["scope"]) != set(SCOPE)
            or any(not isinstance(binding["scope"][k], str) or not ID.fullmatch(binding["scope"][k]) for k in SCOPE)
            or not isinstance(binding["manifest_ref"], dict) or set(binding["manifest_ref"]) != {"id", "digest"}
            or not ID.fullmatch(binding["manifest_ref"].get("id", ""))
            or not DIGEST.fullmatch(binding["manifest_ref"].get("digest", ""))):
        raise ValueError("exact context edge binding required")
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def parse_route(method, raw_url, binding):
    """Closed public read/intent dialect; does not admit private worker routes."""
    if (not isinstance(raw_url, str) or len(raw_url) > 4096 or not raw_url.startswith(PREFIX + "/")
            or re.search(r"[\\\x00-\x20#]", raw_url) or ".." in raw_url
            or re.search(r"%(?:2f|5c|2e|00)", raw_url, re.I)):
        raise EdgeRefusal("context_route_denied", 404)
    parsed = urlsplit(raw_url)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise EdgeRefusal("context_route_denied", 404)
    tail = unquote(parsed.path[len(PREFIX):])
    lookup = tail.startswith("/intent-events/") and ID.fullmatch(tail[len("/intent-events/"):])
    if method == "POST" and tail == "/intent-events" and not parsed.query:
        return "intent"
    if method != "GET" or tail not in {"/snapshot", "/events", "/live-scene-catalog"} and not lookup:
        raise EdgeRefusal("context_route_denied", 404)
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    values = dict(pairs)
    allowed = {"site_id", "room_id"} | ({"after", "stream_epoch", "limit", "wait_s"} if tail == "/events" else set())
    if (len(values) != len(pairs) or set(values) - allowed or not {"site_id", "room_id"} <= set(values)
            or any(values[k] != binding["scope"][k] for k in ("site_id", "room_id"))):
        raise EdgeRefusal("context_scope_denied", 403)
    if tail == "/events":
        for key, maximum, minimum in (("after", 9007199254740991, 0), ("limit", 128, 1), ("wait_s", 10, 0)):
            if key == "after" and key not in values:
                raise EdgeRefusal("context_invalid_query")
            if key in values and (not re.fullmatch(r"0|[1-9][0-9]{0,15}", values[key])
                                  or not minimum <= int(values[key]) <= maximum):
                raise EdgeRefusal("context_invalid_query")
        if "stream_epoch" in values and not ID.fullmatch(values["stream_epoch"]):
            raise EdgeRefusal("context_invalid_query")
    return "read"


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class _Exchange:
    """One deadline and cancellable socket, including HTTP/1.0 detachment."""
    def __init__(self, deadline):
        self.deadline = deadline
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.transport = None
        self.refusal = None
        self.guard = threading.Thread(target=self._watch, name="home-context-edge-deadline")

    def _watch(self):
        if not self.finished.wait(max(0, self.deadline-time.monotonic())):
            self.abort("context_unavailable", 503)

    def abort(self, code, status):
        with self.lock:
            # Authority invalidation remains visible even if I/O also timed out.
            if self.refusal is None or code == "context_binding_changed":
                self.refusal = (code, status)
            transport = self.transport
        if transport is not None:
            try: transport.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            transport.close()
        try: self.loop.call_soon_threadsafe(self.wake.set)
        except RuntimeError: pass  # A completed caller may already have closed its loop.

    def check(self):
        if time.monotonic() >= self.deadline:
            self.abort("context_unavailable", 503)
        with self.lock:
            refusal = self.refusal
        if refusal is not None:
            raise EdgeRefusal(*refusal)

    def attach(self, transport):
        with self.lock:
            self.transport = transport
        self.check()

    def finish(self):
        self.finished.set()
        self.guard.join()
        with self.lock:
            transport, self.transport = self.transport, None
        if transport is not None:
            transport.close()


async def _receive(receive, exchange):
    exchange.check()
    incoming = asyncio.ensure_future(receive())
    interrupted = asyncio.create_task(exchange.wake.wait())
    try:
        done, _ = await asyncio.wait((incoming, interrupted), timeout=max(0, exchange.deadline-time.monotonic()),
                                     return_when=asyncio.FIRST_COMPLETED)
        exchange.check()
        if incoming not in done:
            # Event-loop timer resolution can wake just before the monotonic
            # sample reaches its boundary. A timed-out receive still refuses.
            exchange.abort("context_unavailable", 503)
            exchange.check()
        return incoming.result()
    finally:
        for task in (incoming, interrupted):
            if not task.done(): task.cancel()
        await asyncio.gather(incoming, interrupted, return_exceptions=True)


class ContextEdge:
    def __init__(self, app, *, binding, token_sha256, journal_address, timeout_s=3, intent_version=1):
        self.digest = binding_digest(binding)
        if (not DIGEST.fullmatch(token_sha256 or "") or not callable(journal_address)
                or type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) or not 0 < timeout_s <= 3):
            raise ValueError("fixed scoped context capability required")
        if intent_version not in (1,2,3,4) or type(intent_version) is not int:
            raise ValueError("explicit context intent version required")
        self.intent_version=intent_version
        self.app = app
        self.binding = json.loads(json.dumps(binding))
        self.token_sha256, self.journal_address, self.timeout_s = token_sha256, journal_address, timeout_s
        self.generation = 1
        self._active, self._lock = set(), threading.Lock()

    def invalidate(self):
        with self._lock:
            self.generation += 1
            active = tuple(self._active)
        for exchange in active:
            exchange.abort("context_binding_changed", 409)

    def _forward(self, method, raw_url, body, token, exchange):
        exchange.check()
        address = self.journal_address()
        if (not isinstance(address, (tuple, list)) or len(address) != 2
                or not isinstance(address[0], str) or not ipaddress.ip_address(address[0]).is_loopback
                or type(address[1]) is not int or not 1 <= address[1] <= 65535):
            raise EdgeRefusal("context_session_unavailable", 503)
        exchange.check()
        connection = http.client.HTTPConnection(address[0], address[1])
        response = transport = None
        try:
            # The destination is already a numeric loopback address. Own its
            # actual socket before connect, so cancellation can interrupt every
            # blocking phase without DNS, reconnect or another request attempt.
            transport = socket.socket(socket.AF_INET6 if ipaddress.ip_address(address[0]).version == 6 else socket.AF_INET, socket.SOCK_STREAM)
            exchange.attach(transport)
            transport.settimeout(max(.001, exchange.deadline-time.monotonic()))
            transport.connect(address)
            connection.sock = transport
            exchange.check()
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, raw_url, body=body, headers=headers)
            exchange.check()
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise EdgeRefusal("context_redirect_denied", 502)
            if response.getheader("Content-Type", "").split(";", 1)[0] != "application/json":
                raise EdgeRefusal("context_invalid_response", 502)
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise EdgeRefusal("context_response_too_large", 502)
            value = json.loads(raw, object_pairs_hook=_pairs)
            if not isinstance(value, dict):
                raise EdgeRefusal("context_invalid_response", 502)
            exchange.check()
            return response.status, raw
        finally:
            if response is not None: response.close()
            connection.close()
            if transport is not None: transport.close()

    async def __call__(self, scope, receive, send, *, deadline_monotonic=None):
        if scope.get("type") != "http" or not scope.get("path", "").startswith(PREFIX):
            return await self.app(scope, receive, send)
        started = time.monotonic()
        exchange = forward = forward_worker = None
        cancelled = None
        status, body = 503, b""
        try:
            if deadline_monotonic is not None and (type(deadline_monotonic) not in {int, float}
                    or not math.isfinite(deadline_monotonic) or deadline_monotonic <= 0):
                raise EdgeRefusal("context_unavailable", 503)
            deadline = min(started+self.timeout_s, deadline_monotonic) if deadline_monotonic is not None else started+self.timeout_s
            exchange = _Exchange(deadline)
            with self._lock:
                generation = self.generation
                self._active.add(exchange)
            exchange.guard.start()
            exchange.check()
            headers = {}
            for key, value in scope.get("headers", []):
                key, value = key.decode("ascii").lower(), value.decode("ascii")
                if key in headers and key in {"authorization", "x-home-context-binding", "content-length", "content-type"}:
                    raise EdgeRefusal("context_duplicate_header")
                headers[key] = value
            authorization = headers.get("authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            if (not 1 <= len(token) <= 4096 or re.search(r"\s", token)
                    or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.token_sha256)):
                raise EdgeRefusal("context_credential_denied", 401)
            if headers.get("x-home-context-binding") != self.digest:
                raise EdgeRefusal("context_binding_mismatch", 409)
            path = scope.get("raw_path", scope["path"].encode()).decode("ascii")
            query = scope.get("query_string", b"").decode("ascii")
            raw_url = path + ("?" + query if query else "")
            operation = parse_route(scope["method"], raw_url, self.binding)
            raw = bytearray()
            while True:
                message = await _receive(receive, exchange)
                if message["type"] == "http.disconnect":
                    raise EdgeRefusal("context_client_disconnected", 499)
                raw.extend(message.get("body", b""))
                if len(raw) > MAX_BODY:
                    raise EdgeRefusal("context_body_too_large", 413)
                if not message.get("more_body", False):
                    break
            if operation == "intent":
                if headers.get("content-type", "").split(";", 1)[0] != "application/json":
                    raise EdgeRefusal("context_invalid_body", 415)
                value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                allowed_intents = ({"home-live-intent-event-1","home-live-intent-event-2","home-live-intent-event-3","home-live-intent-event-4"} if self.intent_version==4
                                   else {"home-live-intent-event-1","home-live-intent-event-2","home-live-intent-event-3"} if self.intent_version==3
                                   else {"home-live-intent-event-1","home-live-intent-event-2"} if self.intent_version==2 else {"home-live-intent-event-1"})
                if (not isinstance(value, dict) or value.get("schema") not in allowed_intents
                        or any(value.get(k) != self.binding["scope"][k] for k in SCOPE)
                        or value.get("manifest_ref") != self.binding["manifest_ref"]):
                    raise EdgeRefusal("context_scope_denied", 403)
            elif raw:
                raise EdgeRefusal("context_unexpected_body")
            exchange.check()
            loop = asyncio.get_running_loop()
            forward = loop.create_future()
            def run_forward():
                try:
                    outcome = (self._forward(scope["method"], raw_url, bytes(raw) if operation == "intent" else None, token, exchange), None)
                except BaseException as error:
                    outcome = (None, error)
                loop.call_soon_threadsafe(forward.set_result, outcome)
            # This thread owns one request only. Shield its completion future,
            # then close its real socket and join it on caller cancellation;
            # cancelling an await alone cannot stop blocking HTTP I/O.
            forward_worker = threading.Thread(target=run_forward, name="home-context-edge-forward")
            forward_worker.start()
            outcome, error = await asyncio.shield(forward)
            if error is not None: raise error
            status, body = outcome
            exchange.check()
            if generation != self.generation:
                raise EdgeRefusal("context_binding_changed", 409)
        except asyncio.CancelledError as error:
            cancelled = error
        except EdgeRefusal as error:
            status = error.status
            body = json.dumps({"schema": "home-context-error-1", "error": {"code": error.code, "message": error.code}}).encode()
        except (OSError, ValueError, UnicodeError, asyncio.TimeoutError, http.client.HTTPException):
            status = 503
            body = b'{"schema":"home-context-error-1","error":{"code":"context_unavailable","message":"context_unavailable"}}'
        finally:
            if exchange is not None:
                if forward is not None and not forward.done():
                    exchange.abort("context_unavailable", 503)
                if forward is not None:
                    while True:
                        try:
                            await asyncio.shield(forward)
                            break
                        except asyncio.CancelledError as error:
                            cancelled = error
                            exchange.abort("context_unavailable", 503)
                        except Exception:
                            break
                if forward_worker is not None:
                    forward_worker.join()
                exchange.finish()
                with self._lock:
                    self._active.discard(exchange)
                    changed = generation != self.generation
                if changed:
                    status = 409
                    body = b'{"schema":"home-context-error-1","error":{"code":"context_binding_changed","message":"context_binding_changed"}}'
                elif status < 400 and time.monotonic() >= exchange.deadline:
                    status = 503
                    body = b'{"schema":"home-context-error-1","error":{"code":"context_unavailable","message":"context_unavailable"}}'
        if cancelled is not None:
            raise cancelled
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"private, no-store"),
            (b"x-content-type-options", b"nosniff"), (b"x-home-context-binding", self.digest.encode())]})
        await send({"type": "http.response.body", "body": body})

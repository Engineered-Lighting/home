"""Loopback HTTP API (standard library only).

Routes (CONTRACT "Service and transport"):
  POST   /api/lighting-brain/v1/homes/{home}/events                producer bearer token
  POST   /api/lighting-brain/v1/homes/{home}/overrides
  DELETE /api/lighting-brain/v1/homes/{home}/overrides/{fixture_id}
  GET    /api/lighting-brain/v1/homes/{home}/state
  GET    /api/lighting-brain/v1/homes/{home}/stream                Server-Sent Events
  GET    /healthz

Overrides, state and stream carry no brain-level credential: the brain binds
to loopback and the web-gateway in front of it enforces Home's session auth.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import TOKENS_SCHEMA
from .brain import Brain, stream_frames
from .inputs import EVENT_TYPES, EventError

log = logging.getLogger("lighting_brain.http")

PREFIX = "/api/lighting-brain/v1/homes/"
ROUTE = re.compile(r"^/api/lighting-brain/v1/homes/([a-z0-9][a-z0-9_-]{0,63})/"
                   r"(events|state|stream|overrides)(?:/([A-Za-z0-9._-]{1,160}))?$")
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Producer:
    name: str
    token: str
    homes: frozenset | None = None
    types: frozenset | None = None


def parse_tokens(data) -> list[Producer]:
    """Private producer token file (schema lighting-brain-producer-tokens-1)."""
    if not isinstance(data, dict) or data.get("schema") != TOKENS_SCHEMA:
        raise ValueError(f"token file schema must be {TOKENS_SCHEMA}")
    producers = []
    for i, raw in enumerate(data.get("producers") or []):
        name, token = raw.get("producer"), raw.get("token")
        if not isinstance(name, str) or not name or not isinstance(token, str) or len(token) < 24:
            raise ValueError(f"producers[{i}] needs a name and a token of at least 24 characters")
        types = raw.get("types")
        if types is not None and not set(types) <= set(EVENT_TYPES):
            raise ValueError(f"producers[{i}].types has unknown event types")
        homes = raw.get("homes")
        producers.append(Producer(name, token, frozenset(homes) if homes is not None else None,
                                  frozenset(types) if types is not None else None))
    tokens = [p.token for p in producers]
    if len(set(tokens)) != len(tokens):
        raise ValueError("producer tokens must be unique")
    return producers


def load_tokens(path: str | None = None, env_json: str | None = None) -> list[Producer]:
    if env_json:
        return parse_tokens(json.loads(env_json))
    if path:
        return parse_tokens(json.loads(Path(path).read_text(encoding="utf-8")))
    return []


def authenticate(header: str | None, producers: list[Producer]) -> Producer | None:
    if not header or not header.startswith("Bearer "):
        return None
    presented = header[len("Bearer "):].strip().encode("utf-8")
    match = None
    for producer in producers:  # compare against every token: no early exit
        if hmac.compare_digest(presented, producer.token.encode("utf-8")):
            match = producer
    return match


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "lighting-brain/1"
    protocol_version = "HTTP/1.1"

    @property
    def brain(self) -> Brain:
        return self.server.brain  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # route through logging, not stderr
        log.debug("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ helpers

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, error: str, detail: str = "") -> None:
        self._send_json(status, {"ok": False, "error": error, "detail": detail})

    def _read_json(self):
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            raise EventError(411, "length_required", "Content-Length is required")
        if int(length) > MAX_BODY_BYTES:
            raise EventError(413, "body_too_large", f"body exceeds {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(int(length))
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EventError(400, "invalid_json", str(exc)) from None

    def _route(self):
        path = self.path.split("?", 1)[0]
        return path, ROUTE.match(path)

    # ------------------------------------------------------------ verbs

    def do_GET(self):  # noqa: N802
        path, match = self._route()
        if path == "/healthz":
            return self._send_json(200, self.brain.health())
        if not match or match.group(3):
            return self._error(404, "not_found")
        home, resource = match.group(1), match.group(2)
        try:
            if resource == "state":
                return self._send_json(200, self.brain.state(home))
            if resource == "stream":
                self.brain.state(home)  # 404 for an unknown home before streaming
                return self._stream()
        except EventError as exc:
            return self._send_json(exc.status, exc.to_json())
        return self._error(405, "method_not_allowed")

    def do_POST(self):  # noqa: N802
        path, match = self._route()
        if not match or match.group(3):
            return self._error(404, "not_found")
        home, resource = match.group(1), match.group(2)
        try:
            if resource == "events":
                producer = authenticate(self.headers.get("Authorization"), self.server.producers)  # type: ignore[attr-defined]
                if producer is None:
                    return self._error(401, "unauthorized", "producer bearer token required")
                if producer.homes is not None and home not in producer.homes:
                    return self._error(403, "producer_home_forbidden")
                return self._send_json(200, self.brain.ingest(home, self._read_json(), producer.types))
            if resource == "overrides":
                return self._send_json(200, self.brain.set_override(home, self._read_json()))
        except EventError as exc:
            return self._send_json(exc.status, exc.to_json())
        return self._error(405, "method_not_allowed")

    def do_DELETE(self):  # noqa: N802
        path, match = self._route()
        if not match or match.group(2) != "overrides" or not match.group(3):
            return self._error(404, "not_found")
        try:
            return self._send_json(200, self.brain.clear_override(match.group(1), match.group(3)))
        except EventError as exc:
            return self._send_json(exc.status, exc.to_json())

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 3000\n\n")
            for frame in stream_frames(self.brain, self.brain.settings.stream_period_s, self.server.stopping):  # type: ignore[attr-defined]
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class BrainServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, brain: Brain, producers: list[Producer]):
        super().__init__(address, Handler)
        self.brain = brain
        self.producers = producers
        self.stopping = threading.Event()

    def shutdown(self):
        self.stopping.set()
        self.brain.close()
        super().shutdown()


def run_ticker(brain: Brain, stop: threading.Event) -> threading.Thread:
    """Re-evaluate on a timer so time-based rules (room_off, fades, pause
    expiry, staleness) fire without new events."""
    def loop():
        while not stop.wait(brain.settings.tick_s):
            try:
                brain.tick()
            except Exception:  # keep ticking; one bad evaluation must not stop the brain
                log.exception("tick failed")

    thread = threading.Thread(target=loop, name="lighting-brain-ticker", daemon=True)
    thread.start()
    return thread


def serve(brain: Brain, producers: list[Producer], host: str, port: int) -> BrainServer:
    if not is_loopback(host) and os.environ.get("LIGHTING_BRAIN_ALLOW_NON_LOOPBACK") != "1":
        raise SystemExit(f"refusing to bind {host}: the brain listens on loopback "
                         "(set LIGHTING_BRAIN_ALLOW_NON_LOOPBACK=1 inside a container only)")
    server = BrainServer((host, port), brain, producers)
    run_ticker(brain, server.stopping)
    return server

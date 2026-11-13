"""Step 3's people stream (plan L6): the newest people events, as server-sent events, on 127.0.0.1 only. Standard library.

    GET /api/den-live/v1/homes/<home>/stream   text/event-stream: `event: people` per processed frame, `: heartbeat`
                                              every heartbeat_s, so a silent pipeline reads as offline in under 3 s
    GET /api/den-live/v1/homes/<home>/state    {"people": the newest event, or null}
    GET /api/den-live/v1/homes/<home>/body     one body_clock, then den-live-body-2 ready or clear events
    GET /api/den-live/v1/homes/<home>/labels   the den's labels document (den-room-labels-1, room_labels.py)
    PUT /api/den-live/v1/homes/<home>/labels   saves the whole document: a compare-and-swap on its revision (200 with the
                                              new document, 409 with the current one, 400 with a reason). A JSON body
                                              of at most 64 KB with its Content-Length; the gateway lets only
                                              signed-in Home send it
    GET|POST /api/den-live/v1/homes/<home>/<route>  coordinator routes. POST bodies are JSON, at most 64 KB.

Only the listener's own Host (127.0.0.1:<port>) is accepted. The people stream carries virtual spot snapshots as well
as markers. The body stream has its own clients and bounded queue.
"""
import json
import socket
import socketserver
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/api/den-live/v1/homes/"
LABELS_MAX_BYTES = 65536
BACKLOG = 8
BODY_SCHEMA = "den-live-body-2"
BODY_SCOPE = ("home", "run_id", "track_epoch", "content_revision", "transform_id", "mode")
BODY_FIELDS = ("track_id", "points", "facing_deg", "shoulder_midpoint_m", "pelvis_z_m")
BODY_LEASE_S = 1.0
BODY_REASONS = frozenset(("empty", "ambiguous", "pose_unavailable", "warming_background", "insufficient_depth",
                          "head_mask_unavailable", "scope_changed", "stale", "unavailable"))


def _sse(name, event, seq=None):
    prefix = "" if seq is None else "id: %d\n" % seq
    data = json.dumps(event, separators=(",", ":"), allow_nan=name == "people")
    return ("%sevent: %s\ndata: %s\n\n" % (prefix, name, data)).encode()


class _Client:
    def __init__(self, backlog=BACKLOG):
        self.cond, self.events = threading.Condition(), deque(maxlen=backlog)
        self.retired = False
        self.spot = None


class PeopleStream:
    def __init__(self, *, home="den", host="127.0.0.1", port=8111, max_clients=16, heartbeat_s=2.0, labels=None,
                 body_available=True, clock=time.monotonic, wall_clock=time.time):
        self.home, self.host, self.max_clients, self.heartbeat_s = home, host, max_clients, heartbeat_s
        self.labels = labels   # a room_labels.Store, or None: then the labels paths answer 503
        self.latest, self.clients, self.lock, self.closing = None, set(), threading.Lock(), threading.Event()
        self.body_latest, self.body_clients = None, set()
        self.body_available, self.spot_latest = body_available, None
        self.body_run, self.body_seq = None, 0
        self.clock, self.body_time_offset = clock, wall_clock() - clock()
        self.served = self.refused = 0
        self.routes = {}   # {(method, "/suffix"): handler(body) -> (status, json)}
        stream = self

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            # Windows: SO_REUSEADDR would let a second server share a port in use, so it's off and the bind is exclusive.
            # Linux (the LA service): a second listener is refused either way, and SO_REUSEADDR lets a restarted
            # service rebind at once while its old clients' connections sit in TIME_WAIT.
            allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

            def server_bind(self):
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):   # Windows: nobody else may bind this port while it's ours
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = host, self.server_address[1]

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def answer(self, status, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if self.headers.get("Host") != "%s:%d" % (host, stream.port):
                    stream.refused += 1
                    return self.answer(403, {"ok": False, "error": "host"})
                path = self.path.split("?", 1)[0]
                route = stream.routes.get(("GET", path[len(BASE + stream.home):])) if path.startswith(BASE + stream.home + "/") else None
                if route is not None:
                    return self.routed(route, None)
                if path == BASE + stream.home + "/state":
                    stream.served += 1
                    return self.answer(200, {"people": stream.latest})
                if path == BASE + stream.home + "/stream":
                    return self.sse()
                if path == BASE + stream.home + "/body":
                    if not stream.body_available:
                        return self.answer(503, {"ok": False, "error": "body_unavailable"})
                    return self.sse(body=True)
                if path == BASE + stream.home + "/labels":
                    if stream.labels is None:
                        return self.answer(503, {"ok": False, "error": "labels_unavailable"})
                    stream.served += 1
                    return self.answer(200, stream.labels.get())
                stream.refused += 1
                return self.answer(404, {"ok": False, "error": "not_served"})

            def do_PUT(self):
                """The labels save. Checks run before the body is read: Host, path, length, query, type; then JSON."""
                self.close_connection = True
                if self.headers.get("Host") != "%s:%d" % (host, stream.port):
                    stream.refused += 1
                    return self.answer(403, {"ok": False, "error": "host"})
                path, _, query = self.path.partition("?")
                if path != BASE + stream.home + "/labels":
                    stream.refused += 1
                    return self.answer(405, {"ok": False, "error": "method"})
                length = self.headers.get("Content-Length")
                if self.headers.get("Transfer-Encoding") or length is None or not length.isdigit():
                    return self.answer(411, {"ok": False, "error": "length_required"})
                if int(length) > LABELS_MAX_BYTES:
                    return self.answer(413, {"ok": False, "error": "too_large"})
                if query or "?" in self.path:
                    return self.answer(400, {"ok": False, "error": "query"})
                if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self.answer(415, {"ok": False, "error": "content_type"})
                if stream.labels is None:
                    return self.answer(503, {"ok": False, "error": "labels_unavailable"})
                try:
                    doc = json.loads(self.rfile.read(int(length)).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self.answer(400, {"ok": False, "error": "json_invalid"})
                status, body = stream.labels.put(doc)
                stream.served += 1
                return self.answer(status, body)

            def do_POST(self):
                if self.headers.get("Host") != "%s:%d" % (host, stream.port):
                    stream.refused += 1
                    return self.answer(403, {"ok": False, "error": "host"})
                path = self.path.split("?", 1)[0]
                route = stream.routes.get(("POST", path[len(BASE + stream.home):])) if path.startswith(BASE + stream.home + "/") else None
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    size = -1
                if route is None or not 0 <= size <= 65536:
                    stream.refused += 1
                    return self.answer(501 if route is None else 413, {"ok": False, "error": "not_served" if route is None else "too_large"})
                try:
                    body = json.loads(self.rfile.read(size).decode("utf-8") or "null")
                except ValueError:
                    return self.answer(400, {"ok": False, "error": "not_json"})
                return self.routed(route, body)

            def routed(self, route, body):
                try:
                    status, payload = route(body)
                except Exception as exc:   # a route's failure is its own: a type name, never its text
                    status, payload = 500, {"ok": False, "error": type(exc).__name__}
                stream.served += 1
                return self.answer(status, payload)

            def sse(self, body=False):
                client = _Client(2 if body else BACKLOG)
                clients = stream.body_clients if body else stream.clients
                with stream.lock:
                    if body:
                        stream._expire_body()
                    if len(clients) >= stream.max_clients:
                        stream.refused += 1
                        return self.answer(503, {"ok": False, "error": "too_many_clients"})
                    clients.add(client)
                    latest = stream.body_latest if body else stream.latest
                    if latest is not None:
                        client.events.append(latest)
                    if not body:
                        client.spot = stream.spot_latest
                stream.served += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                if body:
                    self.send_header("X-Home-Den-Body-Schema", BODY_SCHEMA)
                    self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.close_connection = True
                if self.command == "HEAD":
                    return stream._drop(client)
                try:
                    if body:
                        self.connection.settimeout(2.0)
                        self.wfile.write(_sse("body_clock", {"server_ts": stream.body_time(stream.clock())}))
                    else:
                        self.wfile.write(b"retry: 2000\n\n")
                    self.wfile.flush()
                    while not stream.closing.is_set():
                        wait = stream.body_wait() if body else stream.heartbeat_s
                        with client.cond:
                            client.cond.wait_for(lambda: client.events or client.spot or client.retired or stream.closing.is_set(),
                                                 wait)
                            if client.retired:
                                return
                            pending = list(client.events)
                            client.events.clear()
                            spot, client.spot = client.spot, None
                        if spot is not None and spot["expires_at_ms"] > time.time() * 1000:
                            self.wfile.write(b"event: spot_snapshot\ndata: %s\n\n" % json.dumps(spot, separators=(",", ":")).encode())
                        if not pending and spot is None:
                            self.wfile.write(b": heartbeat\n\n")
                        for event in pending:
                            if not body:
                                self.wfile.write(_sse("people", event, event.get("seq") or 0))
                                continue
                            packet = stream.body_for_write(event, client)
                            if packet is not None:
                                self.wfile.write(packet)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    stream._drop(client)

        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="den-live-stream")

    def start(self):
        self.thread.start()
        return self

    def _drop(self, client):
        with self.lock:
            self.clients.discard(client)
            self.body_clients.discard(client)

    def publish(self, event):
        with self.lock:
            self.latest = event
            clients = list(self.clients)
        for client in clients:
            with client.cond:
                client.events.append(event)
                client.cond.notify_all()

    def publish_spot(self, snapshot):
        with self.lock:
            self.spot_latest = snapshot
            for client in self.clients:
                with client.cond:
                    client.spot = snapshot
                    client.cond.notify_all()

    def body_time(self, monotonic):
        return self.body_time_offset + monotonic

    def _set_body(self, scope, *, frame=None, capture_mono=None, reason=None):
        """Called under the publication lock, including when expiry replaces the latest snapshot."""
        if scope["run_id"] != self.body_run:
            if self.body_run is not None:
                for client in self.body_clients:
                    with client.cond:
                        client.retired = True
                        client.cond.notify_all()
                self.body_clients.clear()
            self.body_run, self.body_seq = scope["run_id"], 0
        self.body_seq += 1
        event = {**scope, "schema": BODY_SCHEMA, "seq": self.body_seq, "status": "clear" if frame is None else "ready"}
        deadline = None
        if frame is None:
            event["reason"] = reason
        else:
            deadline = capture_mono + BODY_LEASE_S
            capture_ts = self.body_time(capture_mono)
            event.update({**frame, "capture_ts": capture_ts, "expires_ts": capture_ts + BODY_LEASE_S})
        packet = (event, deadline, _sse("body", event, self.body_seq))
        self.body_latest = packet
        for client in self.body_clients:
            with client.cond:
                if client.events and client.events[-1][0]["status"] == "ready":
                    client.events.pop()
                if frame is None:
                    client.events.clear()
                client.events.append(packet)
                client.cond.notify_all()

    def _expire_body(self):
        if self.body_latest is not None:
            event, deadline, _ = self.body_latest
            if deadline is not None and self.clock() >= deadline:
                self._set_body({key: event[key] for key in BODY_SCOPE}, reason="stale")

    def body_wait(self):
        with self.lock:
            self._expire_body()
            deadline = self.body_latest[1] if self.body_latest is not None else None
            return self.heartbeat_s if deadline is None else max(0, min(self.heartbeat_s, deadline - self.clock()))

    def body_for_write(self, packet, client):
        event, deadline, data = packet
        with self.lock:
            if client.retired or event["run_id"] != self.body_run:
                return None
            if deadline is not None and self.clock() >= deadline:
                if packet is self.body_latest:
                    self._expire_body()
                return None
            # A clear published after the handler drained its queue still invalidates that pending ready.
            if deadline is not None and self.body_latest[0]["seq"] > event["seq"]:
                return None
            return data

    def publish_body(self, scope, *, frame=None, capture_mono=None, reason=None):
        scope = {key: scope[key] for key in BODY_SCOPE}
        with self.lock:
            if frame is not None:
                now = self.clock()
                if not capture_mono <= now < capture_mono + BODY_LEASE_S:
                    return False
                frame = {key: frame[key] for key in BODY_FIELDS}
                frame["points"] = {key: frame["points"][key] for key in ("count", "key", "position_mm", "normal", "kind")}
            elif reason not in BODY_REASONS:
                reason = "unavailable"
            self._set_body(scope, frame=frame, capture_mono=capture_mono, reason=reason)
            return True

    def close(self):
        self.closing.set()
        with self.lock:
            clients = list(self.clients) + list(self.body_clients)
        for client in clients:
            with client.cond:
                client.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()

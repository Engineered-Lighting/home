"""Step 3's people stream (plan L6): the newest people events, as server-sent events, on 127.0.0.1 only. Standard library.

    GET /api/den-live/v1/homes/<home>/stream   text/event-stream: `event: people` per processed frame, `: heartbeat`
                                              every heartbeat_s, so a silent pipeline reads as offline in under 3 s
    GET /api/den-live/v1/homes/<home>/state    {"people": the newest event, or null}
    GET /api/den-live/v1/homes/<home>/body     text/event-stream: `event: body` per body frame (den-live-body-1, from
                                              the detector process), and the same heartbeat. A path of its own, so the
                                              gateway can keep bodies to signed-in Home while the markers stay look-only
    GET /api/den-live/v1/homes/<home>/labels   the den's labels document (den-room-labels-1, room_labels.py)
    PUT /api/den-live/v1/homes/<home>/labels   saves the whole document: a compare-and-swap on its revision (200 with the
                                              new document, 409 with the current one, 400 with a reason). A JSON body
                                              of at most 64 KB with its Content-Length; the gateway lets only
                                              signed-in Home send it

It answers GET and HEAD, and PUT on the labels path only, for its own Host only (127.0.0.1:<port>), to at most
max_clients streams at once. A slow client's backlog is capped: its oldest events go first, since only the newest
position matters. The same paths are the gateway route step 4 adds in front of LA's service, so the page's code
doesn't change between the steps.
"""
import json
import socket
import socketserver
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/api/den-live/v1/homes/"
LABELS_MAX_BYTES = 65536
BACKLOG = 8
BODY_BACKLOG = 2   # body frames are large and only the newest matters


class _Client:
    def __init__(self, backlog=BACKLOG):
        self.cond, self.events = threading.Condition(), deque(maxlen=backlog)


class PeopleStream:
    def __init__(self, *, home="den", host="127.0.0.1", port=8111, max_clients=16, heartbeat_s=2.0, labels=None):
        self.home, self.host, self.max_clients, self.heartbeat_s = home, host, max_clients, heartbeat_s
        self.labels = labels   # a room_labels.Store, or None: then the labels paths answer 503
        self.latest, self.clients, self.lock, self.closing = None, set(), threading.Lock(), threading.Event()
        self.body_latest, self.body_clients = None, set()
        self.served = self.refused = 0
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
                if path == BASE + stream.home + "/state":
                    stream.served += 1
                    return self.answer(200, {"people": stream.latest})
                if path == BASE + stream.home + "/stream":
                    return self.sse()
                if path == BASE + stream.home + "/body":
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

            def sse(self, body=False):
                client = _Client(BODY_BACKLOG if body else BACKLOG)
                clients, name = (stream.body_clients, b"body") if body else (stream.clients, b"people")
                with stream.lock:
                    if len(clients) >= stream.max_clients:
                        stream.refused += 1
                        return self.answer(503, {"ok": False, "error": "too_many_clients"})
                    clients.add(client)
                    latest = stream.body_latest if body else stream.latest
                    if latest is not None:
                        client.events.append(latest)
                stream.served += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                if self.command == "HEAD":
                    return stream._drop(client)
                try:
                    self.wfile.write(b"retry: 2000\n\n")
                    self.wfile.flush()
                    while not stream.closing.is_set():
                        with client.cond:
                            client.cond.wait_for(lambda: client.events or stream.closing.is_set(), stream.heartbeat_s)
                            pending = list(client.events)
                            client.events.clear()
                        if not pending:
                            self.wfile.write(b": heartbeat\n\n")
                        for event in pending:
                            self.wfile.write(b"id: %d\nevent: %s\ndata: %s\n\n"
                                             % (event.get("seq") or 0, name, json.dumps(event, separators=(",", ":")).encode("utf-8")))
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

    def publish_body(self, frame):
        """One den-live-body-1 frame to the body stream's clients (the newest replaces what a slow client hasn't read)."""
        with self.lock:
            self.body_latest = frame
            clients = list(self.body_clients)
        for client in clients:
            with client.cond:
                client.events.append(frame)
                client.cond.notify_all()

    def close(self):
        self.closing.set()
        with self.lock:
            clients = list(self.clients) + list(self.body_clients)
        for client in clients:
            with client.cond:
                client.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()

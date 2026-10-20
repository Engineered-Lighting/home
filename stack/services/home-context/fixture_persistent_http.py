"""Opt-in bounded persistent HTTP for the fictional reference fixture only.

No production server or journal behavior is changed. Request bodies are read
before taking the journal lock; authentication and dispatch are repeated inside
that lock. No grant or processing receipt is cached by a connection.
"""
from __future__ import annotations

import copy
import io
import queue
import select
import socket
import threading
import time

from journal import JournalError, REFERENCE_PROFILE, PREFIX, bounded_json
from server import Handler, LocalServer


INTERFACE_VERSION = "home-adaptive-feedback-persistent-transport-inprocess-1"
FIXTURE_TRANSPORT = {
    "interface_version": INTERFACE_VERSION,
    "mode": "bounded_persistent_1",
    "profile": REFERENCE_PROFILE,
    "connection_workers": 4,
    "queued_connections": 2,
    "listen_backlog": 8,
    "socket_timeout_ms": 3000,
    "requests_per_connection": 4096,
    "shutdown_grace_ms": 2000,
}


def validate_fixture_transport(value):
    if (type(value) is not dict or set(value) != set(FIXTURE_TRANSPORT)
            or any(type(value[k]) is not type(v) or value[k] != v
                   for k, v in FIXTURE_TRANSPORT.items())):
        raise ValueError("exact reference fixture transport configuration required")
    return copy.deepcopy(FIXTURE_TRANSPORT)


class _InterruptibleSocketReader(io.RawIOBase):
    """Preserve the3-second read bound but notice fixture shutdown promptly.

    Windows timeout-mode recv can remain in its internal select after another
    thread calls shutdown. Polling readiness before recv avoids that unjoinable
    idle wait without shortening the documented normal IO deadline.
    """
    def __init__(self, connection, server):
        self.connection, self.server = connection, server

    def readable(self):
        return True

    def readinto(self, buffer):
        timeout = self.connection.gettimeout()
        if timeout == 0:  # Nonblocking BufferedReader.peek; no bytes are consumed.
            try:
                return self.connection.recv_into(buffer)
            except BlockingIOError:
                return None
        deadline = time.monotonic() + self.server.fixture_transport["socket_timeout_ms"] / 1000
        while not self.server._closing.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("fixture socket read deadline")
            try:
                ready, _, _ = select.select([self.connection], [], [], min(.05, remaining))
                if ready:
                    # Do not re-enter Windows' uninterruptible timeout-mode
                    # select after readiness; this one IO owner restores the
                    # advertised timeout before returning to its caller.
                    self.connection.setblocking(False)
                    try:
                        return self.connection.recv_into(buffer)
                    except BlockingIOError:
                        continue
                    finally:
                        self.connection.settimeout(timeout)
            except (OSError, ValueError):
                if self.server._closing.is_set():
                    return 0
                raise
        return 0


class _InterruptibleSocketWriter(io.BufferedIOBase):
    def __init__(self, connection, server):
        self.connection, self.server = connection, server

    def writable(self):
        return True

    def write(self, buffer):
        view = memoryview(buffer)
        sent = 0
        deadline = time.monotonic() + self.server.fixture_transport["socket_timeout_ms"] / 1000
        while sent < len(view):
            if self.server._closing.is_set():
                raise ConnectionAbortedError("fixture transport stopping")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("fixture socket write deadline")
            _, ready, _ = select.select([], [self.connection], [], min(.05, remaining))
            if ready:
                timeout = self.connection.gettimeout()
                self.connection.setblocking(False)
                try:
                    count = self.connection.send(view[sent:sent + 16384])
                except BlockingIOError:
                    continue
                finally:
                    self.connection.settimeout(timeout)
                if count == 0:
                    raise ConnectionResetError("fixture socket closed during write")
                sent += count
        return sent


class BoundedPersistentFixtureServer(LocalServer):
    """Four fixed connection workers and at most two queued accepted sockets."""

    def __init__(self, address, journal, *, fixture_transport):
        self.fixture_transport = validate_fixture_transport(fixture_transport)
        if journal.profile != REFERENCE_PROFILE:
            raise ValueError("persistent transport is restricted to the reference fixture")
        self._pending = queue.Queue(maxsize=self.fixture_transport["queued_connections"])
        self._counter_lock = threading.Lock()
        self._lifecycle = threading.Condition()
        self._stop_requested = threading.Event()
        self._closing = threading.Event()
        self._serve_started = threading.Event()
        self._serve_finished = threading.Event()
        self._sockets = set()
        self._counters = {k: 0 for k in (
            "accepted_connections", "queue_refusals", "active_connections",
            "peak_active_connections", "queued_connections", "peak_queued_connections",
            "requests_started", "requests_completed", "request_errors", "reused_requests",
            "idle_closures", "body_read_timeouts", "drained_connections", "late_pipeline_closures",
        )}
        self._join_complete = False
        super().__init__(address, journal)
        self._workers = [threading.Thread(target=self._connection_worker,
                         name="fixture-http-" + str(n), daemon=False)
                         for n in range(self.fixture_transport["connection_workers"])]
        self._coordinator = threading.Thread(target=self._coordinate_shutdown,
                                            name="fixture-http-shutdown", daemon=False)
        for worker in self._workers:
            worker.start()
        self._coordinator.start()

    def _add(self, **values):
        # Never hold this lock while acquiring journal.lock or doing socket IO.
        with self._counter_lock:
            for key, value in values.items():
                self._counters[key] += value

    def process_request(self, request, client_address):
        refused = False
        with self._counter_lock:
            self._counters["accepted_connections"] += 1
            if self._closing.is_set() or self._stop_requested.is_set():
                refused = True
            else:
                try:
                    self._pending.put_nowait((request, client_address))
                    self._counters["queued_connections"] += 1
                    self._counters["peak_queued_connections"] = max(
                        self._counters["peak_queued_connections"], self._counters["queued_connections"])
                except queue.Full:
                    refused = True
            if refused:
                self._counters["queue_refusals"] += 1
        if refused:
            self.shutdown_request(request)

    def _connection_worker(self):
        while True:
            try:
                request, address = self._pending.get(timeout=.05)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            with self._counter_lock:
                self._counters["queued_connections"] -= 1
                active = not self._closing.is_set()
                if active:
                    self._sockets.add(request)
                    self._counters["active_connections"] += 1
                    self._counters["peak_active_connections"] = max(
                        self._counters["peak_active_connections"], self._counters["active_connections"])
                else:
                    self._counters["drained_connections"] += 1
            try:
                if active:
                    self.finish_request(request, address)
            except (OSError, ValueError):
                # Request errors are returned/counted by the handler. A socket
                # disappearing during shutdown is not a new journal decision.
                pass
            except Exception:
                self._add(request_errors=1)
            finally:
                self.shutdown_request(request)
                if active:
                    with self._counter_lock:
                        self._sockets.discard(request)
                        self._counters["active_connections"] -= 1
                self._pending.task_done()

    def serve_forever(self, poll_interval=.05):
        with self._lifecycle:
            if self._stop_requested.is_set():
                self._serve_finished.set()
                self._lifecycle.notify_all()
                return
            self._serve_started.set()
            self._lifecycle.notify_all()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            with self._lifecycle:
                self._serve_finished.set()
                self._lifecycle.notify_all()

    def request_stop(self):
        with self._lifecycle:
            self._stop_requested.set()
            self._lifecycle.notify_all()

    def _coordinate_shutdown(self):
        with self._lifecycle:
            self._lifecycle.wait_for(self._stop_requested.is_set)
            self._lifecycle.wait_for(lambda: self._serve_started.is_set()
                                    or self._serve_finished.is_set() or self._closing.is_set())
            shutdown = self._serve_started.is_set() and not self._serve_finished.is_set()
        if shutdown:
            self.shutdown()

    def transport_metrics(self):
        with self._counter_lock:
            counters = dict(self._counters)
        return {"interface_version": INTERFACE_VERSION, "fixture_transport": copy.deepcopy(self.fixture_transport),
                **counters, "shutdown_requested": self._stop_requested.is_set(),
                "worker_threads_alive": sum(t.is_alive() for t in self._workers),
                "shutdown_thread_alive": self._coordinator.is_alive(), "join_complete": self._join_complete}

    def close_transport(self):
        """Bounded join. False means SQLite and the directory must stay open."""
        started = time.monotonic()
        deadline = started + self.fixture_transport["shutdown_grace_ms"] / 1000
        self.request_stop()
        with self._lifecycle:
            self._closing.set()
            self._lifecycle.notify_all()
        self._coordinator.join(max(0, deadline - time.monotonic()))
        super().server_close()  # Listener only; this class owns its fixed workers.
        while True:
            try:
                request, _ = self._pending.get_nowait()
            except queue.Empty:
                break
            with self._counter_lock:
                self._counters["queued_connections"] -= 1
                self._counters["drained_connections"] += 1
            self.shutdown_request(request)
            self._pending.task_done()
        with self._counter_lock:
            active_sockets = tuple(self._sockets)
        for connection in active_sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in [self._coordinator, *self._workers]:
            thread.join(max(0, deadline - time.monotonic()))
        self._join_complete = not any(t.is_alive() for t in [self._coordinator, *self._workers])
        return {**self.transport_metrics(), "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}


class PersistentFixtureHandlerMixin:
    """Use before Handler (or a fixture subclass of Handler) in the MRO."""

    def setup(self):
        super().setup()
        self.rfile.close()
        self.wfile.close()
        self.rfile = io.BufferedReader(_InterruptibleSocketReader(self.connection, self.server))
        self.wfile = _InterruptibleSocketWriter(self.connection, self.server)
        self._fixture_connection_requests = 0

    def handle_one_request(self):
        self._request_utf8 = None
        self._fixture_body = None
        self._fixture_body_ready = False
        self._fixture_started = False
        self._fixture_finished = False
        self._fixture_stop_after_response = False
        self.raw_requestline = b""
        started = time.monotonic()
        try:
            super().handle_one_request()
        finally:
            if (not self._fixture_started and self.close_connection
                    and not self.server._closing.is_set()
                    and time.monotonic() - started >= self.server.fixture_transport["socket_timeout_ms"] / 1000 - .02):
                self.server._add(idle_closures=1)

    def handle_expect_100(self):
        self.close_connection = True
        self.send_error(417, "fixture transport does not accept Expect")
        return False

    def _fixture_begin(self):
        if not self._fixture_started:
            self._fixture_started = True
            self._fixture_connection_requests += 1
            self.server._add(requests_started=1,
                             reused_requests=int(self._fixture_connection_requests > 1))

    def _fixture_finish(self, error=False):
        if not self._fixture_finished:
            self._fixture_finished = True
            self.server._add(requests_completed=1, request_errors=int(error))

    def send_error(self, code, message=None, explain=None):
        self._fixture_begin()
        self.close_connection = True
        self._fixture_finish(error=True)
        return super().send_error(code, message, explain)

    def _fixture_no_pipeline(self):
        # A nonblocking buffered peek sees both bytes already buffered by the
        # HTTP parser and bytes still in the socket, without waiting for a new
        # legitimate request. It never consumes the following bytes.
        timeout = self.connection.gettimeout()
        try:
            self.connection.setblocking(False)
            try:
                pending = self.rfile.peek(1)
            except (BlockingIOError, TimeoutError):
                pending = b""
        finally:
            self.connection.settimeout(timeout)
        if pending:
            raise JournalError("invalid_body", "fixture transport forbids pipelining")

    def _fixture_get_framing(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding", []) or lengths not in ([], ["0"]):
            raise JournalError("invalid_body", "GET requires no request body")
        self._fixture_no_pipeline()

    def _fixture_read_post(self):
        path, query = self._query()
        if query:
            raise JournalError("invalid_query", "mutation query parameters are not allowed")
        if path.startswith("/synthetic-"):
            self._test_auth()
            limit = self.server.journal.limits.max_body_bytes
        else:
            grant = self._grant()  # Immutable configured identity; no DB access.
            limit = (self.server.journal.limits.max_worker_commit_bytes
                     if path == PREFIX + "/worker-commits" and "worker_commit" in grant.operations
                     else self.server.journal.limits.max_body_bytes)
        self._fixture_body = Handler._body(self, limit)
        self._fixture_body_ready = True
        self._fixture_no_pipeline()

    def _body(self, limit=None):
        if not self._fixture_body_ready or self._request_utf8 is None:
            raise JournalError("invalid_body", "fixture body preflight required")
        if len(self._request_utf8.encode("utf-8")) > (limit or self.server.journal.limits.max_body_bytes):
            raise JournalError("payload_too_large", "body exceeds configured bound", 413)
        return self._fixture_body

    def _fixture_respond(self, status, encoded, *, reusable=False):
        keep = (reusable and status == 200 and self.request_version == "HTTP/1.1"
                and not self.close_connection and not self.server._stop_requested.is_set()
                and not self._fixture_stop_after_response
                and self._fixture_connection_requests < self.server.fixture_transport["requests_per_connection"])
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "keep-alive" if keep else "close")
        # One bounded write avoids a tiny header/body packet pair on each reused
        # loopback connection. Framing is identical; no socket/OS tuning is used.
        self._headers_buffer.append(b"\r\n")
        framed = b"".join(self._headers_buffer) + encoded
        self._headers_buffer = []
        self.wfile.write(framed)
        self.wfile.flush()
        self.close_connection = not keep
        if self._fixture_stop_after_response:
            self.server.request_stop()

    def _fixture_handle(self, action, *, post=False):
        self._fixture_begin()
        status = 200
        try:
            if post:
                self._fixture_read_post()  # Socket read is outside journal.lock.
            else:
                self._fixture_get_framing()
            with self.server.journal.lock:
                # _get/_post freshly authenticate and check all original rights.
                value = action()
                encoded = bounded_json(value, self.server.journal.limits.max_response_bytes).encode("utf-8")
            try:
                self._fixture_no_pipeline()
            except JournalError:
                # The first action may already have committed. Preserve its
                # actual result/ACK and close; never label that admission rejected
                # or process the extra request bytes.
                self.close_connection = True
                self.server._add(late_pipeline_closures=1)
        except JournalError as error:
            status = error.status
            encoded = bounded_json(error.response(), self.server.journal.limits.max_response_bytes).encode("utf-8")
        except (ValueError, TypeError, KeyError, RecursionError):
            status = 400
            encoded = bounded_json(JournalError("invalid_payload", "request does not match the typed local boundary").response(),
                                   self.server.journal.limits.max_response_bytes).encode("utf-8")
        except (OSError, TimeoutError) as error:
            self.close_connection = True
            self.server._add(body_read_timeouts=int(post and isinstance(error, TimeoutError)))
            self._fixture_finish(error=True)
            return
        try:
            self._fixture_respond(status, encoded, reusable=status == 200)
        except (OSError, TimeoutError):
            self.close_connection = True
            self._fixture_finish(error=True)
            return
        self._fixture_finish(error=status != 200)

    def do_GET(self):
        self._fixture_handle(self._get)

    def do_POST(self):
        self._fixture_handle(self._post, post=True)


class PersistentFixtureHandler(PersistentFixtureHandlerMixin, Handler):
    pass

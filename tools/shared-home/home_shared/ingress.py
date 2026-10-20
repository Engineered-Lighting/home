"""Bounded, certificate-bound TLS transport for the existing admission gateway.

This is a transport component, not a production launcher or a resource authority.
Deployment must still supply the real Gateway and satisfy its admission gates.
Certificate fingerprints are SHA256 over DER; HTTP identity/bearer headers confer
no authority here. Rotation/revocation requires replacing the listener registry.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.server import HTTPServer
import io
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
from types import MappingProxyType
from typing import Mapping

from .admission import AdmissionError
from .gateway import CredentialBinding, Gateway, _Handler


MAX_REQUESTS_PER_CONNECTION = 1000


@dataclass(frozen=True)
class IngressPolicy:
    """Connection slots count connections, never requests.

    max_requests_per_connection None (the default) is the HTTP/1.0 transport
    as before: one request per connection, read deadline from the handshake.
    A number switches the TLS handler to HTTP/1.1 (speed plan S1.5): at most
    that many requests on one connection (1: Connection: close on the first),
    each under its own request_read_timeout_seconds deadline from its first
    byte, after at most idle_timeout_seconds waiting for it.
    """
    max_connections: int
    handshake_timeout_seconds: float
    request_read_timeout_seconds: float
    write_timeout_seconds: float
    max_requests_per_connection: int | None = None
    idle_timeout_seconds: float = 20

    def __post_init__(self):
        if type(self.max_connections) is not int or not 1 <= self.max_connections <= 128:
            raise AdmissionError("invalid ingress connection limit")
        if self.max_requests_per_connection is not None and (
                type(self.max_requests_per_connection) is not int
                or not 1 <= self.max_requests_per_connection <= MAX_REQUESTS_PER_CONNECTION):
            raise AdmissionError("invalid ingress request limit")
        for value in (self.handshake_timeout_seconds, self.request_read_timeout_seconds,
                      self.write_timeout_seconds, self.idle_timeout_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
                raise AdmissionError("invalid ingress timeout")


def _close(connection):
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    connection.close()


class _DeadlineReader(io.RawIOBase):
    """The handler's socket reads, each under its absolute read deadline."""

    def __init__(self, handler):
        super().__init__()
        self._handler = handler

    def readable(self):
        return True

    def readinto(self, buffer):
        return self._handler._read_into(buffer)


class _TLSHandler(_Handler):
    """The absolute request-read deadline is enforced by the reading worker
    itself, with no timer thread (speed plan S1.1): before every socket read
    the timeout is narrowed to the time left, so a peer that drips bytes
    cannot stretch the read, and at the deadline the connection is shut down
    as the per-connection Timer did.

    Keep-alive (speed plan S1.5), only when the policy sets a request cap:
    this handler then speaks HTTP/1.1 (gateway._Handler stays HTTP/1.0). Each
    request gets its own read deadline, armed when its first byte arrives;
    before it the connection may wait idle_timeout_seconds and then closes
    quietly. A response carries Connection: close, and the connection closes
    after it, whenever it is the last one this connection serves: the request
    cap, a body left unread, a peer that asked to close or is not HTTP/1.1, an
    expired read deadline, the legacy chat route or a closing listener."""

    def setup(self):
        policy = self.server.policy
        self._http11 = policy.max_requests_per_connection is not None
        if self._http11:
            self.protocol_version = "HTTP/1.1"
        self._served = 0
        self._idle = False
        self._arm(time.monotonic() + policy.request_read_timeout_seconds)
        super().setup()
        # The same buffered reader, over a raw reader that applies the
        # deadline. Closing the plain one keeps the socket's file count exact.
        self.rfile.close()
        self.rfile = io.BufferedReader(_DeadlineReader(self))

    def _arm(self, read_deadline):
        """Per-request state: the read deadline and what the response said."""
        self._reading = True
        self._expired = False
        self._read_deadline = read_deadline
        self._status = None
        self._responded = False
        self._connection_header = False
        self._force_close = False

    def handle_one_request(self):
        if not self._http11:
            super().handle_one_request()
            return
        if not self._await_request():
            self.close_connection = True
            return
        super().handle_one_request()
        self._served += 1
        # Every such case already answered with Connection: close; a request
        # that got no answer at all (a truncated body) closes here.
        if self._expired or self._reading or not self._responded or self._force_close:
            self.close_connection = True

    def _await_request(self):
        """Wait up to the idle timeout for the next request's first byte, then
        arm that request's absolute read deadline from its arrival."""
        self._idle = True
        self._idle_deadline = time.monotonic() + self.server.policy.idle_timeout_seconds
        try:
            arrived = bool(self.rfile.peek(1))
        except (OSError, ValueError):
            arrived = False   # idle timeout, peer close or listener shutdown
        finally:
            self._idle = False
        if arrived and not self.server._closed:
            self._arm(time.monotonic() + self.server.policy.request_read_timeout_seconds)
            return True
        return False

    def parse_request(self):
        if not super().parse_request():
            return False
        # Only POST bodies are read (by Content-Length, then _body_received);
        # any other request that frames a body leaves it unread.
        if self._http11 and self.command != "POST" and (
                self.headers.get_all("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0"):
            self._force_close = True
        return True

    def _last_response(self):
        return bool(self.close_connection or self.request_version != "HTTP/1.1"
                    or self._served + 1 >= self.server.policy.max_requests_per_connection
                    or self._reading or self._expired or self._force_close or self.server._closed)

    def send_response_only(self, code, message=None):
        self._status = code
        super().send_response_only(code, message)

    def send_header(self, keyword, value):
        if keyword.lower() == "connection":
            self._connection_header = True
        super().send_header(keyword, value)

    def end_headers(self):
        # Interim 100 Continue never carries the decision; a final response
        # that is the connection's last says so (send_error already does).
        if (self._http11 and self._status is not None and self._status >= 200
                and not self._connection_header and self._last_response()):
            self.send_header("Connection", "close")
        if self._status is not None and self._status >= 200:
            self._responded = True
        super().end_headers()

    def _chat(self):
        # The legacy route streams and always closes after its response.
        self._force_close = True
        super()._chat()

    def _read_into(self, buffer):
        if self._idle:
            remaining = self._idle_deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("keep-alive idle timeout elapsed")
            self.connection.settimeout(remaining)
            return self.connection.recv_into(buffer)
        if self._reading:
            remaining = self._read_deadline - time.monotonic()
            if self._expired or remaining <= 0:
                self._expire_read()
                raise socket.timeout("request read deadline elapsed")
            self.connection.settimeout(remaining)
        try:
            return self.connection.recv_into(buffer)
        except socket.timeout:
            if self._reading:
                self._expire_read()
            raise

    def _expire_read(self):
        self._expired = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _body_received(self):
        if self._expired or time.monotonic() >= self._read_deadline:
            raise OSError("request read deadline elapsed")
        self._reading = False
        self.connection.settimeout(self.server.policy.write_timeout_seconds)

    def do_GET(self):
        self._body_received()
        if self.path == "/healthz":
            self._write(200, {"alive": True, "mode": "private-mtls-ingress"})
        else:
            super().do_GET()

    def _binding(self):
        certificate = self.connection.getpeercert(binary_form=True)
        return self.server.identities.get(hashlib.sha256(certificate).hexdigest()) if certificate else None


class PrivateTLSIngress(HTTPServer):
    """One bounded worker per accepted connection, including TLS handshake.

    No secondary inference queue is introduced. Excess sockets close immediately.
    A slot is held by a connection, however many keep-alive requests it carries,
    and released when it closes.
    Shutdown closes active sockets; slots remain charged until handler cleanup,
    including any in-progress admitted backend call. No automatic engine restart.
    """
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], gateway: Gateway, *, certificate: str,
                 private_key: str, client_ca: str,
                 identities: Mapping[str, CredentialBinding], policy: IngressPolicy):
        try:
            host = ipaddress.ip_address(address[0])
        except ValueError as exc:
            raise AdmissionError("ingress requires a literal private address") from exc
        private_ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                          "100.64.0.0/10", "fc00::/7")
        if not (host.is_loopback or any(host in ipaddress.ip_network(net) for net in private_ranges)):
            raise AdmissionError("ingress requires a private or loopback address")
        if type(address[1]) is not int or not 0 <= address[1] <= 65535:
            raise AdmissionError("invalid ingress port")
        if not isinstance(policy, IngressPolicy) or not identities:
            raise AdmissionError("explicit ingress policy and identities required")
        if any(not isinstance(key, str) or not re.fullmatch("[0-9a-f]{64}", key)
               or not isinstance(value, CredentialBinding) for key, value in identities.items()):
            raise AdmissionError("invalid certificate identity registry")
        self.gateway, self.policy = gateway, policy
        self.identities = MappingProxyType(dict(identities))
        self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self._tls.verify_mode = ssl.CERT_REQUIRED
        self._tls.load_cert_chain(certificate, private_key)
        self._tls.load_verify_locations(client_ca)
        # No TLS session resumption (speed plan S1.5): no TLS 1.2 or 1.3
        # tickets. A cached TLS 1.2 session ID is refused in _worker.
        self._tls.options |= ssl.OP_NO_TICKET
        self._tls.num_tickets = 0
        self._slots = threading.BoundedSemaphore(policy.max_connections)
        self._connections = set()
        self._lock = threading.Lock()
        self._drained = threading.Condition(self._lock)
        self._closed = False
        self.request_queue_size = policy.max_connections
        self.address_family = socket.AF_INET6 if host.version == 6 else socket.AF_INET
        super().__init__(address, _TLSHandler)

    @property
    def active_connections(self):
        with self._lock:
            return len(self._connections)

    def wait_handlers(self, timeout_seconds: float) -> bool:
        """Bounded transport drain; never proves independent engine completion."""
        if (type(timeout_seconds) not in (int, float) or not 0 <= timeout_seconds <= 60
                or not math.isfinite(timeout_seconds)):
            raise AdmissionError("invalid handler drain timeout")
        with self._drained:
            return self._drained.wait_for(
                lambda: self._closed and not self._connections, timeout_seconds)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            _close(request)
            return
        with self._lock:
            if self._closed:
                _close(request)
                self._slots.release()
                return
            self._connections.add(request)
        try:
            threading.Thread(target=self._worker, args=(request, client_address), daemon=True).start()
        except BaseException:
            with self._lock:
                self._connections.discard(request)
                self._drained.notify_all()
            _close(request)
            self._slots.release()
            raise

    def _worker(self, raw, address):
        connection = raw
        try:
            raw.settimeout(self.policy.handshake_timeout_seconds)
            try:
                # Without the close that used to push the last segment, a
                # header write and a body write would meet Nagle and delayed
                # ACKs (speed plan S1.5): send every write at once.
                raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            connection = self._tls.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
            with self._lock:
                self._connections.discard(raw)
                self._connections.add(connection)
                if self._closed:
                    return
            connection.do_handshake()
            # Identity only from a full handshake that verified the client
            # certificate. Tickets are off (see __init__), but OpenSSL still
            # resumes a TLS 1.2 session ID from its cache, skipping that
            # verification: such a connection is served nothing.
            if connection.session_reused:
                return
            fingerprint = hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest()
            if fingerprint not in self.identities:
                return
            connection.settimeout(self.policy.request_read_timeout_seconds)
            self.finish_request(connection, address)
        except OSError:
            pass  # Never emit certificate, request or exception contents.
        except Exception:
            # Unexpected handler/storage failures cannot leave admission open or
            # print request-bearing exception text from a worker traceback.
            if not self._closed:
                try:
                    self.gateway.controller.close_admission("private TLS ingress worker failed")
                except Exception:
                    pass  # Controller/ledger failure is itself fail-closed.
        finally:
            try:
                _close(connection)
            finally:
                with self._lock:
                    self._connections.discard(raw)
                    self._connections.discard(connection)
                    self._drained.notify_all()
                self._slots.release()

    def handle_error(self, request, client_address):
        pass

    def server_close(self):
        # A handler can already have read/authenticated a body and still be
        # blocked before submission. Close admission before disconnecting it;
        # listener replacement must not leave that old request executable.
        self.gateway.controller.close_admission("private TLS ingress stopping")
        with self._lock:
            self._closed = True
            connections = tuple(self._connections)
            self._drained.notify_all()
        super().server_close()
        for connection in connections:
            _close(connection)

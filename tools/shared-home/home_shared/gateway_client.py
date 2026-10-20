"""Fixed, provisioned HTTPS transport for the Victoria inference adapter.

Transport failures, including HTTP 404, never prove nonacceptance. The caller
must durably reserve before submit and reconcile by lookup without resubmitting.
This client does not establish LA health, workload authorization or admission.
TLS follows https://docs.python.org/3/library/ssl.html#ssl.PROTOCOL_TLS_CLIENT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping

from .gateway import CredentialBinding, MAX_HTTP_BODY_BYTES, validate_inference_request
from .rejections import REJECTION_REASONS, validate_rejection


MAX_RESPONSE_BYTES = 128 * 1024
MAX_HEADER_BYTES = 16 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_OUTCOMES = {
    "queued": {None}, "dispatching": {None}, "running": {None},
    "indeterminate": {"dispatcher_restart", "payload_unavailable", "upstream_timeout", "upstream_outcome_unknown", "upstream_indeterminate"},
    "terminal": {"completed", "completed_after_deadline", "queue_expired", "restart_before_dispatch",
                 "preflight_rejected", "engine_stopped", *REJECTION_REASONS},
}


class GatewayTransportError(RuntimeError):
    """No trustworthy response; execution may still have been accepted."""


class GatewayBusy(GatewayTransportError):
    """An earlier network/decoder operation has not exited."""


class GatewayNotSent(GatewayTransportError):
    """This POST failed locally before any network worker was launched.

    The caller may close its own unsent reservation. This is not evidence about
    an existing gateway reservation or any earlier request with the same ID.
    """


class GatewayDeadline(GatewayTransportError):
    pass


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class GatewayClientConfig:
    """Installed configuration, never constructed from an inference request.

    Network commissioning must verify private routing to this exact hostname.
    DNS cannot confer identity: the configured CA and hostname must verify TLS.
    Credentials and trust files are operator-provisioned, not discovery inputs.
    """

    hostname: str
    port: int
    ca_file: str | Path
    bearer_token: str = field(repr=False)
    device_id: str
    cameras: Mapping[str, str]
    configuration_revision: str
    client_certificate: str | Path | None = None
    client_key: str | Path | None = field(default=None, repr=False)
    allow_interactive: bool = False

    def __post_init__(self):
        if type(self.allow_interactive) is not bool:
            raise ValueError("explicit interactive capability required")
        if (not isinstance(self.hostname, str) or len(self.hostname) > 253
                or any(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) is None
                       for part in self.hostname.split("."))
                or type(self.port) is not int or not 1 <= self.port <= 65535):
            raise ValueError("a fixed DNS hostname and valid port are required")
        if (not isinstance(self.bearer_token, str) or not 1 <= len(self.bearer_token) <= 4096
                or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", self.bearer_token) is None):
            raise ValueError("a bounded device bearer is required")
        if not isinstance(self.ca_file, (str, Path)) or not str(self.ca_file):
            raise ValueError("an explicit CA file is required")
        for value in (self.client_certificate, self.client_key):
            if value is not None and (not isinstance(value, (str, Path)) or not str(value)):
                raise ValueError("client certificate and key must be file paths")
        if (self.client_certificate is None) != (self.client_key is None):
            raise ValueError("both client certificate and key are required")
        if not isinstance(self.cameras, Mapping):
            raise ValueError("an explicit camera mapping is required")
        binding = CredentialBinding("victoria", self.device_id, "victoria-https-client",
                                    self.cameras, self.configuration_revision, allow_interactive=self.allow_interactive)
        object.__setattr__(self, "cameras", MappingProxyType(dict(binding.cameras)))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _constant(_):
    raise ValueError("nonfinite JSON constant")


class _Operation:
    """The one network worker of a GatewayClient operation: TCP, TLS, then
    exactly one request and its response on that connection. It holds the
    client's permit from start until its thread really exits.

    _request starts it with the request in hand; preopen() starts it before
    the request exists, so TCP and TLS overlap the caller's frame capture
    (speed plan S1.5), and the request arrives later through give(). Either
    way no byte of a request is sent before give() handed it over.
    """

    def __init__(self, client: "GatewayClient", remaining: Callable[[], float]):
        self._client, self._remaining = client, remaining
        self._guard = threading.Lock()
        self._changed = threading.Condition(self._guard)
        self._sockets: list = []
        self._cancelled = self._ended = False
        self._job = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="victoria-gateway-request", daemon=True).start()

    def give(self, job: "_Job") -> bool:
        """Hand over the one request; False once the worker ended or was
        cancelled without one (its connection is gone; nothing was sent)."""
        with self._guard:
            if self._ended or self._cancelled or self._job is not None:
                return False
            self._job = job
            self._changed.notify_all()
            return True

    def cancel(self) -> None:
        """Interrupt a stalled peer; the worker still exits in its own time."""
        with self._guard:
            self._cancelled = True
            self._changed.notify_all()
            sockets = list(self._sockets)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _register(self, connection) -> None:
        with self._guard:
            if self._cancelled:
                connection.close()
                raise GatewayDeadline("gateway response deadline elapsed")
            self._sockets[:] = [connection]

    def _wait_job(self) -> "_Job":
        with self._guard:
            while self._job is None and not self._cancelled:
                self._changed.wait(self._remaining())
            if self._cancelled:
                raise GatewayDeadline("gateway response deadline elapsed")
            return self._job

    def _run(self) -> None:
        client, config = self._client, self._client.config
        outcome = GatewayTransportError("gateway transport or response validation failed")
        try:
            remaining = self._remaining
            with client._connect((config.hostname, config.port), timeout=remaining()) as raw:
                self._register(raw)
                raw.settimeout(remaining())
                # Register the wrapped socket before handshake so caller
                # deadline cancellation can interrupt a stalled TLS peer.
                with client._tls.wrap_socket(raw, server_hostname=config.hostname,
                                             do_handshake_on_connect=False) as connection:
                    self._register(connection)
                    connection.settimeout(remaining())
                    connection.do_handshake()
                    job = self._wait_job()
                    remaining = job.remaining
                    connection.settimeout(remaining())
                    headers = (f"{job.method} {job.path} HTTP/1.1\r\n"
                               f"Host: {config.hostname}:{config.port}\r\n"
                               f"Authorization: Bearer {config.bearer_token}\r\n"
                               "Accept: application/json\r\nConnection: close\r\n"
                               "Content-Type: application/json\r\n"
                               f"Content-Length: {len(job.body)}\r\n\r\n")
                    connection.sendall(headers.encode("ascii") + job.body)
                    outcome = client._response(connection, job.request_id, remaining, job.expected_digest)
                    remaining()
        except GatewayTransportError as exc:
            outcome = exc
        except Exception:
            outcome = GatewayTransportError("gateway transport or response validation failed")
        finally:
            with self._guard:
                self._ended = True
                self._sockets.clear()
                job = self._job
            client._inflight.release()
        if job is not None:
            job.result.put(outcome)


@dataclass(frozen=True)
class _Job:
    method: str
    path: str
    body: bytes
    request_id: str
    remaining: Callable[[], float]
    expected_digest: str | None
    result: queue.Queue


class PreopenedConnection:
    """TCP and TLS to the gateway from GatewayClient.preopen(), for exactly
    one later submit(payload, preopened=...), which owns it from then on.
    close() it when no request will follow (a failed capture): it then closes
    having sent nothing and returns the client's permit. Never reused."""

    def __init__(self, client: "GatewayClient", operation: _Operation | None):
        self._client = client
        self._operation = operation   # None: nothing opened; submit dials as before
        self._lock = threading.Lock()

    def _take(self) -> _Operation | None:
        with self._lock:
            operation, self._operation = self._operation, None
        return operation

    def close(self) -> None:
        """Idempotent, never blocks, and a no-op once submit took it."""
        operation = self._take()
        if operation is not None:
            operation.cancel()


class GatewayClient:
    """At most one network operation, no redirects/proxies/automatic retries.

    The absolute request deadline covers serialization, DNS, TLS, upload, read
    and JSON decoding. Status lookup has a separate three-second budget so it
    can reconcile after execution expiry. A timed-out worker retains the permit
    until it really exits; stuck DNS cannot accumulate additional workers.
    preopen() starts that one worker's TCP and TLS early, still for a single
    request on its own connection.
    """

    def __init__(self, config: GatewayClientConfig, *, clock: Callable = time.time,
                 monotonic: Callable = time.monotonic, _connect: Callable = socket.create_connection):
        self.config = config
        self._clock, self._monotonic, self._connect = clock, monotonic, _connect
        self._inflight = threading.Lock()
        try:
            with open(config.ca_file, "rb") as source:
                ca_bytes = source.read(1024 * 1024 + 1)
            if not ca_bytes or len(ca_bytes) > 1024 * 1024:
                raise ValueError("CA bundle exceeds bounds")
            # Explicit context avoids environment-controlled SSLKEYLOGFILE and
            # implicit system trust additions for this private endpoint.
            self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
            self._tls.hostname_checks_common_name = False
            # Bind identity to the same immutable CA bytes used for trust,
            # avoiding a second file read during certificate rotation.
            self._tls.load_verify_locations(cadata=ca_bytes.decode("ascii"))
            if config.client_certificate:
                self._tls.load_cert_chain(str(config.client_certificate), str(config.client_key))
        except Exception:
            raise GatewayTransportError("gateway trust configuration unavailable") from None
        self._binding = CredentialBinding("victoria", config.device_id, "victoria-https-client",
                                          config.cameras, config.configuration_revision, allow_interactive=config.allow_interactive)
        identity = {"version": 1, "transport": "https", "site_id": "victoria",
                    "hostname": config.hostname.lower(), "port": config.port,
                    "ca_sha256": hashlib.sha256(ca_bytes).hexdigest(),
                    "device_id": config.device_id, "cameras": dict(config.cameras),
                    "configuration_revision": config.configuration_revision}
        if config.allow_interactive:
            identity["allow_interactive"] = True
        self._gateway_identity = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @property
    def gateway_identity(self) -> str:
        """Stable provisioned ingress identity; excludes bearer/private keys."""
        return self._gateway_identity

    def preopen(self, budget: float = 15.0) -> PreopenedConnection:
        """Start TCP and TLS to the gateway now, in parallel with the caller's
        frame capture (speed plan S1.5), for exactly one later submit().

        The worker holds this client's one permit until that submit's exchange
        ends or close(); budget (at most 15 s) bounds its connect, handshake
        and wait for the request. A dial or handshake that fails sends nothing,
        and the submit it was meant for then fails exactly as after a failed
        fresh dial. Never raises: with the permit busy, an unusable budget or
        no worker thread, the handle opens nothing and submit() dials as before.
        """
        started_mono, started_wall = self._monotonic(), self._clock()
        if (not all(_finite(value) for value in (budget, started_mono, started_wall)) or budget <= 0
                or not self._inflight.acquire(blocking=False)):
            return PreopenedConnection(self, None)
        operation = _Operation(self, self._deadline(started_mono, started_wall, min(15.0, budget)))
        try:
            operation.start()
        except Exception:
            self._inflight.release()
            return PreopenedConnection(self, None)
        return PreopenedConnection(self, operation)

    def submit(self, payload: dict, *, preopened: PreopenedConnection | None = None) -> dict:
        """With preopened (from this client's preopen()) the request goes on
        that connection and no other; submit closes it on every path."""
        started_mono, started_wall = self._monotonic(), self._clock()
        try:
            if preopened is not None and (not isinstance(preopened, PreopenedConnection)
                                          or preopened._client is not self):
                preopened = None
                raise ValueError("pre-opened connection belongs to another client")
            if not _finite(started_mono) or not _finite(started_wall):
                raise ValueError("invalid clock")
            # Snapshot the exact validated bytes to prevent a mutable caller
            # changing identity or image after validation while I/O is pending.
            encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_HTTP_BODY_BYTES:
                raise ValueError("oversized request")
            snapshot = json.loads(encoded)
            request = validate_inference_request(snapshot, self._binding, started_wall)
            budget = min(15.0, request.request_deadline - started_wall)
        except Exception:
            if preopened is not None:
                preopened.close()
            raise GatewayNotSent("gateway request validation failed before send") from None
        return self._request("POST", "/v1/inference", request.request_id, encoded,
                             started_mono, started_wall, budget, expected_digest=request.digest,
                             preopened=preopened)

    def status(self, request_id: str) -> dict:
        if not isinstance(request_id, str) or _ID.fullmatch(request_id) is None:
            raise GatewayTransportError("gateway request identity invalid")
        return self._request("GET", "/v1/inference/" + request_id, request_id, b"",
                             self._monotonic(), self._clock(), 3.0)

    def _deadline(self, started_mono, started_wall, budget):
        def remaining():
            mono, wall = self._monotonic(), self._clock()
            if (not all(_finite(v) for v in (mono, wall, started_mono, started_wall, budget))
                    or mono < started_mono or wall < started_wall):
                raise GatewayDeadline("gateway clock is invalid")
            left = min(started_mono + budget - mono, started_wall + budget - wall)
            if left <= 0:
                raise GatewayDeadline("gateway response deadline elapsed")
            return left
        return remaining

    def _request(self, method, path, request_id, body, started_mono, started_wall, budget, *,
                 expected_digest=None, preopened=None):
        remaining = self._deadline(started_mono, started_wall, budget)
        # A pre-opened worker already holds the permit: it is this request's.
        operation = preopened._take() if preopened is not None else None
        try:
            remaining()
        except GatewayDeadline:
            if operation is not None:
                operation.cancel()
            if method == "POST":
                raise GatewayNotSent("gateway request deadline elapsed before send") from None
            raise
        job = _Job(method, path, body, request_id, remaining, expected_digest, queue.Queue(maxsize=1))
        if operation is None:
            if not self._inflight.acquire(blocking=False):
                if method == "POST":
                    raise GatewayNotSent("gateway request blocked locally before send")
                raise GatewayBusy("gateway operation still in progress")
            operation = _Operation(self, remaining)
            operation.give(job)
            try:
                operation.start()
            except Exception:
                self._inflight.release()
                if method == "POST":
                    raise GatewayNotSent("gateway request worker could not start") from None
                raise GatewayTransportError("gateway operation could not start") from None
        elif not operation.give(job):
            # The pre-opened worker already ended: its dial or handshake failed,
            # or it waited out its budget. No byte of this request was sent, yet
            # as after a failed fresh dial this is no proof of nonacceptance.
            raise GatewayTransportError("gateway transport or response validation failed")
        try:
            try:
                outcome = job.result.get(timeout=remaining())
            except queue.Empty:
                raise GatewayDeadline("gateway response deadline elapsed") from None
            remaining()
            if isinstance(outcome, GatewayTransportError):
                raise outcome from None
            return outcome
        finally:
            operation.cancel()

    def _response(self, connection, request_id, remaining, expected_digest=None):
        def receive(length):
            connection.settimeout(remaining())
            chunk = connection.recv(length)
            remaining()
            if not chunk:
                raise GatewayTransportError("gateway response incomplete")
            return chunk

        data = bytearray()
        while b"\r\n\r\n" not in data:
            if len(data) >= MAX_HEADER_BYTES:
                raise GatewayTransportError("gateway response headers exceed bounds")
            data.extend(receive(min(4096, MAX_HEADER_BYTES - len(data))))
        head, body = bytes(data).split(b"\r\n\r\n", 1)
        lines = head.decode("ascii").split("\r\n")
        if not re.fullmatch(r"HTTP/1\.[01] 2[0-9]{2}(?: [\x20-\x7e]*)?", lines[0]):
            raise GatewayTransportError("gateway did not return a successful status")
        if len(lines) > 65:
            raise GatewayTransportError("gateway response headers exceed bounds")
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if (not separator or re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", name) is None
                    or any(ord(c) < 32 and c != "\t" or ord(c) > 126 for c in value)):
                raise GatewayTransportError("gateway response headers invalid")
            name = name.lower()
            if name in headers:
                raise GatewayTransportError("gateway response headers ambiguous")
            headers[name] = value.strip()
        length = headers.get("content-length", "")
        if (not re.fullmatch(r"[0-9]{1,6}", length) or not 1 <= int(length) <= MAX_RESPONSE_BYTES
                or headers.get("content-type", "").lower() not in {"application/json", "application/json; charset=utf-8"}
                or "transfer-encoding" in headers or headers.get("content-encoding", "identity").lower() != "identity"):
            raise GatewayTransportError("gateway response framing invalid")
        length = int(length)
        if len(body) > length:
            raise GatewayTransportError("gateway response framing ambiguous")
        body = bytearray(body)
        while len(body) < length:
            body.extend(receive(min(4096, length - len(body))))
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_object,
                           parse_float=_number, parse_constant=_constant)
        remaining()
        if (not isinstance(value, dict) or not set(value) <= {"version", "request_id", "site_id", "status", "outcome", "observation", "rejection"}
                or not {"version", "request_id", "site_id", "status", "outcome"} <= set(value)
                or type(value["version"]) is not int or value["version"] != 1
                or value["request_id"] != request_id or value["site_id"] != "victoria"
                or not isinstance(value["status"], str) or value["status"] not in _OUTCOMES
                or value["outcome"] not in _OUTCOMES[value["status"]]):
            raise GatewayTransportError("gateway response identity or state invalid")
        if value["outcome"] in REJECTION_REASONS or "rejection" in value:
            if not validate_rejection(value, device_id=self.config.device_id, request_digest=expected_digest):
                raise GatewayTransportError("gateway rejection receipt invalid")
        if "observation" in value:
            observation = value["observation"]
            if (value["status"] != "terminal" or value["outcome"] != "completed"
                    or not isinstance(observation, dict) or observation.get("site_id") != "victoria"
                    or observation.get("device_id") != self.config.device_id
                    or self.config.cameras.get(observation.get("camera_id")) != observation.get("room_id")
                    or observation.get("camera_id") not in self.config.cameras
                    or observation.get("configuration_revision") != self.config.configuration_revision):
                raise GatewayTransportError("gateway observation binding invalid")
        return value

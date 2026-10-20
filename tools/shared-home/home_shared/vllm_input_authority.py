"""Fixed vLLM input preflight; a prepare token never authorizes generation.

Only the exact serialized request is sent. A bound is usable only for that
body/model/manifest/engine/ledger/deadline, and execute separately signs the
accepted count. This client performs no model work and assumes no local GPU.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .admission import ResourceManifest
from .dispatch_token import DispatchTokenAuthority
from .legacy_chat import ChatInput
from .openai_backend import (BackendBusy, BackendDeadline, BackendUnknown,
                             InputTokenBound, SnapshotInput)


MAX_BODY = 16 * 1024 * 1024
MAX_RESPONSE = 4096
MAX_HEADERS = 16 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}")
_DIGEST = re.compile(r"[a-f0-9]{64}")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate JSON member")
        value[name] = item
    return value


def _constant(_):
    raise ValueError("nonfinite JSON")


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)


class FixedInputAuthority:
    """Synchronous caller API, one retained worker for bounded blocking I/O.

    DNS/TLS/header stalls cannot extend the caller's absolute deadline. A
    timed-out worker keeps its permit until it actually exits, cannot send
    after a late connect, and cannot publish a late token bound. Connections
    are per-call, direct, and closed on every outcome; no redirects or retries.
    """
    def __init__(self, manifest: ResourceManifest, authority: DispatchTokenAuthority, *,
                 ca_file=None, bearer_token=None, clock=time.time, monotonic=time.monotonic):
        if (not isinstance(manifest, ResourceManifest) or not isinstance(authority, DispatchTokenAuthority)
                or manifest.digest != authority.manifest_digest or not callable(clock) or not callable(monotonic)):
            raise ValueError("fixed manifest and matching dispatch authority required")
        self._manifest, self._authority = manifest, authority
        self._binding = manifest.digest, authority.engine_epoch
        try:
            origin = urlsplit(manifest.upstream)
            host = origin.hostname
            port = origin.port if origin.port is not None else (443 if origin.scheme == "https" else 80)
            if (origin.scheme not in {"http", "https"} or not host or origin.username or origin.password
                    or origin.query or origin.fragment or not 1 <= port <= 65535
                    or origin.path not in {"", "/", "/v1", "/v1/", "/v1/chat/completions"}):
                raise ValueError()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
                if len(host) > 253 or any(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
                                         for label in host.split(".")):
                    raise ValueError()
            if address is not None and ("%" in host or address.is_unspecified or address.is_multicast):
                raise ValueError()
            if origin.scheme == "http" and host not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError()
        except Exception:
            raise ValueError("fixed HTTPS or loopback HTTP origin required") from None
        self._scheme, self._host, self._port = origin.scheme, host, port
        self._dial_host = "127.0.0.1" if origin.scheme == "http" and host == "localhost" else host
        if bearer_token is not None and (type(bearer_token) is not str or not 1 <= len(bearer_token) <= 4096
                or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", bearer_token) is None):
            raise ValueError("bounded dedicated model bearer required")
        self._bearer = bearer_token
        self._tls = None
        if origin.scheme == "https":
            try:
                with open(ca_file, "rb") as source:
                    ca = source.read(1024 * 1024 + 1)
                if not 1 <= len(ca) <= 1024 * 1024:
                    raise ValueError()
                self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
                self._tls.hostname_checks_common_name = False
                self._tls.load_verify_locations(cadata=ca.decode("ascii"))
            except Exception:
                raise ValueError("explicit input-authority TLS trust unavailable") from None
        elif ca_file is not None:
            raise ValueError("TLS trust does not apply to loopback HTTP")
        self._clock, self._mono = clock, monotonic
        self._permit = threading.Lock()

    @property
    def manifest_digest(self):
        return self._binding[0]

    @property
    def engine_epoch(self):
        return self._binding[1]

    def _validate(self, value, deadline):
        if (not isinstance(value, (SnapshotInput, ChatInput))
                or (self._authority.manifest_digest, self._authority.engine_epoch) != self._binding
                or (value.manifest_digest, value.engine_epoch) != self._binding
                or value.model_revision != self._manifest.model_revision
                or type(value.body) is not bytes or not 1 <= len(value.body) <= MAX_BODY
                or type(value.ledger_id) is not str or _ID.fullmatch(value.ledger_id) is None
                or type(value.request_digest) is not str or _DIGEST.fullmatch(value.request_digest) is None
                or type(value.upstream_body_sha256) is not str or _DIGEST.fullmatch(value.upstream_body_sha256) is None
                or hashlib.sha256(value.body).hexdigest() != value.upstream_body_sha256
                or type(value.max_output_tokens) is not int
                or not 1 <= value.max_output_tokens <= self._manifest.max_output_tokens
                or not _finite(deadline)):
            raise BackendUnknown("input-authority request binding rejected")
        try:
            body = _json(value.body)
            if (not isinstance(body, dict) or body.get("model") != self._manifest.model_alias
                    or type(body.get("max_tokens")) is not int or body["max_tokens"] != value.max_output_tokens):
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise BackendUnknown("input-authority request body rejected") from None

    def assess(self, value, deadline):
        if not self._permit.acquire(False):
            raise BackendBusy("input-authority operation still outstanding")
        started_worker = False
        cancelled = threading.Event()
        socket_ref = [None]
        result = queue.Queue(maxsize=1)
        try:
            started_wall, started_mono = self._clock(), self._mono()
            if (not all(_finite(v) for v in (deadline, started_wall, started_mono))
                    or not 0 < deadline - started_wall <= self._manifest.max_deadline_seconds):
                raise BackendDeadline("input-authority deadline unavailable")
            mono_deadline = started_mono + (deadline - started_wall)
            last = [started_wall, started_mono]
            clock_lock = threading.Lock()

            def remaining():
                with clock_lock:
                    wall, mono = self._clock(), self._mono()
                    if (cancelled.is_set() or not _finite(wall) or not _finite(mono)
                            or wall < last[0] or mono < last[1]):
                        raise BackendDeadline("input-authority clock or deadline invalid")
                    last[:] = [wall, mono]
                    left = min(deadline - wall, mono_deadline - mono)
                    if left <= 0:
                        raise BackendDeadline("input-authority deadline elapsed")
                    return left

            self._validate(value, deadline)
            remaining()
            token = self._authority.issue(value.ledger_id, value.request_digest, value.upstream_body_sha256,
                                          deadline, purpose="prepare", input_bound_tokens=None)
            remaining()

            def run():
                connection = None
                try:
                    if self._scheme == "https":
                        connection = http.client.HTTPSConnection(self._dial_host, self._port,
                                                                 timeout=remaining(), context=self._tls)
                    else:
                        connection = http.client.HTTPConnection(self._dial_host, self._port, timeout=remaining())
                    connection.connect()
                    socket_ref[0] = connection.sock
                    connection.sock.settimeout(remaining())
                    host_header = f"[{self._host}]" if ":" in self._host else self._host
                    headers = {"Content-Type": "application/json", "Accept": "application/json",
                               "Accept-Encoding": "identity", "Content-Length": str(len(value.body)),
                               "Host": f"{host_header}:{self._port}",
                               "Connection": "close", "X-Shared-Home-Dispatch": token,
                               "X-Request-ID": value.ledger_id}
                    if self._bearer is not None:
                        headers["Authorization"] = "Bearer " + self._bearer
                    connection.request("POST", "/shared-home/input-bound", body=value.body, headers=headers)
                    connection.sock.settimeout(remaining())
                    response = connection.getresponse()
                    remaining()
                    pairs = response.getheaders()
                    if (len(pairs) > 32 or sum(len(k) + len(v) + 4 for k, v in pairs) > MAX_HEADERS
                            or len({k.lower() for k, _ in pairs}) != len(pairs)
                            or any(any(ord(c) < 32 or ord(c) == 127 for c in k + v) for k, v in pairs)):
                        raise ValueError()
                    parsed = {k.lower(): v for k, v in pairs}
                    length = parsed.get("content-length", "")
                    if (response.status != 200 or "transfer-encoding" in parsed or "content-encoding" in parsed
                            or re.fullmatch(r"[0-9]{1,4}", length) is None or not 1 <= int(length) <= MAX_RESPONSE
                            or parsed.get("content-type", "").lower().replace(" ", "")
                            not in {"application/json", "application/json;charset=utf-8"}):
                        raise ValueError()
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining())
                    raw = response.read(int(length) + 1)
                    remaining()
                    if len(raw) != int(length):
                        raise ValueError()
                    answer = _json(raw)
                    expected = {"version": 1, "manifest_digest": self.manifest_digest,
                                "engine_epoch": self.engine_epoch, "model_revision": value.model_revision,
                                "upstream_body_sha256": value.upstream_body_sha256,
                                "valid_until": deadline, "ledger_id": value.ledger_id,
                                "request_digest": value.request_digest}
                    if (not isinstance(answer, dict) or set(answer) != set(expected) | {"upper_bound_tokens"}
                            or type(answer["version"]) is not int
                            or any(answer[k] != v for k, v in expected.items())
                            or not _finite(answer["valid_until"])
                            or type(answer["upper_bound_tokens"]) is not int
                            or not 1 <= answer["upper_bound_tokens"] <= self._manifest.max_input_tokens):
                        raise ValueError()
                    remaining()
                    bound = InputTokenBound(self.manifest_digest, self.engine_epoch, value.model_revision,
                                            value.upstream_body_sha256, answer["upper_bound_tokens"], float(deadline))
                    if not cancelled.is_set():
                        result.put_nowait((bound, None))
                except Exception as exc:
                    error = (BackendDeadline("input-authority deadline elapsed") if isinstance(exc, (BackendDeadline, TimeoutError))
                             else BackendUnknown("input-authority preflight rejected"))
                    if not cancelled.is_set():
                        result.put_nowait((None, error))
                finally:
                    try:
                        if connection is not None:
                            connection.close()
                    finally:
                        self._permit.release()

            worker = threading.Thread(target=run, daemon=True, name="shared-home-input-preflight")
            worker.start()
            started_worker = True
            try:
                bound, error = result.get(timeout=remaining())
            except queue.Empty:
                raise BackendDeadline("input-authority deadline elapsed") from None
            remaining()
            if error is not None:
                raise error
            return bound
        finally:
            cancelled.set()
            connection_socket = socket_ref[0]
            # Retain the socket even when getresponse transfers ownership to
            # its file wrapper. Do not clear connection.sock and enable retry.
            if connection_socket is not None:
                try:
                    connection_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection_socket.close()
                except OSError:
                    pass
            if not started_worker:
                self._permit.release()

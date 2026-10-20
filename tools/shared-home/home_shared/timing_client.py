"""Authenticated local timing-authority client, not a measurement authority.

Linux endpoint: /run/victoria-shared-home-timing/authority.sock. Each connection
exchanges one 4-byte network-order length followed by UTF-8 JSON (16 KiB max).
Request: version=1, authority_id, request_id, kind='clock'|'stats', and for
stats snapshot_digest. Response echoes those identities and supplies evidence
(the exact ClockEvidence/StatsTimingEvidence fields) or null. A stats response
must attest that exact canonical snapshot, never merely echo a policy bound.

The server remains a separate commissioning prerequisite. This module neither
issues receipts nor trusts receipt files. Peer credentials and protected socket
ancestors authenticate the installed authority; health.py verifies evidence
scope, timing and budgets. There is no dynamic module or endpoint discovery.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
import threading
import time
import uuid

from .health import ClockEvidence, HostClockEvidence, StatsTimingEvidence

SOCKET_PATH = Path("/run/victoria-shared-home-timing/authority.sock")
MAX_MESSAGE = 16 * 1024


@dataclass(frozen=True)
class TimingClientConfig:
    authority_id: str
    uid: int
    gid: int

    def __post_init__(self):
        if (not isinstance(self.authority_id, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.authority_id) is None
                or type(self.uid) is not int or self.uid < 0
                or type(self.gid) is not int or self.gid < 0):
            raise ValueError("explicit timing authority identity and Linux UID/GID required")


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate timing field")
        value[key] = item
    return value


def _constant(_):
    raise ValueError("nonfinite timing value")


class TimingClient:
    """Nonblocking cache providers backed by one bounded IPC worker.

    Call poll from the service tick; read/read_for perform no I/O. Only one
    latest stats digest is pending. Timed-out workers retain their permit and
    cannot publish late evidence. Missing authorities simply yield no evidence.
    Windows may instantiate this object for config inspection, but real IPC is
    Linux-only and fails closed elsewhere.
    """
    def __init__(self, config: TimingClientConfig, *, monotonic=time.monotonic):
        self.config = config
        self._mono = monotonic
        self._lock = threading.RLock()
        self._thread = self._socket = None
        self._clock = self._stats = self._pending = None
        self._active_digest = self._resolved_digest = None
        self._deadline = self._next_poll = float("-inf")
        self._cancelled = self._stopped = False
        self._invalidation_generation = 0
        self._receipt_validator = None
        self._poll_started = False
        self.last_error = "timing_authority_unavailable"

    def bind_receipt_validator(self, validator):
        """Bind the static health policy once, before any polling attempt.

        The callable is a pure, nonblocking policy check installed by the
        composition's HealthProbe. It cannot be replaced during recovery.
        """
        if not callable(validator):
            raise TypeError("receipt validation policy required")
        with self._lock:
            if self._receipt_validator is not None or self._poll_started or self._stopped:
                raise RuntimeError("timing receipt policy is already fixed or polling has begun")
            self._receipt_validator = validator

    def invalidation_generation(self):
        """Nonmutating revocation marker for this single authority/source cache.

        Every explicit denial or failed refresh revokes earlier health evidence.
        Merely queuing or waiting for a newer digest does not revoke it. A later
        successful response cannot undo a revocation of an earlier receipt.
        """
        with self._lock:
            return self._invalidation_generation

    def current_evidence(self):
        """Immutable receipt snapshot for semantic validation, without queuing.

        Scope/boot policy belongs to HealthProbe, not this transport. Inflight
        refreshes preserve the preceding receipts until replaced or revoked.
        """
        with self._lock:
            return self._clock, self._stats

    def read(self):
        with self._lock:
            return self._clock if not self._stopped else None

    def read_for(self, snapshot_digest):
        if not isinstance(snapshot_digest, str) or re.fullmatch(r"[a-f0-9]{64}", snapshot_digest) is None:
            raise ValueError("canonical stats digest required")
        with self._lock:
            if self._stopped:
                return None
            self._pending = snapshot_digest
            return self._stats if self._stats and self._stats.snapshot_digest == snapshot_digest else None

    def pending_for(self, snapshot_digest):
        """True only for an unresolved queued/inflight lookup, not a denial.

        This may bridge an older already-validated health cache through its
        original expiry; it never authorizes the new snapshot by itself.
        """
        with self._lock:
            return (not self._stopped and not self._cancelled
                    and snapshot_digest != self._resolved_digest
                    and snapshot_digest in (self._pending, self._active_digest))

    def _abort(self):
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass

    def poll(self):
        now = self._mono()
        with self._lock:
            self._poll_started = True
            if self._receipt_validator is None:
                raise RuntimeError("HealthProbe must bind receipt policy before timing polling")
            if self._thread and self._thread.is_alive():
                if now >= self._deadline:
                    if not self._cancelled:
                        self._invalidation_generation += 1
                    self._cancelled = True
                    self._clock = self._stats = None
                    self.last_error = "timing_authority_deadline"
                    self._abort()
                return False
            if self._stopped or now < self._next_poll:
                return False
            self._deadline, self._next_poll = now + 2, now + 1
            self._cancelled = False
            digest = self._pending
            self._pending = None
            self._active_digest = digest
            self._thread = threading.Thread(target=self._run, args=(digest, self._deadline),
                                             daemon=True, name="victoria-timing")
            try:
                self._thread.start()
            except Exception:
                self._invalidation_generation += 1
                self._thread = None
                self._clock = self._stats = None
                self._resolved_digest, self._active_digest = digest, None
                self.last_error = "timing_worker_unavailable"
                return False
            return True

    def stop(self):
        with self._lock:
            if not self._stopped:
                self._invalidation_generation += 1
            self._stopped = self._cancelled = True
            self._clock = self._stats = self._pending = None
            self._active_digest = None
            self._abort()

    def _remaining(self, deadline):
        remaining = deadline - self._mono()
        if remaining <= 0 or self._cancelled or self._stopped:
            raise TimeoutError
        return remaining

    def _socket_identity(self):
        if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
            raise RuntimeError("timing IPC requires Linux peer credentials")
        # No group/other-writable ancestor can swap in a different service.
        for directory in reversed(SOCKET_PATH.parents):
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, self.config.uid)
                    or info.st_mode & 0o022):
                raise ValueError("unprotected timing authority directory")
        info = SOCKET_PATH.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.config.uid
                or info.st_gid != self.config.gid or info.st_mode & 0o007):
            raise ValueError("unprotected timing authority socket")
        return info.st_dev, info.st_ino

    def _dial(self, deadline):
        identity = self._socket_identity()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with self._lock:
                self._socket = connection
            connection.settimeout(self._remaining(deadline))
            connection.connect(str(SOCKET_PATH))
            pid, uid, gid = struct.unpack("iII", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if (pid <= 0 or (uid, gid) != (self.config.uid, self.config.gid)
                    or self._socket_identity() != identity):
                raise ValueError("timing authority peer identity mismatch")
            return connection
        except Exception:
            connection.close()
            raise

    def _request(self, kind, digest, deadline):
        request = {"version": 1, "authority_id": self.config.authority_id,
                   "request_id": uuid.uuid4().hex, "kind": kind}
        if kind == "stats":
            request["snapshot_digest"] = digest
        data = json.dumps(request, separators=(",", ":")).encode()
        connection = None
        try:
            connection = self._dial(deadline)
            connection.settimeout(self._remaining(deadline))
            connection.sendall(struct.pack("!I", len(data)) + data)
            def receive(count):
                result = bytearray()
                while len(result) < count:
                    connection.settimeout(self._remaining(deadline))
                    chunk = connection.recv(count - len(result))
                    if not chunk:
                        raise ValueError("truncated timing response")
                    result.extend(chunk)
                return bytes(result)
            length = struct.unpack("!I", receive(4))[0]
            if not 1 <= length <= MAX_MESSAGE:
                raise ValueError("timing response size exceeds limit")
            value = json.loads(receive(length), object_pairs_hook=_object, parse_constant=_constant)
            self._remaining(deadline)
            if (not isinstance(value, dict) or set(value) != set(request) | {"evidence"}
                    or type(value.get("version")) is not int):
                raise ValueError("invalid timing response fields")
            if any(value[key] != expected for key, expected in request.items()):
                raise ValueError("timing response identity mismatch")
            evidence = value["evidence"]
            if evidence is None:
                return None
            cls = (HostClockEvidence if isinstance(evidence, dict) and evidence.get("version") == 2
                   else ClockEvidence) if kind == "clock" else StatsTimingEvidence
            if not isinstance(evidence, dict) or set(evidence) != {f.name for f in fields(cls)}:
                raise ValueError("invalid timing evidence schema")
            receipt = cls(**evidence)
            if kind == "stats" and receipt.snapshot_digest != digest:
                raise ValueError("timing evidence snapshot mismatch")
            return receipt
        finally:
            if connection:
                connection.close()
            with self._lock:
                self._socket = None

    def _run(self, digest, deadline):
        try:
            clock = self._request("clock", None, deadline)
            if clock is not None and self._receipt_validator("clock", clock, None) is not True:
                raise ValueError("clock receipt does not satisfy provisioned health policy")
            stats = self._request("stats", digest, deadline) if digest else None
            if stats is not None and self._receipt_validator("stats", stats, digest) is not True:
                raise ValueError("stats receipt does not satisfy provisioned health policy")
            with self._lock:
                self._remaining(deadline)
                # Stats IPC may consume the clock receipt's remaining validity.
                # Recheck immediately before publication; semantic failures must
                # enter revocation history even if no health read sees them.
                if clock is not None and self._receipt_validator("clock", clock, None) is not True:
                    raise ValueError("clock receipt expired before publication")
                if clock is None or (digest and stats is None):
                    self._invalidation_generation += 1
                elif self._clock is not None and clock.version != self._clock.version:
                    # A later return to the old schema must not resurrect
                    # health cached before an intervening valid transition.
                    self._invalidation_generation += 1
                self._clock = clock
                if digest:
                    self._stats = stats
                    self._resolved_digest = digest
                self.last_error = None if clock is not None else "timing_evidence_unavailable"
        except Exception:
            with self._lock:
                self._invalidation_generation += 1
                self._clock = self._stats = None
                if digest:
                    self._resolved_digest = digest
                if not self._cancelled:
                    self.last_error = "timing_authority_unavailable"
        finally:
            with self._lock:
                self._active_digest = None

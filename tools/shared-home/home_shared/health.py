"""Bounded camera/Frigate probes with independently commissioned clock evidence.

Frigate 0.18 /api/stats is a cached, roughly 15-second diagnostic snapshot.
The explicit stats-age policy is independent of the three-second image bound
and MQTT occupancy freshness; HTTP receipt never renews a cached measurement.
No clock provider, default uncertainty, login discovery or production endpoint
is supplied here. Installation requires a reviewed producer of ClockEvidence.
Stock stats alone cannot establish processing measurement age: last_updated is
stamped after potentially blocking telemetry. A StatsTimingEvidence provider
must attest the exact snapshot's collection lag, or its enforced producer bound.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import re
import socket
import ssl
import threading
import time
import uuid
from typing import Protocol

from .adapter import finite
from .runtime import HealthSample


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


@dataclass(frozen=True)
class ClockEvidence:
    """Trusted measurement receipt, not a caller-controlled healthy boolean.

    The provider must validate commissioned measurement provenance before
    returning this object. Units are seconds; monotonic times refer to the
    bound camera host's current boot. Uncertainty includes offset/error and
    drift through expiry, rather than an instantaneous offset estimate alone.
    """
    camera_host_identity: str
    camera_boot_id: str
    pi_host_identity: str
    pi_boot_id: str
    la_host_identity: str
    la_boot_id: str
    policy_revision: str
    validation_id: str
    checked_mono: float
    checked_wall: float
    valid_until_mono: float
    camera_source_uncertainty_seconds: float
    pi_uncertainty_seconds: float
    la_uncertainty_seconds: float
    version: int = 1


class ClockEvidenceProvider(Protocol):
    def read(self) -> ClockEvidence | HostClockEvidence | None:
        """Return validated evidence from a nonblocking trusted cache."""


@dataclass(frozen=True)
class HostClockEvidence:
    """Version 2: camera-host UTC error only, excluding device/frame timing.

    The exact-frame causal bracket separately accounts for device exposure,
    quantization and transport bracket. This receipt never attests those terms.
    All scope, expiry and drift requirements of version 1 remain mandatory.
    """
    camera_host_identity: str
    camera_boot_id: str
    pi_host_identity: str
    pi_boot_id: str
    la_host_identity: str
    la_boot_id: str
    policy_revision: str
    validation_id: str
    checked_mono: float
    checked_wall: float
    valid_until_mono: float
    camera_host_uncertainty_seconds: float
    pi_uncertainty_seconds: float
    la_uncertainty_seconds: float
    version: int = 2


def _camera_clock_error(evidence):
    return (evidence.camera_host_uncertainty_seconds if isinstance(evidence, HostClockEvidence)
            else evidence.camera_source_uncertainty_seconds)


def stats_snapshot_digest(snapshot):
    """Canonical parsed snapshot binding for the independent timing authority."""
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StatsTimingEvidence:
    source_instance: str
    source_version: str
    pi_host_identity: str
    pi_boot_id: str
    camera_host_identity: str
    camera_boot_id: str
    policy_revision: str
    snapshot_digest: str
    validation_id: str
    checked_mono: float
    valid_until_mono: float
    max_collection_lag_seconds: float
    version: int = 1


class StatsTimingEvidenceProvider(Protocol):
    def read_for(self, snapshot_digest: str) -> StatsTimingEvidence | None:
        """Read trusted, fresh timing evidence bound to this exact snapshot.

        A narrow instrumented relay or verified producer supervision must
        establish the bound. Echoing the configured number is not evidence.
        Times are in the bound Jetson boot's monotonic clock domain.
        """


@dataclass(frozen=True)
class HealthProbeConfig:
    frigate_hostname: str
    frigate_port: int
    ca_file: str | Path
    frigate_bearer: str = field(repr=False)
    camera_bearer: str = field(repr=False)
    source_instance: str
    detector_id: str
    max_stats_age_seconds: float
    max_stats_collection_lag_seconds: float
    policy_revision: str
    camera_host_identity: str
    camera_boot_id: str
    pi_host_identity: str
    pi_boot_id: str
    la_host_identity: str
    la_boot_id: str

    def __post_init__(self):
        if (not isinstance(self.frigate_hostname, str) or len(self.frigate_hostname) > 253
                or any(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) is None
                       for part in self.frigate_hostname.split("."))
                or type(self.frigate_port) is not int or not 1 <= self.frigate_port <= 65535):
            raise ValueError("fixed provisioned Frigate DNS hostname and port required")
        for token in (self.frigate_bearer, self.camera_bearer):
            if not isinstance(token, str) or not 1 <= len(token) <= 4096 or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token) is None:
                raise ValueError("dedicated bounded bearer credentials required")
        for identity in (self.source_instance, self.detector_id, self.policy_revision,
                         self.camera_host_identity, self.camera_boot_id, self.pi_host_identity,
                         self.pi_boot_id, self.la_host_identity, self.la_boot_id):
            if not isinstance(identity, str) or _ID.fullmatch(identity) is None:
                raise ValueError("explicit health/host policy identity required")
        if not finite(self.max_stats_age_seconds) or not 0 < self.max_stats_age_seconds <= 30:
            raise ValueError("explicit commissioned stats-age bound must be at most 30 seconds")
        if (not finite(self.max_stats_collection_lag_seconds)
                or not 0 <= self.max_stats_collection_lag_seconds < self.max_stats_age_seconds):
            raise ValueError("explicit commissioned stats collection-lag bound required")
        if not isinstance(self.ca_file, (str, Path)) or not str(self.ca_file):
            raise ValueError("explicit Frigate CA required")


@dataclass(frozen=True)
class _Cached:
    sample: HealthSample
    clock: ClockEvidence | HostClockEvidence
    timing: StatsTimingEvidence
    valid_until_mono: float
    stats_wall: float
    wall_reference: float
    mono_reference: float
    authority_generation: tuple


def _valid_clock_evidence(c, evidence, now):
    if (not isinstance(evidence, (ClockEvidence, HostClockEvidence)) or type(evidence.version) is not int
            or evidence.version != (2 if isinstance(evidence, HostClockEvidence) else 1)
            or (evidence.camera_host_identity, evidence.camera_boot_id, evidence.pi_host_identity,
                evidence.pi_boot_id, evidence.la_host_identity, evidence.la_boot_id, evidence.policy_revision) !=
            (c.camera_host_identity, c.camera_boot_id, c.pi_host_identity, c.pi_boot_id,
             c.la_host_identity, c.la_boot_id, c.policy_revision)
            or not isinstance(evidence.validation_id, str) or _ID.fullmatch(evidence.validation_id) is None):
        return False
    values = (evidence.checked_mono, evidence.valid_until_mono,
              _camera_clock_error(evidence), evidence.pi_uncertainty_seconds,
              evidence.la_uncertainty_seconds)
    return (all(finite(v) for v in values) and finite(evidence.checked_wall)
            and 0 <= now - evidence.checked_mono <= 5
            and now < evidence.valid_until_mono
            and all(0 <= v <= 1 for v in values[2:])
            and _camera_clock_error(evidence) + evidence.pi_uncertainty_seconds <= 1
            and _camera_clock_error(evidence) + evidence.la_uncertainty_seconds <= 1)


def _valid_stats_timing(c, timing, digest, version, now):
    return (isinstance(timing, StatsTimingEvidence) and type(timing.version) is int and timing.version == 1
            and isinstance(version, str) and re.fullmatch(r"0\.18(?:\.0)?(?:-[A-Za-z0-9._]+)?", version) is not None
            and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None
            and (timing.source_instance, timing.pi_host_identity, timing.pi_boot_id,
                 timing.camera_host_identity, timing.camera_boot_id, timing.policy_revision,
                 timing.snapshot_digest, timing.source_version) ==
            (c.source_instance, c.pi_host_identity, c.pi_boot_id,
             c.camera_host_identity, c.camera_boot_id, c.policy_revision, digest, version)
            and isinstance(timing.validation_id, str) and _ID.fullmatch(timing.validation_id) is not None
            and all(finite(v) for v in (timing.checked_mono, timing.valid_until_mono,
                                         timing.max_collection_lag_seconds))
            and 0 <= now - timing.checked_mono <= 5
            and timing.checked_mono < timing.valid_until_mono and now < timing.valid_until_mono
            and 0 <= timing.max_collection_lag_seconds <= c.max_stats_collection_lag_seconds)


def _receipt_validator(config, monotonic, clock):
    # Capture only frozen provisioned policy and clock readers. No HealthProbe
    # state, locks or network operations are used by the authority worker.
    def validate(kind, receipt, digest):
        now = monotonic()
        if kind == "clock":
            wall = clock()
            return (_valid_clock_evidence(config, receipt, now) and finite(wall)
                    and abs(wall - (receipt.checked_wall + now - receipt.checked_mono)) <=
                    _camera_clock_error(receipt))
        return (kind == "stats" and isinstance(receipt, StatsTimingEvidence)
                and _valid_stats_timing(config, receipt, digest, receipt.source_version, now))
    return validate


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON fields")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("nonfinite JSON constant")


class HealthProbe:
    """Call poll each runtime tick, and use read as its health_reader.

    Poll schedules at most one worker and attempts once per second, allowing
    camera evidence to remain within three seconds. Read never performs I/O.
    The five-second worker/receipt bound is an additional limit, not permission
    to reuse camera/stats/clock evidence past their individual expiry.
    """
    def __init__(self, config: HealthProbeConfig, *, clock_provider: ClockEvidenceProvider,
                 stats_timing_provider: StatsTimingEvidenceProvider,
                 clock=time.time, monotonic=time.monotonic, _connect=socket.create_connection):
        if not isinstance(config, HealthProbeConfig) or not callable(getattr(clock_provider, "read", None)):
            raise TypeError("provisioned health config and trusted clock provider required")
        if not callable(getattr(stats_timing_provider, "read_for", None)):
            raise TypeError("trusted stats timing provider required")
        self.config, self._provider = config, clock_provider
        self._stats_provider = stats_timing_provider
        self._clock, self._mono, self._connect = clock, monotonic, _connect
        try:
            with open(config.ca_file, "rb") as source:
                ca = source.read(1024 * 1024 + 1)
            if not ca or len(ca) > 1024 * 1024:
                raise ValueError("CA bounds")
            self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
            self._tls.hostname_checks_common_name = False
            self._tls.load_verify_locations(cadata=ca.decode("ascii"))
        except Exception:
            raise ValueError("Frigate trust configuration unavailable") from None
        self._lock = threading.RLock()
        self._worker = self._socket = self._cache = None
        self._deadline = float("-inf")
        self._next_poll = float("-inf")
        self._generation = 0
        self._observer_session = uuid.uuid4().hex
        self._continuity = None
        self._stopped = False
        self._invalidated_job = False
        self.last_error = "no_health_evidence"
        validator = _receipt_validator(config, monotonic, clock)
        bound = set()
        for provider in (clock_provider, stats_timing_provider):
            binder = getattr(provider, "bind_receipt_validator", None)
            if callable(binder) and id(provider) not in bound:
                binder(validator)
                bound.add(id(provider))

    def _valid_clock(self, evidence, now):
        return _valid_clock_evidence(self.config, evidence, now)

    def _valid_timing(self, timing, digest, version, now):
        return _valid_stats_timing(self.config, timing, digest, version, now)

    def _invalidate(self, reason):
        self._generation += 1
        self._cache = self._continuity = None
        self.last_error = reason

    def _authority_generation(self):
        # Optional for synchronous providers. The production IPC cache exposes
        # a monotonic marker so denial/failure revokes captured receipts without
        # read_for() accidentally replacing the latest queued snapshot lookup.
        generations = []
        for provider in (self._provider, self._stats_provider):
            reader = getattr(provider, "invalidation_generation", None)
            generation = reader() if callable(reader) else None
            if generation is not None and (type(generation) is not int or generation < 0):
                raise ValueError("invalid timing authority generation")
            generations.append(generation)
        return tuple(generations)

    def _authority_receipts_valid(self, cached, now, wall):
        # A transport can reject framing but cannot validate provisioned boot
        # and timing policy. Inspect its latest immutable receipts without
        # read_for(), which would overwrite a newer pending digest. These are
        # additional invalidation checks, never replacements for cached expiry.
        clock_reader = getattr(self._provider, "current_evidence", None)
        if callable(clock_reader):
            clock = clock_reader()[0]
            if (not self._valid_clock(clock, now)
                    or clock.version != cached.clock.version
                    or _camera_clock_error(clock) > _camera_clock_error(cached.clock)
                    or clock.la_uncertainty_seconds > cached.clock.la_uncertainty_seconds
                    or clock.pi_uncertainty_seconds > cached.clock.pi_uncertainty_seconds
                    or abs(wall - (clock.checked_wall + now - clock.checked_mono)) >
                    _camera_clock_error(clock)):
                return False
        stats_reader = getattr(self._stats_provider, "current_evidence", None)
        if callable(stats_reader):
            timing = stats_reader()[1]
            if (not isinstance(timing, StatsTimingEvidence)
                    or not isinstance(timing.snapshot_digest, str)
                    or re.fullmatch(r"[a-f0-9]{64}", timing.snapshot_digest) is None
                    or not self._valid_timing(timing, timing.snapshot_digest,
                                              cached.timing.source_version, now)):
                return False
        return True

    def _abort(self):
        stream = self._socket
        if stream:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                stream.close()
            except OSError:
                pass

    def _check_deadline(self, now):
        if (self._worker is not None and self._worker.is_alive()
                and now >= self._deadline and not self._invalidated_job):
            self._invalidated_job = True
            self._invalidate("health_probe_deadline")
            self._abort()

    def poll(self):
        now = self._mono()
        with self._lock:
            if not finite(now):
                self._invalidate("invalid_local_clock")
                return False
            self._check_deadline(now)
            if self._stopped or (self._worker and self._worker.is_alive()) or now < self._next_poll:
                return False
            self._deadline, self._next_poll = now + 5, now + 1
            self._invalidated_job = False
            self._worker = threading.Thread(target=self._run, args=(now, self._deadline), daemon=True,
                                            name="victoria-health")
            try:
                self._worker.start()
            except Exception:
                self._worker = None
                self._invalidate("health_worker_unavailable")
                return False
            return True

    def read(self):
        now, wall = self._mono(), self._clock()
        with self._lock:
            self._check_deadline(now)
            cached = self._cache
            if self._stopped or cached is None:
                return None
            try:
                authority_current = (self._authority_generation() == cached.authority_generation
                                     and self._authority_receipts_valid(cached, now, wall)
                                     and self._authority_generation() == cached.authority_generation)
            except Exception:
                authority_current = False
            clock = cached.clock
            # Recheck the exact receipt used by this sample, never replace its
            # expiry with a newer clock receipt without another probe.
            if (not authority_current or not self._valid_clock(clock, now) or not finite(wall)
                    or not self._valid_timing(cached.timing, cached.timing.snapshot_digest,
                                              cached.timing.source_version, now)
                    or not cached.sample.checked_mono <= now < cached.valid_until_mono
                    or abs(wall - (cached.wall_reference + now - cached.mono_reference)) >
                    min(clock.pi_uncertainty_seconds, clock.la_uncertainty_seconds)
                    or abs(wall - (clock.checked_wall + now - clock.checked_mono)) >
                    _camera_clock_error(clock)
                    or wall - cached.stats_wall + clock.pi_uncertainty_seconds > self.config.max_stats_age_seconds):
                self._invalidate("health_evidence_expired_or_clock_changed")
                return None
            return cached.sample

    def stop(self):
        with self._lock:
            self._stopped = True
            self._invalidated_job = True
            self._invalidate("health_probe_stopped")
            self._abort()

    def _remaining(self, deadline):
        remaining = deadline - self._mono()
        if remaining <= 0 or self._stopped:
            raise TimeoutError
        return remaining

    def _get(self, camera, deadline):
        c = self.config
        host, port, path, token = (("127.0.0.1", 8090, "/health", c.camera_bearer) if camera else
                                   (c.frigate_hostname, c.frigate_port, "/api/stats", c.frigate_bearer))
        raw = stream = None
        try:
            raw = self._connect((host, port), timeout=self._remaining(deadline))
            with self._lock:
                self._remaining(deadline)
                self._socket = raw
            raw.settimeout(self._remaining(deadline))
            stream = raw if camera else self._tls.wrap_socket(raw, server_hostname=host)
            with self._lock:
                self._remaining(deadline)
                self._socket = stream
            request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nAuthorization: Bearer {token}\r\n"
                       "Accept: application/json\r\nAccept-Encoding: identity\r\nX-Cache-Bypass: 1\r\n"
                       "Connection: close\r\n\r\n").encode("ascii")
            stream.settimeout(self._remaining(deadline))
            stream.sendall(request)
            data = bytearray()
            while b"\r\n\r\n" not in data:
                stream.settimeout(self._remaining(deadline))
                chunk = stream.recv(1024)
                if not chunk:
                    raise ValueError("truncated response")
                data.extend(chunk)
                if len(data) > 16 * 1024:
                    raise ValueError("oversized headers")
            head, body = bytes(data).split(b"\r\n\r\n", 1)
            lines = head.decode("ascii").split("\r\n")
            if not re.fullmatch(r"HTTP/1\.[01] 200(?: [^\r\n]*)?", lines[0]):
                raise ValueError("health endpoint unavailable")
            headers = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                name, value = name.lower(), value.strip()
                if name in headers or re.fullmatch(r"[a-z0-9-]+", name) is None:
                    raise ValueError("duplicate or invalid header")
                headers[name] = value
            if (headers.get("content-type", "").split(";", 1)[0] != "application/json"
                    or headers.get("content-encoding", "identity") != "identity"
                    or "transfer-encoding" in headers
                    or re.fullmatch(r"[0-9]{1,6}", headers.get("content-length", "")) is None):
                raise ValueError("unsupported health response")
            length = int(headers["content-length"])
            if length > 128 * 1024 or len(body) > length:
                raise ValueError("oversized health response")
            while len(body) < length:
                stream.settimeout(self._remaining(deadline))
                chunk = stream.recv(min(8192, length - len(body)))
                if not chunk:
                    raise ValueError("truncated health body")
                body += chunk
            result = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            self._remaining(deadline)
            if not isinstance(result, dict):
                raise ValueError("health response must be object")
            return result
        finally:
            if stream:
                stream.close()
            if raw and stream is not raw:
                raw.close()
            with self._lock:
                self._socket = None

    def _run(self, started, deadline):
        try:
            authority_generation = self._authority_generation()
            evidence = self._provider.read()
            if not self._valid_clock(evidence, self._mono()):
                raise ValueError("clock_evidence_unavailable")
            camera = self._get(True, deadline)
            stats = self._get(False, deadline)
            now, wall = self._mono(), self._clock()
            # Fresh negative/changed camera or processing evidence invalidates
            # immediately, even while the new snapshot's timing is pending.
            sample, expires, continuity, stats_wall = self._validate(camera, stats, evidence, started,
                                                                    now, wall)
            digest = stats_snapshot_digest(stats)
            timing = self._stats_provider.read_for(digest)
            now, wall = self._mono(), self._clock()
            pending_reader = getattr(self._stats_provider, "pending_for", None)
            pending = timing is None and callable(pending_reader) and pending_reader(digest) is True
            with self._lock:
                if self._stopped or self._invalidated_job or now >= deadline:
                    return
                if self._authority_generation() != authority_generation:
                    raise ValueError("timing authority invalidated during probe")
                previous_cache = self._cache
                if previous_cache and abs(wall - (previous_cache.wall_reference + now - previous_cache.mono_reference)) > min(
                        evidence.pi_uncertainty_seconds, evidence.la_uncertainty_seconds):
                    raise ValueError("host wall clock changed")
                previous = self._continuity
                changed = previous and (continuity[:2] != previous[:2] or continuity[2] < previous[2]
                                        or continuity[3] < previous[3])
                if pending:
                    if not previous or changed:
                        self._invalidate("stats_timing_pending_without_continuous_evidence")
                    elif self.read() is not None:
                        # Do not replace sample, clock receipt, checked time,
                        # source timestamp or any original cache expiry.
                        self.last_error = "stats_timing_pending"
                    return
                if not self._valid_timing(timing, digest, stats.get("service", {}).get("version"), now):
                    raise ValueError("stats_timing_evidence_unavailable")
                expires = min(expires, timing.valid_until_mono, timing.checked_mono + 5)
                # Session/PID changes and backward source time/uptime create a
                # new observer continuity token, never a fabricated producer epoch.
                if changed:
                    self._generation += 1
                self._continuity = continuity
                identity = hashlib.sha256(
                    f"{self.config.source_instance}:{self._observer_session}:{self._generation}".encode()).hexdigest()
                sample = replace(sample, frigate_instance="probe:" + identity)
                self._cache = _Cached(sample, evidence, timing, expires, stats_wall, wall, now,
                                      authority_generation)
                self.last_error = None
        except Exception:
            with self._lock:
                if not self._invalidated_job and not self._stopped:
                    self._invalidate("health_probe_failed")

    def _validate(self, camera, stats, evidence, started, now, wall):
        generation = camera.get("timing_generation", 0)
        if type(generation) is not int or not 0 <= generation <= 2**63 - 1:
            raise ValueError("invalid camera timing generation")
        if (not self._valid_clock(evidence, now) or not finite(wall)
                or abs(wall - (evidence.checked_wall + now - evidence.checked_mono)) >
                _camera_clock_error(evidence)
                or camera.get("healthy") is not True or camera.get("error") is not None
                or not isinstance(camera.get("session"), str) or _ID.fullmatch(camera["session"]) is None):
            raise ValueError("invalid camera or clock evidence")
        ages = (camera.get("bundle_age_seconds"), camera.get("encoder_age_seconds"))
        # Bound producer age plus the entire camera-request interval, rather
        # than pretending the HTTP response was measured at receipt.
        if any(not finite(age) or age < 0 or age + now - started > 3 for age in ages):
            raise ValueError("stale camera evidence")
        service = stats["service"]
        observed, uptime = service["last_updated"], service["uptime"]
        if (type(observed) is not int or observed <= 0 or type(uptime) is not int or uptime < 0
                or not isinstance(service.get("version"), str)
                or re.fullmatch(r"0\.18(?:\.0)?(?:-[A-Za-z0-9._]+)?", service["version"]) is None
                or observed - wall > evidence.pi_uncertainty_seconds):
            raise ValueError("invalid Frigate source timing or version")
        # Integer source timestamps add one second of conservative quantization.
        age = (max(0, wall - observed) + evidence.pi_uncertainty_seconds + 1
               + self.config.max_stats_collection_lag_seconds)
        if age >= self.config.max_stats_age_seconds:
            raise ValueError("stale Frigate measurement")
        cam = stats["cameras"]["den_oak"]
        detector = stats["detectors"][self.config.detector_id]
        if (cam.get("detection_enabled") is not True
                or any(not finite(cam.get(key)) or cam[key] <= 0 for key in ("camera_fps", "process_fps"))
                or not finite(cam.get("skipped_fps")) or cam["skipped_fps"] < 0
                or not finite(detector.get("inference_speed")) or detector["inference_speed"] <= 0
                or not finite(detector.get("detection_start")) or detector["detection_start"] < 0):
            raise ValueError("Frigate processing is not established")
        # This diagnostic belongs to the snapshot time, not the later HTTP
        # receipt. A nonzero value can mean normal inference was in progress.
        if detector["detection_start"] and (detector["detection_start"] > observed + 1 + evidence.pi_uncertainty_seconds
                                             or observed - 1 - self.config.max_stats_collection_lag_seconds
                                             - detector["detection_start"] > 3):
            raise ValueError("detector work timing unavailable")
        pids = tuple(cam.get(key) for key in ("pid", "capture_pid", "ffmpeg_pid")) + (detector.get("pid"),)
        if any(type(pid) is not int or pid <= 0 for pid in pids):
            raise ValueError("missing continuity identities")
        expires = min(started + 5, started + 3 - max(ages), evidence.valid_until_mono,
                      evidence.checked_mono + 5, now + self.config.max_stats_age_seconds - age)
        sample = HealthSample(camera["session"], "pending", started, True,
                              evidence.la_uncertainty_seconds, evidence.valid_until_mono, generation,
                              evidence.version, evidence.camera_boot_id, evidence.validation_id,
                              _camera_clock_error(evidence) if evidence.version == 2 else None)
        return sample, expires, ((camera["session"], generation, evidence.version, evidence.camera_boot_id),
                                pids, observed, uptime), observed

"""One read-only NTP probe for commissioned, short-lived camera-host evidence.

RFC 5905 sections 6--8 define wire time, header fields and four-time offset:
https://www.rfc-editor.org/rfc/rfc5905.html#section-8
An ephemeral client port follows RFC 9109. No clock is adjusted, no daemon is
configured, and no retry or polling schedule is supplied. A fresh UDP socket
and random 64-bit echoed origin bind one response to one request; neither is
cryptographic server authentication. A provisioned server's honesty, UTC
accuracy, and network trust MUST be commissioned explicitly. This narrow
pilot accepts unextended NTPv4, LI=0 only; leap announcements fail closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
import math
from pathlib import Path
import re
import secrets
import socket
import struct
import sys
import threading
import time
import uuid


NTP_PORT = 123
NTP_EPOCH_SECONDS = 2_208_988_800
NTP_ERA_SECONDS = 2**32
TRUST_ASSUMPTION = "commissioned_unauthenticated_ntp"


def _number(value, lower, upper, *, positive=False):
    return (type(value) in (float, int) and abs(value) <= upper and math.isfinite(value)
            and (lower < value if positive else lower <= value) and value <= upper)


@dataclass(frozen=True)
class NtpClockConfig:
    # All measurements/assumptions are required; there are no accuracy defaults.
    server_ip: str
    commissioning_id: str
    server_trust: str
    server_accuracy_seconds: float
    max_reference_age_seconds: float
    local_clock_quantization_ns: int
    drift_slew_ppm: float
    timeout_seconds: float
    valid_for_seconds: float
    max_uncertainty_seconds: float

    def __post_init__(self):
        address = ipaddress.ip_address(self.server_ip)
        if (str(address) != self.server_ip or address.is_unspecified or address.is_multicast
                or "%" in self.server_ip or getattr(address, "ipv4_mapped", None) is not None
                or not isinstance(self.commissioning_id, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.commissioning_id) is None
                or self.server_trust != TRUST_ASSUMPTION
                or not _number(self.server_accuracy_seconds, 0, 1)
                or not _number(self.max_reference_age_seconds, 0, 2**31 - 1, positive=True)
                or type(self.local_clock_quantization_ns) is not int
                or not 1 <= self.local_clock_quantization_ns <= 1_000_000_000
                or not _number(self.drift_slew_ppm, 0, 1_000_000)
                or not _number(self.timeout_seconds, 0, 5, positive=True)
                or not _number(self.valid_for_seconds, 0, 5, positive=True)
                or not _number(self.max_uncertainty_seconds, 0, 1, positive=True)):
            raise ValueError("explicit commissioned NTP trust and finite bounds required")
        if min(int(self.timeout_seconds * 1e9), int(self.valid_for_seconds * 1e9)) < 1:
            raise ValueError("probe deadline and receipt validity must span a nanosecond")


@dataclass(frozen=True)
class CameraHostClockEvidence:
    host_boot_id: str
    validation_id: str
    checked_monotonic_ns: int
    checked_unix_ns: int
    valid_until_monotonic_ns: int
    uncertainty_seconds: float
    version: int = 1
    semantics: str = "commissioned_camera_host_clock_v1"

    def to_dict(self):
        """A fresh JSON-ready copy; the sample itself remains immutable."""
        return asdict(self)


@dataclass(frozen=True)
class NtpClockSample:
    evidence: CameraHostClockEvidence
    server_ip: str
    commissioning_id: str
    server_trust: str
    stratum: int
    poll_exponent: int
    round_trip_seconds: float
    offset_seconds: float
    root_delay_seconds: float
    root_dispersion_seconds: float
    precision_seconds: float
    reference_age_seconds: float
    read_bracket_seconds: float
    quantization_seconds: float
    server_accuracy_seconds: float
    drift_slew_allowance_seconds: float

    @property
    def cryptographically_authenticated(self):
        return False


class NtpKissOfDeath(ValueError):
    """Do not retry automatically; the configured server has refused service."""
    def __init__(self, code):
        self.code = code
        super().__init__(f"NTP server refused service ({code})")


@dataclass(frozen=True)
class _ClockPoint:
    before_ns: int
    boot_ns: int
    unix_ns: int
    after_ns: int


def _clock_point():
    if sys.platform != "linux" or not hasattr(time, "CLOCK_BOOTTIME"):
        raise RuntimeError("production NTP evidence requires Linux CLOCK_BOOTTIME")
    return _ClockPoint(time.monotonic_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                       time.time_ns(), time.monotonic_ns())


def _kernel_boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if str(uuid.UUID(value)) != value:
        raise ValueError("current kernel boot UUID required")
    return value


def _validate_points(points, config):
    q = config.local_clock_quantization_ns
    for point in points:
        if (not isinstance(point, _ClockPoint)
                or any(type(v) is not int or not 0 <= v <= 2**63 - 1 for v in asdict(point).values())
                or point.unix_ns == 0 or point.after_ns < point.before_ns):
            raise ValueError("invalid clock read bracket")
    # BOOTTIME and MONOTONIC share rate adjustments; their offset must overlap.
    if (max(p.boot_ns - p.after_ns - q for p in points)
            > min(p.boot_ns - p.before_ns + q for p in points)):
        raise ValueError("host suspended during probe")
    for earlier, later in zip(points, points[1:]):
        if later.before_ns < earlier.after_ns or later.boot_ns < earlier.boot_ns:
            raise ValueError("host monotonic clock regressed")
        elapsed = later.after_ns - earlier.before_ns
        allowance = math.ceil(elapsed * config.drift_slew_ppm / 1e6) + 2 * q
        wall_delta = later.unix_ns - earlier.unix_ns
        if not (later.before_ns - earlier.after_ns - allowance <= wall_delta
                <= later.after_ns - earlier.before_ns + allowance):
            raise ValueError("host wall clock stepped beyond commissioned rate/quantization")


def _ntp_unix_ns(raw, pivot_unix_ns):
    """Unfold adjacent eras using integer differences, including the 2036 wrap.

    As in RFC 5905 section 6, the local date must be within half an era of
    the server. Exactly ambiguous dates and NTP's all-zero unknown marker fail.
    """
    if len(raw) != 8 or raw == bytes(8):
        raise ValueError("missing NTP timestamp")
    seconds, fraction = struct.unpack("!II", raw)
    pivot = pivot_unix_ns // 1_000_000_000 + NTP_EPOCH_SECONDS
    era = pivot // NTP_ERA_SECONDS
    candidates = [((number * NTP_ERA_SECONDS + seconds - NTP_EPOCH_SECONDS) * 1_000_000_000
                   + (fraction * 1_000_000_000) // 2**32) for number in (era - 1, era, era + 1)]
    result = min(candidates, key=lambda value: abs(value - pivot_unix_ns))
    if abs(result - pivot_unix_ns) >= (NTP_ERA_SECONDS // 2) * 1_000_000_000:
        raise ValueError("ambiguous NTP era")
    return result


class NtpClockSampler:
    """Synchronous one-shot operation; a concurrent caller fails immediately.

    Private clock seams are for deterministic loopback tests. Production uses
    the kernel's boot identity and real read brackets. There is no evidence
    cache: a later call must send its own fresh nonce and obtains its own expiry.
    """
    def __init__(self, config, *, _point=_clock_point, _boot_id=_kernel_boot_id):
        if not isinstance(config, NtpClockConfig):
            raise TypeError("NtpClockConfig required")
        self.config = config
        self._point, self._boot_id = _point, _boot_id
        self._permit = threading.Lock()

    def sample(self):
        if not self._permit.acquire(False):
            raise RuntimeError("NTP probe already outstanding")
        try:
            return self._sample()
        finally:
            self._permit.release()

    def _sample(self):
        config = self.config
        boot_id = self._boot_id()
        if str(uuid.UUID(boot_id)) != boot_id:
            raise ValueError("current boot UUID required")
        started = self._point()
        points = [started]
        _validate_points(points, config)
        timeout_ns = int(config.timeout_seconds * 1e9)
        mono_deadline = started.before_ns + timeout_ns
        # Lower bound for BOOTTIME at the beginning of the starting bracket.
        boot_deadline = started.boot_ns - (started.after_ns - started.before_ns) + timeout_ns

        def remaining(point):
            _validate_points(points, config)
            value = min(mono_deadline - point.after_ns, boot_deadline - point.boot_ns) / 1e9
            if value <= 0:
                raise TimeoutError("absolute NTP probe deadline elapsed")
            return value

        family = socket.AF_INET6 if ipaddress.ip_address(config.server_ip).version == 6 else socket.AF_INET
        peer = (config.server_ip, NTP_PORT)
        with socket.socket(family, socket.SOCK_DGRAM) as connection:
            connection.settimeout(remaining(started))
            # Numeric literal only. Connect constrains the kernel receive peer;
            # recvfrom is still checked explicitly. OS chooses an ephemeral port.
            connection.connect(peer)
            nonce = secrets.token_bytes(8)
            if nonce == bytes(8):
                raise ValueError("zero NTP request nonce")
            request = bytearray(48)
            request[0], request[2] = (4 << 3) | 3, 6
            # The server echoes this opaque 64-bit value. Actual T1 is retained
            # separately below, never reconstructed from the randomized nonce.
            request[40:48] = nonce
            sent = self._point()
            points.append(sent)
            connection.settimeout(remaining(sent))
            if connection.send(request) != len(request):
                raise OSError("short UDP NTP send")
            waiting = self._point()
            points.append(waiting)
            connection.settimeout(remaining(waiting))
            packet, source = connection.recvfrom(49)
            received = self._point()
            points.append(received)
            remaining(received)
        if ipaddress.ip_address(source[0]) != ipaddress.ip_address(config.server_ip) or source[1] != NTP_PORT:
            raise ValueError("NTP response peer mismatch")
        if len(packet) != 48 or packet[24:32] != nonce:
            raise ValueError("NTP length/origin mismatch")
        leap, version, mode = packet[0] >> 6, (packet[0] >> 3) & 7, packet[0] & 7
        if version != 4 or mode != 4:
            raise ValueError("NTPv4 server response required")
        stratum = packet[1]
        if stratum == 0:
            code = packet[12:16].decode("ascii", errors="replace")
            raise NtpKissOfDeath(code)
        if leap != 0 or not 1 <= stratum <= 15:
            raise ValueError("NTP source unsynchronized or leap boundary unsupported")
        poll, precision_exp = struct.unpack("!bb", packet[2:4])
        root_delay = struct.unpack("!i", packet[4:8])[0] / 65536
        root_dispersion = struct.unpack("!I", packet[8:12])[0] / 65536
        precision = 2.0**precision_exp
        reference = _ntp_unix_ns(packet[16:24], received.unix_ns)
        t2 = _ntp_unix_ns(packet[32:40], received.unix_ns)
        t3 = _ntp_unix_ns(packet[40:48], received.unix_ns)
        reference_age = (t3 - reference) / 1e9
        rtt = (received.after_ns - sent.before_ns) / 1e9
        if t3 < t2 or not 0 <= reference_age <= config.max_reference_age_seconds:
            raise ValueError("incoherent or stale server timestamps")
        # The server cannot spend longer processing than the entire exchange,
        # allowing only stated timestamp precision and commissioned rate error.
        if (t3 - t2) / 1e9 > rtt + 2 * precision + rtt * config.drift_slew_ppm / 1e6:
            raise ValueError("server interval exceeds observed exchange")
        offset = ((t2 - sent.unix_ns) + (t3 - received.unix_ns)) / 2e9
        if self._boot_id() != boot_id:
            raise ValueError("host boot identity changed")
        finished = self._point()
        points.append(finished)
        remaining(finished)
        expires = received.before_ns + int(config.valid_for_seconds * 1e9)
        if finished.after_ns >= expires:
            raise TimeoutError("NTP receipt expired before completion")
        brackets = sum(p.after_ns - p.before_ns for p in points) / 1e9
        # Charge packet 16.16 root-distance truncation, integer timestamp
        # conversion, both server timestamps and both commissioned local reads.
        quantization = 1.5 / 65536 + 4e-9 + 2 * precision + 2 * config.local_clock_quantization_ns / 1e9
        drift = (expires - sent.before_ns) / 1e9 * config.drift_slew_ppm / 1e6
        uncertainty = math.fsum((rtt, abs(offset), max(root_delay, 0) / 2, root_dispersion,
                                 config.server_accuracy_seconds, brackets, quantization, drift))
        # Round upward rather than silently losing a positive bound term.
        uncertainty = math.nextafter(uncertainty, math.inf)
        if not 0 < uncertainty <= config.max_uncertainty_seconds:
            raise ValueError("NTP-derived host UTC bound exceeds commissioned budget")
        evidence = CameraHostClockEvidence(boot_id, "ntp:" + secrets.token_hex(16), received.before_ns,
                                          received.unix_ns, expires, uncertainty)
        return NtpClockSample(evidence, config.server_ip, config.commissioning_id, config.server_trust,
                              stratum, poll, rtt, offset, root_delay, root_dispersion, precision,
                              reference_age, brackets, quantization, config.server_accuracy_seconds, drift)

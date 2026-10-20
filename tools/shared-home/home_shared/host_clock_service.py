"""Explicit one-shot camera host-clock publisher; never adjusts the OS clock.

Run with --config <protected JSON> --once. No daemon, retry, startup enablement,
or automatic NTP polling is installed. The configured reference polling floor
is persisted before sampling and survives invocation failure. Camera receipts
expire at the sampler's original deadline; writing never renews their times.
Kiss-of-death refusals (and crashes with an outstanding probe) leave the reference
blocked across invocations until an operator reviews and resolves the protected
polling state. There is no automatic reset or command-line bypass.

Provisioning JSON has exactly: version=1, expected_hostname, uid, output_path
(an absolute host-clock.json in an existing protected directory),
minimum_reference_interval_seconds and ntp (the exact NtpClockConfig fields).
The explicit unauthenticated-NTP trust decision and measured allowances belong
to commissioning. This producer does not create Pi/LA or Frigate timing evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import threading
import time
import uuid

from .ntp_clock import NtpClockConfig, NtpClockSampler, NtpKissOfDeath

MAX_JSON = 8192
_BOOT = Path('/proc/sys/kernel/random/boot_id')


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate clock provisioning field')
        result[key] = value
    return result


def _json(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_JSON:
        raise ValueError('clock input exceeds bound')
    def reject(_):
        raise ValueError('nonfinite clock input')
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject)


@dataclass(frozen=True)
class HostClockConfig:
    expected_hostname: str
    uid: int
    output_path: str
    minimum_reference_interval_seconds: float
    ntp: NtpClockConfig

    def __post_init__(self):
        if (not isinstance(self.expected_hostname, str)
                or re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', self.expected_hostname) is None
                or self.expected_hostname in ('home-app', 'engineeredlightingserver1')
                or type(self.uid) is not int or self.uid < 0
                or not isinstance(self.output_path, str) or not self.output_path.startswith('/')
                or '\\' in self.output_path or '..' in self.output_path.split('/')
                or self.output_path.split('/')[-1] != 'host-clock.json'
                or not _finite(self.minimum_reference_interval_seconds)
                or not 1 <= self.minimum_reference_interval_seconds <= 86400
                or not isinstance(self.ntp, NtpClockConfig)):
            raise ValueError('explicit protected clock service provisioning required')


def parse_config(raw):
    value = _json(raw)
    if (not isinstance(value, dict) or set(value) != {'version', 'expected_hostname', 'uid', 'output_path',
            'minimum_reference_interval_seconds', 'ntp'} or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['ntp'], dict) or set(value['ntp']) != {f.name for f in fields(NtpClockConfig)}):
        raise ValueError('clock provisioning must exactly match version 1')
    return HostClockConfig(value['expected_hostname'], value['uid'], value['output_path'],
                           value['minimum_reference_interval_seconds'], NtpClockConfig(**value['ntp']))


def _require_linux():
    if sys.platform != 'linux' or not hasattr(os, 'O_NOFOLLOW'):
        raise ValueError('protected clock publication requires Linux')


def _parent(path, uid):
    _require_linux()
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute protected clock path required')
    for directory in reversed(path.parents):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022):
            raise ValueError('unprotected clock directory')
    return path


def _regular(info, uid, *, private=False):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError('unprotected clock artifact')


def load_config(path):
    _require_linux()
    uid = os.geteuid()
    path = _parent(path, uid)
    before = path.lstat()
    # Root-installed configuration is permitted; receipt ownership is explicit.
    if before.st_uid not in (0, uid):
        raise ValueError('clock configuration owner mismatch')
    _regular(before, before.st_uid)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        actual = os.fstat(source.fileno())
        if (actual.st_dev, actual.st_ino, actual.st_mode, actual.st_uid, actual.st_nlink) != (
                before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_nlink):
            raise ValueError('clock configuration changed while opening')
        return parse_config(source.read(MAX_JSON + 1))


@dataclass(frozen=True)
class _Point:
    before: int
    boot: int
    wall: int
    after: int


def _point():
    value = _Point(time.monotonic_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                   time.time_ns(), time.monotonic_ns())
    if (value.wall <= 0 or min(value.before, value.boot) < 0
            or not 0 <= value.after - value.before <= 10_000_000):
        raise ValueError('delayed host clock bracket')
    return value


def _boot_id():
    value = _BOOT.read_text().strip()
    if str(uuid.UUID(value)) != value:
        raise ValueError('kernel boot UUID unavailable')
    return value


def _continuous(first, last, config):
    if (last.before < first.after or last.boot < first.boot
            or max(first.boot - first.after, last.boot - last.after)
            > min(first.boot - first.before, last.boot - last.before)):
        raise ValueError('suspend or monotonic discontinuity')
    allowance = ((last.after - first.before) * config.drift_slew_ppm / 1_000_000
                 + 2 * config.local_clock_quantization_ns)
    if (last.wall - last.after > first.wall - first.before + allowance
            or last.wall - last.before < first.wall - first.after - allowance):
        raise ValueError('wall clock step exceeds commissioned slew allowance')


class HostClockPublisher:
    """One explicit probe while holding Linux process-lifetime ownership.

    There is no asynchronous worker to abandon or automatic retry. close() may
    be called only after publish_once returns. The sampler independently bounds
    its socket operation. An already-written receipt remains self-expiring.
    """
    def __init__(self, config: HostClockConfig):
        _require_linux()
        if (not isinstance(config, HostClockConfig) or os.geteuid() != config.uid
                or socket.gethostname().lower() != config.expected_hostname):
            raise ValueError('clock publisher host or UID differs from provisioning')
        self.config, self._directory, self._lock = config, None, None
        self._used = self._running = False
        self._operation = threading.Lock()
        self.last_error = None
        path = _parent(config.output_path, config.uid)
        parent_info = path.parent.lstat()
        if parent_info.st_uid != config.uid:
            raise ValueError('clock output directory must belong to evidence UID')
        try:
            self._directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            actual = os.fstat(self._directory)
            if (actual.st_dev, actual.st_ino) != (parent_info.st_dev, parent_info.st_ino):
                raise ValueError('clock output directory changed')
            self._lock = os.open('.host-clock.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                 0o600, dir_fd=self._directory)
            _regular(os.fstat(self._lock), config.uid, private=True)
            import fcntl
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._clear()
        except Exception:
            self.close()
            raise

    def _inspect(self, name):
        try:
            info = os.stat(name, dir_fd=self._directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        _regular(info, self.config.uid)
        return info

    def _clear(self):
        if self._inspect('host-clock.json') is not None:
            os.unlink('host-clock.json', dir_fd=self._directory)
            os.fsync(self._directory)

    def _read_state(self):
        before = self._inspect('.host-clock-reference.json')
        if before is None:
            return None
        fd = os.open('.host-clock-reference.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory)
        with os.fdopen(fd, 'rb') as source:
            actual = os.fstat(source.fileno())
            if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('reference state changed while opening')
            return _json(source.read(MAX_JSON + 1))

    def _atomic(self, name, value, before_replace=None):
        self._inspect(name)
        temporary = '.clock-' + uuid.uuid4().hex
        descriptor = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o644, dir_fd=self._directory)
            os.fchmod(descriptor, 0o644)  # Metadata is readable by the separate camera UID.
            with os.fdopen(descriptor, 'wb') as output:
                descriptor = None
                body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                if len(body) > MAX_JSON:
                    raise ValueError('clock output exceeds bound')
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            if before_replace:
                before_replace()
            self._inspect(name)
            os.replace(temporary, name, src_dir_fd=self._directory, dst_dir_fd=self._directory)
            os.fsync(self._directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=self._directory)
            except FileNotFoundError:
                pass

    def publish_once(self):
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('clock publisher operation already outstanding')
        try:
            return self._publish_once()
        finally:
            self._operation.release()

    def _publish_once(self):
        if self._used or self._directory is None:
            raise RuntimeError('publisher is single-use and cannot retry')
        self._used = self._running = True
        try:
            boot, start = _boot_id(), _point()
            previous = self._read_state()
            if previous is not None:
                if (not isinstance(previous, dict) or set(previous) != {'version', 'server_ip', 'boot_id',
                        'attempted_boottime_ns', 'minimum_interval_seconds', 'reference_blocked'}
                        or type(previous['version']) is not int or previous['version'] != 1
                        or not isinstance(previous['boot_id'], str)
                        or type(previous['reference_blocked']) is not bool
                        or type(previous['attempted_boottime_ns']) is not int or previous['attempted_boottime_ns'] < 0
                        or not _finite(previous['minimum_interval_seconds'])
                        or not 1 <= previous['minimum_interval_seconds'] <= 86400):
                    raise ValueError('invalid durable reference polling state')
                if previous['server_ip'] == self.config.ntp.server_ip:
                    if previous['reference_blocked']:
                        raise ValueError('reference refused service; reviewed operator resolution required')
                    elapsed = (start.boot - previous['attempted_boottime_ns'] if previous['boot_id'] == boot else start.boot)
                    interval = max(previous['minimum_interval_seconds'], self.config.minimum_reference_interval_seconds)
                    if elapsed < interval * 1e9:
                        raise ValueError('commissioned reference polling interval has not elapsed')
            attempt = {'version': 1, 'server_ip': self.config.ntp.server_ip,
                'boot_id': boot, 'attempted_boottime_ns': start.boot,
                'minimum_interval_seconds': self.config.minimum_reference_interval_seconds, 'reference_blocked': True}
            self._atomic('.host-clock-reference.json', attempt)
            try:
                sample = NtpClockSampler(self.config.ntp).sample()
            except NtpKissOfDeath:
                # Already durable before the request: a crash or storage error
                # cannot turn a refusal into permission for another invocation.
                raise
            except Exception:
                attempt['reference_blocked'] = False
                self._atomic('.host-clock-reference.json', attempt)
                raise
            attempt['reference_blocked'] = False
            self._atomic('.host-clock-reference.json', attempt)
            evidence = sample.evidence.to_dict()
            if (sample.server_ip != self.config.ntp.server_ip
                    or sample.commissioning_id != self.config.ntp.commissioning_id
                    or sample.server_trust != self.config.ntp.server_trust
                    or evidence['host_boot_id'] != boot
                    or not start.before <= evidence['checked_monotonic_ns']
                    or evidence['valid_until_monotonic_ns'] - evidence['checked_monotonic_ns'] > 5_000_000_000):
                raise ValueError('clock sample source or boot binding differs')
            def current():
                now = _point()
                _continuous(start, now, self.config.ntp)
                if (_boot_id() != boot or not evidence['checked_monotonic_ns'] <= now.before <= now.after
                        < evidence['valid_until_monotonic_ns']):
                    raise ValueError('clock sample expired before publication')
                lower = evidence['checked_unix_ns'] + now.before - evidence['checked_monotonic_ns']
                upper = evidence['checked_unix_ns'] + now.after - evidence['checked_monotonic_ns']
                radius = evidence['uncertainty_seconds'] * 1e9
                if not lower - radius <= now.wall <= upper + radius:
                    raise ValueError('clock sample wall mapping changed')
            self._atomic('host-clock.json', evidence, current)
            current()  # fsync/rename delays cannot leave a newly stale receipt.
            return evidence
        except Exception:
            self.last_error = 'host_clock_publication_unavailable'
            self._clear()
            raise RuntimeError(self.last_error) from None
        finally:
            self._running = False

    def close(self):
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('retain publisher lifetime lock until sampling ends')
        try:
            if self._lock is not None:
                os.close(self._lock)
                self._lock = None
            if self._directory is not None:
                os.close(self._directory)
                self._directory = None
        finally:
            self._operation.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--once', action='store_true')
    operation.add_argument('--check-config', action='store_true')
    args = parser.parse_args(argv)
    publisher = None
    try:
        config = load_config(args.config)
        if args.check_config:
            print('Protected host-clock configuration valid; no probe sent.')
            return 0
        publisher = HostClockPublisher(config)
        publisher.publish_once()
        print('Short-lived camera host-clock receipt published.')
        return 0
    except Exception:
        print('Host-clock publication unavailable; no clock was changed.', file=sys.stderr)
        return 1
    finally:
        if publisher:
            publisher.close()


if __name__ == '__main__':
    raise SystemExit(main())

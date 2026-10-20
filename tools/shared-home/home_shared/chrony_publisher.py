"""Bridge commissioned chrony estimates to the existing protected camera file.

Uses the existing publisher's process lock and atomic-file implementation.
Never changes the original NTP probe ledger or starts/stops a service.
"""
import os
from pathlib import Path
import stat
import subprocess
import threading

from . import host_clock_service as host
from .chrony_clock import ChronyClockConfig, sample


def daemon_identity():
    result = subprocess.run(['/usr/bin/systemctl', 'show', 'victoria-clock-pilot.service',
                             '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ExecMainStartTimestampMonotonic',
                             '-p', 'ExecStart', '-p', 'FragmentPath'],
                            capture_output=True, text=True, check=True, timeout=1)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines())
    pid = values.get('MainPID', '')
    started = values.get('ExecMainStartTimestampMonotonic', '')
    if (values.get('ActiveState') != 'active' or not pid.isdecimal() or int(pid) <= 1
            or not started.isdecimal() or int(started) <= 0):
        raise ValueError('commissioned clock daemon unavailable')
    original = subprocess.run(['/usr/bin/systemctl', 'is-active', 'systemd-timesyncd.service'],
                              capture_output=True, text=True, timeout=1)
    if original.stdout.strip() != 'inactive':
        raise ValueError('another clock writer is active or unknown')
    expected = '/opt/victoria-clock/chrony-4.2/usr/sbin/chronyd'
    # systemd exposes the root-provisioned unit to the camera user. Reading
    # /proc/<other-UID>/exe would require unrelated process-inspection authority.
    argv = expected + ' -U -u systemd-timesync -d -R -f /opt/victoria-clock/chrony-pilot.conf'
    if (values.get('FragmentPath') != '/etc/systemd/system/victoria-clock-pilot.service'
            or 'path='+expected+' ;' not in values.get('ExecStart', '')
            or 'argv[]='+argv+' ;' not in values.get('ExecStart', '')):
        raise ValueError('clock daemon configured executable differs')
    info = Path(expected).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('unprotected clock executable')
    return pid, started


class ChronyPublisher:
    """Explicit one-shot publisher compatible with pilot_snapshot.accept_once."""
    def __init__(self, config, policy, *, _store=host.HostClockPublisher,
                 _sample=sample, _identity=daemon_identity, _point=host._point, _boot=host._boot_id):
        if not isinstance(policy, ChronyClockConfig):
            raise TypeError('explicit chrony policy required')
        if (policy.source_ip != config.ntp.server_ip
                or policy.local_clock_quantization_ns != config.ntp.local_clock_quantization_ns
                or policy.drift_slew_ppm != config.ntp.drift_slew_ppm
                or policy.valid_for_seconds != config.ntp.valid_for_seconds
                or policy.max_uncertainty_seconds > config.ntp.max_uncertainty_seconds):
            raise ValueError('chrony policy differs from camera freshness contract')
        self.config, self.policy = config, policy
        self._sample, self._identity, self._point, self._boot = _sample, _identity, _point, _boot
        self._used = False
        self._operation = threading.Lock()
        self._store = _store(config)

    def publish_once(self):
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('clock publication already active')
        try:
            return self._publish_once()
        finally:
            self._operation.release()

    def _publish_once(self):
        if self._used:
            raise ValueError('one publication per instance')
        self._used = True
        try:
            identity = self._identity()
            evidence = self._sample(self.policy).to_dict()
            start = self._point()
            if self._identity() != identity:
                raise ValueError('clock daemon changed during sampling')
            def current():
                now = self._point()
                host._continuous(start, now, self.config.ntp)
                if (self._boot() != evidence['host_boot_id']
                        or not evidence['checked_monotonic_ns'] <= now.before <= now.after < evidence['valid_until_monotonic_ns']):
                    raise ValueError('clock receipt expired or changed boot')
                mapped = evidence['checked_unix_ns'] + now.before - evidence['checked_monotonic_ns']
                if abs(now.wall-mapped) > evidence['uncertainty_seconds']*1e9:
                    raise ValueError('clock mapping changed before publication')
            self._store._atomic('host-clock.json', evidence, current)
            current()
            return evidence
        except Exception:
            self._store._clear()
            raise

    def close(self):
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('retain clock ownership until publication ends')
        try:
            self._store.close()
        finally:
            self._operation.release()

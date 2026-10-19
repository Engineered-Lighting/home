"""Short camera-host receipts from a commissioned local chrony 4.2 daemon.

Does not install, configure, start or alter the daemon. The provisioned host must
establish that chrony is the sole clock writer and owns localhost UDP 323.
Tracking is read-only. The daemon handles NTP polling; this query never sends an
NTP probe. Accuracy remains an estimate conditional on the commissioned source.
"""
from dataclasses import dataclass
import math
import subprocess
import uuid

from .chrony_tracking import parse_tracking
from .ntp_clock import CameraHostClockEvidence, _clock_point, _kernel_boot_id, _validate_points


@dataclass(frozen=True)
class ChronyClockConfig:
    source_ip: str
    max_reference_age_seconds: float
    local_clock_quantization_ns: int
    drift_slew_ppm: float
    timeout_seconds: float
    valid_for_seconds: float
    max_uncertainty_seconds: float

    def __post_init__(self):
        for name, lower, upper in (('max_reference_age_seconds', 0, 86400),
                                   ('drift_slew_ppm', 0, 1000000),
                                   ('timeout_seconds', 0, 1), ('valid_for_seconds', 0, 5),
                                   ('max_uncertainty_seconds', 0, 1)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower < value <= upper:
                raise ValueError('explicit finite chrony clock policy required')
        if type(self.local_clock_quantization_ns) is not int or not 1 <= self.local_clock_quantization_ns <= 1000000:
            raise ValueError('explicit host read quantization required')


def _tracking(timeout):
    result = subprocess.run(['/opt/victoria-clock/chrony-4.2/usr/bin/chronyc', '-n', '-h', '127.0.0.1', 'tracking'],
                            env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'},
                            capture_output=True, timeout=timeout, check=True)
    if len(result.stdout) > 4096 or result.stderr:
        raise ValueError('unexpected chrony report')
    return result.stdout.decode('ascii')


def sample(config, *, _query=_tracking, _point=_clock_point, _boot_id=_kernel_boot_id):
    if not isinstance(config, ChronyClockConfig):
        raise TypeError('commissioned chrony policy required')
    boot = _boot_id()
    start = _point()
    report = _query(config.timeout_seconds)
    end = _point()
    _validate_points([start, end], config)
    if _boot_id() != boot or end.after_ns - start.before_ns > config.timeout_seconds * 1e9:
        raise ValueError('clock query exceeded bound or changed boot')
    estimate = parse_tracking(report, config.source_ip, max_error_seconds=config.max_uncertainty_seconds)
    if not 0 <= end.unix_ns / 1e9 - estimate.reference_unix_seconds <= config.max_reference_age_seconds:
        raise ValueError('chrony reference stale or in future')
    # Charge the whole query and receipt lifetime at the explicit commissioned
    # rate; repeated queries obtain updated daemon estimates, not renewed samples.
    elapsed = (end.after_ns - start.before_ns) / 1e9
    brackets = (start.after_ns-start.before_ns + end.after_ns-end.before_ns) / 1e9
    uncertainty = math.nextafter(math.fsum((estimate.estimated_error_seconds,
        (elapsed + config.valid_for_seconds) * config.drift_slew_ppm / 1e6,
        brackets, 2 * config.local_clock_quantization_ns / 1e9, 2e-9)), math.inf)
    if uncertainty > config.max_uncertainty_seconds:
        raise ValueError('combined chrony receipt uncertainty exceeds budget')
    return CameraHostClockEvidence(boot, 'chrony:' + uuid.uuid4().hex,
        end.before_ns, end.unix_ns, end.before_ns + int(config.valid_for_seconds * 1e9), uncertainty)

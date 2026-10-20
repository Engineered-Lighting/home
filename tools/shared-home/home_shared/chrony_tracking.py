"""Read chrony 4.2 tracking estimates; this does not issue camera receipts.

Input is LC_ALL=C chronyc -n tracking from the provisioned local daemon.
The caller must establish daemon/config identity and bracket the query with
host clocks before using the estimate. No network probes or clock writes here.
"""
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import ipaddress
import math
import re


@dataclass(frozen=True)
class TrackingEstimate:
    source_ip: str
    stratum: int
    system_offset_seconds: float
    root_delay_seconds: float
    root_dispersion_seconds: float
    skew_ppm: float
    estimated_error_seconds: float
    reference_unix_seconds: float


def parse_tracking(text, expected_source_ip, *, max_error_seconds):
    """Fail closed on unselected/local sources, leaps and malformed estimates."""
    source = ipaddress.IPv4Address(expected_source_ip)
    if (source.is_unspecified or source.is_multicast
            or type(max_error_seconds) not in (int, float)
            or not math.isfinite(max_error_seconds) or not 0 < max_error_seconds <= 1):
        raise ValueError('explicit source and uncertainty budget required')
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError('bounded tracking output required')
    fields = {}
    for line in text.strip().splitlines():
        key, separator, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if not separator or key in fields:
            raise ValueError('invalid or duplicate tracking field')
        fields[key] = value
    expected = {'Reference ID', 'Stratum', 'Ref time (UTC)', 'System time', 'Last offset',
                'RMS offset', 'Frequency', 'Residual freq', 'Skew', 'Root delay',
                'Root dispersion', 'Update interval', 'Leap status'}
    if set(fields) != expected or fields['Leap status'] != 'Normal':
        raise ValueError('unsupported or unsynchronised tracking report')
    if fields['Reference ID'] != f'{int(source):08X} ({source})':
        raise ValueError('uncommissioned reference')
    if not re.fullmatch(r'[1-9]|1[0-5]', fields['Stratum']):
        raise ValueError('invalid stratum')

    def number(key, suffix, signed=False):
        value = fields[key]
        if not value.endswith(suffix):
            raise ValueError('unexpected tracking units')
        value = float(value[:-len(suffix)])
        if not math.isfinite(value) or (not signed and value < 0):
            raise ValueError('invalid tracking value')
        return value

    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?) seconds (fast|slow) of NTP time', fields['System time'])
    if not match:
        raise ValueError('invalid remaining system correction')
    offset = float(match[1])
    delay = number('Root delay', ' seconds')
    dispersion = number('Root dispersion', ' seconds')
    skew = number('Skew', ' ppm')
    error = math.nextafter(math.fsum((offset, dispersion, delay / 2)), math.inf)
    if not math.isfinite(error) or error > max_error_seconds:
        raise ValueError('tracking estimate exceeds budget')
    try:
        reference = parsedate_to_datetime(fields['Ref time (UTC)'] + ' +0000').timestamp()
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('invalid reference time') from error
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError('invalid reference time')
    return TrackingEstimate(str(source), int(fields['Stratum']),
                            offset if match[2] == 'fast' else -offset,
                            delay, dispersion, skew, error, reference)

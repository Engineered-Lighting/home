"""Use the camera's qualified pair ages without synchronizing host wall clocks."""

import math
import sys
import time
from dataclasses import dataclass

from body_input import bundle_frame_key


AGE_HEADER = "X-Den-Body-Capture-Age"
AGE_SCHEMA = "den-body-pair-age-1"


@dataclass(frozen=True)
class CaptureBounds:
    earliest_mono: float
    separation_s: float
    evidence_id: str


def lease_clock():
    """Monotonic figure clock, including host suspension where available."""
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def receipt_arrival(bundle):
    return getattr(bundle, "receipt_arrival_mono", None)


def _finite_number(value):
    return type(value) in (int, float) and -sys.float_info.max <= value <= sys.float_info.max


def capture_bounds(bundle, meta):
    """Accept only an age attestation paired with this exact bundle response.

    The camera produces the attestation after accepting this GET. Each age
    upper-bounds elapsed time from the start of its exposure interval through
    the source's sample in that response. The separation bounds the greatest
    distance between any instants in the two exposure intervals. The source
    owns the timestamp semantics, drift, and discontinuity checks. Ages must
    remain upper bounds in receiver monotonic seconds.

    The sample occurred after the receiver began the GET, so subtracting the
    larger age from GET start is conservative even with asymmetric transit.
    Legacy bundles have no attestation and supply no measured-body clock.
    """
    receipt = getattr(bundle, "capture_age_receipt", None)
    request = getattr(bundle, "request_mono", None)
    arrival = receipt_arrival(bundle)
    expected = bundle_frame_key(bundle, meta)
    if (expected is None or not isinstance(receipt, dict)
            or receipt.get("schema") != AGE_SCHEMA or not _finite_number(request)
            or not _finite_number(arrival) or request > arrival):
        return None
    key = receipt.get("frame_key")
    evidence_id = receipt.get("evidence_id")
    if (type(key) is not list or len(key) != len(expected)
            or any(type(a) is not type(b) or a != b for a, b in zip(key, expected))
            or not isinstance(evidence_id, str) or not 0 < len(evidence_id) <= 128):
        return None
    rgb, depth, separation = (receipt.get(name) for name in
                              ("rgb_age_upper_s", "depth_age_upper_s", "separation_upper_s"))
    if any(not _finite_number(value) or value < 0 for value in (rgb, depth, separation)):
        return None
    earliest = math.nextafter(float(request) - float(max(rgb, depth)), -math.inf)
    return CaptureBounds(earliest, separation, evidence_id) if math.isfinite(earliest) else None

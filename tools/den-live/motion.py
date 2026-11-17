"""Causal, person-bound forecasts from den-track's existing Kalman state.

This module never estimates a second velocity or associates people. Perception
copies the pinned tracker's [x, y, vx, vy] and full covariance after its update.
The extra uncertainty terms are conservative envelopes, not calibrated
probabilities. Neither body-facing nor a language-model answer is velocity.
"""
import math
from collections import OrderedDict, deque
from dataclasses import dataclass


HORIZONS_S = (0.2, 0.5, 1.0, 2.0)
MAX_HORIZON_S = 2.0
MIN_SAMPLES = 3
MIN_HISTORY_S = 0.4
MAX_AGE_S = 1.5
HISTORY_S = 2.0
BASIS = "den_track_kalman_cv"


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _field(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _vector(value, n):
    if not isinstance(value, (tuple, list)) or len(value) != n or not all(map(_finite, value)):
        raise ValueError("invalid motion vector")
    return tuple(float(v) for v in value)


def _covariance(value, n):
    """Copy a finite symmetric PSD matrix; never manufacture a missing covariance.

    A semidefinite Cholesky check accepts zero-variance axes, but rejects a
    nonzero covariance attached to a zero pivot. Tolerance is for arithmetic,
    not a replacement for absent uncertainty.
    """
    if not isinstance(value, (tuple, list)) or len(value) != n:
        raise ValueError("missing motion covariance")
    matrix = tuple(_vector(row, n) for row in value)
    scale = max(1e-15, *(abs(v) for row in matrix for v in row))
    tolerance = scale * 1e-10
    if any(matrix[i][i] < 0 for i in range(n)):
        raise ValueError("negative motion variance")
    if any(abs(matrix[i][j] - matrix[j][i]) > tolerance for i in range(n) for j in range(i)):
        raise ValueError("asymmetric motion covariance")
    # Normalize to a correlation matrix so a legitimate small position variance
    # beside a large velocity variance is not mistaken for a zero pivot.
    sd = [math.sqrt(matrix[i][i]) for i in range(n)]
    normalized = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if sd[i] == 0 or sd[j] == 0:
                if matrix[i][j] != 0:
                    raise ValueError("covariance on zero-variance axis")
            else:
                normalized[i][j] = matrix[i][j] / max(sd[i], sd[j]) / min(sd[i], sd[j])
                if not math.isfinite(normalized[i][j]) or abs(normalized[i][j]) > 1 + 1e-10:
                    raise ValueError("indefinite motion covariance")
    if any(abs(normalized[i][j] - normalized[j][i]) > 1e-10 for i in range(n) for j in range(i)):
        raise ValueError("asymmetric motion covariance")
    tolerance = 1e-10
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        pivot = normalized[i][i] - math.fsum(lower[i][k] ** 2 for k in range(i))
        if not math.isfinite(pivot) or pivot < -tolerance:
            raise ValueError("indefinite motion covariance")
        lower[i][i] = math.sqrt(max(0.0, pivot))
        for j in range(i + 1, n):
            residual = normalized[j][i] - math.fsum(lower[j][k] * lower[i][k] for k in range(i))
            if lower[i][i] > math.sqrt(tolerance):
                lower[j][i] = residual / lower[i][i]
            elif abs(residual) > tolerance:
                raise ValueError("indefinite motion covariance")
    return matrix


@dataclass(frozen=True)
class MotionState:
    service_run_id: str
    camera_epoch: int
    frame_id: int
    track_id: str
    association_revision: int
    transform_id: str
    scan_revision: str
    depth_calibration_sha256: str
    observed_mono: float
    available_mono: float
    valid_until_mono: float
    xy_m: tuple[float, float]
    velocity_mps: tuple[float, float]
    covariance: tuple[tuple[float, ...], ...]  # [x, y, vx, vy], including cross terms
    sample_count: int
    history_span_s: float
    accel_sigma_mps2: float
    registration_error_m: float
    timing_error_s: float
    source_obs_ids: tuple[str, ...]
    basis: str = BASIS

    def __post_init__(self):
        for name in ("service_run_id", "track_id", "transform_id", "scan_revision", "depth_calibration_sha256"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError("unbound motion state")
        for name in ("camera_epoch", "frame_id", "association_revision", "sample_count"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid motion counter")
        for name in ("observed_mono", "available_mono", "valid_until_mono", "history_span_s",
                     "accel_sigma_mps2", "registration_error_m", "timing_error_s"):
            if not _finite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError("invalid motion time or error")
        if not self.observed_mono <= self.available_mono < self.valid_until_mono <= self.observed_mono + MAX_AGE_S:
            raise ValueError("invalid motion receipt order")
        if (self.sample_count < 1 or self.history_span_s > HISTORY_S + 1e-9 or self.basis != BASIS
                or (self.sample_count == 1 and self.history_span_s != 0)):
            raise ValueError("invalid motion history")
        if (not isinstance(self.source_obs_ids, (list, tuple)) or not self.source_obs_ids
                or any(not isinstance(v, str) or not v for v in self.source_obs_ids)):
            raise ValueError("motion has no current observations")
        object.__setattr__(self, "xy_m", _vector(self.xy_m, 2))
        object.__setattr__(self, "velocity_mps", _vector(self.velocity_mps, 2))
        object.__setattr__(self, "covariance", _covariance(self.covariance, 4))
        object.__setattr__(self, "source_obs_ids", tuple(self.source_obs_ids))

    @property
    def lead_qualified(self):
        return self.sample_count >= MIN_SAMPLES and self.history_span_s + 1e-9 >= MIN_HISTORY_S


@dataclass(frozen=True)
class MotionSample:
    horizon_s: float               # after the forecast's issued_mono, not after capture
    at_mono: float
    xy_m: tuple[float, float]
    covariance_m2: tuple[tuple[float, float], tuple[float, float]]
    sigma_m: float                 # sqrt(largest eigenvalue), not a confidence percentage


@dataclass(frozen=True)
class MotionForecast:
    state: MotionState
    issued_mono: float
    valid_until_mono: float        # expires as evidence even if a sample describes a later instant
    samples: tuple[MotionSample, ...]


def matches(state, frame, track_id=None, association_revision=None):
    """The exact frame, calibration and current person claim must still be the ones copied."""
    if not isinstance(state, MotionState):
        return False
    keys = ("service_run_id", "camera_epoch", "frame_id", "transform_id", "scan_revision",
            "depth_calibration_sha256")
    return (all(getattr(state, k) == _field(frame, k) for k in keys)
            and (track_id is None or state.track_id == track_id)
            and (association_revision is None or state.association_revision == association_revision))


def forecast(state, now_mono, *, frame=None, track_id=None, association_revision=None,
             cutoff_mono=None, horizons_s=HORIZONS_S):
    """Return a short-horizon CV envelope or abstain; never renew evidence expiry.

    The causal cutoff is independent of the requested future sample times.
    Observation age is included automatically. A caller using current tracking
    should supply frame, track_id and association_revision for the binding fence.
    """
    if not isinstance(state, MotionState) or not state.lead_qualified or not _finite(now_mono):
        return None
    cutoff = now_mono if cutoff_mono is None else cutoff_mono
    if (not _finite(cutoff) or not state.available_mono <= cutoff <= now_mono < state.valid_until_mono
            or (frame is not None and not matches(state, frame, track_id, association_revision))
            or (track_id is not None and state.track_id != track_id)
            or (association_revision is not None and state.association_revision != association_revision)):
        return None
    if (not isinstance(horizons_s, (list, tuple)) or not horizons_s
            or any(not _finite(h) or not 0 <= h <= MAX_HORIZON_S for h in horizons_s)
            or any(b <= a for a, b in zip(horizons_s, horizons_s[1:]))):
        return None
    P, velocity = state.covariance, state.velocity_mps
    samples = []
    try:
        for horizon in horizons_s:
            at = now_mono + horizon
            dt = at - state.observed_mono
            xy = tuple(p + dt * v for p, v in zip(state.xy_m, velocity))
            # Exact upper-left block of F P F^T. Preserve position/velocity
            # correlation and every input covariance term, including registration
            # uncertainty the observer may already have put in P.
            cov = [[P[i][j] + dt * (P[i + 2][j] + P[i][j + 2]) + dt ** 2 * P[i + 2][j + 2]
                    for j in range(2)] for i in range(2)]
            # Same continuous-white-acceleration process model as den-track's
            # kalman.predict. Evaluate noise through the timing interval's far end.
            noise = state.accel_sigma_mps2 ** 2 * (dt + state.timing_error_s) ** 3 / 3
            velocity_sd = math.sqrt(max(0.0, P[2][2] + P[3][3]))
            timing_radius = ((math.hypot(*velocity) + velocity_sd) * state.timing_error_s
                             + 0.5 * state.accel_sigma_mps2 * state.timing_error_s ** 2)
            envelope = (state.registration_error_m + timing_radius) ** 2
            for i in range(2):
                cov[i][i] += noise + envelope
            covariance = _covariance(cov, 2)
            a, b, d = covariance[0][0], covariance[0][1], covariance[1][1]
            sigma = math.sqrt(max(0.0, (a + d + math.hypot(a - d, 2 * b)) / 2))
            if not all(map(_finite, (*xy, at, sigma))):
                return None
            samples.append(MotionSample(float(horizon), at, xy, covariance, sigma))
    except (OverflowError, ValueError):
        return None
    return MotionForecast(state, float(now_mono), state.valid_until_mono, tuple(samples))


def timing_bound(frame, bundle, meta):
    """Bound the arrival-anchored tracker clock with an exact source age receipt.

    Use an elapsed interval to bridge the suspension-aware receipt clock and
    the tracker's monotonic clock; never subtract their absolute origins.
    Legacy/unattested frames provide no timing bound and hence no motion state.
    """
    from body_clock import capture_bounds, receipt_arrival
    bounds = capture_bounds(bundle, meta)
    arrival, receipt = _field(bundle, "arrival_mono"), receipt_arrival(bundle)
    observed = _field(frame, "capture_mono")
    if (bounds is None or not all(map(_finite, (arrival, receipt, observed)))
            or _field(frame, "camera_session") != _field(bundle, "session")
            or _field(_field(frame, "rgb"), "seq") != _field(bundle, "rgb_seq")
            or _field(_field(frame, "depth"), "seq") != meta.get("depth_seq")):
        return None
    interval = receipt - bounds.earliest_mono
    if interval < 0:
        return None
    earliest = arrival - interval
    return max(abs(observed - earliest), abs(arrival - observed))


class Receipts:
    """Bounded observation receipts, never a second tracker or motion smoother."""

    def __init__(self):
        self.history = OrderedDict()

    def update(self, raw_track, *, frame, track_id, association_revision, association_status, state,
               current_obs_ids, available_mono, accel_sigma_mps2, registration_error_m, timing_error_s):
        key = tuple(_field(frame, k) for k in ("service_run_id", "camera_epoch", "transform_id", "scan_revision",
                                              "depth_calibration_sha256")) + (track_id, association_revision)
        previous = self.history.pop(track_id, None)
        observed = _field(frame, "capture_mono")
        # A coast, an unsafe person claim or missing P interrupts qualification;
        # the revision's two-frame grace must not bridge an ambiguous frame.
        if (state != "confirmed" or association_status != "confident" or not current_obs_ids
                or not isinstance(current_obs_ids, (tuple, list))
                or any(not isinstance(v, str) or not v for v in current_obs_ids)
                or type(_field(frame, "frame_id")) is not int or _field(frame, "frame_id") < 0
                or timing_error_s is None or not _finite(observed)
                or not _finite(getattr(raw_track, "last_seen", None))
                or abs(raw_track.last_seen - observed) > 1e-9):
            return None
        entries = previous[1] if previous is not None and previous[0] == key else deque(maxlen=16)
        if entries and (observed <= entries[-1][0] or _field(frame, "frame_id") <= entries[-1][1]):
            self.history[track_id] = (key, entries)
            return None
        if entries and observed - entries[-1][0] >= MAX_AGE_S:
            entries.clear()
        while entries and observed - entries[0][0] > HISTORY_S:
            entries.popleft()
        if any(set(current_obs_ids).intersection(entry[2]) for entry in entries):
            return None   # repeated evidence cannot qualify as a new observation
        entries.append((observed, _field(frame, "frame_id"), tuple(current_obs_ids)))
        try:
            raw = _vector(getattr(raw_track, "x", None), 4)
            motion = MotionState(
                service_run_id=key[0], camera_epoch=key[1], frame_id=_field(frame, "frame_id"), track_id=track_id,
                association_revision=association_revision, transform_id=key[2], scan_revision=key[3],
                depth_calibration_sha256=key[4], observed_mono=observed, available_mono=available_mono,
                valid_until_mono=observed + MAX_AGE_S - timing_error_s,
                xy_m=raw[:2], velocity_mps=raw[2:], covariance=getattr(raw_track, "P", None),
                sample_count=len(entries), history_span_s=entries[-1][0] - entries[0][0], accel_sigma_mps2=accel_sigma_mps2,
                registration_error_m=registration_error_m, timing_error_s=timing_error_s,
                source_obs_ids=tuple(current_obs_ids))
        except (TypeError, ValueError, OverflowError):
            return None
        self.history[track_id] = (key, entries)
        while len(self.history) > 128:
            self.history.popitem(last=False)
        return motion

"""Causal, person-bound forecasts from den-track's existing Kalman state.

This module never estimates a second velocity or associates people. Perception
copies the pinned tracker's [x, y, vx, vy] and full covariance after its update.
The extra uncertainty terms are conservative envelopes, not calibrated
probabilities. Neither body-facing nor a language-model answer is velocity.

Source-host elapsed time is estimated, with no validated elapsed-clock drift
contract. The native RGB cohort assumes a nominal 1:1 elapsed rate; absolute
age uncertainty stays separate. The legacy arrival-clock audit still requires
disjoint intervals. All envelopes are conditional model calculations, not
demonstrated containment or physical timing qualification.
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
    white_accel_sigma_mps32: float   # sqrt(acceleration spectral density), m / s**(3/2), not m / s**2
    registration_error_m: float
    timing_error_s: float
    source_obs_ids: tuple[str, ...]
    basis: str = BASIS
    time_status: str = "estimated_source_elapsed"
    time_qualified: bool = False
    timing_evidence_id: str | None = None
    filter_clock: str | None = None
    filter_observed_s: float | None = None
    source_rate_assumption: str | None = None

    def __post_init__(self):
        for name in ("service_run_id", "track_id", "transform_id", "scan_revision", "depth_calibration_sha256"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError("unbound motion state")
        for name in ("camera_epoch", "frame_id", "association_revision", "sample_count"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid motion counter")
        for name in ("observed_mono", "available_mono", "valid_until_mono", "history_span_s",
                     "white_accel_sigma_mps32", "registration_error_m", "timing_error_s"):
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
        if self.time_status != "estimated_source_elapsed" or self.time_qualified is not False:
            raise ValueError("source elapsed time is not physically qualified")
        if (self.timing_evidence_id is not None
                and (not isinstance(self.timing_evidence_id, str) or not 0 < len(self.timing_evidence_id) <= 128)):
            raise ValueError("invalid source timing evidence id")
        if self.filter_clock is not None and (self.filter_clock != "rgb_host_elapsed"
                or not _finite(self.filter_observed_s)
                or self.source_rate_assumption != "nominal_1to1_unverified"):
            raise ValueError("invalid native filter clock")
        if self.filter_clock is None and (self.filter_observed_s is not None or self.source_rate_assumption is not None):
            raise ValueError("unbound native filter clock")
        object.__setattr__(self, "xy_m", _vector(self.xy_m, 2))
        object.__setattr__(self, "velocity_mps", _vector(self.velocity_mps, 2))
        object.__setattr__(self, "covariance", _covariance(self.covariance, 4))
        object.__setattr__(self, "source_obs_ids", tuple(self.source_obs_ids))

    @property
    def lead_qualified(self):
        """The shadow history gate only. time_qualified remains False."""
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
    """Return a diagnostic shadow CV envelope or abstain; never renew expiry.

    The causal cutoff is independent of the requested future sample times.
    Observation age is included automatically. A caller using current tracking
    should supply frame, track_id and association_revision for the binding fence.
    The returned state explicitly remains time_qualified=False: these conditional
    calculations do not validate source elapsed-clock drift or physical timing.
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
            # den-track's continuous-white-acceleration model: q has units
            # m**2/s**3. sigma is not a physical acceleration bound.
            noise = state.white_accel_sigma_mps32 ** 2 * dt ** 3 / 3
            for i in range(2):
                cov[i][i] += noise
            base = _covariance(cov, 2)
            base_sd = math.sqrt(_largest_eigenvalue(base))
            velocity_sd = math.sqrt(_largest_eigenvalue(tuple(tuple(P[i][j] for j in (2, 3)) for i in (2, 3))))
            error = state.timing_error_s
            # For integrated white acceleration, the variance of the extra
            # displacement over an uncertain interval <= error is bounded by
            # q * (dt * error**2 + error**3 / 3). This includes the shared
            # Brownian velocity at dt; q * error**3/3 alone would omit it.
            timing_process_sd = state.white_accel_sigma_mps32 * math.sqrt(dt * error ** 2 + error ** 3 / 3)
            timing_radius = (math.hypot(*velocity) + velocity_sd) * error + timing_process_sd
            radius = state.registration_error_m + timing_radius
            # Unknown correlation requires adding standard-deviation bounds,
            # not variances. This isotropic inflation dominates
            # (sqrt(u^T base u) + radius)**2 in every unit direction u.
            inflation = 2 * base_sd * radius + radius ** 2
            for i in range(2):
                cov[i][i] += inflation
            covariance = _covariance(cov, 2)
            sigma = math.sqrt(_largest_eigenvalue(covariance))
            if not all(map(_finite, (*xy, at, sigma))):
                return None
            samples.append(MotionSample(float(horizon), at, xy, covariance, sigma))
    except (OverflowError, ValueError):
        return None
    return MotionForecast(state, float(now_mono), state.valid_until_mono, tuple(samples))


def _largest_eigenvalue(covariance):
    a, b, d = covariance[0][0], covariance[0][1], covariance[1][1]
    return max(0.0, (a + d + math.hypot(a - d, 2 * b)) / 2)


def timing_bound(frame, bundle, meta):
    """Bound the arrival-anchored tracker clock with an exact source age receipt.

    Use an elapsed interval to bridge the suspension-aware receipt clock and
    the tracker's monotonic clock; never subtract their absolute origins.
    Legacy/unattested frames provide no timing bound and hence no motion state.
    """
    interval = capture_interval(frame, bundle, meta)
    observed = _field(frame, "capture_mono")
    if interval is None or not _finite(observed):
        return None
    earliest, arrival = interval
    return max(abs(observed - earliest), abs(arrival - observed))


def capture_interval(frame, bundle, meta):
    """Absolute capture-age bounds, independent of the filter's elapsed axis."""
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
    return earliest, arrival


@dataclass(frozen=True)
class NativeStep:
    """One source-clock step; this is not physical elapsed-time qualification.

    The native RGB host clock is assumed to advance at one receiver second per
    source second. That rate and absolute offset remain unverified. Overlapping
    absolute age intervals do not measure a different native elapsed interval.
    """
    scope: tuple
    frame_id: int
    rgb_seq: int
    source_s: float

    def matches(self, frame):
        return (self.scope == native_scope(frame) and self.frame_id == _field(frame, "frame_id")
                and self.rgb_seq == _field(_field(frame, "rgb"), "seq")
                and self.source_s == _field(_field(frame, "rgb"), "host_s"))


def native_scope(frame):
    return tuple(_field(frame, key) for key in ("service_run_id", "camera_epoch", "camera_session",
                                               "transform_id", "scan_revision", "depth_calibration_sha256"))


class NativeClock:
    """Audit one whole filter cohort without changing its time origin or P.

    Per-frame attested capture intervals constrain one *constant*, nominal-rate
    source-to-receiver offset. Their intersection can refute clock continuity;
    it cannot prove the source clock's rate. Missing age receipts leave the
    filter usable for existing tracking, but supply no current motion-age bound.
    A contradiction requires a new filter epoch, never a rebase of old state.
    """
    def __init__(self, counts=None):
        self.counts = counts if counts is not None else {}
        for key in ("native_clock_frames", "native_clock_incidents", "native_clock_refusals",
                    "native_absolute_intervals_overlap"):
            self.counts.setdefault(key, 0)
        self.previous = self.offset_bounds = self.interval = self.failure = self.arrival_mono = None

    def step(self, frame, bundle, meta):
        if self.failure is not None:
            self.counts["native_clock_refusals"] += 1
            raise ValueError(self.failure)
        rgb = _field(frame, "rgb")
        source, device = _field(rgb, "host_s"), _field(rgb, "device_s")
        seq, fid = _field(rgb, "seq"), _field(frame, "frame_id")
        arrival = _field(frame, "arrival_mono")
        scope = native_scope(frame)
        reason = None
        if (not _finite(source) or source < 0 or type(seq) is not int or seq < 0 or type(fid) is not int or fid < 0
                or not _finite(arrival) or arrival != _field(bundle, "arrival_mono")
                or (device is not None and not _finite(device))
                or (_field(bundle, "t_host") is not None and _field(bundle, "t_host") != source)):
            reason = "native_source_clock_invalid"
        elif self.previous is not None:
            old, old_device = self.previous
            if (scope != old.scope or fid <= old.frame_id or seq <= old.rgb_seq or source <= old.source_s
                    or arrival < self.arrival_mono
                    or (device is not None and old_device is not None and device <= old_device)
                    or (device is None) != (old_device is None)):
                reason = "native_source_clock_discontinuity"
        interval = capture_interval(frame, bundle, meta)
        if reason is None and interval is not None:
            candidate = (interval[0] - source, interval[1] - source)
            if self.offset_bounds is not None:
                candidate = max(candidate[0], self.offset_bounds[0]), min(candidate[1], self.offset_bounds[1])
            if candidate[0] > candidate[1]:
                reason = "native_source_rate_or_offset_changed"
            else:
                self.offset_bounds = candidate
            if self.interval is not None and self.interval[1] >= interval[0]:
                self.counts["native_absolute_intervals_overlap"] += 1
            self.interval = interval
        if reason is not None:
            self.failure = reason
            self.counts["native_clock_incidents"] += 1
            self.counts["native_clock_refusals"] += 1
            raise ValueError(reason)
        receipt = NativeStep(scope, fid, seq, float(source))
        self.previous = receipt, device
        self.arrival_mono = arrival
        self.counts["native_clock_frames"] += 1
        return receipt


class Receipts:
    """Bounded observation receipts, never a second tracker or motion smoother."""

    def __init__(self):
        self.history = OrderedDict()
        self.clocks = OrderedDict()
        self.native_clocks = OrderedDict()
        self.counts = dict.fromkeys(("clock_frames", "motion_clock_unqualified", "source_clock_changed",
                                    "capture_intervals_overlap", "source_history_unavailable",
                                    "timing_receipt_unavailable", "motion_state_unavailable",
                                    "history_unqualified", "shadow_history_ready", "clock_incidents",
                                    "continued_clock_refusals"), 0)

    def audit(self, raw_track, *, frame, timing_error_s):
        """Audit every Kalman step, including tentative and coasting steps.

        The tracker's P assumes its elapsed times are the capture elapsed
        times. A changing arrival-anchored offset violates that assumption.
        A later clean window cannot undo earlier filter updates, so a failure
        taints this raw track until a new birth or camera epoch. Evicted old
        tracks cannot masquerade as audited births: their private history is
        already longer than the single birth observation.
        """
        raw_id = getattr(raw_track, "id", None)
        key = (_field(frame, "service_run_id"), _field(frame, "camera_epoch"), raw_id)
        observed, frame_id = _field(frame, "capture_mono"), _field(frame, "frame_id")
        source = _field(_field(frame, "rgb"), "host_s")
        scope = tuple(_field(frame, k) for k in ("transform_id", "scan_revision", "depth_calibration_sha256"))
        previous = self.clocks.pop(key, None)
        if previous is not None and previous[0] == frame_id and previous[1] == observed:
            self.clocks[key] = previous
            return previous[-1] is None
        reason = previous[-1] if previous is not None else None
        if reason is None:
            if (type(raw_id) is not int or raw_id < 0 or not _finite(observed) or not _finite(source)
                    or type(frame_id) is not int or not _finite(timing_error_s) or timing_error_s < 0):
                reason = "timing_receipt_unavailable"
            elif previous is None:
                raw_history = getattr(raw_track, "history", None)
                if (not isinstance(raw_history, (list, tuple)) or len(raw_history) != 1
                        or raw_history[0] is not True or getattr(raw_track, "last_seen", None) != observed):
                    reason = "source_history_unavailable"
            else:
                _, old_observed, old_source, old_error, old_scope, _ = previous
                # This tolerance covers only floating-point subtraction, not an
                # operational allowance for unmeasured clock drift or delay.
                rounding = 8 * max(math.ulp(v) for v in (observed, old_observed, source, old_source))
                if (scope != old_scope or observed <= old_observed or source <= old_source or frame_id <= previous[0]
                        or abs((observed - old_observed) - (source - old_source)) > rounding):
                    reason = "source_clock_changed"
                elif old_observed + old_error >= observed - timing_error_s:
                    reason = "capture_intervals_overlap"
        self.clocks[key] = (frame_id, observed, source, timing_error_s, scope, reason)
        while len(self.clocks) > 128:
            self.clocks.popitem(last=False)
        self.counts["clock_frames"] += 1
        if reason is not None:
            self.counts["motion_clock_unqualified"] += 1
            self.counts[reason] += 1
            self.counts["clock_incidents" if previous is None or previous[-1] is None else "continued_clock_refusals"] += 1
        return reason is None

    def audit_native(self, raw_track, *, frame, native_step, filter_time):
        key = (*native_scope(frame), getattr(raw_track, "id", None))
        previous = self.native_clocks.pop(key, None)
        if previous is not None and previous[0] == _field(frame, "frame_id"):
            self.native_clocks[key] = previous
            return (previous[2] and isinstance(native_step, NativeStep) and native_step.matches(frame)
                    and filter_time == previous[1] == native_step.source_s)
        valid = (isinstance(native_step, NativeStep) and native_step.matches(frame)
                 and filter_time == native_step.source_s and _finite(getattr(raw_track, "last_seen", None)))
        if valid and previous is None:
            valid = getattr(raw_track, "history", None) == [True] and raw_track.last_seen == filter_time
        elif valid:
            valid = previous[2] and native_step.source_s > previous[1]
        if valid and getattr(raw_track, "obs_ids", None):
            valid = raw_track.last_seen == filter_time
        self.native_clocks[key] = (_field(frame, "frame_id"), filter_time, bool(valid))
        while len(self.native_clocks) > 128:
            self.native_clocks.popitem(last=False)
        return bool(valid)

    def update(self, raw_track, *, frame, track_id, association_revision, association_status, state,
               current_obs_ids, available_mono, white_accel_sigma_mps32, registration_error_m, timing_error_s,
               timing_evidence_id=None, native_step=None, filter_time=None):
        key = tuple(_field(frame, k) for k in ("service_run_id", "camera_epoch", "transform_id", "scan_revision",
                                              "depth_calibration_sha256")) + (track_id, association_revision)
        previous = self.history.pop(track_id, None)
        observed = _field(frame, "capture_mono")
        native = isinstance(native_step, NativeStep)
        if not (self.audit_native(raw_track, frame=frame, native_step=native_step, filter_time=filter_time) if native
                else self.audit(raw_track, frame=frame, timing_error_s=timing_error_s)):
            return None
        history_time = native_step.source_s if native else observed
        # A coast, an unsafe person claim or missing P interrupts qualification;
        # the revision's two-frame grace must not bridge an ambiguous frame.
        if (state != "confirmed" or association_status != "confident" or not current_obs_ids
                or not isinstance(current_obs_ids, (tuple, list))
                or any(not isinstance(v, str) or not v for v in current_obs_ids)
                or type(_field(frame, "frame_id")) is not int or _field(frame, "frame_id") < 0
                or timing_error_s is None or not _finite(observed)
                or not _finite(getattr(raw_track, "last_seen", None))
                or abs(raw_track.last_seen - history_time) > 1e-9):
            return None
        entries = previous[1] if previous is not None and previous[0] == key else deque(maxlen=16)
        if entries and (history_time <= entries[-1][0] or _field(frame, "frame_id") <= entries[-1][1]):
            self.history[track_id] = (key, entries)
            return None
        if entries and history_time - entries[-1][0] >= MAX_AGE_S:
            entries.clear()
        while entries and history_time - entries[0][0] > HISTORY_S:
            entries.popleft()
        if any(set(current_obs_ids).intersection(entry[2]) for entry in entries):
            return None   # repeated evidence cannot qualify as a new observation
        entries.append((history_time, _field(frame, "frame_id"), tuple(current_obs_ids)))
        try:
            raw = _vector(getattr(raw_track, "x", None), 4)
            motion = MotionState(
                service_run_id=key[0], camera_epoch=key[1], frame_id=_field(frame, "frame_id"), track_id=track_id,
                association_revision=association_revision, transform_id=key[2], scan_revision=key[3],
                depth_calibration_sha256=key[4], observed_mono=observed, available_mono=available_mono,
                valid_until_mono=observed + MAX_AGE_S - timing_error_s,
                xy_m=raw[:2], velocity_mps=raw[2:], covariance=getattr(raw_track, "P", None),
                sample_count=len(entries), history_span_s=entries[-1][0] - entries[0][0],
                white_accel_sigma_mps32=white_accel_sigma_mps32,
                registration_error_m=registration_error_m, timing_error_s=timing_error_s,
                source_obs_ids=tuple(current_obs_ids), timing_evidence_id=timing_evidence_id,
                filter_clock="rgb_host_elapsed" if native else None,
                filter_observed_s=history_time if native else None,
                source_rate_assumption="nominal_1to1_unverified" if native else None)
        except (TypeError, ValueError, OverflowError):
            self.counts["motion_state_unavailable"] += 1
            return None
        self.history[track_id] = (key, entries)
        while len(self.history) > 128:
            self.history.popitem(last=False)
        self.counts["shadow_history_ready" if motion.lead_qualified else "history_unqualified"] += 1
        return motion

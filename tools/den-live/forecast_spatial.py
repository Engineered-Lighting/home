"""Qualify an existing motion forecast for room display and virtual shadow evaluation.

This adapter consumes the producer's forecast and its original clocks. It neither
propagates state again nor grants aim, occupancy or physical output authority.
"""
import math
from dataclasses import asdict, replace

import motion
from contracts import FrameRef, PlaneRef, TrackEvidence, TrackingSnapshot

SCHEMA = "den-spatial-forecast-1"
MAX_OBSERVATIONS, MAX_IDENTIFIER = 64, 128


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _identifiers(state, frame, track):
    for name in ("service_run_id", "camera_session", "transform_id", "scan_revision", "depth_calibration_sha256"):
        value = getattr(frame, name)
        if not isinstance(value, str) or not 0 < len(value) <= MAX_IDENTIFIER:
            return False
    for name in ("camera_epoch", "frame_id"):
        if type(getattr(frame, name)) is not int or getattr(frame, name) < 0:
            return False
    rgb = frame.rgb
    if (not isinstance(rgb, PlaneRef) or type(rgb.seq) is not int or rgb.seq < 0 or not _finite(rgb.host_s)
            or any(type(value) is not int or value <= 0 for value in (rgb.width, rgb.height))):
        return False
    return (type(track.association_revision) is int and track.association_revision >= 0
            and isinstance(track.track_id, str) and 0 < len(track.track_id) <= MAX_IDENTIFIER
            and motion.matches(state, frame, track.track_id, track.association_revision))


def _observations(state, track):
    current = track.current_obs_ids
    return (isinstance(current, (tuple, list)) and 0 < len(current) <= MAX_OBSERVATIONS
            and all(isinstance(value, str) and 0 < len(value) <= MAX_IDENTIFIER for value in current)
            and len(set(current)) == len(current) and isinstance(state.source_obs_ids, (tuple, list))
            and len(state.source_obs_ids) <= MAX_OBSERVATIONS
            and tuple(state.source_obs_ids) == tuple(current))


def _times(forecast, frame, now):
    state = forecast.state
    values = (state.observed_mono, state.available_mono, forecast.issued_mono, now, state.valid_until_mono,
              forecast.valid_until_mono, state.timing_error_s, frame.capture_mono, frame.arrival_mono)
    return (all(_finite(value) and value >= 0 for value in values)
            and state.observed_mono == frame.capture_mono
            and state.observed_mono <= state.available_mono <= forecast.issued_mono <= now
            and frame.arrival_mono <= state.available_mono
            and forecast.valid_until_mono == state.valid_until_mono
            and now < state.valid_until_mono <= state.observed_mono + motion.MAX_AGE_S - state.timing_error_s)


def _samples(forecast):
    """Validate supplied samples and copy their existing uncertainty. Never produce new sample positions."""
    if (not isinstance(forecast.samples, (tuple, list))
            or not 0 < len(forecast.samples) <= len(motion.HORIZONS_S)):
        return None, "samples_invalid"
    copied, previous = [], -1.0
    for sample in forecast.samples:
        if (not isinstance(sample, motion.MotionSample) or not _finite(sample.horizon_s)
                or not previous < sample.horizon_s <= motion.MAX_HORIZON_S or sample.horizon_s < 0
                or not _finite(sample.at_mono) or sample.at_mono != forecast.issued_mono + sample.horizon_s):
            return None, "samples_invalid"
        try:
            xy = motion._vector(sample.xy_m, 2)
            covariance = motion._covariance(sample.covariance_m2, 2)
        except (TypeError, ValueError, OverflowError):
            return None, "sample_covariance_invalid"
        a, b, d = covariance[0][0], covariance[0][1], covariance[1][1]
        try:
            sigma = math.sqrt(max(0.0, (a + d + math.hypot(a - d, 2 * b)) / 2))
        except (ValueError, OverflowError):
            return None, "sample_covariance_invalid"
        if (not _finite(sample.sigma_m) or sample.sigma_m < 0 or not _finite(sigma)
                or not math.isclose(sample.sigma_m, sigma, rel_tol=1e-9, abs_tol=1e-12)):
            return None, "sample_covariance_invalid"
        copied.append(replace(sample, xy_m=xy, covariance_m2=covariance))
        previous = sample.horizon_s
    return tuple(copied), None


def qualify(forecast, snapshot, track, now_mono):
    """Return (projection, None), or (None, fixed refusal reason), for one caller-selected current track.

    Coordinates and full covariances are already in the room's floor XY frame.
    The transform and scan identifiers bind them; applying the camera rotation a
    second time would corrupt them. Absolute future sample times and capture
    expiry remain exactly those supplied by the producer. Remaining freshness is
    measured at qualified_at_mono and cannot become a renewed browser TTL.
    """
    if not isinstance(forecast, motion.MotionForecast) or not isinstance(forecast.state, motion.MotionState):
        return None, "forecast_unavailable"
    if (not isinstance(snapshot, TrackingSnapshot) or snapshot.status != "fresh"
            or not isinstance(snapshot.frame, FrameRef)):
        return None, "snapshot_unavailable"
    if (not isinstance(track, TrackEvidence) or track.state != "confirmed"
            or track.association_status != "confident"):
        return None, "track_ineligible"
    frame, state = snapshot.frame, forecast.state
    if not isinstance(snapshot.tracks, (tuple, list)):
        return None, "binding_mismatch"
    current = [value for value in snapshot.tracks
               if isinstance(value, TrackEvidence) and value.track_id == track.track_id]
    if (len(current) != 1 or current[0] != track or snapshot.service_run_id != frame.service_run_id
            or snapshot.camera_epoch != frame.camera_epoch or not _identifiers(state, frame, track)):
        return None, "binding_mismatch"
    if not _observations(state, track):
        return None, "observations_mismatch"
    if getattr(track, "motion", None) != state:
        return None, "motion_unavailable"
    if not _times(forecast, frame, now_mono):
        return None, "timing_invalid"
    try:
        motion._covariance(state.covariance, 4)
    except (TypeError, ValueError, OverflowError):
        return None, "state_covariance_invalid"
    try:
        validated_state = replace(state)
    except (TypeError, ValueError, OverflowError):
        return None, "state_invalid"
    if not validated_state.lead_qualified:
        return None, "history_unqualified"
    samples, reason = _samples(forecast)
    if reason is not None:
        return None, reason
    # Preserve the canonical producer dataclass fields instead of introducing a
    # second motion wire representation or shifting them by arrival age again.
    packet = asdict(replace(forecast, state=validated_state, samples=samples))
    packet.update(schema=SCHEMA, qualified_at_mono=float(now_mono),
                  remaining_freshness_s=forecast.valid_until_mono - now_mono,
                  shadow_only=True, virtual_only=True, physical_qualified=False,
                  walkable_qualified=False, beam_qualified=False, delay_qualified=False,
                  aim_qualified=False, occupancy_qualified=False)
    return packet, None

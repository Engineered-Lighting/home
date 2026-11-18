"""Bounded semantic evidence beside walking geometry, pending held-out admission.

Projection never selects an aim, changes a score, or grants forecast authority.
Times retain the producer's original monotonic observation and expiry.
"""
import math
import re
from collections.abc import Mapping

import intent
import motion
from contracts import PERCEPTION_S

SCHEMA = "den.walking-intent/v1"
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _finite(value):
    try:
        return intent.finite(value)
    except OverflowError:
        return False


def _metadata(hint):
    """Copy only named scalar metadata. Raw evidence and model text stay private."""
    # Bindings use the intake's grammar, so the registered "<camera>@<version>" transform passes.
    if (not isinstance(hint.dependencies, Mapping) or set(hint.dependencies) != intent.DEPENDENCIES
            or not intent.binding_values_valid(hint.dependencies)):
        raise ValueError("malformed_intent_hint")
    if hint.raw_option_scores is not None and (
            not isinstance(hint.raw_option_scores, Mapping) or set(hint.raw_option_scores) != set(intent.CHOICES)
            or any(not _finite(value) for value in hint.raw_option_scores.values())):
        raise ValueError("malformed_intent_hint")
    public = hint.public()
    identifiers = ("request_id", "clip_id", "profile_digest", "snapshot_manifest_digest", "model_revision")
    for key in identifiers:
        value = public.get(key)
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise ValueError("malformed_intent_hint")
    if (not intent.HEX64.fullmatch(public["profile_digest"])
            or not intent.HEX64.fullmatch(public["snapshot_manifest_digest"])
            or not intent.REVISION.fullmatch(public["model_revision"])):
        raise ValueError("malformed_intent_hint")
    dependencies = public.get("dependencies")
    if (not isinstance(dependencies, dict) or set(dependencies) != intent.DEPENDENCIES
            or not intent.binding_values_valid(dependencies)):
        raise ValueError("malformed_intent_hint")
    if (public.get("schema") != intent.SCHEMA or public.get("intent") not in intent.CHOICES
            or public.get("authority") != "semantic_prior_only"
            or public.get("uncertainty") != "uncalibrated_semantic_hint"
            or public.get("clock") != "producer_monotonic"):
        raise ValueError("malformed_intent_hint")
    observed, produced, expires = (public.get(key) for key in ("observed_at", "produced_at", "expires_at"))
    if (not all(_finite(value) for value in (observed, produced, expires))
            or not 0 <= observed <= produced < expires <= observed + 10):
        raise ValueError("malformed_intent_hint")
    scores = public.get("raw_option_scores")
    if scores is not None and (not isinstance(scores, dict) or set(scores) != set(intent.CHOICES)
                               or any(not _finite(value) for value in scores.values())):
        raise ValueError("malformed_intent_hint")
    return {**{key: public[key] for key in identifiers}, "dependencies": dict(dependencies),
            "produced_at": produced, "observed_at": observed, "expires_at": expires,
            "intent": public["intent"], "hypotheses": [
                {"intent": choice, "raw_score": scores[choice] if scores is not None else None,
                 "calibrated_probability": False} for choice in intent.CHOICES]}


def _source_frames(hint, metadata):
    frames = _field(hint.evidence, "frames")
    if not isinstance(frames, (list, tuple)) or len(frames) != 32:
        raise ValueError("malformed_intent_hint")
    deps, previous_time, previous_id = metadata["dependencies"], -math.inf, -1
    for entry in frames:
        source = _field(entry, "source_frame")
        frame_id, stamp = _field(source, "frame_id"), _field(entry, "captured_at")
        arrived = _field(source, "arrival_mono")
        if (type(frame_id) is not int or frame_id < 0 or frame_id < previous_id
                or not all(_finite(value) for value in (stamp, arrived))
                or not 0 <= stamp <= metadata["observed_at"] or stamp < previous_time
                or _field(source, "capture_mono") != stamp or not stamp <= arrived <= metadata["produced_at"]
                or _field(source, "service_run_id") != deps["service_run_id"]
                or type(_field(source, "camera_epoch")) is not int
                or str(_field(source, "camera_epoch")) != deps["camera_epoch"]
                or _field(source, "transform_id") != deps["transform_id"]
                or _field(source, "scan_revision") != deps["scan_revision"]
                or _field(source, "depth_calibration_sha256") != deps["calibration_revision"]):
            raise ValueError("malformed_intent_hint")
        previous_time, previous_id = stamp, frame_id
    return previous_id


def _track(snapshot, dependencies, now):
    frame = _field(snapshot, "frame")
    run, epoch = (_field(snapshot, key) for key in ("service_run_id", "camera_epoch"))
    frame_id = _field(frame, "frame_id")
    captured, arrived = (_field(frame, key) for key in ("capture_mono", "arrival_mono"))
    tracks = _field(snapshot, "tracks", ())
    if (not isinstance(run, str) or not IDENTIFIER.fullmatch(run)
            or type(epoch) is not int or epoch < 0 or type(frame_id) is not int or frame_id < 0
            or _field(frame, "service_run_id") != run or _field(frame, "camera_epoch") != epoch
            or not all(_finite(value) for value in (captured, arrived))
            or not 0 <= captured <= arrived <= now < captured + PERCEPTION_S
            or not isinstance(tracks, (list, tuple)) or len(tracks) > 64):
        return None
    camera_id = _field(frame, "camera_id", _field(snapshot, "camera_id"))
    if camera_id is not None and camera_id != dependencies["camera_id"]:
        return None
    matches = [track for track in tracks if _field(track, "track_id") == dependencies["track_id"]]
    return matches[0] if len(matches) == 1 else None


def _newer_motion(state, snapshot, track, observed, now):
    if not isinstance(state, motion.MotionState) or not state.lead_qualified:
        return False
    return (motion.matches(state, _field(snapshot, "frame"), _field(track, "track_id"),
                           _field(track, "association_revision"))
            and state.observed_mono == _field(_field(snapshot, "frame"), "capture_mono")
            and observed < state.observed_mono <= state.available_mono <= now < state.valid_until_mono
            and state.source_obs_ids == tuple(_field(track, "current_obs_ids", ())))


def project(hint, now, snapshot, *, motion_state=None, control_cutoff=None, plan_reason=None):
    """Report evidence disposition without affecting the geometry consumer's result."""
    result = {"schema": SCHEMA, "disposition": "absent", "reason": "intent_hint_absent",
              "selected": False, "consumed": False, "authority": "semantic_prior_only",
              "uncertainty": "uncalibrated_semantic_hint", "clock": "producer_monotonic",
              "request_id": None, "clip_id": None, "profile_digest": None,
              "snapshot_manifest_digest": None, "model_revision": None, "dependencies": {},
              "produced_at": None, "observed_at": None, "expires_at": None,
              "intent": None, "hypotheses": []}
    if hint is None:
        return result
    result.update(disposition="ignored", reason="malformed_intent_hint")
    if not isinstance(hint, intent.IntentHint):
        return result
    try:
        metadata = _metadata(hint)
        last_frame_id = _source_frames(hint, metadata)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return result
    result.update(metadata)
    if not _finite(now) or now < 0:
        return dict(result, reason="invalid_clock")
    if now < metadata["produced_at"]:
        return dict(result, reason="future_intent_hint")
    if not hint.valid_at(now):
        return dict(result, disposition="expired", reason="capture_lease_expired")
    if control_cutoff is not None:
        if not _finite(control_cutoff) or not 0 <= control_cutoff <= now:
            return dict(result, reason="invalid_control_cutoff")
        if metadata["observed_at"] < control_cutoff:
            return dict(result, reason="newer_control")
    try:
        track = _track(snapshot, metadata["dependencies"], now)
        if (track is None or last_frame_id > _field(_field(snapshot, "frame"), "frame_id")
                or not hint.matches(snapshot, track)):
            return dict(result, reason="current_binding_changed")
        disposition, reason = hint.disposition(now, snapshot, track)
        if disposition != "advisory_only" or reason != "awaiting_held_out_admission":
            return dict(result, reason="current_binding_changed")
        newer = _newer_motion(motion_state, snapshot, track, metadata["observed_at"], now)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return dict(result, reason="current_binding_changed")
    if newer and math.hypot(*motion_state.velocity_mps) < 0.1:
        return dict(result, reason="newer_measured_stop")
    if newer and plan_reason == "motion_changed":
        return dict(result, reason="newer_measured_turn")
    return dict(result, reason="awaiting_held_out_admission")

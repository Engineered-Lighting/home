"""Bind optional RGB head fits to the exact accepted observation. Standard library only."""
import math

from contracts import Orientation3D

BASIS = "rgb_coco5_pnp_model"
JOINT_IDS = (0, 1, 2, 3, 4)
MAX_CANDIDATES = 64
REASONS = frozenset(("missing_calibration", "invalid_calibration", "missing_frame", "wrong_size", "missing_points",
                     "low_score", "face_too_small", "degenerate", "no_solution", "ambiguous", "projection_error",
                     "uncertain", "deadline", "worker_error", "malformed_packet", "binding_mismatch",
                     "observation_mismatch", "orientation_invalid", "other", "low_confidence", "outside_frame",
                     "face_small", "collapsed_ears", "landmark_order", "degenerate_geometry", "pnp_failed",
                     "reprojection", "ambiguous_solution", "ill_conditioned", "uncertainty", "calibration_invalid",
                     "frame_invalid", "row_invalid", "person_limit", "detection_invalid", "numerical_failure",
                     "head_roll"))


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _vector(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(finite(x) for x in value)


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def _rotation(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(_vector(row) for row in value):
        return False
    if any(abs(math.hypot(*row) - 1) > 1e-5 for row in value):
        return False
    if any(abs(_dot(a, b) - (i == j)) > 1e-5 for i, a in enumerate(value) for j, b in enumerate(value)):
        return False
    a, b, c = value
    determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
    determinant += a[2] * (b[0] * c[1] - b[1] * c[0])
    return abs(determinant - 1.0) <= 1e-5


def _frame(value):
    return (isinstance(value, (list, tuple)) and len(value) == 3 and isinstance(value[0], str) and bool(value[0])
            and type(value[1]) is int and value[1] >= 0 and type(value[2]) is int and value[2] > 0)


def _axes(forward, up):
    return (_vector(forward) and _vector(up) and abs(math.hypot(*forward) - 1) <= 1e-4
            and abs(math.hypot(*up) - 1) <= 1e-4 and abs(_dot(forward, up)) <= 1e-4)


def valid_head_axes(forward_room, up_room):
    """Validate head axes with a 75-degree anatomical roll prior against projected room-up."""
    if not _axes(forward_room, up_room) or math.hypot(*forward_room[:2]) < 0.20:
        return False
    # Projecting gravity into the head's transverse plane allows normal downward pitch. This prior rejects
    # inverted solutions from simultaneous left/right swaps without claiming an additional measurement.
    reference = tuple(z - forward_room[2] * component for z, component in zip((0, 0, 1), forward_room))
    length = math.hypot(*reference)
    return _dot(up_room, tuple(value / length for value in reference)) >= math.cos(math.radians(75))


def diagnostics(payload):
    """Bounded numerical health only. Unexpected reason names collapse into one fixed bucket."""
    if not isinstance(payload, dict):
        return 0, {"malformed_packet": 1}
    candidates = payload.get("candidates")
    count = len(candidates) if isinstance(candidates, list) and len(candidates) <= MAX_CANDIDATES else 0
    reasons = {}
    raw = payload.get("reasons")
    if isinstance(raw, dict):
        for name, value in list(raw.items())[:64]:
            if type(value) is int and 0 < value <= MAX_CANDIDATES:
                key = name if name in REASONS else "other"
                reasons[key] = min(MAX_CANDIDATES, reasons.get(key, 0) + value)
    return count, reasons


def _same_pixels(detection, observation):
    points = detection.get("keypoints")
    if not isinstance(points, list) or len(points) != 17:
        return False
    normalized = [point if point is not None else [None, None, None] for point in points]
    return normalized == observation.get("keypoints")


def bind_candidates(row, observations, payload, *, frame_key, transform_id, rgb_from_left, room_from_left,
                    capture_mono):
    """Return obs_id -> room orientation and bounded rejection counts for this exact row.

    The pinned observer emits one observation per row detection in the same order, including rejected entries.
    All index, detector ID, pixel and registration checks are required before a candidate can follow that mapping.
    The caller applies the track's committed safe association before publishing any mapped estimate.
    """
    if payload is None:
        return {}, {}
    if (not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list)
            or len(payload["candidates"]) > MAX_CANDIDATES):
        return {}, {"malformed_packet": 1}
    detections = row.get("detections") if isinstance(row, dict) else None
    if (not isinstance(detections, list) or not isinstance(observations, (list, tuple))
            or len(detections) != len(observations) or not _frame(frame_key) or not finite(capture_mono)
            or any(not isinstance(observation, dict) for observation in observations)
            or not _rotation(rgb_from_left) or not _rotation(room_from_left)):
        return {}, {"binding_mismatch": 1}
    ids = [d.get("id") if isinstance(d, dict) else None for d in detections]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        return {}, {"binding_mismatch": 1}
    output, reasons, seen = {}, {}, set()
    for candidate in payload["candidates"]:
        if not isinstance(candidate, dict):
            reasons["malformed_packet"] = reasons.get("malformed_packet", 0) + 1
            continue
        index = candidate.get("detection_index")
        if (type(index) is not int or not 0 <= index < len(detections)
                or not _frame(candidate.get("frame_key")) or tuple(candidate["frame_key"]) != tuple(frame_key)
                or candidate.get("transform_id") != transform_id or candidate.get("detection_id") != ids[index]):
            reasons["binding_mismatch"] = reasons.get("binding_mismatch", 0) + 1
            continue
        if index in seen:
            output.pop(observations[index].get("obs_id"), None)
            reasons["binding_mismatch"] = reasons.get("binding_mismatch", 0) + 1
            continue
        seen.add(index)
        observation = observations[index]
        if (not isinstance(observation, dict) or observation.get("kind") != "keypoints"
                or observation.get("joint_set") != "coco17" or observation.get("validity") != "valid"
                or observation.get("transform_id") != transform_id
                or (observation.get("freshness") or {}).get("status") != "fresh"
                or not isinstance(observation.get("obs_id"), str) or not _same_pixels(detections[index], observation)):
            reasons["observation_mismatch"] = reasons.get("observation_mismatch", 0) + 1
            continue
        forward, up = candidate.get("forward_rgb"), candidate.get("up_rgb")
        angular_sd, rmse = candidate.get("angular_sd_deg"), candidate.get("fit_rmse_px")
        source_ids = candidate.get("source_joint_ids")
        if (candidate.get("basis") != BASIS or candidate.get("source_joint_ids") not in (list(JOINT_IDS), JOINT_IDS)
                or any(type(value) is not int for value in source_ids)
                or not _axes(forward, up) or not finite(angular_sd) or not 20 <= angular_sd <= 45
                or not finite(rmse) or rmse < 0):
            reasons["orientation_invalid"] = reasons.get("orientation_invalid", 0) + 1
            continue
        inverse_rgb = tuple(zip(*rgb_from_left))
        left_forward = tuple(_dot(axis, forward) for axis in inverse_rgb)
        left_up = tuple(_dot(axis, up) for axis in inverse_rgb)
        room_forward = tuple(_dot(axis, left_forward) for axis in room_from_left)
        room_up = tuple(_dot(axis, left_up) for axis in room_from_left)
        horizontal = math.hypot(*room_forward[:2])
        if not valid_head_axes(room_forward, room_up):
            reasons["orientation_invalid"] = reasons.get("orientation_invalid", 0) + 1
            continue
        output[observation["obs_id"]] = Orientation3D(
            room_forward, room_up, math.degrees(math.atan2(room_forward[0], room_forward[1])) % 360,
            math.degrees(math.atan2(room_forward[2], horizontal)), float(angular_sd), float(capture_mono), JOINT_IDS, BASIS)
    return output, reasons

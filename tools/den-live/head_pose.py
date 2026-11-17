"""Bounded calibrated RGB COCO-5 head pose for the detector worker.

The existing head3d.py adult template supplies anatomical proportions. This model estimates coarse head direction.
Only exact-frame detector landmarks and the registered RGB calibration enter. Image pixels and landmarks are never
logged. numpy and OpenCV load only when estimate is called in the worker.
"""
import math
import time

MODEL_ID = "coco5_head_template_v1"
BASIS = "rgb_coco5_pnp_model"
SOURCE_JOINT_IDS = (0, 1, 2, 3, 4)
# Same canonical right/forward/up metre template as perception-lab/head3d.py. COCO left is negative model right.
HEAD_TEMPLATE = ((0.0, 0.100, -0.020), (-0.032, 0.085, 0.015), (0.032, 0.085, 0.015),
                 (-0.075, 0.0, 0.0), (0.075, 0.0, 0.0))
MODEL_SD_DEG, MAX_SD_DEG, SCORE_MIN = 20.0, 45.0, 0.5
MAX_PEOPLE, MAX_SEEDS, LM_ITERATIONS = 2, 12, 15
MAX_ESTIMATE_S = 0.10
ALTERNATE_ANGLE_DEG = 25.0


class DeadlineExceeded(Exception):
    pass


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise DeadlineExceeded


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _calibration(value, np):
    if not isinstance(value, dict):
        return None
    k, d, size = value.get("k"), value.get("d"), value.get("size")
    rotation = value.get("room_from_rgb")
    transform_id, sigma = value.get("transform_id"), value.get("pixel_sd_px")
    if (not isinstance(k, (list, tuple)) or len(k) != 5 or not all(_finite(v) for v in k)
            or min(k[:2]) <= 0 or not isinstance(d, (list, tuple)) or len(d) != 8
            or not all(_finite(v) for v in d) or size not in ([640, 360], (640, 360))
            or not isinstance(transform_id, str) or not 0 < len(transform_id) <= 128
            or not _finite(sigma) or not 0 < sigma <= 64
            or not isinstance(rotation, (list, tuple)) or len(rotation) != 3
            or any(not isinstance(row, (list, tuple)) or len(row) != 3
                   or not all(_finite(v) for v in row) for row in rotation)):
        return None
    rotation = np.asarray(rotation, dtype=float)
    if (not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4)
            or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-4)):
        return None
    fx, fy, cx, cy, skew = k
    return {"K": np.array(((fx, 0.0, cx), (0.0, fy, cy), (0.0, 0.0, 1.0))),
            "d": np.asarray(d, dtype=float), "skew": skew, "size": tuple(size),
            "transform_id": transform_id, "pixel_sd_px": float(sigma), "room_up_rgb": rotation[2]}


def _pixels(detection, np):
    keypoints = detection.get("keypoints")
    if (not _finite(detection.get("score")) or not SCORE_MIN <= detection["score"] <= 1
            or not isinstance(keypoints, (list, tuple)) or len(keypoints) != 17):
        return None, "low_confidence"
    face = keypoints[:5]
    if any(not isinstance(point, (list, tuple)) or len(point) != 3
           or not all(_finite(v) for v in point) or point[2] < SCORE_MIN for point in face):
        return None, "low_confidence"
    pixels = np.array([point[:2] for point in face], dtype=float)
    if ((pixels < 0).any() or (pixels[:, 0] >= 640).any() or (pixels[:, 1] >= 360).any()):
        return None, "outside_frame"
    eyes, ears = pixels[2] - pixels[1], pixels[4] - pixels[3]
    eye_span, ear_span = float(np.linalg.norm(eyes)), float(np.linalg.norm(ears))
    if ear_span < 1.2 * eye_span:
        return None, "collapsed_ears"
    if ear_span < 8 or eye_span < 3:
        return None, "face_small"
    if float(eyes @ ears) <= 0:
        return None, "landmark_order"
    spread = np.linalg.svd(pixels - pixels.mean(axis=0), compute_uv=False)
    if spread[1] < max(0.5, 0.03 * spread[0]):
        return None, "degenerate_geometry"
    return pixels, None


def _unskew(pixels, calibration):
    pixels = pixels.copy()
    matrix = calibration["K"]
    pixels[:, 0] -= calibration["skew"] * (pixels[:, 1] - matrix[1, 2]) / matrix[1, 1]
    return pixels


def _project(model, rotation, translation, calibration, cv2):
    pixels = cv2.projectPoints(model, rotation, translation, calibration["K"], calibration["d"])[0].reshape(-1, 2)
    matrix = calibration["K"]
    pixels[:, 0] += calibration["skew"] * (pixels[:, 1] - matrix[1, 2]) / matrix[1, 1]
    return pixels


def _angle(a, b, np):
    return math.degrees(math.acos(float(np.clip(a @ b, -1.0, 1.0))))


def _solutions(model, pixels, calibration, np, cv2, deadline=None):
    _check_deadline(deadline)
    unskewed = _unskew(pixels, calibration)
    normalized = cv2.undistortPoints(unskewed.reshape(-1, 1, 2), calibration["K"],
                                     calibration["d"]).reshape(-1, 2)
    # SQPnP supports five non-planar points. Four-point subsets expose competing geometric branches.
    seeds = []
    selections = [(np.arange(5), cv2.SOLVEPNP_SQPNP)]
    selections += [(np.array([j for j in range(5) if j != omit]), cv2.SOLVEPNP_AP3P) for omit in (0, 3, 4)]
    for indices, method in selections:
        _check_deadline(deadline)
        found = cv2.solvePnPGeneric(np.ascontiguousarray(model[indices]), np.ascontiguousarray(normalized[indices]),
                                    np.eye(3), None, flags=method)
        seeds.extend(zip(found[1], found[2]))
    solutions = []
    for rotation, translation in seeds[:MAX_SEEDS]:
        _check_deadline(deadline)
        rotation, translation = cv2.solvePnPRefineLM(model, unskewed, calibration["K"], calibration["d"],
                                                     rotation.copy(), translation.copy(),
                                                     criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS,
                                                               LM_ITERATIONS, 1e-7))
        matrix = cv2.Rodrigues(rotation)[0]
        if not np.isfinite(matrix).all() or not np.isfinite(translation).all():
            continue
        projected = _project(model, rotation, translation, calibration, cv2)
        rmse = float(np.sqrt(np.mean(np.sum((projected - pixels) ** 2, axis=1))))
        if not math.isfinite(rmse):
            continue
        # Model axes are proper orthonormal columns because Rodrigues produces a proper rotation.
        forward, up = matrix[:, 1], matrix[:, 2]
        camera_points = model @ matrix.T + translation.reshape(1, 3)
        if (camera_points[:, 2] <= 0.05).any():
            continue
        toward_camera = -translation.reshape(3) / np.linalg.norm(translation)
        if float(forward @ toward_camera) < math.cos(math.radians(110)):
            continue
        if any(_angle(forward, item["forward"], np) < 0.5
               and _angle(up, item["up"], np) < 0.5 for item in solutions):
            continue
        solutions.append({"rotation": rotation, "translation": translation, "forward": forward, "up": up,
                          "rmse": rmse})
    return sorted(solutions, key=lambda item: item["rmse"])


def _uncertainty(model, pixels, calibration, solution, np, cv2, deadline=None):
    """Local pixel sensitivity with all six pose parameters, including nuisance translation.

    The supplied pixel noise is a lower bound. Summing each anchor's directional deviation permits unknown
    cross-anchor correlation. The separate generic-template floor remains an explicit anatomical assumption.
    """
    rotation, translation = solution["rotation"], solution["translation"]
    columns, direction_columns = [], []
    for index in range(6):
        _check_deadline(deadline)
        step = 1e-5
        plus_r, minus_r = rotation.copy(), rotation.copy()
        plus_t, minus_t = translation.copy(), translation.copy()
        plus, minus = (plus_r, minus_r) if index < 3 else (plus_t, minus_t)
        plus.reshape(3)[index % 3] += step
        minus.reshape(3)[index % 3] -= step
        columns.append(((_project(model, plus_r, plus_t, calibration, cv2)
                         - _project(model, minus_r, minus_t, calibration, cv2)) / (2 * step)).reshape(-1))
        if index < 3:
            high, low = cv2.Rodrigues(plus_r)[0], cv2.Rodrigues(minus_r)[0]
            direction_columns.append((high[:, [1, 2]] - low[:, [1, 2]]) / (2 * step))
    jacobian = np.column_stack(columns)
    _check_deadline(deadline)
    left, singular, right = np.linalg.svd(jacobian, full_matrices=False)
    if not np.isfinite(singular).all() or singular[-1] <= singular[0] * 1e-7:
        return None
    projected = _project(model, rotation, translation, calibration, cv2)
    # Four residual degrees of freedom, ten pixel components and six fitted parameters.
    residual_sigma = math.sqrt(float(np.sum((projected - pixels) ** 2)) / 4)
    sigma = max(calibration["pixel_sd_px"], residual_sigma)
    inverse = (right.T / singular) @ left.T
    deviations = []
    for direction in range(2):
        derivative = np.column_stack([column[:, direction] for column in direction_columns])
        sensitivity = derivative @ inverse[:3]
        deviations.append(sigma * sum(float(np.linalg.norm(sensitivity[:, 2 * j:2 * j + 2], ord="fro"))
                                      for j in range(5)))
    noise = math.degrees(max(deviations))
    return math.hypot(MODEL_SD_DEG, noise)


def _candidate(detection, calibration, np, cv2, deadline=None):
    pixels, reason = _pixels(detection, np)
    if reason:
        return None, reason
    model = np.asarray(HEAD_TEMPLATE, dtype=float)
    solutions = _solutions(model, pixels, calibration, np, cv2, deadline)
    if not solutions:
        return None, "pnp_failed"
    upright = []
    for solution in solutions:
        forward = solution["forward"]
        projected_up = calibration["room_up_rgb"] - float(calibration["room_up_rgb"] @ forward) * forward
        length = float(np.linalg.norm(projected_up))
        if length >= 0.20 and float(solution["up"] @ (projected_up / length)) >= math.cos(math.radians(75)):
            upright.append(solution)
    if not upright:
        return None, "head_roll"
    solutions = upright
    best = solutions[0]
    if best["rmse"] > max(1.5, 1.5 * calibration["pixel_sd_px"]):
        return None, "reprojection"
    for rival in solutions[1:]:
        if (rival["rmse"] <= best["rmse"] + calibration["pixel_sd_px"]
                and max(_angle(best["forward"], rival["forward"], np),
                        _angle(best["up"], rival["up"], np)) >= ALTERNATE_ANGLE_DEG):
            return None, "ambiguous_solution"
    angular_sd = _uncertainty(model, pixels, calibration, best, np, cv2, deadline)
    _check_deadline(deadline)
    if angular_sd is None:
        return None, "ill_conditioned"
    if not math.isfinite(angular_sd) or angular_sd > MAX_SD_DEG:
        return None, "uncertainty"
    return {"forward_rgb": best["forward"].tolist(), "up_rgb": best["up"].tolist(),
            "angular_sd_deg": angular_sd, "source_joint_ids": list(SOURCE_JOINT_IDS),
            "basis": BASIS, "fit_rmse_px": best["rmse"]}, None


def estimate(row, calibration, frame_key):
    """Return exact-frame coarse head candidates and fixed rejection counts, without retaining input data."""
    deadline = time.monotonic() + MAX_ESTIMATE_S
    import cv2
    import numpy as np

    result = {"candidates": [], "reasons": {}}

    def count(reason):
        result["reasons"][reason] = result["reasons"].get(reason, 0) + 1

    calibration = _calibration(calibration, np)
    if calibration is None:
        count("calibration_invalid")
        return result
    if (not isinstance(frame_key, (list, tuple)) or len(frame_key) != 3
            or not isinstance(frame_key[0], str) or not 0 < len(frame_key[0]) <= 128
            or type(frame_key[1]) is not int or frame_key[1] < 0
            or type(frame_key[2]) is not int or frame_key[2] <= 0):
        count("frame_invalid")
        return result
    frame = row.get("frame") if isinstance(row, dict) else None
    detections = row.get("detections") if isinstance(row, dict) else None
    if (not isinstance(frame, dict) or (frame.get("width"), frame.get("height")) != calibration["size"]
            or not isinstance(detections, list) or len(detections) > 64):
        count("row_invalid")
        return result
    people = 0
    ids = set()
    for index, detection in enumerate(detections):
        if not isinstance(detection, dict) or detection.get("class") != "person":
            continue
        if people >= MAX_PEOPLE:
            count("person_limit")
            continue
        people += 1
        detection_id = detection.get("id")
        if not isinstance(detection_id, str) or not 0 < len(detection_id) <= 128 or detection_id in ids:
            count("detection_invalid")
            continue
        ids.add(detection_id)
        try:
            _check_deadline(deadline)
            candidate, reason = _candidate(detection, calibration, np, cv2, deadline)
        except DeadlineExceeded:
            count("deadline")
            break
        except (cv2.error, ValueError, OverflowError, np.linalg.LinAlgError):
            candidate, reason = None, "numerical_failure"
        if candidate is None:
            count(reason)
        else:
            result["candidates"].append({"detection_index": index, "detection_id": detection_id,
                                         "frame_key": list(frame_key), "transform_id": calibration["transform_id"],
                                         **candidate})
    return result

"""Localization core: Frigate events -> world-frame person tracks.

Two stages
==========

STAGE A (room-level, no calibration)
    Every ``frigate/events`` person event is mapped camera -> room via
    ``zones[].frigate_camera`` (fallback: camera name == zone id; cameras
    mapping to nothing — e.g. outdoor ``driveway`` — are ignored).  The
    result is a room-level track with ``pos=null``.

STAGE B (precise, camera has intrinsics + extrinsics)
    The bbox foot point (bottom-center) is back-projected to the floor:

    1. The detection bbox lives in *detect* resolution pixels.  Intrinsics K
       were calibrated at ``intrinsics.image_size``; the foot pixel is scaled
       ``detect_res -> image_size`` first (per-axis linear scale).
    2. ``cv2.undistortPoints`` maps the pixel to normalized camera coords
       (x', y') so the camera ray in the *camera* frame is d_cam=[x', y', 1].
       OpenCV camera convention: +Z forward, +X right, +Y down.
    3. Extrinsics store ``q_wxyz`` (world->camera rotation R as a quaternion)
       and the camera center ``C`` in world coords (``t = -R @ C``).  The ray
       direction in the world frame is ``d = R^T @ d_cam``.
    4. Floor intersection: world frame is Z-up with the floor at z=0, so the
       ray ``X(s) = C + s*d`` hits the floor at ``s = -C_z / d_z``.
       Rejected when ``s <= 0`` (behind camera), ``|d_z| < 0.05`` (grazing,
       numerically explosive), or the hit lies outside the walkable polygon
       buffered +0.5 m (skipped when floor.json is absent).  Hits at most
       0.5 m beyond that hull soft-clamp to the wall line with sigma x
       sqrt(3) instead of being rejected — furniture against a wall (couch,
       bed) pushes seat-plane projections just past it (``_gate_or_clamp``).

    Fallbacks:
      * cropped feet — bbox bottom within 2% of the frame bottom: intersect
        the *bbox-center* ray with the z=0.9 m plane (hip height), then drop
        to the floor; measurement sigma multiplied by sqrt(3).
      * seated — bbox aspect (h/w) > 1.6 AND Frigate ``stationary`` AND the
        bbox bottom is NOT at the frame edge, but the foot-point ray-cast is
        implausible (no floor hit, or the hit fails the caller's
        plausibility check, e.g. outside the walkable hull): the bbox bottom
        is then likely a seat edge rather than feet — intersect the
        bbox-center ray with the z=0.45 m seat plane, drop to floor;
        measurement sigma multiplied by sqrt(3).  A stationary *standing*
        person (tall bbox, plausible foot ray) keeps the foot-point fix.
        (Flip SEATED_ASPECT_GT if your cameras show seated people as squat
        boxes instead.)

High-rate measurement sources (same localize/update path as the bbox)
======================================================================

* Frigate ``path_data`` — every event message repeats the FULL trail of
  normalized bottom-center points ``[[[x_n, y_n], ts], ...]`` sampled at
  5-8 Hz.  NEW points (per-frigate-id ts watermark; points older than 10 s
  dropped; max 20 per message) are scaled by the calibration ``image_size``,
  ray-cast to the floor and fed to the KF in ts order with their own
  timestamps.
* external Stage-C detections (``ingest_external`` / POST /ingest/detection
  in main.py) — a foot pixel in CALIBRATION resolution space under the
  synthetic stream id ``stagec:<camera>`` (no Frigate event id exists).
  ``pose-ankles`` -> floor plane, sigma x0.6 (ankle keypoints are precise);
  ``bbox-bottom`` -> floor plane, sigma x1.0; ``pose-hips`` -> hip midpoint
  of a seated/occluded person: ray ∩ z=0.55 m (HIP_SEATED_PLANE_Z = the
  0.45 m seat plane + ~0.10 m seat-surface-to-hip-joint offset, coherent
  with the seated heuristic), dropped to floor, sigma xsqrt(3) like the
  other plane-height-uncertain fallbacks.  Detections that cannot be
  localized (no calibration, ray miss, outside the walkable hull) still
  count as person evidence for the camera's room track.

Ordering approximation: the KF is now-based (one global 10 Hz predict loop;
updates do not re-propagate the state to the measurement's ts).  Points are
applied in ts order *within* a message, after that message's snapshot-box
fix; ``last_meas``/``last_signal`` are kept monotonic and backlog points
(dt below 1/RATE_MAX_HZ) are maximally rate-distrusted, so within-message
skew is absorbed as measurement noise.

Kalman filter
=============
Per-track constant-velocity KF, state [x, y, vx, vy], sigma_acc = 1.5 m/s^2
(discrete white-noise acceleration Q).  Predicted at 10 Hz; updated on
measurements with R = sigma_xy^2 * I where
``sigma_xy = max(0.15, 0.04 * range_m) * (0.5 / score)``.

* association: Mahalanobis gate at 3 sigma; non-gating measurements spawn
  candidate tracks confirmed after 3 hits (candidates are broadcast as
  ``room_only`` until confirmed).
* cross-camera merge: confirmed tracks within 0.8 m are merged (older id
  wins).
* coast control: position sigma saturates at 1.5 m (prediction freezes);
  > 5 s without measurement -> demoted to room-level (pos=null, room kept);
  Frigate ``stationary=true`` -> hold position with frozen covariance;
  > 30 s without any signal -> retired, but a new detection within 1 m of a
  track retired < 30 s ago re-binds the retired id.

Rate-aware noise (the sigma model above was tuned for ~1 Hz Frigate events;
path_data and /ingest/detection deliver 5-15 Hz).  Per track, an EMA of the
inter-measurement interval estimates the source rate:

* R inflation: measurement variance R is scaled linearly with the observed
  rate above RATE_NOMINAL_HZ (i.e. sigma x sqrt(rate)), so any stream
  contributes the same information *per second* as the 1 Hz baseline — a
  burst of near-simultaneous updates (which get almost no process noise Q
  between them) cannot over-tighten the covariance.
* "active" window: a track counts as active for ~ACTIVE_AGE_DT_MULT missed
  frames at its observed rate, floored at ACTIVE_AGE_S and capped at
  ACTIVE_AGE_MAX_S, instead of a fixed 1 s — so a 1 Hz source no longer
  flickers active->coasting between consecutive measurements while a 10 Hz
  source still demotes promptly.

Room assignment uses point-in-polygon with hysteresis: switch only after
1.5 s *or* 3 consecutive measurements inside the new polygon; a point on a
boundary (or inside no polygon) keeps the previous room.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import time
from typing import Callable, Optional

import cv2
import numpy as np
from shapely.geometry import Point

log = logging.getLogger("spatial.tracker")

# ---- tuning constants ------------------------------------------------------
SIGMA_ACC = 1.5            # m/s^2 process noise (constant-velocity model)
GATE_SIGMA = 3.0           # Mahalanobis association gate
CONFIRM_HITS = 3           # hits before a candidate track is confirmed
MERGE_DIST_M = 0.8         # cross-camera merge distance
COAST_SIGMA_MAX = 1.5      # m; covariance saturation -> freeze prediction
ACTIVE_AGE_S = 1.0         # floor of the "active" window (Track.active_window_s)
ACTIVE_AGE_DT_MULT = 3.0   # active window ~= this many missed frames at the
                           # observed measurement rate ...
ACTIVE_AGE_MAX_S = 2.0     # ... but never longer than this
DEMOTE_AFTER_S = 5.0       # > this without measurement -> room-level
ROOM_LINGER_S = 240.0      # room presence survives event end ("last seen in
                           # <room>") — a seated person stops generating
                           # Frigate events; vanishing instantly reads broken
RETIRE_AFTER_S = 30.0      # > this without any signal -> retire
REBIND_DIST_M = 1.0        # re-acquisition distance to a retired track
REBIND_WINDOW_S = 30.0     # ... retired less than this long ago
MIN_ABS_DZ = 0.05          # grazing-ray rejection
CROPPED_BOTTOM_FRAC = 0.02  # bbox bottom within 2% of frame bottom
CROPPED_PLANE_Z = 0.9      # hip-height plane for cropped-feet fallback
CROPPED_SIGMA_MULT = math.sqrt(3.0)
SEAT_PLANE_Z = 0.45        # seat plane for the seated heuristic
SEATED_ASPECT_GT = 1.6     # h/w threshold (see module docstring)
SEATED_SIGMA_MULT = math.sqrt(3.0)
HIP_SEATED_PLANE_Z = 0.55  # seated-hip plane: SEAT_PLANE_Z + ~0.10 m seat-
                           # surface-to-hip-joint offset (pose-hips ingest;
                           # coherent with the 0.45 m seated heuristic)
SIGMA_FLOOR = 0.15         # m
SIGMA_RANGE_COEFF = 0.04   # m per meter of range
ROOM_SWITCH_DWELL_S = 1.5
ROOM_SWITCH_COUNT = 3
AMBIGUOUS_HOLD_S = 2.0     # how long conf_reason stays multi_ambiguous
PAYLOAD_LOG_SAMPLES = 3
CLAMP_BEYOND_HULL_M = 0.5  # soft-clamp window beyond the +0.5 m hull: seat-
                           # plane projections of a person on furniture against
                           # a wall (couch, bed) overshoot just past it; snap
                           # those to the wall line instead of starving the KF
CLAMP_SIGMA_MULT = math.sqrt(3.0)  # clamped hits are direction-truncated

# ---- high-rate sources (path_data + /ingest/detection) ----------------------
PATH_MAX_AGE_S = 10.0      # path_data points older than this are ignored
PATH_MAX_POINTS = 20       # max path_data points consumed per message
PATH_STATE_TTL_S = 600.0   # GC for per-frigate-id path watermarks
RATE_NOMINAL_HZ = 1.0      # rate the sigma model was tuned at (Frigate ~1 Hz)
RATE_MAX_HZ = 20.0         # cap for the per-track rate estimate
RATE_MIN_DT_S = 1.0 / RATE_MAX_HZ  # dt clamp (also handles out-of-order ts)
RATE_EMA_ALPHA = 0.3       # EMA weight of the newest inter-measurement dt
EXTERNAL_SIGMA_MULT = {    # /ingest/detection per-source sigma scaling
    "pose-ankles": 0.6,            # ankle keypoints are precise
    "bbox-bottom": 1.0,            # plain Frigate-style foot point
    "pose-hips": SEATED_SIGMA_MULT,  # plane-height uncertainty, like seated
}


# ---- geometry helpers -------------------------------------------------------

def quat_wxyz_to_R(q) -> np.ndarray:
    """Unit quaternion [w, x, y, z] -> 3x3 rotation matrix (world->camera)."""
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        raise ValueError("zero-norm quaternion")
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def R_to_quat_wxyz(R: np.ndarray) -> list[float]:
    """3x3 rotation matrix -> unit quaternion [w, x, y, z] (Shepperd)."""
    R = np.asarray(R, dtype=np.float64)
    tr = float(np.trace(R))
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z])
    q /= np.linalg.norm(q)
    return [float(v) for v in q]


class CameraGeometry:
    """Calibrated camera: K/dist at ``image_size``, pose (R world->cam, C world)."""

    def __init__(self, K, dist, image_size, q_wxyz=None, R=None, t=None, C=None,
                 detect_res=None):
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3)
        d = np.asarray(dist if dist is not None else [], dtype=np.float64).reshape(-1)
        self.dist = d if d.size else np.zeros(5)
        self.image_size = (int(image_size[0]), int(image_size[1]))
        if R is not None:
            self.R = np.asarray(R, dtype=np.float64).reshape(3, 3)
        elif q_wxyz is not None:
            self.R = quat_wxyz_to_R(q_wxyz)
        else:
            raise ValueError("need q_wxyz or R")
        if C is not None:
            self.C = np.asarray(C, dtype=np.float64).reshape(3)
            self.t = -self.R @ self.C if t is None else np.asarray(t, np.float64).reshape(3)
        elif t is not None:
            self.t = np.asarray(t, dtype=np.float64).reshape(3)
            self.C = -self.R.T @ self.t
        else:
            raise ValueError("need t or C")
        self.detect_res = (
            (int(detect_res[0]), int(detect_res[1])) if detect_res else self.image_size
        )

    @classmethod
    def from_device(cls, dev: Optional[dict]) -> Optional["CameraGeometry"]:
        """Build from an apartment-model device dict; None if not calibrated."""
        if not dev:
            return None
        cam = dev.get("camera") or {}
        intr = cam.get("intrinsics") or {}
        extr = cam.get("extrinsics") or {}
        if not intr.get("K") or not extr.get("q_wxyz"):
            return None
        if extr.get("C") is None and extr.get("t") is None:
            return None
        video = cam.get("video") or {}
        image_size = intr.get("image_size") or video.get("main_res") or [1280, 720]
        detect_res = video.get("detect_res") or [1280, 720]
        try:
            return cls(
                K=intr["K"],
                dist=intr.get("dist"),
                image_size=image_size,
                q_wxyz=extr["q_wxyz"],
                t=extr.get("t"),
                C=extr.get("C"),
                detect_res=detect_res,
            )
        except Exception:
            log.exception("invalid camera calibration for device %s", dev.get("id"))
            return None

    # -- core math ------------------------------------------------------
    def detect_to_image(self, u: float, v: float) -> tuple[float, float]:
        sx = self.image_size[0] / self.detect_res[0]
        sy = self.image_size[1] / self.detect_res[1]
        return u * sx, v * sy

    def pixel_to_ray(self, u: float, v: float, from_detect: bool = True
                     ) -> tuple[np.ndarray, np.ndarray]:
        """Pixel -> (camera center C [world], unit ray direction d [world])."""
        if from_detect:
            u, v = self.detect_to_image(u, v)
        pts = np.array([[[u, v]]], dtype=np.float64)
        und = cv2.undistortPoints(pts, self.K, self.dist).reshape(2)
        d_cam = np.array([und[0], und[1], 1.0])
        d_world = self.R.T @ d_cam
        d_world = d_world / np.linalg.norm(d_world)
        return self.C.copy(), d_world

    def intersect_plane(self, d: np.ndarray, z: float = 0.0) -> Optional[np.ndarray]:
        """Ray C + s*d  ∩  plane Z=z.  None when grazing or behind the camera."""
        if abs(float(d[2])) < MIN_ABS_DZ:
            return None
        s = (z - self.C[2]) / float(d[2])
        if s <= 0:
            return None
        return self.C + s * d

    def project(self, X) -> Optional[np.ndarray]:
        """World point -> pixel in *image_size* coords (None if behind camera)."""
        X = np.asarray(X, dtype=np.float64).reshape(3)
        x_cam = self.R @ (X - self.C)
        if x_cam[2] <= 1e-9:
            return None
        rvec, _ = cv2.Rodrigues(self.R)
        px, _ = cv2.projectPoints(
            X.reshape(1, 1, 3), rvec, self.t.reshape(3, 1), self.K, self.dist
        )
        return px.reshape(2)


# ---- Frigate payload parsing ------------------------------------------------

def normalize_box(box, frame_w: float, frame_h: float
                  ) -> Optional[tuple[float, float, float, float]]:
    """Defensively normalize a Frigate bbox to (xmin, ymin, xmax, ymax).

    Handles:
      * dict {xmin, ymin, xmax, ymax} / {left, top, right, bottom}
      * dict {x, y, w, h}
      * 4-list [xmin, ymin, xmax, ymax]  (Frigate event payload default)
      * 4-list [x, y, w, h]
      * normalized 0..1 coordinates (scaled by frame size first)

    Ambiguous 4-lists (both readings fit in the frame) are read as
    [xmin, ymin, xmax, ymax] — the Frigate `frigate/events` convention.
    """
    if box is None:
        return None
    if isinstance(box, dict):
        try:
            if all(k in box for k in ("xmin", "ymin", "xmax", "ymax")):
                vals = (box["xmin"], box["ymin"], box["xmax"], box["ymax"])
                return _finish_box(vals, frame_w, frame_h, xyxy=True)
            if all(k in box for k in ("left", "top", "right", "bottom")):
                vals = (box["left"], box["top"], box["right"], box["bottom"])
                return _finish_box(vals, frame_w, frame_h, xyxy=True)
            if all(k in box for k in ("x", "y", "w", "h")):
                vals = (box["x"], box["y"], box["w"], box["h"])
                return _finish_box(vals, frame_w, frame_h, xyxy=False)
        except (TypeError, ValueError):
            return None
        return None
    try:
        a, b, c, d = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    # Normalized coordinates -> scale to pixels first.
    if max(abs(a), abs(b), abs(c), abs(d)) <= 1.001:
        a, b, c, d = a * frame_w, b * frame_h, c * frame_w, d * frame_h
    tol = 1.0
    xyxy_ok = c > a and d > b and c <= frame_w + tol and d <= frame_h + tol
    xywh_ok = c > 0 and d > 0 and a + c <= frame_w + tol and b + d <= frame_h + tol
    if xyxy_ok:  # ambiguous case included: prefer Frigate's xyxy convention
        return _clamp_box(a, b, c, d, frame_w, frame_h)
    if xywh_ok:
        return _clamp_box(a, b, a + c, b + d, frame_w, frame_h)
    # Neither fits cleanly — fall back to xyxy if monotonic, else give up.
    if c > a and d > b:
        return _clamp_box(a, b, c, d, frame_w, frame_h)
    return None


def _finish_box(vals, frame_w, frame_h, xyxy: bool):
    a, b, c, d = (float(v) for v in vals)
    if max(abs(a), abs(b), abs(c), abs(d)) <= 1.001:
        a, b, c, d = a * frame_w, b * frame_h, c * frame_w, d * frame_h
    if not xyxy:
        c, d = a + c, b + d
    if c <= a or d <= b:
        return None
    return _clamp_box(a, b, c, d, frame_w, frame_h)


def _clamp_box(x1, y1, x2, y2, frame_w, frame_h):
    x1 = min(max(x1, 0.0), frame_w)
    x2 = min(max(x2, 0.0), frame_w)
    y1 = min(max(y1, 0.0), frame_h)
    y2 = min(max(y2, 0.0), frame_h)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


# ---- single-detection localization ------------------------------------------

class Localization:
    __slots__ = ("pos", "sigma_xy", "method", "range_m")

    def __init__(self, pos: np.ndarray, sigma_xy: float, method: str, range_m: float):
        self.pos = pos            # [x, y, 0.0] world floor point
        self.sigma_xy = sigma_xy  # measurement std-dev (m)
        self.method = method      # "feet" | "cropped" | "seated" | "path"
                                  # | an /ingest/detection source name
        self.range_m = range_m


def localize_detection(geom: CameraGeometry,
                       box_xyxy: tuple[float, float, float, float],
                       score: float,
                       stationary: bool,
                       frame_size: Optional[tuple[float, float]] = None,
                       plausible: Optional[Callable[[np.ndarray], bool]] = None
                       ) -> Optional[Localization]:
    """Back-project one bbox (detect-res pixels) to a floor position.

    ``plausible`` (optional) is called with the candidate floor point
    ``[x, y, 0]``; returning False marks the foot-point ray-cast implausible
    (e.g. outside the walkable hull), which arms the seated fallback for
    tall+stationary boxes whose bottom is not cropped by the frame edge.
    """
    frame_w, frame_h = frame_size if frame_size else geom.detect_res
    x1, y1, x2, y2 = box_xyxy
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    aspect = h / w
    cropped = (frame_h - y2) <= CROPPED_BOTTOM_FRAC * frame_h
    # seated fallback candidate: tall + static, bottom genuinely visible
    seated_ok = stationary and aspect > SEATED_ASPECT_GT and not cropped

    if cropped:
        u, v, plane_z, method = cx, cy, CROPPED_PLANE_Z, "cropped"
    else:
        u, v, plane_z, method = cx, y2, 0.0, "feet"

    C, d = geom.pixel_to_ray(u, v, from_detect=True)
    X = geom.intersect_plane(d, z=plane_z)
    if seated_ok and (
            X is None
            or (plausible is not None
                and not plausible(np.array([X[0], X[1], 0.0])))):
        # Foot ray-cast implausible for a tall, static, un-cropped box: the
        # bbox bottom is likely a seat edge, not feet (a seated person's feet
        # ray overshoots).  Use the bbox-center ray on the seat plane instead.
        C, d = geom.pixel_to_ray(cx, cy, from_detect=True)
        X = geom.intersect_plane(d, z=SEAT_PLANE_Z)
        method = "seated"
    if X is None:
        return None
    pos = np.array([X[0], X[1], 0.0])  # drop to floor
    range_m = float(np.linalg.norm(X - C))
    score = min(max(float(score or 0.5), 0.05), 1.0)
    sigma = max(SIGMA_FLOOR, SIGMA_RANGE_COEFF * range_m) * (0.5 / score)
    if method == "cropped":
        sigma *= CROPPED_SIGMA_MULT
    elif method == "seated":
        sigma *= SEATED_SIGMA_MULT
    return Localization(pos, sigma, method, range_m)


def localize_pixel(geom: CameraGeometry, u: float, v: float, score: float,
                   *, plane_z: float = 0.0, from_detect: bool = True,
                   sigma_mult: float = 1.0, method: str = "feet"
                   ) -> Optional[Localization]:
    """Back-project a single pixel to a floor position.

    Same ray/plane/sigma model as :func:`localize_detection` but for a bare
    point (no bbox, so no cropped/seated heuristics): the ray through
    ``(u, v)`` is intersected with the ``plane_z`` plane and the hit dropped
    to the floor.  ``from_detect=False`` means (u, v) is already in
    calibration ``image_size`` coordinates — the convention for both
    ``path_data`` points (normalized * image_size) and /ingest/detection
    foot pixels.
    """
    C, d = geom.pixel_to_ray(u, v, from_detect=from_detect)
    X = geom.intersect_plane(d, z=plane_z)
    if X is None:
        return None
    pos = np.array([X[0], X[1], 0.0])
    range_m = float(np.linalg.norm(X - C))
    score = min(max(float(score or 0.5), 0.05), 1.0)
    sigma = max(SIGMA_FLOOR, SIGMA_RANGE_COEFF * range_m) * (0.5 / score)
    return Localization(pos, sigma * sigma_mult, method, range_m)


# ---- Kalman filter -----------------------------------------------------------

_H = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])


class Kalman2D:
    """Hand-rolled constant-velocity KF, state [x, y, vx, vy]."""

    def __init__(self, x: float, y: float, sigma0: float = 0.5,
                 sigma_acc: float = SIGMA_ACC):
        self.x = np.array([x, y, 0.0, 0.0], dtype=np.float64)
        self.P = np.diag([sigma0 ** 2, sigma0 ** 2, 1.0, 1.0]).astype(np.float64)
        self.sigma_acc = sigma_acc

    def pos(self) -> np.ndarray:
        return self.x[:2].copy()

    def vel(self) -> np.ndarray:
        return self.x[2:].copy()

    def pos_sigma(self) -> float:
        return math.sqrt(max(self.P[0, 0], self.P[1, 1], 0.0))

    def pos_cov(self) -> np.ndarray:
        return self.P[:2, :2].copy()

    def saturated(self) -> bool:
        return self.pos_sigma() >= COAST_SIGMA_MAX - 1e-9

    def predict(self, dt: float) -> None:
        """Advance the CV model; frozen once position sigma hits 1.5 m."""
        if dt <= 0 or self.saturated():
            return
        F = np.array(
            [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=np.float64,
        )
        q = self.sigma_acc ** 2
        dt2, dt3, dt4 = dt * dt, dt ** 3, dt ** 4
        Q = q * np.array(
            [
                [dt4 / 4, 0, dt3 / 2, 0],
                [0, dt4 / 4, 0, dt3 / 2],
                [dt3 / 2, 0, dt2, 0],
                [0, dt3 / 2, 0, dt2],
            ],
            dtype=np.float64,
        )
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        # saturate: clamp position variance at COAST_SIGMA_MAX^2
        cap = COAST_SIGMA_MAX ** 2
        if self.P[0, 0] > cap or self.P[1, 1] > cap:
            self.P[0, 0] = min(self.P[0, 0], cap)
            self.P[1, 1] = min(self.P[1, 1], cap)

    def mahalanobis(self, z, sigma_xy: float) -> float:
        z = np.asarray(z, dtype=np.float64).reshape(2)
        y = z - _H @ self.x
        S = _H @ self.P @ _H.T + (sigma_xy ** 2) * np.eye(2)
        return float(math.sqrt(max(y @ np.linalg.solve(S, y), 0.0)))

    def update(self, z, sigma_xy: float) -> None:
        z = np.asarray(z, dtype=np.float64).reshape(2)
        R = (sigma_xy ** 2) * np.eye(2)
        y = z - _H @ self.x
        S = _H @ self.P @ _H.T + R
        K = self.P @ _H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        ikh = np.eye(4) - K @ _H
        self.P = ikh @ self.P @ ikh.T + K @ R @ K.T  # Joseph form


# ---- room hysteresis ----------------------------------------------------------

class RoomHysteresis:
    """Switch rooms only after 1.5 s OR 3 consecutive measurements inside the
    new polygon; a measurement in no/ambiguous polygon keeps the current room."""

    def __init__(self, dwell_s: float = ROOM_SWITCH_DWELL_S,
                 count_n: int = ROOM_SWITCH_COUNT):
        self.dwell_s = dwell_s
        self.count_n = count_n
        self.current: Optional[str] = None
        self._cand: Optional[str] = None
        self._cand_since: float = 0.0
        self._cand_count: int = 0

    def force(self, room: Optional[str]) -> None:
        self.current = room
        self._cand = None
        self._cand_count = 0

    def update(self, room: Optional[str], now: float) -> Optional[str]:
        if room is None or room == self.current:
            self._cand = None
            self._cand_count = 0
            return self.current
        if self.current is None:
            self.current = room
            return self.current
        if room != self._cand:
            self._cand = room
            self._cand_since = now
            self._cand_count = 1
        else:
            self._cand_count += 1
        if (self._cand_count >= self.count_n
                or (now - self._cand_since) >= self.dwell_s):
            self.current = room
            self._cand = None
            self._cand_count = 0
        return self.current


# ---- tracks --------------------------------------------------------------------

class Track:
    def __init__(self, track_id: str, kind: str, now: float,
                 zone_id: Optional[str], camera: str):
        self.id = track_id
        self.kind = kind                  # "kf" | "room"
        self.created = now
        self.kf: Optional[Kalman2D] = None
        self.person: Optional[str] = None
        self.hits = 0
        self.confirmed = kind == "room"   # room tracks need no confirmation
        self.last_meas = now              # last precise measurement
        self.last_signal = now            # last *any* Frigate signal
        self.stationary = False
        self.last_score = 0.5
        self.source_cams: set[str] = {camera}
        self.rooms = RoomHysteresis()
        self.rooms.force(zone_id)
        self.ambiguous_until = 0.0
        self.frigate_ids: set[str] = set()
        self.ended = False                # room tracks: Frigate "end" seen
        self.meas_dt_ema: Optional[float] = None  # EMA inter-measurement dt (s)

    def active_window_s(self) -> float:
        """Measurement age below which the track reads "active".

        Rate-aware: ~ACTIVE_AGE_DT_MULT missed frames at the observed
        measurement rate, floored at ACTIVE_AGE_S (the legacy 1 Hz tuning)
        and capped at ACTIVE_AGE_MAX_S — a 1 Hz Frigate source no longer
        flickers to "coasting" right as the next measurement is due, while a
        10 Hz source still demotes promptly when it drops out.
        """
        if self.meas_dt_ema is None:
            return ACTIVE_AGE_S
        return min(ACTIVE_AGE_MAX_S,
                   max(ACTIVE_AGE_S, ACTIVE_AGE_DT_MULT * self.meas_dt_ema))


class RetiredTrack:
    __slots__ = ("id", "pos", "retired_at", "person")

    def __init__(self, track_id: str, pos: Optional[np.ndarray],
                 retired_at: float, person: Optional[str]):
        self.id = track_id
        self.pos = pos
        self.retired_at = retired_at
        self.person = person


# ---- the tracker -----------------------------------------------------------------

class Tracker:
    """Pure-python tracker core. No I/O; clock and model are injected.

    ``model_provider()`` must return an object with the ApartmentModel
    interface (``zone_for_camera``, ``camera_device``, ``zones_containing``).
    ``walkable_provider()`` (optional) returns the +0.5 m buffered walkable
    polygon, or None to skip the hull check.
    """

    def __init__(self,
                 model_provider: Callable[[], object],
                 clock: Callable[[], float] = time.time,
                 walkable_provider: Optional[Callable[[], object]] = None):
        self.model_provider = model_provider
        self.clock = clock
        self.walkable_provider = walkable_provider or (lambda: None)
        self.tracks: dict[str, Track] = {}
        self.retired: list[RetiredTrack] = []
        self._id_counter = itertools.count(1)
        self._geom_cache: dict[tuple, Optional[CameraGeometry]] = {}
        self._payloads_logged = 0
        self._last_predict: Optional[float] = None
        # per-frigate-id path_data watermark: max point ts already consumed
        # (every event message repeats the FULL trail)
        self._path_consumed: dict[str, float] = {}

    # ---- event ingestion ---------------------------------------------------
    def process_event(self, payload: dict, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        if self._payloads_logged < PAYLOAD_LOG_SAMPLES:
            self._payloads_logged += 1
            try:
                log.info("raw frigate/events payload sample %d: %s",
                         self._payloads_logged, json.dumps(payload)[:2000])
            except Exception:
                log.info("raw frigate/events payload sample %d (unserializable)",
                         self._payloads_logged)

        if not isinstance(payload, dict):
            return
        etype = payload.get("type")
        after = payload.get("after") or payload.get("before") or {}
        if not isinstance(after, dict):
            return
        if after.get("label") != "person":
            return
        camera = after.get("camera")
        if not camera:
            return
        model = self.model_provider()
        zone_id = model.zone_for_camera(camera)
        if zone_id is None:
            return  # unmapped camera (e.g. outdoor driveway) — ignore

        frigate_id = str(after.get("id") or f"{camera}:noid")
        sub_label = after.get("sub_label")
        if isinstance(sub_label, (list, tuple)):
            sub_label = sub_label[0] if sub_label else None
        score = after.get("score") or after.get("top_score") or 0.5
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.5
        stationary = bool(after.get("stationary", False))

        if etype == "end":
            self._handle_end(camera, frigate_id, now)
            return

        meas: Optional[Localization] = None
        clamp: Optional[tuple[tuple[float, float], float]] = None
        dev = model.camera_device(camera)
        geom = self._geom_for(camera, dev)
        if geom is not None:
            box = normalize_box(after.get("box"), geom.detect_res[0], geom.detect_res[1])
            if box is not None:
                meas = localize_detection(geom, box, score, stationary,
                                          plausible=self._in_walkable)
            if meas is not None:
                clamp = self._gate_or_clamp(meas.pos)
                if clamp is None:
                    log.debug("rejecting floor hit outside walkable hull: %s", meas.pos[:2])
                    meas = None

        if meas is not None:
            (cx, cy), smult = clamp
            self.ingest_position(
                camera=camera, zone_id=zone_id,
                pos_xy=(cx, cy),
                sigma=meas.sigma_xy * smult, score=score, stationary=stationary,
                person=sub_label, frigate_id=frigate_id, now=now,
            )
        else:
            self._update_room_track(camera, zone_id, frigate_id, sub_label,
                                    score, stationary, now)

        # 5-8 Hz path_data trail: extra measurements beyond the snapshot box
        # (see module docstring for the ts-ordering approximation).
        if geom is not None:
            self._consume_path_data(after, camera, zone_id, geom, frigate_id,
                                    sub_label, score, stationary, now)

    def _geom_for(self, camera: str, dev: Optional[dict]) -> Optional[CameraGeometry]:
        model = self.model_provider()
        key = (camera, getattr(model, "revision", None))
        if key not in self._geom_cache:
            self._geom_cache.clear()  # model changed; rebuild lazily
            self._geom_cache[key] = CameraGeometry.from_device(dev)
        return self._geom_cache[key]

    def _walkable_inner(self):
        """Un-buffered walls for DISPLAY clamping (the provider's hull carries
        +0.5 m measurement slop; the published dot must stay inside walls)."""
        hull = self.walkable_provider()
        if hull is None:
            return None
        if getattr(self, "_inner_cache_src", None) is not hull:
            self._inner_cache_src = hull
            # -0.5 undoes the measurement buffer; another -0.25 pins clamped
            # dots visibly INSIDE the wall (on-the-line reads as outside from
            # oblique dollhouse views)
            self._inner_cache = hull.buffer(-0.75)
        return self._inner_cache

    def _in_walkable(self, pos) -> bool:
        hull = self.walkable_provider()
        if hull is None:
            return True
        return bool(hull.covers(Point(float(pos[0]), float(pos[1]))))

    def _wall_line(self):
        """Approximate true-wall polygon (hull minus its +0.5 m measurement
        buffer), cached per hull object like ``_walkable_inner``."""
        hull = self.walkable_provider()
        if hull is None:
            return None
        if getattr(self, "_wall_cache_src", None) is not hull:
            self._wall_cache_src = hull
            self._wall_cache = hull.buffer(-0.5)
        return self._wall_cache

    def _gate_or_clamp(self, pos) -> Optional[tuple[tuple[float, float], float]]:
        """Walkable gate with a near-wall soft clamp.

        Returns ``((x, y), sigma_mult)`` or None (genuinely implausible).
        Hits inside the +0.5 m hull pass through unchanged.  Hits at most
        CLAMP_BEYOND_HULL_M beyond it snap to the nearest wall-line point
        with inflated noise — a person seated on furniture pushed against a
        wall projects just past that wall through the seat/hip plane, and
        hard-rejecting every such measurement starves the filter into
        room_only exactly while they sit still.  Anything farther out
        (unmodeled rooms, TV ghosts) stays rejected.
        """
        hull = self.walkable_provider()
        if hull is None:
            return (float(pos[0]), float(pos[1])), 1.0
        pt = Point(float(pos[0]), float(pos[1]))
        if hull.covers(pt):
            return (float(pos[0]), float(pos[1])), 1.0
        if pt.distance(hull) > CLAMP_BEYOND_HULL_M:
            return None
        wall = self._wall_line()
        target = hull if wall is None or wall.is_empty else wall
        from shapely.ops import nearest_points as _np
        q = _np(target, pt)[0]
        return (float(q.x), float(q.y)), CLAMP_SIGMA_MULT

    # ---- path_data ingestion --------------------------------------------------
    def _consume_path_data(self, after: dict, camera: str, zone_id: str,
                           geom: CameraGeometry, frigate_id: str,
                           person: Optional[str], score: float,
                           stationary: bool, now: float) -> None:
        """Feed NEW ``after.path_data`` points as extra KF measurements.

        Frigate repeats the FULL normalized bottom-center trail
        ``[[[x_n, y_n], ts], ...]`` (5-8 Hz samples) in every event message,
        so a per-frigate-id watermark (max ts ever seen) dedupes re-sends.
        Points older than PATH_MAX_AGE_S are dropped, at most
        PATH_MAX_POINTS (the newest) are consumed per message, and the
        survivors are processed in ts order with their own timestamps
        through the same localize -> walkable-gate -> ingest_position path
        as the snapshot box.
        """
        raw = after.get("path_data")
        if not isinstance(raw, (list, tuple)) or not raw:
            return
        watermark = self._path_consumed.get(frigate_id)
        max_ts = watermark
        fresh: list[tuple[float, float, float]] = []
        for entry in raw:
            try:
                xn, yn = float(entry[0][0]), float(entry[0][1])
                ts = float(entry[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if not (-0.001 <= xn <= 1.001 and -0.001 <= yn <= 1.001):
                continue  # not a normalized point; defensively skip
            if max_ts is None or ts > max_ts:
                max_ts = ts
            if watermark is not None and ts <= watermark:
                continue  # already consumed (full history is re-sent)
            if ts < now - PATH_MAX_AGE_S or ts > now + 1.0:
                continue  # stale (or clock-skewed) point
            fresh.append((ts, xn, yn))
        if max_ts is not None:
            self._path_consumed[frigate_id] = max_ts
        if not fresh:
            return
        fresh.sort(key=lambda p: p[0])
        fresh = fresh[-PATH_MAX_POINTS:]  # cap per message; keep the newest
        W, H = geom.image_size  # normalized coords -> calibration pixels
        for ts, xn, yn in fresh:
            meas = localize_pixel(geom, xn * W, yn * H, score,
                                  from_detect=False, method="path")
            if meas is None:
                continue
            clamp = self._gate_or_clamp(meas.pos)
            if clamp is None:
                continue
            (cx, cy), smult = clamp
            self.ingest_position(
                camera=camera, zone_id=zone_id,
                pos_xy=(cx, cy),
                sigma=meas.sigma_xy * smult, score=score, stationary=stationary,
                person=person, frigate_id=frigate_id, now=ts,
            )

    # ---- external (Stage C) ingestion ------------------------------------------
    def ingest_external(self, camera: str, ts: float, foot_px, score: float,
                        source: str, person: Optional[str] = None
                        ) -> Optional[Track]:
        """One POST /ingest/detection measurement (wire contract in main.py).

        ``foot_px`` is [u, v] in the camera's CALIBRATION resolution space
        (intrinsics ``image_size``; 1280x720 for the current cams), NOT
        detect-res.  Reuses the Stage-B localize/update path under the
        synthetic stream id ``stagec:<camera>`` (no Frigate event id
        exists).  Per-source geometry/noise:

          * ``pose-ankles`` — ankle midpoint: floor plane, sigma x0.6
          * ``bbox-bottom`` — bbox bottom-center: floor plane, sigma x1.0
          * ``pose-hips``   — hip midpoint of a seated/occluded person:
            ray ∩ z=HIP_SEATED_PLANE_Z (0.55 m = 0.45 m seat plane + ~0.10 m
            seat-to-hip-joint, coherent with the seated heuristic), dropped
            to the floor, sigma xsqrt(3) (plane-height uncertainty, exactly
            like the seated/cropped fallbacks)

        A detection that cannot be localized precisely (no calibration,
        off-frame pixel, ray miss, far outside the walkable hull) still
        counts as person evidence for the camera's room track; hits at most
        CLAMP_BEYOND_HULL_M beyond the hull soft-clamp to the wall line (see
        ``_gate_or_clamp``).  Returns the touched track, or None for
        unmapped cameras / invalid input.
        """
        if source not in EXTERNAL_SIGMA_MULT:
            log.warning("ingest_external: unknown source %r", source)
            return None
        try:
            ts = float(ts)
            u, v = float(foot_px[0]), float(foot_px[1])
        except (TypeError, ValueError, IndexError):
            return None
        model = self.model_provider()
        zone_id = model.zone_for_camera(camera)
        if zone_id is None:
            return None  # unmapped camera — same policy as the MQTT path
        stream_id = f"stagec:{camera}"
        geom = self._geom_for(camera, model.camera_device(camera))
        meas: Optional[Localization] = None
        clamp: Optional[tuple[tuple[float, float], float]] = None
        if geom is not None:
            W, H = geom.image_size
            if 0.0 <= u <= W and 0.0 <= v <= H:
                plane_z = HIP_SEATED_PLANE_Z if source == "pose-hips" else 0.0
                meas = localize_pixel(
                    geom, u, v, score, plane_z=plane_z, from_detect=False,
                    sigma_mult=EXTERNAL_SIGMA_MULT[source], method=source)
            if meas is not None:
                clamp = self._gate_or_clamp(meas.pos)
                if clamp is None:
                    log.debug("ingest_external: floor hit outside walkable hull")
                    meas = None
        if meas is not None:
            (cx, cy), smult = clamp
            return self.ingest_position(
                camera=camera, zone_id=zone_id,
                pos_xy=(cx, cy),
                sigma=meas.sigma_xy * smult, score=score, stationary=False,
                person=person, frigate_id=stream_id, now=ts)
        return self._update_room_track(camera, zone_id, stream_id, person,
                                       score, False, ts)

    # ---- precise measurements -----------------------------------------------
    def ingest_position(self, *, camera: str, zone_id: Optional[str],
                        pos_xy: tuple[float, float], sigma: float,
                        score: float = 0.7, stationary: bool = False,
                        person: Optional[str] = None,
                        frigate_id: Optional[str] = None,
                        now: Optional[float] = None) -> Track:
        """Associate one world-frame (x, y) measurement (also used by tests).

        Measurement noise is rate-scaled per candidate track (see
        ``_rate_scaled_sigma``) for both gating and the KF update, so 5-15 Hz
        sources (path_data, /ingest/detection) cannot over-tighten the
        covariance relative to the 1 Hz noise tuning.
        """
        now = self.clock() if now is None else now
        z = np.array(pos_xy, dtype=np.float64)

        gated: list[tuple[float, float, float, Track]] = []
        for tr in self.tracks.values():
            if tr.kind != "kf" or tr.kf is None:
                continue
            sig_eff, dt_ema = self._rate_scaled_sigma(tr, sigma, now)
            d = tr.kf.mahalanobis(z, sig_eff)
            if d < GATE_SIGMA:
                gated.append((d, sig_eff, dt_ema, tr))

        if gated:
            gated.sort(key=lambda item: item[0])
            _, sig_eff, dt_ema, track = gated[0]
            if len(gated) > 1:
                track.ambiguous_until = now + AMBIGUOUS_HOLD_S
            track.meas_dt_ema = dt_ema  # commit the rate estimate
            track.kf.update(z, sig_eff)
        else:
            track = self._spawn_or_rebind(z, zone_id, camera, person, now)

        track.hits += 1
        if track.hits >= CONFIRM_HITS:
            track.confirmed = True
        # monotonic: path_data points carry their own (older) timestamps
        track.last_meas = max(track.last_meas, now)
        track.last_signal = max(track.last_signal, now)
        track.stationary = stationary
        track.last_score = score
        track.source_cams.add(camera)
        if person:
            track.person = person
        if frigate_id:
            track.frigate_ids.add(frigate_id)
            # A precise fix for this Frigate object supersedes its room track.
            self.tracks.pop(f"room:{camera}:{frigate_id}", None)
        self._assign_room(track, z, zone_id, now)
        return track

    def _rate_scaled_sigma(self, track: Track, sigma: float, now: float
                           ) -> tuple[float, float]:
        """(effective sigma, next inter-measurement dt EMA) for ``track``.

        The sigma model (SIGMA_FLOOR / SIGMA_RANGE_COEFF) was tuned for ~1 Hz
        Frigate events; path_data and /ingest/detection deliver 5-15 Hz.
        Without correction, N same-second updates tighten the covariance ~Nx
        (the 10 Hz predict loop adds almost no Q between them) and the filter
        over-trusts bursts.  R (= sigma^2) is therefore scaled linearly with
        the observed rate above RATE_NOMINAL_HZ — sigma x sqrt(rate ratio) —
        which keeps the information contributed *per second* rate-independent.
        Out-of-order / duplicate timestamps clamp to RATE_MIN_DT_S, i.e. they
        read as max-rate and are maximally distrusted.  The EMA is only
        committed by the caller once the measurement actually associates.
        """
        dt_obs = max(now - track.last_meas, RATE_MIN_DT_S)
        if track.meas_dt_ema is None:
            dt_ema = dt_obs
        else:
            dt_ema = (RATE_EMA_ALPHA * dt_obs
                      + (1.0 - RATE_EMA_ALPHA) * track.meas_dt_ema)
        rate_hz = min(RATE_MAX_HZ, 1.0 / max(dt_ema, RATE_MIN_DT_S))
        if rate_hz > RATE_NOMINAL_HZ:
            sigma = sigma * math.sqrt(rate_hz / RATE_NOMINAL_HZ)
        return sigma, dt_ema

    def _spawn_or_rebind(self, z: np.ndarray, zone_id: Optional[str],
                         camera: str, person: Optional[str], now: float) -> Track:
        # re-acquisition: bind to a recently retired track within 1 m
        self._prune_retired(now)
        best: Optional[RetiredTrack] = None
        best_d = REBIND_DIST_M
        for rt in self.retired:
            if rt.pos is None:
                continue
            d = float(np.linalg.norm(rt.pos[:2] - z))
            if d <= best_d:
                best, best_d = rt, d
        if best is not None:
            self.retired.remove(best)
            track = Track(best.id, "kf", now, zone_id, camera)
            track.person = person or best.person
            track.confirmed = True  # it was a known track; resume immediately
            log.info("re-bound retired track %s at %s", best.id, z)
        else:
            track = Track(f"t{next(self._id_counter)}", "kf", now, zone_id, camera)
            track.person = person
        track.kf = Kalman2D(float(z[0]), float(z[1]))
        self.tracks[track.id] = track
        return track

    def _assign_room(self, track: Track, z: np.ndarray,
                     camera_zone: Optional[str], now: float) -> None:
        model = self.model_provider()
        containing = model.zones_containing(float(z[0]), float(z[1]))
        if track.rooms.current in containing:
            room = track.rooms.current  # boundary/ambiguous keeps previous
        elif containing:
            room = containing[0]
        else:
            room = None  # outside all polygons -> keep previous (hysteresis)
        if track.rooms.current is None and room is None:
            room = camera_zone
        track.rooms.update(room, now)

    # ---- room-level (stage A) -------------------------------------------------
    def _update_room_track(self, camera: str, zone_id: str, frigate_id: str,
                           person: Optional[str], score: float,
                           stationary: bool, now: float) -> Track:
        # If this Frigate object already feeds a KF track, just refresh signal.
        for tr in self.tracks.values():
            if tr.kind == "kf" and frigate_id in tr.frigate_ids:
                tr.last_signal = now
                tr.stationary = stationary
                return tr
        tid = f"room:{camera}:{frigate_id}"
        track = self.tracks.get(tid)
        if track is None:
            track = Track(tid, "room", now, zone_id, camera)
            self.tracks[tid] = track
        track.last_signal = now
        track.last_meas = now
        track.stationary = stationary
        track.last_score = score
        track.rooms.force(zone_id)
        if person:
            track.person = person
            self._last_person = (person, now)
        elif track.person is None:
            # identity carryover: Frigate face rec only fires on a clear
            # frontal view; in a single-occupant home a recognition within
            # the last 10 min is a strong prior for unlabeled sightings
            lp = getattr(self, "_last_person", None)
            if lp and now - lp[1] < 600.0:
                track.person = lp[0]
        track.frigate_ids.add(frigate_id)
        return track

    def _handle_end(self, camera: str, frigate_id: str, now: float) -> None:
        self._path_consumed.pop(frigate_id, None)  # done with this trail
        room_tid = f"room:{camera}:{frigate_id}"
        track = self.tracks.get(room_tid)
        if track is not None:
            track.ended = True
            return
        for tr in self.tracks.values():
            if tr.kind == "kf" and frigate_id in tr.frigate_ids:
                # No more signal from this camera; coast control takes over.
                tr.stationary = False
                return

    # ---- periodic prediction / lifecycle ---------------------------------------
    def predict_step(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        if self._last_predict is None:
            self._last_predict = now
        dt = now - self._last_predict
        self._last_predict = now

        for tr in list(self.tracks.values()):
            if tr.kind != "kf" or tr.kf is None:
                continue
            meas_age = now - tr.last_meas
            if tr.stationary:
                continue  # hold position, frozen covariance
            if meas_age > DEMOTE_AFTER_S:
                continue  # demoted to room-level; freeze the filter
            if dt > 0:
                tr.kf.predict(dt)

        self._merge_close_tracks(now)
        self._retire_stale(now)

    def _merge_close_tracks(self, now: float) -> None:
        kf_tracks = [t for t in self.tracks.values()
                     if t.kind == "kf" and t.kf is not None and t.confirmed]
        removed: set[str] = set()
        for a, b in itertools.combinations(kf_tracks, 2):
            if a.id in removed or b.id in removed:
                continue
            d = float(np.linalg.norm(a.kf.pos() - b.kf.pos()))
            if d >= MERGE_DIST_M:
                continue
            keep, drop = (a, b) if a.created <= b.created else (b, a)
            if drop.kf.pos_sigma() < keep.kf.pos_sigma():
                keep.kf = drop.kf  # keep the tighter estimate
            keep.source_cams |= drop.source_cams
            keep.frigate_ids |= drop.frigate_ids
            keep.person = keep.person or drop.person
            keep.hits += drop.hits
            keep.last_meas = max(keep.last_meas, drop.last_meas)
            keep.last_signal = max(keep.last_signal, drop.last_signal)
            keep.stationary = keep.stationary or drop.stationary
            removed.add(drop.id)
            log.info("merged track %s into %s (%.2f m apart)", drop.id, keep.id, d)
        for tid in removed:
            self.tracks.pop(tid, None)

    def _retire_stale(self, now: float) -> None:
        for tid, tr in list(self.tracks.items()):
            age = now - tr.last_signal
            if tr.kind == "room":
                if age > ROOM_LINGER_S:
                    self.tracks.pop(tid, None)
                continue
            if age > RETIRE_AFTER_S:
                pos = tr.kf.pos() if tr.kf is not None else None
                pos3 = np.array([pos[0], pos[1], 0.0]) if pos is not None else None
                if tr.confirmed:
                    self.retired.append(RetiredTrack(tr.id, pos3, now, tr.person))
                self.tracks.pop(tid, None)
                log.info("retired track %s (no signal for %.1f s)", tid, age)
        self._prune_retired(now)
        if self._path_consumed:  # GC watermarks of long-gone Frigate objects
            cutoff = now - PATH_STATE_TTL_S
            for fid in [f for f, ts in self._path_consumed.items()
                        if ts < cutoff]:
                del self._path_consumed[fid]

    def _prune_retired(self, now: float) -> None:
        self.retired = [rt for rt in self.retired
                        if now - rt.retired_at <= REBIND_WINDOW_S]

    # ---- output ------------------------------------------------------------------
    def snapshot(self, now: Optional[float] = None) -> list[dict]:
        """Track list in the wire format (activity fields left null; the
        rules engine fills them in)."""
        now = self.clock() if now is None else now
        out: list[dict] = []
        for tr in self.tracks.values():
            out.append(self._track_dict(tr, now))
        out.sort(key=lambda t: t["id"])
        return out

    def _track_dict(self, tr: Track, now: float) -> dict:
        meas_age = now - tr.last_meas
        state = "room_only"
        conf, conf_reason = 0.3, "room_only"
        pos = vel = cov = None

        if tr.kind == "kf" and tr.kf is not None and tr.confirmed:
            if tr.stationary and meas_age <= RETIRE_AFTER_S:
                state, conf, conf_reason = "stationary_held", 0.7, "good"
            elif meas_age <= tr.active_window_s():
                state = "active"
                conf = min(0.95, 0.5 + 0.45 * tr.last_score)
                conf_reason = "good"
            elif meas_age <= DEMOTE_AFTER_S:
                state = "coasting"
                conf = max(0.3, 0.6 - 0.06 * meas_age)
                conf_reason = "coasting"
            if state in ("active", "stationary_held") and now < tr.ambiguous_until:
                conf_reason = "multi_ambiguous"
                conf = min(conf, 0.5)
            if state != "room_only":
                p = tr.kf.pos()
                v = tr.kf.vel()
                pos = [round(float(p[0]), 3), round(float(p[1]), 3), 0.0]
                vel = [round(float(v[0]), 3), round(float(v[1]), 3)]
                cov = [[float(c) for c in row] for row in tr.kf.pos_cov()]
                inner = self._walkable_inner()
                if inner is not None:
                    from shapely.geometry import Point as _P
                    from shapely.ops import nearest_points as _np
                    pt = _P(pos[0], pos[1])
                    if not inner.covers(pt):
                        d = pt.distance(inner)
                        if d > 1.0:
                            # far outside (runaway coast) -> honest room-level
                            state, conf, conf_reason = "room_only", 0.3, "room_only"
                            pos = vel = cov = None
                        else:
                            # near-wall slop -> pin the dot to the wall line
                            q = _np(inner, pt)[0]
                            pos = [round(float(q.x), 3), round(float(q.y), 3), 0.0]

        return {
            "id": tr.id,
            "person": tr.person,
            "state": state,
            "pos": pos,
            "vel": vel,
            "cov": cov,
            "room": tr.rooms.current,
            "zone": None,
            "source_cams": sorted(tr.source_cams),
            "conf": round(float(conf), 3),
            "conf_reason": conf_reason,
            "activity": None,
            "activity_conf": None,
            "activity_source": None,
        }

    def active_count(self) -> int:
        return len(self.tracks)

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
       buffered +0.5 m (skipped when floor.json is absent).

    Fallbacks:
      * cropped feet — bbox bottom within 2% of the frame bottom: intersect
        the *bbox-center* ray with the z=0.9 m plane (hip height), then drop
        to the floor; measurement sigma multiplied by sqrt(3).
      * seated — bbox aspect (h/w) > 1.6 AND Frigate ``stationary``:
        intersect the bbox-center ray with the z=0.45 m seat plane, drop to
        floor.  (Threshold per spec; flip SEATED_ASPECT_GT if your cameras
        show seated people as squat boxes instead.)

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
ACTIVE_AGE_S = 1.0         # <= this since last measurement -> "active"
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
SIGMA_FLOOR = 0.15         # m
SIGMA_RANGE_COEFF = 0.04   # m per meter of range
ROOM_SWITCH_DWELL_S = 1.5
ROOM_SWITCH_COUNT = 3
AMBIGUOUS_HOLD_S = 2.0     # how long conf_reason stays multi_ambiguous
PAYLOAD_LOG_SAMPLES = 3


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
        self.method = method      # "feet" | "cropped" | "seated"
        self.range_m = range_m


def localize_detection(geom: CameraGeometry,
                       box_xyxy: tuple[float, float, float, float],
                       score: float,
                       stationary: bool,
                       frame_size: Optional[tuple[float, float]] = None
                       ) -> Optional[Localization]:
    """Back-project one bbox (detect-res pixels) to a floor position."""
    frame_w, frame_h = frame_size if frame_size else geom.detect_res
    x1, y1, x2, y2 = box_xyxy
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    aspect = h / w
    seated = stationary and aspect > SEATED_ASPECT_GT
    cropped = (frame_h - y2) <= CROPPED_BOTTOM_FRAC * frame_h

    if seated:
        u, v, plane_z, method = cx, cy, SEAT_PLANE_Z, "seated"
    elif cropped:
        u, v, plane_z, method = cx, cy, CROPPED_PLANE_Z, "cropped"
    else:
        u, v, plane_z, method = cx, y2, 0.0, "feet"

    C, d = geom.pixel_to_ray(u, v, from_detect=True)
    X = geom.intersect_plane(d, z=plane_z)
    if X is None:
        return None
    pos = np.array([X[0], X[1], 0.0])  # drop to floor
    range_m = float(np.linalg.norm(X - C))
    score = min(max(float(score or 0.5), 0.05), 1.0)
    sigma = max(SIGMA_FLOOR, SIGMA_RANGE_COEFF * range_m) * (0.5 / score)
    if method == "cropped":
        sigma *= CROPPED_SIGMA_MULT
    return Localization(pos, sigma, method, range_m)


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
        dev = model.camera_device(camera)
        geom = self._geom_for(camera, dev)
        if geom is not None:
            box = normalize_box(after.get("box"), geom.detect_res[0], geom.detect_res[1])
            if box is not None:
                meas = localize_detection(geom, box, score, stationary)
            if meas is not None and not self._in_walkable(meas.pos):
                log.debug("rejecting floor hit outside walkable hull: %s", meas.pos[:2])
                meas = None

        if meas is not None:
            self.ingest_position(
                camera=camera, zone_id=zone_id,
                pos_xy=(float(meas.pos[0]), float(meas.pos[1])),
                sigma=meas.sigma_xy, score=score, stationary=stationary,
                person=sub_label, frigate_id=frigate_id, now=now,
            )
        else:
            self._update_room_track(camera, zone_id, frigate_id, sub_label,
                                    score, stationary, now)

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
            self._inner_cache = hull.buffer(-0.5)
        return self._inner_cache

    def _in_walkable(self, pos) -> bool:
        hull = self.walkable_provider()
        if hull is None:
            return True
        return bool(hull.covers(Point(float(pos[0]), float(pos[1]))))

    # ---- precise measurements -----------------------------------------------
    def ingest_position(self, *, camera: str, zone_id: Optional[str],
                        pos_xy: tuple[float, float], sigma: float,
                        score: float = 0.7, stationary: bool = False,
                        person: Optional[str] = None,
                        frigate_id: Optional[str] = None,
                        now: Optional[float] = None) -> Track:
        """Associate one world-frame (x, y) measurement (also used by tests)."""
        now = self.clock() if now is None else now
        z = np.array(pos_xy, dtype=np.float64)

        gated: list[tuple[float, Track]] = []
        for tr in self.tracks.values():
            if tr.kind != "kf" or tr.kf is None:
                continue
            d = tr.kf.mahalanobis(z, sigma)
            if d < GATE_SIGMA:
                gated.append((d, tr))

        if gated:
            gated.sort(key=lambda item: item[0])
            track = gated[0][1]
            if len(gated) > 1:
                track.ambiguous_until = now + AMBIGUOUS_HOLD_S
            track.kf.update(z, sigma)
        else:
            track = self._spawn_or_rebind(z, zone_id, camera, person, now)

        track.hits += 1
        if track.hits >= CONFIRM_HITS:
            track.confirmed = True
        track.last_meas = now
        track.last_signal = now
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
            elif meas_age <= ACTIVE_AGE_S:
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

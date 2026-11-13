"""Build a head-free cloud from the current registered depth in the detector worker.

Only a qualified empty observation can feed the background. A person needs a
foreground component supported by their current measured torso joints and a
conservative exclusion for the head and upper neck. Every emitted point is an
actual depth pixel from that bundle, times step 2's depth scale (the setup's
depth_scale, as perceive.py scales the lift's depth; 1.0 changes nothing).
"""
import base64
import io
import math
import time
import zipfile

HEIGHT, WIDTH = 400, 640
DEPTH_SCALE_RANGE = (0.90, 1.10)   # contracts.py's: this file loads in the detector's venv on its own
N = HEIGHT * WIDTH
EMPTY_S, LEARN_FRAMES, RELEARN_S = 8.0, 12, 120.0
CUT_MM, NEAR_LIMB_MM, EDGE_MM = 120, 40, 60
NEAR_MM, FAR_MM = 300, 5000
GATE_M, MIN_BODY_PX = 0.9, 300
SPACING_M, MAX_POINTS = 0.013, 16384
TORSO_SEED_PX, TORSO_DEPTH_MM = 8, 80
MIN_TORSO_SEEDS = 2
SHOULDERS = ("left_shoulder", "right_shoulder")
HIPS = ("left_hip", "right_hip")
TORSO = SHOULDERS + HIPS
# These tubes lower the foreground threshold around measured limbs. They never supply geometry.
NEAR_LIMBS = (("left_hip", "left_knee", 0.11), ("left_knee", "left_ankle", 0.09),
              ("right_hip", "right_knee", 0.11), ("right_knee", "right_ankle", 0.09),
              ("left_shoulder", "left_elbow", 0.08), ("left_elbow", "left_wrist", 0.07),
              ("right_shoulder", "right_elbow", 0.08), ("right_elbow", "right_wrist", 0.07))
BODY_JOINTS = frozenset(TORSO).union(name for a, b, _ in NEAR_LIMBS for name in (a, b))


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _b64(array):
    return base64.b64encode(array.tobytes()).decode("ascii")


def head_exclusion(evidence, *, frame_key, binding):
    """Return outward-rounded depth-image bounds for the private head and upper neck.

    The pipeline supplies a conservative bounding rectangle for the whole region
    in rectified depth coordinates and separate alignment, localization and
    inter-capture motion bounds in pixels. The rectangle can extend outside the
    image. A partially known extent must be reported as missing evidence.
    """
    if (not isinstance(evidence, dict) or frame_key is None or binding is None
            or evidence.get("frame_key") != frame_key or evidence.get("binding") != binding):
        return None
    bounds = evidence.get("bounds_px")
    margins = [evidence.get(key) for key in ("alignment_px", "localization_px", "motion_px")]
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4
            or not all(_finite(x) for x in bounds) or not all(_finite(x) and x >= 0 for x in margins)):
        return None
    left, top, right, bottom = bounds
    if left >= right or top >= bottom:
        return None
    margin = sum(margins)
    expanded = left - margin, top - margin, right + margin, bottom + margin
    if not all(math.isfinite(x) for x in expanded):
        return None
    left, top, right, bottom = expanded
    return (min(WIDTH, max(0, math.floor(left))), min(HEIGHT, max(0, math.floor(top))),
            min(WIDTH, max(0, math.floor(right) + 1)), min(HEIGHT, max(0, math.floor(bottom) + 1)))


class BodyMaker:
    """One registered rectified-left depth camera. State lives only in the worker."""

    def __init__(self, np, cv2, *, left_k, room_from_left):
        self.np, self.cv2 = np, cv2
        self.fx, self.fy, self.cx, self.cy = (float(x) for x in left_k[:4])
        self.R, self.t = np.asarray(room_from_left[0], float), np.asarray(room_from_left[1], float)
        if (not all(math.isfinite(x) for x in (self.fx, self.fy, self.cx, self.cy))
                or min(self.fx, self.fy) <= 0 or self.R.shape != (3, 3) or self.t.shape != (3,)
                or not np.isfinite(self.t).all()
                or not np.allclose(self.R.T @ self.R, np.eye(3), atol=1e-4)
                or not np.isclose(np.linalg.det(self.R), 1, atol=1e-4)):
            raise ValueError("invalid depth registration")
        self.background = None
        self.learned_at = None
        self.stats = {}
        self.interrupt_learning()

    def interrupt_learning(self):
        self.empty_since = None
        self.learning = []

    def _learn(self, raw, now):
        np = self.np
        if self.empty_since is None:
            self.empty_since = now
        if now - self.empty_since < EMPTY_S:
            return
        if self.learned_at is not None and now - self.learned_at < RELEARN_S:
            return
        self.learning.append(raw)
        if len(self.learning) < LEARN_FRAMES:
            return
        stack = np.stack(self.learning)
        valid = (stack >= NEAR_MM) & (stack <= FAR_MM)
        seen = valid.sum(axis=0) >= LEARN_FRAMES // 2
        background = np.zeros(raw.shape, np.float32)
        background[seen] = np.nanmedian(np.where(valid[:, seen], stack[:, seen], np.nan), axis=0)
        self.background, self.learned_at, self.learning = background, now, []

    def _camera_points(self, depth, rows, cols):
        z = depth.astype(float) / 1000
        return self.np.column_stack(((cols - self.cx) * z / self.fx, (rows - self.cy) * z / self.fy, z))

    def _project(self, points):
        return self.np.column_stack((self.fx * points[:, 0] / points[:, 2] + self.cx,
                                     self.fy * points[:, 1] / points[:, 2] + self.cy))

    def _window(self, centre):
        np = self.np
        corners = np.array([[centre[0] + dx, centre[1] + dy, z] for dx in (-GATE_M, GATE_M)
                            for dy in (-GATE_M, GATE_M) for z in (0.02, 2.4)])
        camera = (corners - self.t) @ self.R
        if (camera[:, 2] < 0.3).any():
            return 0, HEIGHT, 0, WIDTH
        pixels = self._project(camera)
        u0, v0 = np.maximum(0, np.floor(pixels.min(axis=0)) - 12).astype(int)
        u1, v1 = np.minimum([WIDTH, HEIGHT], np.ceil(pixels.max(axis=0)) + 12).astype(int)
        if min(u1 - u0, v1 - v0) < 16:
            return 0, HEIGHT, 0, WIDTH
        return v0, v1, u0, u1

    def step(self, depth_mm, *, observation, person=None, frame_key=None, binding=None, now):
        """Return the ready-frame fields or a clear reason for the parent publisher."""
        self.stats = {}
        if observation != "empty":
            self.interrupt_learning()
        if observation not in ("empty", "person"):
            return None, "pose_unavailable"
        np = self.np
        raw = np.asarray(depth_mm)
        if raw.shape != (HEIGHT, WIDTH) or raw.dtype != np.dtype("<u2"):
            self.interrupt_learning()
            return None, "insufficient_depth"
        if observation == "empty":
            self._learn(raw.copy(), now)
            return None, "warming_background" if self.background is None else "empty"
        if not isinstance(person, dict):
            return None, "pose_unavailable"
        track_id = person.get("track_id")
        if not isinstance(track_id, str) or not 0 < len(track_id) <= 64:
            return None, "pose_unavailable"

        exclusion = head_exclusion(person.get("head_evidence"), frame_key=frame_key, binding=binding)
        if exclusion is None:
            return None, "head_mask_unavailable"
        if self.background is None:
            return None, "warming_background"
        joints = {}
        left, top, right, bottom = exclusion
        for name, joint in (person.get("joints") or {}).items():
            if name not in BODY_JOINTS or not isinstance(joint, (tuple, list)) or len(joint) != 2:
                continue
            xyz, status = joint
            if status == "supported" and isinstance(xyz, (tuple, list)) and len(xyz) == 3 and all(_finite(x) for x in xyz):
                point = (np.asarray(xyz) - self.t) @ self.R
                if not NEAR_MM <= point[2] * 1000 <= FAR_MM:
                    continue
                u, v = self._project(point[None, :])[0]
                if left - TORSO_SEED_PX <= u < right + TORSO_SEED_PX and top - TORSO_SEED_PX <= v < bottom + TORSO_SEED_PX:
                    continue
                joints[name] = point
        torso = {name: joints[name] for name in TORSO if name in joints}
        if len(torso) < MIN_TORSO_SEEDS:
            return None, "pose_unavailable"

        centre = (np.mean(list(torso.values()), axis=0) @ self.R.T + self.t)[:2]
        v0, v1, u0, u1 = self._window(centre)
        depth = raw[v0:v1, u0:u1].astype(np.float32)
        bg = self.background[v0:v1, u0:u1]
        if left < u1 and right > u0 and top < v1 and bottom > v0:
            depth[max(0, top - v0):bottom - v0, max(0, left - u0):right - u0] = 0
        return self._person(depth, bg, v0, u0, joints, torso, centre, person)

    def _person(self, depth, bg, v0, u0, joints, torso, centre, person):
        np, cv2 = self.np, self.cv2
        valid = (depth >= NEAR_MM) & (depth <= FAR_MM) & (bg > 0)
        delta = bg - depth
        foreground = valid & (delta >= CUT_MM)
        segments = [(joints[a], joints[b], radius) for a, b, radius in NEAR_LIMBS if a in joints and b in joints]
        if segments:
            rows, cols = np.nonzero(valid & (delta >= NEAR_LIMB_MM) & (delta < CUT_MM))
            points = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
            near = np.zeros(len(points), bool)
            for a, b, radius in segments:
                near |= _seg_dist(np, points, a, b) < radius
            foreground[rows[near], cols[near]] = True
        density = cv2.boxFilter(foreground.astype(np.float32), -1, (5, 5), normalize=False, borderType=cv2.BORDER_CONSTANT)
        rows, cols = np.nonzero(foreground & (density >= 15))
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        room = camera @ self.R.T + self.t
        inside = (np.linalg.norm(room[:, :2] - centre, axis=1) <= GATE_M) & (room[:, 2] >= 0.02) & (room[:, 2] <= 2.4)
        eligible = np.zeros(depth.shape, bool)
        eligible[rows[inside], cols[inside]] = True

        component, seeds = self._torso_component(depth, eligible, torso, v0, u0)
        if component is None or int(component.sum()) < MIN_BODY_PX:
            return None, "insufficient_depth"
        rows, cols = np.nonzero(component)
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        room = camera @ self.R.T + self.t
        # Pick a source pixel in every occupied cell. Keep row-major key ordering and spread the cap across the cloud.
        _, selected = np.unique(np.floor(room / SPACING_M).astype(np.int32), axis=0, return_index=True)
        selected.sort()
        if len(selected) > MAX_POINTS:
            selected = selected[np.linspace(0, len(selected) - 1, MAX_POINTS, dtype=int)]
        rows, cols, camera, room = rows[selected], cols[selected], camera[selected], room[selected]
        kept = {(r, c) for r, c in zip(rows.tolist(), cols.tolist())}
        supported = {}
        for name, (r, c) in seeds.items():
            nearby = [(r + dr, c + dc) for dr in range(-TORSO_SEED_PX, TORSO_SEED_PX + 1)
                      for dc in range(-TORSO_SEED_PX, TORSO_SEED_PX + 1)
                      if (r + dr, c + dc) in kept and abs(depth[r + dr, c + dc] - depth[r, c]) <= EDGE_MM]
            if nearby:
                supported[name] = min(nearby, key=lambda pixel: (pixel[0] - r) ** 2 + (pixel[1] - c) ** 2)
        if len(set(supported.values())) < MIN_TORSO_SEEDS:
            return None, "insufficient_depth"

        xyz_mm = np.rint(room * 1000)
        if (xyz_mm < -32768).any() or (xyz_mm > 32767).any():
            return None, "scope_changed"
        normals = self._normals(depth, component, rows, cols, camera, v0, u0)
        keys = (rows + v0) * WIDTH + cols + u0
        heading = person.get("facing_deg")
        shoulders = self._midpoint(joints, supported, SHOULDERS)
        pelvis = self._midpoint(joints, supported, HIPS)
        self.stats = {"measured": len(keys), "eligible": int(component.sum())}
        return {"track_id": person["track_id"],
                "points": {"count": len(keys), "key": _b64(keys.astype("<u4")),
                           "position_mm": _b64(xyz_mm.astype("<i2")),
                           "normal": _b64(np.clip(np.rint(normals * 127), -127, 127).astype("i1")),
                           "kind": _b64(np.zeros(len(keys), np.uint8))},
                "facing_deg": heading if all(name in supported for name in TORSO)
                and _finite(heading) and 0 <= heading < 360 else None,
                "shoulder_midpoint_m": shoulders.tolist() if shoulders is not None else None,
                "pelvis_z_m": float(pelvis[2]) if pelvis is not None else None}, None

    def _midpoint(self, joints, supported, pair):
        if all(name in supported for name in pair):
            return ((joints[pair[0]] + joints[pair[1]]) / 2) @ self.R.T + self.t
        return None

    def _torso_component(self, depth, eligible, torso, v0, u0):
        np, cv2 = self.np, self.cv2
        h, w = depth.shape
        seeds = {}
        for name, joint in torso.items():
            cx, cy = self._project(joint[None, :])[0] - (u0, v0)
            col, row = round(float(cx)), round(float(cy))
            a, b = max(0, row - TORSO_SEED_PX), min(h, row + TORSO_SEED_PX + 1)
            l, r = max(0, col - TORSO_SEED_PX), min(w, col + TORSO_SEED_PX + 1)
            if a >= b or l >= r:
                continue
            patch = eligible[a:b, l:r] & (np.abs(depth[a:b, l:r] - joint[2] * 1000) <= TORSO_DEPTH_MM)
            ys, xs = np.nonzero(patch)
            if len(ys):
                nearest = np.argmin((ys + a - cy) ** 2 + (xs + l - cx) ** 2)
                seeds[name] = (int(ys[nearest] + a), int(xs[nearest] + l))
        if len(set(seeds.values())) < MIN_TORSO_SEEDS:
            return None, {}
        # OpenCV's floating-range flood compares each adjacent pair's measured depths, leaving blocked rays untouched.
        mask = np.ones((h + 2, w + 2), np.uint8)
        mask[1:-1, 1:-1] = ~eligible
        labels = mask[1:-1, 1:-1]
        for label, (row, col) in enumerate(seeds.values(), start=2):
            if labels[row, col] == 0:
                cv2.floodFill(depth, mask, (col, row), 0, EDGE_MM, EDGE_MM,
                              flags=8 | cv2.FLOODFILL_MASK_ONLY | (label << 8))
        groups = {}
        for name, (row, col) in seeds.items():
            groups.setdefault(int(labels[row, col]), {})[name] = (row, col)
        candidates = [(label, points) for label, points in groups.items()
                      if len(set(points.values())) >= MIN_TORSO_SEEDS]
        if len(candidates) != 1:
            return None, {}
        label, points = candidates[0]
        return labels == label, points

    def _normals(self, depth, mask, rows, cols, camera, v0, u0):
        np = self.np
        h, w = depth.shape
        lo, hi = np.maximum(cols - 1, 0), np.minimum(cols + 1, w - 1)
        up, dn = np.maximum(rows - 1, 0), np.minimum(rows + 1, h - 1)
        z = depth[rows, cols]
        neighbors = ((rows, lo), (rows, hi), (up, cols), (dn, cols))
        valid = np.logical_and.reduce([mask[y, x] & (np.abs(depth[y, x] - z) <= EDGE_MM) for y, x in neighbors])
        a, b, c, d = (self._camera_points(depth[y, x], y + v0, x + u0) for y, x in neighbors)
        normal = np.cross(b - a, d - c)
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
        normal[np.einsum("ij,ij->i", normal, camera) > 0] *= -1
        normal[~valid] = 0
        return normal @ self.R.T


def depth_of(np, data, scale=1.0):
    """Read the retained bundle's rectified-left unsigned millimetre plane, times step 2's depth scale."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo("depth_mm.npy").file_size > 1 << 21:
            raise ValueError("depth_mm is too large")
        depth = np.load(io.BytesIO(archive.read("depth_mm.npy")), allow_pickle=False)
    if depth.dtype != np.dtype("<u2") or depth.shape != (HEIGHT, WIDTH):
        raise ValueError("depth_mm must be 400x640 uint16")
    if scale != 1.0:   # as perceive.py's table: rounded half to even, 0 stays 0
        depth = np.where(depth > 0, np.clip(np.rint(depth * scale), 1, 65535), 0).astype("<u2")
    return depth


class BodyChannel:
    """Handle body commands on the last bundle and echo private request identity to the publisher."""

    def __init__(self):
        self.maker = self.binding = None
        self.depth_scale = 1.0

    def handle(self, header, kept):
        """kept is the last detection's (worker id, bundle bytes, pipeline frame key)."""
        if header.get("kind") == "setup_body":
            setup = header.get("setup") or {}
            self.maker, self.binding = None, setup.get("binding")
            try:
                import cv2
                import numpy as np
                cv2.setNumThreads(1)
                if self.binding is None:
                    raise ValueError("missing body calibration binding")
                scale = setup.get("depth_scale", 1.0)
                if type(scale) not in (int, float) or not DEPTH_SCALE_RANGE[0] <= scale <= DEPTH_SCALE_RANGE[1]:
                    raise ValueError("depth scale outside its range")
                self.depth_scale = float(scale)
                self.maker = BodyMaker(np, cv2, left_k=setup["left_k"], room_from_left=setup["room_from_left"])
                return {"body_ready": True, "binding": self.binding}
            except Exception as exc:
                return {"body_refused": type(exc).__name__, "binding": self.binding}
        body_id, frame_key, binding = header.get("id"), header.get("frame_key"), header.get("binding")
        reply = {"body_id": body_id, "request_id": header.get("request_id"), "frame_key": frame_key, "binding": binding}
        if self.maker is None:
            return {**reply, "error": "NotSetUp"}
        if binding != self.binding:
            return {**reply, "frame": None, "why": "scope_changed"}
        if header.get("reset_learning"):
            self.maker.interrupt_learning()
        observation = header.get("observation")
        if observation not in ("empty", "person"):
            self.maker.interrupt_learning()
            return {**reply, "frame": None, "why": "pose_unavailable"}
        if kept is None or frame_key is None or kept[0] != body_id or kept[2] != frame_key:
            self.maker.interrupt_learning()
            return {**reply, "frame": None, "why": "stale"}
        try:
            start = time.perf_counter()
            frame, why = self.maker.step(depth_of(self.maker.np, kept[1], self.depth_scale), observation=observation,
                                         person=header.get("person"), now=float(header["now"]),
                                         frame_key=frame_key, binding=binding)
            return {**reply, "frame": frame, "why": why, "compute_s": round(time.perf_counter() - start, 4),
                    "stats": self.maker.stats}
        except Exception as exc:  # Never put exception text or input data on the pipe.
            self.maker.interrupt_learning()
            return {**reply, "error": type(exc).__name__}


def _seg_dist(np, points, a, b):
    direction = b - a
    length_sq = max(float(direction @ direction), 1e-12)
    fraction = np.clip((points - a) @ direction / length_sq, 0, 1)
    return np.linalg.norm(points - (a + fraction[:, None] * direction), axis=1)

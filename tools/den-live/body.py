"""Build a person's cloud from the current registered depth in the detector worker.

Only a qualified empty observation can feed the background. A person needs a
foreground component supported by their current measured torso joints and,
unless the owner's profile shows the head ("head": "shown", HEAD_SHOWN), a
conservative exclusion for the head and upper neck. Every emitted point is an
actual depth pixel from that bundle, times step 2's depth scale (the setup's
depth_scale, as perceive.py scales the lift's depth; 1.0 changes nothing).
"""
import base64
import io
import math
import time
import zipfile
from collections import deque

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
# What the person holds: a holdable thing's instance mask from the detector worker's segmentation pass (held_seg.py),
# kept where it meets measured depth within WRIST_REACH_M of a wrist. Its pixels become KIND_OBJECT points, actual depth
# pixels like every other, with the detector's label, once the same label held in HELD_FRAMES of the last HELD_OF
# person frames.
KIND_OBJECT = 5
HOLDABLE = frozenset(("book", "cell phone", "cup", "bottle", "remote", "laptop", "scissors", "mouse"))
WRISTS = ("left_wrist", "right_wrist")
WRIST_REACH_M, OBJECT_MIN_PX, OBJECT_MAX_POINTS = 0.32, 40, 4096
HELD_FRAMES, HELD_OF = 2, 3
MAX_POLYGON = 256
# When the seg pass names nothing at the hand (a phone is about 20 px in the RGB frame, too small for it), what the hand
# holds by depth alone, as den-live had it on 2026-11-11 (1a4268f7): measured pixels within WRIST_REACH_M of a wrist
# whose elbow is measured too, that the body's own capsules don't explain (OBJECT_MARGIN_M beyond them), the largest
# 4-connected region of at least DEPTH_OBJECT_MIN_M2 (phone-sized), while the hands move slower than HAND_STILL_MPS. It
# holds on the same 2-of-3 rule, unnamed: kind 5 points, and object null for Home's "Held object" marker.
OBJECT_MARGIN_M, DEPTH_OBJECT_MIN_M2, HAND_STILL_MPS = 0.025, 0.006, 0.3
UNNAMED = ""   # the hold rule's key for an object held by depth alone


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _b64(array):
    return base64.b64encode(array.tobytes()).decode("ascii")


HEAD_SHOWN = "shown"


def head_shown(evidence, *, frame_key, binding):
    """True when this frame's evidence says the owner's profile shows the head: no envelope and no cut, the whole
    measured person. Bound to the frame and binding like any evidence."""
    return (isinstance(evidence, dict) and frame_key is not None and binding is not None
            and evidence.get("frame_key") == frame_key and evidence.get("binding") == binding
            and evidence.get("head") == HEAD_SHOWN and "cut" not in evidence and "bounds_px" not in evidence)


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


def torso_cut(evidence, *, frame_key, binding):
    """(shoulder_px, axis_px, margin_m) of the private shoulder-line cut, or None.

    In the rectified-left depth image, the line through the shoulder midpoint across the torso's own axis (hips to
    shoulders) splits the person: the head and upper neck are always on the far side from the hips, upright, bowed,
    reclined or turned away. Everything on that side, and within margin_m of the line on the hips' side, is cut. It is
    drawn from 2D keypoints, which stay right where their depth lifts slip onto the sofa or the wall behind."""
    if (not isinstance(evidence, dict) or frame_key is None or binding is None
            or evidence.get("frame_key") != frame_key or evidence.get("binding") != binding):
        return None
    cut = evidence.get("cut")
    if not isinstance(cut, dict):
        return None
    shoulder, axis, margin = cut.get("shoulder_px"), cut.get("axis_px"), cut.get("margin_m")
    shoulders, ceiling = cut.get("shoulders_px"), cut.get("ceiling_m")

    def pixel(p):
        return (isinstance(p, (list, tuple)) and len(p) == 2 and all(_finite(x) for x in p)
                and -WIDTH <= p[0] <= 2 * WIDTH and -HEIGHT <= p[1] <= 2 * HEIGHT)

    if (not pixel(shoulder) or not isinstance(axis, (list, tuple)) or len(axis) != 2 or not all(_finite(x) for x in axis)
            or not _finite(margin) or not 0 < margin <= 1.0
            or not isinstance(shoulders, (list, tuple)) or len(shoulders) != 2 or not all(pixel(p) for p in shoulders)
            or not _finite(ceiling) or not -1.0 <= ceiling <= 3.0):
        return None
    length = math.hypot(*axis)
    if not 0.999 <= length <= 1.001:
        return None
    return ((float(shoulder[0]), float(shoulder[1])), (axis[0] / length, axis[1] / length), float(margin),
            tuple((float(p[0]), float(p[1])) for p in shoulders), float(ceiling))


CUT_BAND_PX, CUT_MIN_DEPTH_PX, CUT_MAX_PX = 12, 8, 200


def cut_pixels(cut, depth_mm, fx):
    """(shoulder_px, axis_px, margin_px, ceiling_m) on this frame, or None: the margin in pixels at the nearest 5% of
    the measured depth across the shoulder band (both shoulders and the midpoint, CUT_BAND_PX around), so a head or
    neck nearer the camera than the shoulders still gets enough pixels. A margin wider than CUT_MAX_PX (someone close
    to the camera) is None, never capped: a capped cut would be thinner than the qualified one."""
    if cut is None:
        return None
    (u, v), axis, margin_m, shoulders, ceiling = cut
    us, vs = [u] + [p[0] for p in shoulders], [v] + [p[1] for p in shoulders]
    top, bottom = max(0, int(min(vs)) - CUT_BAND_PX), max(0, int(max(vs)) + CUT_BAND_PX + 1)
    left, right = max(0, int(min(us)) - CUT_BAND_PX), max(0, int(max(us)) + CUT_BAND_PX + 1)
    band = depth_mm[top:bottom, left:right]
    measured = sorted(int(x) for x in band.ravel() if NEAR_MM <= x <= FAR_MM)
    if len(measured) < CUT_MIN_DEPTH_PX:
        return None
    z = measured[len(measured) // 20] / 1000
    margin_px = margin_m * fx / z
    if not margin_px <= CUT_MAX_PX:
        return None
    return (u, v), axis, margin_px, ceiling


def cut_side(cols, rows, cut_px, extra_px=0.0):
    """Which pixels the cut removes: on the head's side of the line, or within its margin (plus extra_px) of it."""
    (u, v), (ax, ay), margin_px = cut_px[:3]
    return (cols - u) * ax + (rows - v) * ay > -(margin_px + extra_px)


def head_mask_available(evidence, *, frame_key, binding):
    """True when this frame's evidence bounds the head: the depth-pixel envelope, the shoulder-line cut, or both. A cut
    the evidence carries but that doesn't read is no mask at all: the envelope never stands in for it. True too when
    the owner's profile shows the head (head_shown): there is nothing to mask."""
    if head_shown(evidence, frame_key=frame_key, binding=binding):
        return True
    cut = torso_cut(evidence, frame_key=frame_key, binding=binding)
    if isinstance(evidence, dict) and "cut" in evidence and cut is None:
        return False
    return head_exclusion(evidence, frame_key=frame_key, binding=binding) is not None or cut is not None


class BodyMaker:
    """One registered rectified-left depth camera. State lives only in the worker."""

    def __init__(self, np, cv2, *, left_k, room_from_left, rgb=None):
        self.np, self.cv2 = np, cv2
        self.rgb = _rgb_view(np, rgb)   # the RGB camera, for the held object's mask; None leaves it off
        self.held_seen, self.held_track = deque(maxlen=HELD_OF), None
        self.hand, self.hand_speed = None, 0.0   # the last frame's wrists and the time, and their smoothed speed
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

    def step(self, depth_mm, *, observation, person=None, frame_key=None, binding=None, now, held=None):
        """Return the ready-frame fields or a clear reason for the parent publisher. held: this frame's holdable
        instance masks from the detector worker (held_seg.instances), or None when that pass is off."""
        self.stats = {}
        if observation != "person":
            self.held_seen.clear()
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

        evidence = person.get("head_evidence")
        shown = head_shown(evidence, frame_key=frame_key, binding=binding)
        exclusion = None if shown else head_exclusion(evidence, frame_key=frame_key, binding=binding)
        cut_evidence = None if shown else torso_cut(evidence, frame_key=frame_key, binding=binding)
        cut = cut_pixels(cut_evidence, raw, self.fx)
        carries_cut = isinstance(evidence, dict) and "cut" in evidence
        if not shown and ((exclusion is None and cut is None) or (carries_cut and cut is None)):
            return None, "head_mask_unavailable"   # a cut that doesn't read or can't be placed never falls back
        if self.background is None:
            return None, "warming_background"
        joints, masked = {}, {}
        for name, joint in (person.get("joints") or {}).items():
            if name not in BODY_JOINTS or not isinstance(joint, (tuple, list)) or len(joint) != 2:
                continue
            xyz, status = joint
            if status == "supported" and isinstance(xyz, (tuple, list)) and len(xyz) == 3 and all(_finite(x) for x in xyz):
                point = (np.asarray(xyz) - self.t) @ self.R
                if not NEAR_MM <= point[2] * 1000 <= FAR_MM:
                    continue
                u, v = self._project(point[None, :])[0]
                if exclusion is not None:
                    left, top, right, bottom = exclusion
                    if left - TORSO_SEED_PX <= u < right + TORSO_SEED_PX and top - TORSO_SEED_PX <= v < bottom + TORSO_SEED_PX:
                        masked[name] = point
                        continue
                # A seed this close to the cut would grow into cut pixels: it counts as masked.
                if cut is not None and (cut_side(u, v, cut, TORSO_SEED_PX) or xyz[2] > cut[3]):
                    masked[name] = point
                    continue
                joints[name] = point
        torso = {name: joints[name] for name in TORSO if name in joints}
        if len(torso) < MIN_TORSO_SEEDS and not (masked and len(joints) >= MIN_TORSO_SEEDS):
            return None, "pose_unavailable"

        anchors = torso if len(torso) >= MIN_TORSO_SEEDS else joints
        centre = (np.mean(list(anchors.values()), axis=0) @ self.R.T + self.t)[:2]
        v0, v1, u0, u1 = self._window(centre)
        depth = raw[v0:v1, u0:u1].astype(np.float32)
        bg = self.background[v0:v1, u0:u1]
        if exclusion is not None:
            left, top, right, bottom = exclusion
            if left < u1 and right > u0 and top < v1 and bottom > v0:
                depth[max(0, top - v0):bottom - v0, max(0, left - u0):right - u0] = 0
        if cut is not None:
            rows, cols = np.mgrid[v0:v1, u0:u1]
            depth[cut_side(cols, rows, cut)] = 0
        return self._person(depth, bg, v0, u0, joints, torso, centre, person, masked=masked,
                            ceiling=None if cut is None else cut[3], held=held, now=now)

    def _person(self, depth, bg, v0, u0, joints, torso, centre, person, *, masked=None, ceiling=None, held=None,
                now=0.0):
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
        if ceiling is not None:
            inside &= room[:, 2] <= ceiling   # nothing above the tracked shoulders: not even an undetected neighbour's head
        eligible = np.zeros(depth.shape, bool)
        eligible[rows[inside], cols[inside]] = True

        component, seeds = (self._torso_component(depth, eligible, torso, v0, u0)
                             if len(torso) >= MIN_TORSO_SEEDS else (None, {}))
        if component is None and masked:
            # The head mask took the shoulders: on a sofa the hips alone sit too close to the cushion to stand out.
            # Any of this person's measured joints may anchor the figure then, each part held by two of them.
            component, seeds = self._torso_component(depth, eligible, joints, v0, u0, union=True)
        if component is None or int(component.sum()) < MIN_BODY_PX:
            return None, "insufficient_depth"
        holding = held is not None and self.rgb is not None
        held_mask, held_now = None, None
        if holding:
            if person["track_id"] != self.held_track:
                self.held_seen.clear()
                self.held_track, self.hand, self.hand_speed = person["track_id"], None, 0.0
            still = self._hands_still(joints, now)
            held_mask, instance = self._held(depth, valid, delta, component, joints, held, v0, u0)
            key = instance["label"] if instance else None
            if instance is None and still:   # the seg pass named nothing here: what the hand holds by depth alone
                held_mask = self._depth_held(depth, valid, delta, component, joints, v0, u0)
                key = UNNAMED if held_mask is not None else None
            self.held_seen.append(key)
            if key is not None and self.held_seen.count(key) >= HELD_FRAMES:
                held_now = instance   # None when unnamed: kind 5 points with object null
                component = component & ~held_mask   # the object's pixels are its own points, not the body's
            else:
                held_mask = None
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

        normals = self._normals(depth, component, rows, cols, camera, v0, u0)
        keys = (rows + v0) * WIDTH + cols + u0
        kinds = np.zeros(len(keys), np.uint8)
        if held_mask is not None:
            o_rows, o_cols = np.nonzero(held_mask)
            o_camera = self._camera_points(depth[o_rows, o_cols], o_rows + v0, o_cols + u0)
            o_room = o_camera @ self.R.T + self.t
            _, pick = np.unique(np.floor(o_room / SPACING_M).astype(np.int32), axis=0, return_index=True)
            pick.sort()
            if len(pick) > OBJECT_MAX_POINTS:
                pick = pick[np.linspace(0, len(pick) - 1, OBJECT_MAX_POINTS, dtype=int)]
            o_rows, o_cols, o_camera, o_room = o_rows[pick], o_cols[pick], o_camera[pick], o_room[pick]
            o_normals = self._normals(depth, held_mask, o_rows, o_cols, o_camera, v0, u0)
            room = np.concatenate([room, o_room])
            normals = np.concatenate([normals, o_normals])
            keys = np.concatenate([keys, (o_rows + v0) * WIDTH + o_cols + u0])
            kinds = np.concatenate([kinds, np.full(len(o_rows), KIND_OBJECT, np.uint8)])
            order = np.argsort(keys, kind="stable")   # one row-major key order, as the body's own points have
            room, normals, keys, kinds = room[order], normals[order], keys[order], kinds[order]
        xyz_mm = np.rint(room * 1000)
        if (xyz_mm < -32768).any() or (xyz_mm > 32767).any():
            return None, "scope_changed"
        heading = person.get("facing_deg")
        # Shoulders the head mask removed are still this frame's measured joints: with the hips supported in the
        # cloud they give the heading and the shoulder midpoint, never points.
        masked = masked or {}
        if all(name in supported for name in SHOULDERS):
            shoulders = self._midpoint(joints, supported, SHOULDERS)
        elif all(name in masked for name in SHOULDERS):
            shoulders = self._midpoint(masked, dict.fromkeys(SHOULDERS), SHOULDERS)
        else:
            shoulders = None
        pelvis = self._midpoint(joints, supported, HIPS)
        self.stats = {"measured": len(keys), "eligible": int(component.sum()), "object": int((kinds == KIND_OBJECT).sum())}
        frame = {"track_id": person["track_id"],
                 "points": {"count": len(keys), "key": _b64(keys.astype("<u4")),
                            "position_mm": _b64(xyz_mm.astype("<i2")),
                            "normal": _b64(np.clip(np.rint(normals * 127), -127, 127).astype("i1")),
                            "kind": _b64(kinds)},
                 "facing_deg": heading if shoulders is not None and pelvis is not None
                 and _finite(heading) and 0 <= heading < 360 else None,
                 "shoulder_midpoint_m": shoulders.tolist() if shoulders is not None else None,
                 "pelvis_z_m": float(pelvis[2]) if pelvis is not None else None}
        if holding:   # only with the held-object pass on: without it the frame is exactly as it was
            frame["object"] = None if held_now is None else {
                "label": held_now["label"], "confidence": round(float(held_now["score"]), 3), "source": "detector"}
        return frame, None

    def _hands_still(self, joints, now):
        """True while the measured wrists move slower than HAND_STILL_MPS (smoothed over frames, as on 2026-11-11)."""
        np = self.np
        wrists = {name: joints[name] for name in WRISTS if name in joints}
        if self.hand is not None and now > self.hand[1]:
            moved = [float(np.linalg.norm(point - self.hand[0][name])) for name, point in wrists.items() if name in self.hand[0]]
            if moved:
                self.hand_speed += (max(moved) / (now - self.hand[1]) - self.hand_speed) * 0.5
        self.hand = (wrists, now)
        return self.hand_speed < HAND_STILL_MPS

    def _capsules(self, joints):
        """The body's own volume from its measured joints, (a, b, radius) in metres: what a held object isn't."""
        np = self.np

        def mid(a, b):
            return (joints[a] + joints[b]) / 2 if a in joints and b in joints else None
        chest, pelvis = mid(*SHOULDERS), mid(*HIPS)
        capsules = []
        if chest is not None and pelvis is not None:
            up = chest - pelvis
            up = up / max(float(np.linalg.norm(up)), 1e-9)
            capsules += [(pelvis, chest, 0.17), (chest, chest + up * 0.25, 0.11)]   # the torso, then the neck and head
        for side in ("left", "right"):
            shoulder, elbow, wrist, hip, knee, ankle = (joints.get(side + "_" + part) for part in
                                                        ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"))
            if shoulder is not None and elbow is not None:
                capsules.append((shoulder, elbow, 0.06))
            if elbow is not None and wrist is not None:
                along = (wrist - elbow) / max(float(np.linalg.norm(wrist - elbow)), 1e-9)
                capsules += [(elbow, wrist, 0.045), (wrist, wrist + along * 0.09, 0.035)]   # the forearm, then the hand
            if hip is not None and knee is not None:
                capsules.append((hip, knee, 0.095))
            if knee is not None and ankle is not None:
                capsules.append((knee, ankle, 0.065))
        return capsules

    def _depth_held(self, depth, valid, delta, component, joints, v0, u0):
        """What the hand holds by depth alone (the module note), as a pixel mask, or None."""
        np, cv2 = self.np, self.cv2
        # A wrist counts only with its elbow: without the forearm's direction the hand itself can't be explained.
        wrists = [joints[side + "_wrist"] for side in ("left", "right")
                  if side + "_wrist" in joints and side + "_elbow" in joints]
        if not wrists:
            return None
        rows, cols = np.nonzero(component | (valid & (delta >= NEAR_LIMB_MM)))
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        pick = np.min([np.linalg.norm(camera - wrist, axis=1) for wrist in wrists], axis=0) <= WRIST_REACH_M
        for a, b, radius in self._capsules(joints):
            pick &= _seg_dist(np, camera, a, b) >= radius + OBJECT_MARGIN_M
        if not pick.any():
            return None
        candidates = np.zeros(depth.shape, np.uint8)
        candidates[rows[pick], cols[pick]] = 1
        count, labels, stats, _ = cv2.connectedComponentsWithStats(candidates, connectivity=4)
        if count <= 1:
            return None
        region = labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        z = depth[region].astype(float)
        region &= np.abs(depth.astype(float) - np.median(z)) <= EDGE_MM   # its own surface, as the seg path keeps
        metres = depth[region] / 1000.0
        if float(np.sum((metres / self.fx) ** 2)) < DEPTH_OBJECT_MIN_M2:
            return None
        return region

    def _held(self, depth, valid, delta, component, joints, held, v0, u0):
        """(this frame's held-object pixel mask, its instance) or (None, None): the person's own pixels, and anything
        nearer than the empty room, within WRIST_REACH_M of a measured wrist, whose RGB pixel one holdable instance
        mask covers, within EDGE_MM of those pixels' median depth. The instance covering the most such pixels, with at
        least OBJECT_MIN_PX of them."""
        np, cv2 = self.np, self.cv2
        wrists = [joints[name] for name in WRISTS if name in joints]
        width, height = self.rgb["size"]
        instances = []
        labels = np.zeros((height, width), np.int16)
        for instance in held if isinstance(held, (list, tuple)) else ():
            polygon = _polygon(np, instance, width, height)
            if polygon is not None and len(instances) < 32:
                instances.append(instance)
                cv2.fillPoly(labels, [polygon], len(instances))
        if not wrists or not instances:
            return None, None
        rows, cols = np.nonzero(component | (valid & (delta >= NEAR_LIMB_MM)))
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        near = np.min([np.linalg.norm(camera - wrist, axis=1) for wrist in wrists], axis=0) <= WRIST_REACH_M
        rows, cols, camera = rows[near], cols[near], camera[near]
        u, v, ahead = _rgb_pixels(np, self.rgb, camera)
        inside = ahead & (u >= 0) & (u < width) & (v >= 0) & (v < height)
        hit = np.zeros(len(rows), np.int64)
        hit[inside] = labels[v[inside].astype(np.int64), u[inside].astype(np.int64)]
        if not (hit > 0).any():
            return None, None
        best = int(np.argmax(np.bincount(hit[hit > 0])))
        pick = hit == best
        # A mask's edge also covers what lies just behind the thing (the chest behind a book): keep the pixels within
        # EDGE_MM of the masked pixels' median depth, the object's own surface.
        z = camera[:, 2] * 1000
        pick &= np.abs(z - np.median(z[pick])) <= EDGE_MM
        if int(pick.sum()) < OBJECT_MIN_PX:
            return None, None
        mask = np.zeros(depth.shape, bool)
        mask[rows[pick], cols[pick]] = True
        return mask, instances[best - 1]

    def _midpoint(self, joints, supported, pair):
        if all(name in supported for name in pair):
            return ((joints[pair[0]] + joints[pair[1]]) / 2) @ self.R.T + self.t
        return None

    def _torso_component(self, depth, eligible, torso, v0, u0, *, union=False):
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
        if union and candidates:
            return np.isin(labels, [label for label, _ in candidates]), {
                name: point for _, points in candidates for name, point in points.items()}
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
        """kept is the last detection's (worker id, bundle bytes, pipeline frame key[, its held-object instances])."""
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
                self.maker = BodyMaker(np, cv2, left_k=setup["left_k"], room_from_left=setup["room_from_left"],
                                       rgb=setup.get("rgb"))
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
                                         frame_key=frame_key, binding=binding,
                                         held=kept[3] if len(kept) > 3 else None)
            return {**reply, "frame": frame, "why": why, "compute_s": round(time.perf_counter() - start, 4),
                    "stats": self.maker.stats}
        except Exception as exc:  # Never put exception text or input data on the pipe.
            self.maker.interrupt_learning()
            return {**reply, "error": type(exc).__name__}


def _rgb_view(np, rgb):
    """The RGB camera from the body setup: {k: [fx, fy, cx, cy, skew], d: 8 rational terms, from_left: [R, t], size:
    [w, h]}, as den-observe's rig has it; None without one. A malformed one is refused."""
    if rgb is None:
        return None
    k, d, (rotation, translation), size = rgb["k"], rgb["d"], rgb["from_left"], rgb["size"]
    k = [float(x) for x in k]
    d = [float(x) for x in d] + [0.0] * (8 - len(d))
    R, t = np.asarray(rotation, float), np.asarray(translation, float)
    width, height = int(size[0]), int(size[1])
    if (len(k) != 5 or not all(math.isfinite(x) for x in k + d) or len(d) != 8 or min(k[0], k[1]) <= 0
            or R.shape != (3, 3) or t.shape != (3,) or not np.isfinite(R).all() or not np.isfinite(t).all()
            or not np.allclose(R.T @ R, np.eye(3), atol=1e-4) or not 0 < width <= 4096 or not 0 < height <= 4096):
        raise ValueError("invalid RGB camera for the held object")
    return {"k": k, "d": d, "R": R, "t": t, "size": (width, height)}


def _rgb_pixels(np, rgb, camera):
    """(u, v, in front) of rectified-left camera points in the RGB image: den-observe camgeom's project and distort."""
    p = camera @ rgb["R"].T + rgb["t"]
    ahead = p[:, 2] > 1e-6
    z = np.where(ahead, p[:, 2], 1.0)
    x, y = p[:, 0] / z, p[:, 1] / z
    k1, k2, p1, p2, k3, k4, k5, k6 = rgb["d"]
    r2 = x * x + y * y
    radial = (1 + r2 * (k1 + r2 * (k2 + r2 * k3))) / (1 + r2 * (k4 + r2 * (k5 + r2 * k6)))
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    fx, fy, cx, cy, skew = rgb["k"]
    return fx * xd + skew * yd + cx, fy * yd + cy, ahead


def _polygon(np, instance, width, height):
    """An instance's polygon as int32 RGB pixels, or None: a holdable label, a score in (0, 1], 3 to MAX_POLYGON
    finite vertices near the image."""
    if not isinstance(instance, dict) or instance.get("label") not in HOLDABLE:
        return None
    score, polygon = instance.get("score"), instance.get("polygon")
    if (not _finite(score) or not 0 < score <= 1 or not isinstance(polygon, (list, tuple))
            or not 3 <= len(polygon) <= MAX_POLYGON):
        return None
    try:
        points = np.asarray(polygon, float)
    except (TypeError, ValueError):
        return None
    if (points.shape != (len(polygon), 2) or not np.isfinite(points).all()
            or (np.abs(points) > 4 * max(width, height)).any()):
        return None
    return np.round(points).astype(np.int32)


def _seg_dist(np, points, a, b):
    direction = b - a
    length_sq = max(float(direction @ direction), 1e-12)
    fraction = np.clip((points - a) @ direction / length_sq, 0, 1)
    return np.linalg.norm(points - (a + fraction[:, None] * direction), axis=1)

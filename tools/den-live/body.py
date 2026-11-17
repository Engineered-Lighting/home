"""Build a person's cloud from the current registered depth in the detector worker.

Only a qualified empty observation can feed the background. A person needs a
foreground component supported by their current measured torso joints and,
unless the owner's profile shows the head ("head": "shown", HEAD_SHOWN), a
conservative exclusion for the head and upper neck. Every emitted point is an
actual depth pixel from that bundle, times step 2's depth scale (the setup's
depth_scale, as perceive.py scales the lift's depth; 1.0 changes nothing).
"""
import base64
import hashlib
import io
import json
import math
import os
import time
import zipfile
from collections import deque

HEIGHT, WIDTH = 400, 640
DEPTH_SCALE_RANGE = (0.90, 1.10)   # contracts.py's: this file loads in the detector's venv on its own
N = HEIGHT * WIDTH
EMPTY_S, LEARN_FRAMES, RELEARN_S = 8.0, 12, 120.0
# The learned empty room, kept in the worker's state folder so a restart or an install while someone is in the den
# doesn't blank the figure until the room is empty again. It's keyed by what makes it this room from this camera (the
# scan revision, the camera's registration and its depth profile, intrinsics and depth scale), never by the run. It's
# read back only within KEPT_MAX_S of being learned. As before, the next empty spell after RELEARN_S learns it afresh.
BACKGROUND_FILE, KEPT_MAX_S = "body-background.npz", 24 * 3600.0
CUT_MM, NEAR_LIMB_MM, EDGE_MM = 120, 40, 60
NEAR_MM, FAR_MM = 300, 5000
GATE_M, MIN_BODY_PX = 0.9, 300
# One measured pixel per SPACING_M cube. At the desk and sofa, 2-3 m away, depth pixels are 6-7 mm apart: 13 mm kept
# about one pixel in four and the figure read as wisps (3.5k points at the desk); 8 mm keeps about one in two and twice
# the points (7.5k), all real pixels, well under MAX_POINTS.
SPACING_M, MAX_POINTS = 0.008, 16384
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
# pixels like every other, with the detector's label, once the same measured instance is supported in HELD_FRAMES
# of the last HELD_OF person frames, for the same owner and association revision.
KIND_OBJECT = 5
HOLDABLE = frozenset(("book", "cell phone", "cup", "bottle", "remote", "laptop", "scissors", "mouse"))
WRISTS = ("left_wrist", "right_wrist")
WRIST_REACH_M, OBJECT_MIN_PX, OBJECT_MAX_POINTS = 0.32, 40, 4096
HELD_FRAMES, HELD_OF = 2, 3
MAX_POLYGON = 256
OBJECT_MATCH_M, OBJECT_GAP_S = 0.12, 0.6
# When the seg pass names nothing at the hand (a phone is about 20 px in the RGB frame, too small for it), what the hand
# holds by depth alone, as den-live had it on 2026-11-11 (1a4268f7): measured pixels within WRIST_REACH_M of a wrist
# whose elbow is measured too, that the body's own capsules don't explain (OBJECT_MARGIN_M beyond them), the largest
# 4-connected region of at least DEPTH_OBJECT_MIN_M2 (phone-sized), while the hands move slower than HAND_STILL_MPS. It
# holds on the same 2-of-3 rule, unnamed: kind 5 points, and object null for Home's "Held object" marker.
OBJECT_MARGIN_M, DEPTH_OBJECT_MIN_M2, HAND_STILL_MPS = 0.025, 0.006, 0.3
UNNAMED = ""   # the hold rule's key for an object held by depth alone
# The seg model's names that a held phone takes: about 20 px in the RGB frame, it is never "cell phone" there, and it
# is "remote" on some frames, a few cm from the wrist at 0.36-0.67, where no score floor parts it from the books (the
# sofa benchmark, 2026-11-14). For these the mask still gives the object's points, but they hold on the unnamed key:
# object null, and Home's "Held object", never a wrong name.
UNNAMED_LABELS = frozenset(("cell phone", "remote"))


class ObjectHistory:
    """Confirm one measured instance for one exact owner. Geometry never survives a missing current mask."""

    def __init__(self):
        self.serial = 0
        self.clear()

    def clear(self):
        self.owner = self.last = None
        self.seen = deque(maxlen=HELD_OF)

    def observe(self, owner, frame_id, now, candidate):
        if self.owner != owner or self.last is not None and (now <= self.last[0] or now - self.last[0] > OBJECT_GAP_S):
            self.clear()
            self.owner = owner
        if self.last is not None and frame_id <= self.last[1]:
            return False
        if candidate is None:
            self.seen.append(False)
            return False
        centre, keys, wrist = candidate
        matches = False
        if self.last is not None:
            _, _, previous, previous_keys, previous_wrist = self.last
            distance = math.dist(centre, previous)
            union = len(keys | previous_keys)
            overlap = len(keys & previous_keys) / union if union else 0.0
            matches = wrist == previous_wrist and distance <= OBJECT_MATCH_M and (overlap >= 0.10 or distance <= 0.04)
        if not matches:
            self.serial += 1
            self.seen.clear()
        self.last = now, frame_id, centre, keys, wrist
        self.seen.append(True)
        return sum(self.seen) >= HELD_FRAMES



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
        self.fx, self.fy, self.cx, self.cy = (float(x) for x in left_k[:4])
        self.R, self.t = np.asarray(room_from_left[0], float), np.asarray(room_from_left[1], float)
        if (not all(math.isfinite(x) for x in (self.fx, self.fy, self.cx, self.cy))
                or min(self.fx, self.fy) <= 0 or self.R.shape != (3, 3) or self.t.shape != (3,)
                or not np.isfinite(self.t).all()
                or not np.allclose(self.R.T @ self.R, np.eye(3), atol=1e-4)
                or not np.isclose(np.linalg.det(self.R), 1, atol=1e-4)):
            raise ValueError("invalid depth registration")
        self.rgb = _rgb_view(np, rgb)
        self.objects = ObjectHistory()
        self.hand, self.hand_speed = None, 0.0
        self.background = None
        self.learned_at = None
        self.on_learned = None
        self.stats = {}
        self.interrupt_learning()

    def interrupt_learning(self):
        self.empty_since = None
        self.learning = []

    def reset_objects(self):
        self.objects.clear()
        self.hand, self.hand_speed = None, 0.0

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
        if self.on_learned is not None:
            self.on_learned(background)

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

    def step(self, depth_mm, *, observation, person=None, frame_key=None, binding=None, now, masks=None):
        """Return the ready-frame fields or a clear reason for the parent publisher."""
        self.stats = {}
        owner = _owner(person, frame_key, binding) if observation == "person" else None
        mask_input = (isinstance(masks, dict) and masks.get("frame_key") == frame_key
                      and masks.get("segmentation_status") == "ok" and owner is not None and self.rgb is not None)
        if not mask_input:
            self.reset_objects()
        elif self.objects.owner != owner:
            self.reset_objects()
        if observation != "empty":
            self.interrupt_learning()
        if observation not in ("empty", "person"):
            self.reset_objects()
            return None, "pose_unavailable"
        np = self.np
        raw = np.asarray(depth_mm)
        if raw.shape != (HEIGHT, WIDTH) or raw.dtype != np.dtype("<u2"):
            self.interrupt_learning()
            return None, "insufficient_depth"
        if observation == "empty":
            self.reset_objects()
            self._learn(raw.copy(), now)
            return None, "warming_background" if self.background is None else "empty"
        if not isinstance(person, dict):
            return None, "pose_unavailable"
        track_id = person.get("track_id")
        if not isinstance(track_id, str) or not 0 < len(track_id) <= 64:
            return None, "pose_unavailable"
        if person.get("association_status", "confident") != "confident":
            self.reset_objects()
            return None, "ambiguous"

        evidence = person.get("head_evidence")
        shown = head_shown(evidence, frame_key=frame_key, binding=binding)
        exclusion = None if shown else head_exclusion(evidence, frame_key=frame_key, binding=binding)
        cut_evidence = None if shown else torso_cut(evidence, frame_key=frame_key, binding=binding)
        cut = cut_pixels(cut_evidence, raw, self.fx)
        carries_cut = isinstance(evidence, dict) and "cut" in evidence
        if not shown and ((exclusion is None and cut is None) or (carries_cut and cut is None)):
            self.reset_objects()
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
        # All optional mask geometry uses the same measured and privacy-filtered depth as the body.
        person_mask = None
        if mask_input and masks.get("person_masks"):
            person_mask = self._person_mask(depth, joints, masks["person_masks"], v0, u0)
            if person_mask is None:
                self.reset_objects()
                return None, "ambiguous"
        return self._person(depth, bg, v0, u0, joints, torso, centre, person, masked=masked,
                            ceiling=None if cut is None else cut[3], person_mask=person_mask,
                            masks=masks if mask_input else None, owner=owner, frame_key=frame_key, now=now)

    def _person(self, depth, bg, v0, u0, joints, torso, centre, person, *, masked=None, ceiling=None,
                person_mask=None, masks=None, owner=None, frame_key=None, now=None):
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
        if person_mask is not None:
            foreground &= person_mask
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
            self.reset_objects()
            return None, "insufficient_depth"
        held_mask, held_now = None, None
        if masks is not None:
            still = self._hands_still(joints, now)
            held_mask, instance, wrist = self._held(depth, valid, delta, component, joints,
                                                   masks.get("held_masks", []), v0, u0, ceiling)
            if instance is False:
                self.reset_objects()
            if held_mask is None and instance is not False and still:
                held_mask, wrist = self._depth_held(depth, valid, delta, component, joints, v0, u0, ceiling)
                instance = None
            candidate = None
            if held_mask is not None:
                hr, hc = np.nonzero(held_mask)
                measured = self._camera_points(depth[hr, hc], hr + v0, hc + u0)
                candidate = (tuple(np.median(measured, axis=0)), frozenset(((hr + v0) * WIDTH + hc + u0).tolist()), wrist)
            if self.objects.observe(owner, frame_key[2], now, candidate):
                component &= ~held_mask
                held_now = instance
            else:
                held_mask = None
        rows, cols = np.nonzero(component)
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        room = camera @ self.R.T + self.t
        # Pick a source pixel in every occupied cell. Keep row-major key ordering and spread the cap across the cloud.
        _, selected = np.unique(np.floor(room / SPACING_M).astype(np.int32), axis=0, return_index=True)
        selected.sort()
        object_count = 0 if held_mask is None else min(int(held_mask.sum()), OBJECT_MAX_POINTS)
        body_cap = MAX_POINTS - object_count
        if len(selected) > body_cap:
            selected = selected[np.linspace(0, len(selected) - 1, body_cap, dtype=int)]
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
            if len(pick) > object_count:
                pick = pick[np.linspace(0, len(pick) - 1, object_count, dtype=int)]
            o_rows, o_cols, o_camera, o_room = o_rows[pick], o_cols[pick], o_camera[pick], o_room[pick]
            o_normals = self._normals(depth, held_mask, o_rows, o_cols, o_camera, v0, u0)
            room = np.concatenate([room, o_room])
            normals = np.concatenate([normals, o_normals])
            keys = np.concatenate([keys, (o_rows + v0) * WIDTH + o_cols + u0])
            kinds = np.concatenate([kinds, np.full(len(o_rows), KIND_OBJECT, np.uint8)])
            order = np.argsort(keys, kind="stable")
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
        self.stats = {"measured": len(keys), "eligible": int(component.sum())}
        frame = {"track_id": person["track_id"],
                "points": {"count": len(keys), "key": _b64(keys.astype("<u4")),
                           "position_mm": _b64(xyz_mm.astype("<i2")),
                           "normal": _b64(np.clip(np.rint(normals * 127), -127, 127).astype("i1")),
                           "kind": _b64(kinds)},
                "facing_deg": heading if shoulders is not None and pelvis is not None
                and _finite(heading) and 0 <= heading < 360 else None,
                "shoulder_midpoint_m": shoulders.tolist() if shoulders is not None else None,
                "pelvis_z_m": float(pelvis[2]) if pelvis is not None else None}
        if masks is not None:
            # Phone/remote labels confused in the held-sofa benchmark retain their measured generic marker.
            frame["object"] = None if held_now is None or held_now["label"] in UNNAMED_LABELS else {
                "label": held_now["label"], "confidence": held_now["score"], "source": "detector"}
            self.stats.update(object=int((kinds == KIND_OBJECT).sum()), object_instance=self.objects.serial)
        return frame, None

    def _mask(self, instance, *, person=False):
        np = self.np
        width, height = self.rgb["size"]
        polygon = _polygon(np, instance, width, height, person=person)
        if polygon is None:
            return None
        mask = np.zeros((height, width), np.uint8)
        self.cv2.fillPoly(mask, [polygon], 1)
        return mask

    def _mask_pixels(self, depth, mask, v0, u0):
        """Rectified-left measured rays inside one RGB mask, using nearest visible depth per RGB pixel."""
        np = self.np
        rows, cols = np.nonzero((depth >= NEAR_MM) & (depth <= FAR_MM))
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        u, v, z = _rgb_pixels(np, self.rgb, camera)
        height, width = mask.shape
        inside = np.isfinite(u) & np.isfinite(v) & np.isfinite(z) & (z > 0)
        inside &= (u >= 0) & (u < width) & (v >= 0) & (v < height)
        rows, cols, u, v, z = rows[inside], cols[inside], u[inside].astype(int), v[inside].astype(int), z[inside]
        nearest = np.full(width * height, np.inf)
        index = v * width + u
        np.minimum.at(nearest, index, z)
        take = (mask[v, u] != 0) & (z <= nearest[index] + 0.01)
        result = np.zeros(depth.shape, bool)
        result[rows[take], cols[take]] = True
        return result

    def _person_mask(self, depth, joints, instances, v0, u0):
        if not isinstance(instances, (list, tuple)) or len(instances) > 32:
            return None
        anchors = [joints[name] for name in TORSO if name in joints]
        if len(anchors) < MIN_TORSO_SEEDS:
            anchors = list(joints.values())
        u, v, z = _rgb_pixels(self.np, self.rgb, self.np.asarray(anchors))
        width, height = self.rgb["size"]
        inside = (z > 0) & self.np.isfinite(u) & self.np.isfinite(v) & (u >= 0) & (u < width) & (v >= 0) & (v < height)
        u, v = u[inside].astype(int), v[inside].astype(int)
        matches = []
        for instance in instances:
            mask = self._mask(instance, person=True)
            if mask is not None and int(mask[v, u].sum()) >= MIN_TORSO_SEEDS:
                matches.append(mask)
        if len(matches) != 1:
            return None
        return self._mask_pixels(depth, matches[0], v0, u0)

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

    def _object_support(self, depth, valid, delta, component, joints, v0, u0, ceiling):
        np = self.np
        wrists = {name: joints[name] for name in WRISTS if name in joints}
        rows, cols = np.nonzero(valid & (component | (delta >= NEAR_LIMB_MM)))
        camera = self._camera_points(depth[rows, cols], rows + v0, cols + u0)
        if not wrists:
            return rows[:0], cols[:0], camera[:0], [], []
        distance = np.asarray([np.linalg.norm(camera - point, axis=1) for point in wrists.values()])
        closest = np.argmin(distance, axis=0)
        take = np.min(distance, axis=0) <= WRIST_REACH_M
        room = camera @ self.R.T + self.t
        take &= (room[:, 2] >= 0.02) & (room[:, 2] <= (2.4 if ceiling is None else min(2.4, ceiling)))
        return rows[take], cols[take], camera[take], closest[take], list(wrists)

    def _held(self, depth, valid, delta, component, joints, held, v0, u0, ceiling):
        np = self.np
        if not isinstance(held, (list, tuple)) or len(held) > 32:
            return None, None, None
        rows, cols, camera, closest, wrists = self._object_support(depth, valid, delta, component, joints, v0, u0, ceiling)
        if not len(rows):
            return None, None, None
        candidates = []
        for instance in held:
            mask = self._mask(instance)
            if mask is None:
                continue
            rgb_hit = self._mask_pixels(depth, mask, v0, u0)
            take = rgb_hit[rows, cols]
            if take.any():
                take &= np.abs(camera[:, 2] * 1000 - np.median(camera[take, 2]) * 1000) <= EDGE_MM
            if int(take.sum()) >= OBJECT_MIN_PX:
                measured = np.zeros(depth.shape, bool)
                measured[rows[take], cols[take]] = True
                wrist = wrists[int(np.argmax(np.bincount(closest[take])))]
                candidates.append((measured, instance, wrist))
        # Competing holdable instances cannot safely confirm ownership, even when their labels agree.
        return candidates[0] if len(candidates) == 1 else (None, False if candidates else None, None)

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


    def _depth_held(self, depth, valid, delta, component, joints, v0, u0, ceiling):
        np, cv2 = self.np, self.cv2
        rows, cols, camera, closest, wrists = self._object_support(depth, valid, delta, component, joints, v0, u0, ceiling)
        if not len(rows):
            return None, None
        pick = np.ones(len(rows), bool)
        for a, b, radius in self._capsules(joints):
            pick &= _seg_dist(np, camera, a, b) >= radius + OBJECT_MARGIN_M
        # Require a measured elbow for the selected hand, so the forearm cannot masquerade as an object.
        pick &= np.asarray([wrists[int(index)].replace("wrist", "elbow") in joints for index in closest])
        pixels = np.zeros(depth.shape, np.uint8)
        pixels[rows[pick], cols[pick]] = 1
        count, labels, stats, _ = cv2.connectedComponentsWithStats(pixels, connectivity=4)
        if count <= 1:
            return None, None
        region = labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        median = float(np.median(depth[region]))
        region &= np.abs(depth - median) <= EDGE_MM
        metres = depth[region] / 1000.0
        if float(np.sum((metres / self.fx) ** 2)) < DEPTH_OBJECT_MIN_M2:
            return None, None
        selected = region[rows, cols]
        wrist = wrists[int(np.argmax(np.bincount(closest[selected])))]
        return region, wrist

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


def background_key(setup):
    """The kept background's key: this room's scan revision, the camera's registration and depth profile, and the
    depth geometry (intrinsics, room_from_left, depth scale). Never the run or the camera epoch."""
    binding = setup.get("binding")
    binding = binding if isinstance(binding, dict) else {}
    identity = {"content_revision": binding.get("content_revision"), "transform_id": binding.get("transform_id"),
                "depth_profile": binding.get("depth_profile"), "left_k": setup.get("left_k"),
                "room_from_left": setup.get("room_from_left"), "depth_scale": setup.get("depth_scale", 1.0)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def keep_background(np, folder, key, background, *, wall=time.time):
    """Write the learned background beside its key and time, atomically. Nothing on failure: it's only a head start."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / (BACKGROUND_FILE + ".part")
        with open(part, "wb") as out:
            np.savez(out, background=background.astype(np.float32), key=np.array(key), saved_unix=np.array(float(wall())))
        os.replace(part, folder / BACKGROUND_FILE)
    except (OSError, ValueError):
        pass


def kept_background(np, folder, key, *, wall=time.time):
    """The kept background for this key, learned within KEPT_MAX_S, or None."""
    try:
        with np.load(folder / BACKGROUND_FILE, allow_pickle=False) as kept:
            background, saved_key, saved = kept["background"], str(kept["key"]), float(kept["saved_unix"])
    except (OSError, KeyError, ValueError, TypeError):
        return None
    if (saved_key != key or not 0 <= wall() - saved <= KEPT_MAX_S or background.shape != (HEIGHT, WIDTH)
            or background.dtype != np.float32 or not np.isfinite(background).all()):
        return None
    return background



class BodyChannel:
    """Handle body commands on the last bundle and echo private request identity to the publisher."""

    def __init__(self, state=None, *, wall=time.time):
        from pathlib import Path
        self.maker = self.binding = None
        self.depth_scale = 1.0
        self.state, self.wall = (Path(state) if state is not None else None), wall

    def handle(self, header, kept):
        """kept is (worker id, bundle bytes, pipeline frame key[, exact-frame masks])."""
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
                if self.state is not None:
                    key = background_key(setup)
                    # A kept background is this room's from this camera: start from it rather than warming. The next
                    # empty spell learns it afresh (learned_at stays None, so it may at once).
                    self.maker.background = kept_background(np, self.state, key, wall=self.wall)
                    self.maker.on_learned = lambda background: keep_background(np, self.state, key, background,
                                                                                wall=self.wall)
                return {"body_ready": True, "binding": self.binding}
            except Exception as exc:
                return {"body_refused": type(exc).__name__, "binding": self.binding}
        body_id, frame_key, binding = header.get("id"), header.get("frame_key"), header.get("binding")
        reply = {"body_id": body_id, "request_id": header.get("request_id"), "frame_key": frame_key, "binding": binding}
        if self.maker is None:
            return {**reply, "error": "NotSetUp"}
        if binding != self.binding:
            self.maker.reset_objects()
            return {**reply, "frame": None, "why": "scope_changed"}
        if header.get("reset_learning"):
            self.maker.interrupt_learning()
            self.maker.reset_objects()
        observation = header.get("observation")
        if observation not in ("empty", "person"):
            self.maker.interrupt_learning()
            self.maker.reset_objects()
            return {**reply, "frame": None, "why": "pose_unavailable"}
        if kept is None or frame_key is None or kept[0] != body_id or kept[2] != frame_key:
            self.maker.interrupt_learning()
            self.maker.reset_objects()
            return {**reply, "frame": None, "why": "stale"}
        try:
            start = time.perf_counter()
            frame, why = self.maker.step(depth_of(self.maker.np, kept[1], self.depth_scale), observation=observation,
                                         person=header.get("person"), now=float(header["now"]),
                                         frame_key=frame_key, binding=binding,
                                         masks=kept[3] if len(kept) > 3 else None)
            return {**reply, "frame": frame, "why": why, "compute_s": round(time.perf_counter() - start, 4),
                    "stats": self.maker.stats}
        except Exception as exc:  # Never put exception text or input data on the pipe.
            self.maker.interrupt_learning()
            self.maker.reset_objects()
            return {**reply, "error": type(exc).__name__}


def _owner(person, frame_key, binding):
    if (not isinstance(person, dict) or not isinstance(frame_key, (list, tuple)) or len(frame_key) != 3
            or not isinstance(frame_key[0], str) or not frame_key[0]
            or type(frame_key[1]) is not int or frame_key[1] < 0
            or type(frame_key[2]) is not int or frame_key[2] <= 0
            or type(person.get("association_revision")) is not int or person["association_revision"] < 0
            or person.get("mask_association_status", person.get("association_status")) != "confident"
            or person.get("mode") != "rgb"):
        return None
    return (frame_key[0], frame_key[1], person.get("track_id"), person["association_revision"],
            person["mode"], json.dumps(binding, sort_keys=True, allow_nan=False))


def _rgb_view(np, rgb):
    """Validate registered RGB intrinsics and the rectified-left-to-RGB rigid transform."""
    if rgb is None:
        return None
    k, d, (rotation, translation), size = rgb["k"], rgb["d"], rgb["from_left"], rgb["size"]
    k = [float(x) for x in k]
    d = [float(x) for x in d]
    if len(d) > 8:
        raise ValueError("invalid RGB distortion")
    d += [0.0] * (8 - len(d))
    rotation, translation = np.asarray(rotation, float), np.asarray(translation, float)
    width, height = size
    if (len(k) != 5 or not all(math.isfinite(x) for x in k + d) or min(k[0], k[1]) <= 0
            or rotation.shape != (3, 3) or translation.shape != (3,)
            or not np.isfinite(rotation).all() or not np.isfinite(translation).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4)
            or not np.isclose(np.linalg.det(rotation), 1, atol=1e-4)
            or type(width) is not int or type(height) is not int or not 0 < width <= 4096 or not 0 < height <= 4096):
        raise ValueError("invalid RGB camera")
    return {"k": k, "d": d, "R": rotation, "t": translation, "size": (width, height)}


def _rgb_pixels(np, rgb, camera):
    """Project measured left-camera rays into distorted RGB, returning its axial depth in metres."""
    points = camera @ rgb["R"].T + rgb["t"]
    z = points[:, 2]
    divisor = np.where(z > 1e-6, z, 1.0)
    x, y = points[:, 0] / divisor, points[:, 1] / divisor
    k1, k2, p1, p2, k3, k4, k5, k6 = rgb["d"]
    radius = x * x + y * y
    denominator = 1 + radius * (k4 + radius * (k5 + radius * k6))
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        radial = (1 + radius * (k1 + radius * (k2 + radius * k3))) / denominator
        xd = x * radial + 2 * p1 * x * y + p2 * (radius + 2 * x * x)
        yd = y * radial + p1 * (radius + 2 * y * y) + 2 * p2 * x * y
        fx, fy, cx, cy, skew = rgb["k"]
        return fx * xd + skew * yd + cx, fy * yd + cy, z


def _polygon(np, instance, width, height, *, person=False):
    if not isinstance(instance, dict) or not person and instance.get("label") not in HOLDABLE:
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
    return np.rint(points).astype(np.int32)


def _seg_dist(np, points, a, b):
    direction = b - a
    length_sq = max(float(direction @ direction), 1e-12)
    fraction = np.clip((points - a) @ direction / length_sq, 0, 1)
    return np.linalg.norm(points - (a + fraction[:, None] * direction), axis=1)

"""Contract 2's remaining person fields, worked out once per track per frame by perception. Standard library.

The three fields (contracts.md §10, "Contract 2's fields"), from stereo-supported joints only (perceive.stereo_joints'
rule: lift stereo, status supported, a room position):
- body_facing {deg, sd_deg, as_of_mono, n}: den-track's records.body_heading, unedited at its pin, on a copy of the pose
  that holds both shoulders and, when their left-to-right line lies within HIP_AGREE_DEG of the shoulders', both hips.
  A sample counts when its sd is at most den-track's heading_max_sd_deg (30). A sample that turns FLIP_DEG or more
  from the facing published now waits for the next sample, and counts only if that one turns as far too: a detector
  swapping left and right for one frame turns the body round, and one frame mustn't. The published deg is the
  circular median of the samples over the last WINDOW_S, ending at the newest; sd_deg is the median of their sds; n is
  how many there are; as_of_mono is the newest one's capture time. It expires with the pose: past PERCEPTION_S after
  as_of_mono it's None, and it starts again whenever the track's association_revision moves. Degrees are clockwise
  from +y, as den-track's are.
- head_point_m {xyz, n}: the median (each axis) of the stereo-supported face joints, nose, eyes and ears; None with
  fewer than two. A position only.
- head_orientation and torso_orientation: current metric landmark axes, with propagated covariance and an explicit
  anatomical-model uncertainty floor. Missing, contradictory or uncertain geometry abstains. The head direction
  describes coarse anatomy. Eye gaze requires separate evidence.
- wrists_m: the stereo-supported wrists, {left_wrist: xyz, right_wrist: xyz}, each only when it's there.

- posture: one closed word, from what perception measures, in Qwen's own words for subject_posture (den-live-scene-2)
  less lying, which nothing here measures: sitting while seated (seated.Published); else walking when the track's
  floor position moved at WALK_MPS or more over the last SPEED_S, upright or with no stereo pose; else standing on the
  seated rule's clear standing evidence (seated.standing_evidence: a head at 1.50 m or more; with no head, a mid-hip
  over 0.95 m; or the mid-hip 0.35 m over the knees); else unclear. Hips at the sofa band's edge are no evidence.

The people projection's heading_deg is body_facing's deg (None without it), and it carries seated and posture. The
preview reads head_point_m.
"""
import math
import statistics
from collections import deque
from types import MappingProxyType

from contracts import PERCEPTION_S, BodyFacing, HeadPoint, Orientation3D
from seated import body_joints, standing_evidence

WINDOW_S = 1.0       # the circular median's window, ending at the newest sample
FLIP_DEG = 150.0     # a turn this far from the published facing waits for a second sample
HIP_AGREE_DEG = 60.0  # the hips join the shoulders only when their lines agree this well
FACE = ("nose", "left_eye", "right_eye", "left_ear", "right_ear")
WRISTS = ("left_wrist", "right_wrist")
POSTURES = ("sitting", "standing", "walking", "unclear")
WALK_MPS = 0.3       # a floor speed this fast or more is walking
SPEED_S = 1.0        # over this long: the newest position against the oldest within it, at least MIN_SPAN_S apart
MIN_SPAN_S = 0.5
SHOULDERS = (5, 6)   # coco17 indices, den-observe's joints_room j
HIPS = (11, 12)
HEAD_ORIENTATION_JOINTS = (0, 1, 2, 3, 4)
TORSO_ORIENTATION_JOINTS = (*SHOULDERS, *HIPS)
# These floors describe uncalibrated anatomical proxies. Landmark noise adds to them.
HEAD_MODEL_SD_DEG, HEAD_MAX_SD_DEG = 20.0, 45.0
TORSO_MODEL_SD_DEG, TORSO_MAX_SD_DEG = 8.0, 30.0


def _difference(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _midpoint(a, b):
    return tuple((x + y) / 2 for x, y in zip(a, b))


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def _unit(a):
    length = math.hypot(*a)
    return tuple(x / length for x in a) if length > 1e-8 else None


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _perpendicular(vector, right):
    along = _dot(vector, right)
    return tuple(x - along * r for x, r in zip(vector, right))


def _covariance(value):
    """Accept a finite symmetric positive-semidefinite 3x3 covariance, including zero uncertainty."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    if any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in value):
        return False
    if any(type(x) not in (int, float) or not math.isfinite(x) for row in value for x in row):
        return False
    scale = max(1e-15, max(abs(x) for row in value for x in row))
    # Even a 100 m uncertainty cannot support the sub-metre anatomical axes below. Bound arithmetic too.
    if scale > 10000:
        return False
    tolerance = scale * 1e-9
    if any(value[i][i] < 0 for i in range(3)):
        return False
    if any(abs(value[i][j] - value[j][i]) > tolerance for i in range(3) for j in range(i)):
        return False
    if any(value[i][i] * value[j][j] - value[i][j] ** 2 < -scale * tolerance
           for i in range(3) for j in range(i)):
        return False
    a, b, c = value[0]
    _, d, e = value[1]
    _, _, f = value[2]
    return a * d * f + 2 * b * c * e - a * e * e - d * c * c - f * b * b >= -scale ** 2 * tolerance


def _orientation_joints(pose, ids):
    if (not pose or pose.get("kind") != "keypoints" or pose.get("joint_set") != "coco17"
            or pose.get("validity", "valid") != "valid"):
        return None
    joints = {}
    for entry in pose.get("joints_room") or ():
        if not isinstance(entry, dict):
            return None
        j = entry.get("j")
        if j not in ids:
            continue
        xyz = entry.get("xyz")
        if (j in joints or not _stereo(entry) or not isinstance(xyz, (list, tuple)) or len(xyz) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in xyz)
                or not _covariance(entry.get("cov"))):
            return None
        joints[j] = entry
    return joints if len(joints) == len(ids) else None


def _torso_axes(points):
    left, right, left_hip, right_hip = (points[j] for j in TORSO_ORIENTATION_JOINTS)
    shoulders, hips = _difference(right, left), _difference(right_hip, left_hip)
    rising = _difference(_midpoint(left, right), _midpoint(left_hip, right_hip))
    if not (0.15 <= math.hypot(*shoulders) <= 0.75 and 0.10 <= math.hypot(*hips) <= 0.65
            and 0.15 <= math.hypot(*rising) <= 0.90):
        return None
    lateral, hip_lateral, up = _unit(shoulders), _unit(hips), _unit(rising)
    if (_dot(lateral, hip_lateral) < math.cos(math.radians(HIP_AGREE_DEG))
            or abs(_dot(lateral, up)) > 0.8):
        return None
    forward = _unit(_cross(up, lateral))
    return forward, _unit(_cross(lateral, forward))


def _head_axes(points):
    """A coarse face-forward proxy from ear centre towards the nose/eye centre, with no fitted face model."""
    nose, left_eye, right_eye, left_ear, right_ear = (points[j] for j in HEAD_ORIENTATION_JOINTS)
    ear_axis, eye_axis = _difference(right_ear, left_ear), _difference(right_eye, left_eye)
    if not (0.08 <= math.hypot(*ear_axis) <= 0.25 and 0.025 <= math.hypot(*eye_axis) <= 0.10):
        return None
    right, eye_right = _unit(ear_axis), _unit(eye_axis)
    if _dot(right, eye_right) < math.cos(math.radians(30)):
        return None
    ears, eyes = _midpoint(left_ear, right_ear), _midpoint(left_eye, right_eye)
    to_nose = _perpendicular(_difference(nose, ears), right)
    to_eyes = _perpendicular(_difference(eyes, ears), right)
    if not (0.025 <= math.hypot(*to_nose) <= 0.20 and 0.025 <= math.hypot(*to_eyes) <= 0.20):
        return None
    if _dot(_unit(to_nose), _unit(to_eyes)) < math.cos(math.radians(60)):
        return None
    forward = _unit(_perpendicular(_difference(_midpoint(nose, eyes), ears), right))
    return forward, _unit(_cross(right, forward))


def _angular_noise(points, joints, axes):
    """Propagate full covariance through the local direction Jacobians.

    Summing each joint's standard-deviation bound allows unknown cross-joint correlation. For each joint the
    larger forward/up directional variance is used, so shoulder-axis uncertainty is retained too.
    """
    bound, step = 0.0, 1e-5
    for j, xyz in points.items():
        columns = []
        for axis in range(3):
            plus, minus = list(xyz), list(xyz)
            plus[axis] += step
            minus[axis] -= step
            hi, lo = axes({**points, j: plus}), axes({**points, j: minus})
            if hi is None or lo is None:
                return math.inf
            columns.append(tuple(tuple((a - b) / (2 * step) for a, b in zip(h, l)) for h, l in zip(hi, lo)))
        covariance = joints[j]["cov"]
        variances = [math.fsum(_dot(columns[a][direction], columns[b][direction]) * covariance[a][b]
                              for a in range(3) for b in range(3)) for direction in range(2)]
        bound += math.sqrt(max(0.0, *variances))
    return math.degrees(bound)


def _orientation(pose, t, ids, axes, model_sd, max_sd, basis):
    joints = _orientation_joints(pose, ids)
    if joints is None or type(t) not in (int, float) or not math.isfinite(t):
        return None
    points = {j: joints[j]["xyz"] for j in ids}
    directions = axes(points)
    if directions is None:
        return None
    forward, up = directions
    horizontal = math.hypot(*forward[:2])
    angular_sd = math.hypot(model_sd, _angular_noise(points, joints, axes))
    if horizontal < 0.20 or not math.isfinite(angular_sd) or angular_sd > max_sd:
        return None
    return Orientation3D(forward, up, math.degrees(math.atan2(forward[0], forward[1])) % 360,
                         math.degrees(math.atan2(forward[2], horizontal)), angular_sd, float(t), ids, basis)


def head_orientation(pose, t):
    """Current measured coarse head direction. Missing, contradictory or noisy five-landmark geometry abstains."""
    return _orientation(pose, t, HEAD_ORIENTATION_JOINTS, _head_axes, HEAD_MODEL_SD_DEG,
                        HEAD_MAX_SD_DEG, "stereo_face_axis")


def torso_orientation(pose, t):
    """Current shoulder/hip-plane direction and uncertainty, independent of the held body_facing summary."""
    return _orientation(pose, t, TORSO_ORIENTATION_JOINTS, _torso_axes, TORSO_MODEL_SD_DEG,
                        TORSO_MAX_SD_DEG, "stereo_torso_plane")


def orientation_projection(value):
    """JSON-safe measured orientation for the people projection, preserving the typed estimate's timestamp."""
    if value is None:
        return None
    return {"forward": list(value.forward), "up": list(value.up), "yaw_deg": value.yaw_deg,
            "pitch_deg": value.pitch_deg, "angular_sd_deg": value.angular_sd_deg,
            "as_of_mono": value.as_of_mono, "source_joint_ids": list(value.source_joint_ids), "basis": value.basis}


def _stereo(entry):
    return (isinstance(entry, dict) and entry.get("lift") == "stereo" and entry.get("status") == "supported"
            and entry.get("xyz") is not None)


def facing_pose(pose):
    """A copy of a den-observe keypoints observation holding only what body_facing may use: both stereo-supported
    shoulders, and both stereo-supported hips when their left-to-right line lies within HIP_AGREE_DEG of the
    shoulders'. None without both shoulders."""
    if not pose or pose.get("kind") != "keypoints":
        return None
    joints = {e.get("j"): e for e in pose.get("joints_room") or () if _stereo(e)}
    if not all(j in joints for j in SHOULDERS):
        return None
    keep = [joints[j] for j in SHOULDERS]
    if all(j in joints for j in HIPS):
        (lx, ly), (rx, ry) = (joints[j]["xyz"][:2] for j in SHOULDERS)
        (hlx, hly), (hrx, hry) = (joints[j]["xyz"][:2] for j in HIPS)
        sx, sy, hx, hy = rx - lx, ry - ly, hrx - hlx, hry - hly
        span = math.hypot(sx, sy) * math.hypot(hx, hy)
        if span > 0 and sx * hx + sy * hy >= math.cos(math.radians(HIP_AGREE_DEG)) * span:
            keep += [joints[j] for j in HIPS]
    return {"kind": "keypoints", "joint_set": pose.get("joint_set"), "joints_room": keep}


def turn(a, b):
    """The unsigned angle between two bearings, 0-180."""
    return abs((b - a + 180.0) % 360.0 - 180.0)


def circular_median(angles):
    """The sample that is nearest, in total turn, to all the others (the newest on a tie: the list is oldest first)."""
    return min(reversed(angles), key=lambda a: sum(turn(a, b) for b in angles))


def head_point(stereo):
    """head_point_m from a track's stereo-supported joints {name: (xyz, status)}, or None with fewer than two face
    joints."""
    seen = [stereo[name][0] for name in FACE if name in stereo]
    if len(seen) < 2:
        return None
    return HeadPoint(tuple(statistics.median(p[i] for p in seen) for i in range(3)), len(seen))


def wrists(stereo):
    """wrists_m from a track's stereo-supported joints: each wrist that's there."""
    return MappingProxyType({name: tuple(stereo[name][0]) for name in WRISTS if name in stereo})


def posture(seated, stereo, speed_mps):
    """posture from this frame's seated (a SeatedState or None), stereo-supported joints and floor speed (or None)."""
    if seated is not None and seated.state:
        return "sitting"
    upright = standing_evidence(body_joints(stereo))
    if speed_mps is not None and speed_mps >= WALK_MPS and (upright or not stereo):
        return "walking"
    return "standing" if upright else "unclear"


class Speed:
    """Each track's floor positions over the last SPEED_S, for posture's walking."""

    def __init__(self):
        self.paths = {}

    def update(self, track_id, t, xy):
        """The floor speed in m/s over the last SPEED_S, or None with under MIN_SPAN_S of positions."""
        path = self.paths.setdefault(track_id, deque())
        path.append((t, xy))
        while path and t - path[0][0] > SPEED_S:
            path.popleft()
        span = path[-1][0] - path[0][0]
        if span < MIN_SPAN_S:
            return None
        (x0, y0), (x1, y1) = path[0][1], path[-1][1]
        return math.hypot(x1 - x0, y1 - y0) / span

    def keep(self, track_ids):
        for gone in set(self.paths) - set(track_ids):
            self.paths.pop(gone, None)


class Facing:
    """Each track's recent facing samples, published as body_facing once per frame."""

    def __init__(self, body_heading, max_sd_deg):
        self.body_heading, self.max_sd = body_heading, max_sd_deg
        self.samples, self.pending, self.revision = {}, {}, {}

    def update(self, track_id, revision, t, pose):
        """This frame's body_facing for one track: pose is the keypoints observation den-track took for it this frame,
        or None/{} without one (the published facing then holds until it expires)."""
        if self.revision.get(track_id) != revision:
            self._forget(track_id)
            self.revision[track_id] = revision
        history = self.samples.setdefault(track_id, deque())
        if history and t - history[-1][0] > PERCEPTION_S:   # expired with the pose: the next sample starts afresh
            history.clear()
            self.pending.pop(track_id, None)
        sample = self._sample(pose)
        if sample is not None:
            published = self._published(history)
            if published is not None and turn(published[0], sample[0]) >= FLIP_DEG:
                if self.pending.get(track_id) is None:
                    self.pending[track_id] = (t, *sample)   # wait for the next sample
                    sample = None
                else:   # the second in a row: the turn is real, and the old side's samples go
                    history.clear()
                    history.append(self.pending.pop(track_id))
            else:
                self.pending.pop(track_id, None)
            if sample is not None:
                history.append((t, *sample))
        while history and history[-1][0] - history[0][0] > WINDOW_S:
            history.popleft()
        published = self._published(history)
        if published is None or t - history[-1][0] > PERCEPTION_S:
            return None
        return BodyFacing(*published)

    def _sample(self, pose):
        copy = facing_pose(pose)
        value = self.body_heading(copy) if copy is not None else None
        if value is None or not all(math.isfinite(v) for v in value) or value[1] > self.max_sd:
            return None
        return float(value[0]), float(value[1])

    @staticmethod
    def _published(history):
        """(deg, sd_deg, as_of_mono, n) over the window, or None with no samples."""
        if not history:
            return None
        deg = circular_median([d for _, d, _ in history])
        return deg, statistics.median(sd for _, _, sd in history), history[-1][0], len(history)

    def keep(self, track_ids):
        """Forget every track but these (the frame's own)."""
        for gone in set(self.revision) - set(track_ids):
            self._forget(gone)
            self.revision.pop(gone, None)

    def _forget(self, track_id):
        self.samples.pop(track_id, None)
        self.pending.pop(track_id, None)

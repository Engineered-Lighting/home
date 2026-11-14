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
  fewer than two. A position only: nothing measures a head direction.
- wrists_m: the stereo-supported wrists, {left_wrist: xyz, right_wrist: xyz}, each only when it's there.

The people projection's heading_deg is body_facing's deg (None without it). The preview reads head_point_m.
"""
import math
import statistics
from collections import deque
from types import MappingProxyType

from contracts import PERCEPTION_S, BodyFacing, HeadPoint

WINDOW_S = 1.0       # the circular median's window, ending at the newest sample
FLIP_DEG = 150.0     # a turn this far from the published facing waits for a second sample
HIP_AGREE_DEG = 60.0  # the hips join the shoulders only when their lines agree this well
FACE = ("nose", "left_eye", "right_eye", "left_ear", "right_ear")
WRISTS = ("left_wrist", "right_wrist")
SHOULDERS = (5, 6)   # coco17 indices, den-observe's joints_room j
HIPS = (11, 12)


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

"""Seated: the coordinator's rule, so den-live's ask and the coordinator agree on who's seated. Standard library.

seated() and its helpers are copied unchanged from the coordinator's tools/den-core/body.py at BODY_PIN (local
claude/den-coordinator); test_ask.py holds every copied function and constant to that commit. When den-live carries the
coordinator's code, import it from there instead and delete the copy. The rule (the coordinator plan's C3):
- becoming seated needs HOLD_S of low hips (the mid-hip 0.30-0.75 m above the floor; with no pair of hips, the face
  below 1.30 m) and a torso moving under 0.15 m/s;
- staying seated needs only low hips on the newest sample, so shifting along a sofa keeps you seated and standing up
  ends it at once;
- a newest sample older than max_gap_s means not seated.
The room frame is den-observe's z_up_metric_floor0, so z is height above the floor.

Seated (below) keeps each track's recent poses and its last answer, and passes that answer back as was_seated.
den-live's joints are {name: [xyz, status]}; body.py's are [x, y, z, confidence], so a supported joint counts as 1.0
and an uncertain one as 0.5 (both over body.py's 0.3).
"""
import math
import statistics
from collections import deque

BODY_PIN = "9d5c802b"   # the coordinator's commit this copy is held to
COPIED = ("_joint", "_mid", "shoulder_mid", "head", "_passes", "_torso_xy", "seated")
CONSTANTS = ("CONF", "HIP_Z", "HEAD_SEATED_MAX_Z", "SEATED_SPEED", "SEATED_HOLD_S", "FACE")
CONF = 0.3
HIP_Z = (0.30, 0.75)
HEAD_SEATED_MAX_Z = 1.30
SEATED_SPEED = 0.15
SEATED_HOLD_S = 2.0
MAX_GAP_S = 3.0
FACE = ("nose", "left_eye", "right_eye", "left_ear", "right_ear")
KEEP_S = SEATED_HOLD_S + 1.0
STATUS_CONF = {"supported": 1.0, "uncertain": 0.5}


# ---- body.py's, unchanged ------------------------------------------------------------------------------------------

def _joint(joints, name, conf=CONF):
    row = (joints or {}).get(name)
    return tuple(row[:3]) if row and len(row) >= 4 and row[3] >= conf else None


def _mid(a, b):
    return tuple((a[i] + b[i]) / 2.0 for i in range(3))


def shoulder_mid(joints):
    left, right = _joint(joints, "left_shoulder"), _joint(joints, "right_shoulder")
    return _mid(left, right) if left and right else None


def head(joints):
    """The median of the face joints seen, or None."""
    seen = [p for p in (_joint(joints, n) for n in FACE) if p]
    return tuple(statistics.median(p[i] for p in seen) for i in range(3)) if seen else None


def _passes(joints):
    left, right = _joint(joints, "left_hip"), _joint(joints, "right_hip")
    if left and right:
        return HIP_Z[0] <= _mid(left, right)[2] <= HIP_Z[1]
    h = head(joints)
    return bool(h) and h[2] < HEAD_SEATED_MAX_Z


def _torso_xy(joints):
    for point in (shoulder_mid(joints), head(joints)):
        if point:
            return point[:2]
    return None


def seated(samples, now, hold_s=SEATED_HOLD_S, was_seated=False, max_gap_s=3.0):
    """samples: [(t, joints)] in time order. Becoming seated needs hold_s of low hips and a still torso.
    Staying seated (was_seated) needs only low hips on the newest sample, so shifting along a sofa keeps you
    seated and standing up ends it at once. A newest sample older than max_gap_s means not seated."""
    if not samples or samples[-1][0] < now - max_gap_s or not _passes(samples[-1][1]):
        return False
    if was_seated:
        return True
    last = samples[-1][0]
    recent = [(t, j) for t, j in samples if last - hold_s - 0.25 <= t <= last]
    if recent[-1][0] - recent[0][0] < hold_s - 0.5:
        return False
    if not all(_passes(j) for _, j in recent):
        return False
    for (t0, j0), (t1, j1) in zip(recent, recent[1:]):
        a, b = _torso_xy(j0), _torso_xy(j1)
        if a and b and t1 > t0 and math.hypot(b[0] - a[0], b[1] - a[1]) / (t1 - t0) > SEATED_SPEED:
            return False
    return True


# ---- den-live's side -----------------------------------------------------------------------------------------------

def body_joints(joints):
    """den-live's room joints {name: [xyz, status]} as body.py's {name: [x, y, z, confidence]}."""
    out = {}
    for name, (xyz, status) in (joints or {}).items():
        if xyz and status in STATUS_CONF:
            out[name] = [xyz[0], xyz[1], xyz[2], STATUS_CONF[status]]
    return out


class Seated:
    """Each track's recent poses and its last answer, so staying seated works as the coordinator's does."""

    def __init__(self):
        self.samples, self.was = {}, {}

    def update(self, track_id, t, joints):
        """One frame's pose for a confirmed track (den-live's joints); a frame with no pose for it adds nothing."""
        converted = body_joints(joints)
        if not converted:
            return
        history = self.samples.setdefault(track_id, deque())
        history.append((t, converted))
        while history and t - history[0][0] > KEEP_S:
            history.popleft()

    def seated(self, track_id, now):
        if track_id not in self.samples:   # never posed: not seated, and nothing kept for it
            self.was.pop(track_id, None)
            return False
        result = seated(list(self.samples[track_id]), now, was_seated=self.was.get(track_id, False))
        self.was[track_id] = result
        for gone in [k for k, h in self.samples.items() if not h or now - h[-1][0] > MAX_GAP_S]:
            self.samples.pop(gone, None)
            self.was.pop(gone, None)
        return result

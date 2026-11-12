"""A person's body, from den-live's 3D skeleton: seated or not, facing, head, task point (coordinator plan C3).

Joints are a dict name -> [x, y, z, confidence] in room metres, Z up, floor at 0, with COCO names (nose,
left_eye, right_eye, left_ear, right_ear, left_shoulder, right_shoulder, left_wrist, right_wrist, left_hip,
right_hip, ...).

- seated(samples, now): THE seated test, shared with den-live's router and the labels line. Seated when the
  mid-hip point is 0.30-0.75 m above the floor and the torso moves under 0.15 m/s, over the last 2 s. With no
  hips seen, the head below 1.30 m stands in. Getting up is seen on the next sample: the newest must pass too.
- facing(): from the shoulder labels, checked by the nose; never "away from the camera" (Codex 16).
- task_point(): the wrists' midpoint a little ahead, else aim.py's shoulder estimate, else the seat mark.
- ShiftFilter: a new aim only when every point in the last hold_s is more than shift_m from the current aim,
  so a page turn never moves the spot (Codex 14). Both numbers are owner settings.

Standard library only. Pure.
"""
import math
import statistics

CONF = 0.3
WRIST_CONF = 0.5
HIP_Z = (0.30, 0.75)
HEAD_SEATED_MAX_Z = 1.30
SEATED_SPEED = 0.15
SEATED_HOLD_S = 2.0
MIN_SHOULDER_SPAN_M = 0.10
BOOK_AHEAD_M = 0.08
HAND_FORWARD_M, HAND_DOWN_M = 0.35, 0.25  # aim.py's hand point
SEAT_MARK_RADIUS_M = 1.0
FACE = ("nose", "left_eye", "right_eye", "left_ear", "right_ear")


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


def facing(joints):
    """A horizontal unit vector the person faces, or None when it can't be told."""
    left, right = _joint(joints, "left_shoulder"), _joint(joints, "right_shoulder")
    if not (left and right):
        return None
    sx, sy = right[0] - left[0], right[1] - left[1]
    span = math.hypot(sx, sy)
    if span < MIN_SHOULDER_SPAN_M:
        return None
    f = (-sy / span, sx / span)  # anatomical labels: right minus left, turned a quarter to the front
    nose, mid = _joint(joints, "nose"), _mid(left, right)
    if nose:
        vx, vy = nose[0] - mid[0], nose[1] - mid[1]
        if math.hypot(vx, vy) > 0.05 and vx * f[0] + vy * f[1] < 0:
            f = (-f[0], -f[1])  # the nose disagrees with the labels: trust the nose
    return f


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


def task_point(joints, face, seat_mark=None):
    """(point, source): where a seated person's book or work is. None when nothing places it."""
    left, right = _joint(joints, "left_wrist", WRIST_CONF), _joint(joints, "right_wrist", WRIST_CONF)
    if left and right and face:
        m = _mid(left, right)
        return (m[0] + face[0] * BOOK_AHEAD_M, m[1] + face[1] * BOOK_AHEAD_M, m[2]), "wrists"
    mid = shoulder_mid(joints)
    if mid and face:
        return (mid[0] + face[0] * HAND_FORWARD_M, mid[1] + face[1] * HAND_FORWARD_M, mid[2] - HAND_DOWN_M), "shoulders"
    torso = _torso_xy(joints)
    if seat_mark and torso and math.hypot(seat_mark[0] - torso[0], seat_mark[1] - torso[1]) <= SEAT_MARK_RADIUS_M:
        return tuple(seat_mark), "seat_mark"
    return None, None


class ShiftFilter:
    """Holds a person's aim still through page turns and fidgets."""

    def __init__(self, shift_m=0.10, hold_s=1.5):
        self.shift_m, self.hold_s = shift_m, hold_s
        self.aim, self.history = None, []

    def update(self, t, point):
        """Feed one task point. Returns the new aim when it moves, else None."""
        if point is None:
            return None
        self.history = [(s, p) for s, p in self.history if s >= t - self.hold_s - 0.5] + [(t, tuple(point))]
        if self.aim is None:
            self.aim = tuple(point)
            return self.aim
        window = [p for s, p in self.history if s >= t - self.hold_s]
        covered = self.history[0][0] <= t - self.hold_s
        if covered and all(math.dist(p, self.aim) > self.shift_m for p in window):
            self.aim = tuple(statistics.median(p[i] for p in window) for i in range(3))
            return self.aim
        return None

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

den-live's side adds hysteresis to the copy (held_seated, below; the copy itself is unchanged): becoming seated also
counts a head under 1.40 m, and staying seated ends on two consecutive samples of clear standing evidence (a head
at 1.50 m or more; with no head, a mid-hip over 0.95 m; or the mid-hip 0.35 m over the knees, unless the head is at
seated height, under 1.40 m), or at once on one sample with the head at 1.60 m and the mid-hip at 0.90 m or more.
Hips at the sofa band's edge no longer end it.

Seated (below) keeps each track's recent poses and its last answer, passes that answer back as was_seated, and asks
held_seated.
den-live's joints are {name: [xyz, status]}; body.py's are [x, y, z, confidence], so a supported joint counts as 1.0
and an uncertain one as 0.5 (both over body.py's 0.3).

Published (below) is contract 2's seated {state, since_mono, basis}: perceive.py works it out once per track per frame
and puts it on the track's evidence. The ask and the preview read it and never work it out again, so there's one answer.
"""
import math
import statistics
from collections import deque

from contracts import SeatedState

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

# den-live's hysteresis around the copied rule (the spine, 2026-11-14). Measured on LA with the owner seated (671 frames
# over 163 s): their mid-hip sits on HIP_Z's top (mid-hip - 0.75 m: p10 -0.14, p50 +0.01, p90 +0.10), so the copy's
# "staying seated needs low hips on the newest sample" ended seated 42 times while they sat. Seated heads were well
# apart from standing ones (head - 1.30 m: p5 -0.25, p50 -0.05, p95 -0.04; a standing head is 1.6 m or more), and so was
# the mid-hip above the knees (p50 +0.08, p95 +0.23; standing about +0.4 or more), though the knees are often missing.
HEAD_ENTER_Z = 1.40       # becoming seated: the copy's low hips, or a head below this (held and still, as the copy's)
HEAD_STAND_Z = 1.50       # clear standing evidence: a head at or above this (0.24 m over the seated p95)
HIP_STAND_Z = 0.95        # or, with no head, a mid-hip above this (0.10 m over the seated p90)
HIP_OVER_KNEE_M = 0.35    # or the mid-hip this far above the knees seen (0.12 m over the seated p95)
STAND_SAMPLES = 2         # staying seated ends only on this many consecutive samples of that evidence,
HEAD_UP_Z, HIP_UP_Z = 1.60, 0.90   # or at once on one sample with both the head and the mid-hip this high: a body
                                   # that has stood up (seated, the head's p95 was 1.26 m and the mid-hip's p90 0.85 m)


def standing_evidence(joints):
    """Clear evidence of someone standing on one sample (body.py's joints): a high head, or with no head high hips, or
    hips well above the knees. Anything less (hips on the sofa band's edge, a missing face) is no evidence, and a head
    at seated height (under HEAD_ENTER_Z) is none whatever the hips or knees say: no standing adult's head is that low,
    and a knee point found at the feet would otherwise unseat a reader."""
    face = head(joints)
    left, right = _joint(joints, "left_hip"), _joint(joints, "right_hip")
    hip = _mid(left, right) if left and right else None
    knees = [k for k in (_joint(joints, "left_knee"), _joint(joints, "right_knee")) if k]
    if face is not None and face[2] >= HEAD_STAND_Z:
        return True
    if face is not None and face[2] < HEAD_ENTER_Z:
        return False
    if face is None and hip is not None and hip[2] > HIP_STAND_Z:
        return True
    return hip is not None and bool(knees) and hip[2] - sum(k[2] for k in knees) / len(knees) > HIP_OVER_KNEE_M


def stood_up(joints):
    """One sample that is plainly standing: the head and the mid-hip both up. It ends seated at once, so standing up
    is as quick as ever; one signal alone (a stray face point, hips at the band's edge) never is."""
    face = head(joints)
    left, right = _joint(joints, "left_hip"), _joint(joints, "right_hip")
    return bool(face and left and right and face[2] >= HEAD_UP_Z and _mid(left, right)[2] >= HIP_UP_Z)


def _enters(joints):
    face = head(joints)
    return _passes(joints) or (face is not None and face[2] < HEAD_ENTER_Z)


def held_seated(samples, now, was_seated=False, hold_s=SEATED_HOLD_S, max_gap_s=MAX_GAP_S):
    """The copy's seated() with den-live's hysteresis. Becoming seated: hold_s of samples that pass the copy's low hips
    or have a head under HEAD_ENTER_Z, with a still torso (the copy's own hold and speed test). Staying seated
    (was_seated): until STAND_SAMPLES consecutive samples show clear standing evidence, or one sample has stood up (the
    head and the hips both up: at once, as the copy did), so hips at the band's edge never end it. A newest sample
    older than max_gap_s means not seated, as the copy's."""
    if not samples or samples[-1][0] < now - max_gap_s:
        return False
    if was_seated:
        if stood_up(samples[-1][1]):
            return False
        newest = samples[-STAND_SAMPLES:]
        return not (len(newest) == STAND_SAMPLES and all(standing_evidence(j) for _, j in newest))
    if not _enters(samples[-1][1]):
        return False
    last = samples[-1][0]
    recent = [(t, j) for t, j in samples if last - hold_s - 0.25 <= t <= last]
    if recent[-1][0] - recent[0][0] < hold_s - 0.5:
        return False
    if not all(_enters(j) for _, j in recent):
        return False
    for (t0, j0), (t1, j1) in zip(recent, recent[1:]):
        a, b = _torso_xy(j0), _torso_xy(j1)
        if a and b and t1 > t0 and math.hypot(b[0] - a[0], b[1] - a[1]) / (t1 - t0) > SEATED_SPEED:
            return False
    return True


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
        result = held_seated(list(self.samples[track_id]), now, was_seated=self.was.get(track_id, False))
        self.was[track_id] = result
        for gone in [k for k, h in self.samples.items() if not h or now - h[-1][0] > MAX_GAP_S]:
            self.samples.pop(gone, None)
            self.was.pop(gone, None)
        return result


class Published:
    """Contract 2's seated {state, since_mono, basis}, once per track per frame: Seated's rule on the track's
    stereo-supported joints, started again whenever the track's association_revision moves.
    - since_mono: the capture time of the first sample of the hold that qualified (seated()'s own window), kept for as
      long as the track stays seated;
    - basis: hips when the newest sample has both hips, else head (seated()'s stand-in for the hips).
    Not seated is SeatedState(False, None, None)."""

    def __init__(self):
        self.rule, self.since, self.revision = Seated(), {}, {}

    def update(self, track_id, revision, t, joints):
        """This frame's answer for one track; joints is its stereo-supported pose now, or {} without one."""
        if self.revision.get(track_id) != revision:
            self._forget(track_id)
            self.revision[track_id] = revision
        self.rule.update(track_id, t, joints)
        if not self.rule.seated(track_id, t):
            self.since.pop(track_id, None)
            return SeatedState(False, None, None)
        samples = self.rule.samples[track_id]
        if track_id not in self.since:
            last = samples[-1][0]
            self.since[track_id] = next(at for at, _ in samples if last - SEATED_HOLD_S - 0.25 <= at)
        newest = samples[-1][1]
        both_hips = _joint(newest, "left_hip") and _joint(newest, "right_hip")
        return SeatedState(True, self.since[track_id], "hips" if both_hips else "head")

    def keep(self, track_ids):
        """Forget every track but these (the frame's own)."""
        for gone in set(self.revision) - set(track_ids):
            self._forget(gone)
            self.revision.pop(gone, None)

    def _forget(self, track_id):
        self.rule.samples.pop(track_id, None)
        self.rule.was.pop(track_id, None)
        self.since.pop(track_id, None)

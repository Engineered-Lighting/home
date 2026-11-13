"""A NeedV1 instance and its evidence lifetime for the current seated episode."""

import math
from collections import deque

from contracts import INFERENCE_S


def field(record, name, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def pose(track):
    """Adapt the supported room-space joints to the pinned core's COCO rows."""
    joints = field(track, "supported_pose") or {}
    result = {}
    for name, row in joints.items():
        if isinstance(row, (list, tuple)):
            xyz = row[0] if len(row) == 2 and row[1] == "supported" else row[:3] if len(row) == 4 and row[3] >= .5 else None
        else:
            xyz = field(row, "xyz_m", field(row, "point_m")) if field(row, "support_count", 0) else None
        if isinstance(xyz, (tuple, list)) and len(xyz) == 3 and all(map(finite, xyz)):
            result[name] = [*xyz, 1.0]
    return result


def seated(track, captured):
    """Contract 2's seated, as perception published it on the track: (True, since_mono) while seated with a hold that
    began at or before this frame, else (False, None). The preview reads it and never works it out again."""
    value = field(track, "seated")
    since = field(value, "since_mono")
    if field(value, "state") is True and finite(since) and since <= captured:
        return True, since
    return False, None


def activity(facts):
    word = field(facts, "activity_hypothesis")
    if field(facts, "people_visible") != "one" or word in (None, "unclear"):
        return "unknown"
    visible = field(facts, "visible_basis") or ()
    phone = "phone_screen_toward_face" in visible or (
        field(facts, "held_object") == "phone" and field(facts, "gaze_target") == "phone")
    screen = "hands_on_keyboard" in visible or field(facts, "typing_visible") == "yes" or (
        field(facts, "screen_facing_subject") == "yes" and field(facts, "gaze_target") == "screen")
    closed_book = field(facts, "book_state") == "closed" or "book_closed" in visible
    if phone or screen or (word == "reading" and closed_book):
        return "idle"
    return "playing" if word in ("reading", "hobby", "desk_hobby", "craft") else "idle"


class Episode:
    def __init__(self, policy, binding, started, frame_binding):
        self.need, self.binding, self.started = policy, binding, started
        self.frame_binding = frame_binding
        self.last_observed = None
        self.valid_until = None
        self.requests = deque(maxlen=64)

    def expire(self, now, patience):
        idle = self.need.idle_since
        if (self.valid_until is not None and now >= self.valid_until) or (
                idle is not None and self.need.engaged and now >= idle + patience):
            self.clear_support()

    def clear_support(self):
        self.need.engaged = False
        self.need.playing = 0
        self.need.idle_since = self.need.onset_ts = self.need.support = None
        self.valid_until = None

    def accept(self, reply, now, patience):
        keys = ("service_run_id", "camera_epoch", "track_id", "association_revision")
        if tuple(field(reply, k) for k in keys) != self.binding:
            return False
        request = field(reply, "request_id")
        association = field(reply, "association")
        start = field(association, "window_start_mono")
        end = field(association, "window_end_mono")
        observed, available, valid = (field(reply, k) for k in ("observed_mono", "available_mono", "valid_until_mono"))
        if (not isinstance(request, str) or not request or request in self.requests or
                field(association, "safe") is not True or field(association, "status") != "confident" or
                not all(map(finite, (start, end, observed, available, valid))) or
                not self.started <= start <= end == observed <= available <= now < valid or
                (self.last_observed is not None and observed <= self.last_observed)):
            return False
        frames = field(reply, "frames", ())
        frame_keys = ("service_run_id", "camera_epoch", "transform_id", "scan_revision")
        expected = (*self.binding[:2], *self.frame_binding)
        if not frames or any(tuple(field(frame, key) for key in frame_keys) != expected for frame in frames):
            return False
        captures = tuple(field(frame, "capture_mono") for frame in frames)
        if (not all(map(finite, captures)) or captures[0] != start or captures[-1] != end or
                any(left >= right for left, right in zip(captures, captures[1:]))):
            return False
        observation = field(reply, "observation")
        facts = field(observation, "facts")
        status = activity(facts) if field(observation, "status", "valid") == "valid" else "unknown"
        self.expire(now, patience)
        self.last_observed = observed
        self.requests.append(request)
        self.need.exit_idle_s = patience
        if field(observation, "status") == "valid" and field(facts, "people_visible") in ("none", "multiple"):
            self.clear_support()
            self.started = observed
            return True
        if status == "unknown":
            self.need.playing = 0
            return True
        self.need._dwell({"status": status, "observed_at": observed, "available_at": available,
                          "answered": True, "request_id": request, "facts": facts}, now)
        self.valid_until = min(valid, observed + INFERENCE_S)
        self.expire(now, patience)
        return True

    def deadline(self, patience):
        if not self.need.engaged or self.valid_until is None:
            return None
        if self.need.idle_since is None:
            return self.valid_until
        return min(self.valid_until, self.need.idle_since + patience)

"""Submit the exact frame retained by the detector and fence late body replies."""
import math
import statistics
import threading
import time
from collections import deque

from body_clock import lease_clock
from stream import BODY_LEASE_S

# The least time between two body jobs. Under the camera's own frame interval (about 0.24 s at 4.2 frames a second), so
# every tracked frame can carry a body: 0.3 s skipped every other frame and held the figure to 2 a second. The worker
# answers a body before the next frame (one pipe, in order). A frame still at the detector, or a Qwen stills request
# waiting for the process's one optional slot (perceive.DetectorWorker.send_alone), skips its body (busy).
MIN_INTERVAL_S = 0.2
# A frame's misses for the same person: the last published frame keeps its own lease through them, so one frame
# without a pose or enough depth doesn't blank the figure. Anything that changes the scene (empty, ambiguous, a new
# scope or track, the detector stopping) clears at once.
HOLD_REASONS = frozenset(("pose_unavailable", "insufficient_depth", "stale"))
TIMING_STAGES = ("body_queue", "mask_wait", "body_compute", "segment_queue", "segment_inference", "body_roundtrip")
TIMING_SAMPLES = 100


def _milliseconds(value):
    return type(value) in (int, float) and 0 <= value <= BODY_LEASE_S * 1000 and math.isfinite(value)


class BodyLink:
    def __init__(self, detector, *, stream, min_interval_s=MIN_INTERVAL_S, clock=lease_clock, monotonic=time.monotonic):
        self.detector, self.stream, self.clock = detector, stream, clock
        self.monotonic = monotonic
        self.min_interval_s = min_interval_s
        self.lock = threading.Lock()
        self.scope = self.track = self.binding = self.pending = None
        self.association_revision = None
        self.setting_up = self.bound = None
        self.reset_learning = True
        self.request_id, self.generation = 0, 0
        self.facing = self.facing_candidate = None
        self.last_sent = self.last_clear = self.why = self.stopped = self.compute_s = None
        self.shown_until = None   # the lease end of the last published frame
        self.closed = False
        self._timings = deque(maxlen=TIMING_SAMPLES)
        self.counts = dict(sent=0, skipped_busy=0, skipped_rate=0, frames=0, empty=0, errors=0)
        detector.on_body = self._reply

    def _clear(self, reason, *, reset=True):
        self.generation += 1
        self.facing = self.facing_candidate = None
        if reset:
            self.reset_learning = True
        self.why = reason
        if reason in HOLD_REASONS and self.shown_until is not None and self.clock() < self.shown_until:
            return   # the figure stays until its own lease ends (Home clears it then) or the next frame replaces it
        self.shown_until = None
        if self.scope is not None and reason != self.last_clear:
            self.stream.publish_body(self.scope, reason=reason)
            self.last_clear = reason

    def _scope(self, scope, binding, track, association_revision=None):
        if (scope, binding, track, association_revision) != (self.scope, self.binding, self.track, self.association_revision):
            self.scope, self.binding, self.track = scope, binding, track
            self.association_revision = association_revision
            self._timings.clear()
            self.last_sent = self.last_clear = self.shown_until = None
            self._clear("scope_changed")

    def clear(self, reason, *, scope=None):
        with self.lock:
            if scope is not None and scope != self.scope:
                self._scope(scope, None, None)
            self._clear(reason)

    def prepare(self, scope, binding, track, association_revision=None):
        with self.lock:
            self._scope(scope, binding, track, association_revision)

    def _finish_setup(self, reply):
        if self.setting_up is None or reply.get("binding") != self.setting_up:
            return
        self.bound = self.setting_up if reply.get("body_ready") else None
        self.setting_up = None
        if self.bound is None:
            self.counts["errors"] += 1
            self.stopped = "body setup refused"
            self._clear("unavailable")

    def _facing(self, current):
        if current is None:
            self.facing_candidate = None
            return None

        def distance(a, b):
            return abs((a - b + 180) % 360 - 180)
        if self.facing is not None and distance(current, self.facing) >= 150:
            if self.facing_candidate is None or distance(current, self.facing_candidate) > 45:
                self.facing_candidate = current
                return None
        self.facing, self.facing_candidate = current, None
        return current

    def _off(self):
        self.stopped = "detector stopped"
        self.pending = self.setting_up = None
        self._clear("unavailable")

    def _reply(self, reply):
        with self.lock:
            if self.closed:
                return
            if reply is None:
                self._off()
                return
            if "body_ready" in reply or "body_refused" in reply:
                self._finish_setup(reply)
                return
            pending = self.pending
            if reply.get("error") == "ProtocolError":
                self.pending = self.setting_up = None
                self.bound = None
                self.counts["errors"] += 1
                self._clear("unavailable")
                return
            if pending is None or pending["request_id"] != reply.get("request_id"):
                return
            self.pending = None
            if pending["generation"] != self.generation:
                return
            if (reply.get("body_id") != pending["id"] or reply.get("frame_key") != pending["frame_key"]
                    or reply.get("binding") != self.binding or "error" in reply):
                self.counts["errors"] += 1
                self._clear("unavailable")
                return
            self.compute_s = reply.get("compute_s")
            capture_mono = pending["capture_mono"]
            if self.clock() >= capture_mono + BODY_LEASE_S:
                self._clear("stale")
                return
            frame = reply.get("frame")
            if frame is None:
                self.counts["empty"] += 1
                self._clear(reply.get("why") or "unavailable", reset=pending["observation"] != "empty")
                return
            if pending["observation"] != "person" or frame.get("track_id") != self.track:
                self._clear("unavailable")
                return
            frame = {**frame, "facing_deg": self._facing(frame.get("facing_deg"))}
            if self.stream.publish_body(self.scope, frame=frame, capture_mono=capture_mono):
                self.counts["frames"] += 1
                self.why = self.last_clear = None
                self.shown_until = capture_mono + BODY_LEASE_S
                self._record_timing(reply, pending)
            else:
                self.shown_until = None
                self._clear("stale")

    def _record_timing(self, reply, pending):
        now = self.monotonic()
        values = reply.get("timings_ms")
        values = values if isinstance(values, dict) else {}
        sample = {key: round(float(values[key]), 3) for key in TIMING_STAGES[:-1]
                  if _milliseconds(values.get(key))}
        if "body_compute" not in sample and type(reply.get("compute_s")) in (int, float):
            compute_ms = reply["compute_s"] * 1000
            if _milliseconds(compute_ms):
                sample["body_compute"] = round(compute_ms, 3)
        elapsed = (now - pending["submitted_mono"]) * 1000
        if _milliseconds(elapsed):
            sample["body_roundtrip"] = round(elapsed, 3)
        if sample and math.isfinite(now):
            self._timings.append({"at_mono": now, "request_id": pending["request_id"],
                                  "body_id": pending["id"], "timings_ms": sample})

    def timing_summary(self):
        """Numeric performance samples admitted after the same identity and expiry gates as body publication."""
        with self.lock:
            stages = {}
            for key in TIMING_STAGES:
                values = sorted(sample["timings_ms"][key] for sample in self._timings if key in sample["timings_ms"])
                if values:
                    stages[key] = {"n": len(values), "p50": round(statistics.median(values), 3),
                                   "p95": values[math.ceil(len(values) * .95) - 1], "max": values[-1]}
            age = (self.monotonic() - self._timings[-1]["at_mono"]) * 1000 if self._timings else None
            return {"samples": len(self._timings), "limit": TIMING_SAMPLES, "clock": "host_monotonic",
                    "last_age_ms": round(age, 3) if age is not None and math.isfinite(age) and age >= 0 else None,
                    "timings_ms": stages}

    def submit(self, frame_id, *, frame_key, scope, setup, capture_mono, observation, person=None):
        """Queue setup or one exact-frame job, without waiting for a reply."""
        with self.lock:
            if self.closed:
                return False
            binding = setup["binding"]
            self._scope(scope, binding, person["track_id"] if person is not None else None,
                        person.get("association_revision") if person is not None else None)
            if self.stopped is not None:
                self._clear("unavailable")
                return False
            now = self.clock()
            if (type(capture_mono) not in (int, float) or not math.isfinite(capture_mono)
                    or capture_mono > now or now >= capture_mono + BODY_LEASE_S):
                self._clear("stale")
                return False
            if observation == "empty" and self.last_clear in (None, "scope_changed"):
                self._clear("empty", reset=False)
            if self.pending is not None or self.setting_up is not None:
                self.counts["skipped_busy"] += 1
                return False
            if self.bound != binding:
                self.setting_up = binding
                header = {"kind": "setup_body", "setup": setup}
            else:
                if observation == "person" and self.last_sent is not None and now - self.last_sent < self.min_interval_s:
                    self.counts["skipped_rate"] += 1
                    return False
                self.request_id += 1
                header = {"kind": "body", "id": frame_id, "request_id": self.request_id, "now": capture_mono,
                          "frame_key": frame_key, "binding": binding, "observation": observation, "person": person,
                          "reset_learning": self.reset_learning, "remaining_s": capture_mono + BODY_LEASE_S - now}
                previous_sent = self.last_sent
                self.pending = {**header, "generation": self.generation, "capture_mono": capture_mono,
                                "submitted_mono": self.monotonic()}
                self.reset_learning = False
                self.last_sent = now
                self.counts["sent"] += 1
        try:
            admitted = self.detector.send_alone(header)
        except Exception:
            with self.lock:
                self._off()
            return False
        if admitted is False:
            with self.lock:
                self.counts["skipped_busy"] += 1
                if header["kind"] == "setup_body":
                    self.setting_up = None
                elif self.pending is not None and self.pending["request_id"] == header["request_id"]:
                    if self.pending["generation"] == self.generation:
                        self.last_sent = previous_sent
                    self.reset_learning |= header["reset_learning"]
                    self.pending = None
                    self.counts["sent"] -= 1
            return False
        return header["kind"] == "body"

    def close(self):
        with self.lock:
            self.closed = True
            if self.detector.on_body == self._reply:
                self.detector.on_body = None

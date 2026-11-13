"""Submit the exact frame retained by the detector and fence late body replies."""
import math
import threading
import time

from stream import BODY_LEASE_S

MIN_INTERVAL_S = 0.3


class BodyLink:
    def __init__(self, detector, *, stream, min_interval_s=MIN_INTERVAL_S, clock=time.monotonic):
        self.detector, self.stream, self.clock = detector, stream, clock
        self.min_interval_s = min_interval_s
        self.lock = threading.Lock()
        self.scope = self.track = self.binding = self.pending = None
        self.setting_up = self.bound = None
        self.reset_learning = True
        self.request_id, self.generation = 0, 0
        self.facing = self.facing_candidate = None
        self.last_sent = self.last_clear = self.why = self.stopped = self.compute_s = None
        self.closed = False
        self.counts = dict(sent=0, skipped_busy=0, skipped_rate=0, frames=0, empty=0, errors=0)
        detector.on_body = self._reply

    def _clear(self, reason, *, reset=True):
        self.generation += 1
        self.facing = self.facing_candidate = None
        if reset:
            self.reset_learning = True
        self.why = reason
        if self.scope is not None and reason != self.last_clear:
            self.stream.publish_body(self.scope, reason=reason)
            self.last_clear = reason

    def _scope(self, scope, binding, track):
        if (scope, binding, track) != (self.scope, self.binding, self.track):
            self.scope, self.binding, self.track = scope, binding, track
            self.last_sent = self.last_clear = None
            self._clear("scope_changed")

    def clear(self, reason, *, scope=None):
        with self.lock:
            if scope is not None and scope != self.scope:
                self._scope(scope, None, None)
            self._clear(reason)

    def prepare(self, scope, binding, track):
        with self.lock:
            self._scope(scope, binding, track)

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
            else:
                self._clear("stale")

    def submit(self, frame_id, *, frame_key, scope, setup, capture_mono, observation, person=None):
        """Queue setup or one exact-frame job, without waiting for a reply."""
        with self.lock:
            if self.closed:
                return False
            binding = setup["binding"]
            self._scope(scope, binding, person["track_id"] if person is not None else None)
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
                          "reset_learning": self.reset_learning}
                self.pending = {**header, "generation": self.generation, "capture_mono": capture_mono}
                self.reset_learning = False
                self.last_sent = now
                self.counts["sent"] += 1
        try:
            self.detector.send_alone(header)
        except Exception:
            with self.lock:
                self._off()
            return False
        return header["kind"] == "body"

    def close(self):
        with self.lock:
            self.closed = True
            if self.detector.on_body == self._reply:
                self.detector.on_body = None

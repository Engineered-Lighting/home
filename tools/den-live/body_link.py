"""The loop's side of the live body. Standard library.

The body is made in the detector's own process (detect_worker.py, body.py's BodyChannel), from the bundle that process
just detected on, so a frame crosses the pipe once and numpy loads once. This asks for it without ever waiting:
- submit() asks for the body of the frame just detected, only while no body is in flight, at most once every
  min_interval_s while someone is there (each frame is ~75 KB), and on every frame while nobody is (to learn the room).
- The replies come on the detector's reader thread, to on_frame.
- A body that won't set up, or a detector that stops, turns the body off (stopped says why). The markers go on.
"""
import threading

MIN_INTERVAL_S = 0.3   # at most about 3 body frames a second while someone is there: Home interpolates between them


class BodyLink:
    def __init__(self, detector, *, setup, on_frame, start_timeout_s=30, min_interval_s=MIN_INTERVAL_S):
        self.detector, self.on_frame, self.min_interval_s = detector, on_frame, min_interval_s
        self.lock, self.busy, self.alive, self.last_sent = threading.Lock(), False, False, None
        self.counts = {"sent": 0, "skipped_busy": 0, "skipped_rate": 0, "frames": 0, "empty": 0, "errors": 0}
        self.why = None             # why the last frame had no body ("learning the empty room", ...)
        self.stopped = None         # why the body is off, once it is
        self.compute_s = None       # the last body step's time in the detector's process
        self.ready = threading.Event()
        detector.on_body = self._reply
        try:
            detector.send_alone({"kind": "setup_body", "setup": setup})
        except Exception:
            self._off("the detector process stopped")
            return
        if not self.ready.wait(start_timeout_s) and self.stopped is None:
            self._off("the body didn't set up in %d s" % start_timeout_s)

    def _off(self, why):
        with self.lock:
            if self.stopped is None:
                self.stopped = why
            self.alive = self.busy = False
        self.ready.set()

    def _reply(self, reply):
        if reply is None:
            self._off("the detector process stopped")
            return
        if "body_ready" in reply or "body_refused" in reply:
            if reply.get("body_ready"):
                with self.lock:
                    self.alive = self.stopped is None
                self.ready.set()
            else:
                self._off("the body didn't set up (%s)" % reply.get("body_refused"))
            return
        with self.lock:
            self.busy = False
        if "error" in reply:
            self.counts["errors"] += 1
            return
        self.compute_s = reply.get("compute_s")
        frame = reply.get("frame")
        if frame is None:
            self.counts["empty"] += 1
            self.why = reply.get("why")
            return
        self.counts["frames"] += 1
        self.why = None
        try:
            self.on_frame(frame)
        except Exception:   # the publisher's failure is the publisher's; the body goes on
            self.counts["errors"] += 1

    def submit(self, frame_id, *, now, capture_ts, seq, person):
        """Ask for the body of the frame the detector just detected (frame_id); returns whether it was asked for."""
        with self.lock:
            if not self.alive:
                return False
            if self.busy:
                self.counts["skipped_busy"] += 1
                return False
            if person is not None and self.last_sent is not None and now - self.last_sent < self.min_interval_s:
                self.counts["skipped_rate"] += 1
                return False
            self.busy, self.last_sent = True, now
        try:
            self.detector.send_alone({"kind": "body", "id": frame_id, "now": now, "capture_ts": capture_ts, "seq": seq,
                                      "person": person})
        except Exception:
            self._off("the detector process stopped")
            return False
        self.counts["sent"] += 1
        return True

    def close(self):
        """Nothing of its own to close: the detector's process is the detector's."""
        with self.lock:
            self.alive = False
        if self.detector.on_body == self._reply:
            self.detector.on_body = None

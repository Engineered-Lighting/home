"""The loop's side of body_worker.py: one process in the detector's venv, fed frames without waiting. Standard library.

The people markers never wait for the body. submit() hands a frame over only while the body process is idle, and
otherwise skips it, counted. A reader thread passes each frame the process makes to on_frame. A body process that
stops, refuses or answers out of protocol turns the body off (stopped says why) and the markers go on.
"""
import json
import struct
import subprocess
import threading


MIN_INTERVAL_S = 0.3   # at most about 3 body frames a second: Home interpolates between them, and each is ~120 KB


class BodyLink:
    def __init__(self, argv, log_path, *, setup, on_frame, start_timeout_s=60, min_interval_s=MIN_INTERVAL_S):
        self.on_frame, self.log = on_frame, open(log_path, "ab")
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.lock, self.busy, self.next_id, self.alive = threading.Lock(), False, 0, False
        self.counts = {"sent": 0, "skipped_busy": 0, "skipped_rate": 0, "frames": 0, "empty": 0, "errors": 0}
        self.min_interval_s, self.last_sent = min_interval_s, None
        self.why = None             # why the last frame had no body ("learning the empty room", ...)
        self.stopped = None         # why the body is off, once it is
        self.first, ready = None, threading.Event()
        threading.Thread(target=self._read, args=(ready,), daemon=True, name="den-live-body-reader").start()
        try:
            self._send(json.dumps({"setup": setup}).encode("utf-8"))
        except (OSError, ValueError):
            pass
        ready.wait(start_timeout_s)
        if (self.first or {}).get("ready"):
            with self.lock:
                self.alive = self.stopped is None
        else:
            self._off("the body process didn't start: %s" % ((self.first or {}).get("refused") or "no answer"))

    def _read(self, ready):
        try:
            for raw in self.proc.stdout:
                try:
                    reply = json.loads(raw.decode("utf-8"))
                except ValueError:
                    self._off("the body process wrote something that isn't its protocol")
                    break
                if self.first is None:
                    self.first = reply
                    ready.set()
                    continue
                self._reply(reply)
        except (OSError, ValueError):
            pass
        finally:
            if (self.first or {}).get("ready"):   # before that, the constructor says why it didn't start
                self._off("the body process stopped")
            ready.set()

    def _send(self, *parts):
        self.proc.stdin.write(b"".join(struct.pack(">I", len(p)) + p for p in parts))
        self.proc.stdin.flush()

    def _off(self, why):
        with self.lock:
            if self.stopped is None:
                self.stopped = why
            self.alive = self.busy = False

    def _reply(self, reply):
        with self.lock:
            self.busy = False
        if "error" in reply:
            self.counts["errors"] += 1
            return
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

    def submit(self, bundle_data, *, now, capture_ts, seq, person):
        """Hand one frame over if the body process is idle; returns whether it was sent."""
        with self.lock:
            if not self.alive:
                return False
            if self.busy:
                self.counts["skipped_busy"] += 1
                return False
            if person is not None and self.last_sent is not None and now - self.last_sent < self.min_interval_s:
                self.counts["skipped_rate"] += 1   # nobody there: every frame, to learn the empty room sooner
                return False
            self.busy, self.next_id, self.last_sent = True, self.next_id + 1, now
            frame_id = self.next_id
        header = json.dumps({"id": frame_id, "now": now, "capture_ts": capture_ts, "seq": seq, "person": person},
                            separators=(",", ":")).encode("utf-8")
        try:
            self._send(header, bundle_data)
        except (OSError, ValueError):
            self._off("the body process stopped")
            return False
        self.counts["sent"] += 1
        return True

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait(5)
        finally:
            if self.proc.stdout:
                self.proc.stdout.close()
            self.log.close()

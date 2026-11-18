"""Asynchronous exact-frame bridge to the optional loopback model worker.

The camera thread only replaces one pending reference. Decoding, private blob
I/O, HTTP, clip construction and result validation run on this separate thread.
Disabled/unadmitted workers do not cause frames to be decoded or persisted.
"""
import dataclasses
import hashlib
import io
import http.client
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
import zipfile
from collections import deque

import intent
import model_projection

TERMINAL = frozenset(("succeeded", "failed", "cancelled", "expired", "superseded", "invalidated", "unsupported"))
MAX_BYTES = 128 * 1024 * 1024
MAX_FRAMES = 96


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Client:
    def __init__(self, port, token_file, *, connection_factory=None):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("invalid_worker_port")
        path = Path(token_file)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256:
            raise ValueError("private_worker_token_required")
        if os.name == "posix" and (path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid()):
            raise ValueError("private_worker_token_required")
        token = path.read_text(encoding="ascii").strip()
        if not intent.HEX64.fullmatch(token):
            raise ValueError("private_worker_token_required")
        self.port, self.token = port, token
        self.connection_factory = connection_factory or http.client.HTTPConnection

    def call(self, method, path, data=None):
        # Direct connection: environment proxy settings and redirects cannot
        # move private credentials or frame references off loopback.
        connection = self.connection_factory("127.0.0.1", self.port, timeout=.25)
        try:
            connection.request(method, path, body=None if data is None else canonical(data).encode(),
                               headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(128 * 1024 + 1)
            if response.status not in (200, 202) or len(raw) > 128 * 1024:
                raise ValueError("worker_response_over_bound")
            def pairs(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise ValueError("duplicate_worker_key")
                    value[key] = item
                return value
            def nonfinite(_):
                raise ValueError("nonfinite_worker_value")
            return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
        finally:
            connection.close()

    def health(self):
        return self.call("GET", "/healthz")

    def submit(self, request):
        return self.call("POST", "/v1/intent-jobs", request)

    def poll(self, request_id):
        if not isinstance(request_id, str) or not intent.IDENT.fullmatch(request_id):
            raise ValueError("invalid_request_id")
        return self.call("GET", "/v1/intent-jobs/" + request_id)

    def cancel(self, request_id):
        if not isinstance(request_id, str) or not intent.IDENT.fullmatch(request_id):
            raise ValueError("invalid_request_id")
        return self.call("DELETE", "/v1/intent-jobs/" + request_id)


def decode_rgb(prepared):
    """Read only the bounded NV12 plane already received by Den; make no camera request."""
    width, height = prepared.ref.rgb.width, prepared.ref.rgb.height
    if width != 640 or height != 360:
        raise ValueError("unsupported_rgb_geometry")
    with zipfile.ZipFile(io.BytesIO(prepared.bundle.data)) as archive:
        info = archive.getinfo("rgb_nv12.npy")
        if info.file_size > width * height * 2 or info.compress_size > 2 * 1024 * 1024:
            raise ValueError("rgb_plane_over_bound")
        raw = archive.read(info)
    import numpy as np
    import cv2
    plane = np.load(io.BytesIO(raw), allow_pickle=False)
    if plane.dtype != np.uint8 or plane.shape != (height * 3 // 2, width):
        raise ValueError("invalid_nv12_plane")
    return cv2.cvtColor(plane, cv2.COLOR_YUV2RGB_NV12).tobytes()


def binding(snapshot, camera_id):
    frame = snapshot.frame
    tracks = [track for track in snapshot.tracks if track.state == "confirmed"]
    if (snapshot.status != "fresh" or snapshot.presence != "present" or frame is None
            or len(tracks) != 1 or len(snapshot.tracks) != 1):
        return None, None
    track = tracks[0]
    if track.association_status != "confident" or not track.current_obs_ids or track.position_basis != "stereo":
        return None, None
    if not frame.depth_calibration_sha256:
        return None, None
    return {"camera_id": camera_id, "service_run_id": frame.service_run_id,
            "camera_epoch": str(frame.camera_epoch), "source_generation": intent.generation(frame.service_run_id, frame.camera_epoch),
            "track_id": track.track_id, "association_revision": str(track.association_revision),
            "transform_id": frame.transform_id, "scan_revision": frame.scan_revision,
            "calibration_revision": frame.depth_calibration_sha256}, track


class Bridge:
    def __init__(self, settings=None, *, camera_id, on_hint=None, clock=time.monotonic, decoder=decode_rgb,
                 client=None, profile=None, threaded=True, base_stages=None):
        self.settings = dict(settings or {})
        self.enabled = self.settings.get("enabled") is True
        self.camera_id, self.clock, self.decoder = camera_id, clock, decoder
        self.on_hint = on_hint or (lambda hint: None)
        self.base_stages = base_stages or (lambda now: ())
        self.lock, self.wake, self.halt = threading.Lock(), threading.Event(), threading.Event()
        self.latest = self.pending = None
        self.snapshot_observed = None
        self.source_written = None
        self.samples, self.requests, self.owned = deque(), {}, set()
        self.hints = deque(maxlen=2)
        self.dependencies = None
        self.last_submit = self.last_poll = -math.inf
        self.last_cutoff = -math.inf
        self.worker_health = None
        self.stage_observed = None
        self.reason = "disabled" if not self.enabled else "worker_unavailable"
        self.counts = {key: 0 for key in ("offered", "superseded", "decoded", "submitted", "completed",
                                         "expired", "invalidated", "refused", "failures")}
        self.thread = None
        self.client = client
        self.profile = profile
        self.root = None
        self.source_path = None
        self.blob_mode = 0o600
        self.duration = self.settings.get("duration_s", 4)
        if self.duration not in (4, 8):
            raise ValueError("invalid_intent_window")
        if self.enabled:
            root = Path(self.settings["frame_root"])
            if not root.is_absolute() or root.is_symlink():
                raise ValueError("private_frame_root_required")
            if root.exists() and (not root.is_dir() or any(root.iterdir())):
                # A prior process may still have native readers. Never silently
                # reclaim or ignore unaccounted inputs after a crash/restart.
                raise ValueError("frame_store_needs_reconciliation")
            if root.exists() and os.name == "posix" and root.stat().st_mode & 0o007:
                raise ValueError("private_frame_root_required")
            self.root = root
            if self.settings.get("dependencies_file") is not None:
                self.source_path = Path(self.settings["dependencies_file"])
                if (not self.source_path.is_absolute() or self.source_path.is_symlink()
                        or self.source_path.parent == root or not self.source_path.parent.is_dir()):
                    raise ValueError("private_source_receipt_required")
            access = self.settings.get("frame_access", "owner")
            if access not in ("owner", "private_group"):
                raise ValueError("invalid_frame_access")
            if access == "private_group":
                gid = self.settings.get("frame_group")
                if (os.name != "posix" or type(gid) is not int or gid <= 0 or not root.is_dir()
                        or root.stat().st_gid != gid or root.stat().st_mode & 0o007
                        or not root.stat().st_mode & 0o2000 or gid not in os.getgroups() + [os.getgid()]):
                    raise ValueError("private_group_frame_root_required")
                self.blob_mode = 0o640
                if self.source_path is not None:
                    parent = self.source_path.parent.stat()
                    if (parent.st_gid != gid or parent.st_mode & 0o007 or not parent.st_mode & 0o2000):
                        raise ValueError("private_group_source_root_required")
            if self.profile is None:
                self.profile = json.loads(Path(self.settings["profile_file"]).read_text(encoding="utf-8"))
            if self.client is None:
                self.client = Client(self.settings["port"], self.settings["token_file"])
            if threaded:
                self.thread = threading.Thread(target=self._run, name="den-optional-intent", daemon=True)
                self.thread.start()

    def offer_snapshot(self, snapshot):
        with self.lock:
            self.latest = snapshot
            self.snapshot_observed = self.clock()
        self.wake.set()

    def offer_frame(self, snapshot, prepared, reply):
        if not self.enabled:
            return
        with self.lock:
            self.latest = snapshot
            self.snapshot_observed = self.clock()
            self.counts["offered"] += 1
            if self.pending is not None:
                self.counts["superseded"] += 1
            self.pending = (snapshot, prepared, reply)
        self.wake.set()

    def _run(self):
        while not self.halt.is_set():
            self.wake.wait(.1)
            self.wake.clear()
            try:
                self.step()
            except Exception:
                # Failure text may contain a path, credential or model output.
                self.reason = "optional_worker_failure"
                self.counts["failures"] += 1

    def _poll_health(self, now):
        if now - self.last_poll < 1:
            return
        self.last_poll = now
        try:
            value = self.client.health()
            digest = hashlib.sha256(canonical(self.profile).encode()).hexdigest()
            if (value.get("schema") != "den.model-health/v1" or value.get("profile_digest") != digest
                    or value.get("profile_id") != self.profile["id"]):
                raise ValueError("worker_profile_mismatch")
            observed = self.clock()
            worker_observed = value.get("observed_at")
            if (not intent.finite(worker_observed) or not worker_observed <= observed < worker_observed + 3):
                raise ValueError("stale_worker_health")
            with self.lock:
                self.worker_health, self.stage_observed = value, worker_observed
            self.reason = value.get("deferred_reason") or ("ready" if value.get("admitted") is True else "not_admitted")
        except Exception:
            with self.lock:
                self.worker_health = None
            self.reason = "worker_unreachable"

    def step(self):
        """Deterministic step for tests; all blocking operations stay off the perception thread."""
        if not self.enabled:
            return
        now = self.clock()
        # Unsubmitted history expires even when health/poll/cancel transport
        # fails. Unknown native request pins remain independently protected.
        self._trim(now)
        self._cleanup()
        self._poll_health(now)
        now = self.clock()
        with self.lock:
            snapshot, pending, observed = self.latest, self.pending, self.snapshot_observed
            self.pending = None
        deps, track = binding(snapshot, self.camera_id) if snapshot is not None else (None, None)
        self._source_receipt(snapshot, deps, observed)
        if deps != self.dependencies:
            self.dependencies = deps
            self.last_cutoff = -math.inf
            self.samples.clear()
            with self.lock:
                self.hints.clear()
            for request_id in list(self.requests):
                self.client.cancel(request_id)
                self.counts["invalidated"] += 1
        self._results(snapshot, now)
        now = self.clock()
        self._trim(now)
        if (deps is None or self.worker_health is None or self.worker_health.get("admitted") is not True
                or self.worker_health.get("resident") is not True):
            self._cleanup()
            return
        if pending is not None:
            self._frame(*pending, deps=deps)
        if self.samples and len(self.requests) < 2 and now - self.last_submit >= 1:
            request = self._clip(now)
            if request is not None:
                # Pin BEFORE the transport call: a lost POST response does
                # not prove that the remote native job never started.
                self.requests[request["request_id"]] = request
                self.last_submit = now
                self.last_cutoff = request["cutoff_at"]
                try:
                    self.client.submit(request)
                    self.counts["submitted"] += 1
                except Exception:
                    self.reason = "submission_unproven"
                    self.counts["failures"] += 1
        self._cleanup()

    def _source_receipt(self, snapshot, deps, observed):
        if self.source_path is None or snapshot is None or observed is None or observed == self.source_written:
            return
        expiry = observed + 2
        if deps is not None:
            expiry = min(expiry, snapshot.frame.capture_mono + 1.5)
        value = dict(schema="den.intent-source/v1", observed_at=observed, valid_until=expiry,
                     dependencies=[deps] if deps is not None else [])
        # Distinct from frame inputs: worker-control service gets only the
        # explicitly shared numeric identity receipt, never Den's HA secrets.
        part = self.source_path.with_name(self.source_path.name + ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                if self.blob_mode != 0o600:
                    os.fchmod(out.fileno(), self.blob_mode)
                    if os.fstat(out.fileno()).st_gid != self.settings["frame_group"]:
                        raise ValueError("source_group_mismatch")
                out.write(canonical(value))
            os.replace(part, self.source_path)
        finally:
            part.unlink(missing_ok=True)
        self.source_written = observed

    def _frame(self, snapshot, prepared, reply, *, deps):
        current, _ = binding(snapshot, self.camera_id)
        frame = snapshot.frame
        if (current != deps or prepared.ref != frame or frame.arrival_mono > self.clock()
                or self.clock() >= frame.capture_mono + 1.5):
            return
        row = reply.get("row") or {}
        if (row.get("frame", {}).get("width"), row.get("frame", {}).get("height")) != (frame.rgb.width, frame.rgb.height):
            return
        people = [item for item in row.get("detections", ()) if item.get("class") == "person"
                  and intent.finite(item.get("score")) and .5 <= item["score"] <= 1]
        if (len(people) != 1 or not isinstance(people[0].get("keypoints"), list)
                or len(people[0]["keypoints"]) != 17):
            return
        box = people[0].get("box_xyxy")
        if (not isinstance(box, list) or len(box) != 4 or not all(intent.finite(v) for v in box)
                or not 0 <= box[0] < box[2] <= frame.rgb.width
                or not 0 <= box[1] < box[3] <= frame.rgb.height):
            return
        if self.samples and frame.capture_mono <= self.samples[-1]["captured_at"]:
            return
        data = self.decoder(prepared)
        if len(data) != frame.rgb.width * frame.rgb.height * 3:
            raise ValueError("invalid_rgb_bytes")
        header = ("P6\n%d %d\n255\n" % (frame.rgb.width, frame.rgb.height)).encode()
        payload = header + data
        digest = hashlib.sha256(payload).hexdigest()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.root / (digest + ".ppm")
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as out:
                    if self.blob_mode != 0o600:
                        # UMask0077 must not silently remove the deliberately
                        # provisioned input-only group access. No world access.
                        os.fchmod(out.fileno(), self.blob_mode)
                        if os.fstat(out.fileno()).st_gid != self.settings["frame_group"]:
                            raise ValueError("frame_group_mismatch")
                    out.write(payload)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            self.owned.add(path.name)
        elif (path.name not in self.owned or path.is_symlink()
              or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError("frame_blob_mismatch")
        self.samples.append(dict(frame_id=str(frame.frame_id), blob_ref=path.name, sha256=digest,
                                 captured_at=frame.capture_mono, track_bbox_xyxy=list(box),
                                 source_frame=dataclasses.asdict(frame)))
        self.counts["decoded"] += 1
        while (len(self.samples) > MAX_FRAMES
               or self.samples[-1]["captured_at"] - self.samples[0]["captured_at"] > 9):
            self.samples.popleft()

    def _clip(self, now):
        end = self.samples[-1]["captured_at"]
        if end + 2 <= now or end <= self.last_cutoff:
            return None
        selected, seen = [], set()
        for i in range(32):
            slot = end - self.duration + (i + .5) * self.duration / 32
            candidates = [frame for frame in self.samples if frame["captured_at"] <= slot]
            if not candidates or slot - candidates[-1]["captured_at"] > .4:
                return None
            frame = candidates[-1]
            selected.append(dict(frame, sample_at=slot, repeated=frame["frame_id"] in seen))
            seen.add(frame["frame_id"])
        boxes = [frame["track_bbox_xyxy"] for frame in selected]
        width, height = self.samples[-1]["source_frame"]["rgb"]["width"], self.samples[-1]["source_frame"]["rgb"]["height"]
        left, top = min(box[0] for box in boxes), min(box[1] for box in boxes)
        right, bottom = max(box[2] for box in boxes), max(box[3] for box in boxes)
        size = min(width, height)
        if right - left > size or bottom - top > size:
            self.reason = "crop_coverage_incomplete"
            self.counts["refused"] += 1
            return None
        x = min(width - size, max(0, math.floor((left + right - size) / 2)))
        y = min(height - size, max(0, math.floor((top + bottom - size) / 2)))
        if not (x <= left and right <= x + size and y <= top and bottom <= y + size):
            return None
        request_id = uuid.uuid4().hex
        return dict(schema="den.intent-request/v1", request_id=request_id, clip_id=request_id,
                    profile_digest=hashlib.sha256(canonical(self.profile).encode()).hexdigest(),
                    dependencies=dict(self.dependencies), frames=selected, roi_xyxy=[x, y, x + size, y + size],
                    source_size=[width, height], cutoff_at=end, expires_at=end + 2,
                    duration_seconds=self.duration, mode="choose")

    def _results(self, snapshot, now):
        for request_id, request in list(self.requests.items()):
            value = self.client.poll(request_id)
            now = self.clock()
            if (not isinstance(value, dict) or value.get("schema") != "den.intent-job/v1"
                    or any(value.get(k) != request[k] for k in ("request_id", "clip_id", "profile_digest"))):
                raise ValueError("unproven_job_response")
            if value.get("status") not in TERMINAL:
                if now >= request["expires_at"]:
                    self.client.cancel(request_id)
                continue
            del self.requests[request_id]
            if value["status"] == "succeeded" and value.get("hint") is not None:
                try:
                    hint = intent.from_wire(value["hint"], now_mono=now, request=request)
                    with self.lock:
                        snapshot = self.latest
                    track = next((t for t in snapshot.tracks if t.track_id == hint.dependencies["track_id"]), None) if snapshot else None
                    if hint.model_revision != self.profile["revision"] or not hint.matches(snapshot, track):
                        raise ValueError("current_binding_changed")
                    with self.lock:
                        self.hints.append(hint)
                    self.counts["completed"] += 1
                    self.on_hint(hint)
                except (ValueError, TypeError, StopIteration):
                    self.counts["refused"] += 1
            elif value["status"] == "expired":
                self.counts["expired"] += 1

    def _trim(self, now):
        while self.samples and self.samples[0]["captured_at"] < now - 9:
            self.samples.popleft()
        with self.lock:
            self.hints = deque((hint for hint in self.hints if hint.valid_at(now)), maxlen=2)

    def _cleanup(self):
        pinned = {frame["blob_ref"] for frame in self.samples}
        for request in self.requests.values():
            pinned.update(frame["blob_ref"] for frame in request["frames"])
        for name in tuple(self.owned - pinned):
            (self.root / name).unlink(missing_ok=True)
            self.owned.discard(name)
        if len(pinned) > 160 or sum((self.root / name).stat().st_size for name in pinned) > MAX_BYTES:
            self.reason = "frame_store_over_bound"
            self.samples.clear()
            raise ValueError("frame_store_over_bound")

    def stages(self, now_mono):
        with self.lock:
            value, observed = self.worker_health, self.stage_observed
        fresh = value is not None and observed is not None and now_mono < observed + 3
        state = "disabled" if not self.enabled else "unavailable"
        if fresh:
            state = ("not_admitted" if not value.get("admitted") else "preparing" if value.get("startup_in_progress")
                     else "running" if value.get("running") else "draining" if value.get("retiring")
                     else "idle" if value.get("resident") else "provisioned")
        return [{"id": (self.profile or {}).get("id", "vjepa-lm"), "role": "motion_intent", "state": state,
                 "configured": self.enabled, "provisioned": value.get("provisioned") if fresh else None,
                 "loaded": value.get("resident") if fresh else None, "device": "cuda:0" if fresh and value.get("resident") else None,
                 "model_id": (self.profile or {}).get("repository"), "model_revision": (self.profile or {}).get("revision"),
                 "profile_digest": hashlib.sha256(canonical(self.profile).encode()).hexdigest() if self.profile else None,
                 "admission_scope": value.get("admission_scope") if fresh else None,
                 "reason": self.reason, "queued": int(bool(value.get("pending"))) if fresh else None,
                 "running": int(bool(value.get("running"))) if fresh else None,
                 "observed_mono": observed if self.enabled else now_mono,
                 "valid_until_mono": observed + 3 if observed is not None else now_mono + 3}]

    def decorate(self, event, snapshot):
        self.offer_snapshot(snapshot)
        with self.lock:
            hints = tuple(self.hints)
        now = self.clock()
        return model_projection.project(event, snapshot, now,
                                        stages=(*self.base_stages(now), *self.stages(now)), hints=hints)

    def close(self):
        self.halt.set(); self.wake.set()
        if self.thread is not None:
            self.thread.join(2)
            if self.thread.is_alive():
                # Do not race a blocked optional I/O operation or delete input
                # that an unproven native job may still read.
                return
        for request_id in list(self.requests):
            try:
                self.client.cancel(request_id)
            except Exception:
                pass
        # Unknown native completion intentionally retains its bounded input blobs.
        self.samples.clear()
        if self.root is not None:
            self._cleanup()

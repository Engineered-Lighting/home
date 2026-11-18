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
import re
import stat
import threading
import time
import traceback
import uuid
import zipfile
from collections import deque

import intent
import model_projection

TERMINAL = frozenset(("succeeded", "failed", "cancelled", "expired", "superseded", "invalidated", "unsupported"))
MAX_BYTES = 128 * 1024 * 1024
MAX_FRAMES = 96
# How long after a clip's last capture its hint stays valid. Both contracts allow up to cutoff + 10 s
# (intent.from_wire and the worker's validate_request). Two seconds left an 8B worker well under 1.5 s
# for decode, encoder and language model once capture-to-arrival lag and the bridge cadence were spent,
# so every completed inference expired unseen; 8 s keeps a margin under the 10 s cap and the worker's
# 20 s live-job ceiling. The hint is advisory and its age stays visible in Home.
HINT_LEASE_SECONDS = 8.0
# HTTP answers that settle one request for good. A submit refused as invalid
# (400/422) created no job; a poll or cancel answered 404 job_not_found names a
# request the worker does not hold. Neither job can read its inputs or yield a
# hint, so the bridge drops it instead of polling it forever.
REFUSALS = frozenset((400, 404, 422))
REJECTED = frozenset((400, 422))
DECIMAL = re.compile(r"0|[1-9][0-9]*\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class WorkerRefused(ValueError):
    """A definitive worker refusal: the HTTP status and the worker's fixed error word, never its body."""

    def __init__(self, status, error=None):
        super().__init__("worker_refused")
        self.status, self.error = status, error

    @property
    def unknown_request(self):
        return self.status == 404 and self.error == "job_not_found"


def _error_word(raw):
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    error = value.get("error") if isinstance(value, dict) else None
    return error if isinstance(error, str) and intent.IDENT.fullmatch(error) else None


def _systemd_worker_credential(path, info, fd, env):
    """Recognize only the protected, read-only systemd credential mount.

    systemd may retain root ownership and grant the service access with a named
    ACL, represented by mode 0440. An environment variable alone grants nothing.
    """
    folder = env.get("CREDENTIALS_DIRECTORY")
    if not isinstance(folder, str):
        return False
    directory = Path(folder)
    if (not path.is_absolute() or not directory.is_absolute() or folder != str(directory)
            or ".." in path.parts or path.parent != directory
            or path.name != "model-worker-token.txt"
            or directory.parent != Path("/run/credentials")
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o440)
            or not getattr(os, "O_NOFOLLOW", 0)):
        return False
    for parent in (directory, *directory.parents):
        item = parent.lstat()
        if (not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or item.st_gid != 0
                or item.st_mode & 0o022):
            return False
        if parent == directory and stat.S_IMODE(item.st_mode) != 0o550:
            return False
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(directory, flags)
    try:
        parent = os.fstat(directory_fd)
        named = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        return ((parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) == (0, 0, 0o550)
                and stat.S_ISDIR(parent.st_mode) and stat.S_ISREG(named.st_mode)
                and (named.st_dev, named.st_ino) == (info.st_dev, info.st_ino)
                and parent.st_dev == info.st_dev
                and bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY)
                and bool(os.fstatvfs(directory_fd).f_flag & os.ST_RDONLY))
    finally:
        os.close(directory_fd)


def worker_token(token_file, *, env=None):
    """Read a bounded token from one validated descriptor, without following links."""
    path = Path(token_file)
    fd = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 256:
            raise ValueError
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError
        if os.name == "posix":
            private_owner = info.st_uid == os.getuid() and not info.st_mode & 0o077
            if not private_owner and not _systemd_worker_credential(path, info, fd, os.environ if env is None else env):
                raise ValueError
        raw = os.read(fd, 257)
        if len(raw) > 256 or not intent.HEX64.fullmatch(raw.decode("ascii").strip()):
            raise ValueError
        return raw.decode("ascii").strip()
    except (OSError, ValueError, UnicodeError):
        raise ValueError("private_worker_token_required") from None
    finally:
        if fd is not None:
            os.close(fd)


class Client:
    def __init__(self, port, token_file, *, connection_factory=None):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("invalid_worker_port")
        self.port, self.token = port, worker_token(token_file)
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
            if len(raw) > 128 * 1024:
                raise ValueError("worker_response_over_bound")
            if response.status in REFUSALS:
                raise WorkerRefused(response.status, _error_word(raw))
            if response.status not in (200, 202):
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


def binding_acceptable(deps):
    """The worker's dependency contract (den_models validate_request), checked before anything is advertised or
    sent: IDENT values (the registered-transform grammar for transform_id), a decimal camera epoch and association
    revision, and a full calibration hash. A frame without a scan revision, say, can never be admitted."""
    return (intent.binding_values_valid(deps) and DECIMAL.fullmatch(deps["camera_epoch"]) is not None
            and DECIMAL.fullmatch(deps["association_revision"]) is not None
            and intent.HEX64.fullmatch(deps["calibration_revision"]) is not None)


def _plane_timed(plane):
    return (type(plane.seq) is int and plane.seq >= 0 and intent.finite(plane.host_s) and intent.finite(plane.device_s)
            and type(plane.width) is int and type(plane.height) is int
            and 1 <= plane.width <= 8192 and 1 <= plane.height <= 8192)


def failure_site(exc):
    """'Type@function:line' of the innermost frame that raised `exc`, preferring this module's own frames.
    Only identifiers and a line number: never the exception text, a path, a credential or model output."""
    def word(text):
        return re.sub(r"[^A-Za-z0-9_]", "", str(text))[:60] or "unknown"
    frames = traceback.extract_tb(exc.__traceback__)
    own = [f for f in frames if Path(f.filename).name == Path(__file__).name]
    site = (own or frames or [None])[-1]
    where = "" if site is None else "@%s:%d" % (word(site.name), site.lineno or 0)
    if frames and site is not frames[-1]:
        # The library frame that actually raised, by module stem and function name only.
        where += "<" + word(Path(frames[-1].filename).stem) + "." + word(frames[-1].name)
    return word(type(exc).__name__) + where


def frame_problem(frame):
    """Why the worker's source-lineage contract (den_models validate_lineage) would refuse a clip holding this
    frame, as the bridge count that records the skip, or None. Nothing is repaired or re-timed."""
    if frame.mode != "rgb" or not isinstance(frame.camera_session, str) or not intent.IDENT.fullmatch(frame.camera_session):
        return "lineage_skipped"
    if (not all(intent.finite(value) for value in (frame.capture_mono, frame.arrival_mono, frame.capture_ts))
            or not all(_plane_timed(plane) for plane in (frame.rgb, frame.right, frame.depth) if plane is not None)):
        # A camera plane without a finite device time, typically: the worker requires one on every plane present.
        return "untimed_skipped"
    return None


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
                                         "expired", "invalidated", "refused", "failures",
                                         "rejected", "unknown", "noncausal_skipped", "untimed_skipped",
                                         "lineage_skipped", "binding_refused", "frame_failed")}
        # Where the last failure was raised: exception type, function and line only (failure_site), never its text.
        self.last_failure = None
        # The last request the worker definitively refused or did not know: a fixed word, never worker text.
        self.last_drop_reason = None
        self.refused_binding = None
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
            except Exception as exc:
                # Failure text may contain a path, credential or model output: keep only where it was raised.
                self.reason = "optional_worker_failure"
                self.counts["failures"] += 1
                self.last_failure = failure_site(exc)

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
        if deps is not None and not binding_acceptable(deps):
            # The worker would refuse every clip bound to it, so it is neither advertised nor sampled: Den waits.
            if deps != self.refused_binding:
                self.refused_binding = deps
                self.counts["binding_refused"] += 1
            deps = track = None
        self._source_receipt(snapshot, deps, observed)
        if deps != self.dependencies:
            self.dependencies = deps
            self.last_cutoff = -math.inf
            self.samples.clear()
            with self.lock:
                self.hints.clear()
            for request_id in list(self.requests):
                if not self._cancel(request_id):  # A request the worker did not hold counts once, as unknown.
                    self.counts["invalidated"] += 1
        self._results(snapshot, now)
        now = self.clock()
        self._trim(now)
        if (deps is None or self.worker_health is None or self.worker_health.get("admitted") is not True
                or self.worker_health.get("resident") is not True):
            self._cleanup()
            return
        if pending is not None:
            try:
                self._frame(*pending, deps=deps)
            except Exception as exc:
                # One frame that cannot be decoded or stored is skipped and counted; it must not stop every
                # later clip (the partial blob, if any, was already removed). Its text is never kept.
                self.counts["frame_failed"] += 1
                self.last_failure = failure_site(exc)
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
                except Exception as exc:
                    if isinstance(exc, WorkerRefused) and exc.status in REJECTED:
                        # Validated and refused: no job exists. The next eligible clip goes normally.
                        self._drop(request["request_id"], "worker_rejected_request", "rejected")
                    else:
                        self.reason = "submission_unproven"
                        self.counts["failures"] += 1
        self._cleanup()

    def _drop(self, request_id, reason, count):
        """Forget a request no worker job holds; its input pins go at the next cleanup and it can never yield a hint."""
        self.requests.pop(request_id, None)
        self.counts[count] += 1
        self.last_drop_reason = self.reason = reason

    def _cancel(self, request_id):
        """Cancel keeps the pins until a terminal status, except for a request the worker does not hold, which is
        dropped (True)."""
        try:
            self.client.cancel(request_id)
        except WorkerRefused as exc:
            if not exc.unknown_request:
                raise
            self._drop(request_id, "worker_unknown_request", "unknown")
            return True
        return False

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
        if frame.capture_mono > frame.arrival_mono:
            # Its capture time maps after its own arrival, so no causal clip may hold it (the worker refuses
            # noncausal_source_arrival). Skip it; timestamps are never altered.
            self.counts["noncausal_skipped"] += 1
            return
        problem = frame_problem(frame)
        if problem is not None:
            self.counts[problem] += 1
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
        if (self.profile.get("inputs") or {}).get("sampling") != intent.SAMPLING:
            # The worker's profile admits another sampling rule: it would refuse every clip built here.
            self.reason = "sampling_unsupported"
            return None
        end = self.samples[-1]["captured_at"]
        if end + HINT_LEASE_SECONDS <= now or end <= self.last_cutoff:
            return None
        # intent.SAMPLING: 32 uniform causal slot centres, each holding the newest frame captured at or before
        # it for at most MAX_HOLD_SECONDS; repeats are marked and keep their true capture times.
        selected, seen = [], set()
        for i in range(32):
            slot = end - self.duration + (i + .5) * self.duration / 32
            candidates = [frame for frame in self.samples if frame["captured_at"] <= slot]
            if not candidates or slot - candidates[-1]["captured_at"] > intent.MAX_HOLD_SECONDS:
                return None
            frame = candidates[-1]
            selected.append(dict(frame, sample_at=slot, repeated=frame["frame_id"] in seen))
            seen.add(frame["frame_id"])
        if any(frame["source_frame"]["arrival_mono"] > end for frame in selected):
            return None  # A held frame arrived after the cutoff: not yet causal, so the worker would refuse it.
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
                    source_size=[width, height], cutoff_at=end, expires_at=end + HINT_LEASE_SECONDS,
                    duration_seconds=self.duration, mode="choose", sampling=intent.SAMPLING)

    def _results(self, snapshot, now):
        for request_id, request in list(self.requests.items()):
            try:
                value = self.client.poll(request_id)
            except WorkerRefused as exc:
                if not exc.unknown_request:
                    raise  # Anything else keeps the pins and is retried at the next step, as before.
                self._drop(request_id, "worker_unknown_request", "unknown")
                continue
            now = self.clock()
            if (not isinstance(value, dict) or value.get("schema") != "den.intent-job/v1"
                    or any(value.get(k) != request[k] for k in ("request_id", "clip_id", "profile_digest"))):
                raise ValueError("unproven_job_response")
            if value.get("status") not in TERMINAL:
                if now >= request["expires_at"]:
                    self._cancel(request_id)
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
                 "valid_until_mono": (observed + 3 if observed is not None else None) if self.enabled else now_mono + 3}]

    def status_words(self):
        """The journal status line's movement-intent part: counts and fixed words only, never a position or
        worker text. None while the bridge is disabled."""
        if not self.enabled:
            return None
        c = self.counts
        skipped = c["noncausal_skipped"] + c["untimed_skipped"] + c["lineage_skipped"]
        return ("intent %s: %d sent, %d hints, %d expired, %d rejected, %d unknown, %d frames skipped "
                "(%d noncausal, %d untimed, %d lineage), %d bindings refused, %d decoded, %d frame failures%s%s"
                % (self.reason, c["submitted"], c["completed"], c["expired"], c["rejected"], c["unknown"], skipped,
                   c["noncausal_skipped"], c["untimed_skipped"], c["lineage_skipped"], c["binding_refused"],
                   c["decoded"], c["frame_failed"],
                   "" if self.last_drop_reason is None else ", last drop " + self.last_drop_reason,
                   "" if self.last_failure is None else ", last failure " + self.last_failure))

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

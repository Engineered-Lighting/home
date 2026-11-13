"""Step 3's perception: one camera bundle -> detector row -> observations -> tracks -> one people event (plan L1-L6).

Standard library only, in the loop's own process. The detector runs in its own process, in the detector's venv, with
no network (detect_worker.py). den-observe (rung 2) and den-track (rung 3) run unedited at the den simulation's pins:
- observe.observe() on each frame, with Camera built once from step 2's room model;
- TrackerCore.step() once per frame, on a method copy whose floor_m is your scan's floor, padded (see FLOOR_PAD_M).

Numbers this phase sets, with where they come from:
- FRESH_S 1.5: the detector-freshness exception, plan pick 10. den-observe proposes 1.0; a frame crosses between
  homes at step 4. It's the pose's expiry for every consumer; identity has the grace below.
- The camera epoch (contract 0's camera_epoch, Perceiver.sessions): a new one on a new camera session, the camera's
  clock going back more than EPOCH_BACK_S, a new depth calibration hash, a day/night mode switch, or EPOCH_GAP_S (3.0)
  without a fresh frame. A late or missing frame never starts one: idle() publishes a stale snapshot and keeps the
  epoch, and the tracker is kept across shorter gaps.
- association_revision (contract 2): it moves only once a track's safe has held its new value for two consecutive
  processed frames. Each frame's association status stays honest: one unsafe frame says ambiguous, so the ask skips
  it, and the revision stays.
- The registration gate is step 2's method file's gate_m (0.15 m), with its sofa checks held to sofa_check_max_m
  (0.10 m) by register_now.py. den-observe's own max_error_bound_m is the ladder's 0.05, which a scan-picked
  registration isn't held to (plan pick 3).
- The clock: capture time on this PC's monotonic clock = t_host + offset, the offset anchored to this PC's arrival
  times (the 5th percentile over the last 50 frames: the least delayed). The den Jetson's wall clock read 1970 on
  2026-11-11, so the tracker never uses captured_unix_ns (plan L1); and a monotonic clock here means a wall-clock step
  on this PC can't stall or reverse the tracker. The bias is the transit, about 0.1-0.3 s, which "behind" leaves out.
- The transit, on the health line only (plan L1, step 4): the Jetson's clock is right again since 2026-11-11, so each
  bundle's arrival time here less its captured_unix_ns is the camera-to-here transit. transit_s_median is its median
  over the health window, from frames where it lies between 0 and 5 s only; a clock that's wrong again gives null, and
  nothing else changes. The walk test adds it to "behind" for its capture-to-stream lag.
- Too dark (plan L2): the frame's mean Y under 40 for 3 s, back over 60 for 3 s. Until step 6's infrared there's no
  picture to use then, so the stream says mode none, why too dark.
- Every bundle must say depth_correction applied when the method file asks for it (as step 2's paste checks); one
  that doesn't is refused, counted and never lifted. And when step 2's receipt names the correction its take was made
  on (source_correction: one factor, one calibration_sha256), every bundle must say applied with that factor and that
  hash, whatever the depth scale (body_rgb.py's rule): after a camera release with a re-measured factor, the receipt's
  scale would sit on another correction. Such a frame is refused and counted, and the status says register again.
- Step 2's depth scale (contract 1, den-live-register-now-2's depth_scale, 0.90-1.10, 1.0 for none): every depth
  plane is multiplied by it right after it's read, before any lift, so the lift sees the depth the registration was
  fitted on. The figure's depth gets the same scale in the detector's process (body.py). The Perceiver refuses a scale
  other than the one the registration's own take was made at (its name says, <take>-x0.971-m). At 1.0 nothing changes.

An event holds tracks' floor positions in your scan's frame, their spread, heading and age: no joints, no image, no
name. Only confirmed and coasting tracks go out, and only once a pose or a person box at 0.5 or more has supported
them (plan L3). Track ids carry the epoch (e1-trk-1), so a camera restart's new tracks never take an old marker over.
Each track also carries near: what's near it from your confirmed labels (room_labels.near_phrase, for example "at the
desk, beside the 3D printer"), or null with no labels, none near, or labels placed on another scan revision. Home
shows it under the marker. And each carries the person binding beside its track_id (contract 2's people projection):
service_run_id, camera_epoch and association_revision.
"""
import importlib.util
import json
import math
import queue
import statistics
import struct
import subprocess
import sys
import threading
import time
import uuid
from array import array
from dataclasses import dataclass
from types import MappingProxyType
from collections import deque
from pathlib import Path

import room_labels
import seated as seated_rule
from body import head_exclusion
from body_clock import receipt_arrival
from body_input import bundle_frame_key, qualified as qualified_body_input
from contracts import (DEPTH_SCALE_RANGE, FIGURE_S, PERCEPTION_S, RGB_PAIR_S, FrameRef, PlaneRef, TrackEvidence,
                       TrackingSnapshot, take_scale)

SCHEMA = "den-live-people-1"
FRESH_S = PERCEPTION_S
FLOOR_PAD_M = 1.0
# den-track's METHOD.md at its pin: the floor_m slot's exact text, which the method copy replaces (as the den
# simulation's chain.py hop 8 does, 85744f2c on claude/den-sim-connected).
TRACK_FLOOR_SLOT = '"floor_m": {"open": true, "proposed": [0.0, 0.0, 4.572, 3.759]'
TRACK_METHOD_COPY = "METHOD-den-track-floor.md"
DARK_LUMA, LIGHT_LUMA, DARK_S = 40.0, 60.0, 3.0
OFFSET_WINDOW, OFFSET_QUANTILE = 50, 0.05
OUT_STATES = ("confirmed", "coasting")
EPOCH_BACK_S = 1.0   # the camera's own clock going back more than this within a session is a new epoch
EPOCH_GAP_S = 3.0    # this long without a fresh frame is a new epoch; a shorter gap keeps the epoch and the tracker
HEALTH_S = 10.0      # frames a second and lag are over this window only: an old frame never speaks for now
HEADING_FRESH_S = 3.0   # an arrow older than this is left off: den-track keeps its last heading indefinitely
PERSON_MIN = 0.5     # den-detect's pose line, and plan L3's marker line
TRANSIT_MAX_S = 5.0  # arrival less captured_unix_ns counts only between 0 and this (plan L1)
NOT_CORRECTED = "depth not corrected at the camera"
CORRECTION_CHANGED = "the camera's depth correction isn't the one step 2 registered on: register again with step 2"


class Stop(Exception):
    pass


class Refused(Stop):
    """A stop that a restart can't mend: the registration, or the camera against it. Register again with step 2."""


# ---- the pinned tools ----------------------------------------------------------------------------------------

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Tools:
    """den-observe and den-take from the observe tree, den-track from the track tree. Each tree's method.py, and
    den-track's records.py, load under their own names: den-register has a method.py and a records.py too, and a
    process may hold both."""

    def __init__(self, observe_tree, track_tree):
        for folder in (Path(track_tree) / "tools" / "den-track", Path(observe_tree) / "tools" / "den-observe",
                       Path(observe_tree) / "tools" / "den-take"):
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
        import observe
        import roommodel
        import takes
        self.observe, self.roommodel, self.takes = observe, roommodel, takes
        self.records = load_module("den_track_records", Path(track_tree) / "tools" / "den-track" / "records.py")
        self.observe_method = load_module("den_observe_method", Path(observe_tree) / "tools" / "den-observe" / "method.py")
        self.track_method = load_module("den_track_method", Path(track_tree) / "tools" / "den-track" / "method.py")
        self.observe_tree, self.track_tree = Path(observe_tree), Path(track_tree)


# ---- the floor -------------------------------------------------------------------------------------------------

def floor_bounds(polygon):
    """[x0, y0, x1, y1] of a floor file's walkable_polygon, to the millimetre (den-sim chain.py's floor_bounds)."""
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if not isinstance(polygon, list) or len(polygon) < 3 or not all(isinstance(p, list) and len(p) >= 2 and number(p[0]) and number(p[1]) for p in polygon):
        raise ValueError("its walkable_polygon is not three or more [x, y] points")
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    bounds = [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("its walkable_polygon has no area")
    return bounds


def padded(bounds, pad=FLOOR_PAD_M):
    """The floor box, FLOOR_PAD_M wider on every side. A floor plan has no floor under a sofa, so a sofa against a wall
    can sit past the walkable polygon's box; the den simulation widened it around your sofa mark's seat, and this pads
    every side instead, so it needs no private file. A coasting track can drift that much further before it ends."""
    return [round(bounds[0] - pad, 3), round(bounds[1] - pad, 3), round(bounds[2] + pad, 3), round(bounds[3] + pad, 3)]


def track_method_copy(tools, bounds, folder):
    """den-track's METHOD.md with only its floor_m slot's proposed value replaced by bounds, still open (unscored)."""
    text = (tools.track_tree / "tools" / "den-track" / "METHOD.md").read_text(encoding="utf-8")
    if text.count(TRACK_FLOOR_SLOT) != 1:
        raise Stop("den-track's METHOD.md no longer has the floor_m slot the copy replaces")
    copy = Path(folder) / TRACK_METHOD_COPY
    copy.write_text(text.replace(TRACK_FLOOR_SLOT, '"floor_m": {"open": true, "proposed": %s' % json.dumps(bounds)),
                    encoding="utf-8", newline="\n")
    return copy


# ---- the clock -------------------------------------------------------------------------------------------------

class ClockOffset:
    """this PC's monotonic clock ~ t_host + offset, from the arrival times (the module note)."""

    def __init__(self, window=OFFSET_WINDOW, quantile=OFFSET_QUANTILE):
        self.samples, self.quantile = deque(maxlen=window), quantile

    def add(self, t_host, arrival_mono):
        self.samples.append(arrival_mono - t_host)

    def offset(self):
        ordered = sorted(self.samples)
        return ordered[int(self.quantile * (len(ordered) - 1))]

    def reset(self):
        self.samples.clear()


# ---- the detector process ------------------------------------------------------------------------------------------

class DetectorWorker:
    """detect_worker.py in the detector's venv: one frame in, one row out, in order. A reader thread holds its replies,
    so a worker that hangs is a Stop after start_timeout_s or frame_timeout_s, never a loop that waits forever.
    The live body's replies (body_ready, body_refused, body_id: see body.BodyChannel) go to on_body instead, never into
    the rows' queue, so they can arrive between frames without the loop waiting for them."""

    def __init__(self, argv, log_path, *, start_timeout_s=300, frame_timeout_s=10):
        self.log = open(log_path, "ab")
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.frame_timeout_s, self.next_id = frame_timeout_s, 0
        self.on_body = None
        self.on_stills = None   # the Qwen ask's stills (ask.py): replies whose first key is stills_id
        self.writes = queue.Queue(maxsize=1)
        self.writer_stop = threading.Event()
        self.pending = None
        self.optional = None
        self.dispatch_lock = threading.Lock()
        self.writer = threading.Thread(target=self._write, daemon=True, name="den-live-detector-writer")
        self.writer.start()
        self.replies = queue.Queue()
        threading.Thread(target=self._read, daemon=True, name="den-live-detector-reader").start()
        try:
            first = self._line(start_timeout_s, "to start")
            if not first.get("ready"):
                raise Stop("the detector didn't start: %s" % (first.get("refused") or "it said something else"))
        except Stop:
            self.close()
            raise
        self.load_ms = first.get("load_ms")

    def _read(self):
        try:
            for raw in self.proc.stdout:
                stills = raw.startswith(b'{"stills_id"')   # the ask's stills, as json.dumps writes them: first key stills_id
                if raw.startswith(b'{"body_') or stills:   # the body's replies, as json.dumps writes them: first key body_*
                    try:
                        reply = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        reply = {"stills_id" if stills else "body_id": None, "error": "ProtocolError"}
                    with self.dispatch_lock:
                        if self.optional is not None and self.optional[0] == ("stills" if stills else "body"):
                            self.optional = None
                    handler = self.on_stills if stills else self.on_body
                    if handler is not None:
                        try:
                            handler(reply)
                        except Exception:   # the body's failure is the body's; the rows go on
                            pass
                    continue
                self.replies.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            self.replies.put(None)
            for handler in (self.on_body, self.on_stills):
                if handler is not None:
                    try:
                        handler(None)   # the process stopped
                    except Exception:
                        pass

    def _write(self):
        try:
            while not self.writer_stop.is_set():
                try:
                    header, bundle = self.writes.get(timeout=0.1)
                except queue.Empty:
                    continue
                data = json.dumps(header, separators=(",", ":")).encode("utf-8")
                self.proc.stdin.write(struct.pack(">I", len(data)) + data)
                if bundle is not None:
                    self.proc.stdin.write(struct.pack(">I", len(bundle)))
                    self.proc.stdin.write(bundle)
                self.proc.stdin.flush()
        except (OSError, ValueError):
            self.replies.put(None)

    def send_alone(self, header):
        """Queue optional work without waiting for the pipe or a busy detector."""
        with self.dispatch_lock:
            if self.proc.poll() is not None:
                raise Stop("the detector process stopped")
            if self.pending is not None or self.optional is not None:
                return False
            try:
                self.writes.put_nowait((header, None))
            except queue.Full:
                return False
            kind = "stills" if header["kind"] == "stills" else "body"
            self.optional = (kind, time.monotonic())
            return True

    def _line(self, timeout_s, what):
        try:
            raw = self.replies.get(timeout=timeout_s)
        except queue.Empty:
            self.proc.kill()
            raise Stop("the detector didn't answer %s in %d s (its log: %s)" % (what, timeout_s, self.log.name)) from None
        if raw is None:
            raise Stop("the detector process stopped (its log: %s)" % self.log.name)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise Stop("the detector wrote something that isn't its protocol (its log: %s)" % self.log.name) from None

    def submit(self, bundle, delay_s, ref=None):
        with self.dispatch_lock:
            if self.pending is not None:
                return False
            if self.proc.poll() is not None:
                raise Stop("the detector process stopped")
            self.next_id += 1
            header = {"id": self.next_id, "rgb_seq": bundle.rgb_seq, "t_host": bundle.t_host,
                      "delay_s": delay_s}
            if ref is not None:
                header["frame_key"] = list(ref.key)
            try:
                self.writes.put_nowait((header, bundle.data))
            except queue.Full:
                return False
            self.pending = (self.next_id, header.get("frame_key"), time.monotonic())
            return True

    def poll(self):
        with self.dispatch_lock:
            pending = self.pending
            optional = self.optional
        if pending is None:
            if optional is not None and time.monotonic() - optional[1] >= self.frame_timeout_s:
                raise Stop("the detector didn't answer optional work")
            return None
        frame_id, key, started = pending
        try:
            raw = self.replies.get_nowait()
        except queue.Empty:
            if time.monotonic() - started >= self.frame_timeout_s:
                raise Stop("the detector didn't answer a frame")
            return None
        if raw is None:
            raise Stop("the detector process stopped")
        try:
            reply = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise Stop("the detector wrote something that isn't its protocol") from None
        if reply.get("id") != frame_id or (key is not None and reply.get("frame_key") != key):
            raise Stop("the detector answered out of turn")
        with self.dispatch_lock:
            self.pending = None
        return reply

    def detect(self, bundle, delay_s, *, ref=None):
        """Synchronous entry for local tools; the service uses submit and poll."""
        while not self.submit(bundle, delay_s, ref):
            time.sleep(0.005)
        while (reply := self.poll()) is None:
            time.sleep(0.005)
        return reply

    def close(self):
        self.writer_stop.set()
        if self.proc.poll() is None:
            try:
                self.proc.kill()  # releases any blocked reader or writer before closing a pipe
            except ProcessLookupError:
                pass
        self.proc.wait(5)
        self.writer.join(2)
        if self.proc.stdin:
            self.proc.stdin.close()
        if self.proc.stdout:
            self.proc.stdout.close()
        self.log.close()


# ---- one frame -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Prepared:
    ref: FrameRef
    meta: dict
    depth: object
    bundle: object


class Perceiver:
    """Times: the tracker runs on this PC's monotonic clock (capture_mono = t_host + the arrival-anchored offset), so a
    step in the wall clock (time sync) can't send it backwards or stall it. Events carry wall-clock times: emitted_ts is
    now, capture_ts is now less how long ago the frame was captured."""

    depth_scale, depth_table = 1.0, None   # step 2's depth scale and its table; __init__ sets them from the registration

    def __init__(self, tools, *, room_model_path, camera_id, max_error_bound_m, track_method_path, home, detector=None,
                 require_source_correction=True, content_revision=None, now=time.time, depth_scale=1.0,
                 source_correction=None):
        """source_correction: (factor, calibration_sha256) from step 2's receipt, the camera's correction its take was
        made on; every frame must say applied with exactly these. None holds frames to no receipt (a direct caller)."""
        self.t, self.detector, self.home, self.now = tools, detector, home, now
        self.body_provider = None
        self.body = None   # a body_link.BodyLink, when the live body runs: asked for each fresh frame, never waited for
        self.labels = None   # a room_labels.Store: each track's near phrase, from the confirmed labels on this scan
        self.require_source_correction = require_source_correction
        self.source_correction = None if source_correction is None else tuple(source_correction)
        try:
            room_model = tools.roommodel.load(room_model_path)
            transform_id = tools.roommodel.current_transform_id(room_model, camera_id)
            self.camera = tools.roommodel.Camera(room_model, transform_id, max_error_bound_m=max_error_bound_m)
            self.camera.take_state(now(), scored=False)   # this session ends now: after the registration's own take
            fitted_on = take_scale((tools.roommodel.resolve(room_model, transform_id).get("mount") or {}).get("take"))
        except tools.roommodel.RegistrationRefused as exc:
            raise Refused("the registration can't be used: %s" % exc) from None
        if type(depth_scale) not in (int, float) or not DEPTH_SCALE_RANGE[0] <= depth_scale <= DEPTH_SCALE_RANGE[1]:
            raise Refused("den-live's depth scale must lie within %g-%g, not %r" % (*DEPTH_SCALE_RANGE, depth_scale))
        if depth_scale != fitted_on:
            raise Refused("%s was fitted on depth x%g, and den-live would apply x%g: register again with step 2's "
                          "register_now.py" % (transform_id, fitted_on, depth_scale))
        self.depth_scale = float(depth_scale)
        # Each millimetre value's scaled value, once: 0 stays 0, as register_now.py's median copy rounds it.
        self.depth_table = None if self.depth_scale == 1.0 else array(
            "H", [0] + [min(65535, max(1, round(v * self.depth_scale))) for v in range(1, 65536)])
        method = tools.observe_method.load(tools.observe_tree / "tools" / "den-observe" / "METHOD.md")
        self.settings = tools.observe.Settings(method)
        self.settings.max_age_s = FRESH_S
        self.settings.max_gap_s = RGB_PAIR_S + 1e-9  # float rounding at the inclusive 67 ms boundary
        self.track_method = tools.track_method.Method(str(track_method_path))
        self.floor = self.track_method.room["floor_m"]
        self.scope = {"transform_id": self.camera.transform_id, "content_revision": content_revision}
        self.clock = ClockOffset()
        self.session, self.sessions, self.seq = None, 0, 0
        self.run_id, self.frame_id = uuid.uuid4().hex, 0
        self.retired = set()
        self.last_seq = self.calibration_id = None
        self.clock_reset_candidate = None
        self.snapshot = TrackingSnapshot(self.run_id, 0, 0, None, "starting", None, "unknown", ())
        self.body_input = None
        self.mode, self.dark_since, self.light_since = "rgb", None, None
        self.counts = {"frames": 0, "refused_depth": 0, "unreadable": 0, "detector_errors": 0, "expired": 0, "out_of_order": 0,
                       "observations": 0, "weak_only_hidden": 0}
        self.last_t_host = self.last_capture = None
        self.arrivals, self.lags, self.drops, self.transits = deque(), deque(), deque(), deque()
        self.problem = None
        self.hops = {}
        self.last_input_mono = None
        self._new_core()

    def _new_core(self):
        room = self.track_method.room
        self.core = self.t.records.tracker_core.TrackerCore(self.track_method.tracker, exits=room["exits_m"], floor=room["floor_m"])
        self.strong = set()   # tracks a pose or a person box at PERSON_MIN or more has ever supported
        self.last_capture = None
        self.pending_capture = None   # the capture time of the frame prepare handed on, until finish takes it
        self.body_input = None
        self.associations = {}
        self.seated = seated_rule.Published()   # contract 2's seated, once per track per frame

    def _epoch(self, *, clock_reset=False):
        self._clear_body("scope_changed")
        self.sessions += 1
        self._new_core()
        self.dark_since = self.light_since = None
        if clock_reset:
            self.clock.reset()
            self.last_t_host = self.last_seq = None
            self.clock_reset_candidate = None
            self.mode = "rgb"

    def _timing(self, now_mono, **seconds):
        for stage, elapsed in seconds.items():
            if elapsed is not None:
                self.hops.setdefault(stage, deque(maxlen=100)).append((now_mono, max(0.0, elapsed) * 1000.0))

    def _drop(self, what, now_mono, problem):
        self.counts[what] += 1
        self.drops.append(now_mono)
        self.problem = problem
        return None

    def _health(self, now_mono):
        while self.arrivals and now_mono - self.arrivals[0] > HEALTH_S:
            self.arrivals.popleft()
        while self.lags and now_mono - self.lags[0][0] > HEALTH_S:
            self.lags.popleft()
        while self.drops and now_mono - self.drops[0] > 60.0:
            self.drops.popleft()
        while self.transits and now_mono - self.transits[0][0] > HEALTH_S:
            self.transits.popleft()
        seconds = (self.arrivals[-1] - self.arrivals[0]) if len(self.arrivals) > 1 else 0
        timings = {}
        for stage, samples in self.hops.items():
            while samples and now_mono - samples[0][0] > HEALTH_S:
                samples.popleft()
            if not samples:
                continue
            values = sorted(value for _, value in samples)
            timings[stage] = {"n": len(values), "p50": round(statistics.median(values), 1),
                              "p95": round(values[math.ceil(len(values) * 0.95) - 1], 1)}
        return {"timings_ms": timings, "fps_in": round((len(self.arrivals) - 1) / seconds, 2) if seconds > 0 else None,
                "dropped_per_min": len(self.drops),
                "lag_s_median": round(statistics.median(lag for _, lag in self.lags), 3) if self.lags else None,
                "transit_s_median": round(statistics.median(s for _, s in self.transits), 3) if self.transits else None}

    def _transit(self, meta, bundle):
        """Legacy wall-clock packaging-to-arrival estimate for display; never used as a freshness bound."""
        captured = (meta.get("raw") or {}).get("captured_unix_ns")
        if type(captured) is not int:
            return
        transit = bundle.arrival_unix - captured / 1e9
        if 0.0 <= transit <= TRANSIT_MAX_S:
            self.transits.append((bundle.arrival_mono, transit))

    def event(self, ago_s, tracks, camera_state, now_mono):
        """ago_s: how long before now the frame was captured (None for an event with no frame)."""
        self.seq += 1
        emitted = self.now()
        return {"schema": SCHEMA, "home": self.home, "seq": self.seq, **self.scope,
                "capture_ts": None if ago_s is None else round(emitted - ago_s, 3), "emitted_ts": round(emitted, 3),
                "mode": self.mode, "camera": {"status": camera_state.get("status", "ok"), "why": camera_state.get("why") or self.problem},
                "health": self._health(now_mono), "tracks": self._near(tracks)}

    def _near(self, tracks):
        """Each track with its near phrase: null without labels, or with labels placed on another scan revision."""
        doc = self.labels.get() if self.labels is not None else None
        revision = self.scope["content_revision"]
        usable = bool(doc and doc["labels"]) and (revision is None or doc["content_revision"] == revision)
        labels = [label for label in doc["labels"] if label["status"] == "confirmed"] if usable else []
        return [{**track, "near": room_labels.near_phrase(labels, track["xy_m"]) if labels else None} for track in tracks]

    def _snapshot(self, event, *, ref=None, status, presence="unknown", tracks=()):
        if status != "fresh":
            self.body_input = None
        self.snapshot = TrackingSnapshot(self.run_id, self.sessions, event["seq"], ref, status,
                                         event["camera"]["why"], presence, tuple(tracks))
        event["presence"] = presence
        event["perception_status"] = status
        return event

    def idle(self, camera_state, now_mono):
        """A snapshot with no frame, saying why. It keeps the epoch and the tracker: a late or missing frame is only
        stale, and the next fresh frame within EPOCH_GAP_S goes on with the same tracks (prepare's gap check). Once
        EPOCH_GAP_S has passed with no fresh frame, the next epoch starts here, once per gap: the stale snapshots of a
        long outage carry it, not the epoch before. A frame prepare has handed on, still within FRESH_S of its capture,
        is no outage: prepare measured its gap capture to capture, and it's at the detector, so the epoch waits for it."""
        pending = self.pending_capture is not None and now_mono < self.pending_capture + FRESH_S
        if self.last_capture is not None and not pending and now_mono - self.last_capture >= EPOCH_GAP_S:
            self._epoch()   # _new_core forgets last_capture, so neither a later idle nor the next frame bumps again
        self.body_input = None
        status = camera_state.get("status")
        if status not in ("starting", "offline", "refused"):
            status = "refused" if self.problem == CORRECTION_CHANGED else "stale"   # a frame was refused: register again
        self._clear_body("stale" if camera_state.get("status") == "ok" else "unavailable")
        return self._snapshot(self.event(None, [], camera_state, now_mono), status=status)

    def _clear_body(self, reason):
        if self.body is not None:
            self.body.clear(reason)

    def _submit_body(self, prepared, *, observations=(), tracks=(), supported=(), association=None):
        if self.body is None or self.body_provider is None:
            return
        ref, bundle, meta, depth = prepared.ref, prepared.bundle, prepared.meta, prepared.depth
        if bundle_frame_key(bundle, meta) != [ref.camera_session, ref.rgb.seq, getattr(ref.depth, "seq", None)]:
            self._clear_body("unavailable")
            return
        frame_key = list(ref.key)
        try:
            value = self.body_provider(bundle, meta, depth, frame_key=frame_key, mode=self.mode,
                                    observations=observations, tracks=tracks, supported=supported,
                                    association={} if association is None else association)
            arrival = receipt_arrival(bundle)
            if (qualified_body_input(value, self.scope) and value.frame_key == frame_key
                    and value.run_id == ref.service_run_id and value.track_epoch == ref.camera_epoch
                    and arrival is not None and value.capture_earliest_mono <= arrival):
                request = self._body_request(value)
                if request is not None:
                    self.body.submit(self.detector.next_id, **request)
                return
        except Exception:
            pass   # the optional body adapter cannot interrupt tracking
        self._clear_body("unavailable")

    def _body_request(self, value):
        if value is None:
            return None
        scope = value.scope(self.home)
        binding = value.binding()
        if value.observation not in ("person", "empty"):
            self.body.clear("ambiguous" if value.observation == "ambiguous" else "pose_unavailable", scope=scope)
            return
        person = None
        if value.observation == "person":
            if (value.track or {}).get("association") == "ambiguous":
                self.body.clear("ambiguous", scope=scope)
                return None
            person = body_person(value.track, value.pose, self.t.records.body_heading,
                                 self.track_method.tracker["heading_max_sd_deg"])
            if person is None:
                self.body.clear("pose_unavailable", scope=scope)
                return None
            if head_exclusion(value.head_evidence, frame_key=value.frame_key, binding=binding) is None:
                self.body.clear("head_mask_unavailable", scope=scope)
                return None
            person["head_evidence"] = value.head_evidence
        rotation, translation = self.camera.room_from_left
        setup = {"binding": binding, "left_k": list(self.camera.rig.left_k[:4]),
                 "room_from_left": [[list(r) for r in rotation], list(translation)]}
        if self.depth_scale != 1.0:   # body.py takes none as 1.0, so at 1.0 the setup is as it always was
            setup["depth_scale"] = self.depth_scale
        self.body.prepare(scope, binding, person["track_id"] if person else None)
        return dict(frame_key=value.frame_key, scope=scope, setup=setup, capture_mono=value.capture_earliest_mono,
                    observation=value.observation, person=person)

    def _mode(self, luma, now_mono):
        if luma is None:
            return
        if luma < DARK_LUMA:
            self.light_since = None
            self.dark_since = now_mono if self.dark_since is None else self.dark_since
            if self.mode == "rgb" and now_mono - self.dark_since >= DARK_S:
                self.mode = "none"
        elif luma > LIGHT_LUMA:
            self.dark_since = None
            self.light_since = now_mono if self.light_since is None else self.light_since
            if self.mode == "none" and now_mono - self.light_since >= DARK_S:
                self.mode = "rgb"
        else:
            self.dark_since = self.light_since = None

    @staticmethod
    def _plane(raw, width, height):
        if not isinstance(raw, dict) or type(raw.get("sequence")) is not int:
            return None
        host = raw.get("host_monotonic_timestamp_seconds")
        device = raw.get("device_timestamp_seconds")
        if type(host) not in (int, float) or not math.isfinite(host):
            return None
        if type(device) not in (int, float) or not math.isfinite(device):
            device = None
        return PlaneRef(raw["sequence"], device, host, raw.get("width", width), raw.get("height", height))

    def prepare(self, bundle, now_mono):
        """Read a candidate before submission and fix its identity and estimated capture time."""
        if bundle.session in self.retired:
            return self._drop("out_of_order", now_mono, "retired camera session")
        if bundle.session != self.session:
            if self.session is not None:
                self.retired.add(self.session)
            self.session = bundle.session
            self._epoch(clock_reset=True)
        elif self.last_t_host is not None and (bundle.t_host <= self.last_t_host or bundle.rgb_seq <= self.last_seq):
            previous = self.clock_reset_candidate
            if bundle.t_host < self.last_t_host - EPOCH_BACK_S and bundle.rgb_seq < self.last_seq:
                if previous is not None and bundle.t_host > previous[0] and bundle.rgb_seq > previous[1]:
                    self._epoch(clock_reset=True)
                else:
                    self.clock_reset_candidate = bundle.t_host, bundle.rgb_seq
                    return self._drop("out_of_order", now_mono, "camera clock moved back")
            else:
                self.clock_reset_candidate = None
                return self._drop("out_of_order", now_mono, "frames out of order")
        self.clock_reset_candidate = None
        if self.last_input_mono is not None and bundle.arrival_mono - self.last_input_mono >= FRESH_S:
            self.dark_since = self.light_since = None
        self.last_input_mono = bundle.arrival_mono
        self.last_t_host, self.last_seq = bundle.t_host, bundle.rgb_seq
        self._timing(now_mono, fetch=bundle.fetch_s, parse=bundle.parse_s, mailbox=now_mono - bundle.arrival_mono)
        self.counts["frames"] += 1
        self.arrivals.append(bundle.arrival_mono)
        self.clock.add(bundle.t_host, bundle.arrival_mono)
        capture_mono = bundle.t_host + self.clock.offset()
        if now_mono >= capture_mono + FRESH_S:
            return self._drop("expired", now_mono, "camera frame expired")
        if self.last_capture is not None and capture_mono <= self.last_capture:
            return self._drop("out_of_order", now_mono, "frames out of order")
        try:
            meta, depth = self.t.takes.read_frame(bundle.data)
        except self.t.takes.BundleError:
            return self._drop("unreadable", now_mono, "frames unreadable")
        depth = self.scaled(depth)
        self._transit(meta, bundle)
        raw = meta.get("raw") or {}
        correction = raw.get("depth_correction")
        if self.require_source_correction and not (isinstance(correction, dict) and correction.get("applied") is True):
            self._drop("refused_depth", now_mono, NOT_CORRECTED)
            return None
        if self.source_correction is not None and not (
                isinstance(correction, dict) and correction.get("applied") is True
                and (correction.get("factor"), correction.get("calibration_sha256")) == self.source_correction):
            self._drop("refused_depth", now_mono, CORRECTION_CHANGED)
            return None
        try:
            self.camera.check_frame(meta.get("intrinsics"))
        except self.t.roommodel.RegistrationRefused as exc:
            raise Refused("the camera no longer matches the registration (%s); register again with step 2" % exc) from None
        if self.last_capture is not None and capture_mono - self.last_capture >= EPOCH_GAP_S:
            dark_since, light_since = self.dark_since, self.light_since   # the mode's own timers go on
            self._epoch()
            self.dark_since, self.light_since = dark_since, light_since
        calibration = correction.get("calibration_sha256") if isinstance(correction, dict) else None
        if self.calibration_id is not None and calibration != self.calibration_id:
            self._epoch()
        self.calibration_id = calibration
        frames = raw.get("frames") or {}
        self.frame_id += 1
        ref = FrameRef(self.run_id, self.sessions, self.frame_id, bundle.session, self.mode,
                       self._plane(frames.get("rgb"), 640, 360) or PlaneRef(bundle.rgb_seq, meta.get("t_device"), bundle.t_host, 640, 360),
                       self._plane(frames.get("right"), 640, 400), self._plane(frames.get("depth"), 640, 400),
                       calibration, self.scope["transform_id"], self.scope["content_revision"], capture_mono,
                       bundle.arrival_mono, self.now() - (now_mono - capture_mono))
        self.pending_capture = capture_mono
        return Prepared(ref, meta, depth, bundle)

    def scaled(self, depth):
        """The depth plane times step 2's depth scale, before any lift: the same plane at 1.0."""
        if self.depth_table is None:
            return depth
        return self.t.takes.Depth(depth.width, depth.height, array("H", map(self.depth_table.__getitem__, depth.mm)),
                                  depth.t_host, depth.sequence)

    def process(self, bundle, camera_state, now_mono):
        """Synchronous replay entry; live uses prepare and finish around worker polling."""
        prepared = self.prepare(bundle, now_mono)
        if prepared is None:
            if self.problem in (NOT_CORRECTED, CORRECTION_CHANGED):
                return self.idle(camera_state, now_mono)
            return None
        detector = self.detector
        delay = max(0.0, now_mono - prepared.ref.capture_mono)
        reply = detector.detect(bundle, delay, ref=prepared.ref) if isinstance(detector, DetectorWorker) else detector.detect(bundle, delay)
        return self.finish(prepared, reply, camera_state, now_mono)

    def finish(self, prepared, reply, camera_state, now_mono):
        self.body_input = None
        self.pending_capture = None
        ref, meta, depth = prepared.ref, prepared.meta, prepared.depth
        capture_mono = ref.capture_mono
        if ref.camera_epoch != self.sessions or now_mono >= capture_mono + FRESH_S:
            return self._drop("expired", now_mono, "detector result expired")
        if "error" in reply:
            self._drop("detector_errors", now_mono, "detector errors")
            return self.idle(camera_state, now_mono)
        self.problem = None
        previous_mode = self.mode
        luma = reply.get("luma")
        if type(luma) not in (int, float) or not math.isfinite(luma):
            luma = None
        self._mode(luma, capture_mono)
        if previous_mode != self.mode:
            self._epoch()
            # The worker keyed this frame before the handover; start with the next frame.
            return self._snapshot(self.event(None, [], dict(camera_state, why="mode changed"), now_mono), status="degraded")
        if self.mode == "none" or luma is None or luma < DARK_LUMA:
            # A dark frame isn't a fresh one: after EPOCH_GAP_S of them, prepare starts the next epoch.
            if self.mode == "none" and ref.camera_epoch == self.sessions:
                self._submit_body(prepared)
            else:
                self._clear_body("pose_unavailable")
            return self._snapshot(self.event(now_mono - capture_mono, [], dict(camera_state, why="too dark"), now_mono),
                                  status="degraded")
        self.last_capture = capture_mono
        offset = capture_mono - ref.rgb.host_s
        row = reply["row"]
        row = dict(row, available_ts=ref.rgb.host_s + max(0.0, now_mono - capture_mono))
        start_lift = time.monotonic()
        observations = self.t.observe.observe(row, meta, depth, self.camera, self.settings, offset_s=offset)
        self._timing(now_mono, lift=time.monotonic() - start_lift, detector_model=reply.get("compute_s"))
        start_track = time.monotonic()
        self.counts["observations"] += len(observations)
        if any(o.get("validity") == "rejected:detector_result_expired" for o in observations):
            self.counts["expired"] += 1
            self.drops.append(now_mono)
        # Plan L3: a person box under PERSON_MIN reaches the tracker, as den-observe made it, but never the markers on
        # its own: a track shows only once a pose (den-detect poses only boxes at PERSON_MIN or more) or such a box
        # has supported it.
        strong = {o["obs_id"] for o in observations if o.get("validity") == "valid"
                  and (o.get("kind") == "keypoints" or (o.get("kind") == "person_box" and (o.get("score") or 0) >= PERSON_MIN))}
        frames, _counts = self.t.records.frames_from_observations(observations, self.track_method.tracker)
        detections = frames[0]["detections"] if frames else []
        tracks, supported, association = [], [], {}
        for track, state in self.core.step(capture_mono, detections):
            if strong.intersection(track.obs_ids):
                self.strong.add(track.id)
            if state not in OUT_STATES:
                continue
            if track.id not in self.strong:
                self.counts["weak_only_hidden"] += 1
                continue
            record = self.t.records.track_record(track, state)
            cov = record["position"]["cov"]
            fresh = bool(record["heading"]) and track.heading_as_of is not None and capture_mono - track.heading_as_of <= HEADING_FRESH_S
            tracks.append({"track_id": "%s-e%d-%s" % (self.run_id, self.sessions, record["track_id"]), "state": state,
                           "xy_m": [record["position"]["x"], record["position"]["y"]],
                           "sigma_m": round(math.sqrt(max(cov[0][0], cov[1][1], 0.0)), 3),
                           "heading_deg": record["heading"]["deg"] if fresh else None,
                           "age_s": round(max(0.0, capture_mono - track.last_seen), 3)})
            supported.append(set(track.obs_ids))
            # den-track's association (confident or ambiguous, since when on this clock), for the ask's evidence
            association[tracks[-1]["track_id"]] = (record["association"]["status"], record["association"]["since_ts"])
        now_mono += time.monotonic() - start_lift
        if now_mono >= capture_mono + FRESH_S:
            return self._drop("expired", now_mono, "tracking result expired")
        self._timing(now_mono, track=time.monotonic() - start_track)
        self.lags.append((now_mono, now_mono - capture_mono))
        event = self.event(now_mono - capture_mono, tracks, camera_state, now_mono)
        candidates = [o for o in observations if o["kind"] in ("person_box", "keypoints")
                      and (o.get("score") or 0) >= PERSON_MIN]
        evidence = []
        for marker, obs_ids in zip(event["tracks"], supported):
            track_id = marker["track_id"]
            status, since = association[track_id]
            sources = [o for o in candidates if o["obs_id"] in obs_ids and o.get("validity") == "valid"]
            pose = next((o for o in sources if o["kind"] == "keypoints"), {})
            safe = (marker["state"] == "confirmed" and status == "confident" and len(candidates) == 1
                    and bool(sources))
            # The association grace: the revision moves once safe (and, while safe, den-track's since) has held its
            # new value for two consecutive processed frames. This frame says confident only while it's safe and the
            # revision is a safe one, so a single unsafe frame reads ambiguous and keeps the revision.
            revision, committed, pending = self.associations.get(track_id, (0, (False, None), None))
            current = (True, since) if safe else (False, None)
            if current == committed:
                pending = None
            elif current == pending:
                revision, committed, pending = revision + 1, current, None
            else:
                pending = current
            self.associations[track_id] = revision, committed, pending
            safe = safe and current == committed
            stereo = stereo_joints(pose) if meta.get("intrinsics") is not None else {}
            position_observations = [o for o in sources if o.get("room_position")]
            if not position_observations:
                basis = "coast"
            else:
                stereo_torso = {"left_hip", "right_hip", "left_shoulder", "right_shoulder"}.intersection(stereo)
                basis = "stereo" if stereo_torso or any(o["kind"] == "person_box" for o in position_observations) else "plane"
            evidence.append(TrackEvidence(track_id, marker["state"], tuple(marker["xy_m"]), marker["sigma_m"],
                                          basis, tuple(sorted(obs_ids)), "confident" if safe else "ambiguous",
                                          revision, since, MappingProxyType(stereo), marker.get("near"),
                                          seated=self.seated.update(track_id, revision, capture_mono, stereo)))
            # The people projection carries the person binding (contract 2): run, epoch, track id, revision.
            marker.update(service_run_id=self.run_id, camera_epoch=self.sessions, association_revision=revision)
            if safe and stereo and ref.depth is not None:
                depth_mono = ref.depth.host_s + offset
                if (abs(ref.depth.host_s - ref.rgb.host_s) <= RGB_PAIR_S + 1e-9
                        and now_mono < min(capture_mono, depth_mono) + FIGURE_S):
                    self.body_input = {"frame": ref, "track_id": track_id, "observation_id": pose["obs_id"],
                                       "association_revision": revision, "box_native": pose["image_box"],
                                       "depth_mm": depth, "left_k": self.camera.rig.left_k,
                                       "selected_k": self.camera.rig.rgb_k,
                                       "selected_from_left": (self.camera.rig.rotation, self.camera.rig.translation),
                                       "room_from_left": self.camera.room_from_left,
                                       "alignment_error_m": self.camera.error_bound_m,
                                       "pair_skew_s": ref.depth.host_s - ref.rgb.host_s,
                                       "selected_distortion": self.camera.rig.rgb_d,
                                       "valid_until_mono": min(capture_mono, depth_mono) + FIGURE_S,
                                       "pose": stereo, "head_native": tuple(pose["keypoints"][:5]),
                                       "joint_covariance_m2": {COCO[j["j"]]: j["cov"] for j in pose["joints_room"]
                                                               if j.get("lift") == "stereo"}}
        self.associations = {e.track_id: self.associations[e.track_id] for e in evidence}
        self.seated.keep([e.track_id for e in evidence])
        possible_person = any(o["kind"] in ("person_box", "keypoints") for o in observations)
        presence = "present" if candidates else "unknown" if possible_person or tracks else "empty"
        self._submit_body(prepared, observations=observations, tracks=tracks, supported=supported, association=association)
        snapshot_event = self._snapshot(event, ref=ref, status="fresh", presence=presence, tracks=evidence)
        return snapshot_event


COCO = ("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
        "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle",
        "right_ankle")   # den-observe's coco17 joints_room order


def stereo_joints(pose):
    """Only current metric joints can support the seated rule and downstream aim."""
    return {COCO[j["j"]]: (tuple(j["xyz"]), "supported") for j in pose.get("joints_room", ())
            if j.get("lift") == "stereo" and j.get("status") == "supported" and j.get("xyz") is not None}


def pose_joints(pose):
    """A pose observation's room joints {name: [xyz, status]}, supported or uncertain only."""
    joints = {}
    for entry in pose.get("joints_room") or []:
        j = entry.get("j")
        if isinstance(j, int) and 0 <= j < len(COCO) and entry.get("xyz") and entry.get("status") in ("supported", "uncertain"):
            joints[COCO[j]] = [entry["xyz"], entry["status"]]
    return joints


def track_poses(observations, tracks, supported):
    """{track_id: room joints} for each confirmed track a pose supports in this frame (the seated test's input)."""
    poses = {o["obs_id"]: o for o in observations if o.get("kind") == "keypoints" and o.get("validity") == "valid"}
    out = {}
    for track, obs_ids in zip(tracks, supported):
        if track["state"] != "confirmed":
            continue
        pose = next((poses[o] for o in sorted(obs_ids) if o in poses), None)
        out[track["track_id"]] = pose_joints(pose) if pose is not None else {}
    return out


def _legacy_body_person(observations, tracks, supported):
    """Who the live body is made for: the first confirmed track a pose supports in this frame, with that pose's room
    joints (supported or uncertain; unsupported ones left out); else the first confirmed track, without joints; else
    None (nobody tracked, so the body step may learn the empty room)."""
    poses = {o["obs_id"]: o for o in observations if o.get("kind") == "keypoints" and o.get("validity") == "valid"}
    first = None
    for track, obs_ids in zip(tracks, supported):
        if track["state"] != "confirmed":
            continue
        person = {"track_id": track["track_id"], "xy_m": track["xy_m"], "joints": {}}
        first = first or person
        for obs_id in obs_ids:
            pose = poses.get(obs_id)
            if pose is None:
                continue
            for entry in pose.get("joints_room") or []:
                j = entry.get("j")
                if isinstance(j, int) and 0 <= j < len(COCO) and entry.get("xyz") and entry.get("status") in ("supported", "uncertain"):
                    person["joints"][COCO[j]] = [entry["xyz"], entry["status"]]
            return person
    if first is not None:
        return first
    if tracks:
        return {"track_id": tracks[0]["track_id"], "xy_m": tracks[0]["xy_m"], "joints": {}}
    # Someone the tracker hasn't confirmed yet still isn't an empty room: no body, and no learning either.
    seen = any(o.get("kind") in ("keypoints", "person_box") for o in observations)
    return {"track_id": None, "xy_m": None, "joints": {}} if seen else None


def body_person(track, pose, heading, max_heading_sd=None):
    """Pass only the current supported body joints from one confidently associated pose."""
    if max_heading_sd is None:
        return _legacy_body_person(track, pose, heading)
    if (not track or track.get("state") != "confirmed" or track.get("association") != "confident"
            or not isinstance(track.get("track_id"), str) or not 0 < len(track["track_id"]) <= 64
            or not pose or pose.get("kind") != "keypoints" or pose.get("validity") != "valid"):
        return None
    joints = {name: entry for name, entry in pose_joints(pose).items() if entry[1] == "supported" and name in COCO[5:]}
    names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    facing = None
    if all(name in joints for name in names):
        left, right, hip_l, hip_r = (joints[name][0] for name in names)
        sx, sy = right[0] - left[0], right[1] - left[1]
        hx, hy = hip_r[0] - hip_l[0], hip_r[1] - hip_l[1]
        span = math.hypot(sx, sy) * math.hypot(hx, hy)
        if min(math.hypot(sx, sy), math.hypot(hx, hy)) >= 0.08 and sx * hx + sy * hy >= 0.5 * span:
            value = heading(pose)
            if value is not None and value[1] <= max_heading_sd:
                facing = value[0]
    return {"track_id": track["track_id"], "xy_m": track.get("xy_m"), "joints": joints, "facing_deg": facing}


class Loop(threading.Thread):
    """The newest bundle from the mailbox, perceived, published. When no event has gone out for idle_s (no frame, or
    every frame dropped), an idle event says why, so the page never reads a running loop as stopped."""

    def __init__(self, source, mailbox, perceiver, publish, *, idle_s=1.0, clock=time.monotonic, observe=None, body_sink=None):
        """Observers receive the public event and its in-process snapshot before publication."""
        super().__init__(daemon=True, name="den-live-loop")
        self.source, self.mailbox, self.perceiver, self.publish = source, mailbox, perceiver, publish
        self.idle_s, self.clock = idle_s, clock
        self.body_sink = body_sink
        self.observers = list(observe) if isinstance(observe, (list, tuple)) else ([observe] if observe else [])
        self.halt = threading.Event()
        self.stop_reason = self.stop_error = None

    def _publish(self, event):
        for observe in self.observers:
            try:
                event = observe(event, self.perceiver.snapshot) or event
            except Exception:   # an observer cannot take the camera loop down
                pass
        if self.body_sink is not None:
            try:
                self.body_sink(self.perceiver.snapshot, self.perceiver.body_input)
            except Exception:   # body processing cannot stop the marker loop
                pass
        started = time.monotonic()
        self.publish(event)
        self.perceiver._timing(self.clock(), publish=time.monotonic() - started)

    def run(self):
        p = self.perceiver
        detector = p.detector
        pending = None
        submitted = None
        last = self.clock()
        try:
            while not self.halt.is_set():
                now = self.clock()
                camera = self.source.state()
                ref = p.snapshot.frame
                if ref is not None and (now >= ref.capture_mono + FRESH_S
                                        or camera.get("status") in ("offline", "refused")
                                        or camera.get("session") not in (None, ref.camera_session)):
                    self._publish(p.idle(camera, now))
                    last = now
                if pending is not None:
                    reply = detector.poll()
                    if reply is not None:
                        result_at = self.clock()
                        p._timing(result_at, detector_turnaround=result_at - submitted)
                        current = self.source.state()
                        if (current.get("status") in ("offline", "refused")
                                or current.get("session") not in (None, pending.ref.camera_session)):
                            event = p.idle(current, result_at)
                        else:
                            event = p.finish(pending, reply, current, result_at)
                        pending = None
                        if event is not None:
                            self._publish(event)
                            last = result_at
                if pending is None:
                    bundle = self.mailbox.take(min(self.idle_s, 0.02))
                    if bundle is not None:
                        now = self.clock()
                        if not hasattr(detector, "submit"):
                            event = p.process(bundle, camera, now)
                            if event is not None:
                                self._publish(event)
                                last = now
                        else:
                            prepared = p.prepare(bundle, now)
                            if p.snapshot.camera_epoch != p.sessions:
                                self._publish(p._snapshot(p.event(None, [], camera, now), status="starting"))
                                last = now
                            if prepared is None and p.problem in (NOT_CORRECTED, CORRECTION_CHANGED, "camera clock moved back"):
                                self._publish(p.idle(camera, now))
                                last = now
                            if prepared is not None and detector.submit(bundle, now - prepared.ref.capture_mono, prepared.ref):
                                pending = prepared
                                submitted = now
                else:
                    self.halt.wait(0.02)
                now = self.clock()
                if p.snapshot.frame is None and now - last >= self.idle_s:
                    self._publish(p.idle(self.source.state(), now))
                    last = now
        except Stop as exc:
            self.stop_reason, self.stop_error = str(exc), exc
        except Exception as exc:  # a type name only: the loop's text could quote what it read
            self.stop_reason = "the loop stopped (%s)" % type(exc).__name__

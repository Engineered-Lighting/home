"""Step 3's perception: one camera bundle -> detector row -> observations -> tracks -> one people event (plan L1-L6).

Standard library only, in the loop's own process. The detector runs in its own process, in the detector's venv, with
no network (detect_worker.py). den-observe (rung 2) and den-track (rung 3) run unedited at the den simulation's pins:
- observe.observe() on each frame, with Camera built once from step 2's room model;
- TrackerCore.step() once per frame, on a method copy whose floor_m is your scan's floor, padded (see FLOOR_PAD_M).

Numbers this phase sets, with where they come from:
- FRESH_S 1.5: the detector-freshness exception, plan pick 10. den-observe proposes 1.0; a frame crosses between
  homes at step 4.
- The registration gate is step 2's method file's gate_m (0.10). den-observe's own max_error_bound_m is the ladder's
  0.05, which a scan-picked registration isn't held to (plan pick 3).
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
  that doesn't is refused, counted and never lifted.

An event holds tracks' floor positions in your scan's frame, their spread, heading and age: no joints, no image, no
name. Only confirmed and coasting tracks go out, and only once a pose or a person box at 0.5 or more has supported
them (plan L3). Track ids carry the epoch (e1-trk-1), so a camera restart's new tracks never take an old marker over.
Each track also carries near: what's near it from your confirmed labels (room_labels.near_phrase, for example "at the
desk, beside the 3D printer"), or null with no labels, none near, or labels placed on another scan revision. Home
shows it under the marker.
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
from collections import deque
from pathlib import Path

import room_labels
from body import head_exclusion
from body_input import qualified as qualified_body_input

SCHEMA = "den-live-people-1"
FRESH_S = 1.5
FLOOR_PAD_M = 1.0
# den-track's METHOD.md at its pin: the floor_m slot's exact text, which the method copy replaces (as the den
# simulation's chain.py hop 8 does, 85744f2c on claude/den-sim-connected).
TRACK_FLOOR_SLOT = '"floor_m": {"open": true, "proposed": [0.0, 0.0, 4.572, 3.759]'
TRACK_METHOD_COPY = "METHOD-den-track-floor.md"
DARK_LUMA, LIGHT_LUMA, DARK_S = 40.0, 60.0, 3.0
OFFSET_WINDOW, OFFSET_QUANTILE = 50, 0.05
OUT_STATES = ("confirmed", "coasting")
EPOCH_BACK_S = 1.0   # the camera's own clock going back more than this within a session is a new epoch
HEALTH_S = 10.0      # frames a second and lag are over this window only: an old frame never speaks for now
HEADING_FRESH_S = 3.0   # an arrow older than this is left off: den-track keeps its last heading indefinitely
PERSON_MIN = 0.5     # den-detect's pose line, and plan L3's marker line
TRANSIT_MAX_S = 5.0  # arrival less captured_unix_ns counts only between 0 and this (plan L1)


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
        self.write_lock = threading.Lock()   # the loop and the ask's sender both write requests
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

    def send_alone(self, header):
        """A header with no bundle after it (the live body's setup and requests). Raises Stop if the process is gone."""
        data = json.dumps(header, separators=(",", ":")).encode("utf-8")
        try:
            with self.write_lock:
                self.proc.stdin.write(struct.pack(">I", len(data)) + data)
                self.proc.stdin.flush()
        except (OSError, ValueError):
            raise Stop("the detector process stopped (its log: %s)" % self.log.name) from None

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

    def detect(self, bundle, delay_s, *, frame_key=None):
        self.next_id += 1
        header = json.dumps({"id": self.next_id, "rgb_seq": bundle.rgb_seq, "t_host": bundle.t_host,
                             "delay_s": round(delay_s, 4), "frame_key": frame_key}).encode("utf-8")
        try:
            with self.write_lock:
                self.proc.stdin.write(struct.pack(">I", len(header)) + header + struct.pack(">I", len(bundle.data)) + bundle.data)
                self.proc.stdin.flush()
        except OSError:
            raise Stop("the detector process stopped (its log: %s)" % self.log.name) from None
        reply = self._line(self.frame_timeout_s, "a frame")
        if reply.get("id") != self.next_id:
            raise Stop("the detector answered out of turn")
        return reply

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


# ---- one frame -----------------------------------------------------------------------------------------------------

class Perceiver:
    """Times: the tracker runs on this PC's monotonic clock (capture_mono = t_host + the arrival-anchored offset), so a
    step in the wall clock (time sync) can't send it backwards or stall it. Events carry wall-clock times: emitted_ts is
    now, capture_ts is now less how long ago the frame was captured."""

    def __init__(self, tools, *, room_model_path, camera_id, max_error_bound_m, track_method_path, home, detector=None,
                 require_source_correction=True, content_revision=None, now=time.time):
        self.t, self.detector, self.home, self.now = tools, detector, home, now
        self.body = None   # a body_link.BodyLink, when the live body runs: asked for each fresh frame, never waited for
        self.body_input = None
        self.labels = None   # a room_labels.Store: each track's near phrase, from the confirmed labels on this scan
        self.require_source_correction = require_source_correction
        try:
            room_model = tools.roommodel.load(room_model_path)
            transform_id = tools.roommodel.current_transform_id(room_model, camera_id)
            self.camera = tools.roommodel.Camera(room_model, transform_id, max_error_bound_m=max_error_bound_m)
            self.camera.take_state(now(), scored=False)   # this session ends now: after the registration's own take
        except tools.roommodel.RegistrationRefused as exc:
            raise Refused("the registration can't be used: %s" % exc) from None
        method = tools.observe_method.load(tools.observe_tree / "tools" / "den-observe" / "METHOD.md")
        self.settings = tools.observe.Settings(method)
        self.settings.max_age_s = FRESH_S
        self.track_method = tools.track_method.Method(str(track_method_path))
        self.floor = self.track_method.room["floor_m"]
        self.scope = {"transform_id": self.camera.transform_id, "content_revision": content_revision}
        self.clock = ClockOffset()
        self.session, self.sessions, self.seq = None, 0, 0
        self.mode, self.dark_since, self.light_since = "rgb", None, None
        self.counts = {"frames": 0, "refused_depth": 0, "unreadable": 0, "detector_errors": 0, "expired": 0, "out_of_order": 0,
                       "observations": 0, "weak_only_hidden": 0}
        self.last_t_host = self.last_capture = None
        self.arrivals, self.lags, self.drops, self.transits = deque(), deque(), deque(), deque()
        self.problem = None
        self.last_frame = None   # the last fully processed frame, for the Qwen ask (process() sets it)
        self._new_core()

    def _new_core(self):
        room = self.track_method.room
        self.core = self.t.records.tracker_core.TrackerCore(self.track_method.tracker, exits=room["exits_m"], floor=room["floor_m"])
        self.strong = set()   # tracks a pose or a person box at PERSON_MIN or more has ever supported
        self.last_capture = None

    def _drop(self, what, now_mono, problem):
        self.counts[what] += 1
        self.drops.append(now_mono)
        self.problem = problem
        self._clear_body("stale" if what == "out_of_order" else "unavailable")
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
        return {"fps_in": round((len(self.arrivals) - 1) / seconds, 2) if seconds > 0 else None,
                "dropped_per_min": len(self.drops),
                "lag_s_median": round(statistics.median(lag for _, lag in self.lags), 3) if self.lags else None,
                "transit_s_median": round(statistics.median(s for _, s in self.transits), 3) if self.transits else None}

    def _transit(self, meta, bundle):
        """Arrival here less the bundle's captured_unix_ns, kept only between 0 and TRANSIT_MAX_S (the module note)."""
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

    def idle(self, camera_state, now_mono):
        """An event with no new frame shown: the camera is offline, slow, or its frames are being dropped (the why says
        which). Tracks are left to fade on the page."""
        self._clear_body("stale" if camera_state.get("status") == "ok" else "unavailable")
        return self.event(None, [], camera_state, now_mono)

    def _clear_body(self, reason):
        if self.body is not None:
            self.body.clear(reason)

    def _body_input(self, bundle, meta, depth):
        if self.body is None or self.body_input is None:
            return None
        try:
            value = self.body_input(bundle, meta, depth)
            if qualified_body_input(value, self.scope) and value.capture_earliest_mono <= bundle.arrival_mono:
                return value
        except Exception:
            pass   # the optional body adapter cannot interrupt tracking
        self._clear_body("unavailable")
        return None

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
        self.body.prepare(scope, binding, person["track_id"] if person else None)
        return dict(frame_key=value.frame_key, scope=scope, setup=setup, capture_mono=value.capture_earliest_mono,
                    observation=value.observation, person=person)

    def _mode(self, luma, now_mono):
        if luma is None:
            return
        if luma < DARK_LUMA:
            self.light_since = None
            self.dark_since = self.dark_since or now_mono
            if self.mode == "rgb" and now_mono - self.dark_since >= DARK_S:
                self.mode = "none"
        elif luma > LIGHT_LUMA:
            self.dark_since = None
            self.light_since = self.light_since or now_mono
            if self.mode == "none" and now_mono - self.light_since >= DARK_S:
                self.mode = "rgb"
        else:
            self.dark_since = self.light_since = None

    def process(self, bundle, camera_state, now_mono):
        """One bundle to one event, or None when the frame is dropped (counted; the loop says why if it lasts).
        now_mono is this PC's monotonic clock now. Raises Stop when the registration no longer fits the camera."""
        t = self.t
        self.last_frame = None
        went_back = self.last_t_host is not None and bundle.t_host < self.last_t_host - EPOCH_BACK_S
        if bundle.session != self.session or went_back:   # a camera restart starts a new epoch: new tracks, a new clock
            self._clear_body("scope_changed")
            self.session, self.sessions = bundle.session, self.sessions + 1
            self.clock.reset()
            self._new_core()
        self.last_t_host = bundle.t_host
        self.counts["frames"] += 1
        self.arrivals.append(bundle.arrival_mono)
        self.clock.add(bundle.t_host, bundle.arrival_mono)
        offset = self.clock.offset()
        capture_mono = bundle.t_host + offset
        # den-track takes frames in time order only. The offset is re-estimated each frame, so a frame can land at or
        # before the last one by a few milliseconds; it's skipped and counted, never fed backwards.
        if self.last_capture is not None and capture_mono <= self.last_capture:
            return self._drop("out_of_order", now_mono, "frames out of order")
        try:
            meta, depth = t.takes.read_frame(bundle.data)
        except t.takes.BundleError:
            return self._drop("unreadable", now_mono, "frames unreadable")
        self._transit(meta, bundle)
        correction = (meta.get("raw") or {}).get("depth_correction")
        if self.require_source_correction and not (isinstance(correction, dict) and correction.get("applied") is True):
            self._drop("refused_depth", now_mono, "depth not corrected at the camera")
            return self.event(now_mono - capture_mono, [], camera_state, now_mono)
        try:
            self.camera.check_frame(meta.get("intrinsics"))
        except t.roommodel.RegistrationRefused as exc:
            self._clear_body("scope_changed")
            raise Refused("the camera no longer matches the registration (%s); register again with step 2" % exc) from None
        body_request = None
        try:
            body_request = self._body_request(self._body_input(bundle, meta, depth))
        except Exception:
            self._clear_body("unavailable")
        body_key = {"frame_key": body_request["frame_key"]} if body_request is not None else {}
        reply = self.detector.detect(bundle, max(0.0, now_mono - capture_mono), **body_key)
        if "error" in reply:
            return self._drop("detector_errors", now_mono, "detector errors")
        if body_request is not None:
            try:
                self.body.submit(self.detector.next_id, **body_request)
            except Exception:
                self._clear_body("unavailable")
        self.problem = None
        self._mode(reply.get("luma"), now_mono)
        if self.mode == "none":
            return self.event(now_mono - capture_mono, [], dict(camera_state, why="too dark"), now_mono)
        self.last_capture = capture_mono
        observations = t.observe.observe(reply["row"], meta, depth, self.camera, self.settings, offset_s=offset)
        self.counts["observations"] += len(observations)
        if any(o.get("validity") == "rejected:detector_result_expired" for o in observations):
            self.counts["expired"] += 1
            self.drops.append(now_mono)
        # Plan L3: a person box under PERSON_MIN reaches the tracker, as den-observe made it, but never the markers on
        # its own: a track shows only once a pose (den-detect poses only boxes at PERSON_MIN or more) or such a box
        # has supported it.
        strong = {o["obs_id"] for o in observations if o.get("validity") == "valid"
                  and (o.get("kind") == "keypoints" or (o.get("kind") == "person_box" and (o.get("score") or 0) >= PERSON_MIN))}
        frames, _counts = t.records.frames_from_observations(observations, self.track_method.tracker)
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
            record = t.records.track_record(track, state)
            cov = record["position"]["cov"]
            fresh = bool(record["heading"]) and track.heading_as_of is not None and capture_mono - track.heading_as_of <= HEADING_FRESH_S
            tracks.append({"track_id": "e%d-%s" % (self.sessions, record["track_id"]), "state": state,
                           "xy_m": [record["position"]["x"], record["position"]["y"]],
                           "sigma_m": round(math.sqrt(max(cov[0][0], cov[1][1], 0.0)), 3),
                           "heading_deg": record["heading"]["deg"] if fresh else None,
                           "age_s": round(max(0.0, capture_mono - track.last_seen), 3)})
            supported.append(set(track.obs_ids))
            # den-track's association (confident or ambiguous, since when on this clock), for the ask's evidence
            association[tracks[-1]["track_id"]] = (record["association"]["status"], record["association"]["since_ts"])
        self.lags.append((now_mono, now_mono - capture_mono))
        event = self.event(now_mono - capture_mono, tracks, camera_state, now_mono)
        # For the Qwen ask (ask.py): this frame's detection id (the detector's process keeps the frame under it), its
        # times, and each confirmed track's pose heights for the seated test.
        self.last_frame = {"id": getattr(self.detector, "next_id", None), "session": self.sessions, "capture_ts": event["capture_ts"],
                           "capture_mono": capture_mono, "arrival_unix": bundle.arrival_unix,
                           "poses": track_poses(observations, tracks, supported), "association": association}
        return event


COCO = ("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
        "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle",
        "right_ankle")   # den-observe's coco17 joints_room order


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


def body_person(track, pose, heading, max_heading_sd):
    """Pass only the current supported body joints from one confidently associated pose."""
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

    def __init__(self, source, mailbox, perceiver, publish, *, idle_s=1.0, clock=time.monotonic, observe=None):
        """observe: one observer or a list of them, observe(event, frame), in order. Each sees each event before it's
        published and returns it, perhaps with more in it (the Qwen ask, ask.Asker.observe; later the end-to-end plan's
        person state). frame is the perceiver's last_frame for an event of a processed frame, else None. An observer's
        failure is its own: the event goes on as it was."""
        super().__init__(daemon=True, name="den-live-loop")
        self.source, self.mailbox, self.perceiver, self.publish = source, mailbox, perceiver, publish
        self.idle_s, self.clock = idle_s, clock
        self.observers = list(observe) if isinstance(observe, (list, tuple)) else ([observe] if observe else [])
        self.halt = threading.Event()
        self.stop_reason = self.stop_error = None

    def run(self):
        try:
            last = self.clock()
            while not self.halt.is_set():
                bundle = self.mailbox.take(self.idle_s)
                now_mono = self.clock()
                event = None if bundle is None else self.perceiver.process(bundle, self.source.state(), now_mono)
                frame = self.perceiver.last_frame if event is not None else None
                if event is None and now_mono - last >= self.idle_s:
                    event = self.perceiver.idle(self.source.state(), now_mono)
                if event is not None:
                    for observe in self.observers:
                        try:
                            event = observe(event, frame) or event
                        except Exception:   # an observer's failure is its own; the markers go on
                            pass
                    self.publish(event)
                    last = now_mono
        except Stop as exc:
            self.stop_reason, self.stop_error = str(exc), exc
        except Exception as exc:  # a type name only: the loop's text could quote what it read
            self.stop_reason = "the loop stopped (%s)" % type(exc).__name__

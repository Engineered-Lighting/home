"""den-live's Qwen ask (inference plan, step 2). Standard library, in the loop's own process.

When it asks (the owner, 2026-11-12: Qwen only once Frigate has noticed someone):
- Frigate's occupancy reads on (occupancy.py), a confirmed track is seated, the settings say asks_on, and the back-off
  monitor is below its step 2. Seated is contract 2's, which perception publishes once per track (seated.Published,
  on TrackEvidence.seated); the ask reads it and never works it out again, so it and the preview have one answer.
- One ask in flight at a time; the next no sooner than min_interval_s after the last was sent (2 s; at least 4 s at the
  monitor's step 1); at most max_per_minute in any 60 s; BACKOFF_S after a failure.
What it sends: 3 stills from the last 3 s, none from before the seated episode's since_mono or the association's since,
picked by spread() (perception-lab qwen_live.spread at den-core-live's pin, copied), made into JPEGs in the detector's
process (stills.py), in den_live_scene's den-live-scene-2 body. The context is the local time, Qwen's last valid
answer when it's under 10 s old, and near_person: the labeling plan's line, only while its switch is on and exactly one
person is tracked.
What it keeps: one words-only line per ask in <state>/days/<UTC date>/asks.jsonl (no image, no reply text), for
RETENTION_DAYS. Every time the chain has is in it: each still's capture and arrival, decided, sent and answered.
observe() updates the current TrackingSnapshot and pop_reply() returns a bound InferenceReply after the snapshot.
The people stream shows Qwen health and the eligible track's current activity.
"""
import base64
import hashlib
import json
import queue
import shutil
import statistics
import threading
import time
import urllib.request
from collections import deque
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import den_live_scene as scene
from contracts import INFERENCE_S, PERCEPTION_S, InferenceReply

ASK_SCHEMA = "den-live-ask-1"
SETTINGS_KEYS = {"asks_on": True, "labels_to_qwen": True, "min_interval_s": 2.0, "max_per_minute": 30}
SLOW_INTERVAL_S = 4.0     # the back-off monitor's step 1
BACKOFF_S = 30.0          # after a failed ask
STILLS_TIMEOUT_S = 2.0
POST_TIMEOUT_S = 20.0
POOL_S = 5.0              # frames the pool keeps; the detector's process keeps about as many (stills.RING)
UNAVAILABLE_AFTER = 3     # failed asks in a row before the health line says Qwen isn't answering
RETENTION_DAYS = 14
BACKOFF_STALE_S = 600.0   # the monitor writes its status each minute: older than this, it isn't running


class FramesUnavailable(Exception):
    pass


def spread(pool, now, *, count=3, span=3.0):
    """Select oldest-first stills from a continuous supported window using LA monotonic time."""
    if not pool:
        raise FramesUnavailable("frames_unavailable")
    latest = pool[-1]
    if now - latest.capture_mono >= PERCEPTION_S:
        raise FramesUnavailable("frames_stale")
    chosen, step = [latest], span / (count - 1)
    for index in range(1, count):
        target = latest.capture_mono - step * index
        options = [f for f in pool if f.capture_mono < chosen[-1].capture_mono - .2
                   and abs(f.capture_mono - target) <= step / 2]
        if options:
            chosen.append(min(options, key=lambda f: (abs(f.capture_mono - target), f.key)))
        else:
            break
    if len(chosen) < scene.MIN_IMAGES:
        raise FramesUnavailable("frames_insufficient")
    chosen.reverse()
    return chosen


# ---- what it reads -------------------------------------------------------------------------------------------------

class Settings:
    """The asks block of den-live's settings file, re-read whenever the file changes, so a switch needs no restart.
    It fails closed: no block, or one that doesn't read, turns the asks off until it does, and why says so. The pace
    can't go past AGENTS.md's bound (at least 2 s apart, at most 30 a minute)."""

    def __init__(self, path=None, block=None):
        self.path, self.mtime, self.why = (Path(path) if path else None), None, None
        self.values = dict(SETTINGS_KEYS)
        if block is not None:
            self._take(block)

    def _off(self, why):
        self.values, self.why = dict(self.values, asks_on=False), why

    def _take(self, block):
        if not isinstance(block, dict):
            return self._off("the settings file has no asks block")
        values = dict(SETTINGS_KEYS)
        for key, default in SETTINGS_KEYS.items():
            if key not in block:
                continue
            value = block[key]
            if isinstance(default, bool):
                ok = type(value) is bool
            elif key == "min_interval_s":
                ok = type(value) in (int, float) and 2.0 <= value <= 60.0
            else:
                ok = type(value) is int and 1 <= value <= 30
            if not ok:
                return self._off("the asks settings don't read (%s)" % key)
            values[key] = value
        self.values, self.why = values, None

    def get(self):
        if self.path is not None:
            try:
                mtime = self.path.stat().st_mtime_ns
                if mtime != self.mtime:
                    self.mtime = mtime
                    self._take(json.loads(self.path.read_text(encoding="utf-8")).get("asks"))
            except (OSError, ValueError, AttributeError):
                self.mtime = None   # read it again next time
                self._off("the settings file doesn't read")
        return dict(self.values)


class Backoff:
    """The back-off monitor's level (0-3) from its status file. With no file set, None: there's no monitor to follow.
    With one set that's missing, stale or unreadable, 1: a monitor that has gone quiet slows the asks rather than
    lifting its own step."""

    def __init__(self, path=None, *, wall=time.time):
        self.path, self.wall = (Path(path) if path else None), wall

    def level(self):
        if self.path is None:
            return None
        try:
            if self.wall() - self.path.stat().st_mtime > BACKOFF_STALE_S:
                return 1
            level = json.loads(self.path.read_text(encoding="utf-8")).get("level")
        except (OSError, ValueError, AttributeError):
            return 1
        return level if type(level) is int and 0 <= level <= 3 else 1


def poster(url, *, timeout_s=POST_TIMEOUT_S):
    """post(body bytes) -> vLLM's parsed chat completion, or raises. url: vLLM's base, http://127.0.0.1:8000 on LA."""
    target = url.rstrip("/") + "/v1/chat/completions"

    def post(body):
        request = urllib.request.Request(target, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read(1 << 20).decode("utf-8"))
    return post


def local_label(wall, zone=None):
    """The time label for the context, in the den's own time zone (zone, an IANA name from the settings file), else
    the host's."""
    stamp = time.localtime(wall)
    if zone:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            moment = datetime.fromtimestamp(wall, ZoneInfo(zone))
            return scene.local_time_label(moment.hour, moment.minute)
        except Exception:
            pass
    return scene.local_time_label(stamp.tm_hour, stamp.tm_min)


# ---- the log -------------------------------------------------------------------------------------------------------

class AskLog:
    """One line per ask, words and numbers only, in <state>/days/<UTC date>/asks.jsonl; days past RETENTION_DAYS go."""

    def __init__(self, state):
        self.days, self.today = Path(state) / "days", None
        self.lock = threading.Lock()

    def write(self, line, wall):
        day = time.strftime("%Y-%m-%d", time.gmtime(wall))
        with self.lock:
            folder = self.days / day
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "asks.jsonl", "a", encoding="utf-8", newline="\n") as out:
                out.write(json.dumps(line, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
            if day != self.today:
                self.today = day
                cutoff = time.strftime("%Y-%m-%d", time.gmtime(wall - RETENTION_DAYS * 86400))
                for old in self.days.iterdir():
                    if old.is_dir() and len(old.name) == 10 and old.name < cutoff:
                        shutil.rmtree(old, ignore_errors=True)


# ---- the ask -------------------------------------------------------------------------------------------------------

class Asker:
    def __init__(self, *, detector, occupancy, post, state, site_id, camera_id, settings=None, backoff=None,
                 near_line=None, zone=None, clock=time.monotonic, wall=time.time, start=True):
        self.detector, self.occupancy, self.post = detector, occupancy, post
        self.settings, self.backoff = settings or Settings(), backoff or Backoff()
        self.near_line, self.zone, self.clock, self.wall = near_line, zone, clock, wall
        self.site_id, self.camera_id = site_id, camera_id
        self.log = AskLog(state)
        self.lock = threading.Lock()
        self.pool = deque()
        self.support = self.eligibility = self.gate_signature = None
        self.eligibility_revision = self.gate_revision = 0
        self.snapshot = self.reply = self.activity = self.prior = None
        self.inflight = self.completed = None
        self.next_at = self.backoff_until = 0.0
        self.sent = deque()
        self.reply_s = deque(maxlen=20)
        self.last_reply_at = None
        self.failed_in_row = self.stills_failed_in_row = 0
        self.ask_seq = 0
        self.session_id = uuid4().hex
        self.counts = {"asked": 0, "answered": 0, "invalid": 0, "unbound": 0, "failed": 0, "stills_failed": 0,
                       "frames_unavailable": 0, "log_failed": 0}
        self.jobs = queue.Queue(maxsize=16)
        detector.on_stills = self._stills
        self.halt = threading.Event()
        self.sender = threading.Thread(target=self._send_loop, daemon=True, name="den-live-ask")
        if start:
            self.sender.start()

    def _gate(self):
        """With the lock held, record effective gate changes observed by either thread."""
        values, level = self.settings.get(), self.backoff.level()
        occupancy = self.occupancy.evidence()
        enabled = values["asks_on"] and (level is None or level < 2)
        signature = (enabled, occupancy["effective"] == "on", occupancy["revision"])
        if signature != self.gate_signature:
            self.gate_signature = signature
            self.gate_revision += 1
        if not values["asks_on"]:
            why = "switched off" + (": %s" % self.settings.why if self.settings.why else "")
        elif not enabled:
            why = "paused by the back-off monitor"
        else:
            why = occupancy["why"]
        return values, level, occupancy, why

    @staticmethod
    def _track(snapshot, now):
        frame = snapshot.frame
        if (snapshot.status != "fresh" or frame is None or frame.mode != "rgb"
                or not 0 <= now - frame.capture_mono < PERCEPTION_S):
            return None
        current = [track for track in snapshot.tracks if track.current_obs_ids]
        if len(current) != 1:
            return None
        track = current[0]
        if (track.state == "confirmed" and track.association_status == "confident"
                and track.association_since_mono is not None and track.association_since_mono <= frame.capture_mono):
            return track
        return None

    @staticmethod
    def _seated_since(track, frame):
        """The seated episode's since_mono when perception says this track is seated on this frame, else None."""
        state = track.seated
        if state is None or state.state is not True or state.since_mono is None:
            return None
        return state.since_mono if state.since_mono <= frame.capture_mono else None

    def observe(self, event, snapshot):
        """Update eligibility, then finalize replies against this same snapshot on the perception loop."""
        now = self.clock()
        frame, track = snapshot.frame, self._track(snapshot, now)
        with self.lock:
            values, level, occupancy, why = self._gate()
            scope = None if track is None else (frame.key[:2], track.track_id, track.association_revision,
                                                self.gate_revision)
            if (scope != self.support or
                    self.pool and frame is not None and frame.capture_mono - self.pool[-1].capture_mono >= PERCEPTION_S):
                self.pool.clear()
            self.support = scope
            seated = False
            if track is not None:
                # Contract 2's seated, as perception published it for this track on this frame. The pool keeps
                # the supported frames; _prepare takes stills only from the seated episode, since_mono on.
                seated = self._seated_since(track, frame) is not None
                if not self.pool or frame.key != self.pool[-1].key:
                    self.pool.append(frame)
                    while frame.capture_mono - self.pool[0].capture_mono > POOL_S:
                        self.pool.popleft()
            if why is None:
                if snapshot.status != "fresh" or frame is None or frame.mode != "rgb":
                    why = snapshot.reason or "no fresh RGB frame"
                elif not 0 <= now - frame.capture_mono < PERCEPTION_S:
                    why = "no fresh RGB frame"
                elif track is None or not seated:
                    why = "nobody seated" if track is not None or not snapshot.tracks else "no single supported subject"
            eligible = None if why or not seated else scope
            if eligible != self.eligibility:
                self.eligibility = eligible
                self.eligibility_revision += 1
                self.reply = self.activity = self.prior = None
            self.snapshot = replace(snapshot, eligible_track_id=None if eligible is None else track.track_id,
                                    eligibility_revision=self.eligibility_revision)
            if self.completed is not None:
                self._accept_completion(now)
            pending = self.inflight
            if pending is not None and pending["stage"] == "stills":
                if now - pending["decided_mono"] >= STILLS_TIMEOUT_S:
                    self._fail_stills(now, pending, "stills_timeout")
                elif not self._usable(now, pending):
                    self._done(now, pending)
                    self._record(pending, outcome="not_sent", why="the evidence changed")
            may = (eligible is not None and self.inflight is None and now >= self.next_at and now >= self.backoff_until
                   and self._paced(now, values, level))
            request = self._prepare(now, track, occupancy, level) if may else None
            event = self._decorate(event, now, eligible is not None, why, level)
        if request is not None:
            self._request_stills(request)
        return event

    def pop_reply(self):
        """The coordinator consumes the reply after it receives snapshot from the same observe() turn."""
        with self.lock:
            reply, self.reply = self.reply, None
            return reply

    def _paced(self, now, values, level):
        self._last_minute(now)
        interval = max(values["min_interval_s"], SLOW_INTERVAL_S if (level or 0) >= 1 else 0)
        return (len(self.sent) < values["max_per_minute"]
                and (not self.sent or now - self.sent[-1] >= interval))

    def _last_minute(self, now):
        while self.sent and now - self.sent[0] >= 60.0:
            self.sent.popleft()
        return len(self.sent)

    def _prepare(self, now, track, occupancy, level):
        seated_since = track.seated.since_mono   # eligible, so seated: the episode the stills must lie in
        start = max(track.association_since_mono, seated_since)
        try:
            frames = tuple(spread([frame for frame in self.pool if frame.capture_mono >= start], now))
        except FramesUnavailable:
            self.counts["frames_unavailable"] += 1
            return None
        self.ask_seq += 1
        first, latest = frames[0], frames[-1]
        evidence = {"safe": True, "status": "confident",
                    "since_at": latest.capture_ts + (track.association_since_mono - latest.capture_mono),
                    "window_start_mono": first.capture_mono, "window_end_mono": latest.capture_mono,
                    "verified_samples": sum(frame.capture_mono >= first.capture_mono for frame in self.pool)}
        request = {
            "ask_id": self.ask_seq, "stage": "stills", "decided_mono": now, "decided_at": self.wall(),
            "request_id": hashlib.sha256(f"{self.session_id}:{self.ask_seq}".encode()).hexdigest(),
            "frames": frames, "keys": [list(frame.key) for frame in frames], "track": track,
            "association": evidence, "seated_since_mono": seated_since,
            "eligibility_revision": self.eligibility_revision, "gate_revision": self.gate_revision,
            "occupancy": occupancy, "backoff_level": level, "valid_until_mono": latest.capture_mono + INFERENCE_S,
        }
        self.inflight = request
        return request

    def _request_stills(self, request):
        try:
            admitted = self.detector.send_alone({"kind": "stills", "id": request["ask_id"], "frames": request["keys"]})
        except Exception:
            admitted = False
        if admitted is False:
            with self.lock:
                self._fail_stills(self.clock(), request, "stills_worker_busy")

    def _usable(self, now, ask):
        snap = self.snapshot
        return (not self.halt.is_set() and snap is not None and snap.frame is not None
                and snap.eligible_track_id == ask["track"].track_id
                and snap.eligibility_revision == ask["eligibility_revision"]
                and snap.frame.key[:2] == ask["frames"][-1].key[:2]
                and ask["gate_revision"] == self.gate_revision
                and 0 <= now - snap.frame.capture_mono < PERCEPTION_S
                and now < ask["valid_until_mono"])

    def _decorate(self, event, now, open_, why, level):
        failing = ("Qwen isn't answering" if self.failed_in_row >= UNAVAILABLE_AFTER else
                   "the pictures aren't being made" if self.stills_failed_in_row >= UNAVAILABLE_AFTER else None)
        health = {"state": ("unavailable" if failing else "asking") if open_ else "off",
                  "why": failing if open_ and failing else why, "last_reply_at": self.last_reply_at,
                  "reply_s_median": round(statistics.median(self.reply_s), 3) if self.reply_s else None,
                  "asks_per_min": self._last_minute(now), "backoff_level": level}
        event.setdefault("health", {})["qwen"] = health
        if self.activity is not None:
            track_id, expiry, value = self.activity
            if now >= expiry:
                self.activity = None
            else:
                for track in event.get("tracks") or []:
                    if track.get("track_id") == track_id:
                        track["activity"] = dict(value)
        return event

    # -- Detector reply and sender threads --

    def _stills(self, reply):
        with self.lock:
            ask = self.inflight
            if ask is None or ask["stage"] != "stills":
                return
            now = self.clock()
            if reply is None:
                self._fail_stills(now, ask, "stills_worker_stopped")
                return
            if reply.get("stills_id") != ask["ask_id"]:
                return
            if now - ask["decided_mono"] >= STILLS_TIMEOUT_S:
                self._fail_stills(now, ask, "stills_timeout")
                return
            jpegs = reply.get("jpegs")
            if (reply.get("frames") != ask["keys"] or not isinstance(jpegs, list)
                    or len(jpegs) != len(ask["frames"]) or "error" in reply):
                self._fail_stills(now, ask, "stills_frame_mismatch")
                return
            ask.update(stage="send", stills_mono=now)
            if not self._enqueue(self.send, ask, jpegs):
                self._fail_stills(now, ask, "sender_busy")

    def _enqueue(self, function, *args, **kwargs):
        try:
            self.jobs.put_nowait((function, args, kwargs))
        except queue.Full:
            return False
        return True

    def _send_loop(self):
        while not self.halt.is_set():
            try:
                function, args, kwargs = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                function(*args, **kwargs)
            except (OSError, ValueError):
                self.counts["log_failed"] += 1

    def send(self, ask, jpegs):
        try:
            self._send(ask, jpegs)
        except Exception as exc:
            self._failed_post(ask, type(exc).__name__)

    def _send(self, ask, jpegs):
        frames = ask["frames"]
        latest = frames[-1]
        with self.lock:
            values, _, _, _ = self._gate()
            prior = (self.prior[0] if self.prior is not None and self.clock() < self.prior[1]
                     and self._usable(self.clock(), ask) else None)
        near, revision = None, None
        if values["labels_to_qwen"] and self.near_line is not None:
            try:
                track = ask["track"]
                near, revision = self.near_line({"track_id": track.track_id, "xy_m": track.xy_m, "near": track.near})
            except Exception:
                near, revision = None, None
        label = local_label(latest.capture_ts, self.zone)
        try:
            context = scene.model_context(label, prior, near)
        except ValueError:
            near, revision = None, None
            context = scene.model_context(label, prior)
        try:
            images = [base64.b64decode(image, validate=True) for image in jpegs]
            body = scene.body_bytes(scene.request_body(context, images, [frame.capture_mono for frame in frames]))
        except (ValueError, TypeError):
            with self.lock:
                self._fail_stills(self.clock(), ask, "invalid_jpeg")
            return
        with self.lock:
            values, level, occupancy, why = self._gate()
            now = self.clock()
            if self.inflight is not ask:
                return
            if why is not None or not self._usable(now, ask) or not self._paced(now, values, level):
                self._done(now, ask)
                self._record(ask, outcome="not_sent", why="the gate or evidence changed before sending")
                return
            ask.update(request_body_sha256=hashlib.sha256(body).hexdigest(),
                       labels={"labels_to_qwen": values["labels_to_qwen"], "near_person": near, "labels_revision": revision},
                       settings={key: values[key] for key in ("asks_on", "min_interval_s", "max_per_minute")},
                       occupancy=occupancy, backoff_level=level, stage="posted", sent_mono=now, sent_at=self.wall())
            self.sent.append(now)
            self.counts["asked"] += 1
        response = self.post(body)
        answered_mono, answered_at = self.clock(), self.wall()
        content, finish = scene.reply_from_proof(response)
        # Project evidence times from a single wall anchor so an NTP step cannot reorder the stills.
        offset = latest.capture_ts - latest.capture_mono
        line, facts, reason = scene.fact_line(
            request_id=ask["request_id"], session_id=self.session_id,
            frames=[{"media_id": "den-live:%s:%d:%d" % frame.key, "frame_id": str(frame.frame_id),
                     "capture_epoch": "%s:%d" % frame.key[:2], "observed_at": frame.capture_mono + offset}
                    for frame in frames],
            sent_at=ask["sent_mono"] + offset, available_at=answered_mono + offset,
            context=context, body_sha256=ask["request_body_sha256"], content=content, finish_reason=finish,
            prior_supplied=prior is not None, site_id=self.site_id, camera_id=self.camera_id)
        usage = response.get("usage") if isinstance(response, dict) else None
        tokens = ({key: usage.get(key) if type(usage.get(key)) is int else None
                   for key in ("prompt_tokens", "completion_tokens")} if isinstance(usage, dict) else None)
        model = response.get("model") if isinstance(response, dict) and isinstance(response.get("model"), str) else None
        with self.lock:
            if self.inflight is ask:
                self.completed = (ask, line["observation"], facts, reason, model, tokens, answered_mono, answered_at)

    def _failed_post(self, ask, reason):
        with self.lock:
            if self.inflight is ask:
                now = self.clock()
                self.counts["failed"] += 1
                self._done(now, ask, failed=True)
                self._record(ask, outcome="failed", why=reason, answered_at=self.wall(), answered_mono=now)

    def _accept_completion(self, now):
        ask, observation, facts, reason, model, tokens, answered_mono, answered_at = self.completed
        self.completed = None
        self.counts["answered" if facts else "invalid"] += 1
        self.reply_s.append(answered_mono - ask["sent_mono"])
        self.last_reply_at = round(answered_at, 3)
        outcome = "completed"
        if not self._usable(now, ask):
            self.counts["unbound"] += 1
            outcome = "unbound"
            reason = "evidence_expired" if now >= ask["valid_until_mono"] else "evidence_changed"
        elif facts:
            latest, track = ask["frames"][-1], ask["track"]
            self.reply = InferenceReply(
                request_id=ask["request_id"], request_body_sha256=ask["request_body_sha256"],
                service_run_id=latest.service_run_id, camera_epoch=latest.camera_epoch, track_id=track.track_id,
                association_revision=track.association_revision, eligibility_revision=ask["eligibility_revision"],
                frames=ask["frames"], observation=observation, association=ask["association"],
                gate={"frigate_raw": ask["occupancy"]["raw"], "frigate_effective": ask["occupancy"]["effective"],
                      "read_age_ms": ask["occupancy"]["read_age_ms"], "seated_track_id": track.track_id,
                      "seated_since_mono": ask["seated_since_mono"]},
                model={"served_alias": model, "artifact_id": observation["artifact_sha256"],
                       "prompt_version": scene.PROMPT_VERSION},
                observed_mono=latest.capture_mono, available_mono=answered_mono,
                valid_until_mono=ask["valid_until_mono"],
                timings_ms={"estimated_capture_to_arrival": round((latest.arrival_mono - latest.capture_mono) * 1000, 1),
                            "capture_to_decision": round((ask["decided_mono"] - latest.capture_mono) * 1000, 1),
                            "stills": round((ask["stills_mono"] - ask["decided_mono"]) * 1000, 1),
                            "dispatch": round((ask["sent_mono"] - ask["stills_mono"]) * 1000, 1),
                            "model_http": round((answered_mono - ask["sent_mono"]) * 1000, 1),
                            "reply_to_handoff": round((now - answered_mono) * 1000, 1)})
            if facts["people_visible"] == "one":
                self.prior = facts, ask["valid_until_mono"]
                self.activity = (track.track_id, ask["valid_until_mono"],
                                 {"word": facts["activity_hypothesis"], "close_up": scene.close_up(facts),
                                  "at": round(answered_at, 3)})
            else:
                # The coordinator needs the current negative observation to revoke support.
                self.counts["unbound"] += 1
                outcome, reason = "unbound", "model_subject_unbound"
                self.activity = self.prior = None
        self._done(now, ask)
        self._record(ask, outcome=outcome, reason=reason, facts=facts, model=model, usage=tokens,
                     answered_mono=answered_mono, answered_at=answered_at)

    def _fail_stills(self, now, ask, reason):
        if self.inflight is ask:
            self.counts["stills_failed"] += 1
            self.stills_failed_in_row += 1
            self._done(now, ask)
            self._record(ask, outcome="stills_failed", why=reason)

    def _done(self, now, ask, *, failed=False):
        """With self.lock held, finish only the matching request."""
        if self.inflight is not ask:
            return
        interval = max(self.settings.get()["min_interval_s"],
                       SLOW_INTERVAL_S if (self.backoff.level() or 0) >= 1 else 0)
        self.inflight = None
        self.next_at = ask.get("sent_mono", now) + interval
        if failed:
            self.failed_in_row += 1
            self.backoff_until = now + BACKOFF_S
        elif "sent_mono" in ask:
            self.failed_in_row = self.stills_failed_in_row = 0

    def _record(self, ask, **fields):
        if not self._enqueue(self._log, ask, **fields):
            self.counts["log_failed"] += 1

    def _log(self, ask, *, outcome, why=None, answered_at=None, answered_mono=None, facts=None, reason=None,
             model=None, usage=None):
        frames = ask["frames"]
        latest, sent_mono = frames[-1], ask.get("sent_mono")
        line = {"schema": ASK_SCHEMA, "ask_id": ask["ask_id"], "session_id": self.session_id,
                "request_id": ask["request_id"], "request_body_sha256": ask.get("request_body_sha256"),
                "prompt_version": scene.PROMPT_VERSION, "outcome": outcome, "why": why,
                "gate": {"occupancy": ask["occupancy"]["effective"], "seated": [ask["track"].track_id],
                         "people": 1, "backoff_level": ask["backoff_level"]},
                "association": ask["association"], "settings": ask.get("settings"), "labels": ask.get("labels"),
                "stills": [{"frame_key": frame.key, "captured_at": frame.capture_ts,
                            "arrived_at": frame.capture_ts + (frame.arrival_mono - frame.capture_mono)} for frame in frames],
                "decided_at": ask["decided_at"], "sent_at": ask.get("sent_at"), "answered_at": answered_at,
                "reply_s": round(answered_mono - sent_mono, 3) if answered_mono is not None and sent_mono is not None else None,
                "picture_to_answer_s": round(answered_mono - latest.capture_mono, 3) if answered_mono is not None else None,
                "model": model, "usage": usage, "status": None if outcome != "completed" else ("valid" if facts else "invalid"),
                "invalid_reason": reason, "facts": facts}
        try:
            self.log.write(line, answered_at or self.wall())
        except (OSError, ValueError):
            self.counts["log_failed"] += 1

    def close(self):
        self.halt.set()
        if self.detector.on_stills == self._stills:
            self.detector.on_stills = None

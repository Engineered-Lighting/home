"""den-live's Qwen ask (inference plan, step 2). Standard library, in the loop's own process.

When it asks (the owner, 2026-11-12: Qwen only once Frigate has noticed someone):
- Frigate's occupancy reads on (occupancy.py), a confirmed track is seated (seated.py), the settings say asks_on, and
  the back-off monitor is below its step 2.
- One ask in flight at a time; the next no sooner than min_interval_s after the last was sent (2 s; at least 4 s at the
  monitor's step 1); at most max_per_minute in any 60 s; BACKOFF_S after a failure.
What it sends: 3 stills from the last 3 s, picked by spread() (perception-lab qwen_live.spread at den-core-live's pin,
copied), made into JPEGs in the detector's process (stills.py), in den_live_scene's den-live-scene-2 body. The context is
the local time, Qwen's last valid answer when it's under 10 s old, and near_person: the labeling plan's line, only while
its switch is on and exactly one person is tracked.
What it keeps: one words-only line per ask in <state>/days/<UTC date>/asks.jsonl (no image, no reply text), for
RETENTION_DAYS. Every time the chain has is in it: each still's capture and arrival, decided, sent and answered.
What it gives: each on_reply hook gets {"fact": the la-fact-log-1 line, "ask": the ask's line}; the stream's events get
health.qwen and, for each track asked about, activity {word, close_up, at}.
"""
import hashlib
import json
import queue
import shutil
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import den_live_scene as scene
import seated as seated_rule

ASK_SCHEMA = "den-live-ask-1"
SETTINGS_KEYS = {"asks_on": True, "labels_to_qwen": True, "min_interval_s": 2.0, "max_per_minute": 30}
SLOW_INTERVAL_S = 4.0     # the back-off monitor's step 1
BACKOFF_S = 30.0          # after a failed ask
STILLS_TIMEOUT_S = 2.0
POST_TIMEOUT_S = 20.0
POOL_S = 5.0              # frames the pool keeps; the detector's process keeps about as many (stills.RING)
ACTIVITY_S = 30.0         # an answer shows on its track's marker this long
UNAVAILABLE_AFTER = 3     # failed asks in a row before the health line says Qwen isn't answering
RETENTION_DAYS = 14
BACKOFF_STALE_S = 600.0   # the monitor writes its status each minute: older than this, it isn't running
PRIOR_MAX_S = scene.MAX_AGE_SECONDS


class FramesUnavailable(Exception):
    pass


def spread(pool, now, *, count=3, span=3.0):
    """perception-lab qwen_live.spread at den-core-live's pin, unchanged: the newest candidate, then for each earlier slot
    the same-epoch candidate nearest latest - k*span/(count-1) (within half a step, more than 0.2 s before the previous
    choice), oldest first."""
    if not pool:
        raise FramesUnavailable("frames_unavailable")
    latest = max(pool, key=lambda f: (f["observed_at"], f["media_id"]))
    if now >= latest["valid_until"]:
        raise FramesUnavailable("frames_stale")
    chosen, step = [latest], span / (count - 1)
    for index in range(1, count):
        target = latest["observed_at"] - step * index
        options = [f for f in pool if f["capture_epoch"] == latest["capture_epoch"]
                   and f["observed_at"] < chosen[-1]["observed_at"] - .2 and abs(f["observed_at"] - target) <= step / 2]
        if options:
            chosen.append(min(options, key=lambda f: (abs(f["observed_at"] - target), f["media_id"])))
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
        """near_line(track) -> (line or None, labels revision or None): the labeling plan's room_labels.line_for, bound to
        the current labels; None until the labels exist."""
        self.detector, self.occupancy, self.post = detector, occupancy, post
        self.settings, self.backoff = settings or Settings(), backoff or Backoff()
        self.near_line, self.zone, self.clock, self.wall = near_line, zone, clock, wall
        self.site_id, self.camera_id = site_id, camera_id
        self.log = AskLog(state)
        self.on_reply = []
        self.lock = threading.Lock()
        self.seated = seated_rule.Seated()
        self.association = {}    # track id -> deque of (capture_mono, den-track's status, since on the wall clock)
        self.pool = deque()
        self.inflight = None
        self.next_at = self.backoff_until = 0.0
        self.sent = deque()
        self.reply_s = deque(maxlen=20)
        self.last_reply_at = None
        self.failed_in_row = self.stills_failed_in_row = 0
        self.activity = {}
        self.prior = None
        self.ask_seq = 0
        self.last_mono = None    # the newest frame's capture time, on the loop's monotonic clock
        started = wall()
        self.session_id = hashlib.sha256(("den-live|%r" % started).encode()).hexdigest()[:32]
        self.counts = {"asked": 0, "answered": 0, "invalid": 0, "failed": 0, "stills_failed": 0, "frames_unavailable": 0}
        self.jobs = queue.Queue()
        detector.on_stills = self._stills
        self.halt = threading.Event()
        self.sender = threading.Thread(target=self._send_loop, daemon=True, name="den-live-ask")
        if start:
            self.sender.start()

    # -- in the loop's thread --

    def gate(self, seated_ids):
        """(open, why): whether an ask may go out now, and the health line's reason when it may not."""
        values = self.settings.get()
        level = self.backoff.level()
        if not values["asks_on"]:
            return False, "switched off" + (": %s" % self.settings.why if self.settings.why else "")
        if level is not None and level >= 2:
            return False, "paused by the back-off monitor"
        word, why = self.occupancy.state()
        if word != "on":
            return False, why
        if not seated_ids:
            return False, "nobody seated"
        return True, None

    def observe(self, event, frame):
        now = self.clock()
        confirmed = [t for t in event.get("tracks") or [] if t.get("state") == "confirmed"]
        if frame is not None:
            mono = frame["capture_mono"]
            self.pool.append({"media_id": "den-live:%s:%d" % (frame["session"], frame["id"]), "frame_id": str(frame["id"]),
                              "capture_epoch": str(frame["session"]), "observed_at": frame["capture_ts"],
                              "valid_until": frame["capture_ts"] + scene.MAX_AGE_SECONDS, "id": frame["id"],
                              "arrival_unix": frame["arrival_unix"], "capture_mono": mono})
            while self.pool and frame["capture_ts"] - self.pool[0]["observed_at"] > POOL_S:
                self.pool.popleft()
            for track in confirmed:
                self.seated.update(track["track_id"], mono, frame["poses"].get(track["track_id"]))
            offset = frame["capture_ts"] - mono   # this frame's wall time less its monotonic time
            for track_id, (status, since) in (frame.get("association") or {}).items():
                history = self.association.setdefault(track_id, deque())
                history.append((mono, status, None if since is None else since + offset))
                while history and mono - history[0][0] > POOL_S + 1.0:
                    history.popleft()
            for gone in [k for k, h in self.association.items() if not h or mono - h[-1][0] > POOL_S + 1.0]:
                del self.association[gone]
            self.last_mono = mono
        at = self.last_mono
        seated_ids = [t["track_id"] for t in confirmed if at is not None and self.seated.seated(t["track_id"], at)]
        open_, why = self.gate(seated_ids)
        with self.lock:
            if self.inflight is not None and self.inflight["stage"] == "stills" and now - self.inflight["mono"] > STILLS_TIMEOUT_S:
                self._stills_failed(now, self.inflight)
            may = (open_ and self.inflight is None and now >= self.next_at and now >= self.backoff_until
                   and self._last_minute(now) < self.settings.get()["max_per_minute"])
        if may:
            self._start(now, event, confirmed, seated_ids)
        return self._decorate(event, open_, why)

    def _last_minute(self, now):
        while self.sent and now - self.sent[0] > 60.0:
            self.sent.popleft()
        return len(self.sent)

    def _start(self, now, event, confirmed, seated_ids):
        try:
            chosen = spread(list(self.pool), self.wall())
        except FramesUnavailable:
            self.counts["frames_unavailable"] += 1
            return
        self.ask_seq += 1
        track = next(t for t in confirmed if t["track_id"] == seated_ids[0])
        ask = {"ask_id": self.ask_seq, "stage": "stills", "mono": now, "decided_at": self.wall(),
               "frames": [{k: c[k] for k in ("media_id", "frame_id", "capture_epoch", "observed_at", "arrival_unix")} for c in chosen],
               "tracks": list(seated_ids), "people": len(confirmed), "track": dict(track),
               "association": self._association(track["track_id"], chosen[0], chosen[-1]),
               "occupancy": self.occupancy.state()[0], "backoff_level": self.backoff.level()}
        with self.lock:
            self.inflight = ask
        try:
            self.detector.send_alone({"kind": "stills", "id": ask["ask_id"], "frames": [c["id"] for c in chosen]})
        except Exception:
            with self.lock:
                self._stills_failed(now, ask)

    def _association(self, track_id, first, last):
        """The asked track's association over the stills' window (first and last: the oldest and newest still), for
        person state's evidence (LIVE L10): safe when den-track held it confident on every frame of the window, and
        since before the window began; plus its status and since-time (wall clock) at the window's end."""
        window = [h for h in self.association.get(track_id) or () if first["capture_mono"] <= h[0] <= last["capture_mono"]]
        if not window:
            return {"safe": False, "status": None, "since_at": None}
        _, status, since_at = window[-1]
        safe = (all(s == "confident" for _, s, _ in window) and since_at is not None
                and since_at <= first["observed_at"])
        return {"safe": safe, "status": status, "since_at": None if since_at is None else round(since_at, 3)}

    def _decorate(self, event, open_, why):
        now_wall = self.wall()
        with self.lock:
            failing = ("Qwen isn't answering" if self.failed_in_row >= UNAVAILABLE_AFTER else
                       "the pictures aren't being made" if self.stills_failed_in_row >= UNAVAILABLE_AFTER else None)
            health = {"state": ("unavailable" if failing else "asking") if open_ else "off",
                      "why": failing if open_ and failing else why,
                      "last_reply_at": self.last_reply_at,
                      "reply_s_median": round(statistics.median(self.reply_s), 3) if self.reply_s else None,
                      "asks_per_min": self._last_minute(self.clock()), "backoff_level": self.backoff.level()}
            activity = {k: v for k, v in self.activity.items() if now_wall - v["at"] <= ACTIVITY_S}
            self.activity = activity
        event.setdefault("health", {})["qwen"] = health
        for track in event.get("tracks") or []:
            if track.get("track_id") in activity:
                track["activity"] = dict(activity[track["track_id"]])
        return event

    # -- in the detector's reader thread --

    def _stills(self, reply):
        now = self.clock()
        with self.lock:
            ask = self.inflight
            if reply is None:   # the detector's process stopped
                if ask is not None and ask["stage"] == "stills":
                    self._stills_failed(now, ask)
                return
            if ask is None or ask["stage"] != "stills" or reply.get("stills_id") != ask["ask_id"]:
                return
            if "error" in reply or not reply.get("jpegs"):
                self._stills_failed(now, ask)
                return
            ask["stage"] = "send"
        self.jobs.put((ask, reply["jpegs"]))

    # -- in the sender's thread --

    def _send_loop(self):
        while not self.halt.is_set():
            try:
                ask, jpegs = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.send(ask, jpegs)
            except Exception as exc:  # a type name only. _done acts only if this ask is still the one in flight
                with self.lock:
                    if self.inflight is ask:
                        self.counts["failed"] += 1
                        self._done(self.clock(), ask, failed=True)
                try:
                    self._log(ask, outcome="failed", why=type(exc).__name__)
                except Exception:
                    pass

    def send(self, ask, jpegs):
        import base64
        values = self.settings.get()
        # The gate again, just before posting: a switch, the monitor or Frigate may have changed since the decision.
        level = self.backoff.level()
        if not values["asks_on"] or (level is not None and level >= 2) or self.occupancy.state()[0] != "on":
            with self.lock:
                self._done(self.clock(), ask, failed=False)
            self._log(ask, outcome="not_sent", why="the gate closed before sending")
            return
        images = [base64.b64decode(j) for j in jpegs]
        frames = [{k: f[k] for k in ("media_id", "frame_id", "capture_epoch", "observed_at")} for f in ask["frames"]]
        observed = [f["observed_at"] for f in frames]
        with self.lock:
            prior = self.prior[0] if self.prior and observed[-1] - self.prior[1] <= PRIOR_MAX_S else None
        near, revision = None, None
        if values["labels_to_qwen"] and self.near_line is not None and ask["people"] == 1:
            try:
                near, revision = self.near_line(ask["track"])
            except Exception:
                near, revision = None, None
        label = local_label(observed[-1], self.zone)
        try:
            context = scene.model_context(label, prior, near)
        except ValueError:   # a label line that isn't one short line goes unsent; the ask doesn't
            near, revision = None, None
            context = scene.model_context(label, prior, None)
        try:
            body = scene.body_bytes(scene.request_body(context, images, observed))
        except ValueError as exc:   # the stills, not Qwen: no failure count and no back-off
            with self.lock:
                self._stills_failed(self.clock(), ask)
            self._log(ask, outcome="stills_failed", why=str(exc)[:80])
            return
        body_sha = hashlib.sha256(body).hexdigest()
        ask.update(request_id=body_sha, labels={"labels_to_qwen": values["labels_to_qwen"], "near_person": near,
                                                 "labels_revision": revision},
                   settings={k: values[k] for k in ("asks_on", "min_interval_s", "max_per_minute")})
        sent_wall, sent_mono = self.wall(), self.clock()
        with self.lock:
            self.sent.append(sent_mono)
            self.counts["asked"] += 1
        try:
            response = self.post(body)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            answered = self.wall()
            with self.lock:
                self.counts["failed"] += 1
                self._done(self.clock(), ask, failed=True, sent_mono=sent_mono, interval=values["min_interval_s"])
            self._log(ask, outcome="failed", why=type(exc).__name__, sent_at=sent_wall, answered_at=answered)
            return
        answered = self.wall()
        content, finish = scene.reply_from_proof(response)
        line, facts, reason = scene.fact_line(
            request_id=body_sha, session_id=self.session_id, frames=frames, sent_at=sent_wall, available_at=answered,
            context=context, body_sha256=body_sha, content=content, finish_reason=finish, prior_supplied=prior is not None,
            site_id=self.site_id, camera_id=self.camera_id)
        usage = response.get("usage") if isinstance(response, dict) else None
        tokens = {k: usage.get(k) if type(usage.get(k)) is int else None for k in ("prompt_tokens", "completion_tokens")} \
            if isinstance(usage, dict) else None
        model = response.get("model") if isinstance(response, dict) and isinstance(response.get("model"), str) else None
        word = facts["activity_hypothesis"] if facts else "unclear"
        with self.lock:
            self.counts["answered" if facts else "invalid"] += 1
            self.reply_s.append(answered - sent_wall)
            self.last_reply_at = round(answered, 3)
            # The marker shows an answer only when den-track was sure whose it was through the stills' window.
            if ask["association"]["safe"]:
                for track_id in ask["tracks"]:
                    self.activity[track_id] = {"word": word, "close_up": scene.close_up(facts), "at": round(answered, 3)}
            if facts:
                self.prior = (facts, observed[-1])
            self._done(self.clock(), ask, failed=False, sent_mono=sent_mono, interval=values["min_interval_s"])
        try:
            record = self._log(ask, outcome="completed", sent_at=sent_wall, answered_at=answered, facts=facts,
                               reason=reason, model=model, usage=tokens)
        except Exception:   # a line that doesn't log still goes to the hooks
            record = None
        for hook in list(self.on_reply):
            try:
                hook({"fact": line, "ask": record})
            except Exception:   # a consumer's failure is its own
                pass

    def _stills_failed(self, now, ask):
        """With self.lock held: the stills for this ask didn't come. Not Qwen's failure: no back-off."""
        if self.inflight is ask:
            self.counts["stills_failed"] += 1
            self.stills_failed_in_row += 1
            self._done(now, ask, failed=False)

    def _done(self, now, ask, *, failed, sent_mono=None, interval=None):
        """With self.lock held: this ask is over. It acts only while the ask is the one in flight, so an ask never ends
        twice and never ends the next one."""
        if self.inflight is not ask:
            return
        interval = max(interval or self.settings.get()["min_interval_s"], SLOW_INTERVAL_S if (self.backoff.level() or 0) >= 1 else 0)
        self.inflight = None
        self.next_at = (sent_mono if sent_mono is not None else now) + interval
        if failed:
            self.failed_in_row += 1
            self.backoff_until = now + BACKOFF_S
        elif sent_mono is not None:
            self.failed_in_row = self.stills_failed_in_row = 0

    def _log(self, ask, *, outcome, why=None, sent_at=None, answered_at=None, facts=None, reason=None, model=None, usage=None):
        frames = ask["frames"]
        newest = frames[-1]["observed_at"]
        line = {"schema": ASK_SCHEMA, "ask_id": ask["ask_id"], "session_id": self.session_id,
                "request_id": ask.get("request_id"), "prompt_version": scene.PROMPT_VERSION, "outcome": outcome, "why": why,
                "gate": {"occupancy": ask["occupancy"], "seated": ask["tracks"], "people": ask["people"],
                         "backoff_level": ask["backoff_level"]},
                "association": ask["association"],
                "settings": ask.get("settings"), "labels": ask.get("labels"),
                "stills": [{"captured_at": f["observed_at"], "arrived_at": f["arrival_unix"]} for f in frames],
                "decided_at": ask["decided_at"], "sent_at": sent_at, "answered_at": answered_at,
                "reply_s": round(answered_at - sent_at, 3) if sent_at and answered_at else None,
                "picture_to_answer_s": round(answered_at - newest, 3) if answered_at else None,
                "model": model, "usage": usage, "status": None if outcome != "completed" else ("valid" if facts else "invalid"),
                "invalid_reason": reason, "facts": facts}
        try:
            self.log.write(line, answered_at or self.wall())
        except (OSError, ValueError):   # a full disk, or a value JSON won't write (a NaN): the line still goes to the hooks
            self.counts["log_failed"] = self.counts.get("log_failed", 0) + 1
        return line

    def close(self):
        self.halt.set()
        if self.detector.on_stills == self._stills:
            self.detector.on_stills = None

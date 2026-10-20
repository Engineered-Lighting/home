"""Recording-owned continuous Qwen perceiver pump (Victoria side).

Wired by service_runtime (one pump per recording, from the live config's
'qwen_live' block via live_detector_app.qwen_live_factory); scene_context feeds
the shared qwen_scene record to JEV. Nothing here starts on import, and a pump
sends nothing until start().

Frame source: the registered RGB preview frames the controller already stores
for the recording (PreviewPump -> PreviewIngestor: the camera owner's
commissioned 640x360 /frame.jpg, <=256 KiB, clock-mapped, sha256-bound in the
media table, ~2 Hz). The pump reads those stored bytes back, verifies size,
digest and decode, and sends them unchanged. It never opens the camera, never
re-encodes and never makes a second capture, so Qwen sees exactly the frames the
specialists saw and its evidence roots name them (same_camera_visual).

Transport: qwen_transport.QwenTransport (one kept-alive connection), subclassed
only for the live consumer's paths, its typed pre-admission refusals and its
reconcile outcome.

Admission discipline: one request in flight (the pump is single-threaded and
never submits while a prior outcome is unproven); latest frame wins, and only a
new frame is sent; interval 0.5-10 s (default 4 s) and a cap of 1-120
submissions per rolling minute (default 15), both config; typed skips on
refusal, with a 30 s backoff after admission_closed or thermal_shed; a halt for
the rest of the session if the LA ever reports an indeterminate row (the
recording pump's owner-reviewed semantics, unchanged); an operator kill switch;
clean stop with the recording.

Occupancy (owner requirement): a pump cannot be built without an occupied()
gate (occupancy_gate.OccupancyGate over the recording's camera-derived Frigate
person-occupancy HA rows). Every new dispatch asks it first; anything but
'occupied' skips with no_person / occupancy_unknown before the engine-epoch
probe or any frame read, so nothing reaches the LA. Reconciling an already
sent request by identity is not a new dispatch and is never gated. The gate
only gates: it adds no evidence and refreshes no freshness. With the in-memory
occupancy_reader gate, an off/unknown -> on edge wakes a vacant pump at once
(its sleep waits on "stopped or wake"); the tick still checks occupancy before
the engine-epoch probe, so nothing reaches the LA while the room is vacant.

Always-on reuse (speed plan S1.3, la_live_perceiver): the same pump also runs
outside recordings with injected frames (frame_ring.FrameRing, which wakes a
pump waiting for frames when a new one arrives), its own active/enabled checks,
a ledger with terminal() (la_fact_log.FactLog), which receives every LA
terminal outcome with its request (send time, frames), and an
indeterminate_seconds interval: for that pump only (plan S1.3(c)), an
indeterminate row pauses instead of halting. The request stays outstanding
(never resubmitted) and is looked up by its identity at most once per
interval, whatever the lookup returns, until LA's reconcile (plan S1.8) ends it
as unknown_resolved, with no fact, or another terminal proof arrives; then the
pump resumes. The recording pump's defaults are unchanged.
"""
import hashlib
import io
import json
import threading
import time
from collections import deque

import occupancy_gate
import qwen_scene as scene
from qwen_transport import STATES, TERMINAL_STATES, CompletionUnproven, QwenNotExecuted, QwenTransport

SUBMIT_PATH = "/v1/lab/live-scene"
LOOKUP_PATH = "/v1/lab/live-scene/lookup"
# Exact refusals home_shared/lab_live_scene.py emits with no ledger write.
# thermal_shed (speed plan S1.4): LA's GPU or CPU is at the live-Qwen rung of
# its thermal ladder; refused before any reservation, like admission_closed.
PRE_EXECUTION = frozenset({(400, "invalid_request"), (400, "profile_mismatch"), (400, "prompt_version_unsupported"),
                           (400, "input_bound_exceeded"), (403, "consumer_not_registered"), (409, "deadline_insufficient"),
                           (411, "length_required"), (413, "body_too_large"), (429, "perceiver_busy"),
                           (429, "interactive_queued"), (429, "queue_busy"), (503, "admission_closed"),
                           (503, "thermal_shed")})
# Refusals after which the pump sends nothing for BACKOFF_SECONDS.
BACKOFF_REFUSALS = frozenset({"admission_closed", "thermal_shed"})
# LA's reconcile of an indeterminate live row (speed plan S1.8): terminal, no
# answer. Its proof is failed with this reason (the shared outcome mapping) or,
# if LA names it as a state, this state.
UNKNOWN_RESOLVED = "unknown_resolved"
PROFILE_FIELDS = frozenset({"model_version", "served_model", "artifact_sha256", "engine_epoch",
                            "runtime_revision", "adapter_revision"})
DEFAULTS = {"interval_seconds": 4.0, "frames": 3, "span_seconds": 3.0, "max_per_minute": 15,
            "horizon_seconds": 120, "prior_max_age_seconds": 20.0, "timezone": "America/Vancouver",
            # Off by default: live 2026-09-30 the model anchored on its own earlier
            # reading ("resting", "no_change") while listing head_toward_monitor_side;
            # change over time is computed in code from the stored history instead.
            "send_prior": False}
MAX_DEADLINE_SECONDS = 8
BACKOFF_SECONDS = 30
# The re-probe interval of a pump that pauses on an indeterminate LA row
# (indeterminate_seconds; None, the recording pump's default, halts instead).
INDETERMINATE_BOUNDS = (1, 600)
HALTED = "halted_la_indeterminate"
PAUSED = "la_indeterminate"
# Skips that only a new frame can end. With a frame source that reports new
# frames (subscribe), a pump skipping for one of these wakes at the next frame.
FRAME_WAIT_REASONS = frozenset({"frames_unavailable", "frames_insufficient", "frames_stale", "no_new_frame"})
EVENT_KINDS = ("qwen.observation", "qwen.rejected", "qwen.conflict")


class FramesUnavailable(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def pump_config(value=None):
    """Exact optional keys with bounds; absent keys take DEFAULTS."""
    value = dict(value or {})
    if set(value) - set(DEFAULTS):
        raise ValueError("unknown Qwen live setting")
    config = {**DEFAULTS, **value}
    # The 0.5 s floor matches the ~2 Hz preview grid; only a new frame is ever
    # sent, so below ~0.5 s the frames, not the interval, would set the rate.
    checks = (("interval_seconds", .5, 10), ("span_seconds", 1, 4), ("prior_max_age_seconds", 5, 30))
    for key, low, high in checks:
        if not scene.finite(config[key]) or not low <= config[key] <= high:
            raise ValueError(f"{key} outside its bound")
    if type(config["frames"]) is not int or not scene.MIN_IMAGES <= config["frames"] <= scene.MAX_IMAGES:
        raise ValueError("2-4 frames required")
    if type(config["max_per_minute"]) is not int or not 1 <= config["max_per_minute"] <= 120:
        raise ValueError("at most 120 perceiver requests per minute")
    if config["horizon_seconds"] not in scene.HORIZONS:
        raise ValueError("supported history horizon required")
    if type(config["send_prior"]) is not bool:
        raise ValueError("send_prior must be true or false")
    if not isinstance(config["timezone"], str) or not config["timezone"]:
        raise ValueError("IANA time zone required")
    return config


def zone_clock(name):
    """(hour, minute) at a unix time in a named zone; None when the zone cannot load."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)
    except Exception:
        return None

    def local(epoch):
        moment = datetime.fromtimestamp(epoch, tz)
        return moment.hour, moment.minute
    return local


class LiveSceneTransport(QwenTransport):
    """QwenTransport for the live consumer: its paths, typed refusals and
    reconcile outcome only."""
    STATES = STATES | {UNKNOWN_RESOLVED}
    TERMINAL_STATES = TERMINAL_STATES | {UNKNOWN_RESOLVED}

    def _proof(self, status, value, reference, *, submission):
        if submission and status in {s for s, _ in PRE_EXECUTION}:
            if isinstance(value, dict) and set(value) == {"reason"} and (status, value["reason"]) in PRE_EXECUTION:
                raise QwenNotExecuted(value["reason"])
            raise CompletionUnproven("analysis_completion_unproven")
        return super()._proof(status, value, reference, submission=submission)

    def submit(self, envelope, *, budget=10):
        body = self._check_envelope(envelope)
        status, value = self._post(SUBMIT_PATH, body, budget, submission=True)
        return self._proof(status, value, envelope, submission=True)

    def lookup(self, reference, *, budget=10):
        if not isinstance(reference, dict) or "body" in reference:
            raise CompletionUnproven("lookup_reference_invalid")
        body = scene.canonical(reference).encode()
        if len(body) > 16384:
            raise CompletionUnproven("lookup_reference_invalid")
        status, value = self._post(LOOKUP_PATH, body, budget, submission=False)
        return self._proof(status, value, reference, submission=False)


def spread(pool, now, *, count=3, span=3.0):
    """The frames a request uses, oldest first: the newest candidate, then for
    each earlier slot the same-epoch candidate nearest latest - k*span/(count-1)
    (within half a step, more than 0.2 s before the previous choice). Shared by
    the recording's stored previews and the always-on RAM ring, so both send
    the same kind of window. Raises FramesUnavailable (unavailable, stale,
    insufficient)."""
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


class RegisteredPreviewFrames:
    """Select 2-4 recent registered preview JPEGs spanning ~span seconds."""

    def __init__(self, store, *, scan_rows=48):
        self.store, self.scan_rows = store, scan_rows

    def candidates(self, session, now):
        with self.store.lock:
            if self.store.db.execute("SELECT 1 FROM media_deletions WHERE session_id=? AND state='pending'",
                                     (session["id"],)).fetchone():
                raise FramesUnavailable("media_deleted")
            rows = self.store.db.execute("""SELECT id,size,sha256,metadata FROM media WHERE session_id=?
                AND mime_type='image/jpeg' ORDER BY rowid DESC LIMIT ?""", (session["id"], self.scan_rows)).fetchall()
        result = []
        for row in rows:
            meta = json.loads(row["metadata"])
            if (meta.get("stream") != "rgb" or meta.get("clock_mapping_status") != "verified"
                    or meta.get("site_id") != session["site_id"] or meta.get("camera_id") != session["camera_id"]
                    or (meta.get("width"), meta.get("height")) != scene.IMAGE_SIZE
                    or not all(scene.finite(meta.get(k)) for k in ("observed_at", "available_at", "valid_until"))
                    or not isinstance(meta.get("frame_id"), str) or not isinstance(meta.get("capture_epoch"), str)
                    or meta["available_at"] > now or meta["observed_at"] > now
                    or not 0 < row["size"] <= scene.MAX_IMAGE_BYTES):
                continue
            result.append({"media_id": row["id"], "frame_id": meta["frame_id"], "capture_epoch": meta["capture_epoch"],
                           "observed_at": meta["observed_at"], "available_at": meta["available_at"],
                           "valid_until": meta["valid_until"], "size": row["size"], "sha256": row["sha256"]})
        return result

    def select(self, session, now, *, count=3, span=3.0):
        chosen = spread(self.candidates(session, now), now, count=count, span=span)
        return [dict(frame, data=self.read(session, frame)) for frame in chosen]

    def read(self, session, frame):
        from PIL import Image
        try:
            item = self.store.get_media(session["id"], frame["media_id"], generation=session["generation"])
            with self.store.media_path(session["id"], item["id"]).open("rb") as source:
                data = source.read(item["size"] + 1)
        except Exception:
            raise FramesUnavailable("frame_read_failed") from None
        if len(data) != frame["size"] or hashlib.sha256(data).hexdigest() != frame["sha256"]:
            raise FramesUnavailable("frame_integrity_failed")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if (image.format != "JPEG" or image.size != scene.IMAGE_SIZE or image.mode not in {"RGB", "L"}
                        or getattr(image, "n_frames", 1) != 1):
                    raise ValueError()
                image.load()
        except Exception:
            raise FramesUnavailable("frame_decode_failed") from None
        return data


class QwenSceneLedger:
    """Durable qwen.observation receipts; first receipt wins (caption_history rules)."""

    def __init__(self, store):
        self.store = store

    def record(self, sid, draft):
        with self.store.transaction() as db:
            session = self.store.get_session(sid)
            if (session["state"] != "recording" or draft.get("session_id") != sid
                    or draft.get("site_id") != session["site_id"] or draft.get("camera_id") != session["camera_id"]):
                raise ValueError("Active matching recording required")
            now = self.store.clock()
            payload = scene.first_receipt(draft, now)
            prior = db.execute("""SELECT id,kind,payload FROM events WHERE session_id=? AND kind IN ('qwen.observation','qwen.rejected')
                AND json_extract(payload,'$.request_id')=? ORDER BY id LIMIT 1""", (sid, payload["request_id"])).fetchone()
            saved = json.loads(prior["payload"]) if prior else None
            latest = db.execute("""SELECT max(json_extract(payload,'$.observed_at')) FROM events WHERE session_id=?
                AND kind='qwen.observation'""", (sid,)).fetchone()[0]
            decision = scene.admit(payload, prior=saved, latest_observed_at=latest, now=now)
            if decision == "duplicate":
                if prior["kind"] == "qwen.rejected":
                    return {"state": saved["reason"], "event_id": prior["id"]}
                return {"state": "available" if now < saved["valid_until"] else "expired", "event_id": prior["id"]}
            if decision == "identity_conflict":
                seen = db.execute("""SELECT id FROM events WHERE session_id=? AND kind='qwen.conflict'
                    AND json_extract(payload,'$.request_id')=? AND json_extract(payload,'$.fingerprint')=?""",
                    (sid, payload["request_id"], payload["fingerprint"])).fetchone()
                if seen:
                    return {"state": "identity_conflict", "event_id": seen["id"]}
                conflict = {"site_id": payload["site_id"], "camera_id": payload["camera_id"], "session_id": sid,
                            "request_id": payload["request_id"], "fingerprint": payload["fingerprint"],
                            "prior_event_id": prior["id"], "prior_fingerprint": saved.get("fingerprint"),
                            "available_at": now, "reason": "identity_conflict"}
                self.store.check_capacity(len(scene.canonical(conflict)) + 65536)
                return {"state": "identity_conflict", "event_id": self.store._event(db, sid, "qwen.conflict", conflict)}
            if decision in {"out_of_order", "expired_on_receipt"}:
                rejected = scene.rejection(payload, decision)
                self.store.check_capacity(len(scene.canonical(rejected)) + 65536)
                return {"state": decision, "event_id": self.store._event(db, sid, "qwen.rejected", rejected)}
            self.store.check_capacity(len(scene.canonical(payload)) + 65536)
            return {"state": "available", "event_id": self.store._event(db, sid, "qwen.observation", payload)}

    def receipts(self, session, cutoff, *, horizon_seconds=120):
        """(receipts, conflicts) a projection at cutoff may use; never later rows."""
        if not scene.finite(cutoff):
            raise ValueError("finite cutoff required")
        with self.store.lock:
            rows = self.store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='qwen.observation'
                AND json_extract(payload,'$.available_at')<=? AND json_extract(payload,'$.observed_at')>=?
                ORDER BY id DESC LIMIT 128""", (session["id"], cutoff, cutoff - horizon_seconds - scene.MAX_AGE_SECONDS)).fetchall()
            latest = self.store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='qwen.observation'
                AND json_extract(payload,'$.available_at')<=? ORDER BY json_extract(payload,'$.observed_at') DESC,id DESC LIMIT 1""",
                (session["id"], cutoff)).fetchone()
            conflicts = self.store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='qwen.conflict'
                AND json_extract(payload,'$.available_at')<=?""", (session["id"], cutoff)).fetchall()
        values = {}
        for row in list(rows) + ([latest] if latest else []):
            value = json.loads(row["payload"])
            if value.get("session_id") == session["id"] and value.get("site_id") == session["site_id"]:
                values[value["request_id"]] = value
        return list(values.values()), [json.loads(row["payload"]) for row in conflicts]


def scene_context(store, session, cutoff, *, generation, arms, horizon_seconds=120):
    """Identical shared qwen_scene record for every granted arm (integration helper)."""
    receipts, conflicts = QwenSceneLedger(store).receipts(session, cutoff, horizon_seconds=horizon_seconds)
    projected = scene.projection(receipts, cutoff, horizon_seconds=horizon_seconds, conflicts=conflicts)
    record = scene.scene_record(projected, receipts, cutoff, site_id=session["site_id"], camera_id=session["camera_id"],
                                session_id=session["id"], conflicts=conflicts)
    return scene.shared_records(record, arms=arms, generation=generation)


class QwenLivePump:
    """One recording's perceiver loop. tick() is the whole behaviour; the thread
    only calls it on the configured interval."""

    SCENE = scene

    def __init__(self, store, session, *, transport, profile, frames=None, ledger=None, config=None,
                 clock=time.time, monotonic=time.monotonic, local_time=None, enabled=None, active=None,
                 occupied=None, owns_transport=True, indeterminate_seconds=None):
        self.config = pump_config(config)
        low, high = INDETERMINATE_BOUNDS
        if indeterminate_seconds is not None and (not scene.finite(indeterminate_seconds)
                                                  or not low <= indeterminate_seconds <= high):
            raise ValueError("indeterminate re-probe interval outside its bound")
        if (not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS or profile.get("model_version") != self.SCENE.MODEL
                or profile.get("served_model") != self.SCENE.SERVED_MODEL or profile.get("adapter_revision") != self.SCENE.PROMPT_VERSION
                or any(not isinstance(v, str) or not v for v in profile.values())):
            raise ValueError("exact live-scene Qwen profile required")
        deadline = getattr(transport, "config", {}).get("deadline_seconds")
        if not scene.finite(deadline) or not 5 <= deadline <= MAX_DEADLINE_SECONDS:
            raise ValueError("live perceiver deadline must be 5-8 seconds")
        self.occupied = occupancy_gate.required(occupied)
        self.store, self.session = store, dict(session)
        self.transport, self.owns_transport = transport, owns_transport
        self.base_profile, self.profile = dict(profile), None
        self.frames = frames or RegisteredPreviewFrames(store)
        self.ledger = ledger or QwenSceneLedger(store)
        self.clock, self.monotonic = clock, monotonic
        self.local_time = local_time or zone_clock(self.config["timezone"])
        self.enabled = enabled or (lambda: True)
        self.active = active or (lambda: self.store.get_session(self.session["id"])["state"] == "recording")
        self.deadline_seconds = deadline
        self.indeterminate_seconds = indeterminate_seconds
        self.stopped = threading.Event()
        # Set by stop() and, with an edge-reporting gate (occupancy_reader), at
        # every off/unknown -> on occupancy edge: a vacant pump ticks at once.
        # A frame source that reports new frames (frame_ring) sets it too, so
        # a pump waiting for frames sends as soon as they are there.
        self.wake = threading.Event()
        occupancy_gate.subscribe(self.occupied, self.wake)
        self.frame_wake = callable(getattr(self.frames, "subscribe", None))
        occupancy_gate.subscribe(self.frames, self.wake)
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="lab-qwen-live", daemon=True)
        self.sent = deque()
        self.outstanding = None
        self.last_media = None
        self.prior = None
        self.backoff_until, self.backoff_reason = None, None
        # HALTED for good once LA reports the outstanding row indeterminate
        # (the recording pump); PAUSED while it stays indeterminate for a pump
        # with indeterminate_seconds, else None. reprobe_at: that pump's next
        # identity lookup (monotonic).
        self.halted = None
        self.reprobe_at = None
        self.status = "idle"
        self.last_reason = None
        self.counts = {}
        self.occupancy = {"state": "unchecked", "reason": None}

    # --- lifecycle -----------------------------------------------------
    def start(self):
        self.thread.start()

    def stop(self, timeout=None):
        """Stop ticking; an in-flight call ends within its own bound."""
        self.stopped.set()
        self.wake.set()
        occupancy_gate.unsubscribe(self.occupied, self.wake)
        occupancy_gate.unsubscribe(self.frames, self.wake)
        if self.owns_transport and callable(getattr(self.transport, "close", None)):
            self.transport.close()
        if timeout is not None and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def _woken(self):
        """Whether a wake may end the sleep: only after a tick that sent
        nothing for occupancy, or (with a frame source that reports new frames)
        for want of a new frame. Sends therefore stay an interval apart."""
        reason = self.last_reason
        return occupancy_gate.occupancy_skip(reason) or (self.frame_wake and reason in FRAME_WAIT_REASONS)

    def _run(self):
        # Stopped or woken: an occupancy edge ends the sleep only while the pump
        # is skipping for occupancy (or a new frame while it waits for one);
        # tick() still asks the gate first.
        delay = self.config["interval_seconds"]
        while not occupancy_gate.wait(self.stopped, self.wake, delay, self._woken):
            started = self.monotonic()
            try:
                self.tick()
            except Exception:
                self._note("pump_error")
            delay = max(0.0, self.config["interval_seconds"] - (self.monotonic() - started))
        self.status = "stopped"

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "last_reason": self.last_reason, "counts": dict(self.counts),
                    "outstanding": self.outstanding is not None, "halted": self.halted,
                    "occupancy": {**self.occupancy, "skipped": {r: self.counts.get(r, 0)
                                  for r in occupancy_gate.SKIP_REASONS.values()}}}

    def _note(self, reason, status=None):
        with self.lock:
            self.last_reason = reason
            if reason in self.counts or len(self.counts) < 32:
                self.counts[reason] = self.counts.get(reason, 0) + 1
            if status:
                self.status = status
        return {"state": "skipped" if status in (None, "skipping") else status, "reason": reason}

    # --- one tick ------------------------------------------------------
    def tick(self):
        if self.stopped.is_set():
            return self._note("stopped", "stopped")
        if self.halted == HALTED:
            return self._note(self.halted, "halted")
        if not self.enabled():
            return self._note("disabled", "disabled")
        try:
            if not self.active():
                return self._note("not_recording", "skipping")
        except Exception:
            return self._note("not_recording", "skipping")
        if self.outstanding is not None:
            return self._reconcile()
        skip, reading = occupancy_gate.check(self.occupied)
        with self.lock:
            self.occupancy = reading
        if skip:
            return self._note(skip, "skipping")
        now, mono = self.clock(), self.monotonic()
        if self.backoff_until is not None and mono < self.backoff_until:
            return self._note(self.backoff_reason + "_backoff", "skipping")
        while self.sent and mono - self.sent[0] >= 60:
            self.sent.popleft()
        if len(self.sent) >= self.config["max_per_minute"]:
            return self._note("rate_capped", "skipping")
        if self.local_time is None:
            return self._note("local_time_unavailable", "skipping")
        if self.profile is None:
            try:
                self.profile = self._current_profile(now)
            except QwenNotExecuted as error:
                return self._note(str(error), "skipping")
        try:
            frames = self.frames.select(self.session, now, count=self.config["frames"], span=self.config["span_seconds"])
        except FramesUnavailable as error:
            return self._note(error.reason, "skipping")
        if frames[-1]["media_id"] == self.last_media:
            return self._note("no_new_frame", "skipping")
        try:
            request = self._request(frames, now)
        except FramesUnavailable as error:
            return self._note(error.reason, "skipping")
        self.last_media = frames[-1]["media_id"]
        self.sent.append(mono)
        with self.lock:
            self.status = "in_flight"
        # Victoria-clock send time, kept with the request for a ledger with
        # terminal() (the always-on fact log's sent_at); never sent to LA.
        request["sent_at"] = self.clock()
        try:
            proof = self.transport(request["envelope"])
        except QwenNotExecuted as error:
            reason = str(error)
            if reason in BACKOFF_REFUSALS:
                # Not sent and nothing written on either side: wait it out.
                self.backoff_until, self.backoff_reason = self.monotonic() + BACKOFF_SECONDS, reason
            elif reason == "profile_mismatch":
                self.profile = None
            return self._note(reason, "skipping")
        except CompletionUnproven:
            self.outstanding = request
            return self._note("outcome_unproven", "awaiting_terminal")
        return self._terminal(request, proof)

    def _request(self, frames, now):
        hour_minute = self.local_time(now)
        prior = None
        if (self.config["send_prior"] and self.prior is not None
                and frames[-1]["observed_at"] - self.prior[0] <= self.config["prior_max_age_seconds"]):
            prior = self.prior[1]
        context = scene.model_context(scene.local_time_label(*hour_minute), prior)
        body = scene.request_body(context, [f["data"] for f in frames], [f["observed_at"] for f in frames])
        body_sha256 = scene.digest(body)
        request_id = scene.digest({"session_id": self.session["id"], "media_ids": [f["media_id"] for f in frames],
                                   "body_sha256": body_sha256, "prompt_version": scene.PROMPT_VERSION})
        envelope = {"request_id": request_id, "site_id": self.session["site_id"], "session_id": self.session["id"],
                    "profile": dict(self.profile), "body": body, "body_sha256": body_sha256,
                    "prompt_version": scene.PROMPT_VERSION, "task": scene.TASK, "deadline": now + self.deadline_seconds}
        return {"envelope": envelope, "reference": {k: v for k, v in envelope.items() if k != "body"},
                "frames": [{k: f[k] for k in ("media_id", "frame_id", "capture_epoch", "observed_at")} for f in frames],
                "context": context, "prior_supplied": prior is not None, "profile": dict(self.profile)}

    def _current_profile(self, now):
        """The LA's current engine epoch from an identity-only probe that can
        neither find nor create work (fresh id, no body)."""
        lookup = getattr(self.transport, "lookup", None)
        if not callable(lookup):
            return dict(self.base_profile)
        probe = {"request_id": scene.digest({"engine_epoch_probe": self.session["id"], "at": now}),
                 "site_id": self.session["site_id"], "session_id": self.session["id"], "profile": self.base_profile,
                 "body_sha256": scene.digest({"engine_epoch_probe": True}), "prompt_version": self.SCENE.PROMPT_VERSION,
                 "task": self.SCENE.TASK, "deadline": now + self.deadline_seconds}
        try:
            proof = lookup(probe)
        except Exception:
            raise QwenNotExecuted("engine_epoch_unavailable") from None
        if (not isinstance(proof, dict) or proof.get("request_id") != probe["request_id"]
                or proof.get("state") not in {"not_found", "not_admitted"} or not isinstance(proof.get("engine_epoch"), str)
                or not proof["engine_epoch"]):
            raise QwenNotExecuted("engine_epoch_unavailable")
        if proof.get("artifact_sha256") != self.base_profile["artifact_sha256"]:
            raise QwenNotExecuted("artifact_mismatch")
        return {**self.base_profile, "engine_epoch": proof["engine_epoch"]}

    def _reconcile(self):
        request = self.outstanding
        if self.halted == PAUSED:
            # One identity lookup per indeterminate_seconds while paused,
            # whatever it returns: a failed or unusable lookup waits too.
            mono = self.monotonic()
            if mono < self.reprobe_at:
                return self._note("la_indeterminate_backoff", "awaiting_terminal")
            self.reprobe_at = mono + self.indeterminate_seconds
        try:
            proof = self.transport.lookup(request["reference"])
        except CompletionUnproven:
            return self._note("outcome_unproven", "awaiting_terminal")
        if not isinstance(proof, dict) or proof.get("request_id") != request["reference"]["request_id"]:
            return self._note("outcome_unproven", "awaiting_terminal")
        if proof.get("state") == "indeterminate":
            if self.indeterminate_seconds is None:
                # Shared LA admission is closed until the owner reconciles it: add
                # no load and never resubmit for the rest of this recording.
                self.halted = HALTED
                return self._note(self.halted, "halted")
            # A pump with indeterminate_seconds (the always-on perceiver, plan
            # S1.3(c)): LA's live admission stays closed while this row is
            # indeterminate. Never resubmit: keep the request outstanding until
            # LA's reconcile (plan S1.8) marks it terminal, unknown_resolved.
            if self.halted != PAUSED:
                self.reprobe_at = self.monotonic() + self.indeterminate_seconds
            self.halted = PAUSED
            return self._note(self.halted, "awaiting_terminal")
        if proof.get("terminal") is not True:
            return self._note("awaiting_terminal", "awaiting_terminal")
        self.outstanding, self.halted, self.reprobe_at = None, None, None
        return self._terminal(request, proof)

    def _receipt(self, request, draft):
        """Record a completed request's draft. A ledger with terminal()
        (la_fact_log.FactLog) is given the request too; the recording ledger
        records the draft under this session, as before."""
        terminal = getattr(self.ledger, "terminal", None)
        if callable(terminal):
            return terminal(request, "completed", draft)
        return self.ledger.record(self.session["id"], draft)

    def _outcome(self, request, outcome):
        """A terminal LA outcome without an answer: only a ledger with
        terminal() records it (one line, no fact); the recording ledger
        records nothing, as before. Never raises into the tick."""
        terminal = getattr(self.ledger, "terminal", None)
        if callable(terminal):
            try:
                terminal(request, outcome, None)
            except Exception:
                self._note("record_failed")

    def _terminal(self, request, proof):
        state = proof.get("state") if isinstance(proof, dict) else None
        if state != "completed":
            reason = proof.get("reason") if isinstance(proof, dict) else None
            resolved = UNKNOWN_RESOLVED in (state, reason)
            self._outcome(request, UNKNOWN_RESOLVED if resolved else state if isinstance(state, str) else "unknown")
            if resolved:
                # Ended by LA's reconcile with no answer: no fact, nothing
                # recorded; the next tick sends again.
                return self._note("la_" + UNKNOWN_RESOLVED, "skipping")
            return self._note("la_" + (state if isinstance(state, str) else "unknown")
                              + ("_" + reason if isinstance(reason, str) and state == "rejected" else ""), "skipping")
        profile = request["profile"]
        content, finish = scene.reply_from_proof(proof)
        if proof.get("engine_epoch") != profile["engine_epoch"] or proof.get("artifact_sha256") != profile["artifact_sha256"]:
            content = None  # a reply from another engine or artifact is never bound to this request
        try:
            draft = scene.build_observation(
                site_id=self.session["site_id"], camera_id=self.session["camera_id"], session_id=self.session["id"],
                request_id=request["reference"]["request_id"], frames=request["frames"], generated_at=self.clock(),
                prompt_version=scene.PROMPT_VERSION, context=request["context"],
                body_sha256=request["reference"]["body_sha256"], engine_epoch=proof.get("engine_epoch"),
                artifact_sha256=proof.get("artifact_sha256"), content=content, finish_reason=finish,
                prior_supplied=request["prior_supplied"])
            result = self._receipt(request, draft)
        except Exception:
            return self._note("record_failed", "skipping")
        if result["state"] == "available" and draft["status"] == "valid":
            self.prior = (draft["observed_at"], draft["facts"])
            return self._note("recorded_valid", "current")
        if result["state"] == "available":
            return self._note("recorded_invalid_" + draft["invalid_reason"], "current")
        return self._note("receipt_" + result["state"], "skipping")

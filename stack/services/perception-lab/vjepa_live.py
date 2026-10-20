"""Recording-owned V-JEPA 2.1 clip pump (Victoria side).

service_runtime accepts a 'vjepa_live' pump factory (started, gated and
stopped like qwen_live); live_detector_app.vjepa_live_factory builds it from
the managed live config's 'vjepa_live' block, with the per-session LA lease of
vjepa_lease. Nothing here starts on import, and a pump sends nothing until
start().

Frames: the registered 640x360 RGB preview JPEGs the controller already stores
for the recording (~2 Hz, sha256-bound in the media table), read back through
qwen_live.RegisteredPreviewFrames' filters. Every tick takes the latest frame
and seven earlier ones of its capture epoch, one per slot of a 0.5 s grid
behind it (8 frames over ~3.5 s, the probe's 8-frame/3.5 s profile). The
stored previews arrive about twice a second but unevenly, so a slot takes any
stored frame within clip_slot_tolerance_seconds of its grid time (rule
grid-slot-nearest-1; default and bound 0.4 s, 0.25 s is the old strict grid),
and the clip is the one with the smallest total distance from the grid whose
gaps (0.25-0.75 s) and span (3.0-4.0 s) meet the wire contract the LA worker
enforces. No frame is duplicated or invented: a stored gap over 0.75 s inside
the last 3 s cannot be bridged and skips as frames_gap. The frame times
relative to the latest, their grid offsets, the span and the largest gap are
kept in the observation's input.clip. It verifies size, digest and JPEG
header, and sends the stored bytes unchanged. Nothing re-encodes, opens the
camera or decodes pixels here: the Jetson spends one core at most on digests
and base64.

Person box: the union of the Lab detector's person boxes on those same frames,
from recorded completed perception.output rows of either arm (the union is
symmetric in the arms, and the observation is shared by both). The detectors
sample about 1 Hz and their newest output lags the preview by ~0.6 s, so only
~3 of a clip's 8 frames have an output of their own: each clip frame is matched,
per arm, to the nearest completed output of the same capture epoch observed
within box_match_seconds of it (rule nearest-detector-output-1, bounded at
0.75 s; 0 is exact-frame matching) and available by now. The rule, tolerance
and largest offset used are kept in the observation's input. The heads were
trained on person crops, so a clip with no usable box is skipped with a typed
reason rather than run full-frame; two or more people on any frame skip too
(single-person heads). The NTU zero-shot head runs only when every box is
confident (>= 0.7), as in its training runtime.

Admission: one request in flight (the pump is single-threaded and each call is
bounded), latest frame wins, interval 1-10 s (default 2 s), a per-minute cap of
1-60 (default 40), typed skips (a supervisor refusal such as thermal_shed is
one), a 30 s backoff after repeated transport failures, an operator kill switch
and a clean stop with the recording. The transport keeps one HTTPS connection
(VjepaTransport); a kept socket the supervisor had already closed is a typed
skip that is neither retried in the tick nor counted toward the backoff. The pump's typed tick counts (every skip reason
and outcome) are written to the ledger as a vjepa.pump_status event every
STATUS_SECONDS and once when its thread ends, so a session shows why clips were
skipped. The LA worker is leased per session from its
own lifecycle supervisor; this base pump never starts or stops it, and
vjepa_lease.LeasedVjepaPump holds that lease through the prepare() hook.

Occupancy (owner requirement): a pump cannot be built without an occupied()
gate (occupancy_gate.OccupancyGate over the recording's camera-derived Frigate
person-occupancy HA rows). Every tick asks it before any frame read or box
lookup; anything but 'occupied' skips with no_person / occupancy_unknown and
nothing reaches the LA. The gate only gates: it adds no evidence and
refreshes no freshness. An occupancy_reader gate's off/unknown -> on edge wakes
a vacant pump at once, as in qwen_live.
"""
import hashlib
import http.client
import io
import json
import ssl
import threading
import time
from collections import deque
from urllib.parse import urlsplit

import occupancy_gate
import vjepa_scene as scene
from qwen_live import FramesUnavailable, RegisteredPreviewFrames
from qwen_transport import close_quietly, kept_connection_unusable, response_released

EXECUTE_PATH = "/v1/specialist/execute"
DEFAULTS = {"interval_seconds": 2.0, "max_per_minute": 40, "min_box_frames": 4, "min_confidence": .5,
            "ntu_confidence": .7, "prior_max_gap_seconds": 5.0, "box_match_seconds": .75,
            "clip_slot_tolerance_seconds": .4}
BACKOFF_AFTER_FAILURES = 3
BACKOFF_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024
# A kept connection reset or closed before any response byte (speed plan S1.7):
# the supervisor had dropped an idle socket, which is not a supervisor failure.
STALE_SOCKET = "transport_stale_socket"
_STALE_ERRORS = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                 ssl.SSLEOFError, ssl.SSLZeroReturnError)
EVENT_KINDS = ("vjepa.observation", "vjepa.rejected", "vjepa.conflict", "vjepa.pump_status")
# Clip frame -> detector output matching (PersonBoxes.union).
BOX_MATCH_RULE = "nearest-detector-output-1"
MAX_BOX_MATCH_SECONDS = .75
# Clip frame selection (ClipFrames.select_clip): per grid slot, a stored frame
# within the tolerance of its grid time. Half the grid step is the old strict
# grid; the bound keeps every slot nearer its own grid time than a neighbour's.
CLIP_RULE = "grid-slot-nearest-1"
MIN_SLOT_TOLERANCE_SECONDS = scene.STEP_SECONDS / 2
MAX_SLOT_TOLERANCE_SECONDS = .4
# The pump's typed counts are written to the ledger this often, and at stop.
STATUS_SCHEMA = "vjepa-pump-status-1"
STATUS_SECONDS = 30


class BoxUnavailable(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class VjepaNotExecuted(Exception):
    """The supervisor or worker refused the clip with a typed reason."""


class TransportUnavailable(Exception):
    """No bound reply (network, TLS, timeout, malformed body)."""


def pump_config(value=None):
    value = dict(value or {})
    if set(value) - set(DEFAULTS):
        raise ValueError("unknown V-JEPA live setting")
    config = {**DEFAULTS, **value}
    for key, low, high in (("interval_seconds", 1.0, 10), ("prior_max_gap_seconds", 2.5, 10),
                           ("min_confidence", .25, .95), ("ntu_confidence", .5, .99)):
        if not scene.finite(config[key]) or not low <= config[key] <= high:
            raise ValueError(f"{key} outside its bound")
    if (type(config["box_match_seconds"]) not in (int, float) or not scene.finite(config["box_match_seconds"])
            or not 0 <= config["box_match_seconds"] <= MAX_BOX_MATCH_SECONDS):
        raise ValueError("box_match_seconds outside its bound")
    check_slot_tolerance(config["clip_slot_tolerance_seconds"])
    if config["ntu_confidence"] < config["min_confidence"]:
        raise ValueError("NTU gate cannot be looser than the crop threshold")
    if type(config["max_per_minute"]) is not int or not 1 <= config["max_per_minute"] <= 60:
        raise ValueError("at most 60 clip requests per minute")
    if type(config["min_box_frames"]) is not int or not 1 <= config["min_box_frames"] <= scene.FRAME_COUNT:
        raise ValueError("1-8 boxed frames required")
    return config


def check_slot_tolerance(value):
    if (type(value) not in (int, float) or not scene.finite(value)
            or not MIN_SLOT_TOLERANCE_SECONDS <= value <= MAX_SLOT_TOLERANCE_SECONDS):
        raise ValueError("clip_slot_tolerance_seconds outside its bound")
    return value


def clip_input(frames, tolerance):
    """How the clip's frames sit on the grid, for the observation's input:
    times relative to the latest frame and offsets from each slot's grid time
    (oldest first), the span and the largest and smallest gaps, in seconds."""
    times = [f["observed_at"] for f in frames]
    latest, last = times[-1], len(times) - 1
    gaps = [b - a for a, b in zip(times, times[1:])]
    offsets = [t - (latest - scene.STEP_SECONDS * (last - i)) for i, t in enumerate(times)]
    return {"rule": CLIP_RULE, "slot_tolerance_seconds": tolerance,
            "relative_times_seconds": [round(t - latest, 3) for t in times],
            "grid_offsets_seconds": [round(o, 3) for o in offsets],
            "max_grid_offset_seconds": round(max(abs(o) for o in offsets), 3),
            "span_seconds": round(latest - times[0], 3),
            "max_gap_seconds": round(max(gaps), 3), "min_gap_seconds": round(min(gaps), 3)}


class ClipFrames(RegisteredPreviewFrames):
    """Eight registered preview frames, one per slot of a 0.5 s grid ending at
    the latest one, each within slot_tolerance of its grid time."""

    def __init__(self, store, *, slot_tolerance=DEFAULTS["clip_slot_tolerance_seconds"], scan_rows=48):
        super().__init__(store, scan_rows=scan_rows)
        self.slot_tolerance = check_slot_tolerance(slot_tolerance)

    def select_clip(self, session, now):
        pool = self.candidates(session, now)
        if not pool:
            raise FramesUnavailable("frames_unavailable")
        latest = max(pool, key=lambda f: (f["observed_at"], f["media_id"]))
        if now >= latest["valid_until"]:
            raise FramesUnavailable("frames_stale")
        end = latest["observed_at"]
        same = sorted((f for f in pool if f["capture_epoch"] == latest["capture_epoch"] and f["observed_at"] < end),
                      key=lambda f: (f["observed_at"], f["media_id"]))
        chosen = self._search(latest, same)
        if chosen is None:
            # Every valid clip covers the last MIN_SPAN seconds, so a stored gap
            # over MAX_GAP there cannot be bridged by any choice of frames.
            covered = [f["observed_at"] for f in same if f["observed_at"] >= end - scene.MIN_SPAN] + [end]
            window = [f["observed_at"] for f in same if f["observed_at"] < end - scene.MIN_SPAN][-1:] + covered
            gap = any(b - a > scene.MAX_GAP for a, b in zip(window, window[1:]))
            raise FramesUnavailable("frames_gap" if gap else "frames_insufficient")
        try:
            scene.check_times([f["observed_at"] for f in chosen])
        except ValueError:
            raise FramesUnavailable("frames_irregular") from None
        return [dict(frame, data=self.read(session, frame)) for frame in chosen]

    def _search(self, latest, earlier):
        """Oldest-first frames ending at latest, one per grid slot within the
        tolerance, with every gap and the span inside the wire contract
        (scene.check_times), minimizing the summed distance from the grid
        (ties: the smaller media ids). None when no such clip exists.

        A small dynamic programme over the slots, newest to oldest: each state
        is the frame chosen for the slot, holding the best path to it."""
        end, tolerance = latest["observed_at"], self.slot_tolerance
        frontier = [(0.0, (latest["media_id"],), [latest])]
        for index in range(1, scene.FRAME_COUNT):
            target = end - scene.STEP_SECONDS * index
            reached = []
            for frame in earlier:
                offset = abs(frame["observed_at"] - target)
                if offset > tolerance + 1e-9:
                    continue
                paths = [(cost + offset, ids + (frame["media_id"],), path + [frame])
                         for cost, ids, path in frontier
                         if scene.MIN_GAP <= path[-1]["observed_at"] - frame["observed_at"] <= scene.MAX_GAP]
                if paths:
                    reached.append(min(paths, key=lambda p: p[:2]))
            if not reached:
                return None
            frontier = reached
        done = [p for p in frontier if scene.MIN_SPAN <= end - p[2][-1]["observed_at"] <= scene.MAX_SPAN]
        return list(reversed(min(done, key=lambda p: p[:2])[2])) if done else None

    def read(self, session, frame):
        """Stored bytes, digest-checked, JPEG header only (no pixel decode)."""
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
                if image.format != "JPEG" or image.size != scene.IMAGE_SIZE or image.mode not in {"RGB", "L"}:
                    raise ValueError()
        except Exception:
            raise FramesUnavailable("frame_decode_failed") from None
        return data


class PersonBoxes:
    """Union person box from recorded Lab detector outputs on the clip's frames."""

    def __init__(self, store, *, min_frames=4, min_confidence=.5, ntu_confidence=.7, match_seconds=MAX_BOX_MATCH_SECONDS):
        if (type(match_seconds) not in (int, float) or not scene.finite(match_seconds)
                or not 0 <= match_seconds <= MAX_BOX_MATCH_SECONDS):
            raise ValueError("bounded box match tolerance required")
        self.store, self.min_frames = store, min_frames
        self.min_confidence, self.ntu_confidence = min_confidence, ntu_confidence
        self.match_seconds = match_seconds

    def _people(self, result):
        people = []
        for item in result["boxes"][:512]:
            if not isinstance(item, dict) or item.get("label") != "person":
                continue
            score = item.get("confidence")
            if not scene.finite(score) or score < self.min_confidence:
                continue
            try:
                box = scene.check_box([item["x"], item["y"], item["x"] + item["w"], item["y"] + item["h"]])
            except (KeyError, TypeError, ValueError):
                continue
            people.append((box, score))
        return people

    def union(self, session, frames, now):
        """The union box over the clip frames' matched detector outputs.

        Each clip frame is matched, per arm, to the nearest completed output
        of the clip's capture epoch observed within match_seconds of it (ties:
        the earlier event) and available by now; never a later row. A matched
        output with two or more confident people skips the clip."""
        tolerance = self.match_seconds
        times = [f["observed_at"] for f in frames]
        epoch = frames[-1]["capture_epoch"]
        with self.store.lock:
            rows = self.store.db.execute("""SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output'
                AND json_extract(payload,'$.observed_at') BETWEEN ? AND ?
                AND json_extract(payload,'$.available_at')<=? ORDER BY id LIMIT 64""",
                (session["id"], min(times) - tolerance, max(times) + tolerance, now)).fetchall()
        outputs = []  # (arm, observed_at, event id, people)
        for row in rows:
            value = json.loads(row["payload"])
            result = value.get("result")
            if (value.get("status") != "completed" or value.get("arm") not in {"a", "b"}
                    or value.get("site_id") != session["site_id"] or value.get("camera_id") != session["camera_id"]
                    or value.get("capture_epoch") != epoch or not scene.finite(value.get("observed_at"))
                    or not scene.finite(value.get("available_at")) or value["available_at"] > now
                    or not isinstance(result, dict) or result.get("schema_version") != 1
                    or not isinstance(value.get("frame_id"), str) or result.get("frame_id") != value["frame_id"]
                    or result.get("coordinate_space") != "source_pixels" or not isinstance(result.get("boxes"), list)):
                continue
            outputs.append((value["arm"], float(value["observed_at"]), row["id"], self._people(result)))
        matched, covered, offset = {}, set(), 0.0
        for frame in frames:
            for arm in ("a", "b"):
                options = [o for o in outputs if o[0] == arm and abs(o[1] - frame["observed_at"]) <= tolerance + 1e-9]
                if not options:
                    continue
                best = min(options, key=lambda o: (abs(o[1] - frame["observed_at"]), o[2]))
                matched[best[2]] = best
                offset = max(offset, abs(best[1] - frame["observed_at"]))
                if best[3]:
                    covered.add(frame["frame_id"])
        boxes, single = [], True
        for _, _, _, people in sorted(matched.values(), key=lambda o: o[2]):
            if len(people) > 1:
                raise BoxUnavailable("multiple_people")
            if people:
                boxes.append(people[0][0])
                single = single and people[0][1] >= self.ntu_confidence
        if not boxes:
            raise BoxUnavailable("no_person_box" if matched else "detector_output_unavailable")
        if len(covered) < self.min_frames:
            raise BoxUnavailable("person_box_sparse")
        union = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        return {"box": union, "frames": len(covered), "single_person": single, "event_ids": sorted(matched)[:32],
                "match": {"rule": BOX_MATCH_RULE, "tolerance_seconds": tolerance,
                          "max_offset_seconds": round(offset, 3)}}


class VjepaTransport:
    """HTTPS to the LA V-JEPA supervisor's fixed execute proxy (bearer token, pinned CA).

    Keep-alive (speed plan S1.7, keep_alive default true): one connection is
    kept between clips and replaced before a send when it has been idle more
    than qwen_transport.KEEP_ALIVE_IDLE_SECONDS, its socket is readable, or the
    last response carried Connection: close; it is closed after any error and
    by close(). A kept connection reset or closed before any response byte
    raises STALE_SOCKET; nothing is retried here."""

    def __init__(self, origin, credential, ca_file, *, timeout=4.0, connection_factory=None, keep_alive=True,
                 monotonic=time.monotonic):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or parsed.hostname != "192.168.0.100" or parsed.port != 9447
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("provisioned LA V-JEPA origin required")
        if not scene.finite(timeout) or not 1 <= timeout <= 6:
            raise ValueError("bounded clip timeout required")
        if type(keep_alive) is not bool:
            raise ValueError("keep_alive must be true or false")
        self.credential, self.timeout, self.keep_alive, self.monotonic = credential, timeout, keep_alive, monotonic
        context = ssl.create_default_context(cafile=ca_file) if connection_factory is None else None
        self.connection = connection_factory or (lambda: http.client.HTTPSConnection(
            parsed.hostname, parsed.port, timeout=timeout, context=context))
        self.closed = False
        # The kept connection and when its last exchange ended (monotonic). The
        # pump calls from one thread; close() may come from another.
        self._kept, self._last_used = None, None
        self._lock = threading.Lock()

    def close(self):
        """Refuse new clips and close the kept connection; an in-flight clip
        ends within its timeout and then closes its own."""
        with self._lock:
            self.closed = True
            connection, self._kept = self._kept, None
        close_quietly(connection)

    def _take(self):
        """(connection, reused): the kept one unless unusable, else a new one."""
        with self._lock:
            connection, self._kept = self._kept, None
        if connection is not None and not kept_connection_unusable(connection, self._last_used, self.monotonic()):
            return connection, True
        close_quietly(connection)
        return self.connection(), False

    def _release(self, connection, reusable):
        with self._lock:
            if reusable and self.keep_alive and not self.closed:
                self._kept, self._last_used = connection, self.monotonic()
                return
        close_quietly(connection)

    def __call__(self, body):
        if self.closed:
            raise TransportUnavailable("transport_closed")
        try:
            token = self.credential()
        except Exception:
            token = None
        if not isinstance(token, str) or not 32 <= len(token) <= 512 or "\r" in token or "\n" in token:
            raise VjepaNotExecuted("credential_unavailable")
        data = scene.canonical(body).encode()
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if not self.keep_alive:
            headers["Connection"] = "close"
        connection, reused = self._take()
        reusable = False
        try:
            try:
                connection.request("POST", EXECUTE_PATH, data, headers)
                response = connection.getresponse()
            except _STALE_ERRORS:
                # Before any response byte: on a kept connection the supervisor
                # had already closed it. Typed, never retried in this call.
                raise TransportUnavailable(STALE_SOCKET if reused else "transport_unavailable") from None
            except (OSError, http.client.HTTPException, ssl.SSLError):
                raise TransportUnavailable("transport_unavailable") from None
            try:
                # read(n) ends a Content-Length body exactly (Python 3.10 too).
                status, raw = response.status, response.read(MAX_RESPONSE_BYTES + 1)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                raise TransportUnavailable("transport_unavailable") from None
            reusable = len(raw) <= MAX_RESPONSE_BYTES and response_released(connection, response)
        finally:
            self._release(connection, reusable)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportUnavailable("response_too_large")
        try:
            value = json.loads(raw)
        except ValueError:
            raise TransportUnavailable("response_not_json") from None
        if status == 200:
            return value, hashlib.sha256(raw).hexdigest()
        reason = value.get("reason") if isinstance(value, dict) else None
        if isinstance(reason, str) and len(reason) <= 64 and reason.replace("_", "").isalnum():
            raise VjepaNotExecuted(reason)
        raise TransportUnavailable("status_%d" % status if 100 <= status <= 599 else "status_invalid")


class VjepaLedger:
    """Durable vjepa.observation receipts; first receipt wins."""

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
            prior = db.execute("""SELECT id,kind,payload FROM events WHERE session_id=? AND kind IN ('vjepa.observation','vjepa.rejected')
                AND json_extract(payload,'$.request_id')=? ORDER BY id LIMIT 1""", (sid, payload["request_id"])).fetchone()
            saved = json.loads(prior["payload"]) if prior else None
            latest = db.execute("""SELECT max(json_extract(payload,'$.observed_at')) FROM events WHERE session_id=?
                AND kind='vjepa.observation'""", (sid,)).fetchone()[0]
            decision = scene.admit(payload, prior=saved, latest_observed_at=latest, now=now)
            if decision == "duplicate":
                if prior["kind"] == "vjepa.rejected":
                    return {"state": saved["reason"], "event_id": prior["id"]}
                return {"state": "available" if now < saved["valid_until"] else "expired", "event_id": prior["id"]}
            if decision == "identity_conflict":
                conflict = {"site_id": payload["site_id"], "camera_id": payload["camera_id"], "session_id": sid,
                            "request_id": payload["request_id"], "fingerprint": payload["fingerprint"],
                            "prior_event_id": prior["id"], "available_at": now, "reason": "identity_conflict"}
                self.store.check_capacity(len(scene.canonical(conflict)) + 65536)
                return {"state": "identity_conflict", "event_id": self.store._event(db, sid, "vjepa.conflict", conflict)}
            if decision in {"out_of_order", "expired_on_receipt"}:
                rejected = scene.rejection(payload, decision)
                self.store.check_capacity(len(scene.canonical(rejected)) + 65536)
                return {"state": decision, "event_id": self.store._event(db, sid, "vjepa.rejected", rejected)}
            self.store.check_capacity(len(scene.canonical(payload)) + 65536)
            return {"state": "available", "event_id": self.store._event(db, sid, "vjepa.observation", payload)}

    def receipts(self, session, cutoff):
        """Receipts a projection at cutoff may use; never later rows. A request
        with a known identity conflict is excluded once that conflict is known."""
        if not scene.finite(cutoff):
            raise ValueError("finite cutoff required")
        with self.store.lock:
            rows = self.store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='vjepa.observation'
                AND json_extract(payload,'$.available_at')<=? AND json_extract(payload,'$.observed_at')>=?
                ORDER BY id DESC LIMIT 128""", (session["id"], cutoff, cutoff - scene.HORIZON_SECONDS - scene.MAX_AGE_SECONDS)).fetchall()
            latest = self.store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='vjepa.observation'
                AND json_extract(payload,'$.available_at')<=? ORDER BY json_extract(payload,'$.observed_at') DESC,id DESC LIMIT 1""",
                (session["id"], cutoff)).fetchone()
            conflicted = {json.loads(r["payload"]).get("request_id") for r in self.store.db.execute(
                """SELECT payload FROM events WHERE session_id=? AND kind='vjepa.conflict'
                AND json_extract(payload,'$.available_at')<=?""", (session["id"], cutoff)).fetchall()}
        values = {}
        for row in list(rows) + ([latest] if latest else []):
            value = json.loads(row["payload"])
            if (value.get("session_id") == session["id"] and value.get("site_id") == session["site_id"]
                    and value.get("request_id") not in conflicted):
                values[value["request_id"]] = value
        return list(values.values())


def scene_context(store, session, cutoff, *, generation, arms):
    """Identical shared vjepa_scene record for every granted arm (integration helper)."""
    receipts = VjepaLedger(store).receipts(session, cutoff)
    projected = scene.projection(receipts, cutoff)
    record = scene.scene_record(projected, receipts, cutoff, site_id=session["site_id"], camera_id=session["camera_id"],
                                session_id=session["id"])
    return scene.shared_records(record, arms=arms, generation=generation)


class VjepaLivePump:
    """One recording's clip loop. tick() is the whole behaviour; the thread
    only calls it on the configured interval."""

    def __init__(self, store, session, *, transport, profile, runtime_revision=None, frames=None, boxes=None,
                 ledger=None, config=None, clock=time.time, monotonic=time.monotonic, enabled=None, active=None,
                 occupied=None, owns_transport=True):
        self.config = pump_config(config)
        self.profile = scene.check_profile(profile, runtime_revision=runtime_revision)
        timeout = getattr(transport, "timeout", None)
        if timeout is not None and (not scene.finite(timeout) or timeout > 6):
            raise ValueError("clip transport must be bounded to 6 seconds")
        self.occupied = occupancy_gate.required(occupied)
        self.store, self.session = store, dict(session)
        self.transport, self.owns_transport = transport, owns_transport
        self.frames = frames or ClipFrames(store, slot_tolerance=self.config["clip_slot_tolerance_seconds"])
        self.boxes = boxes or PersonBoxes(store, min_frames=self.config["min_box_frames"],
                                          min_confidence=self.config["min_confidence"],
                                          ntu_confidence=self.config["ntu_confidence"],
                                          match_seconds=self.config["box_match_seconds"])
        self.ledger = ledger or VjepaLedger(store)
        self.clock, self.monotonic = clock, monotonic
        self.enabled = enabled or (lambda: True)
        self.active = active or (lambda: self.store.get_session(self.session["id"])["state"] == "recording")
        self.stopped = threading.Event()
        # Set by stop() and at an off/unknown -> on occupancy edge (qwen_live).
        self.wake = threading.Event()
        occupancy_gate.subscribe(self.occupied, self.wake)
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="lab-vjepa-live", daemon=True)
        self.sent = deque()
        self.last_media = None
        self.previous = None  # (request_id, observed_at) of the last valid recorded clip
        self.failures = 0
        self.backoff_until = None
        self.status = "idle"
        self.last_reason = None
        self.counts = {}
        self.occupancy = {"state": "unchecked", "reason": None}

    def start(self):
        self.thread.start()

    def stop(self, timeout=None):
        """Stop ticking; an in-flight call ends within its own bound."""
        self.stopped.set()
        self.wake.set()
        occupancy_gate.unsubscribe(self.occupied, self.wake)
        if self.owns_transport and callable(getattr(self.transport, "close", None)):
            self.transport.close()
        if timeout is not None and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def _run(self):
        delay = self.config["interval_seconds"]
        reported = self.monotonic()
        while not occupancy_gate.wait(self.stopped, self.wake, delay,
                                      lambda: occupancy_gate.occupancy_skip(self.last_reason)):
            started = self.monotonic()
            try:
                self.tick()
            except Exception:
                self._note("pump_error")
            if self.monotonic() - reported >= STATUS_SECONDS:
                reported = self.monotonic()
                self.status_event("periodic")
            delay = max(0.0, self.config["interval_seconds"] - (self.monotonic() - started))
        self.status = "stopped"
        self.status_event("stopped")

    def status_event(self, reason):
        """A vjepa.pump_status ledger event with the pump's cumulative typed
        tick counts (skip reasons and outcomes), so a session shows why clips
        were or were not sent. Local only; best effort, never raises."""
        with self.lock:
            counts, status, last = dict(self.counts), self.status, self.last_reason
        try:
            return self.store.append_event(self.session["id"], "vjepa.pump_status", {
                "schema": STATUS_SCHEMA, "site_id": self.session["site_id"], "camera_id": self.session["camera_id"],
                "session_id": self.session["id"], "available_at": self.clock(), "reason": reason, "status": status,
                "last_reason": last, "counts": counts, "ticks": sum(counts.values()),
                "box_match": {"rule": BOX_MATCH_RULE, "tolerance_seconds": self.config["box_match_seconds"],
                              "min_box_frames": self.config["min_box_frames"]},
                "clip": {"rule": CLIP_RULE, "slot_tolerance_seconds": self.config["clip_slot_tolerance_seconds"]}})
        except Exception:
            return None

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "last_reason": self.last_reason, "counts": dict(self.counts),
                    "occupancy": {**self.occupancy, "skipped": {r: self.counts.get(r, 0)
                                  for r in occupancy_gate.SKIP_REASONS.values()}}}

    def prepare(self):
        """None when the worker may be asked, else a typed skip reason. The
        base pump has a fixed profile and nothing to admit."""
        return None

    def _note(self, reason, status=None):
        with self.lock:
            self.last_reason = reason
            if reason in self.counts or len(self.counts) < 32:
                self.counts[reason] = self.counts.get(reason, 0) + 1
            if status:
                self.status = status
        return {"state": "skipped" if status in (None, "skipping") else status, "reason": reason}

    def tick(self):
        if self.stopped.is_set():
            return self._note("stopped", "stopped")
        if not self.enabled():
            return self._note("disabled", "disabled")
        try:
            if not self.active():
                return self._note("not_recording", "skipping")
        except Exception:
            return self._note("not_recording", "skipping")
        skip, reading = occupancy_gate.check(self.occupied)
        with self.lock:
            self.occupancy = reading
        if skip:
            return self._note(skip, "skipping")
        # Worker admission (vjepa_lease.LeasedVjepaPump holds the per-session
        # LA lease here, after every gate, so an empty room or a revoked grant
        # never starts the worker). Before the clock read: acquiring may wait.
        reason = self.prepare()
        if reason is not None:
            return self._note(reason, "skipping")
        now, mono = self.clock(), self.monotonic()
        if self.backoff_until is not None and mono < self.backoff_until:
            return self._note("transport_backoff", "skipping")
        while self.sent and mono - self.sent[0] >= 60:
            self.sent.popleft()
        if len(self.sent) >= self.config["max_per_minute"]:
            return self._note("rate_capped", "skipping")
        try:
            frames = self.frames.select_clip(self.session, now)
        except FramesUnavailable as error:
            return self._note(error.reason, "skipping")
        if frames[-1]["media_id"] == self.last_media:
            return self._note("no_new_frame", "skipping")
        try:
            box = self.boxes.union(self.session, frames, now)
        except BoxUnavailable as error:
            self.last_media = frames[-1]["media_id"]
            return self._note(error.reason, "skipping")
        previous = None
        if self.previous is not None and 0 < frames[-1]["observed_at"] - self.previous[1] <= self.config["prior_max_gap_seconds"]:
            previous = self.previous[0]
        body = scene.request_body(site_id=self.session["site_id"], camera_id=self.session["camera_id"],
                                  session_id=self.session["id"], profile=self.profile, images=[f["data"] for f in frames],
                                  observed=[f["observed_at"] for f in frames], media_ids=[f["media_id"] for f in frames],
                                  box=box["box"], single_person=box["single_person"], previous_request_id=previous)
        body_sha256 = scene.digest(body)
        self.last_media = frames[-1]["media_id"]
        self.sent.append(mono)
        with self.lock:
            self.status = "in_flight"
        try:
            value, response_sha256 = self.transport(body)
        except VjepaNotExecuted as error:
            # A typed supervisor or worker refusal (thermal_shed included) is a
            # skip, never a transport failure.
            return self._note("la_" + str(error), "skipping")
        except TransportUnavailable as error:
            reason = str(error) or "transport_unavailable"
            # A stale kept socket is not counted toward the backoff; the clip
            # is not retried in this tick (the next tick sends a newer one).
            if reason != STALE_SOCKET:
                self.failures += 1
                if self.failures >= BACKOFF_AFTER_FAILURES:
                    self.failures, self.backoff_until = 0, self.monotonic() + BACKOFF_SECONDS
            return self._note(reason, "skipping")
        self.failures = 0
        request = {k: body[k] for k in ("request_id", "profile", "person_box", "single_person", "previous_request_id")}
        provenance = [{k: f[k] for k in ("media_id", "frame_id", "capture_epoch", "observed_at")} for f in frames]
        try:
            draft = scene.build_observation(site_id=self.session["site_id"], camera_id=self.session["camera_id"],
                                            session_id=self.session["id"], request=request, frames=provenance,
                                            box_input=box, generated_at=max(self.clock(), frames[-1]["observed_at"]),
                                            result=value, body_sha256=body_sha256, response_sha256=response_sha256)
            # How the frames sit on the grid (grid-slot-nearest-1), so the
            # effect of uneven spacing on the embeddings can be judged later.
            # Part of input, so it is bound into the receipt's fingerprint.
            draft["input"]["clip"] = clip_input(frames, self.config["clip_slot_tolerance_seconds"])
            result = self.ledger.record(self.session["id"], draft)
        except Exception:
            return self._note("record_failed", "skipping")
        if result["state"] == "available" and draft["status"] == "valid":
            self.previous = (draft["request_id"], draft["observed_at"])
            return self._note("recorded_valid", "current")
        if result["state"] == "available":
            return self._note("recorded_invalid_" + draft["invalid_reason"], "current")
        return self._note("receipt_" + result["state"], "skipping")

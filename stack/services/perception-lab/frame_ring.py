"""In-memory ring of the newest commissioned den previews (speed plan S1.3).

The always-on LA perceiver (la_live_perceiver) needs recent den frames outside
Lab recordings. The camera serves one /frame.jpg at a time and refuses
anything concurrent, so the Lab controller stays the only capture owner
(plan section 13 #1, section 15 #11): the ring is filled inside the controller
process, through the same commissioned client (ChronyPreviewClient) and the
same process-local capture lock that camera chat and a recording's preview
pump use. No second process, no second camera owner.

FrameRing keeps the newest accepted previews of one camera, RAM only: at most
RING_SECONDS (10 s) of frames and MAX_FRAMES of them, never written to disk,
pruned by age. Each frame is accepted by PreviewIngestor's rules for the live
causal client: a schema-3 receipt checked by the shared camera contract
(home_shared.protocol.camera_frame: exact fields, JPEG hash, causal ordering,
uncertainty arithmetic), observed no later than now and at most 3 s old with
its uncertainty, a 640x360 single-frame JPEG that decodes. A frame carries its
bytes, sha256, observed_at, valid_until (observed_at + 3 s - uncertainty, as
the Lab stores it), frame_id and capture epoch; its media_id is
'ring-<sha256>' (it names the bytes; no Lab media row exists). select() is the
pump's frame source: the same window rule as the recording's stored previews
(qwen_live.spread). Every new frame wakes subscribed pumps.

RingCapture is the ring's capture thread:
- it captures only while the occupancy gate (occupancy_reader) is open and
  its enabled() check holds (the perceiver: the household kill switch is on,
  its pump could send the frames soon and its fact log can record the
  answers); nothing while the room is vacant or unknown (it waits for the
  gate's on-edge, then captures at once) and nothing while enabled() is false
  (poke() makes it look again at once);
- an idle gap after every capture: the next one starts at least one interval
  (default 0.5 s) after the previous one ended, so with captures shorter than
  the interval the ring holds the capture lock less than half the time and
  never captures above 2 Hz;
- camera chat comes first: a slot is skipped while another in-process capture
  (camera chat or a recording's preview) holds the lock, is waiting for it, or
  ended less than YIELD_SECONDS (0.5 s) ago. The ring never waits for the
  lock: CaptureTurns refuses the ring's acquire at once in those cases, so the
  commissioned client reports 'busy' and the slot is skipped;
- a ring capture is one attempt (RingPreviewClient, the perceiver's fetch):
  the commissioned client's own retry loop (up to 3 attempts within 2 s while
  it holds the lock) is longer than camera chat's 1 s wait for its turn, so
  the ring gives a rejected slot up instead. Camera chat arriving during a
  ring capture waits for that one attempt only.

CaptureTurns observes chrony_preview_client._CAPTURE_LOCK without changing it
for anyone else: it wraps that same lock (acquire, release and locked delegate
to it, with the caller's own timeout), and only counts waiting callers and
remembers when a capture other than the ring's ended.

A recording's own preview captures can also be offered to the ring (offer());
they are accepted by the same rules, so the ring keeps filling during a Lab
recording while it yields every capture slot to that recording.
"""
import base64
import hashlib
import io
import json
import math
import re
import threading
import time
import weakref
from collections import deque

import chrony_preview_client
import occupancy_gate
import qwen_live
import qwen_scene as scene

RING_SECONDS = 10.0
MAX_FRAMES = 24
CAPTURE_INTERVAL = .5
CAPTURE_BOUNDS = (.5, 2)
# Camera chat first: no ring capture within this long of another capture.
YIELD_SECONDS = .5
MAX_JPEG_BYTES = 256*1024
MAX_HEADER_CHARS = 16384
# PreviewIngestor's capture-epoch identifier (store.identifier).
_EPOCH = re.compile(r"[A-Za-z0-9_-]{1,100}")
_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,47}")
# The commissioned client's own refusal when it gets no capture turn.
BUSY = "Commissioned camera capture busy"


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def causal_contract(data, header):
    """The shared camera contract the Lab's PreviewIngestor and camera chat use."""
    from home_shared.protocol import camera_frame
    return camera_frame(data, header)


def preview_frame(data, header, now, *, contract=causal_contract):
    """One accepted causal preview as the ring keeps it, else ValueError.

    PreviewIngestor's acceptance for the live causal client, without a
    recording: schema 3 only, the shared contract, the epoch identifier,
    timing fields, and freshness (observed_at <= now, now - observed_at +
    uncertainty <= 3 s, uncertainty <= 1 s, the receiver clock's included)."""
    if (not isinstance(data, bytes) or not 0 < len(data) <= MAX_JPEG_BYTES or not isinstance(header, str)
            or not 0 < len(header) <= MAX_HEADER_CHARS or not _finite(now)):
        raise ValueError("bounded JPEG and metadata required")
    padded = header + "=" * ((-len(header)) % 4)
    source = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    if not isinstance(source, dict) or type(source.get("schema")) is not int or source["schema"] != 3:
        raise ValueError("causal schema-3 preview required")
    contract(data, padded)
    epoch, sequence, timing = source.get("session"), source.get("sequence"), source.get("timing_generation")
    stamp, exposure = source.get("observed_at_unix_ns"), source.get("exposure_duration_seconds")
    uncertainty, receiver = source.get("uncertainty_seconds"), source.get("host_clock_uncertainty_seconds")
    if (not isinstance(epoch, str) or not _EPOCH.fullmatch(epoch) or type(sequence) is not int or sequence < 0
            or type(timing) is not int or timing < 0 or type(stamp) is not int or stamp <= 0
            or not _finite(uncertainty) or not 0 <= uncertainty <= 1 or not _finite(exposure)
            or not 0 < exposure <= uncertainty or not _finite(receiver) or not 0 <= receiver <= 1):
        raise ValueError("invalid preview timing")
    # int/int division rounds the rational once; converting the 64-bit stamp
    # to float first can disagree with the journal's exact original mapping.
    observed = stamp / 1_000_000_000
    # Schema 3 already validates this complete bound, including the SAME host clock.
    total = uncertainty
    if total > 1 or observed > now or now - observed + total > 3:
        raise ValueError("preview stale, future or clock uncertainty too large")
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format != "JPEG" or image.size != scene.IMAGE_SIZE or image.mode not in {"RGB", "L"}
                    or getattr(image, "n_frames", 1) != 1):
                raise ValueError()
            image.load()
    except Exception:
        raise ValueError("preview JPEG dimensions/type mismatch") from None
    sha256 = hashlib.sha256(data).hexdigest()
    return {"media_id": "ring-" + sha256, "frame_id": f"{epoch}-{timing}-{sequence}", "capture_epoch": epoch,
            "observed_at": observed, "available_at": now, "valid_until": observed + 3 - total,
            "size": len(data), "sha256": sha256, "data": data}


class FrameRing:
    """The newest accepted previews of one camera, in memory only.

    Not sized with len() on purpose: a pump treats an empty source as absent."""

    def __init__(self, *, seconds=RING_SECONDS, max_frames=MAX_FRAMES, clock=time.time, contract=causal_contract):
        if not _finite(seconds) or not 3 <= seconds <= RING_SECONDS:
            raise ValueError("bounded frame ring required")
        if type(max_frames) is not int or not scene.MAX_IMAGES <= max_frames <= MAX_FRAMES:
            raise ValueError("bounded frame ring required")
        self.seconds, self.max_frames = seconds, max_frames
        self.clock, self.contract = clock, contract
        self.lock = threading.Lock()
        self.frames = deque()
        self.subscribers = set()
        self.counts = {"accepted": 0, "duplicate": 0, "rejected": 0, "pruned": 0}

    def offer(self, data, header):
        """Accept one commissioned capture: True kept, False a duplicate.
        Raises ValueError when the frame is not acceptable (counted)."""
        now = self.clock()
        try:
            frame = preview_frame(data, header, now, contract=self.contract)
        except Exception:
            with self.lock:
                self.counts["rejected"] += 1
            raise ValueError("ring_frame_rejected") from None
        with self.lock:
            self._prune(now)
            if any(f["frame_id"] == frame["frame_id"] or f["sha256"] == frame["sha256"] for f in self.frames):
                self.counts["duplicate"] += 1
                return False
            self.frames.append(frame)
            ordered = sorted(self.frames, key=lambda f: (f["observed_at"], f["media_id"]))
            self.frames = deque(ordered[-self.max_frames:])
            self.counts["pruned"] += len(ordered) - len(self.frames)
            self.counts["accepted"] += 1
            wake = list(self.subscribers)
        for event in wake:
            event.set()
        return True

    def _prune(self, now):
        """Drop frames older than the ring window (caller holds the lock)."""
        keep = deque(f for f in self.frames if f["observed_at"] >= now - self.seconds)
        self.counts["pruned"] += len(self.frames) - len(keep)
        self.frames = keep

    def prune(self):
        with self.lock:
            self._prune(self.clock())

    def clear(self):
        with self.lock:
            self.counts["pruned"] += len(self.frames)
            self.frames = deque()

    def candidates(self, now):
        """Frames usable at now: causally available and inside the window."""
        with self.lock:
            self._prune(now)
            return [dict(f) for f in self.frames if f["available_at"] <= now and f["observed_at"] <= now]

    def select(self, session, now, *, count=3, span=3.0):
        """The pump's frames (qwen_live.spread over the ring), bytes included."""
        return qwen_live.spread(self.candidates(now), now, count=count, span=span)

    def subscribe(self, event):
        """A pump's wake event, set at every new frame."""
        with self.lock:
            self.subscribers.add(event)

    def unsubscribe(self, event):
        with self.lock:
            self.subscribers.discard(event)

    def snapshot(self):
        now = self.clock()
        with self.lock:
            newest = self.frames[-1]["observed_at"] if self.frames else None
            return {"frames": len(self.frames), "newest_age_s": None if newest is None else round(now - newest, 3),
                    "bytes": sum(f["size"] for f in self.frames), "counts": dict(self.counts)}


class CaptureTurns:
    """The process-local commissioned capture lock, observed (see the module
    docstring). Other callers keep the lock's exact behaviour; the ring's
    threads never wait for it and are refused while another capture holds,
    waits for or just ended its turn."""

    def __init__(self, inner, *, yield_seconds=YIELD_SECONDS, monotonic=time.monotonic):
        if not _finite(yield_seconds) or not 0 <= yield_seconds <= 2:
            raise ValueError("bounded capture yield required")
        self.inner, self.yield_seconds, self.monotonic = inner, yield_seconds, monotonic
        self.state = threading.Lock()
        self.ring_threads = set()
        self.waiting = 0
        self.held_by_other = False
        self.other_ended = None
        self.refused = 0

    def enter(self):
        """Mark the calling thread as a ring capture thread."""
        with self.state:
            self.ring_threads.add(threading.get_ident())

    def leave(self):
        with self.state:
            self.ring_threads.discard(threading.get_ident())

    def _busy(self):
        """Another capture holds, waits for or just ended its turn (caller holds state)."""
        recent = self.other_ended is not None and 0 <= self.monotonic() - self.other_ended < self.yield_seconds
        return self.waiting > 0 or self.held_by_other or recent

    def other_busy(self):
        with self.state:
            return self._busy()

    def acquire(self, blocking=True, timeout=-1):
        ring = threading.get_ident() in self.ring_threads
        if ring:
            with self.state:
                if self._busy():
                    self.refused += 1
                    return False
            # Never wait: a held lock means another capture is running.
            return self.inner.acquire(False)
        with self.state:
            self.waiting += 1
        acquired = False
        try:
            acquired = self.inner.acquire(blocking, timeout)
        finally:
            with self.state:
                self.waiting -= 1
                if acquired:
                    self.held_by_other = True
        return acquired

    def release(self):
        with self.state:
            if self.held_by_other:
                self.held_by_other = False
                self.other_ended = self.monotonic()
        self.inner.release()

    def locked(self):
        return self.inner.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


_INSTALL = threading.Lock()


class RingPreviewClient(chrony_preview_client.ChronyPreviewClient):
    """The ring's commissioned capture: ChronyPreviewClient's acceptance path
    (_capture) unchanged, but one attempt per slot and no wait for the
    process-local lock (see the module docstring). A rejected attempt raises
    as the commissioned client's last attempt would; the ring counts it and
    tries again one interval later."""

    def __call__(self):
        lock = chrony_preview_client._CAPTURE_LOCK
        if not lock.acquire(False):
            raise ValueError(BUSY)
        try:
            return self._capture()
        finally:
            lock.release()


def install_turns(module=None):
    """CaptureTurns around the commissioned client's process-local lock,
    installed once per process; returns it."""
    if module is None:
        module = chrony_preview_client
    with _INSTALL:
        turns = module._CAPTURE_LOCK
        if not isinstance(turns, CaptureTurns):
            turns = CaptureTurns(turns)
            module._CAPTURE_LOCK = turns
        return turns


_CONTROLLER_SCOPE_LOCK = threading.Lock()
_CONTROLLER_SCOPE = None


class ControllerCaptureScope:
    """A controller-lifetime capability for the actual shared capture permit.

    Merely importing the camera client or constructing a standalone producer
    cannot create this capability. An incumbent controller capture registers
    it; no LA model readiness or inference permission is part of its validity.
    """
    def __init__(self, owner, turns, *, runtime=False):
        self.owner = weakref.ref(owner)
        self.turns = turns
        self.runtime = runtime
        self.closed = False

    def require_turns(self):
        owner = self.owner()
        if (self.closed or owner is None or chrony_preview_client._CAPTURE_LOCK is not self.turns
                or (self.runtime and owner.closed)
                or (not self.runtime and (owner.stopped.is_set() or owner.turns is not self.turns))):
            raise ValueError('controller_capture_context_unavailable')
        return self.turns

    def close(self):
        self.closed = True


def register_controller_capture_scope(capture):
    """Called by controller composition, not by a standalone acquisition client.

    Explicit isolated capture turns used by synthetic fixtures remain usable
    by their original callers but cannot authorize the production default.
    """
    if not isinstance(capture, RingCapture):
        raise ValueError('incumbent controller capture required')
    try:
        existing = existing_controller_capture_scope()
    except ValueError:
        existing = None
    if existing is not None:
        if capture.turns is None:
            capture.turns = existing.require_turns()
        # This fallback does not own the existing controller's lifetime.
        return None
    if capture.turns is None:
        capture.turns = install_turns()
    if capture.turns is not chrony_preview_client._CAPTURE_LOCK or not isinstance(capture.turns, CaptureTurns):
        return None
    scope = ControllerCaptureScope(capture, capture.turns)
    global _CONTROLLER_SCOPE
    with _CONTROLLER_SCOPE_LOCK:
        _CONTROLLER_SCOPE = weakref.ref(scope)
    return scope


def register_controller_runtime_scope(runtime):
    """The existing managed controller anchors arbitration independently of LA."""
    from managed_pair import ManagedPair
    if not isinstance(runtime, ManagedPair) or runtime.closed:
        raise ValueError('incumbent managed controller runtime required')
    scope = ControllerCaptureScope(runtime, install_turns(), runtime=True)
    global _CONTROLLER_SCOPE
    with _CONTROLLER_SCOPE_LOCK:
        _CONTROLLER_SCOPE = weakref.ref(scope)
    return scope


def existing_controller_capture_scope():
    """Resolve existing controller authority without creating a new permit."""
    with _CONTROLLER_SCOPE_LOCK:
        scope = _CONTROLLER_SCOPE() if _CONTROLLER_SCOPE is not None else None
    if scope is None:
        raise ValueError('controller_capture_context_unavailable')
    scope.require_turns()
    return scope


class RingCapture:
    """The ring's capture thread (see the module docstring)."""

    def __init__(self, ring, fetch, gate, *, enabled=None, turns=None, interval=CAPTURE_INTERVAL,
                 monotonic=time.monotonic):
        low, high = CAPTURE_BOUNDS
        if not _finite(interval) or not low <= interval <= high:
            raise ValueError("at most 2 Hz ring capture required")
        if not callable(fetch):
            raise ValueError("commissioned capture client required")
        self.ring, self.fetch = ring, fetch
        self.gate = occupancy_gate.required(gate)
        self.enabled = enabled or (lambda: True)
        self.turns, self.interval, self.monotonic = turns, interval, monotonic
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.status = "idle"
        self.counts = {}
        self.last_capture_ms = None
        self.thread = threading.Thread(target=self._run, name="la-live-ring", daemon=True)

    def start(self):
        if self.turns is None:
            self.turns = install_turns()
        occupancy_gate.subscribe(self.gate, self.wake)
        self.thread.start()

    def stop(self, timeout=None):
        self.stopped.set()
        self.wake.set()
        occupancy_gate.unsubscribe(self.gate, self.wake)
        if timeout is not None and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def poke(self):
        """Look at the gate and enabled() again now (the perceiver's pump can
        use frames again), instead of at the next one-second check."""
        self.wake.set()

    def is_open(self):
        """Captures (and offered previews) are taken only while this is True."""
        if self.stopped.is_set():
            return False
        skip, _reading = occupancy_gate.check(self.gate)
        if skip is not None:
            return False
        try:
            return self.enabled() is True
        except Exception:
            return False

    def _count(self, reason):
        with self.lock:
            if reason in self.counts or len(self.counts) < 24:
                self.counts[reason] = self.counts.get(reason, 0) + 1
            else:
                self.counts["other"] = self.counts.get("other", 0) + 1

    def _sleep(self, seconds):
        """Until stopped, a gate edge, or seconds pass; True once stopped."""
        if self.wake.wait(max(0.0, seconds)):
            self.wake.clear()
        return self.stopped.is_set()

    def _run(self):
        self.turns.enter()
        try:
            due = self.monotonic()
            while not self.stopped.is_set():
                if not self.is_open():
                    # Vacant, unknown or switched off: no capture. Frames age
                    # out of the ring; an on-edge wakes this loop at once.
                    self.status = "closed"
                    self.ring.prune()
                    if self._sleep(1.0):
                        break
                    due = self.monotonic()
                    continue
                now = self.monotonic()
                if now < due:
                    if self._sleep(due - now):
                        break
                    continue
                if self.turns.other_busy():
                    self.status = "yielding"
                    self._count("yield_capture_busy")
                    due = now + self.interval
                    continue
                self._capture()
                # The idle gap: measured from the end of this capture, so the
                # lock is free for at least one interval after every ring capture.
                due = self.monotonic() + self.interval
        finally:
            self.turns.leave()
            self.status = "stopped"

    def _capture(self):
        started = self.monotonic()
        self.status = "capturing"
        try:
            data, header = self.fetch()
        except Exception as error:
            if isinstance(error, ValueError) and str(error) == BUSY:
                self._count("yield_capture_busy")
                self.status = "yielding"
            else:
                self._count("capture_failed:" + _category(error))
                self.status = "unavailable"
            return
        finally:
            self.last_capture_ms = round((self.monotonic() - started) * 1000, 1)
        if not self.is_open():
            self._count("discarded_gate_closed")
            return
        try:
            kept = self.ring.offer(data, header)
        except ValueError:
            self._count("frame_rejected")
            self.status = "unavailable"
            return
        self._count("captured" if kept else "duplicate")
        self.status = "current"

    def snapshot(self):
        with self.lock:
            counts = dict(self.counts)
        return {"status": self.status, "counts": counts, "last_capture_ms": self.last_capture_ms,
                "yield_refusals": getattr(self.turns, "refused", 0)}


def _category(error):
    """Content-free failure label: a short code or the exception type."""
    for value in (getattr(error, "category", None), getattr(error, "code", None)):
        if isinstance(value, str) and _CATEGORY.fullmatch(value):
            return value
    return type(error).__name__

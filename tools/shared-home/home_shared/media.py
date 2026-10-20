"""Bounded encoded-video primitives, deliberately without an enabled recorder.

A future RTSP demuxer must deliver complete video-only H.264 GOPs beginning at
an independently decodable IDR (including required SPS/PPS). No decoding,
transcoding, camera ownership, audio, filesystem writes or network occurs here.
Timestamps are monotonic observation seconds from a validated PTS mapping.
"""
from collections import deque
from dataclasses import dataclass
import math
import sys
from threading import Lock


MAX_BYTES = 32 * 1024 * 1024
MAX_RING_SECONDS = 30.0
MAX_CLIP_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class EncodedGop:
    """One complete independently-decodable Annex B H.264 GOP, no audio."""

    session: str
    start: float
    end: float
    data: bytes
    independently_decodable: bool = True

    def __post_init__(self):
        if (not isinstance(self.session, str) or not self.session or len(self.session) > 256 or
                not math.isfinite(self.start) or
                not math.isfinite(self.end) or self.end <= self.start or
                not isinstance(self.data, bytes) or not self.data):
            raise ValueError("Invalid encoded GOP")
        if not self.independently_decodable:
            raise ValueError("GOP must begin at an independently decodable IDR")


@dataclass(frozen=True)
class ClipSelection:
    session: str
    anchor: float
    requested_start: float
    requested_end: float
    gops: tuple[EncodedGop, ...]
    partial_reasons: tuple[str, ...]

    @property
    def complete(self):
        return not self.partial_reasons

    @property
    def byte_size(self):
        return sum(len(gop.data) for gop in self.gops)

    @property
    def actual_start(self):
        return self.gops[0].start if self.gops else None

    @property
    def actual_end(self):
        return self.gops[-1].end if self.gops else None

    def joined_annex_b(self):
        """Materialize at most one bounded video-only clip for a future muxer."""
        return b"".join(gop.data for gop in self.gops)


class GopRing:
    """Retain complete GOPs only; evict whole GOPs on either hard limit.

    The caller must bound its unfinished GOP buffer independently. An oversized
    GOP clears the ring and fails; it is never retained. A source reconnect
    resets the ring to avoid joining incompatible stream sessions.
    """

    def __init__(self, max_seconds=MAX_RING_SECONDS, max_bytes=MAX_BYTES):
        if (not math.isfinite(max_seconds) or not 0 < max_seconds <= MAX_RING_SECONDS or
                type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES):
            raise ValueError("Ring limits exceed admitted maximum")
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self._gops = deque()
        self._bytes = 0
        self._resident_bytes = 0
        self._session = None
        self._last_end = None
        self._lock = Lock()

    @property
    def byte_size(self):
        with self._lock:
            return self._bytes

    @property
    def duration(self):
        with self._lock:
            return self._gops[-1].end - self._gops[0].start if self._gops else 0.0

    @property
    def resident_bytes(self):
        """Conservative owned-GOP storage accounting, including metadata."""
        with self._lock:
            return self._resident_bytes + sys.getsizeof(self._gops)

    @staticmethod
    def _cost(gop):
        # Charge references/shared objects per GOP as well: over-accounting is
        # preferable to a tiny-payload stream allocating unbounded metadata.
        return (sys.getsizeof(gop) + sys.getsizeof(gop.data) + sys.getsizeof(gop.session) +
                sys.getsizeof(gop.start) + sys.getsizeof(gop.end))

    def append(self, gop):
        if not isinstance(gop, EncodedGop):
            raise ValueError("Expected encoded GOP")
        with self._lock:
            if gop.session != self._session:
                self._gops.clear()
                self._bytes = 0
                self._resident_bytes = 0
                self._session = gop.session
                self._last_end = None
            if self._last_end is not None and gop.start < self._last_end:
                raise ValueError("Out-of-order or overlapping GOP")
            self._last_end = gop.end
            if len(gop.data) > self.max_bytes or gop.end - gop.start > self.max_seconds:
                self._gops.clear()
                self._bytes = 0
                self._resident_bytes = 0
                raise ValueError("Single GOP exceeds ring bounds")
            if self._cost(gop) + sys.getsizeof(self._gops) > MAX_BYTES:
                self._gops.clear()
                self._bytes = self._resident_bytes = 0
                raise ValueError("Single GOP exceeds resident memory bound")
            self._gops.append(gop)
            self._bytes += len(gop.data)
            self._resident_bytes += self._cost(gop)
            while self._gops and (self._bytes > self.max_bytes or
                   self._resident_bytes + sys.getsizeof(self._gops) > MAX_BYTES or
                   self._gops[-1].end - self._gops[0].start > self.max_seconds):
                removed = self._gops.popleft()
                self._bytes -= len(removed.data)
                self._resident_bytes -= self._cost(removed)

    def select(self, *, session, anchor, event_received, now,
               mapping_validation_id, mapping_uncertainty_seconds):
        """Select anchor-5 .. anchor+10 without pretending partial data is full.

        Select after the desired post-roll plus GOP closure. This operation
        never waits or creates a clip job. The event receipt must be no more
        than ten seconds after the first detection, before occupancy debounce.
        The caller supplies an actual commissioned PTS mapping receipt ID.
        """
        if any(not math.isfinite(value) for value in
               (anchor, event_received, now, mapping_uncertainty_seconds)):
            raise ValueError("Invalid event timing")
        if (not isinstance(mapping_validation_id, str) or not mapping_validation_id.strip() or
                not 0 <= mapping_uncertainty_seconds <= 1):
            raise ValueError("Unvalidated or uncertain PTS mapping")
        if not anchor <= event_received <= now or event_received - anchor > 10:
            raise ValueError("Event arrived too late or from the future")
        wanted_start, wanted_end = anchor - 5, anchor + 10
        with self._lock:
            if session != self._session:
                candidates = ()
            else:
                candidates = tuple(g for g in self._gops
                                   if g.end > wanted_start and g.start < wanted_end and g.end <= now)
        reasons = []
        if now < wanted_end:
            reasons.append("post_roll_pending")
        selected = []
        byte_size = 0
        for gop in candidates:
            if selected and gop.start != selected[-1].end:
                # A joined elementary stream would hide the wall-clock gap.
                reasons.append("source_gap")
                break
            if byte_size + len(gop.data) > MAX_BYTES:
                reasons.append("byte_limit")
                break
            if gop.end - (selected[0].start if selected else gop.start) > MAX_CLIP_SECONDS:
                reasons.append("duration_limit")
                break
            selected.append(gop)
            byte_size += len(gop.data)
        if not selected:
            reasons.append("no_decodable_coverage")
        else:
            if selected[0].start + mapping_uncertainty_seconds > wanted_start:
                reasons.append("pre_roll_missing")
            if selected[-1].end - mapping_uncertainty_seconds < wanted_end:
                reasons.append("post_roll_missing")
        return ClipSelection(session, anchor, wanted_start, wanted_end,
                             tuple(selected), tuple(dict.fromkeys(reasons)))

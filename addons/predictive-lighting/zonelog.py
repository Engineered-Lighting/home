"""zonelog.py — zone-transition logic for the predictive-lighting logger
(Addendum 37 Phase 1).

Pure and testable: feed it parsed `frigate/events` messages plus a wall-clock
timestamp; it returns committed zone-transition records to append to
transitions.jsonl. No MQTT, no file I/O — that lives in app.py.

The Addendum 37 §5 data-cleaning is here:
  - most-specific-zone resolution (a sub-zone beats a whole_* aggregate),
  - a 600 ms commit debounce against single-frame zone flutter,
  - a <400 ms dwell flag (`dwell_suspect`) so the predictor can down-weight
    teleport-like records.

transitions.jsonl records, per track: an `__entry__ -> zone` record when a
track first commits a zone, `zone -> zone` records as it moves, and a
`zone -> __exit__` record when it ends. The entry/exit records let a
cross-camera room move (one Frigate track ends, a new one begins) be
stitched back into a single `zone -> zone` transition downstream
(predictor/stitch.py) — without the entry record those room-to-room moves
are invisible.

Every record also carries the person's raw normalised [x, y] foot-position,
so the transition graph stays re-derivable against any future zone redraw
rather than being locked to the Frigate zone labels recorded today.
"""

from __future__ import annotations

import datetime as _dt

# The zone graph (Addendum 37 §3), lowercased. whole_* aggregates
# geometrically contain their sub-zones, so a sub-zone is always the more
# specific (and preferred) committed zone.
AGGREGATE_ZONES = frozenset({
    "whole_living_room", "whole_dining_room", "whole_kitchen",
})
SUB_ZONES = frozenset({
    "front_door", "office", "sofa", "weights", "front_left",
    "dining_left", "dining_right", "island_right", "sink", "island_left",
    "workshop_zone", "e28",
})
KNOWN_ZONES = AGGREGATE_ZONES | SUB_ZONES

COMMIT_DEBOUNCE_S = 0.6    # a candidate zone must hold this long to commit
# A committed zone whose dwell is under this is flagged dwell_suspect — the
# person essentially transited it. Addendum 37 §5 nominally says 400 ms, but
# the 600 ms commit debounce means a committed zone is held >=600 ms by
# construction; 1.2 s is the reachable, faithful equivalent ("barely paused").
DWELL_SUSPECT_S = 1.2
EXIT = "__exit__"
ENTRY = "__entry__"

# Frigate detect resolution — every multi-zone camera runs 1280x720
# (spatial_model.json). The event box is in detect pixels; these normalise
# it to the [0,1] frame the zone polygons + light footprints share.
DETECT_W = 1280.0
DETECT_H = 720.0


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _position(after):
    """Person foot-position as a normalised [x, y] in [0,1] — the bounding
    box's bottom-centre. (None, None) when no usable box is present.

    Stored on every record so the transition graph can be re-derived against
    a future zone redraw instead of being locked to the Frigate zone labels
    recorded today (the agnostic-data principle)."""
    box = after.get("box")
    if isinstance(box, (list, tuple)) and len(box) >= 4:
        x = ((_f(box[0]) + _f(box[2])) / 2.0) / DETECT_W
        y = _f(box[3]) / DETECT_H
        return (round(min(1.0, max(0.0, x)), 4),
                round(min(1.0, max(0.0, y)), 4))
    return None, None


def tod_bucket(ts):
    """6 time-of-day buckets (Addendum 37 §6). Raw ts is also stored so the
    buckets can be re-derived (e.g. to routine-tied buckets) offline."""
    h = _dt.datetime.fromtimestamp(ts).hour
    if h < 6:
        return "night"
    if h < 10:
        return "morning"
    if h < 14:
        return "midday"
    if h < 18:
        return "afternoon"
    if h < 22:
        return "evening"
    return "late"


def dow_bucket(ts):
    """Day of week, 0=Mon .. 6=Sun. Stored raw for later conditioning."""
    return _dt.datetime.fromtimestamp(ts).weekday()


def normalize_zones(raw):
    """Frigate `current_zones` -> a sorted list of known lowercased zone
    slugs. Unknown / malformed entries are dropped (Frigate zone names are
    matched case-insensitively)."""
    out = set()
    if isinstance(raw, list):
        for z in raw:
            if isinstance(z, str):
                s = z.strip().lower()
                if s in KNOWN_ZONES:
                    out.add(s)
    return sorted(out)


def most_specific(zones, committed):
    """The single committed zone for one frame: a sub-zone beats a whole_*
    aggregate. Sticky — if `committed` is still present at the same
    specificity, keep it (suppresses doorway-straddle oscillation between
    two simultaneously-occupied zones). `zones` must be sorted so the
    tie-break is deterministic. Returns None when no known zone is present."""
    subs = [z for z in zones if z in SUB_ZONES]
    if subs:
        return committed if committed in subs else subs[0]
    aggs = [z for z in zones if z in AGGREGATE_ZONES]
    if aggs:
        return committed if committed in aggs else aggs[0]
    return None


class _Track:
    __slots__ = ("committed", "committed_since", "candidate",
                 "candidate_since", "identity", "prev", "prev2", "camera")

    def __init__(self):
        self.committed = None
        self.committed_since = None
        self.candidate = None
        self.candidate_since = None
        self.identity = None
        self.prev = None
        self.prev2 = None
        self.camera = None


class ZoneTracker:
    """Per-track committed-zone state machine.

    process(msg, now) returns a list of transition records — 0 normally, 1
    on a committed zone change or a track end.
    """

    def __init__(self):
        self._tracks = {}

    def active_tracks(self):
        return len(self._tracks)

    def process(self, msg, now):
        if not isinstance(msg, dict):
            return []
        etype = msg.get("type")
        after = msg.get("after")
        if not isinstance(after, dict):
            after = msg.get("before")
        if not isinstance(after, dict):
            return []
        tid = after.get("id")
        if not tid or after.get("label") != "person":
            return []

        tr = self._tracks.get(tid)
        if tr is None:
            tr = _Track()
            self._tracks[tid] = tr
        if after.get("camera"):
            tr.camera = after["camera"]
        sub = after.get("sub_label")
        if isinstance(sub, str) and sub.strip():
            tr.identity = sub.strip()

        speed = _f(after.get("average_estimated_speed"))
        angle = _f(after.get("velocity_angle"))
        score = _f(after.get("score"))
        px, py = _position(after)

        # Track end -> a terminal __exit__ transition.
        if etype == "end":
            rec = None
            if tr.committed is not None:
                rec = self._emit(tr, tid, EXIT, now, speed, angle,
                                 score, px, py)
            self._tracks.pop(tid, None)
            return [rec] if rec else []

        cand = most_specific(normalize_zones(after.get("current_zones")),
                             tr.committed)
        # No known zone, or no change -> clear any pending candidate.
        if cand is None or cand == tr.committed:
            tr.candidate = None
            tr.candidate_since = None
            return []

        # A different zone — debounce it against single-frame flutter.
        if cand != tr.candidate:
            tr.candidate = cand
            tr.candidate_since = now
            return []
        # epsilon: a candidate that has held for ~the debounce window counts
        # as met — float subtraction of wall-clock stamps is not exact.
        if now - tr.candidate_since < COMMIT_DEBOUNCE_S - 1e-6:
            return []

        tr.candidate = None
        tr.candidate_since = None
        # Every commit emits — including a track's first zone, as an
        # `__entry__ -> zone` record so a cross-camera room move can be
        # stitched to the preceding track's `-> __exit__`.
        return [self._emit(tr, tid, cand, now, speed, angle, score,
                           px, py)]

    def _emit(self, tr, tid, to_zone, now, speed, angle, score, px, py):
        from_zone = tr.committed if tr.committed is not None else ENTRY
        from_dwell = (now - tr.committed_since) if tr.committed_since else 0.0
        rec = {
            "ts": _dt.datetime.fromtimestamp(
                now, _dt.timezone.utc).isoformat(),
            "from_zone": from_zone,
            "to_zone": to_zone,
            "tod_bucket": tod_bucket(now),
            "dow_bucket": dow_bucket(now),
            "from_dwell_s": round(from_dwell, 2),
            "transit_speed_mps": round(speed, 2),
            "velocity_angle": round(angle, 1),
            "x": px,
            "y": py,
            "track_id": str(tid),
            "identity": tr.identity or "person_unknown",
            "prev_zone": tr.prev,
            "prev2_zone": tr.prev2,
            "dwell_suspect": tr.committed is not None
            and from_dwell < DWELL_SUSPECT_S,
            "camera": tr.camera,
            "frigate_score": round(score, 2),
        }
        tr.prev2 = tr.prev
        tr.prev = tr.committed
        tr.committed = to_zone
        tr.committed_since = now
        return rec

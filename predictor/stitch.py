"""stitch.py — reconstruct cross-camera (room-to-room) transitions from the
raw logger output (Addendum 37 §13).

The logger records one Frigate track at a time. A person crossing a camera
boundary ends one track and starts another, so a room-to-room move shows up
as a `<zone> -> __exit__` record followed a few seconds later by an
`__entry__ -> <zone>` record. stitch_transitions() pairs those by time
proximity into a single `<zone> -> <zone>` transition — the room-to-room
moves the Markov predictor needs to pre-light the room you are walking to.

clean_transitions() is the companion corpus-quality filter — it drops
low-confidence "ghost" tracks (a shadow, pet, or momentary mis-detect that
Frigate tracked briefly and weakly) before stitching, so the predictor
learns from real human movement rather than detector noise.
transitions_since() trims the corpus to data collected after a known
config change (e.g. the Frigate threshold fix) without deleting history.
"""

from __future__ import annotations

import datetime as _dt

ENTRY = "__entry__"
EXIT = "__exit__"
DEFAULT_MAX_GAP_S = 8.0      # walk time across a camera blind spot


def to_epoch(ts):
    """A record's ts -> epoch seconds. Accepts a float (the synthetic
    corpus) or an ISO-8601 string (the live logger)."""
    if isinstance(ts, (int, float)):
        return float(ts)
    return _dt.datetime.fromisoformat(str(ts)).timestamp()


def clean_transitions(records, min_score=0.0):
    """Corpus-quality filter — drop low-confidence "ghost" tracks before
    stitching / training.

    A real person produces a Frigate track that reaches a confident
    detection score at some point in its life; a shadow, pet, reflection,
    or momentary mis-detect produces a brief, weak "ghost" track that never
    does. This drops every record belonging to a track whose BEST
    frigate_score (across the whole track) stays below `min_score` — it
    removes the whole ghost, while keeping a genuine track intact even when
    individual frames dipped to a low score.

    `min_score <= 0` is a no-op. A record with no `frigate_score` (the
    synthetic corpus has none) is treated as fully confident, so synthetic
    data is never filtered. Records with no `track_id` are kept as-is.
    """
    if min_score <= 0:
        return list(records)
    best = {}
    for r in records:
        tid = r.get("track_id")
        if tid is None:
            continue
        sc = r.get("frigate_score")
        sc = 1.0 if sc is None else float(sc)
        if sc > best.get(tid, 0.0):
            best[tid] = sc
    return [r for r in records
            if r.get("track_id") is None
            or best.get(r.get("track_id"), 1.0) >= min_score]


def transitions_since(records, since=None):
    """Keep only records at or after `since` — an ISO-8601 string or an epoch
    float. `since=None` is a no-op.

    Used to train from a clean cutoff after a known regime change (e.g. the
    Frigate dining-camera threshold fix) WITHOUT deleting the older history:
    the raw corpus stays intact on disk, the trainer just ignores what came
    before the cutoff.
    """
    if since is None:
        return list(records)
    cutoff = to_epoch(since)
    return [r for r in records if to_epoch(r.get("ts")) >= cutoff]


def _stitched(exit_rec, entry_rec):
    """Synthesize the room-to-room transition from a paired exit + entry,
    attributed to the entry (the arrival)."""
    r = dict(entry_rec)
    r["from_zone"] = exit_rec.get("from_zone")
    r["prev_zone"] = exit_rec.get("prev_zone")
    r["prev2_zone"] = exit_rec.get("prev2_zone")
    r["stitched"] = True
    r["camera_from"] = exit_rec.get("camera")
    r["camera_to"] = entry_rec.get("camera")
    return r


def stitch_transitions(records, max_gap_s=DEFAULT_MAX_GAP_S):
    """Raw logger records -> a clean transition list for the predictor.

    - within-camera `A -> B` records pass through unchanged;
    - a `X -> __exit__` followed within `max_gap_s` by an `__entry__ -> Y`
      becomes a single stitched `X -> Y` room move;
    - a `X -> __exit__` with no following entry stays `X -> __exit__`
      (a genuine exit — left the house / a camera blind spot);
    - an `__entry__ -> Y` with no recent preceding exit is dropped (a fresh
      appearance is not a transition).

    Every returned record has a real zone as `from_zone`. The result is
    sorted chronologically.
    """
    recs = sorted(records, key=lambda r: to_epoch(r.get("ts")))
    out = []
    pending = None                          # an unmatched `X -> __exit__`
    for r in recs:
        ts = to_epoch(r.get("ts"))
        # a pending exit no entry will pair with -> a genuine exit
        if pending is not None and ts - to_epoch(pending["ts"]) > max_gap_s:
            out.append(pending)
            pending = None
        fz, tz = r.get("from_zone"), r.get("to_zone")
        if fz == ENTRY:
            if pending is not None:
                out.append(_stitched(pending, r))
                pending = None
            # an unmatched entry is a fresh appearance — dropped
        elif tz == EXIT:
            if pending is not None:         # back-to-back exits
                out.append(pending)
            pending = r
        else:
            out.append(r)                   # within-camera `A -> B`
    if pending is not None:
        out.append(pending)
    return sorted(out, key=lambda r: to_epoch(r.get("ts")))

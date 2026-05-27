"""M20 — collapse override_event bursts into one logical "manual adjustment
session" where the final value wins.

After M19 changed manual-detection automations from mode:single → mode:queued
max:20, a rapid slider scrub on one light writes 5-15 override_event records
to JSONL — one per trigger fire as the brightness attribute settles through
the burst window. Each record is real evidence, but treated independently
they over-count what is logically ONE user adjustment.

This module collapses a chronologically-ordered list of override_event
records into a list of sessions, where consecutive events on the SAME light
within `session_window_s` are merged into a single record. The merged
record carries the LAST (final) event's values — "final value wins" —
plus session metadata so downstream consumers can see how many raw events
were folded in and the full observed-pct path.

Read-path implementation by design: the JSONL on disk is the durable
evidence record; this collapse is applied at query time so the underlying
log stays lossless. Tunable via the `session_window_s` parameter (default
10 s — covers a typical slider scrub plus the 3-s settle window of the
manual-detection trigger, with headroom for hesitation).

`pending_preference` is unaffected — it's already deduped at the
capture-on-vacant gate and remains the durable preference label.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_SESSION_WINDOW_S = 10.0

# Fields that "follow the final event" — when we merge a later event into
# an existing session, these get overwritten so the session's view is
# always the latest snapshot. context_id specifically tracks the LAST
# override's context_id, matching what capture-on-vacant's
# `related_override_context_id` will point at.
_FOLLOW_FINAL_FIELDS = (
    "actual_pct",
    "predicted_pct",
    "delta_pct",
    "light_state",
    "predictions_by_zone",
    "state",
    "profile",
    "tv_playing",
    "asleep",
    "night_safe",
    "user_at_home",
    "any_occupied",
    "anticipated_room",
    "shadow_mode",
    "source_user_id",
    "debug_match_reason",
    "evidence_source",
    "context_id",
    "ts",
)


def _parse_ts(ts: Any) -> float | None:
    """Parse an ISO-8601 ts string (or float epoch) into epoch seconds.
    Returns None on any failure — caller treats None as "can't merge,
    start a new session" to be safe."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return float(ts)
        except (TypeError, ValueError):
            return None
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None
    return None


def collapse_into_sessions(
    events: list[dict[str, Any]],
    session_window_s: float = DEFAULT_SESSION_WINDOW_S,
) -> list[dict[str, Any]]:
    """Collapse a chronologically-ordered list of override_event records
    into a list of sessions.

    Two events join the same session if:
      1. they have the same `light_entity`, AND
      2. the later event's `ts` is within `session_window_s` of the
         current tail of that light's session.

    "Final value wins": the session record is a copy of the LAST event
    in the burst, with these added fields:
      - session_event_count   : int, total raw events folded in
      - session_first_ts      : str, ts of the FIRST event
      - session_last_ts       : str, ts of the LAST event (== record's ts)
      - session_first_context_id : str, context_id of the FIRST event
      - session_last_context_id  : str, context_id of the LAST event (== record's context_id)
      - session_observed_path : list[int|None], chronological actual_pct values
      - session_collapsed     : bool, True iff session_event_count > 1

    Input is expected oldest-first. Output is also oldest-first. Records
    that don't carry a parseable `ts` start a new session each (safer
    than dropping evidence or merging without time anchor).
    """
    if not events:
        return []

    sessions: list[dict[str, Any]] = []
    last_idx_by_light: dict[Any, int] = {}

    for ev in events:
        light = ev.get("light_entity")
        ts_epoch = _parse_ts(ev.get("ts"))
        idx = last_idx_by_light.get(light)
        joined = False

        if idx is not None and ts_epoch is not None:
            tail = sessions[idx]
            tail_ts = _parse_ts(tail.get("ts"))
            if tail_ts is not None and (ts_epoch - tail_ts) <= session_window_s:
                # Merge: count++, append observed, overwrite the
                # "follow-final" fields so the session view is always the
                # latest snapshot.
                tail["session_event_count"] = (
                    int(tail.get("session_event_count", 1)) + 1
                )
                tail["session_observed_path"] = (
                    list(tail.get("session_observed_path", []))
                    + [ev.get("actual_pct")]
                )
                tail["session_last_ts"] = ev.get("ts")
                tail["session_last_context_id"] = ev.get("context_id")
                for k in _FOLLOW_FINAL_FIELDS:
                    if k in ev:
                        tail[k] = ev[k]
                tail["session_collapsed"] = True
                joined = True

        if not joined:
            new_sess = dict(ev)
            new_sess["session_event_count"] = 1
            new_sess["session_first_ts"] = ev.get("ts")
            new_sess["session_last_ts"] = ev.get("ts")
            new_sess["session_first_context_id"] = ev.get("context_id")
            new_sess["session_last_context_id"] = ev.get("context_id")
            new_sess["session_observed_path"] = [ev.get("actual_pct")]
            new_sess["session_collapsed"] = False
            sessions.append(new_sess)
            last_idx_by_light[light] = len(sessions) - 1

    return sessions

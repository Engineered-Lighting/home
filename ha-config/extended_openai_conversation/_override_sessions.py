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

M22 — intent vs automation aftermath. Live data showed sessions where the
trailing values are pilot-driven turn-offs (path ends [..., 58, 58, 0, 0]).
Treating the literal final 0 as "the user's choice" pollutes learning
aggregation. M22 adds an intent classifier that splits the path into
(user_path, tail) where the trailing 0s after a stable nonzero run are
marked as automation aftermath. The collapsed session now carries BOTH:
  - session_final_actual_pct  — literal last observed value (observability)
  - session_user_landed_pct   — last value BEFORE the off-tail (intent)
  - learning_value_pct        — preferred for aggregation (intent if
                                 automation_tail_detected else final)
plus `automation_tail_detected`, `session_tail_path`, `intent_confidence`,
and `learning_value_source`. The session_observed_path stays unchanged for
auditability — the new fields are added alongside, not in place of.
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


def _is_off_value(v: Any) -> bool:
    """A path entry counts as "off" if it's 0, None, or missing.
    Anything else (including 1-pct dim) is treated as "user is touching
    a light that's on". 0% is the only HA value indicating a light_state
    of 'off' (the manual-detection automation emits `actual_pct: 0` for
    off lights), so 0 is a robust signal for automation turn-off."""
    if v is None:
        return True
    try:
        return int(v) == 0
    except (TypeError, ValueError):
        return True  # un-coercible → safer to treat as off than as a real pct


def _split_user_path_and_tail(
    path: list[Any],
) -> tuple[list[Any], list[Any]]:
    """Walk from the end of `path` backwards counting trailing 0/off
    entries. Returns (user_path, tail).

    Edge cases:
      - All-off path → (path, []). The whole session is an intentional
        off (no automation tail, since there's no preceding nonzero run).
      - No trailing zeros → (path, []). All the activity was the user.
      - Mixed path ending in zeros after a nonzero run → split.
    """
    if not path:
        return [], []
    # Find the index of the LAST non-off value; everything strictly after
    # that is the trailing-off tail.
    last_nonzero_idx = -1
    for i, v in enumerate(path):
        if not _is_off_value(v):
            last_nonzero_idx = i
    if last_nonzero_idx < 0:
        # Whole path is off → intentional off session, no automation tail
        return list(path), []
    if last_nonzero_idx == len(path) - 1:
        # Last value is nonzero → no trailing off-tail
        return list(path), []
    return list(path[: last_nonzero_idx + 1]), list(path[last_nonzero_idx + 1:])


def _classify_session_intent(
    path: list[Any],
    final_actual_pct: Any,
    final_light_state: Any = None,
) -> dict[str, Any]:
    """M22 — distinguish user intent from automation aftermath.

    Heuristic v1:
      - Trailing 0/off values in the path are candidate automation tail.
      - If there's a stable nonzero user run before the tail → mark
        automation_tail_detected=true and prefer that user value for
        learning.
      - All-off session → intentional off; learning value is 0.
      - Path ending nonzero (no tail) → final value wins (legacy M20).

    `intent_confidence`:
      - "high": no tail (path ended nonzero, clear final) OR tail with
        a stable user run (≥ 2 identical values at the tail of user_path).
      - "medium": tail detected, user_path has ≥ 2 values that differ
        (user was still scrubbing when automation cut in).
      - "low": tail detected with only 1 user-side event before it
        (could be a glitch — not enough data to be confident).

    `learning_value_source`:
      - "user_landed"  — preferred user value before automation tail
      - "final_actual" — no tail; final actual_pct is the answer
      - "off_session"  — whole session was off; user intended off
    """
    user_path, tail = _split_user_path_and_tail(path)

    # All-zero (or empty) session → intentional off.
    if not user_path or all(_is_off_value(v) for v in user_path):
        return {
            "session_final_actual_pct": int(final_actual_pct or 0),
            "session_user_landed_pct": 0,
            "automation_tail_detected": False,
            "session_tail_path": [],
            "intent_confidence": "high",
            "learning_value_pct": 0,
            "learning_value_source": "off_session",
        }

    # No automation tail → no second-guessing; final is the answer.
    if not tail:
        # Prefer the explicit final_actual_pct; fall back to the last
        # path value if final_actual_pct is None / missing (defensive
        # against shapes where actual_pct didn't reach the collapser).
        try:
            if final_actual_pct is not None:
                final_int = int(final_actual_pct)
            else:
                final_int = int(user_path[-1]) if user_path[-1] is not None else 0
        except (TypeError, ValueError):
            final_int = 0
        return {
            "session_final_actual_pct": final_int,
            "session_user_landed_pct": final_int,
            "automation_tail_detected": False,
            "session_tail_path": [],
            "intent_confidence": "high",
            "learning_value_pct": final_int,
            "learning_value_source": "final_actual",
        }

    # Automation tail detected. Pick the last user-side value.
    user_landed_raw = user_path[-1]
    try:
        user_landed = int(user_landed_raw) if user_landed_raw is not None else 0
    except (TypeError, ValueError):
        user_landed = 0

    # Confidence: stable user run (last 2 identical) is "high"; a single
    # user event is "low"; otherwise "medium".
    if len(user_path) >= 2 and user_path[-1] == user_path[-2]:
        confidence = "high"
    elif len(user_path) >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    try:
        final_int = int(final_actual_pct) if final_actual_pct is not None else 0
    except (TypeError, ValueError):
        final_int = 0

    return {
        "session_final_actual_pct": final_int,
        "session_user_landed_pct": user_landed,
        "automation_tail_detected": True,
        "session_tail_path": tail,
        "intent_confidence": confidence,
        "learning_value_pct": user_landed,
        "learning_value_source": "user_landed",
    }


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

    # M22 — classify intent vs automation aftermath for each session.
    # The classifier reads the session's final actual_pct + path and
    # attaches session_final_actual_pct / session_user_landed_pct /
    # automation_tail_detected / session_tail_path / intent_confidence /
    # learning_value_pct / learning_value_source. Pure additive — the
    # legacy M20 fields (session_observed_path, session_event_count,
    # final-event-wins actual_pct, etc.) stay untouched.
    for sess in sessions:
        try:
            classification = _classify_session_intent(
                sess.get("session_observed_path") or [],
                sess.get("actual_pct"),
                sess.get("light_state"),
            )
            sess.update(classification)
        except Exception:  # pylint: disable=broad-except
            # Be defensive — never poison the response if classifier hits
            # an unexpected shape. The legacy M20 view still works.
            pass

    return sessions

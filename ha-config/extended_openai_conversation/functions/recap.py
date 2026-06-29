"""Daily-recap composition tool (Addendum 27 / Section 6 / M2).

Single tool: recap(window_hours)

Composes data from THREE sources into one compact JSON payload the
agent can turn into a 1-paragraph narrative without thrashing through
many tool calls:

  1. Frigate clip events (via find_clips internals) — counts per room,
     per identified person, plus the 3 most "interesting" clips
     (recognized faces preferred).
  2. World-state aggregator's per-person history (when available) —
     last-seen room + camera per known identity.
  3. HA Recorder history for binary_sensor.*_person_occupancy across
     known rooms — minutes-occupied per room within the window.

Why a composition tool instead of letting the agent chain calls:
  - Bounded latency — one tool call (~200-800ms total) vs N×2-5s
  - Deterministic shape — easier for the agent to summarize
  - Token-bounded — payload caps at ~3KB regardless of window length
  - Works whether the user asks "what happened?" or "did anyone come
    over?" — the agent receives the same compact summary either way

Return shape:
  {
    "data": {
      "window_hours":   <int>,
      "from":           "<ISO ts>",
      "to":             "<ISO ts now>",
      "rooms":          [{
        "room":             "kitchen",
        "clips":            12,
        "minutes_occupied": 42,        # null if recorder unavailable
        "identified":       [{"name":"Marcelo","seen":8}],
      }],
      "people":         [{"name":"Marcelo","total_seen":8,
                          "last_room":"kitchen","last_seen_ago_s":12}],
      "top_clips":      [{...same shape as find_clips entries...}],
      "summary_hints":  ["Marcelo in kitchen 42 min",
                         "Driveway car at 2:14pm",
                         "Living room empty since 10am"],
    },
    "suggested_phrasing": "Mostly quiet — Marcelo in the kitchen ~40 minutes; one car on the driveway at 2pm.",
    "freshness":       "fresh" | "stale" | "none",
  }

Defaults:
  - window_hours = 6 (works for "today" if asked midday, "last hours"
    otherwise; agent can override with 24 for "today" or 1 for
    "the last hour")
  - max_clips = 20 internally; top_clips capped at 4 in the payload
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .base import Function
from .frigate import FrigateFunction, _iso_from_unix

_LOGGER = logging.getLogger(__name__)

DEFAULT_WINDOW_H = 6
MAX_WINDOW_H = 24 * 7    # 1 week ceiling
FETCH_CLIPS_LIMIT = 20   # internal cap; top_clips trimmed below
TOP_CLIPS_OUTPUT = 4
RECAP_TIMEOUT_S = 8.0


class RecapFunction(Function):
    """Single-tool dispatcher mirroring FrigateFunction/RegistryFunction."""

    def __init__(self) -> None:
        super().__init__(vol.Schema({vol.Required("name"): str}))
        # Reuse FrigateFunction internals — it does the actual HTTP +
        # normalization work; we only re-aggregate the result.
        self._frigate = FrigateFunction()

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        name = function_config["name"]
        if name == "recap":
            return await self._recap(hass, arguments)
        return {"error": f"unknown recap tool: {name}"}

    # ─── Tool: recap ─────────────────────────────────────────────────

    async def _recap(self, hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
        wh_raw = arguments.get("window_hours")
        try:
            window_h = int(wh_raw) if wh_raw is not None and wh_raw != "" else DEFAULT_WINDOW_H
        except (TypeError, ValueError):
            window_h = DEFAULT_WINDOW_H
        window_h = max(1, min(window_h, MAX_WINDOW_H))

        now_s = time.time()
        from_s = now_s - window_h * 3600

        # 1. Pull clip events via the existing find_clips machinery.
        # No `search` filter — we want chronological context. Cap to
        # FETCH_CLIPS_LIMIT so we don't pull thousands of events for a
        # week-long window.
        clip_result = await self._frigate._find_clips({
            "window_min": window_h * 60,
            "limit": FETCH_CLIPS_LIMIT,
        })
        clips = (clip_result.get("data") or {}).get("clips") or []
        clip_error = clip_result.get("error")

        # 2. Per-room + per-person aggregations.
        room_clips: dict[str, int] = defaultdict(int)
        room_identified: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        person_total: dict[str, int] = defaultdict(int)
        person_last: dict[str, dict[str, Any]] = {}
        for c in clips:
            room = c.get("camera") or "unknown"
            room_clips[room] += 1
            sub = c.get("sub_label")
            if sub:
                room_identified[room][sub] += 1
                person_total[sub] += 1
                # Keep the MOST RECENT sighting per person (clips arrive
                # newest-first from Frigate, but be defensive).
                prev = person_last.get(sub)
                clip_ts_iso = c.get("ts") or ""
                if prev is None or clip_ts_iso > (prev.get("ts") or ""):
                    person_last[sub] = {
                        "ts": clip_ts_iso, "room": room,
                    }

        # 3. Room occupancy minutes via HA recorder. Pull
        # binary_sensor.<room>_person_occupancy for every room that has
        # a clip OR is in the world_state aggregator's known rooms.
        # Best-effort — if recorder is unavailable, minutes_occupied
        # is left null.
        minutes_by_room: dict[str, int] = {}
        try:
            minutes_by_room = await self._occupancy_minutes(hass, list(room_clips.keys()), from_s, now_s)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("recap: recorder unavailable: %s", e)

        # 4. Build the compact output.
        rooms_out = []
        all_room_keys = sorted(set(room_clips.keys()) | set(minutes_by_room.keys()))
        for room in all_room_keys:
            ident_list = [
                {"name": name, "seen": cnt}
                for name, cnt in sorted(
                    room_identified.get(room, {}).items(),
                    key=lambda x: -x[1],
                )
            ]
            rooms_out.append({
                "room":             room,
                "clips":            room_clips.get(room, 0),
                "minutes_occupied": minutes_by_room.get(room),  # None if recorder n/a
                "identified":       ident_list,
            })
        # Sort rooms by activity (clips + minutes), most active first.
        rooms_out.sort(
            key=lambda r: -((r.get("clips") or 0) + (r.get("minutes_occupied") or 0))
        )

        people_out = []
        for name, total in sorted(person_total.items(), key=lambda x: -x[1]):
            last = person_last.get(name) or {}
            last_iso = last.get("ts") or ""
            people_out.append({
                "name":            name,
                "total_seen":      total,
                "last_room":       last.get("room"),
                "last_seen_ago_s": _seconds_since(last_iso, now_s),
            })

        # Top clips: prefer ones with a recognized face. Among those,
        # newest-first. Cap to TOP_CLIPS_OUTPUT.
        clips_with_face = [c for c in clips if c.get("sub_label")]
        clips_no_face = [c for c in clips if not c.get("sub_label")]
        top_clips = (clips_with_face + clips_no_face)[:TOP_CLIPS_OUTPUT]

        summary_hints = self._compose_hints(rooms_out, people_out, top_clips)
        suggested = self._compose_phrasing(rooms_out, people_out, window_h, clip_error)

        # Freshness from clip ages.
        freshness = "none"
        if clips:
            newest_iso = clips[0].get("ts") or ""
            ago = _seconds_since(newest_iso, now_s) or 1e9
            freshness = "fresh" if ago < 300 else "stale" if ago < 6 * 3600 else "old"

        out: dict[str, Any] = {
            "data": {
                "window_hours":   window_h,
                "from":           _iso_from_unix(from_s),
                "to":             _iso_from_unix(now_s),
                "rooms":          rooms_out,
                "people":         people_out,
                "top_clips":      top_clips,
                "summary_hints":  summary_hints,
            },
            "suggested_phrasing": suggested,
            "freshness":          freshness,
        }
        if clip_error:
            out["data"]["clip_source_error"] = clip_error
        return out

    # ─── Recorder helper ─────────────────────────────────────────────

    async def _occupancy_minutes(
        self,
        hass: HomeAssistant,
        rooms: list[str],
        from_s: float,
        to_s: float,
    ) -> dict[str, int]:
        """For each room, sum minutes that
        binary_sensor.<room>_person_occupancy was 'on' in the window.
        Returns {room: minutes_int}. Skips rooms with no recorder data
        or where the entity doesn't exist."""
        from datetime import datetime, timezone, timedelta
        try:
            from homeassistant.components import recorder
            from homeassistant.components.recorder import history as recorder_history
        except ImportError:
            return {}
        entity_ids = [f"binary_sensor.{r}_person_occupancy" for r in rooms]
        if not entity_ids:
            return {}
        start_dt = datetime.fromtimestamp(from_s, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(to_s, tz=timezone.utc)

        def _read():
            with recorder.util.session_scope(hass=hass, read_only=True) as session:
                return recorder_history.get_significant_states_with_session(
                    hass, session, start_dt, end_dt, entity_ids,
                    None, True, True, True, True,
                )

        result = await recorder.get_instance(hass).async_add_executor_job(_read)
        # result: { entity_id: [LazyState, ...] } where each item has .state + .last_changed
        out: dict[str, int] = {}
        for entity_id, rows in (result or {}).items():
            room = entity_id.replace("binary_sensor.", "").replace("_person_occupancy", "")
            on_seconds = 0.0
            on_since: float | None = None
            for s in rows:
                lc = s.last_changed
                if lc is None:
                    continue
                # Convert HA's datetime to unix seconds
                try:
                    ts = lc.timestamp() if hasattr(lc, "timestamp") else float(lc)
                except Exception:
                    continue
                state = getattr(s, "state", None)
                if state == "on" and on_since is None:
                    on_since = max(ts, from_s)
                elif state != "on" and on_since is not None:
                    on_seconds += max(0.0, ts - on_since)
                    on_since = None
            # If the room is currently on at end-of-window, close out the segment
            if on_since is not None:
                on_seconds += max(0.0, to_s - on_since)
            if on_seconds > 0:
                out[room] = int(on_seconds // 60)
        return out

    # ─── Phrasing helpers ────────────────────────────────────────────

    def _compose_hints(
        self,
        rooms: list[dict[str, Any]],
        people: list[dict[str, Any]],
        clips: list[dict[str, Any]],
    ) -> list[str]:
        """Short bullet-style hints the agent can quote or paraphrase.
        Capped at 6 to keep payload tight."""
        hints: list[str] = []
        for r in rooms[:3]:
            mins = r.get("minutes_occupied")
            cnt = r.get("clips") or 0
            ident = r.get("identified") or []
            who = ident[0]["name"] if ident else None
            if mins is not None and mins > 0 and who:
                hints.append(f"{who} in {r['room']} {mins} min")
            elif mins is not None and mins > 0:
                hints.append(f"{r['room']} occupied {mins} min")
            elif cnt > 0 and who:
                hints.append(f"{who} on {r['room']} ({cnt} clips)")
            elif cnt > 0:
                hints.append(f"{cnt} clip{'s' if cnt > 1 else ''} on {r['room']}")
        for p in people[:2]:
            ago = p.get("last_seen_ago_s")
            if ago is not None and ago < 600 and p.get("last_room"):
                hints.append(f"{p['name']} last seen in {p['last_room']} ~{int(ago // 60)}m ago")
        # If totally quiet, say so explicitly
        if not hints:
            hints.append("nothing notable in this window")
        return hints[:6]

    def _compose_phrasing(
        self,
        rooms: list[dict[str, Any]],
        people: list[dict[str, Any]],
        window_h: int,
        clip_error: str | None,
    ) -> str:
        if clip_error:
            return f"Recap unavailable — Frigate error ({clip_error})."
        if not rooms and not people:
            return f"Pretty quiet over the last {window_h}h — nothing notable."
        bits: list[str] = []
        # Lead with the most-active room.
        if rooms:
            r = rooms[0]
            ident = r.get("identified") or []
            who = ident[0]["name"] if ident else None
            mins = r.get("minutes_occupied")
            if who and mins:
                bits.append(f"{who} in the {r['room']} ~{mins} min")
            elif who:
                bits.append(f"{who} active in the {r['room']}")
            elif mins:
                bits.append(f"the {r['room']} was occupied ~{mins} min")
            else:
                bits.append(f"some activity in the {r['room']}")
        # Add a second person if more than one identified
        if len(people) > 1:
            bits.append(f"{people[1]['name']} also seen")
        # If multiple rooms had activity, mention them
        extra_rooms = [r["room"] for r in rooms[1:3] if (r.get("clips") or 0) > 0]
        if extra_rooms:
            bits.append("also " + ", ".join(extra_rooms))
        return ("Over the last " + str(window_h) + "h: " + "; ".join(bits) + ".") if bits else (
            f"Pretty quiet over the last {window_h}h."
        )


# ── small helpers ─────────────────────────────────────────────────────

def _seconds_since(iso_z: str, now_s: float) -> int | None:
    """Parse a Z-suffixed ISO ts → seconds since (clamped >= 0)."""
    if not iso_z:
        return None
    try:
        from datetime import datetime, timezone
        clean = iso_z.rstrip("Z")
        # Tolerate both "2026-05-17T22:00:00" and the same with fractional secs
        # (find_clips strips fractions but be defensive).
        if "." in clean:
            clean = clean.split(".", 1)[0]
        dt = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
        delta = now_s - dt.timestamp()
        return max(0, int(delta))
    except Exception:
        return None

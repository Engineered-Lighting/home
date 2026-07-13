"""The OpenAI Conversation integration."""

from __future__ import annotations

import json
import logging
from functools import wraps
from pathlib import Path

from aiohttp import web
from openai import AsyncClient
from openai._exceptions import AuthenticationError, OpenAIError

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_PROVIDER,
    DEFAULT_SKIP_AUTHENTICATION,
    DOMAIN,
)
from .helpers import get_authenticated_client
from .identity_store import IdentityStore
from .lighting_evidence import all_source_status, export_source_lines
from .frigate_sync import (
    auto_seed_identities_from_frigate,
    drain_pending_writes,
    probe_frigate_capabilities,
)
from .services import async_setup_services
from .template import async_setup_templates, async_unload_templates
from .world_state import WorldStateAggregator

_LOGGER = logging.getLogger(__name__)

_ROUTING_LOG_PATH = Path("/config/external_routing.log")

# Addendum 38 Phase 1 — the light-footprint model. The user hand-draws
# each light's footprint as a polygon in the Home app's /spatial drawer;
# the POST writes each light's footprint into this file. The skeleton is
# scaffolded by tools/init-spatial-model.py.
_SPATIAL_MODEL_PATH = Path("/config/spatial_model.json")

# 3D apartment spatial model (apartment_model v1) — the unified Z-up metric
# document behind the Home app's /apartment screen: zones, devices, camera
# calibrations, light influence polygons. Distinct from spatial_model (the
# legacy per-camera light-footprint store) — the two coexist. Single-writer
# (the app) with revision-based optimistic concurrency; the spatial-tracker
# service polls GET.
_APARTMENT_MODEL_PATH = Path("/config/apartment_model.json")

# Phase 4A.24 — custom avatars per identity. Files live on disk; presence
# is the boolean source of truth. Deleting the file reverts to initials.
# Client (Tauri) does the crop + resize before POSTing; server just
# validates size + writes bytes.
_AVATARS_DIR = Path("/config/extended_openai_conversation/avatars")
_AVATAR_MAX_BYTES = 1 * 1024 * 1024  # 1 MB hard cap on upload (post-resize)
_AVATAR_ALLOWED_CONTENT_TYPES = frozenset({
    "image/jpeg", "image/jpg", "image/png", "image/webp",
})

# CORS for cross-origin REST clients (Tauri Home app, browser tools).
#
# The Tauri WebView2 runs at `http://tauri.localhost`; any cross-origin
# request the app sends with `Authorization: Bearer …` is a non-simple
# request and triggers a browser CORS preflight. PATCH/DELETE with a
# JSON body trigger preflight unconditionally.
#
# Setup: HA's `aiohttp_cors` middleware handles both preflight and the
# `Access-Control-*` response headers on the actual response. It only
# acts on views that opt in with `cors_allowed = True` AND only when
# `http: cors_allowed_origins` is configured in `configuration.yaml`.
# The middleware sets the response header based on the request's
# `Origin`, so views MUST NOT set `Access-Control-Allow-Origin`
# themselves — duplicate headers make `aiohttp_cors` raise and drop
# the connection, surfacing in the browser as "Failed to fetch".
#
# Operator config required (`configuration.yaml`):
#
#     http:
#       cors_allowed_origins:
#         - "http://tauri.localhost"
#
# Trust model: `requires_auth = True` is unchanged. CORS controls
# which origins can READ a response; the bearer token still gates
# access. See `tools/enable-cors-for-tauri.md`.


class CORSHomeAssistantView(HomeAssistantView):
    """HomeAssistantView opted in to HA's CORS middleware.

    `cors_allowed = True` registers the view with `aiohttp_cors`, which
    handles OPTIONS preflight and adds the `Access-Control-*` response
    headers based on the request's `Origin` and the operator's
    `http: cors_allowed_origins` setting in `configuration.yaml`.

    Do not override `json()` to add CORS headers here — the middleware
    will collide with any pre-set `Access-Control-Allow-Origin` and
    abort the response.

    All integration views inherit from this instead of HomeAssistantView."""
    cors_allowed = True


class RoutingLogView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/routing_log?tail=N

    Returns the last N (default 20, max 200) routing-decision entries
    from /config/external_routing.log as parsed JSON. Used by the Home
    app's /route-log slash command for bulk-paste-back analysis.

    Auth: standard HA bearer token (requires_auth = True).
    Returns {"entries": []} if the log file doesn't exist yet —
    never 500s. Sensitive content (user text, response snippets) is
    captured per the EXTERNAL_ROUTING_LOG_MODE env var; this endpoint
    just surfaces whatever the integration already wrote.
    """

    url = "/api/extended_openai_conversation/routing_log"
    name = "api:extended_openai_conversation:routing_log"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        # Clamp tail to [1, 200] — caller can ask for more or less,
        # but we cap to keep responses bounded.
        try:
            tail = int(request.query.get("tail", "20"))
        except (TypeError, ValueError):
            tail = 20
        tail = max(1, min(tail, 200))
        # M5 (Addendum 27 Section 6 Milestone 5): optional ?conv_id=
        # filter so the explainability drawer can pull every routing-
        # log entry (decision + tool_calls + perception_dispatch) that
        # shares one conversation_id. We scan a larger window (up to
        # 1000 lines) when filtering since the matching entries may be
        # interspersed with other turns.
        conv_id = (request.query.get("conv_id") or "").strip() or None

        def _read_tail() -> list[dict]:
            if not _ROUTING_LOG_PATH.is_file():
                return []
            try:
                # File should stay under 1 MB at typical use; full
                # readlines() is acceptable. If the rotation policy
                # ever lifts and we see multi-MB files, switch to a
                # chunked tail.
                lines = _ROUTING_LOG_PATH.read_text(encoding="utf-8").splitlines()
            except (OSError, PermissionError) as e:
                _LOGGER.debug("routing_log view: read failed: %s", e)
                return []
            # When filtering by conv_id we scan a larger backlog so
            # multi-entry turns (decision + tool_calls + perception)
            # all reach the result, regardless of how many unrelated
            # turns are interleaved.
            scan_count = 1000 if conv_id else tail
            out = []
            for line in lines[-scan_count:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if conv_id and entry.get("conv_id") != conv_id:
                    continue
                out.append(entry)
            # When NOT filtering, still cap at tail. When filtering,
            # return ALL matches (could be more than tail).
            if not conv_id and len(out) > tail:
                out = out[-tail:]
            return out

        entries = await hass.async_add_executor_job(_read_tail)
        return self.json({"entries": entries, "conv_id_filter": conv_id})


class WorldStateView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/world_state[?room=<name>]

    Returns the current structured world state from the aggregator —
    the same shape the agent's `get_world_state()` tool sees. Used by
    the Home app's `/world-state` slash command (Phase 4B) for an
    on-demand snapshot, independent of any throttled event cache.

    `?room=<name>` filters to one room (calls `get_room_state` instead).
    Returns `{"enabled": false}` if the aggregator is disabled via
    `EXTENDED_OPENAI_WORLD_STATE=off`.
    """

    url = "/api/extended_openai_conversation/world_state"
    name = "api:extended_openai_conversation:world_state"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        agg = hass.data.get(DOMAIN, {}).get("world_state")
        if agg is None:
            return self.json({"enabled": False, "reason": "not initialized"})
        room = (request.query.get("room") or "").strip() or None
        if room:
            return self.json(agg.get_room_state(room))
        return self.json(agg.get_world_state())


def _normalize_jsonl_override(obj: dict) -> dict:
    """Translate a JSONL override_event record to the in-memory deque shape
    that home-app.jsx + downstream consumers expect.

    JSONL stores:
      observed.{brightness_pct,light_state}, predicted.{brightness_pct,...},
      context.{shadow_mode, profile, tv_playing, ...}

    Deque (live bus event) stores everything flat at top level. Normalize
    JSONL → flat so the merged response presents one consistent shape.
    """
    observed = obj.get("observed") or {}
    predicted = obj.get("predicted") or {}
    context = obj.get("context") or {}
    return {
        "context_id": obj.get("context_id"),
        "ts": obj.get("ts"),
        "zone": obj.get("zone"),
        "light_entity": obj.get("light_entity"),
        "camera": obj.get("camera"),
        "owning_zones": obj.get("owning_zones"),
        "actual_pct": observed.get("brightness_pct"),
        "predicted_pct": predicted.get("brightness_pct"),
        "delta_pct": obj.get("delta_pct"),
        "light_state": observed.get("light_state"),
        "predictions_by_zone": obj.get("predictions_by_zone"),
        # Flatten context to top level (matches deque/bus-event shape).
        "state": context.get("state"),
        "profile": context.get("profile"),
        "tv_playing": context.get("tv_playing"),
        "asleep": context.get("asleep"),
        "night_safe": context.get("night_safe"),
        "user_at_home": context.get("user_at_home"),
        "anticipated_room": context.get("anticipated_room"),
        "any_occupied": context.get("any_occupied"),
        "shadow_mode": context.get("shadow_mode"),
        "source_user_id": context.get("source_user_id"),
        "debug_match_reason": obj.get("debug_match_reason"),
        "evidence_source": "jsonl",
    }


def _dedupe_override_key(rec: dict) -> tuple:
    """Stable dedupe key. Prefer context_id when present (always-unique per
    real event). Fall back to the tuple (ts, zone, light_entity, actual_pct,
    predicted_pct) for records missing context_id (very old data).
    """
    cid = rec.get("context_id")
    if cid:
        return ("cid", cid)
    return (
        "fb",
        rec.get("ts"),
        rec.get("zone"),
        rec.get("light_entity"),
        rec.get("actual_pct"),
        rec.get("predicted_pct"),
    )


class RecentOverridesView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/recent_overrides
              [?zone=<>&hours=<>]

    Returns recent manual lighting overrides, merged from two sources:

      1. WorldStateAggregator's in-memory rolling deque (live bus events,
         fast, cleared on HA restart). Each record tagged
         evidence_source: "memory".
      2. Streaming-tail of /config/lighting_preferences_pending.jsonl,
         filtered to kind=override_event. Survives restart. Each record
         tagged evidence_source: "jsonl".

    Records are merged + deduped by context_id (with a ts+zone+light+pct
    fallback for old records that lack context_id). Memory wins ties (it
    arrived live; the JSONL append might trail the event by a few hundred
    ms during high-load).

    Response shape is preserved: {data: [...], count, hours, zone}. Each
    record gets an additional `evidence_source` field. Sorted oldest-first.

    Codex M3: this view stays read-only. The JSONL fallback is what makes
    /why-light explain real recent behavior even after an HA restart —
    the in-memory deque alone would return empty until new events fire.
    """

    url = "/api/extended_openai_conversation/recent_overrides"
    name = "api:extended_openai_conversation:recent_overrides"
    requires_auth = True

    JSONL_PATH = "/config/lighting_preferences_pending.jsonl"
    CHUNK_SIZE = 64 * 1024
    MAX_SCAN_BYTES = 16 * 1024 * 1024
    KIND = "override_event"

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            hours = float(request.query.get("hours") or 1.0)
        except (TypeError, ValueError):
            hours = 1.0
        zone = (request.query.get("zone") or "").strip().lower() or None
        # M20: ?collapse=true|false (default true). When true, consecutive
        # override_events on the same light within session_window_s are
        # folded into one "manual adjustment session" with the final value
        # winning. pending_preference remains the durable label (separate
        # endpoint, separate dedupe gate).
        collapse_q = (request.query.get("collapse") or "").strip().lower()
        collapse = collapse_q not in ("false", "0", "no", "off")
        try:
            session_window_s = float(
                request.query.get("session_window_s") or 10.0)
        except (TypeError, ValueError):
            session_window_s = 10.0

        # ── source 1: in-memory deque ──────────────────────────────────
        # Pull memory uncollapsed — the merge with JSONL needs raw events
        # so context_id dedupe can pair memory and disk copies of the
        # same event. Session collapse runs AFTER merge.
        agg = hass.data.get(DOMAIN, {}).get("world_state")
        memory_records: list[dict] = []
        memory_error: str | None = None
        if agg is not None:
            try:
                memory_records = list(
                    agg.recent_overrides(hours=hours, zone=zone, collapse=False)
                )
                for r in memory_records:
                    r["evidence_source"] = "memory"
            except Exception as e:  # noqa: BLE001
                memory_error = str(e)

        # ── source 2: JSONL streaming-tail backfill ────────────────────
        import os
        import json as _json
        from datetime import datetime, timezone

        path = self.JSONL_PATH
        cutoff_ts = None
        if hours and hours > 0:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - (hours * 3600.0)
        kind_gate_a = b'"kind": "override_event"'
        kind_gate_b = b'"kind":"override_event"'

        def _read_jsonl_overrides() -> tuple[list[dict], int, bool]:
            """Streaming-tail same shape as the other M2 endpoints. Filters
            kind=override_event + zone + recency cutoff inline. Capped at
            MAX_SCAN_BYTES."""
            if not os.path.exists(path):
                return [], 0, False
            size = os.path.getsize(path)
            if size <= 0:
                return [], 0, False
            collected: list[dict] = []
            buf = b""
            scanned = 0
            hit_cap = False
            done_early = False
            try:
                with open(path, "rb") as f:
                    pos = size
                    while pos > 0 and not done_early:
                        if scanned >= self.MAX_SCAN_BYTES:
                            hit_cap = True
                            break
                        read_size = min(self.CHUNK_SIZE, pos)
                        pos -= read_size
                        scanned += read_size
                        f.seek(pos)
                        chunk = f.read(read_size)
                        buf = chunk + buf
                        lines = buf.split(b"\n")
                        if pos > 0:
                            buf = lines[0]
                            lines = lines[1:]
                        else:
                            buf = b""
                        for ln_bytes in reversed(lines):
                            ln_stripped = ln_bytes.strip()
                            if not ln_stripped:
                                continue
                            if (kind_gate_a not in ln_stripped
                                    and kind_gate_b not in ln_stripped):
                                continue
                            try:
                                obj = _json.loads(ln_stripped.decode("utf-8", errors="replace"))
                            except (ValueError, TypeError):
                                continue
                            if obj.get("kind") != self.KIND:
                                continue
                            if zone is not None:
                                rec_zone = obj.get("zone")
                                if rec_zone != zone:
                                    continue
                            if cutoff_ts is not None:
                                ts_str = obj.get("ts")
                                if ts_str:
                                    try:
                                        ts_dt = datetime.fromisoformat(str(ts_str))
                                        if ts_dt.tzinfo is None:
                                            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                                        if ts_dt.timestamp() < cutoff_ts:
                                            done_early = True
                                            break
                                    except (ValueError, TypeError):
                                        pass
                            collected.append(_normalize_jsonl_override(obj))
                            # No per-source `tail` cap — the wall-clock hours
                            # window is the bound. Defensively capped by
                            # MAX_SCAN_BYTES + outer dedupe.
            except Exception:  # pylint: disable=broad-except
                # Best-effort: return what we have so far.
                return collected[::-1], scanned, hit_cap
            collected.reverse()
            return collected, scanned, hit_cap

        jsonl_records, scanned_bytes, hit_scan_cap = (
            await hass.async_add_executor_job(_read_jsonl_overrides)
        )

        # ── merge + dedupe ─────────────────────────────────────────────
        # Memory wins ties (live event arrived first). Iterate memory first
        # to seed the seen-set, then JSONL records skip if already present.
        merged: list[dict] = []
        seen: set = set()
        for r in memory_records:
            k = _dedupe_override_key(r)
            if k in seen:
                continue
            seen.add(k)
            merged.append(r)
        jsonl_added = 0
        for r in jsonl_records:
            k = _dedupe_override_key(r)
            if k in seen:
                continue
            seen.add(k)
            merged.append(r)
            jsonl_added += 1

        # Sort oldest-first by ts (preserves ordering convention of the
        # in-memory deque + matches what home-app.jsx renders as "showing
        # last 3"). Records without ts sink to the front (deterministic).
        def _sort_key(r):
            ts = r.get("ts") or ""
            return (ts == "", ts)

        merged.sort(key=_sort_key)
        raw_event_count = len(merged)

        # ── M20: session collapse ─────────────────────────────────────
        # Fold consecutive same-light events within session_window_s
        # into one "manual adjustment session" record. Each surviving
        # record carries session_event_count, session_observed_path,
        # session_first_ts / session_last_ts, and session_collapsed.
        # `collapse=false` (or `session_window_s=0`) returns the raw
        # merged list — the previous (pre-M20) behavior — for debugging.
        events_collapsed_count = 0
        if collapse and merged and session_window_s > 0:
            try:
                from ._override_sessions import collapse_into_sessions
                merged = collapse_into_sessions(
                    merged, session_window_s=session_window_s
                )
                events_collapsed_count = raw_event_count - len(merged)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning(
                    "RecentOverridesView: session collapse failed (%s); "
                    "returning raw merged events",
                    e,
                )

        return self.json({
            "data": merged,
            "count": len(merged),
            "hours": hours,
            "zone": zone,
            # M3 diagnostics — useful for /why-light debug + the probe script.
            "evidence": {
                "memory_count": len(memory_records),
                "memory_error": memory_error,
                "memory_ready": agg is not None,
                "jsonl_scanned_bytes": scanned_bytes,
                "jsonl_hit_scan_cap": hit_scan_cap,
                "jsonl_added_after_dedupe": jsonl_added,
                # M20 diagnostics: how many raw events were merged into
                # sessions. `raw_event_count` is what /recent_overrides
                # would have returned pre-collapse; `count` (above) is
                # the session count (== raw_event_count when collapse=false).
                "raw_event_count": raw_event_count,
                "events_collapsed": events_collapsed_count,
                "collapse_applied": collapse and session_window_s > 0,
                "session_window_s": session_window_s,
            },
        })


class LightingDecisionsView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/lighting_decisions
              [?zone=<slug>&tail=<N>&hours=<H>]

    Returns the last N entries from /config/lighting_decisions.log
    (written by the shadow-mode logger automation), optionally filtered
    by zone and recency. Used by the Home app's `/why-light` slash command
    to explain the last classifier transition for a zone.

    Streaming tail: read the file from the end in 64 KB chunks, parse
    lines back-to-front, stop when `tail` records are accumulated or
    the `hours` cutoff is reached. Never slurps the full file.

    Returns `{"entries": []}` if the file doesn't exist (never 500).
    """

    url = "/api/extended_openai_conversation/lighting_decisions"
    name = "api:extended_openai_conversation:lighting_decisions"
    requires_auth = True

    DEFAULT_TAIL = 20
    MAX_TAIL = 200
    DEFAULT_HOURS = 24.0
    LOG_PATH = "/config/lighting_decisions.log"
    CHUNK_SIZE = 64 * 1024
    # Hard cap on total bytes scanned per request, regardless of how many
    # matching records we've found. Protects against pathologically large
    # logs (zone with zero recent entries on a busy log). 16 MB ~= 50k
    # average-sized JSONL rows; we'll never scan further than that even
    # if filters match zero records.
    MAX_SCAN_BYTES = 16 * 1024 * 1024

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            tail = int(request.query.get("tail") or self.DEFAULT_TAIL)
        except (TypeError, ValueError):
            tail = self.DEFAULT_TAIL
        tail = max(1, min(tail, self.MAX_TAIL))
        try:
            hours = float(request.query.get("hours") or self.DEFAULT_HOURS)
        except (TypeError, ValueError):
            hours = self.DEFAULT_HOURS
        zone = (request.query.get("zone") or "").strip().lower() or None

        # Resolve the zone -> classifier-sensor entity_id prefix. The shadow
        # log writes `entity_id: sensor.<camera>_<slug>_lighting_state`, so
        # we filter by substring `_<slug>_lighting_state`. Cheap + reliable.
        zone_filter = f"_{zone}_lighting_state" if zone else None

        import os
        import json as _json
        from datetime import datetime, timezone

        path = self.LOG_PATH
        if not os.path.exists(path):
            return self.json({"entries": [], "reason": "log_not_present",
                              "tail": tail, "hours": hours, "zone": zone})

        cutoff_ts = None
        if hours and hours > 0:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - (hours * 3600.0)

        def _read_tail_filtered() -> tuple[list[dict], int, bool]:
            """Stream file from end, applying zone + recency filters inline.

            Returns (entries_oldest_first, bytes_scanned, hit_scan_cap).

            Stops early on any of:
              - len(collected) >= tail
              - parsed_ts < cutoff_ts (assumes append-only chronological log)
              - bytes_scanned >= MAX_SCAN_BYTES (safety cap)
              - file fully consumed (pos == 0)

            Codex round-2 fix: previously read MAX_TAIL=200 raw lines, THEN
            filtered. On a busy log with frequent transitions across many
            zones, the last 200 lines could contain zero office entries —
            and /why-light office would silently return empty even when the
            office log was rich just past the 200-line window. This version
            filters inside the read loop, so zone-filtered queries scan as
            far back as needed (capped at MAX_SCAN_BYTES).
            """
            size = os.path.getsize(path)
            if size <= 0:
                return [], 0, False
            collected: list[dict] = []
            buf = b""
            scanned = 0
            hit_cap = False
            done_early = False
            try:
                with open(path, "rb") as f:
                    pos = size
                    while pos > 0 and not done_early:
                        if scanned >= self.MAX_SCAN_BYTES:
                            hit_cap = True
                            break
                        read_size = min(self.CHUNK_SIZE, pos)
                        pos -= read_size
                        scanned += read_size
                        f.seek(pos)
                        chunk = f.read(read_size)
                        buf = chunk + buf
                        # Split on newline; keep last partial in buf for next
                        # chunk merge (unless we're at the start of file).
                        lines = buf.split(b"\n")
                        if pos > 0:
                            buf = lines[0]
                            lines = lines[1:]
                        else:
                            buf = b""
                        # Iterate newest-first within this chunk.
                        for ln_bytes in reversed(lines):
                            ln_stripped = ln_bytes.strip()
                            if not ln_stripped:
                                continue
                            # Cheap substring zone gate BEFORE JSON parse.
                            if zone_filter is not None:
                                if zone_filter.encode("utf-8") not in ln_stripped:
                                    continue
                            # Parse JSON (corrupt lines silently skipped —
                            # don't break the scan).
                            try:
                                obj = _json.loads(ln_stripped.decode("utf-8", errors="replace"))
                            except (ValueError, TypeError):
                                continue
                            # Recency cutoff — assumes append-only chronological
                            # log. If this entry is older than cutoff, every
                            # entry further back is also older → stop scanning.
                            if cutoff_ts is not None:
                                ts_str = obj.get("ts")
                                if ts_str:
                                    try:
                                        ts_dt = datetime.fromisoformat(str(ts_str))
                                        if ts_dt.tzinfo is None:
                                            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                                        if ts_dt.timestamp() < cutoff_ts:
                                            done_early = True
                                            break
                                    except (ValueError, TypeError):
                                        # Unparseable ts — keep entry rather
                                        # than dropping it (better too much
                                        # than too little for introspection).
                                        pass
                            collected.append(obj)
                            if len(collected) >= tail:
                                done_early = True
                                break
            except Exception:  # pylint: disable=broad-except
                # On any I/O error, return what we have so far rather than
                # 500-ing — the endpoint is for introspection and degraded
                # results are better than no results.
                return collected[::-1], scanned, hit_cap
            # collected is newest-first; reverse to oldest-first.
            collected.reverse()
            return collected, scanned, hit_cap

        # File I/O off the event loop to avoid blocking.
        entries, scanned_bytes, hit_scan_cap = await hass.async_add_executor_job(
            _read_tail_filtered
        )

        return self.json({
            "entries": entries,
            "count": len(entries),
            "tail": tail,
            "hours": hours,
            "zone": zone,
            "scanned_bytes": scanned_bytes,
            "hit_scan_cap": hit_scan_cap,
        })


class RecentPendingPreferencesView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/recent_pending_preferences
              [?zone=<slug>&tail=<N>&hours=<H>]

    Returns the last N `pending_preference` records from
    /config/lighting_preferences_pending.jsonl, optionally filtered by zone
    and recency. Used by the Home app's `/why-light` slash command (M2) to
    surface the most recent zone-exit preference snapshot alongside the
    classifier state + recent overrides.

    The JSONL file is shared with `override_event` records — we filter to
    kind=pending_preference here so callers get exactly what they asked for.

    Streaming tail: reads the file from the end in 64 KB chunks, parses
    lines back-to-front, stops on tail-met / cutoff / 16 MB safety cap.
    Returns `{"entries": []}` if the file doesn't exist (never 500).

    Read-only — does not modify the JSONL.
    """

    url = "/api/extended_openai_conversation/recent_pending_preferences"
    name = "api:extended_openai_conversation:recent_pending_preferences"
    requires_auth = True

    DEFAULT_TAIL = 20
    MAX_TAIL = 200
    DEFAULT_HOURS = 24.0
    LOG_PATH = "/config/lighting_preferences_pending.jsonl"
    CHUNK_SIZE = 64 * 1024
    MAX_SCAN_BYTES = 16 * 1024 * 1024
    KIND = "pending_preference"

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            tail = int(request.query.get("tail") or self.DEFAULT_TAIL)
        except (TypeError, ValueError):
            tail = self.DEFAULT_TAIL
        tail = max(1, min(tail, self.MAX_TAIL))
        try:
            hours = float(request.query.get("hours") or self.DEFAULT_HOURS)
        except (TypeError, ValueError):
            hours = self.DEFAULT_HOURS
        zone = (request.query.get("zone") or "").strip().lower() or None

        import os
        import json as _json
        from datetime import datetime, timezone

        path = self.LOG_PATH
        if not os.path.exists(path):
            return self.json({"entries": [], "reason": "log_not_present",
                              "count": 0, "tail": tail, "hours": hours,
                              "zone": zone})

        cutoff_ts = None
        if hours and hours > 0:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - (hours * 3600.0)

        # Pre-parse substring gate (cheap). HA's tojson emits `"kind": "pending_preference"`.
        # Both space and no-space variants are accepted for robustness.
        kind_gate_a = b'"kind": "pending_preference"'
        kind_gate_b = b'"kind":"pending_preference"'

        def _read_tail_filtered() -> tuple[list[dict], int, bool]:
            """Stream JSONL from end, applying kind/zone/recency filters inline.

            Same streaming-tail-with-filter discipline as LightingDecisionsView.
            Zone match is done post-JSON-parse against `primary_zone` (or `zone`
            as fallback for older records) so it's exact, not substring.
            """
            size = os.path.getsize(path)
            if size <= 0:
                return [], 0, False
            collected: list[dict] = []
            buf = b""
            scanned = 0
            hit_cap = False
            done_early = False
            try:
                with open(path, "rb") as f:
                    pos = size
                    while pos > 0 and not done_early:
                        if scanned >= self.MAX_SCAN_BYTES:
                            hit_cap = True
                            break
                        read_size = min(self.CHUNK_SIZE, pos)
                        pos -= read_size
                        scanned += read_size
                        f.seek(pos)
                        chunk = f.read(read_size)
                        buf = chunk + buf
                        lines = buf.split(b"\n")
                        if pos > 0:
                            buf = lines[0]
                            lines = lines[1:]
                        else:
                            buf = b""
                        for ln_bytes in reversed(lines):
                            ln_stripped = ln_bytes.strip()
                            if not ln_stripped:
                                continue
                            # Cheap pre-parse kind gate (skip override_event).
                            if (kind_gate_a not in ln_stripped
                                    and kind_gate_b not in ln_stripped):
                                continue
                            try:
                                obj = _json.loads(ln_stripped.decode("utf-8", errors="replace"))
                            except (ValueError, TypeError):
                                continue
                            # Belt-and-suspenders kind check (post-parse).
                            if obj.get("kind") != self.KIND:
                                continue
                            # Exact zone match — prefer primary_zone (M2), fall
                            # back to zone for older records.
                            if zone is not None:
                                rec_zone = obj.get("primary_zone") or obj.get("zone")
                                if rec_zone != zone:
                                    continue
                            # Recency cutoff (assumes append-only chronological).
                            if cutoff_ts is not None:
                                ts_str = obj.get("ts")
                                if ts_str:
                                    try:
                                        ts_dt = datetime.fromisoformat(str(ts_str))
                                        if ts_dt.tzinfo is None:
                                            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                                        if ts_dt.timestamp() < cutoff_ts:
                                            done_early = True
                                            break
                                    except (ValueError, TypeError):
                                        pass
                            collected.append(obj)
                            if len(collected) >= tail:
                                done_early = True
                                break
            except Exception:  # pylint: disable=broad-except
                return collected[::-1], scanned, hit_cap
            collected.reverse()
            return collected, scanned, hit_cap

        entries, scanned_bytes, hit_scan_cap = await hass.async_add_executor_job(
            _read_tail_filtered
        )

        return self.json({
            "entries": entries,
            "count": len(entries),
            "tail": tail,
            "hours": hours,
            "zone": zone,
            "scanned_bytes": scanned_bytes,
            "hit_scan_cap": hit_scan_cap,
        })


class LightingEvidenceStatusView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/lighting_evidence/status

    Read-only source-of-truth status for HAOS lighting evidence files.
    """

    url = "/api/extended_openai_conversation/lighting_evidence/status"
    name = "api:extended_openai_conversation:lighting_evidence_status"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            payload = await hass.async_add_executor_job(all_source_status)
        except Exception as e:  # pylint: disable=broad-except
            return self.json({"ok": False, "error": str(e)}, status_code=500)
        return self.json(payload)


class LightingEvidenceExportView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/lighting_evidence/export
              ?source=preferences|decisions|activity&offset=<byte>&max_bytes=<n>

    Bounded raw JSONL export for Intelligence cursor pull. Returns only
    complete lines and never mutates the evidence files.
    """

    url = "/api/extended_openai_conversation/lighting_evidence/export"
    name = "api:extended_openai_conversation:lighting_evidence_export"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        source = (request.query.get("source") or "preferences").strip()
        try:
            offset = int(request.query.get("offset") or 0)
        except (TypeError, ValueError):
            offset = 0
        try:
            max_bytes = int(request.query.get("max_bytes") or 0)
        except (TypeError, ValueError):
            max_bytes = 0

        def _export() -> dict:
            return export_source_lines(source, offset=offset, max_bytes=max_bytes)

        try:
            payload = await hass.async_add_executor_job(_export)
        except ValueError as e:
            return self.json({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:  # pylint: disable=broad-except
            return self.json({"ok": False, "error": str(e)}, status_code=500)
        return self.json(payload)


class SpatialModelView(CORSHomeAssistantView):
    """GET  /api/extended_openai_conversation/spatial_model
    POST /api/extended_openai_conversation/spatial_model

    The light-footprint model — for each light, a hand-drawn polygon of
    where it shines, in normalised [0,1] camera-frame coordinates. Read
    from /config/spatial_model.json (scaffolded by
    tools/init-spatial-model.py, drawn in the Home app's /spatial drawer).

    GET returns the model. Never 500s — if the file is absent GET returns
    {"calibrated": false, "reason": "..."}.

    POST writes one light's footprint. Body:
      {"entity_id": "light.x",
       "footprint_polygon": {"<camera>": [[x, y], ...]},
       "footprint": {"<camera>": {"col,row": 1.0, ...}}}
    footprint_polygon is the editable source of truth; footprint is its
    rasterised-to-cells form. The record is patched into the model file
    with an atomic temp-file replace.
    """

    url = "/api/extended_openai_conversation/spatial_model"
    name = "api:extended_openai_conversation:spatial_model"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        def _read() -> dict:
            if not _SPATIAL_MODEL_PATH.is_file():
                return {"calibrated": False,
                        "reason": "spatial_model.json not found — run "
                                  "tools/init-spatial-model.py"}
            try:
                model = json.loads(
                    _SPATIAL_MODEL_PATH.read_text(encoding="utf-8"))
            except (OSError, PermissionError, json.JSONDecodeError) as e:
                _LOGGER.debug("spatial_model view: read failed: %s", e)
                return {"calibrated": False, "reason": f"read failed: {e}"}
            model["calibrated"] = True
            return model

        return self.json(await hass.async_add_executor_job(_read))

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self.json({"error": "invalid JSON body"}, status_code=400)
        entity = (body.get("entity_id") or "").strip()
        if not entity:
            return self.json({"error": "entity_id required"}, status_code=400)
        polygon = body.get("footprint_polygon")
        footprint = body.get("footprint")
        if not isinstance(polygon, dict) or not isinstance(footprint, dict):
            return self.json(
                {"error": "footprint_polygon and footprint objects required"},
                status_code=400)

        def _write() -> dict:
            from datetime import datetime, timezone
            if not _SPATIAL_MODEL_PATH.is_file():
                return {"error": "no spatial_model — run "
                                 "tools/init-spatial-model.py"}
            try:
                model = json.loads(
                    _SPATIAL_MODEL_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                return {"error": f"read failed: {e}"}
            lights = model.get("lights") or {}
            if entity not in lights:
                return {"error": f"unknown light {entity}"}
            rec = lights[entity]
            # footprint_polygon is the editable source of truth (the
            # drawn shape); footprint is its rasterised-to-cells form.
            # An empty polygon clears the light to footprint_empty.
            rec["footprint_polygon"] = polygon
            rec["footprint"] = footprint
            rec["footprint_empty"] = not polygon
            rec["human_verdict"] = "hand_drawn"
            rec["human_reviewed_at"] = datetime.now(timezone.utc).isoformat()
            # Atomic write — temp file + replace so a crash mid-write
            # never leaves a truncated spatial_model.json.
            tmp = _SPATIAL_MODEL_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(model, indent=2), encoding="utf-8")
            tmp.replace(_SPATIAL_MODEL_PATH)
            return {"ok": True, "entity_id": entity, "light": rec}

        result = await hass.async_add_executor_job(_write)
        return self.json(result,
                         status_code=200 if result.get("ok") else 400)


class ApartmentModelView(CORSHomeAssistantView):
    """GET  /api/extended_openai_conversation/apartment_model
    POST /api/extended_openai_conversation/apartment_model

    The 3D apartment spatial model (apartment_model v1, Z-up metric):
    zones, devices, camera calibrations, light influence polygons. Backs the
    Home app's /apartment screen. Coexists with the legacy spatial_model.

    GET never 500s — absent file returns {"exists": false, "revision": 0}.

    POST replaces the whole document with optimistic concurrency: the body's
    `revision` must equal the stored revision; the server increments it on
    write. A mismatch returns 409 with the stored document so the client can
    rebase. Writes are atomic (temp + replace) and the previous version is
    kept as a timestamped backup (last 10 retained).
    """

    url = "/api/extended_openai_conversation/apartment_model"
    name = "api:extended_openai_conversation:apartment_model"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        def _read() -> dict:
            if not _APARTMENT_MODEL_PATH.is_file():
                return {"exists": False, "revision": 0, "schema_version": 1,
                        "zones": [], "devices": []}
            try:
                model = json.loads(
                    _APARTMENT_MODEL_PATH.read_text(encoding="utf-8"))
            except (OSError, PermissionError, json.JSONDecodeError) as e:
                _LOGGER.debug("apartment_model view: read failed: %s", e)
                return {"exists": False, "revision": 0, "schema_version": 1,
                        "zones": [], "devices": [],
                        "reason": f"read failed: {e}"}
            model["exists"] = True
            return model

        return self.json(await hass.async_add_executor_job(_read))

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self.json({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return self.json({"error": "object body required"},
                             status_code=400)
        if body.get("schema_version") != 1:
            return self.json({"error": "schema_version 1 required"},
                             status_code=400)
        if not isinstance(body.get("revision"), int):
            return self.json({"error": "integer revision required"},
                             status_code=400)
        for key in ("zones", "devices"):
            if not isinstance(body.get(key), list):
                return self.json({"error": f"{key} array required"},
                                 status_code=400)

        def _write() -> tuple[dict, int]:
            from datetime import datetime, timezone
            stored_rev = 0
            stored = None
            if _APARTMENT_MODEL_PATH.is_file():
                try:
                    stored = json.loads(
                        _APARTMENT_MODEL_PATH.read_text(encoding="utf-8"))
                    stored_rev = int(stored.get("revision") or 0)
                except (OSError, json.JSONDecodeError, ValueError) as e:
                    return ({"error": f"stored model unreadable: {e}"}, 500)
            if body["revision"] != stored_rev:
                # stale client — hand back the stored doc to rebase onto
                conflict = stored or {"revision": stored_rev}
                conflict["conflict"] = True
                return (conflict, 409)
            body["revision"] = stored_rev + 1
            body["updated_at"] = datetime.now(timezone.utc).isoformat()
            body.pop("exists", None)
            body.pop("conflict", None)
            # timestamped backup of the outgoing version (keep last 10)
            if stored is not None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup = _APARTMENT_MODEL_PATH.with_name(
                    f"apartment_model.{stamp}.bak.json")
                try:
                    backup.write_text(json.dumps(stored), encoding="utf-8")
                    baks = sorted(_APARTMENT_MODEL_PATH.parent.glob(
                        "apartment_model.*.bak.json"))
                    for old in baks[:-10]:
                        old.unlink(missing_ok=True)
                except OSError as e:  # backup failure never blocks the write
                    _LOGGER.warning("apartment_model backup failed: %s", e)
            tmp = _APARTMENT_MODEL_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
            tmp.replace(_APARTMENT_MODEL_PATH)
            return ({"ok": True, "revision": body["revision"],
                     "updated_at": body["updated_at"]}, 200)

        result, status = await hass.async_add_executor_job(_write)
        return self.json(result, status_code=status)


class IdentityListView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/identities

    Returns all identities (excluding archived + ignored by default).
    Query params: `?include_archived=1`, `?include_ignored=1`,
    `?relationship_type=<type>`. Used by the Home app's `people` route
    to render the discovery queue + relationship graph + list view.

    Returns `{"enabled": false}` if the store is disabled via
    `EXTENDED_OPENAI_IDENTITY_STORE=off`.
    """

    url = "/api/extended_openai_conversation/identities"
    name = "api:extended_openai_conversation:identities"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False, "reason": "not initialized"})
        # Addendum 16 F-4a: surface setup_error explicitly so the Tauri
        # overlay can render a "store not ready" banner instead of
        # silently empty. The F-4b banner reads `ready` + `setup_error`.
        setup_error = getattr(store, "setup_error", None)
        if setup_error:
            return self.json({
                "enabled": True,
                "ready": False,
                "setup_error": setup_error,
                "identities": [],
            })
        include_archived = request.query.get("include_archived", "0") == "1"
        include_ignored = request.query.get("include_ignored", "0") == "1"
        rel_type = (request.query.get("relationship_type") or "").strip() or None

        def _list() -> list[dict]:
            from dataclasses import asdict
            rows = store.list_identities(
                include_archived=include_archived,
                include_ignored=include_ignored,
                relationship_type=rel_type,
            )
            return [asdict(r) for r in rows]
        identities = await hass.async_add_executor_job(_list)
        # Addendum 24: expose the Frigate base URL so the people overlay
        # can build face-crop image URLs without hardcoding the IP.
        from dataclasses import asdict
        from .frigate_sync import base_url as _frigate_base_url
        data = hass.data.get(DOMAIN, {})
        seed_report = data.get("frigate_seed_report")
        capabilities = data.get("frigate_capabilities")
        return self.json({
            "enabled": True, "ready": True,
            "identities": identities,
            "frigate_url": _frigate_base_url(),
            "frigate_seed_report": asdict(seed_report) if seed_report is not None else None,
            "frigate_capabilities": asdict(capabilities) if capabilities is not None else None,
        })


class IdentityDetailView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/identity/{uuid}
    PATCH /api/extended_openai_conversation/identity/{uuid}
    DELETE /api/extended_openai_conversation/identity/{uuid}

    Single-identity CRUD. GET also returns relationships + preferences
    in one payload so the people-tab detail panel doesn't need three
    round-trips. PATCH body is a JSON object of fields to update; only
    fields in IdentityStore.update_identity's `allowed` set are accepted.
    DELETE cascades locally + queues a Frigate-side delete in
    pending_writes (per AR-8).
    """

    url = "/api/extended_openai_conversation/identity/{uuid}"
    name = "api:extended_openai_conversation:identity_detail"
    requires_auth = True

    async def get(self, request: web.Request, uuid: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)

        def _read() -> dict | None:
            from dataclasses import asdict
            row = store.get_identity(uuid)
            if row is None:
                return None
            return {
                "identity": asdict(row),
                "relationships": store.list_relationships(row.uuid, include_ended=True),
                "preferences": store.list_preferences(row.uuid),
            }

        payload = await hass.async_add_executor_job(_read)
        if payload is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(payload)

    async def patch(self, request: web.Request, uuid: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return self.json({"error": "body must be a JSON object"}, status_code=400)
        expected_version = body.pop("expected_version", None)
        actor = body.pop("actor", "user")

        def _update() -> dict:
            try:
                ok = store.update_identity(uuid, body, actor=actor,
                                            expected_version=expected_version)
                return {"updated": ok}
            except PermissionError as e:
                return {"error": str(e), "status": 403}
            except ValueError as e:
                return {"error": str(e)}

        result = await hass.async_add_executor_job(_update)
        if "error" in result:
            status = int(result.pop("status", 400))
            return self.json(result, status_code=status)
        return self.json(result)

    async def delete(self, request: web.Request, uuid: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)

        def _delete() -> dict:
            ok = store.delete_identity(uuid, actor="user")
            # Cascade: best-effort avatar-file removal. Doesn't block the
            # delete return if the unlink fails (file just sits orphaned).
            avatar_removed = False
            if ok:
                avatar_removed = _delete_avatar_file_sync(uuid)
            return {"deleted": ok, "avatar_removed": avatar_removed}

        result = await hass.async_add_executor_job(_delete)
        if not result["deleted"]:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(result)


class IdentityBackupView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/identity_backup

    Returns the full identity store as JSON for backup/restore (AR2-12).
    Includes: identities + aliases + enrollments + relationships +
    preferences. Excludes face_crops_meta (Frigate owns the crops) and
    change_log (volatile audit, retention-managed separately).

    Used by the Home app's transparency view (US-5.02 "export as JSON")
    AND by the daily auto-backup script (Slice 12). Returns
    {"enabled": false} when the store is disabled.
    """

    url = "/api/extended_openai_conversation/identity_backup"
    name = "api:extended_openai_conversation:identity_backup"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)

        # AR24-10: opt-in avatar inclusion via ?include_avatars=1. Default
        # OFF because base64 JPEG bytes bloat the JSON (~50 KB per
        # identity); user can pass the flag for "complete" backups. Files
        # also live on disk under /config/.../avatars/<uuid>.jpg and can
        # be tar'd separately for the raw-bytes path.
        include_avatars = request.query.get("include_avatars") in ("1", "true", "yes")

        def _snapshot() -> dict:
            from dataclasses import asdict
            from datetime import datetime, timezone
            import base64
            identities = store.list_identities(
                include_archived=True, include_ignored=True
            )
            out_identities = []
            for i in identities:
                row = asdict(i)
                row["relationships"] = store.list_relationships(i.uuid, include_ended=True)
                row["preferences"] = store.list_preferences(i.uuid)
                if include_avatars:
                    path = _AVATARS_DIR / f"{i.uuid}.jpg"
                    try:
                        if path.exists():
                            row["avatar_b64"] = base64.b64encode(
                                path.read_bytes()
                            ).decode("ascii")
                    except OSError:
                        # Best-effort: drop the avatar entry on read failure;
                        # rest of the backup proceeds.
                        pass
                out_identities.append(row)
            return {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": 1,
                "include_avatars": include_avatars,
                "identities": out_identities,
            }

        payload = await hass.async_add_executor_job(_snapshot)
        return self.json(payload)


class IdentityCreateView(CORSHomeAssistantView):
    """POST /api/extended_openai_conversation/identities

    Body: {"display_name": "...", "relationship_type": "...", ...}.
    Returns {"uuid": "<new uuid>"} on success.
    """

    url = "/api/extended_openai_conversation/identities/create"
    name = "api:extended_openai_conversation:identity_create"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status_code=400)
        display_name = (body.get("display_name") or "").strip()
        if not display_name:
            return self.json({"error": "display_name required"}, status_code=400)

        def _create() -> dict:
            try:
                new_uuid = store.create_identity(
                    display_name,
                    relationship_type=body.get("relationship_type", "unknown"),
                    relationship_subrole=body.get("relationship_subrole"),
                    pronouns=body.get("pronouns"),
                    notes=body.get("notes"),
                    aliases=body.get("aliases") or [],
                    frigate_person_name=body.get("frigate_person_name"),
                    ha_person_entity_id=body.get("ha_person_entity_id"),
                    actor=body.get("actor", "user"),
                )
                return {"uuid": new_uuid}
            except ValueError as e:
                return {"error": str(e)}

        result = await hass.async_add_executor_job(_create)
        if "error" in result:
            return self.json(result, status_code=400)
        return self.json(result)


class RelationshipsView(CORSHomeAssistantView):
    """POST /api/extended_openai_conversation/identity/{uuid}/relationships
    DELETE /api/extended_openai_conversation/identity/{uuid}/relationships/{edge_id}

    Add a relationship edge from `uuid` to a target identity, or mark an
    existing edge as ended.
    """

    url = "/api/extended_openai_conversation/identity/{uuid}/relationships"
    name = "api:extended_openai_conversation:relationships"
    requires_auth = True

    async def post(self, request: web.Request, uuid: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status_code=400)
        to_uuid = (body.get("to_uuid") or "").strip()
        rel_type = (body.get("rel_type") or "").strip()
        if not to_uuid or not rel_type:
            return self.json({"error": "to_uuid + rel_type required"}, status_code=400)

        def _add() -> dict:
            try:
                edge_id = store.add_relationship(
                    uuid, to_uuid, rel_type,
                    edge_data=body.get("edge_data") or {},
                    actor=body.get("actor", "user"),
                )
                return {"edge_id": edge_id}
            except ValueError as e:
                return {"error": str(e)}

        result = await hass.async_add_executor_job(_add)
        if "error" in result:
            return self.json(result, status_code=400)
        return self.json(result)


class PreferencesView(CORSHomeAssistantView):
    """POST /api/extended_openai_conversation/identity/{uuid}/preferences

    Upsert a preference on an identity. Body:
        {"key": "light_temp_pref", "value": 2700,
         "scope": {"time_of_day": "evening"}, "source": "manual"}
    """

    url = "/api/extended_openai_conversation/identity/{uuid}/preferences"
    name = "api:extended_openai_conversation:preferences"
    requires_auth = True

    async def post(self, request: web.Request, uuid: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON"}, status_code=400)
        key = (body.get("key") or "").strip()
        if not key:
            return self.json({"error": "key required"}, status_code=400)
        if "value" not in body:
            return self.json({"error": "value required"}, status_code=400)

        def _set() -> dict:
            try:
                pref_id = store.set_preference(
                    uuid, key, body["value"],
                    scope=body.get("scope") or {},
                    source=body.get("source", "manual"),
                    confidence=body.get("confidence"),
                    provenance=body.get("provenance") or {},
                    actor=body.get("actor", "user"),
                )
                return {"preference_id": pref_id}
            except ValueError as e:
                return {"error": str(e)}

        result = await hass.async_add_executor_job(_set)
        if "error" in result:
            return self.json(result, status_code=400)
        return self.json(result)


class FrigateProxyView(CORSHomeAssistantView):
    """GET /api/extended_openai_conversation/frigate_proxy?path=api/faces

    Tauri ↔ Frigate cross-origin proxy. Frigate's nginx doesn't send
    CORS headers, so the WebView2 fetch from tauri.localhost is blocked.
    This view fetches Frigate server-side (no CORS gate) and returns the
    response with our CORS-enabled view headers.

    Whitelisted paths only — never an arbitrary URL passthrough. The
    `clips/faces/<name>/<file>` images don't need proxying because
    <img> tag loads don't trigger CORS preflight.
    """

    url = "/api/extended_openai_conversation/frigate_proxy"
    name = "api:extended_openai_conversation:frigate_proxy"
    requires_auth = True

    # Whitelist of allowed proxy paths (exact match or prefix match).
    _ALLOWED_PREFIXES = ("api/faces",)

    async def get(self, request: web.Request) -> web.Response:
        path = (request.query.get("path") or "").lstrip("/")
        if not path or not any(path.startswith(p) for p in self._ALLOWED_PREFIXES):
            return self.json({"error": "path not allowed"}, status_code=400)
        from .frigate_sync import base_url as _frigate_base_url
        base = _frigate_base_url()
        if not base:
            return self.json({"error": "frigate not configured"}, status_code=503)
        url = f"{base.rstrip('/')}/{path}"
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            return web.Response(
                body=resp.content,
                status=resp.status_code,
                content_type=resp.headers.get("content-type", "application/json"),
            )
        except Exception as e:  # noqa: BLE001
            return self.json({"error": f"frigate fetch failed: {e}"}, status_code=502)


class AvatarView(CORSHomeAssistantView):
    """GET/POST/DELETE /api/extended_openai_conversation/identity/{uuid}/avatar

    Custom per-identity avatar (Addendum 24 Phase 3). File presence is the
    source-of-truth — when /config/.../avatars/<uuid>.jpg exists, frontends
    render it as the node avatar; otherwise initials fall back. No schema
    change; revert by deleting the file.

    Client does the crop + resize (canvas → JPEG); the server validates
    content-type + size, writes bytes via executor (disk I/O blocks the loop
    otherwise). Auth required for all methods.
    """

    url = "/api/extended_openai_conversation/identity/{uuid}/avatar"
    name = "api:extended_openai_conversation:identity_avatar"
    requires_auth = True

    @staticmethod
    def _path_for(uuid: str) -> Path | None:
        # Defense against ../ traversal even though HA's router enforces
        # the {uuid} placeholder shape. Only allow uuid-like chars.
        if not uuid or any(c in uuid for c in "/\\."):
            return None
        return _AVATARS_DIR / f"{uuid}.jpg"

    async def get(self, request: web.Request, uuid: str) -> web.Response:
        path = self._path_for(uuid)
        if path is None:
            return self.json({"error": "invalid uuid"}, status_code=400)
        hass: HomeAssistant = request.app["hass"]

        def _read() -> bytes | None:
            try:
                if not path.exists():
                    return None
                return path.read_bytes()
            except OSError:
                return None

        data = await hass.async_add_executor_job(_read)
        if data is None:
            return self.json({"error": "no avatar"}, status_code=404)
        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={"Cache-Control": "max-age=60, must-revalidate"},
        )

    async def head(self, request: web.Request, uuid: str) -> web.Response:
        """Cheap existence check used by the people overlay's pre-check
        (AR24-9) — returns 200 if the avatar file exists, 404 if not.
        No body. Avoids fetching the full image bytes just to test presence."""
        path = self._path_for(uuid)
        if path is None:
            return web.Response(status=400)
        hass: HomeAssistant = request.app["hass"]
        exists = await hass.async_add_executor_job(
            lambda: path.exists() if path else False
        )
        return web.Response(status=200 if exists else 404)

    async def post(self, request: web.Request, uuid: str) -> web.Response:
        path = self._path_for(uuid)
        if path is None:
            return self.json({"error": "invalid uuid"}, status_code=400)
        hass: HomeAssistant = request.app["hass"]

        # Confirm the identity exists before we touch disk — avoids orphan
        # avatar files for never-created uuids.
        store: IdentityStore | None = hass.data.get(DOMAIN, {}).get("identity_store")
        if store is None:
            return self.json({"enabled": False}, status_code=503)
        identity = await hass.async_add_executor_job(store.get_identity, uuid)
        if identity is None:
            return self.json({"error": "identity not found"}, status_code=404)

        ctype = (request.content_type or "").lower()
        try:
            if ctype.startswith("multipart/"):
                reader = await request.multipart()
                field = await reader.next()
                if field is None:
                    return self.json({"error": "no file field"}, status_code=400)
                # Drain into memory with size cap. Avatars are <=1MB by
                # client-side resize contract; reject anything larger.
                data = await field.read(decode=False)
            else:
                # Direct raw-body upload (image/jpeg or octet-stream); useful
                # for clients that can't construct multipart cleanly.
                data = await request.read()
        except Exception as e:  # noqa: BLE001
            return self.json({"error": f"read failed: {e}"}, status_code=400)

        if not data:
            return self.json({"error": "empty body"}, status_code=400)
        if len(data) > _AVATAR_MAX_BYTES:
            return self.json({"error": "too large", "max_bytes": _AVATAR_MAX_BYTES},
                             status_code=413)
        # Quick magic-byte sniff: must look like JPEG/PNG/WebP. Cheap rejection
        # of obvious non-images without pulling in PIL.
        head = data[:12]
        is_jpeg = head[:3] == b"\xff\xd8\xff"
        is_png = head[:8] == b"\x89PNG\r\n\x1a\n"
        is_webp = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
        if not (is_jpeg or is_png or is_webp):
            return self.json({"error": "unsupported image format"}, status_code=415)

        def _write() -> dict:
            _AVATARS_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".jpg.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic on POSIX
            return {"ok": True, "size_bytes": len(data)}

        try:
            result = await hass.async_add_executor_job(_write)
        except OSError as e:
            return self.json({"error": f"write failed: {e}"}, status_code=500)
        return self.json(result)

    async def delete(self, request: web.Request, uuid: str) -> web.Response:
        path = self._path_for(uuid)
        if path is None:
            return self.json({"error": "invalid uuid"}, status_code=400)
        hass: HomeAssistant = request.app["hass"]

        def _unlink() -> bool:
            try:
                if path.exists():
                    path.unlink()
                    return True
                return False
            except OSError:
                return False

        existed = await hass.async_add_executor_job(_unlink)
        return self.json({"deleted": existed})


def _delete_avatar_file_sync(uuid: str) -> bool:
    """Sync-only helper used by the identity delete cascade.

    Returns True if a file was removed, False otherwise (no-op on missing).
    Safe to call from the executor; never raises."""
    try:
        path = _AVATARS_DIR / f"{uuid}.jpg"
        if path.exists():
            path.unlink()
            return True
    except OSError:
        pass
    return False


def _admin_only_legacy_handler(handler):
    """Require HA administrator authority for every legacy private API."""

    @wraps(handler)
    async def wrapped(self, request: web.Request, *args, **kwargs):
        user = request.get("hass_user")
        if user is None or not bool(getattr(user, "is_admin", False)):
            raise Unauthorized()
        return await handler(self, request, *args, **kwargs)

    wrapped._home_agent_admin_guard = True
    return wrapped


_LEGACY_PRIVATE_VIEW_CLASSES = (
    RoutingLogView,
    WorldStateView,
    RecentOverridesView,
    LightingDecisionsView,
    RecentPendingPreferencesView,
    LightingEvidenceStatusView,
    LightingEvidenceExportView,
    SpatialModelView,
    ApartmentModelView,
    IdentityListView,
    IdentityDetailView,
    IdentityBackupView,
    IdentityCreateView,
    RelationshipsView,
    PreferencesView,
    FrigateProxyView,
    AvatarView,
)
for _view_class in _LEGACY_PRIVATE_VIEW_CLASSES:
    for _method_name in ("get", "head", "post", "put", "patch", "delete"):
        _handler = _view_class.__dict__.get(_method_name)
        if _handler is not None and not getattr(
            _handler, "_home_agent_admin_guard", False
        ):
            setattr(
                _view_class,
                _method_name,
                _admin_only_legacy_handler(_handler),
            )


_LEGACY_SENSITIVE_LOG_NAMES = ("asr_debug.log", "external_routing.log")


def _purge_legacy_sensitive_logs(config_dir: str) -> None:
    """Delete prior contentful conversation/debug logs without reading them."""

    root = Path(config_dir)
    for name in _LEGACY_SENSITIVE_LOG_NAMES:
        candidate = root / name
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError("legacy sensitive log purge failed") from exc


PLATFORMS = [Platform.AI_TASK, Platform.CONVERSATION]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ExtendedOpenAIConfigEntry = ConfigEntry[AsyncClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up OpenAI Conversation."""
    try:
        await hass.async_add_executor_job(
            _purge_legacy_sensitive_logs, hass.config.config_dir
        )
    except Exception as exc:  # fail closed before private legacy APIs register
        _LOGGER.error(
            "Legacy sensitive log purge failed closed: %s", type(exc).__name__
        )
        return False
    await async_migrate_integration(hass)
    await async_setup_services(hass, config)
    # Register the routing-log REST view for the Home app's /route-log
    # slash command. Safe to re-register on reload; HA's register_view
    # is idempotent w.r.t. duplicate registration via the view name.
    try:
        hass.http.register_view(RoutingLogView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register RoutingLogView: %s", e)
    try:
        hass.http.register_view(WorldStateView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register WorldStateView: %s", e)
    try:
        hass.http.register_view(RecentOverridesView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register RecentOverridesView: %s", e)
    try:
        hass.http.register_view(LightingDecisionsView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register LightingDecisionsView: %s", e)
    try:
        hass.http.register_view(RecentPendingPreferencesView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register RecentPendingPreferencesView: %s", e)
    try:
        hass.http.register_view(LightingEvidenceStatusView())
    except Exception as e:  # noqa: BLE001 - never block setup on this
        _LOGGER.warning("Failed to register LightingEvidenceStatusView: %s", e)
    try:
        hass.http.register_view(LightingEvidenceExportView())
    except Exception as e:  # noqa: BLE001 - never block setup on this
        _LOGGER.warning("Failed to register LightingEvidenceExportView: %s", e)
    try:
        hass.http.register_view(SpatialModelView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register SpatialModelView: %s", e)
    try:
        hass.http.register_view(ApartmentModelView())
    except Exception as e:  # noqa: BLE001 — never block setup on this
        _LOGGER.warning("Failed to register ApartmentModelView: %s", e)

    # Identity store + Frigate sync (Addendum 14 / Slice 2). The store is
    # the durable record of identities + relationships + preferences; the
    # Frigate sync seeds it from Frigate's face library at boot. Both are
    # gated by env vars for one-knob rollback (per Addendum 14).
    for view_cls in (
        IdentityListView, IdentityDetailView, IdentityCreateView,
        RelationshipsView, PreferencesView, IdentityBackupView,
        AvatarView, FrigateProxyView,
    ):
        try:
            hass.http.register_view(view_cls())
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("Failed to register %s: %s", view_cls.__name__, e)

    identity_store = IdentityStore(hass)
    try:
        # SQLite open + schema apply runs in executor; not allowed on the
        # event loop. The store's setup() is synchronous + cheap.
        await hass.async_add_executor_job(identity_store.setup)
    except Exception as e:  # noqa: BLE001 — never break HA startup on this
        # Addendum 16 F-4a: previously WARNING; now ERROR. A broken
        # SQLite open is invisible to the user otherwise — the integration
        # silently loses relationship + display_name enrichment in
        # world_state, and the people overlay shows "no identities" with
        # no way to tell the difference between "store empty" and "store
        # broken". Setting setup_error on the store + bumping log level
        # surfaces the failure end-to-end (the IdentityListView reads
        # setup_error and reports `ready: false` so the Tauri overlay
        # can render a clear banner per F-4b).
        _LOGGER.error("IdentityStore setup FAILED: %s", e)
        identity_store.setup_error = str(e)
    hass.data.setdefault(DOMAIN, {})["identity_store"] = identity_store
    # Wire the HA bus into the store so it can fire identity_mutation
    # events from _audit() (F-5a). The store doesn't depend on hass at
    # construct time; we set the attribute lazily so audit fires can
    # be no-op in pytest. Tests pass `hass=None` and won't trigger.
    identity_store.set_hass_for_events(hass)

    # Frigate auto-seed: only runs if the store is empty. If it has rows,
    # this is a no-op. If Frigate is offline, returns an error report
    # without raising; the people tab can surface that for retry.
    try:
        seed_report = await auto_seed_identities_from_frigate(identity_store)
        if seed_report.attempted:
            _LOGGER.info(
                "Frigate auto-seed: found %d, created %d, skipped existing %d, errors %d",
                seed_report.frigate_faces_found,
                seed_report.identities_created,
                seed_report.identities_skipped_existing,
                len(seed_report.errors),
            )
        hass.data[DOMAIN]["frigate_seed_report"] = seed_report
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("Frigate auto-seed crashed: %s", e)

    # Probe Frigate capabilities once for the people-tab status indicator.
    try:
        caps = await probe_frigate_capabilities()
        hass.data[DOMAIN]["frigate_caps"] = caps
        _LOGGER.info(
            "Frigate capabilities: reachable=%s version=%s face_library=%s",
            caps.reachable, caps.version, caps.face_library,
        )
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("Frigate capability probe crashed: %s", e)

    # Instantiate the world-state aggregator and hang it off hass.data
    # so the world-state tools can find it. The aggregator decides
    # internally whether it's enabled (env var). Setup is async because
    # it scans the state machine on init.
    aggregator = WorldStateAggregator(hass)
    try:
        await aggregator.async_setup()
    except Exception as e:  # noqa: BLE001 — never break HA startup on this
        _LOGGER.warning("WorldStateAggregator setup failed: %s", e)
    # Slice 8: hand the store to the aggregator so _render_person
    # enriches with identity_context (relationship_type, privacy flags,
    # display_name substitution).
    try:
        aggregator.set_identity_store(identity_store)
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("Failed to wire IdentityStore into aggregator: %s", e)
    hass.data.setdefault(DOMAIN, {})["world_state"] = aggregator

    # Slice 11: periodic drainer for the pending_writes outbox (Frigate
    # rename + delete push-back). Runs every 60 seconds; cheap when
    # outbox is empty.
    async def _drain_loop():
        import asyncio as _asyncio
        while True:
            try:
                await _asyncio.sleep(60)
                summary = await drain_pending_writes(identity_store)
                if summary.get("processed", 0) > 0:
                    _LOGGER.info("pending_writes drainer: %s", summary)
            except _asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("pending_writes drainer iteration failed: %s", e)
    try:
        import asyncio as _asyncio
        drainer_task = _asyncio.create_task(_drain_loop())
        hass.data[DOMAIN]["pending_writes_drainer_task"] = drainer_task

        # Addendum 16 F-3: cancel the drainer on HA shutdown. Drainer
        # is created in async_setup (component-level), NOT async_setup_entry,
        # so async_unload_entry never runs for it. Without this cleanup,
        # HA reloads would leak tasks (drainer's `while True` never exits).
        # Reuse the existing EVENT_HOMEASSISTANT_STOP path that HA fires
        # before its own shutdown.
        from homeassistant.const import EVENT_HOMEASSISTANT_STOP

        async def _shutdown_drainer(_event):
            task = hass.data.get(DOMAIN, {}).get("pending_writes_drainer_task")
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except _asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("drainer cleanup: %s", exc)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _shutdown_drainer)
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("Failed to launch pending_writes drainer: %s", e)

    # Addendum 19 (2026-05-17): periodic Frigate face-library poll.
    # Originally auto-seed ran once at integration startup and skipped
    # when the store was non-empty. Result: any face added to Frigate
    # AFTER the first boot (user trains a new person, creates a new
    # face bucket) never appeared in the identity_store, people tab,
    # or LLM tools. With the per-face dedup (resolve_frigate_name) now
    # handling idempotency, calling auto_seed periodically is safe AND
    # closes the discovery gap.
    # Polls every 5 minutes; cheap when no new faces (one /api/faces
    # GET + dedup check per bucket).
    async def _frigate_reseed_loop():
        import asyncio as _asyncio
        while True:
            try:
                await _asyncio.sleep(300)  # 5 min
                rep = await auto_seed_identities_from_frigate(identity_store)
                if rep.identities_created > 0:
                    _LOGGER.info(
                        "Frigate reseed: created %d new identities from %d Frigate faces",
                        rep.identities_created, rep.frigate_faces_found,
                    )
            except _asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Frigate reseed iteration failed: %s", e)
    try:
        import asyncio as _asyncio
        reseed_task = _asyncio.create_task(_frigate_reseed_loop())
        hass.data[DOMAIN]["frigate_reseed_task"] = reseed_task

        from homeassistant.const import EVENT_HOMEASSISTANT_STOP

        async def _shutdown_reseed(_event):
            task = hass.data.get(DOMAIN, {}).get("frigate_reseed_task")
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except _asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("reseed cleanup: %s", exc)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _shutdown_reseed)
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("Failed to launch Frigate reseed task: %s", e)

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ExtendedOpenAIConfigEntry
) -> bool:
    """Set up OpenAI Conversation from a config entry."""

    try:
        client = await get_authenticated_client(
            hass=hass,
            api_key=entry.data[CONF_API_KEY],
            base_url=entry.data.get(CONF_BASE_URL),
            api_version=entry.data.get(CONF_API_VERSION),
            organization=entry.data.get(CONF_ORGANIZATION),
            skip_authentication=entry.data.get(
                CONF_SKIP_AUTHENTICATION, DEFAULT_SKIP_AUTHENTICATION
            ),
            api_provider=entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER),
        )
    except AuthenticationError as err:
        _LOGGER.error("Invalid API key: %s", err)
        return False
    except OpenAIError as err:
        raise ConfigEntryNotReady(err) from err

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await async_setup_templates(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload OpenAI."""
    await async_unload_templates(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_integration(hass: HomeAssistant) -> None:
    """Migrate integration entry structure."""

    # Make sure we get enabled config entries first
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda e: e.disabled_by is not None,
    )
    if not any(entry.version == 1 for entry in entries):
        return

    for entry in entries:
        _LOGGER.warning(
            "Migrating Extended OpenAI Conversation config entry %s from version %s to version 2",
            entry.entry_id,
            entry.version,
        )
        subentry = ConfigSubentry(
            data=entry.options,
            subentry_type="conversation",
            title=entry.title,
            unique_id=None,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        hass.config_entries.async_update_entry(
            entry, title=entry.title, options={}, version=2
        )

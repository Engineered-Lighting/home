"""Structured world state aggregator for the Home app.

Centralizes identity / presence / room / perception state into a single
queryable dict that the LLM agent can read via the world-state tools
(`functions/world_state.py`).

Why this exists
---------------
Identity detection lives in many places: Frigate publishes
`sensor.<camera>_last_recognized_face` per camera; HA tracks
`person.<X>` + `device_tracker.<X>`; `binary_sensor.<camera>_person_occupancy`
says "is something person-shaped here." The bridge has its own per-camera
identity events, and vision-sidecar produces prose captions.

Before this module, the conversation agent saw NONE of it as a
structured signal — it would answer "I see a human" instead of
"I see Marcelo" because there was no tool to map a recognized face to
a room. See `~/.claude/plans/keen-doodling-parasol.md` Addendum 4 for
the full design.

What this module does
---------------------
- Subscribes to `state_changed` for relevant entities only (cameras,
  occupancy sensors, face-rec sensors, person/device_tracker).
- Maintains a single in-memory dict with TWO indices kept in sync via
  a single write path (`_apply_detection`):
    rooms[<room>]   → occupancy + persons + perception + freshness
    people[<name>]  → location history + currently-seen flag
- Computes confidence bands + freshness from tunable constants.
- Throttle-fires `extended_openai_conversation.world_state_updated`
  events for live subscribers (Phase 4B's Tauri drawer).
- Exposes get/find helpers that the world-state tools return verbatim,
  including a `suggested_phrasing` field with the correctly-hedged
  sentence (so the agent doesn't have to reason about confidence rules
  every turn).

Disable
-------
Set env var `EXTENDED_OPENAI_WORLD_STATE=off` to disable. Aggregator
becomes a no-op; tools return `{"error": "world state disabled"}`.
Single rollback knob if anything goes sideways.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Optional

# HA imports are guarded so this module can be imported standalone for
# pytest (mirrors the external_routing.py pattern). At runtime inside HA
# the real symbols are used; in tests the no-op stubs are sufficient
# because tests drive the aggregator's internal API directly.
if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant

try:
    from homeassistant.core import callback
except ImportError:  # pragma: no cover — only true in standalone tests
    def callback(f):  # type: ignore[no-redef]
        return f

try:
    from homeassistant.helpers.event import async_track_state_change_event
except ImportError:  # pragma: no cover
    async_track_state_change_event = None  # type: ignore[assignment]

try:
    from homeassistant.const import EVENT_STATE_CHANGED
except ImportError:  # pragma: no cover
    EVENT_STATE_CHANGED = "state_changed"  # fallback for standalone tests

# Dual import to support both package mode (HA at runtime) and standalone
# mode (pytest on workstation without HA installed).
try:
    from .const import (
        CAMERA_TO_ROOM,
        EVENT_PERCEPTION_CAPTION,
        EVENT_WORLD_STATE_UPDATED,
        FRESH_SECONDS,
        FRIGATE_EVENT_DEFAULT_CONFIDENCE,
        FRIGATE_PERSON_TRACKER_DEFAULT_CONFIDENCE,
        HA_PERSON_TO_CANONICAL,
        IDENTITY_CONFIDENCE_HIGH,
        IDENTITY_CONFIDENCE_MEDIUM,
        PERCEPTION_AUTO_RATE_CAP_PER_MIN,
        PERCEPTION_AUTO_TIMEOUT_S,
        PERCEPTION_AUTO_TRIGGER_DEBOUNCE_S,
        PERSON_HISTORY_DEPTH,
        PERSON_NAME_ALIASES,
        PRIMARY_USER_NAME,
        RECENT_SECONDS,
        STALE_SECONDS,
        VISION_SIDECAR_URL,
        WORLD_STATE_DISABLED_ENV,
        WORLD_STATE_EVENT_THROTTLE_S,
    )
except ImportError:  # pragma: no cover — only true in standalone tests
    from const import (  # type: ignore[no-redef]
        CAMERA_TO_ROOM,
        EVENT_PERCEPTION_CAPTION,
        EVENT_WORLD_STATE_UPDATED,
        FRESH_SECONDS,
        FRIGATE_EVENT_DEFAULT_CONFIDENCE,
        FRIGATE_PERSON_TRACKER_DEFAULT_CONFIDENCE,
        HA_PERSON_TO_CANONICAL,
        IDENTITY_CONFIDENCE_HIGH,
        IDENTITY_CONFIDENCE_MEDIUM,
        PERCEPTION_AUTO_RATE_CAP_PER_MIN,
        PERCEPTION_AUTO_TIMEOUT_S,
        PERCEPTION_AUTO_TRIGGER_DEBOUNCE_S,
        PERSON_HISTORY_DEPTH,
        PERSON_NAME_ALIASES,
        PRIMARY_USER_NAME,
        RECENT_SECONDS,
        STALE_SECONDS,
        VISION_SIDECAR_URL,
        WORLD_STATE_DISABLED_ENV,
        WORLD_STATE_EVENT_THROTTLE_S,
    )

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# State filters: entity_id patterns we subscribe to. Anything else is
# ignored at the source so we don't waste cycles on every HA state change.
# ─────────────────────────────────────────────────────────────────────
_FACE_SENSOR_RE = re.compile(r"^sensor\.([a-z0-9_]+)_last_recognized_face$")
_OCCUPANCY_RE = re.compile(r"^binary_sensor\.([a-z0-9_]+)_person_occupancy$")
_PERSON_RE = re.compile(r"^person\.([a-z0-9_]+)$")
_DEVICE_TRACKER_RE = re.compile(r"^device_tracker\.([a-z0-9_]+)$")
_CAMERA_RE = re.compile(r"^camera\.([a-z0-9_]+)$")
# Frigate's per-person tracker — the authoritative "where is X right
# now" signal. Frigate publishes a separate sensor per known person
# whose state is the camera where they were last seen.
_FRIGATE_PERSON_RE = re.compile(r"^sensor\.frigate_([a-z0-9_]+)_last_camera$")


# ─────────────────────────────────────────────────────────────────────
# Confidence + freshness helpers
# ─────────────────────────────────────────────────────────────────────
def _confidence_band(score: Optional[float]) -> str:
    """Return 'high' | 'medium' | 'low' | 'unknown' for a 0..1 score."""
    if score is None:
        return "unknown"
    if score >= IDENTITY_CONFIDENCE_HIGH:
        return "high"
    if score >= IDENTITY_CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


def _freshness(age_seconds: Optional[float]) -> str:
    """Return 'fresh' | 'recent' | 'stale' | 'none' for an age in seconds."""
    if age_seconds is None:
        return "none"
    if age_seconds <= FRESH_SECONDS:
        return "fresh"
    if age_seconds <= RECENT_SECONDS:
        return "recent"
    return "stale"


def _camera_to_room(entity_id: str) -> Optional[str]:
    """Resolve a camera entity_id to a room name.

    Cameras: first the explicit override dict, then "the whole slug
    after `camera.`" as the fallback. Sensor/binary_sensor entities
    DON'T go through this — their `_apply_*` methods already extracted
    the room slug from the entity_id regex group.
    """
    if entity_id in CAMERA_TO_ROOM:
        return CAMERA_TO_ROOM[entity_id]
    m = _CAMERA_RE.match(entity_id)
    if m:
        return m.group(1)
    return None


def _humanize_age(seconds: float) -> str:
    """Render a friendly 'X ago' phrase for the suggested_phrasing field."""
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    if seconds < 3600:
        m = max(1, int(seconds // 60))
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = max(1, int(seconds // 3600))
    return f"{h} hour{'s' if h != 1 else ''} ago"


# ─────────────────────────────────────────────────────────────────────
# Detection record — the unit fed through the single write path.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class _Detection:
    """One identity detection from a single source (face-rec, person.*, etc.)."""

    source: str  # "frigate_face" | "ha_person" | "ha_device_tracker"
    person_name: Optional[str]  # None for unknown/generic person
    room: Optional[str]
    camera_entity_id: Optional[str]
    confidence: Optional[float]
    ts: float  # unix timestamp


# ─────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────
class WorldStateAggregator:
    """In-memory aggregator over HA identity/presence/occupancy entities.

    Lifecycle: instantiated once in `async_setup_entry`, hangs off the
    config entry's runtime data. `async_setup()` scans current state and
    registers the state_changed subscription; `async_unload()` removes it.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.enabled = os.environ.get(WORLD_STATE_DISABLED_ENV, "").strip().lower() != "off"
        # rooms[<room>] = {occupied, frigate_labels, cameras, persons, perception_*}
        self._rooms: dict[str, dict[str, Any]] = defaultdict(self._empty_room)
        # people[<name>] = {ha_location, last_visual_room, ...}
        self._people: dict[str, dict[str, Any]] = defaultdict(self._empty_person)
        # per-person rolling history of detections (newest last)
        self._history: dict[str, deque[_Detection]] = defaultdict(
            lambda: deque(maxlen=PERSON_HISTORY_DEPTH)
        )
        # entity_ids we subscribe to (set at setup)
        self._tracked_entities: set[str] = set()
        self._unsub_listener = None
        # Addendum 20: subscription to Frigate's `frigate_event` HA-bus
        # event. This is the AUTHORITATIVE face-rec signal because the
        # per-camera + per-person sensors often don't update in current
        # Frigate versions, but every detection event fires here with
        # the sub_label populated.
        self._unsub_frigate_event = None
        # throttle for the world_state_updated event
        self._last_event_fire_ts: float = 0.0
        # Identity store enrichment (Addendum 14 / Slice 8). Optional —
        # set by the integration in __init__.py after both are constructed.
        # When None, _render_person omits identity_context cleanly.
        self._identity_store = None  # type: ignore[assignment]
        # M1 event-aware perception scheduler state (Addendum 27 / M1).
        # _perception_last_dispatch[room] = ts of most recent auto-dispatch
        # used for the 30s per-room debounce gate.
        # _perception_recent_dispatches = rolling timestamps of every auto
        # dispatch in the last 60s; len() compared to global cap before
        # firing. Bounded length so a quiet system doesn't accumulate
        # forever.
        self._perception_last_dispatch: dict[str, float] = {}
        self._perception_recent_dispatches: deque[float] = deque(
            maxlen=PERCEPTION_AUTO_RATE_CAP_PER_MIN * 4
        )
        # Track previous occupancy state per room to detect 0→1 transitions
        # (versus subsequent on-events for the same continuous person).
        self._previous_occupancy: dict[str, bool] = {}
        # ── Override observation buffer (First-PR scope) ──
        # Rolling 1-hour window of `living_lights_override_detected` events.
        # The agent's `get_recent_overrides()` tool reads from here.
        # `_override_unsub` is set by async_setup() and cleared in async_unload().
        self._override_buffer: deque[dict[str, Any]] = deque(maxlen=200)
        self._override_unsub = None

    def set_identity_store(self, store) -> None:
        """Wire the identity store. Called by the integration setup after
        both the aggregator and the store are instantiated. Loose-coupling
        on purpose — the aggregator never imports identity_store directly,
        so the world_state module stays testable without the store."""
        # A fenced legacy database is historical evidence only. Keeping this
        # defense here (in addition to integration setup) ensures no future
        # caller can accidentally wire names, notes, relationships, or privacy
        # metadata into model-facing WorldState projections.
        if getattr(store, "semantic_writes_frozen", True):
            self._identity_store = None
            return
        self._identity_store = store

    # ─── lifecycle ──────────────────────────────────────────────────
    async def async_setup(self) -> None:
        """Scan HA registry, build initial state, subscribe to changes."""
        if not self.enabled:
            _LOGGER.info("world_state: disabled via %s=off", WORLD_STATE_DISABLED_ENV)
            return

        # Best-effort initial scan: discover entities that already have
        # state at setup time. Frigate entities may not be added yet
        # (HA loads integrations asynchronously) — those are handled
        # by the broad state_changed subscription below.
        for state in self.hass.states.async_all():
            if self._is_tracked_entity(state.entity_id):
                self._tracked_entities.add(state.entity_id)
                # Seed initial state — feed each entity through the same
                # ingest path as a live state_changed event.
                self._ingest(state.entity_id, None, state)

        _LOGGER.info(
            "world_state: seeded from %d tracked entities at boot across %d rooms",
            len(self._tracked_entities),
            len(self._rooms),
        )

        # Addendum 19 fix: subscribe to the BROAD state_changed event,
        # filter via _is_tracked_entity in the callback. The old design
        # passed a fixed list of entity IDs to async_track_state_change_event,
        # which meant late-added entities (Frigate restart, integration
        # loaded after world_state) were never tracked. The broad
        # subscription also self-heals: any new tracked entity that
        # appears later gets ingested on its first state event.
        if self.hass and hasattr(self.hass, "bus"):
            self._unsub_listener = self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._handle_state_change_broad,
            )

        # Addendum 20: subscribe to Frigate's `frigate_event` HA-bus
        # event. This is the authoritative face-rec signal because the
        # per-camera face sensor + per-person tracker sensor don't
        # update reliably in current Frigate versions, but every
        # detection event fires this with the sub_label populated.
        if self.hass and hasattr(self.hass, "bus"):
            self._unsub_frigate_event = self.hass.bus.async_listen(
                "frigate_event", self._handle_frigate_event,
            )

        # ── Override observation buffer (First-PR scope) ──
        # Subscribe to living_lights_override_detected events so the agent's
        # get_recent_overrides() tool has factual context to answer "why did
        # the office dim?" / "show me my recent overrides" within the last
        # hour. Buffer is in-memory only; persistence lives in
        # /config/lighting_preferences_pending.jsonl (gated separately).
        if self.hass and hasattr(self.hass, "bus"):
            self._override_unsub = self.hass.bus.async_listen(
                "living_lights_override_detected",
                self._handle_override_detected,
            )

    async def async_unload(self) -> None:
        """Remove the state_changed + frigate_event + override subscriptions."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        if self._unsub_frigate_event is not None:
            self._unsub_frigate_event()
            self._unsub_frigate_event = None
        if self._override_unsub is not None:
            try:
                self._override_unsub()
            except Exception:  # pylint: disable=broad-except
                pass
            self._override_unsub = None

    # ─── living_lights override observation buffer (First-PR scope) ──
    @callback
    def _handle_override_detected(self, event) -> None:
        """Append the override event to the in-memory rolling buffer.

        The buffer is a deque(maxlen=200); older entries fall off naturally.
        `recent_overrides()` filters by wall-clock recency on read.
        """
        try:
            data = dict(event.data) if event.data else {}
            # Stamp with the event's wall-clock time for filtering. HA's
            # event.time_fired is a datetime; persist as ISO so the buffer
            # entries are JSON-serializable for the REST endpoint.
            try:
                event_ts = event.time_fired
                if hasattr(event_ts, "isoformat"):
                    data["_buffer_ts_iso"] = event_ts.isoformat()
                    data["_buffer_ts_epoch"] = event_ts.timestamp()
                else:
                    now = datetime.now(timezone.utc)
                    data["_buffer_ts_iso"] = now.isoformat()
                    data["_buffer_ts_epoch"] = now.timestamp()
            except Exception:  # pylint: disable=broad-except
                now = datetime.now(timezone.utc)
                data["_buffer_ts_iso"] = now.isoformat()
                data["_buffer_ts_epoch"] = now.timestamp()
            self._override_buffer.append(data)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("world_state: override buffer append failed: %s", exc)

    def recent_overrides(
        self,
        hours: float = 1.0,
        zone: str | None = None,
        collapse: bool = True,
        session_window_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return override events from the last <hours> hours, optionally
        filtered to a single zone slug. Newest-last ordering.

        Capped at the deque size (200) and the wall-clock window. For
        windows longer than ~1 hour the caller should fall back to the
        on-disk JSONL — V1 only supports the in-memory buffer.

        M20: `collapse=True` (default) folds bursts of override_events
        on the same light within `session_window_s` into one logical
        manual-adjustment session where the final value wins. Pass
        `collapse=False` to get the raw burst for debugging.
        """
        try:
            hours_f = float(hours) if hours is not None else 1.0
        except (TypeError, ValueError):
            hours_f = 1.0
        if hours_f <= 0:
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - (hours_f * 3600.0)
        zone_filter = (zone or "").strip().lower() or None
        out: list[dict[str, Any]] = []
        for entry in self._override_buffer:
            try:
                ts = float(entry.get("_buffer_ts_epoch", 0.0))
                if ts < cutoff:
                    continue
                if zone_filter and (entry.get("zone") or "").lower() != zone_filter:
                    continue
                # Return a shallow copy with the internal _buffer_* keys
                # stripped so the caller sees only the event payload.
                out.append({k: v for k, v in entry.items()
                            if not k.startswith("_buffer_")})
            except Exception:  # pylint: disable=broad-except
                continue
        if collapse and out:
            # Lazy import — keeps test imports of world_state.py clean
            # even when the integration package layout shifts.
            try:
                from ._override_sessions import (
                    collapse_into_sessions, DEFAULT_SESSION_WINDOW_S,
                )
                win = (float(session_window_s)
                       if session_window_s is not None
                       else DEFAULT_SESSION_WINDOW_S)
                out = collapse_into_sessions(out, session_window_s=win)
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "world_state: session collapse failed (%s); returning raw events",
                    exc,
                )
        return out

    # ─── event handling ─────────────────────────────────────────────
    @callback
    def _handle_state_change(self, event: Event) -> None:
        """state_changed callback. Dispatches to _ingest then fires the
        throttled world_state_updated event.

        Used by tests + the legacy fixed-entity-list subscription path.
        Production calls _handle_state_change_broad instead, which adds
        the tracking-filter so it can subscribe broadly."""
        try:
            data = event.data
            entity_id = data.get("entity_id")
            new_state = data.get("new_state")
            old_state = data.get("old_state")
            if not entity_id:
                return
            self._ingest(entity_id, old_state, new_state)
            self._maybe_fire_update_event()
        except Exception as e:  # noqa: BLE001 — never let the aggregator crash HA
            _LOGGER.debug("world_state: ingest failed for %s: %s", entity_id, e)

    @callback
    def _handle_frigate_event(self, event: Event) -> None:
        """Frigate's `frigate_event` HA-bus callback.

        This is the AUTHORITATIVE face-rec signal (Addendum 20). In
        current Frigate versions the per-camera `_last_recognized_face`
        sensor + the per-person `frigate_<person>_last_camera` sensor
        often don't update — but the bus event fires for every
        detection event with the sub_label populated.

        Payload shape across Frigate releases:
        - Newer (Frigate 0.13+):  {type, before, after}  with after.sub_label
        - Flat:                    {camera, label, sub_label, ...}
        - Tuple sub_label:        sub_label = ["marcelo", 0.94]
        We normalize all three.
        """
        try:
            data = event.data or {}
            # Prefer `after` if present (newer shape); fall back to root.
            after = data.get("after") or data
            camera = after.get("camera") or data.get("camera")
            sub_label = after.get("sub_label")
            if not camera or not sub_label:
                return
            # Normalize sub_label: can be string OR [name, score] tuple
            if isinstance(sub_label, (list, tuple)) and len(sub_label) >= 1:
                name = sub_label[0]
                score = sub_label[1] if len(sub_label) > 1 else None
            else:
                name = sub_label
                score = after.get("top_score") or after.get("score")
            name = (name or "").strip()
            if not name or name.lower() in {"unknown", "none", "null"}:
                return
            try:
                score_val = float(score) if score is not None else FRIGATE_EVENT_DEFAULT_CONFIDENCE
            except (TypeError, ValueError):
                score_val = FRIGATE_EVENT_DEFAULT_CONFIDENCE

            camera_entity = camera if camera.startswith("camera.") else f"camera.{camera}"
            # Addendum 28 / acute fix: defensive validation. frigate_event
            # payloads have historically been real cameras, but the same
            # phantom-zone bug class would apply if Frigate ever emitted
            # zone-scoped events. Skip if the camera entity is absent.
            if self.hass is not None and self.hass.states.get(camera_entity) is None:
                _LOGGER.debug(
                    "world_state: skipping frigate_event for %s — no camera entity",
                    camera_entity,
                )
                return
            room = _camera_to_room(camera_entity) or camera_entity[len("camera."):]

            det = _Detection(
                source="frigate_event",
                person_name=name.title() if name == name.lower() else name,
                room=room,
                camera_entity_id=camera_entity,
                confidence=score_val,
                ts=time.time(),
            )
            self._apply_detection(det)
            self._maybe_fire_update_event()
            # M1: every face-rec event is a high-value perception
            # trigger — we just learned the room has a NAMED person, so
            # pre-fetch the caption now while the camera frame is fresh.
            # Debounce + rate cap keep it from saturating under storms.
            self._schedule_perception_async(room, reason="frigate_event")
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("world_state: frigate_event ingest failed: %s", e)

    @callback
    def _handle_state_change_broad(self, event: Event) -> None:
        """Broad state_changed callback used in production. Filters via
        _is_tracked_entity so we ignore events for entities we don't
        care about (lights, switches, automations, etc.). Auto-adds
        newly-tracked entities to _tracked_entities so debugging /
        get_world_state can introspect them.

        This is the fix for the Addendum 19 startup-order race: when
        Frigate entities are added to HA AFTER world_state.async_setup
        runs, the old fixed-list subscription missed them entirely.
        Broad subscription with in-callback filter handles late
        arrivals correctly."""
        try:
            data = event.data
            entity_id = data.get("entity_id", "")
            if not entity_id or not self._is_tracked_entity(entity_id):
                return
            if entity_id not in self._tracked_entities:
                self._tracked_entities.add(entity_id)
                _LOGGER.info(
                    "world_state: late-discovered tracked entity %s", entity_id,
                )
            self._ingest(entity_id, data.get("old_state"), data.get("new_state"))
            self._maybe_fire_update_event()
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("world_state: ingest failed for %s: %s", entity_id, e)

    def _is_tracked_entity(self, entity_id: str) -> bool:
        return bool(
            _FRIGATE_PERSON_RE.match(entity_id)
            or _FACE_SENSOR_RE.match(entity_id)
            or _OCCUPANCY_RE.match(entity_id)
            or _PERSON_RE.match(entity_id)
            or _DEVICE_TRACKER_RE.match(entity_id)
            or _CAMERA_RE.match(entity_id)
        )

    def _ingest(self, entity_id: str, old_state, new_state) -> None:
        """Single entry point for all entity updates. Routes to the
        appropriate _apply_* method. _apply_* is the only place that
        writes to self._rooms / self._people, preventing index drift."""
        if new_state is None:
            return
        # Check frigate per-person tracker FIRST — it also matches the
        # generic `sensor.X` pattern, but its semantics (per-person, not
        # per-camera) are different. Order matters.
        fp_m = _FRIGATE_PERSON_RE.match(entity_id)
        if fp_m:
            self._apply_frigate_person(fp_m.group(1), new_state)
            return
        face_m = _FACE_SENSOR_RE.match(entity_id)
        if face_m:
            self._apply_face_sensor(face_m.group(1), new_state)
            return
        occ_m = _OCCUPANCY_RE.match(entity_id)
        if occ_m:
            self._apply_occupancy(occ_m.group(1), new_state)
            return
        person_m = _PERSON_RE.match(entity_id)
        if person_m:
            self._apply_person(person_m.group(1), new_state)
            return
        # device_tracker and camera contribute context but no detection
        # records of their own — handled via lookups when assembling the
        # output dicts.

    # ─── single write path ──────────────────────────────────────────
    def _apply_detection(self, det: _Detection) -> None:
        """The only place that mutates rooms[] AND people[] together.
        Keeps the two indices in sync so they never drift."""
        # Push to per-person history.
        if det.person_name:
            self._history[det.person_name].append(det)

        # Update the room slot.
        if det.room:
            room = self._rooms[det.room]
            if det.camera_entity_id and det.camera_entity_id not in room["cameras"]:
                room["cameras"].append(det.camera_entity_id)
            # Replace any prior identity entry for this person in this room
            # (the latest detection wins for "who is currently here").
            room["persons"] = [
                p for p in room["persons"]
                if (p.get("identity") or {}).get("name") != det.person_name
                or det.person_name is None
            ]
            if det.person_name:
                room["persons"].append({
                    "identity": {
                        "name": det.person_name,
                        "confidence": det.confidence,
                        "confidence_band": _confidence_band(det.confidence),
                        "source": det.source,
                    },
                    "generic_person_detected": True,
                    "source_camera": det.camera_entity_id,
                    "last_seen_at_ts": det.ts,
                })

        # Update the per-person index. Any visual-grounding source
        # (frigate_face per-camera sensor OR frigate_person_tracker
        # per-person sensor) updates last_visual_*. Sources that don't
        # confirm a room (e.g., ha_person from a presence entity)
        # don't update visual fields.
        VISUAL_SOURCES = {"frigate_face", "frigate_person_tracker", "frigate_event"}
        if det.person_name and det.source in VISUAL_SOURCES and det.room:
            person = self._people[det.person_name]
            person["last_visual_room"] = det.room
            person["last_visual_camera"] = det.camera_entity_id
            person["last_visual_at_ts"] = det.ts
            person["last_visual_confidence"] = det.confidence

    def _apply_face_sensor(self, camera_room_token: str, state) -> None:
        """sensor.<camera>_last_recognized_face → identity detection.

        Frigate convention: state is the recognized person name (or
        'Unknown'/'None'/'unavailable'); attributes carry `score` or
        `confidence` as a float. We treat 'Unknown'/'None'/empty as no
        identity.
        """
        raw_name = (state.state or "").strip()
        if raw_name.lower() in {"unknown", "none", "unavailable", "", "off"}:
            return
        attrs = state.attributes or {}
        score = attrs.get("score")
        if score is None:
            score = attrs.get("confidence")
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None

        # `camera_room_token` here is the slug from `sensor.X_last_recognized_face`.
        # The corresponding camera entity is `camera.<token>` (per CAMERA_TO_ROOM
        # convention). The room is whatever the entity resolves to.
        # Addendum 28 / acute fix: same defensive validation as
        # _apply_occupancy — if the camera entity doesn't exist, the
        # sensor refers to a zone or stale config; ignore.
        camera_entity = f"camera.{camera_room_token}"
        if self.hass is not None and self.hass.states.get(camera_entity) is None:
            _LOGGER.debug(
                "world_state: skipping face sensor for %s — no camera entity %s",
                camera_room_token, camera_entity,
            )
            return
        room = _camera_to_room(camera_entity) or camera_room_token

        det = _Detection(
            source="frigate_face",
            person_name=raw_name,
            room=room,
            camera_entity_id=camera_entity,
            confidence=score,
            ts=time.time(),
        )
        self._apply_detection(det)

    def _apply_occupancy(self, camera_room_token: str, state) -> None:
        """binary_sensor.<camera>_person_occupancy → set occupancy + label.

        M1: also detects 0→1 transitions to fire the perception
        scheduler. We only schedule on the EDGE (room going from
        unoccupied → occupied), not on every "still on" repeat event,
        so a long-lived presence doesn't generate redundant captions.

        Addendum 28 / acute fix: Frigate publishes occupancy sensors for
        BOTH cameras and sub-zones (e.g. `binary_sensor.sofa_person_occupancy`,
        `binary_sensor.dining_left_person_occupancy`). Zone slugs have no
        matching `camera.<slug>` entity. Without filtering, every zone
        creates a phantom room + phantom camera that the M1 scheduler
        then tries to fetch a snapshot for — producing http_502 storms
        and burning the perception rate-cap with failures. Reject the
        event unless the corresponding camera entity actually exists.
        """
        camera_entity = f"camera.{camera_room_token}"
        if self.hass is not None and self.hass.states.get(camera_entity) is None:
            _LOGGER.debug(
                "world_state: skipping occupancy sensor for %s — no camera entity %s",
                camera_room_token, camera_entity,
            )
            return
        room_name = _camera_to_room(camera_entity) or camera_room_token
        room = self._rooms[room_name]
        if camera_entity not in room["cameras"]:
            room["cameras"].append(camera_entity)
        is_on = (state.state or "").lower() == "on"
        was_on = self._previous_occupancy.get(room_name, False)
        room["occupied"] = is_on
        labels = set(room.get("frigate_labels") or [])
        if is_on:
            labels.add("person")
        else:
            labels.discard("person")
        room["frigate_labels"] = sorted(labels)
        self._previous_occupancy[room_name] = is_on
        # M1: schedule on rising edge only (was off, now on). Off→off
        # and on→on don't carry new information for perception.
        if is_on and not was_on:
            self._schedule_perception_async(room_name, reason="occupancy")

    def _apply_person(self, person_token: str, state) -> None:
        """person.<name> → HA presence (home/not_home/<zone>).

        Canonical-name mapping (HA_PERSON_TO_CANONICAL) lets us unify HA
        user accounts with face-rec identities — e.g., the admin account
        `person.engineeredlighting` maps to "Marcelo" so the same person
        index merges visual + presence signals.
        """
        if person_token in HA_PERSON_TO_CANONICAL:
            display = HA_PERSON_TO_CANONICAL[person_token]
        else:
            attrs = state.attributes or {}
            display = (
                attrs.get("friendly_name")
                or person_token.replace("_", " ").title()
            )
            display = " ".join(str(display).split())
        person = self._people[display]
        person["ha_location"] = state.state or "unknown"
        person["ha_location_entity_id"] = f"person.{person_token}"

    def _apply_frigate_person(self, person_slug: str, state) -> None:
        """sensor.frigate_<person>_last_camera → authoritative per-person location.

        Frigate publishes this sensor for every trained face. Its state
        is the camera (entity slug, friendly name, or `camera.X` form)
        where the person was most recently identified. This is THE
        canonical signal for "where is X right now" — better than
        scanning per-camera face-rec sensors because it's already
        de-duplicated and per-person.

        Frigate only updates on confident matches, so we treat as
        high-confidence by default unless the sensor attributes carry
        a more precise `score`.
        """
        camera_value = (state.state or "").strip()
        if camera_value.lower() in {"unknown", "none", "unavailable", "", "off"}:
            return
        person_name = person_slug.replace("_", " ").title()
        attrs = state.attributes or {}
        score = attrs.get("score") or attrs.get("confidence")
        try:
            score = float(score) if score is not None else FRIGATE_PERSON_TRACKER_DEFAULT_CONFIDENCE
        except (TypeError, ValueError):
            score = FRIGATE_PERSON_TRACKER_DEFAULT_CONFIDENCE

        # Resolve camera value to entity_id + room. Frigate's state value
        # is usually the bare camera slug ("kitchen") but can also be
        # the friendly name ("Living Room") or a full entity_id.
        if camera_value.startswith("camera."):
            camera_entity = camera_value
        else:
            normalized = camera_value.lower().replace(" ", "_")
            camera_entity = f"camera.{normalized}"
        room = _camera_to_room(camera_entity) or camera_entity[len("camera."):]

        det = _Detection(
            source="frigate_person_tracker",
            person_name=person_name,
            room=room,
            camera_entity_id=camera_entity,
            confidence=score,
            ts=time.time(),
        )
        self._apply_detection(det)

    # ─── update event throttling ────────────────────────────────────
    def _maybe_fire_update_event(self) -> None:
        now = time.time()
        if now - self._last_event_fire_ts < WORLD_STATE_EVENT_THROTTLE_S:
            return
        self._last_event_fire_ts = now
        try:
            self.hass.bus.async_fire(EVENT_WORLD_STATE_UPDATED, self.get_world_state())
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("world_state: event fire failed: %s", e)

    # ─── M1: event-aware perception scheduler ───────────────────────
    def _schedule_perception_async(
        self, room: str, reason: str = "auto"
    ) -> str:
        """Maybe spawn a background vision-sidecar /describe for `room`.

        Returns the verdict string for telemetry / tests:
          - "dispatched"      → task spawned
          - "debounced"       → per-room debounce gate; skipped
          - "rate_capped"     → global cap exceeded; skipped
          - "no_camera"       → no camera entity known for room; skipped
          - "disabled"        → aggregator disabled OR no hass
          - "no_loop"         → no asyncio loop reachable (test environment)

        Auto-trigger only — manual /refresh_perception calls bypass this
        whole gate (AR27-3) and go through functions/world_state.py's
        own per-conversation budget instead.

        Wired from `_handle_frigate_event` (every face-rec match) and
        `_handle_state_change_broad` (occupancy 0→1 transition). Both
        paths reach this with the same room name so the dedupe is
        consistent. `reason` is recorded in the routing log entry for
        gap analysis; the scheduling decision itself doesn't depend on
        it.
        """
        if not self.enabled or self.hass is None:
            return "disabled"
        now = time.time()
        # Per-room debounce: 30s default. Same room within window =
        # silently skip. Manual refresh bypasses this gate by going
        # through a different code path entirely.
        last = self._perception_last_dispatch.get(room)
        if last is not None and (now - last) < PERCEPTION_AUTO_TRIGGER_DEBOUNCE_S:
            self._log_perception_async(room, "debounced", reason, age_seconds=int(now - last))
            return "debounced"
        # Global rate cap: count dispatches in the last 60s. Bounded
        # deque ensures O(1) eviction; we filter <60s old before count.
        cutoff = now - 60.0
        recent = [t for t in self._perception_recent_dispatches if t >= cutoff]
        if len(recent) >= PERCEPTION_AUTO_RATE_CAP_PER_MIN:
            self._log_perception_async(room, "rate_capped", reason,
                                       age_seconds=int(now - recent[0]))
            return "rate_capped"
        # Resolve room → first known camera entity_id. Without one we
        # can't fetch a snapshot; skip rather than guess.
        room_data = self._rooms.get(room) or {}
        cameras = room_data.get("cameras") or []
        if not cameras:
            self._log_perception_async(room, "no_camera", reason)
            return "no_camera"
        camera_entity = cameras[0]
        # Reserve the slot BEFORE spawning so a flood of events in the
        # same tick can't race past the cap.
        self._perception_last_dispatch[room] = now
        self._perception_recent_dispatches.append(now)
        # Spawn fire-and-forget on the HA event loop. We don't await
        # the result — auto-trigger is best-effort by design.
        try:
            self.hass.async_create_task(
                self._dispatch_perception(room, camera_entity, reason)
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("world_state: failed to spawn perception task: %s", e)
            return "no_loop"
        return "dispatched"

    async def _dispatch_perception(
        self, room: str, camera_entity: str, reason: str
    ) -> None:
        """Background coroutine: call vision-sidecar /describe, store the
        caption via record_perception, fire EVENT_PERCEPTION_CAPTION, and
        log to the routing log with kind=perception_dispatch."""
        import asyncio as _asyncio

        url = f"{VISION_SIDECAR_URL.rstrip('/')}/describe"
        # camera_entity is e.g. "camera.kitchen"; sidecar accepts the
        # short name too, but pass the full entity_id to match what we
        # cache snapshots under.
        camera_short = camera_entity[len("camera."):] if camera_entity.startswith("camera.") else camera_entity
        body = {"camera": camera_short}
        t0 = time.monotonic()
        caption = ""
        snapshot_url: Optional[str] = None
        verdict = "completed"
        error_kind: Optional[str] = None
        try:
            # httpx.AsyncClient per call — internal LAN HTTP, no SSL
            # context blocking warning (which only triggers on httpx).
            # The HA shared httpx client (get_async_client) requires
            # full HA context which the test harness can't provide.
            import httpx
            timeout = httpx.Timeout(PERCEPTION_AUTO_TIMEOUT_S)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body)
                if resp.status_code != 200:
                    verdict = "error"
                    error_kind = f"http_{resp.status_code}"
                else:
                    data = resp.json() or {}
                    caption = (data.get("description") or "").strip()
                    if caption:
                        self.record_perception(room, caption)
                        snapshot_url = (
                            f"{VISION_SIDECAR_URL.rstrip('/')}"
                            f"/snapshot/{camera_short}/latest.jpg"
                        )
        except _asyncio.TimeoutError:
            verdict = "timeout"
            error_kind = "TimeoutError"
        except Exception as e:  # noqa: BLE001
            verdict = "error"
            error_kind = type(e).__name__
            _LOGGER.debug("world_state: vision-sidecar dispatch failed: %s", e)
        latency_ms = int((time.monotonic() - t0) * 1000)

        # Fire HA bus event so Tauri can render the chip + thumbnail
        # without polling. Includes snapshot_url for the chat bubble.
        # Only fire on success — failures stay in the routing log.
        if caption:
            try:
                self.hass.bus.async_fire(
                    EVENT_PERCEPTION_CAPTION,
                    {
                        "room": room,
                        "caption": caption,
                        "source": reason,           # "frigate_event" / "occupancy" / "manual"
                        "snapshot_url": snapshot_url,
                        "ts": time.time(),
                        "latency_ms": latency_ms,
                        "camera_entity_id": camera_entity,
                    },
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("world_state: perception event fire failed: %s", e)

        # Log to routing_log with kind=perception_dispatch so the
        # baseline aggregator can chart auto-trigger volume + latency
        # over time. Reuses the M0.4 multi-kind schema.
        self._log_perception_async(
            room, verdict, reason,
            latency_ms=latency_ms,
            error_kind=error_kind,
            camera_entity=camera_entity,
        )

    def _log_perception_async(
        self,
        room: str,
        verdict: str,
        source: str,
        latency_ms: Optional[int] = None,
        age_seconds: Optional[int] = None,
        error_kind: Optional[str] = None,
        camera_entity: Optional[str] = None,
    ) -> None:
        """Best-effort write to the routing log with kind=perception_dispatch.
        Never raises; never blocks the scheduler if the routing log path
        is unavailable (e.g., test environment without an HA hass).
        """
        try:
            # Lazy import — keeps standalone tests from needing the
            # external_routing module loaded just for the scheduler.
            from .external_routing import LOG_KIND_PERCEPTION_DISPATCH, log_decision
        except ImportError:
            try:
                from external_routing import (  # type: ignore[no-redef]
                    LOG_KIND_PERCEPTION_DISPATCH,
                    log_decision,
                )
            except ImportError:
                return
        entry: dict[str, Any] = {
            "ts": time.time(),
            "kind": LOG_KIND_PERCEPTION_DISPATCH,
            "conv_id": None,  # auto-trigger isn't tied to a conv turn
            "room": room,
            "verdict": verdict,
            "source": source,
        }
        if latency_ms is not None:
            entry["latency_ms"] = latency_ms
        if age_seconds is not None:
            entry["age_seconds"] = age_seconds
        if error_kind is not None:
            entry["error_kind"] = error_kind
        if camera_entity is not None:
            entry["camera_entity_id"] = camera_entity
        try:
            self.hass.async_create_task(log_decision(self.hass, entry))
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("world_state: perception log_decision failed: %s", e)

    # ─── public API ─────────────────────────────────────────────────
    def get_world_state(self) -> dict[str, Any]:
        """Return the full structured world state."""
        if not self.enabled:
            return {"enabled": False, "reason": f"{WORLD_STATE_DISABLED_ENV}=off"}
        now = time.time()
        return {
            "enabled": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "rooms": {
                name: self._render_room(name, now) for name in sorted(self._rooms.keys())
            },
            "people": {
                name: self._render_person(name, now)
                for name in sorted(self._people.keys())
            },
            "system": {
                "frigate_online": self._frigate_online(),
                "tracked_entity_count": len(self._tracked_entities),
            },
        }

    def get_room_state(self, room: str) -> dict[str, Any]:
        """Return state for one room + a hedged phrasing for the agent."""
        if not self.enabled:
            return {"error": "world state disabled"}
        now = time.time()
        room_key = self._resolve_room(room)
        if room_key is None or room_key not in self._rooms:
            return {
                "data": None,
                "suggested_phrasing": f"I don't have any data for the {room} right now.",
                "confidence_band": "unknown",
                "freshness": "none",
            }
        data = self._render_room(room_key, now)
        # Pick the freshest identified person for the headline phrasing.
        identified = [
            p for p in data["persons"]
            if p.get("identity")
            and (p["identity"].get("confidence") or 0) >= IDENTITY_CONFIDENCE_MEDIUM
        ]
        identified.sort(key=lambda p: p.get("age_seconds") or 1e9)
        if identified:
            top = identified[0]
            return {
                "data": data,
                "suggested_phrasing": self._phrase_person_in_room(top, room_key),
                "confidence_band": top["identity"]["confidence_band"],
                "freshness": _freshness(top.get("age_seconds")),
            }
        if data.get("occupied"):
            return {
                "data": data,
                "suggested_phrasing": (
                    f"I see someone in the {room_key}, but I can't confirm who."
                ),
                "confidence_band": "unknown",
                "freshness": _freshness(data.get("perception_age_seconds")),
            }
        return {
            "data": data,
            "suggested_phrasing": f"I don't currently see anyone in the {room_key}.",
            "confidence_band": "unknown",
            "freshness": "none",
        }

    def find_person(self, name: str) -> dict[str, Any]:
        """Locate a person via face-rec history + HA presence."""
        if not self.enabled:
            return {"error": "world state disabled"}
        canonical = self._resolve_person(name)
        if canonical is None:
            return {
                "data": None,
                "suggested_phrasing": f"I don't have a record of anyone named {name}.",
                "confidence_band": "unknown",
                "freshness": "none",
            }
        now = time.time()
        person_data = self._render_person(canonical, now)
        # Look for a currently-fresh visual.
        if (
            person_data.get("currently_seen")
            and person_data.get("last_visual_room")
        ):
            room = person_data["last_visual_room"]
            conf = person_data.get("last_visual_confidence") or 0.0
            band = _confidence_band(conf)
            if band == "high":
                phrasing = f"I see {canonical} in the {room}."
            elif band == "medium":
                phrasing = (
                    f"I think I see {canonical} in the {room}, "
                    f"but I'm not fully confident."
                )
            else:
                phrasing = (
                    f"I see someone in the {room} who might be {canonical}, "
                    f"but the confidence is low."
                )
            return {
                "data": person_data,
                "suggested_phrasing": phrasing,
                "confidence_band": band,
                "freshness": "fresh",
            }
        # Visual not fresh — fall back to last-seen + HA location.
        # Always lead with the visual answer ("I don't currently see…")
        # because find_person is typically called for "do you see X?" —
        # leading with phone-location reads as a non-answer. Phone state
        # and last-known sighting are secondary context appended after.
        last_visual_age = person_data.get("last_visual_age_seconds")
        last_visual_room = person_data.get("last_visual_room")
        ha_location = person_data.get("ha_location")
        parts = [f"I don't currently see {canonical} on any camera"]
        if ha_location == "home":
            parts.append("their phone is home")
        elif ha_location and ha_location not in (None, "unknown", "unavailable"):
            parts.append(f"their phone shows them as {ha_location}")
        if last_visual_room and last_visual_age is not None:
            parts.append(
                f"I last saw them in the {last_visual_room} "
                f"{_humanize_age(last_visual_age)}"
            )
        phrasing = "; ".join(parts) + "."
        return {
            "data": person_data,
            "suggested_phrasing": phrasing,
            "confidence_band": "unknown",
            "freshness": _freshness(last_visual_age),
        }

    def who_is_in(self, room: str) -> dict[str, Any]:
        """List identified + unknown persons in a room."""
        if not self.enabled:
            return {"error": "world state disabled"}
        room_key = self._resolve_room(room)
        if room_key is None or room_key not in self._rooms:
            return {
                "data": {"room": room, "identified": [], "unknown_count": 0},
                "suggested_phrasing": f"I don't have any data for the {room} right now.",
                "confidence_band": "unknown",
                "freshness": "none",
            }
        now = time.time()
        room_data = self._render_room(room_key, now)
        identified = []
        for p in room_data.get("persons", []):
            ident = p.get("identity") or {}
            if ident.get("name") and (ident.get("confidence") or 0) >= IDENTITY_CONFIDENCE_MEDIUM:
                identified.append({
                    "name": ident["name"],
                    "confidence": ident["confidence"],
                    "confidence_band": ident["confidence_band"],
                    "age_seconds": p.get("age_seconds"),
                })
        unknown_count = 1 if (room_data.get("occupied") and not identified) else 0
        # Phrasing.
        if identified:
            names = [p["name"] for p in identified]
            if len(names) == 1:
                phrasing = f"I see {names[0]} in the {room_key}."
            else:
                phrasing = (
                    f"I see {', '.join(names[:-1])} and {names[-1]} in the {room_key}."
                )
            top_band = identified[0]["confidence_band"]
        elif unknown_count:
            phrasing = f"I see someone in the {room_key}, but I can't confirm who."
            top_band = "unknown"
        else:
            phrasing = f"I don't currently see anyone in the {room_key}."
            top_band = "unknown"
        return {
            "data": {
                "room": room_key,
                "identified": identified,
                "unknown_count": unknown_count,
            },
            "suggested_phrasing": phrasing,
            "confidence_band": top_band,
            "freshness": _freshness(
                identified[0]["age_seconds"] if identified else None
            ),
        }

    # ─── rendering helpers ──────────────────────────────────────────
    def _empty_room(self) -> dict[str, Any]:
        return {
            "occupied": False,
            "frigate_labels": [],
            "cameras": [],
            "persons": [],
            "perception_summary": None,
            "perception_at_ts": None,
        }

    def _empty_person(self) -> dict[str, Any]:
        return {
            "ha_location": None,
            "ha_location_entity_id": None,
            "last_visual_room": None,
            "last_visual_camera": None,
            "last_visual_at_ts": None,
            "last_visual_confidence": None,
        }

    def _render_room(self, room: str, now: float) -> dict[str, Any]:
        r = self._rooms.get(room, self._empty_room())
        rendered_persons = []
        historical_persons = []
        for p in r.get("persons", []):
            ts = p.get("last_seen_at_ts")
            age = (now - ts) if ts else None
            entry = {
                **{k: v for k, v in p.items() if k != "last_seen_at_ts"},
                "age_seconds": age,
                "stale": (age is not None and age > STALE_SECONDS),
            }
            # ROOT-CAUSE FIX (post-Addendum 24): `_apply_detection` appends
            # to room["persons"] forever and never prunes. A detection from
            # 2 days ago still appears in the list — `get_all_rooms_state`
            # then reports "David in living room" when he hasn't been seen
            # in days. Filter at READ time so the LLM tools (get_room_state,
            # who_is_in, get_all_rooms_state) only see persons who are
            # ACTUALLY recent. Threshold = RECENT_SECONDS (3 min) — covers
            # "stepped briefly out of frame" but excludes ancient entries.
            # The raw list is preserved in historical_persons for any
            # consumer that wants the full history (e.g., a future "last
            # seen" timeline view); current tools ignore it.
            if age is None or age <= RECENT_SECONDS:
                rendered_persons.append(entry)
            else:
                historical_persons.append(entry)
        perception_age = (
            now - r["perception_at_ts"] if r.get("perception_at_ts") else None
        )
        return {
            "occupied": bool(r.get("occupied")),
            "frigate_labels": list(r.get("frigate_labels") or []),
            "cameras": list(r.get("cameras") or []),
            "persons": rendered_persons,
            "historical_persons": historical_persons,
            "perception_summary": r.get("perception_summary"),
            "perception_age_seconds": perception_age,
        }

    def _render_person(self, name: str, now: float) -> dict[str, Any]:
        p = self._people.get(name, self._empty_person())
        last_visual_age = (
            now - p["last_visual_at_ts"] if p.get("last_visual_at_ts") else None
        )
        currently_seen = (
            last_visual_age is not None and last_visual_age <= FRESH_SECONDS
        )
        out = {
            "name": name,
            "ha_location": p.get("ha_location"),
            "ha_location_entity_id": p.get("ha_location_entity_id"),
            "last_visual_room": p.get("last_visual_room"),
            "last_visual_camera": p.get("last_visual_camera"),
            "last_visual_at_ts": p.get("last_visual_at_ts"),
            "last_visual_age_seconds": last_visual_age,
            "last_visual_confidence": p.get("last_visual_confidence"),
            "currently_seen": currently_seen,
        }
        # Addendum 14 / Slice 8: enrich with identity_store metadata so
        # the LLM tools see relationship + privacy context automatically.
        # Done at the projection layer so existing prompts + tools get
        # richer payloads without any prompt-template changes.
        ctx = self._identity_context_for(name)
        if ctx:
            out["identity_context"] = ctx
            # Substitute display_name into the canonical 'name' field if
            # we have one — the LLM speaks via this string. Frigate may
            # call the person 'sarah' (lowercased); we know it's 'Sarah'.
            if ctx.get("display_name") and ctx["display_name"] != name:
                out["display_name"] = ctx["display_name"]
        return out

    def _identity_context_for(self, frigate_name: str) -> Optional[dict[str, Any]]:
        """Look up identity_store context for this Frigate-side person name.
        Returns None if the store isn't wired or no match. Cheap — the
        store does its own caching + the lookup is keyed on an indexed
        column."""
        store = self._identity_store
        if store is None:
            return None
        try:
            row = store.resolve_frigate_name(frigate_name)
        except Exception:
            return None
        if row is None:
            return None
        return {
            "display_name": row.display_name,
            "relationship_type": row.relationship_type,
            "relationship_subrole": row.relationship_subrole,
            "pronouns": row.pronouns,
            "notes": row.notes,
            "is_private": row.is_private,
            "is_silent": row.is_silent,
            "is_archived": row.is_archived,
            "uuid": row.uuid,
        }

    def _phrase_person_in_room(
        self, person_entry: dict[str, Any], room: str
    ) -> str:
        ident = person_entry.get("identity") or {}
        name = ident.get("name") or PRIMARY_USER_NAME
        band = ident.get("confidence_band", "unknown")
        age = person_entry.get("age_seconds") or 0
        if band == "high" and age <= FRESH_SECONDS:
            return f"I see {name} in the {room}."
        if band == "medium" or (band == "high" and age <= RECENT_SECONDS):
            return f"I think I see {name} in the {room}, but I'm not fully confident."
        if age and age > STALE_SECONDS:
            return (
                f"I last saw {name} in the {room} {_humanize_age(age)}; "
                f"that data may be stale."
            )
        return f"I see someone in the {room}, but I can't confirm who."

    # ─── name + room resolution ─────────────────────────────────────
    def _resolve_room(self, room: str) -> Optional[str]:
        """Match a user-supplied room name to a known room key."""
        if not room:
            return None
        normalized = room.strip().lower().replace(" ", "_")
        if normalized in self._rooms:
            return normalized
        # Try fuzzy: strip underscores both ways.
        flat = normalized.replace("_", "")
        for key in self._rooms:
            if key.replace("_", "") == flat:
                return key
        return None

    def _resolve_person(self, name: str) -> Optional[str]:
        """Match a user-supplied person name (case-insensitive).

        Resolution order: exact match → ASR alias → pronoun fallback.
        The exact-match-first rule means if someone literally named
        "Marcello" is tracked, the alias to "Marcelo" never overrides.
        """
        if not name:
            return None
        target = name.strip().lower()
        for key in list(self._people.keys()) + list(self._history.keys()):
            if key.lower() == target:
                return key
        # ASR-alias fallback (Phase 4A.6 defense-in-depth) — handles
        # typed input bypassing the bridge OR ASR variants the bridge
        # rule doesn't cover. Returns canonical name; downstream
        # _render_person() handles the case where it isn't tracked
        # yet (returns empty-shape person).
        if target in PERSON_NAME_ALIASES:
            return PERSON_NAME_ALIASES[target]
        # "me" / "I" → assume primary user.
        if target in {"me", "i", "myself"}:
            return PRIMARY_USER_NAME
        return None

    def _frigate_online(self) -> bool:
        """Best-effort: any camera entity in a state other than 'unavailable'."""
        for state in self.hass.states.async_all():
            m = _CAMERA_RE.match(state.entity_id)
            if m and (state.state or "").lower() not in {"unavailable", "unknown"}:
                return True
        return False

    # ─── perception ingestion (Phase 4A: kept simple) ───────────────
    def record_perception(
        self, room: str, summary: str, ts: Optional[float] = None
    ) -> None:
        """Stash a perception caption for a room. Called by the future bridge
        integration (Phase 4C) or by refresh_perception. Safe no-op if room
        unknown."""
        if not self.enabled or not room or not summary:
            return
        room_key = self._resolve_room(room) or room
        r = self._rooms[room_key]
        r["perception_summary"] = summary
        r["perception_at_ts"] = ts or time.time()
        self._maybe_fire_update_event()

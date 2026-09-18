"""Living-Lights function tools (Addendum 33 Phase 4.A).

Voice/agent-facing tools for the presence-override layer:

  - set_presence_override(zone, deltas, source_text)
      Apply an explicit lighting override. `zone` may be a single zone
      slug, a whole room ("living room", "kitchen"), or "all"/"my
      lights" - the tool fans the override out across every actuating
      zone in scope. Absolute spoken commands ("set my lights to 100%")
      route here so they PERSIST, rather than to a bare light.turn_on
      that the ambient engine immediately reverts. Writes a short JSON
      payload to input_text.living_lights_override_text_<zone> for each
      target; the classifier transitions to `presence_override` (top of
      the state machine, immune to the asleep cap/bypass) and the
      per-zone pilot applies the payload brightness. The
      override-lifecycle automation eases it back to automatic only
      after a minimum hold AND the zone has been genuinely vacant for
      the grace window.

Two payloads (the 255 fix): Home Assistant caps every input_text at
255 characters and refuses a longer set_value, so the override text
silently never landed once the payload grew a nested baseline (about
392 characters). The helper now receives only the six keys its readers
use (HELPER_PAYLOAD_KEYS: command_id, brightness_pct, hold_until,
vacancy_grace_s, pinned, source - about 160 characters), guarded by
MAX_INPUT_TEXT before the write. The long payload (color temperature,
prompt, started_at, min_hold_min, remote, baseline) goes to the command
ledger JSONL and to the tool result. `build_ledger_payload`,
`helper_payload`, `encode_helper_payload` and `ledger_event` are pure
functions so tests can import them without Home Assistant.

  - clear_presence_override(zone | "all")
      Clear an active override immediately. Voice "end the override" /
      "reset the lights" routes here.

All actions are gated by:
  - input_boolean.living_lights_enabled = on (master)
  - per-zone input_boolean.living_lights_zone_<slug>_enabled = on

Per Addendum 33 AR33-7: brightness=0 from a voice override is clamped
to 5% (lights-off must come from explicit `light.turn_off` calls, not
overrides). source in {voice, app}; "auto" / null is rejected.

Persistence (user-chosen "leave + grace, with a minimum hold"): the
payload carries `hold_until` (now + MIN_HOLD_MINUTES) and
`vacancy_grace_s`. The override-lifecycle automation
(living_lights_override_lifecycle.yaml) clears an override only once
BOTH (a) now > hold_until and (b) the zone has read continuously
vacant for vacancy_grace_s. So an explicit command sticks for at least
MIN_HOLD_MINUTES even if the cameras briefly lose a still occupant,
then returns to automatic shortly after you actually leave. `remote`
(zone vacant at set time) is retained for audit only.

Per Addendum 33 AR33-3: `pinned: true` keeps the override active until
explicitly cleared (the lifecycle automation skips pinned overrides).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Home Assistant, voluptuous and the component base class are always
# present in the running integration. The fallbacks exist only so the
# pure payload builders below can be imported by tests that run without
# Home Assistant (tests/living_lights/test_override_payload.py).
try:
    import voluptuous as vol
except ImportError:  # pragma: no cover - test import outside Home Assistant
    vol = None  # type: ignore[assignment]

try:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import llm
except ImportError:  # pragma: no cover - test import outside Home Assistant
    HomeAssistant = Any  # type: ignore[misc,assignment]
    llm = None  # type: ignore[assignment]

try:
    from .base import Function
except ImportError:  # pragma: no cover - test import outside Home Assistant
    class Function:  # type: ignore[no-redef]
        """Stand-in for the component base class outside Home Assistant."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

_LOGGER = logging.getLogger(__name__)

# Zone slug -> set of known synonyms (used to resolve "dining room",
# "kitchen island left", etc to canonical slug). Covers every
# occupancy-managed light zone (the 10 LIGHT_TARGETS the pilots actuate).
ZONE_SYNONYMS: dict[str, list[str]] = {
    "dining_left": ["dining left", "left dining", "dining_left"],
    "dining_right": ["dining right", "right dining", "dining_right"],
    "sink": ["sink", "kitchen sink"],
    "island_left": ["island left", "left island", "kitchen island left"],
    "island_right": ["island right", "right island", "kitchen island right"],
    "sofa": ["sofa", "couch", "living room sofa", "lounge"],
    "front_left": ["front left", "front left light", "front left lamp"],
    "front_door": ["front door", "entry", "entryway", "entrance"],
    "weights": ["weights", "gym", "workout", "weight bench", "home gym"],
    "office": ["office", "desk", "study", "office light"],
}

# Zone slug -> owning camera (room). Every actuating zone is covered.
ZONE_TO_CAMERA: dict[str, str] = {
    "dining_left": "dining_room",
    "dining_right": "dining_room",
    "sink": "kitchen",
    "island_left": "kitchen",
    "island_right": "kitchen",
    "sofa": "living_room",
    "front_left": "living_room",
    "front_door": "living_room",
    "weights": "living_room",
    "office": "living_room",
}

# Room -> the minimal set of zones whose lights together cover the whole
# room (no overlap). "the living room lights" -> sofa (front_left/front_right/
# rear_left/rear_right) + office; redundant subset-zones are intentionally
# excluded so two pilots don't fight over a shared light.
ROOM_TO_ZONES: dict[str, list[str]] = {
    "living_room": ["sofa", "office"],
    "dining_room": ["dining_left", "dining_right"],
    "kitchen": ["sink", "island_left", "island_right"],
}

ROOM_SYNONYMS: dict[str, list[str]] = {
    "living_room": ["living room", "living_room", "lounge", "front room", "den"],
    "dining_room": ["dining room", "dining_room", "dining", "dining area"],
    "kitchen": ["kitchen"],
}

# "all" / "the house" / "my lights" -> every managed zone, deduped to the
# non-overlapping cover (so a house-wide command writes one override per
# physical light, not several fighting writes).
ALL_TARGET_ZONES: list[str] = (
    ROOM_TO_ZONES["living_room"]
    + ROOM_TO_ZONES["dining_room"]
    + ROOM_TO_ZONES["kitchen"]
)
ALL_PHRASES = {
    "all", "everything", "all lights", "all the lights", "the lights",
    "my lights", "the house", "whole house", "everywhere", "house",
}

# Minimum time an explicit override is honored before the lifecycle
# automation is allowed to ease it back to automatic - even if the
# cameras briefly lose the (still) occupant. Matches the user-chosen
# "leave + grace, but hold at least this long" persistence model.
MIN_HOLD_MINUTES = 40
# Seconds a zone must read continuously vacant (after min-hold elapses)
# before the override eases back to automatic.
VACANCY_GRACE_S = 300

# Valid override sources. AR33-7: reject "auto"/null to prevent
# agent-side hallucination from locking lights.
VALID_SOURCES = {"voice", "app", "hardware"}

# Brightness floor for overrides. AR33-7: zero is reserved for
# explicit light.turn_off; overrides never zero out lights.
MIN_OVERRIDE_BRIGHTNESS_PCT = 5
MAX_OVERRIDE_BRIGHTNESS_PCT = 100
# Color-temp clamps roughly the Hue/most-bulbs range.
MIN_OVERRIDE_KELVIN = 2000
MAX_OVERRIDE_KELVIN = 6500
LIGHTING_ACTIVITY_PATH = Path(os.environ.get(
    "LIVING_LIGHTS_ACTIVITY_JSONL",
    "/config/lighting_activity.jsonl",
))

# Home Assistant's ceiling for any input_text value. A longer set_value is
# refused by the input_text integration, so an oversize override never
# lands - the guard in encode_helper_payload raises before the write.
MAX_INPUT_TEXT = 255
# The only keys the override text's readers use: the pilot override
# branch (brightness_pct), the lifecycle automation (pinned, hold_until,
# vacancy_grace_s) and the sim's command_id probe. Everything else lives
# in the ledger row. Same key set as homeai_good_morning.yaml writes.
HELPER_PAYLOAD_KEYS: tuple[str, ...] = (
    "command_id", "brightness_pct", "hold_until", "vacancy_grace_s",
    "pinned", "source",
)


class OverridePayloadTooLong(ValueError):
    """The encoded helper payload would exceed the input_text ceiling."""


def build_ledger_payload(
    *,
    baseline: dict[str, Any],
    args: dict[str, Any],
    source: str,
    now: datetime,
    command_id: str,
    remote: bool,
) -> dict[str, Any]:
    """The long override payload: every field the command ledger, the
    tool result and later analysis want, resolved from absolute or delta
    inputs against the zone's current prediction (`baseline`). Pure: no
    Home Assistant access."""
    bri_abs = args.get("brightness_pct")
    bri_delta = args.get("brightness_delta_pct")
    ct_abs = args.get("color_temp_kelvin")
    ct_delta = args.get("color_temp_delta_kelvin")

    if bri_abs is not None:
        brightness_pct = _clamp_brightness(bri_abs)
    elif bri_delta is not None:
        try:
            brightness_pct = _clamp_brightness(baseline["brightness_pct"] + int(bri_delta))
        except (TypeError, ValueError):
            brightness_pct = baseline["brightness_pct"]
    else:
        brightness_pct = baseline["brightness_pct"]

    if ct_abs is not None:
        color_temp_kelvin = _clamp_kelvin(ct_abs)
    elif ct_delta is not None:
        try:
            color_temp_kelvin = _clamp_kelvin(baseline["color_temp_kelvin"] + int(ct_delta))
        except (TypeError, ValueError):
            color_temp_kelvin = baseline["color_temp_kelvin"]
    else:
        color_temp_kelvin = baseline["color_temp_kelvin"]

    hold_until = now + timedelta(minutes=MIN_HOLD_MINUTES)
    return {
        "command_id": command_id,
        "brightness_pct": brightness_pct,
        "color_temp_kelvin": color_temp_kelvin,
        "started_at": now.isoformat(),
        "hold_until": hold_until.isoformat(),
        "min_hold_min": MIN_HOLD_MINUTES,
        "vacancy_grace_s": VACANCY_GRACE_S,
        "source": source,
        "prompt": (args.get("source_text") or "")[:160],
        "remote": remote,
        "pinned": bool(args.get("pinned", False)),
        "baseline": baseline,
    }


def helper_payload(ledger_payload: dict[str, Any]) -> dict[str, Any]:
    """The short payload written to input_text.living_lights_override_text_*:
    the HELPER_PAYLOAD_KEYS subset of the long payload, in that order."""
    return {key: ledger_payload[key] for key in HELPER_PAYLOAD_KEYS}


def encode_helper_payload(short: dict[str, Any]) -> str:
    """Serialise the short payload for the input_text write (compact JSON,
    no spaces) and refuse anything over MAX_INPUT_TEXT before it is sent -
    Home Assistant would reject the write and the override would silently
    never land."""
    text = json.dumps(short, separators=(",", ":"))
    if len(text) > MAX_INPUT_TEXT:
        raise OverridePayloadTooLong(
            f"override helper payload is {len(text)} chars, over the "
            f"{MAX_INPUT_TEXT}-char input_text ceiling: {text[:80]}..."
        )
    return text


def ledger_event(zone: str, entity_id: str, ledger_payload: dict[str, Any],
                 short: dict[str, Any]) -> dict[str, Any]:
    """The command-ledger JSONL row for one zone write: the long payload
    plus the exact short payload that reached the helper."""
    return {
        "schema_version": 1,
        "kind": "lighting_command_event",
        "raw_event_kind": "lighting_command_event",
        "event_id": ledger_payload["command_id"],
        "command_id": ledger_payload["command_id"],
        "ts": ledger_payload["started_at"],
        "zone": zone,
        "entity_id": entity_id,
        "source": ledger_payload["source"],
        "source_text": ledger_payload.get("prompt", ""),
        "requested": {
            "brightness_pct": ledger_payload["brightness_pct"],
            "color_temp_kelvin": ledger_payload["color_temp_kelvin"],
            "pinned": ledger_payload["pinned"],
            "remote": ledger_payload["remote"],
        },
        "baseline": ledger_payload.get("baseline"),
        "payload": ledger_payload,
        "helper_payload": short,
    }


def _resolve_zone(raw: str) -> str | None:
    """Map a free-text zone name to a canonical slug, or None if unknown."""
    if not raw:
        return None
    needle = raw.strip().lower().replace("_", " ")
    for slug, synonyms in ZONE_SYNONYMS.items():
        for syn in synonyms:
            if needle == syn.lower():
                return slug
    # Substring fallback: longest synonym wins
    best: tuple[int, str] | None = None
    for slug, synonyms in ZONE_SYNONYMS.items():
        for syn in synonyms:
            sl = syn.lower()
            if sl in needle or needle in sl:
                if best is None or len(sl) > best[0]:
                    best = (len(sl), slug)
    return best[1] if best else None


def _master_enabled(hass: HomeAssistant) -> bool:
    s = hass.states.get("input_boolean.living_lights_enabled")
    return s is not None and s.state == "on"


def _zone_enabled(hass: HomeAssistant, zone: str) -> bool:
    s = hass.states.get(f"input_boolean.living_lights_zone_{zone}_enabled")
    return s is not None and s.state == "on"


def _zone_occupied(hass: HomeAssistant, zone: str) -> bool:
    """True if the zone's occupancy sensor reads 'on'."""
    s = hass.states.get(f"binary_sensor.{zone}_person_occupancy")
    return s is not None and s.state == "on"


def _clamp_brightness(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return MIN_OVERRIDE_BRIGHTNESS_PCT
    return max(MIN_OVERRIDE_BRIGHTNESS_PCT, min(MAX_OVERRIDE_BRIGHTNESS_PCT, n))


def _clamp_kelvin(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 2700
    return max(MIN_OVERRIDE_KELVIN, min(MAX_OVERRIDE_KELVIN, n))


def _current_zone_state(hass: HomeAssistant, zone: str) -> dict[str, Any]:
    """Read current predicted lighting state for the zone (for delta-style
    'brighter' / 'warmer' inputs that need a baseline)."""
    s = hass.states.get(f"sensor.{_camera_for_zone(zone)}_{zone}_lighting_state")
    if s is None:
        return {"brightness_pct": 50, "color_temp_kelvin": 2700}
    attrs = s.attributes or {}
    return {
        "brightness_pct": int(attrs.get("predicted_brightness_pct", 50) or 50),
        "color_temp_kelvin": int(attrs.get("predicted_color_temp_kelvin", 2700) or 2700),
    }


def _camera_for_zone(zone: str) -> str:
    """Zone -> owning camera (room). Falls back to the slug itself."""
    return ZONE_TO_CAMERA.get(zone, zone)


def _resolve_targets(raw: str) -> list[str] | None:
    """Resolve a free-text target into a list of canonical zone slugs.

    Accepts a single zone ("sink", "kitchen island left"), a whole room
    ("the living room", "kitchen"), or a house-wide phrase ("all",
    "my lights", "the house"). Returns the deduped list of actuating
    zones, or None if nothing resolved.
    """
    if not raw:
        return None
    needle = raw.strip().lower().replace("_", " ")
    if needle in ALL_PHRASES:
        return list(ALL_TARGET_ZONES)
    # Whole-room match (exact synonym) -> that room's cover set.
    for room, synonyms in ROOM_SYNONYMS.items():
        if needle in (s.lower() for s in synonyms):
            return list(ROOM_TO_ZONES[room])
    # Single-zone resolution (reuses the synonym table).
    zone = _resolve_zone(raw)
    if zone is not None:
        return [zone]
    # Last resort: a room name embedded in a longer phrase
    # ("turn the living room lights up") -> that room's cover set.
    for room, synonyms in ROOM_SYNONYMS.items():
        if any(s.lower() in needle for s in synonyms):
            return list(ROOM_TO_ZONES[room])
    return None


def _new_command_id(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return f"cmd-{int(dt.timestamp())}-{dt.microsecond}"


def _append_lighting_command_event(payload: dict[str, Any]) -> None:
    """Best-effort append-only command ledger write.

    This must never block or fail the actual lighting command. HA-side light
    state events remain the source of truth for the resulting physical change;
    this row gives Intelligence a high-confidence command anchor when present.
    """
    try:
        if not LIGHTING_ACTIVITY_PATH.parent.exists():
            return
        with LIGHTING_ACTIVITY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.warning("living_lights: command ledger append failed: %s", exc)


class LivingLightsFunction(Function):
    """Dispatcher for the living-lights tools (set_presence_override,
    clear_presence_override).

    Same `function: {type: 'living_lights', name: '...'}` config pattern
    as world_state.py.
    """

    def __init__(self) -> None:
        super().__init__(vol.Schema({vol.Required("name"): str}))

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        name = function_config["name"]
        if name in {"set_presence_override", "clear_presence_override"}:
            return {
                "success": False,
                "ok": False,
                "capability_disabled": True,
                "error_kind": "model_action_disabled",
                "error_message": (
                    "Model-originated lighting actions are disabled until "
                    "the external safety kernel is available."
                ),
                "tool": name,
            }
        if name == "set_presence_override":
            return await self._set_presence_override(hass, arguments)
        if name == "clear_presence_override":
            return await self._clear_presence_override(hass, arguments)
        if name == "get_recent_overrides":
            return await self._get_recent_overrides(hass, arguments)
        return {
            "ok": False,
            "error": {
                "kind": "unknown_function",
                "message": f"living_lights tool '{name}' not registered",
            },
        }

    async def _get_recent_overrides(
        self, hass: HomeAssistant, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Return manual adjustments observed in the last <hours> hours.
        Used to answer 'why did the office dim earlier?' / 'show me recent
        manual overrides' from the agent.

        Reads from the world-state aggregator's 1-hour rolling buffer (in
        memory only). For longer windows the caller should fall back to
        /config/lighting_preferences_pending.jsonl on disk (V2).

        M20: bursts of trigger fires on the same light within ~10s are
        collapsed into one logical session record (final value wins)
        before being returned. The agent gets a clean "what the user
        did" view. Pass `collapse=False` in the tool args to see the
        raw per-trigger events (e.g. for counting trigger fires).
        """
        try:
            hours = float(args.get("hours") or 1.0)
        except (TypeError, ValueError):
            hours = 1.0
        zone = args.get("zone")
        if zone is not None:
            zone = str(zone).strip().lower() or None
        # collapse defaults True - the agent answering "what did the user
        # do" is always better off with sessions, not raw trigger fires.
        collapse_arg = args.get("collapse")
        if collapse_arg is None:
            collapse = True
        elif isinstance(collapse_arg, bool):
            collapse = collapse_arg
        else:
            collapse = str(collapse_arg).strip().lower() not in (
                "false", "0", "no", "off",
            )

        # Defensive lookup - handles HA startup race + missing integration
        # entry. The DOMAIN constant lives at the integration's package
        # root; importing here keeps the test-imports clean.
        try:
            from ..const import DOMAIN  # type: ignore[no-redef]
        except Exception:  # pylint: disable=broad-except
            DOMAIN = "extended_openai_conversation"
        domain_data = hass.data.get(DOMAIN, {})
        aggregator = domain_data.get("world_state")
        if aggregator is None:
            return {
                "ok": True,
                "data": [],
                "suggested_phrasing": "World state aggregator not ready yet.",
                "freshness": "none",
                "confidence_band": "low",
            }

        try:
            overrides = aggregator.recent_overrides(
                hours=hours, zone=zone, collapse=collapse,
            )
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("get_recent_overrides failed: %s", exc)
            overrides = []

        n = len(overrides)
        if n == 0:
            scope = f" in {zone.replace('_', ' ')}" if zone else ""
            phrasing = f"No manual adjustments{scope} in the last {hours:g} hour(s)."
            freshness = "none"
        else:
            scope = f" in {zone.replace('_', ' ')}" if zone else ""
            # M20: report sessions, not raw events. Mention burst count
            # only when meaningful (any session folded > 1 event).
            collapsed_sessions = sum(
                1 for r in overrides if r.get("session_collapsed")
            )
            unit = (
                "manual adjustment" if collapse else "override event"
            )
            phrasing = (
                f"There {'was' if n == 1 else 'were'} {n} {unit}"
                f"{'' if n == 1 else 's'}{scope} in the last {hours:g} hour"
                f"{'' if hours == 1 else 's'}."
            )
            if collapse and collapsed_sessions:
                phrasing += (
                    f" {collapsed_sessions} of those were burst sessions "
                    "(multiple trigger fires within ~10s folded into one)."
                )
            freshness = "fresh"

        return {
            "ok": True,
            "data": overrides,
            "count": n,
            "hours": hours,
            "zone": zone,
            "collapse": collapse,
            "suggested_phrasing": phrasing,
            "confidence_band": "high",
            "freshness": freshness,
        }

    def _build_override_payload(
        self,
        hass: HomeAssistant,
        zone: str,
        args: dict[str, Any],
        source: str,
        now: datetime,
        command_id: str,
    ) -> dict[str, Any]:
        """Read the zone's current prediction and occupancy from Home
        Assistant and build the long (ledger) payload through
        `build_ledger_payload`; `_write_zone_override` derives the short
        helper payload from it."""
        baseline = _current_zone_state(hass, zone)
        # `remote` (zone vacant at command time) is audit-only - the
        # lifecycle automation owns expiry via hold_until + vacancy grace,
        # so a remote set persists the same min-hold as an in-room one.
        remote = not _zone_occupied(hass, zone)
        return build_ledger_payload(
            baseline=baseline, args=args, source=source, now=now,
            command_id=command_id, remote=remote,
        )

    async def _write_zone_override(
        self, hass: HomeAssistant, zone: str, payload: dict[str, Any],
    ) -> None:
        """Write the short helper payload + command-id helper for one zone
        and append the long command-ledger row. Raises before any write if
        the short payload would exceed MAX_INPUT_TEXT, and if the override
        write itself fails (the caller records the zone as skipped)."""
        entity_id = f"input_text.living_lights_override_text_{zone}"
        short = helper_payload(payload)
        text = encode_helper_payload(short)
        await hass.services.async_call(
            "input_text", "set_value",
            {"entity_id": entity_id, "value": text},
            blocking=True,
        )
        command_helper = f"input_text.living_lights_zone_{zone}_last_command_id"
        try:
            await hass.services.async_call(
                "input_text", "set_value",
                {"entity_id": command_helper, "value": payload["command_id"]},
                blocking=True,
            )
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning(
                "living_lights: command id helper write failed for %s: %s",
                zone, exc,
            )
        _append_lighting_command_event(ledger_event(zone, entity_id, payload, short))

    async def _set_presence_override(
        self, hass: HomeAssistant, args: dict[str, Any]
    ) -> dict[str, Any]:
        # 1. Validate source (AR33-7)
        source = (args.get("source") or "").strip().lower()
        if source not in VALID_SOURCES:
            return {
                "ok": False,
                "error": {
                    "kind": "invalid_source",
                    "message": f"source must be one of {sorted(VALID_SOURCES)}; "
                               f"got '{source}'",
                },
            }

        # 2. Resolve target(s): a single zone, a whole room, or "all".
        zone_raw = args.get("zone", "")
        targets = _resolve_targets(zone_raw)
        if not targets:
            return {
                "ok": False,
                "error": {
                    "kind": "unknown_zone",
                    "message": f"target '{zone_raw}' not recognized",
                    "known_zones": sorted(ZONE_SYNONYMS.keys()),
                    "known_rooms": sorted(ROOM_TO_ZONES.keys()),
                },
            }

        # 3. Master toggle (per-zone toggles are checked per target below).
        if not _master_enabled(hass):
            return {
                "ok": False,
                "error": {
                    "kind": "master_disabled",
                    "message": "input_boolean.living_lights_enabled is OFF; "
                               "override will not take effect",
                },
                "suggested_phrasing": "The living-lights system is off; nothing to override.",
            }

        # 4. Apply the override to each enabled target zone. A single
        #    spoken "set my lights to 100%" fans out across the room.
        now = datetime.now(timezone.utc)
        command_id = _new_command_id(now)
        applied: list[dict[str, Any]] = []
        skipped: list[str] = []
        for zone in targets:
            if not _zone_enabled(hass, zone):
                skipped.append(zone)
                continue
            payload = self._build_override_payload(
                hass, zone, args, source, now, command_id,
            )
            try:
                await self._write_zone_override(hass, zone, payload)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "living_lights: override write failed for %s", zone,
                )
                skipped.append(zone)
                continue
            applied.append({"zone": zone, "payload": payload})

        if not applied:
            return {
                "ok": False,
                "error": {
                    "kind": "no_zones_applied",
                    "message": "every targeted zone is disabled or failed to write",
                    "skipped": skipped,
                },
                "suggested_phrasing": (
                    "Couldn't set those lights - that area is turned off in "
                    "Living Lights right now."
                ),
            }

        # 5. Suggested phrasing - singular vs fan-out.
        first = applied[0]["payload"]
        bri = first["brightness_pct"]
        ct = first["color_temp_kelvin"]
        if len(applied) == 1:
            zlabel = applied[0]["zone"].replace("_", " ")
        else:
            zlabel = f"{len(applied)} zones"
        if first["pinned"]:
            phrasing = (f"OK - {zlabel} pinned to {bri}% at {ct}K. "
                        f"Stays put until you clear it.")
        else:
            phrasing = (f"OK - {zlabel} to {bri}% at {ct}K. "
                        f"Holds at least {MIN_HOLD_MINUTES} min, then eases back "
                        f"to automatic once you've left.")

        return {
            "ok": True,
            "data": {
                "zones": [a["zone"] for a in applied],
                "skipped": skipped,
                "command_id": command_id,
                "payload": first,
            },
            "suggested_phrasing": phrasing,
            "confidence_band": "high",
            "freshness": "fresh",
        }

    async def _clear_presence_override(
        self, hass: HomeAssistant, args: dict[str, Any]
    ) -> dict[str, Any]:
        scope = (args.get("zone") or "all").strip().lower()
        cleared: list[str] = []
        if scope == "all":
            zones = list(ZONE_SYNONYMS.keys())
        else:
            z = _resolve_zone(scope)
            if z is None:
                return {
                    "ok": False,
                    "error": {
                        "kind": "unknown_zone",
                        "message": f"zone '{scope}' not recognized",
                    },
                }
            zones = [z]
        for zone in zones:
            entity_id = f"input_text.living_lights_override_text_{zone}"
            current = hass.states.get(entity_id)
            if current is None or current.state in ("", "unknown", "unavailable", None):
                continue
            try:
                await hass.services.async_call(
                    "input_text", "set_value",
                    {"entity_id": entity_id, "value": ""},
                    blocking=True,
                )
                cleared.append(zone)
            except Exception as e:
                _LOGGER.warning("living_lights: clear failed for %s: %s", zone, e)
        if not cleared:
            return {
                "ok": True,
                "data": {"cleared": []},
                "suggested_phrasing": "No active overrides to clear.",
                "confidence_band": "high",
                "freshness": "fresh",
            }
        return {
            "ok": True,
            "data": {"cleared": cleared},
            "suggested_phrasing": (
                f"Cleared {len(cleared)} override{'s' if len(cleared) > 1 else ''} "
                f"({', '.join(z.replace('_', ' ') for z in cleared)}). "
                f"Automation back in charge."
            ),
            "confidence_band": "high",
            "freshness": "fresh",
        }

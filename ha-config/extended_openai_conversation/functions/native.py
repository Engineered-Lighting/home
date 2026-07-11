"""Native tool for Home Assistant operations."""

from __future__ import annotations

from datetime import timedelta
import logging
import os
import time
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.components import automation, energy, recorder
from homeassistant.components.recorder import history as recorder_history
from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers import area_registry as ar, entity_registry as er, llm
import homeassistant.util.dt as dt_util

from ..const import EVENT_AUTOMATION_REGISTERED, HIGH_IMPACT_DOMAINS, STAKES_BY_DOMAIN
from ..exceptions import CallServiceError, NativeNotFound
from ..room_binding import resolve
from .base import Function

_LOGGER = logging.getLogger(__name__)

_MODEL_ACTION_FUNCTIONS = frozenset(
    {"execute_service", "execute_service_single", "add_automation", "invoke_script"}
)


def _model_action_disabled(
    tool: str, domain: str | None = None, service: str | None = None
) -> dict[str, Any]:
    """Return the fail-closed result for every model-originated mutation.

    This check lives in the dispatcher as a second boundary after tool-surface
    filtering.  No argument—including the former ``confirmed=true`` flag—can
    turn it into an allow decision.
    """
    result: dict[str, Any] = {
        "success": False,
        "ok": False,
        "capability_disabled": True,
        "error_kind": "model_action_disabled",
        "error_message": (
            "Model-originated Home Assistant actions are disabled until the "
            "external safety kernel is available."
        ),
        "tool": tool,
    }
    if domain:
        result["domain"] = domain
    if service:
        result["service"] = service
    return result


_LL_MANAGED_AREA_TO_ZONE = {
    "kitchen": "kitchen",
    "living_room": "living room",
    "livingroom": "living room",
    "dining_room": "dining room",
    "diningroom": "dining room",
}

_LL_ENTITY_TO_ZONE = {
    "light.dining_table_left": "dining_left",
    "light.dining_table_right": "dining_right",
    "light.sink": "sink",
    "light.island_left": "island_left",
    "light.island_right": "island_right",
    "light.office": "office",
    "light.front_left": "front_left",
    "light.front_right": "front_door",
}

_LL_ENTITY_TO_AREA = {
    "light.kitchen": "kitchen",
    "light.kitchen_lights": "kitchen",
    "light.dining_room": "dining room",
    "light.dining_room_lights": "dining room",
    "light.living_room_lights": "living room",
}

_LL_ZONE_TO_AREA = {
    "dining_left": "dining room",
    "dining_right": "dining room",
    "sink": "kitchen",
    "island_left": "kitchen",
    "island_right": "kitchen",
    "office": "living room",
    "front_left": "living room",
    "front_door": "living room",
}

_LL_LEVEL_KEYS = ("brightness_pct", "brightness", "color_temp_kelvin", "color_temp")


def _listify_target(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _target_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _mired_to_kelvin(value: Any) -> int | None:
    try:
        mired = float(value)
    except (TypeError, ValueError):
        return None
    if mired <= 0:
        return None
    return int(round(1000000 / mired))


def _brightness_to_pct(value: Any) -> int | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, int(round(raw / 255 * 100))))


def _living_lights_override_args(
    domain: str,
    service: str,
    service_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Convert mistaken managed-room light.turn_on calls to a durable override.

    The prompt tells the model to call set_presence_override for Living Lights
    level/kelvin changes, but small local models sometimes emit execute_services
    anyway. This guard is intentionally narrow: turn_off, non-managed lights,
    and color modes the override tool cannot represent still pass through.
    """
    if domain != "light" or service != "turn_on":
        return None
    if not any(key in service_data for key in _LL_LEVEL_KEYS):
        return None

    zone: str | None = None
    area_ids = _listify_target(service_data.get("area_id"))
    area_zones = {
        _LL_MANAGED_AREA_TO_ZONE[_target_key(area)]
        for area in area_ids
        if _target_key(area) in _LL_MANAGED_AREA_TO_ZONE
    }
    if len(area_zones) == 1:
        zone = next(iter(area_zones))
    elif len(area_zones) > 1:
        return None

    if zone is None:
        entity_ids = [entity.lower() for entity in _listify_target(service_data.get("entity_id"))]
        area_targets = {_LL_ENTITY_TO_AREA[entity] for entity in entity_ids if entity in _LL_ENTITY_TO_AREA}
        zone_targets = {_LL_ENTITY_TO_ZONE[entity] for entity in entity_ids if entity in _LL_ENTITY_TO_ZONE}
        if area_targets:
            if len(area_targets) == 1 and not zone_targets:
                zone = next(iter(area_targets))
            else:
                return None
        elif zone_targets:
            room_targets = {_LL_ZONE_TO_AREA[z] for z in zone_targets}
            if len(zone_targets) == 1:
                zone = next(iter(zone_targets))
            elif len(room_targets) == 1:
                zone = next(iter(room_targets))
            else:
                return None

    if zone is None:
        return None

    out: dict[str, Any] = {
        "zone": zone,
        "source": "voice",
        "source_text": "rerouted execute_services light.turn_on",
    }
    if "brightness_pct" in service_data:
        out["brightness_pct"] = service_data.get("brightness_pct")
    elif "brightness" in service_data:
        pct = _brightness_to_pct(service_data.get("brightness"))
        if pct is not None:
            out["brightness_pct"] = pct
    if "color_temp_kelvin" in service_data:
        out["color_temp_kelvin"] = service_data.get("color_temp_kelvin")
    elif "color_temp" in service_data:
        kelvin = _mired_to_kelvin(service_data.get("color_temp"))
        if kelvin is not None:
            out["color_temp_kelvin"] = kelvin
    return out


def _living_lights_master_not_off(hass: HomeAssistant) -> bool:
    try:
        states = getattr(hass, "states", None)
        state = states.get("input_boolean.living_lights_enabled") if states else None
    except Exception:  # pragma: no cover - defensive around HA startup/tests
        return True
    return state is None or getattr(state, "state", None) == "on"


def _media_entities_in_area(hass: HomeAssistant, area_key: str) -> list[str]:
    """Resolve `area_id` → list of media_player entity_ids in that area.

    Used by execute_service_single's targetless-call backstop when the
    LLM emits a media_player service without entity_id (Addendum 16 F-1).
    Area lookup is name-tolerant: the bound key may be an area_id, an
    area name, or a normalised slug (e.g. 'living_room' vs 'Living Room').

    Returns [] when no media_player entities are found in the resolved
    area — caller treats that as a hard error rather than falling back
    to area_id (which would silently no-op for media_player)."""
    if not area_key:
        return []
    try:
        a_reg = ar.async_get(hass)
        e_reg = er.async_get(hass)
    except Exception as exc:  # pragma: no cover — HA registry not ready
        _LOGGER.debug("media area resolve: registry unavailable: %s", exc)
        return []

    # Resolve area_key to an area_id. Accept either the id OR the
    # human name; HA's area registry has both. Normalize for slug
    # matching too (lowercase + underscored).
    target = (area_key or "").strip()
    target_slug = target.lower().replace(" ", "_")
    resolved_area_id: str | None = None
    # Fast path: exact id match
    try:
        if hasattr(a_reg, "async_get_area") and a_reg.async_get_area(target):
            resolved_area_id = target
    except Exception:
        pass
    # Fallback: iterate areas
    if resolved_area_id is None:
        try:
            for area in a_reg.async_list_areas():
                if (area.id == target
                    or area.name == target
                    or area.name.lower().replace(" ", "_") == target_slug):
                    resolved_area_id = area.id
                    break
        except Exception as exc:
            _LOGGER.debug("media area resolve: listing failed: %s", exc)
            return []
    if resolved_area_id is None:
        _LOGGER.debug("media area resolve: no area matched %r", area_key)
        return []
    # Filter entity_registry to media_player entities in this area.
    out: list[str] = []
    try:
        for ent in er.async_entries_for_area(e_reg, resolved_area_id):
            if ent.entity_id.startswith("media_player."):
                out.append(ent.entity_id)
    except Exception as exc:
        _LOGGER.debug("media area resolve: entity lookup failed: %s", exc)
        return []
    return out


def _stakes_for(domain: str) -> str:
    """F-7 (Addendum 27): classify a service-call domain by stakes.
    HIGH_IMPACT_DOMAINS always win (safety) regardless of any explicit
    mapping. STAKES_BY_DOMAIN supplies low/med for everything else;
    unmapped domains default to 'low' (don't over-emphasize routine
    calls)."""
    if domain in HIGH_IMPACT_DOMAINS:
        return "high"
    return STAKES_BY_DOMAIN.get(domain, "low")


def _ack_hint(domain: str, service: str, service_data: dict, stakes: str) -> str:
    """F-7: build a stakes-matched verbal ack the agent can speak
    verbatim. Format reflects significance:
      low  → terse "OK."
      med  → brief "<service> applied."
      high → explicit + named "Done — <target> <service>ed."
    Falls back gracefully when target info is sparse."""
    # Identify a friendly target name. Prefer entity_id (singular or
    # first of list); fall back to area_id; final fallback "this".
    entity_id = service_data.get("entity_id") if service_data else None
    if isinstance(entity_id, (list, tuple)) and entity_id:
        # Single entity → use it; multiple → "N items"
        if len(entity_id) == 1:
            target = str(entity_id[0]).split(".", 1)[-1].replace("_", " ")
        else:
            target = f"{len(entity_id)} items"
    elif isinstance(entity_id, str):
        target = entity_id.split(".", 1)[-1].replace("_", " ")
    elif service_data and service_data.get("area_id"):
        area = service_data["area_id"]
        if isinstance(area, (list, tuple)):
            target = ", ".join(str(a).replace("_", " ") for a in area)
        else:
            target = str(area).replace("_", " ")
    else:
        target = "this"

    # Normalize the service verb for casual phrasing. HA convention is
    # underscore-snake; humans say "turn off", "play media", etc.
    verb = service.replace("_", " ")

    if stakes == "low":
        return "OK."
    if stakes == "high":
        # Past-tense the verb best-effort. Most HA services are
        # already imperative ("lock", "disarm") — convert to past
        # by appending "ed" if it doesn't already look past. For
        # "turn off" / "alarm disarm" leave them as the action verb
        # since "Done — front door turn off-ed" reads worse than
        # "Done — front door turn off."
        if " " in verb or verb.endswith("e") or verb.endswith("d"):
            return f"Done — {target} {verb}."
        return f"Done — {target} {verb}ed."
    # med
    return f"{verb.capitalize()} on {target}."


class NativeFunction(Function):
    def __init__(self) -> None:
        """Initialize native tool."""
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
        if name in _MODEL_ACTION_FUNCTIONS:
            return _model_action_disabled(name)
        if name == "execute_service":
            return await self.execute_service(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "execute_service_single":
            return await self.execute_service_single(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "add_automation":
            return await self.add_automation(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "get_history":
            return await self.get_history(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "invoke_script":
            return await self.invoke_script(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "get_energy":
            return await self.get_energy(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "get_statistics":
            return await self.get_statistics(
                hass, function_config, arguments, llm_context, exposed_entities
            )
        if name == "get_user_from_user_id":
            return await self.get_user_from_user_id(
                hass, function_config, arguments, llm_context, exposed_entities
            )

        raise NativeNotFound(name)

    async def execute_service_single(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        service_argument: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Defense in depth: this method used to trust model-supplied
        # `confirmed=true`.  It now returns before validation, mutation of the
        # arguments, logging a false confirmation, or any HA service lookup.
        domain = str(service_argument.get("domain") or "")
        service = str(service_argument.get("service") or "")
        return _model_action_disabled("execute_service_single", domain, service)

        # Legacy dispatcher retained temporarily for non-model migration
        # reference. It is unreachable through this method.
        # M0.3: standardized tool-result shape. We RETURN additive `ok` +
        # `latency_ms` fields alongside the legacy `success`/`error` keys
        # so existing LLM prompts that key on "success" still parse, while
        # the routing log + lab tab gain per-call latency without a
        # follow-up turn. Latency is measured around the actual
        # hass.services.async_call dispatch only (registry lookups +
        # validation are excluded — those are constant-cost and not what
        # the user feels). See Addendum 27 Section 13 / M0.3.
        domain = service_argument["domain"]
        service = service_argument["service"]
        service_data = service_argument.get(
            "service_data", service_argument.get("data", {})
        )
        entity_id = service_data.get("entity_id", service_argument.get("entity_id"))
        area_id = service_data.get("area_id")
        device_id = service_data.get("device_id")

        if isinstance(entity_id, str):
            entity_id = [e.strip() for e in entity_id.split(",")]
        service_data["entity_id"] = entity_id

        if entity_id is None and area_id is None and device_id is None:
            # Proactive room-binding backstop: if the LLM emitted a targetless
            # service call AND this conversation's device has a live room
            # binding, default the area to that room rather than failing.
            # Strictly additive -- never overrides an explicit target.
            bound = (
                resolve(getattr(llm_context, "device_id", None))
                if llm_context
                else None
            )
            if bound:
                # Addendum 16 F-1: media_player services (play_media, volume,
                # transport) are NOT addressable by area_id in HA — the
                # service accepts the area but routes to zero entities,
                # leading to silent no-op. (Confirmed regression: "Sonos
                # playback no longer functions reliably.") For media_player,
                # resolve area → media_player entities explicitly via
                # entity_registry and pass entity_id instead.
                if domain == "media_player":
                    resolved_entities = _media_entities_in_area(hass, bound)
                    if resolved_entities:
                        service_data.pop("area_id", None)
                        service_data["entity_id"] = resolved_entities
                        entity_id = resolved_entities  # for downstream validate
                    else:
                        raise CallServiceError(domain, service, service_data)
                else:
                    # Other domains (light, switch, climate, scene, etc.)
                    # support area_id targeting natively — keep prior behavior.
                    service_data.pop("entity_id", None)
                    service_data["area_id"] = bound
            else:
                raise CallServiceError(domain, service, service_data)
        if not hass.services.has_service(domain, service):
            raise ServiceNotFound(domain, service)
        self.validate_entity_ids(hass, entity_id or [], exposed_entities)

        # M6 (Addendum 27 Section 6 / Milestone 6): high-impact safety
        # gate. Domains like lock, alarm, garage_door require an
        # explicit `confirmed: true` flag at the top of the service
        # argument before dispatch. Unconfirmed calls return a
        # structured `requires_confirmation` result; the agent is
        # expected to ask the user, then re-emit with the flag.
        #
        # The `confirmed` flag is stripped before dispatch so it never
        # reaches HA (it's a meta-flag for our dispatcher only).
        #
        # v1 design: trust the flag (no server-side conv_id correlation
        # yet). Audit trail via LOG_KIND_CONFIRMATION lets us SEE every
        # interception + resolution. v2 would add a lookback to ensure
        # `confirmed: true` only fires when a matching
        # `requires_confirmation` was logged on this conv_id within
        # the last 60s.
        # Unreachable legacy reference path. A model-supplied confirmation is
        # intentionally never read; execute_service_single returns the
        # containment result before reaching this code.
        confirmed_flag = False
        is_high_impact = domain in HIGH_IMPACT_DOMAINS
        if is_high_impact and not confirmed_flag:
            # Intercept: log + return the structured result, do NOT dispatch.
            from ..external_routing import LOG_KIND_CONFIRMATION, log_decision
            conv_id = (
                getattr(llm_context, "conversation_id", None)
                if llm_context else None
            )
            try:
                await log_decision(hass, {
                    "ts": time.time(),
                    "kind": LOG_KIND_CONFIRMATION,
                    "conv_id": conv_id,
                    "domain": domain,
                    "service": service,
                    "intercepted": True,
                    "resolved": False,
                })
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("M6 log_decision (intercept) failed: %s", e)
            # Compose suggested phrasing the agent can use verbatim.
            target_str = (
                ", ".join(entity_id) if isinstance(entity_id, (list, tuple)) and entity_id
                else (entity_id if isinstance(entity_id, str) else (area_id or "this"))
            )
            phrasing_verb = service.replace("_", " ")
            return {
                "ok": False,
                "requires_confirmation": True,
                "domain": domain,
                "service": service,
                "action": {
                    "domain": domain,
                    "service": service,
                    "entity_id": entity_id,
                    "area_id": area_id,
                },
                "suggested_phrasing": (
                    f"I'm about to {phrasing_verb} {target_str}. "
                    f"Confirm?"
                ),
                "instructions_for_agent": (
                    "Ask the user to confirm. If they say yes, call "
                    "execute_services again with the SAME args plus "
                    "`confirmed: true` at the TOP of each service item "
                    "(not inside service_data)."
                ),
            }
        if is_high_impact and confirmed_flag:
            # Resolved: log + proceed to dispatch.
            from ..external_routing import LOG_KIND_CONFIRMATION, log_decision
            conv_id = (
                getattr(llm_context, "conversation_id", None)
                if llm_context else None
            )
            try:
                await log_decision(hass, {
                    "ts": time.time(),
                    "kind": LOG_KIND_CONFIRMATION,
                    "conv_id": conv_id,
                    "domain": domain,
                    "service": service,
                    "intercepted": False,
                    "resolved": True,
                })
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("M6 log_decision (resolve) failed: %s", e)

        # ── House-wide ct convergence injector (CT plan) ──────────────
        living_lights_args = (
            _living_lights_override_args(domain, service, service_data)
            if _living_lights_master_not_off(hass)
            else None
        )
        if living_lights_args is not None:
            t_override_start = time.monotonic()
            from .living_lights import LivingLightsFunction
            override_result = await LivingLightsFunction()._set_presence_override(
                hass, living_lights_args,
            )
            latency_ms = int((time.monotonic() - t_override_start) * 1000)
            if override_result.get("ok") is True:
                return {
                    "success": True,
                    "ok": True,
                    "latency_ms": latency_ms,
                    "domain": "living_lights",
                    "service": "set_presence_override",
                    "rerouted_from": {
                        "domain": domain,
                        "service": service,
                        "service_data": service_data,
                    },
                    "data": override_result.get("data"),
                    "suggested_phrasing": override_result.get("suggested_phrasing"),
                    "stakes": "low",
                    "ack_hint": override_result.get("suggested_phrasing", "OK."),
                }
            error_obj = override_result.get("error")
            return {
                "success": False,
                "ok": False,
                "latency_ms": latency_ms,
                "domain": "living_lights",
                "service": "set_presence_override",
                "rerouted_from": {
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                },
                "error": error_obj,
                "error_kind": (
                    error_obj.get("kind")
                    if isinstance(error_obj, dict)
                    else "living_lights_override_failed"
                ),
                "error_message": (
                    error_obj.get("message")
                    if isinstance(error_obj, dict)
                    else str(error_obj or "Living Lights override failed")
                ),
                "suggested_phrasing": override_result.get("suggested_phrasing"),
            }

        # If the LLM emits `light.turn_on` without ANY color-mode parameter
        # (color_temp_kelvin / color_temp / rgb_color / hs_color / xy_color),
        # inject color_temp_kelvin from AL's global target so the bulb's
        # last-stored ct doesn't surface. Explicit color commands ("warmer",
        # "make it blue") pass through untouched. Carve-outs: turn_off is
        # naturally skipped (service != "turn_on"); kelvin-on-bare-on (LLM
        # emits just entity_id) is THE case this catches.
        # See plan: keen-doodling-parasol.md → "House-wide ct convergence".
        if domain == "light" and service == "turn_on":
            color_keys = (
                "color_temp_kelvin", "color_temp",
                "rgb_color", "hs_color", "xy_color", "rgbw_color", "rgbww_color",
            )
            if not any(k in service_data for k in color_keys):
                al_state = hass.states.get("switch.home_adaptive_lighting_home")
                ct_target = None
                if al_state is not None:
                    ct_target = al_state.attributes.get("color_temp_kelvin")
                if ct_target is None:
                    # Fallback: sofa zone classifier (representative; same
                    # tod_color_warm curve as every zone).
                    zs = hass.states.get(
                        "sensor.living_room_sofa_lighting_state")
                    if zs is not None:
                        ct_target = zs.attributes.get(
                            "predicted_color_temp_kelvin")
                if ct_target is None:
                    ct_target = 2700  # final warm-safe fallback
                try:
                    service_data["color_temp_kelvin"] = int(ct_target)
                    _LOGGER.debug(
                        "CT-converge: injected color_temp_kelvin=%s into "
                        "%s.%s (entity_id=%s)",
                        ct_target, domain, service, entity_id)
                except (TypeError, ValueError):
                    # Bad attribute shape — silently skip injection rather
                    # than break the user's command.
                    _LOGGER.warning(
                        "CT-converge: could not coerce ct_target=%r to int; "
                        "skipping injection.", ct_target)

        t_dispatch_start = time.monotonic()
        try:
            await hass.services.async_call(
                domain=domain,
                service=service,
                service_data=service_data,
            )
            latency_ms = int((time.monotonic() - t_dispatch_start) * 1000)
            # Legacy `success` preserved for any LLM prompt / downstream
            # consumer that still keys on it; `ok` + `latency_ms` are the
            # new canonical fields. `domain` + `service` echoed back so
            # downstream tooling can group results by call without parsing
            # the original args.
            # F-7 (Addendum 27): stakes + ack_hint help the agent match
            # verbal weight to the action. Low-stakes dispatches get
            # terse "OK."; high-stakes get explicit named confirmation.
            stakes = _stakes_for(domain)
            return {
                "success": True,
                "ok": True,
                "latency_ms": latency_ms,
                "domain": domain,
                "service": service,
                "stakes": stakes,
                "ack_hint": _ack_hint(domain, service, service_data, stakes),
            }
        except HomeAssistantError as e:
            latency_ms = int((time.monotonic() - t_dispatch_start) * 1000)
            _LOGGER.error(e)
            return {
                "error": str(e),
                "ok": False,
                "latency_ms": latency_ms,
                "domain": domain,
                "service": service,
                "error_kind": type(e).__name__,
                "error_message": str(e),
            }

    async def execute_service(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        for service_argument in arguments.get("list", []):
            result.append(
                await self.execute_service_single(
                    hass,
                    function_config,
                    service_argument,
                    llm_context,
                    exposed_entities,
                )
            )
        return result

    async def add_automation(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        return _model_action_disabled("add_automation")

        # Legacy implementation retained temporarily for migration reference.
        automation_config = yaml.safe_load(arguments["automation_config"])
        config = {"id": str(round(time.time() * 1000))}
        if isinstance(automation_config, list):
            config.update(automation_config[0])
        if isinstance(automation_config, dict):
            config.update(automation_config)

        await automation.config._async_validate_config_item(hass, config, True, False)

        automations = [config]
        with open(
            os.path.join(hass.config.config_dir, AUTOMATION_CONFIG_PATH),
            encoding="utf-8",
        ) as f:
            current_automations = yaml.safe_load(f.read())

        with open(
            os.path.join(hass.config.config_dir, AUTOMATION_CONFIG_PATH),
            "a" if current_automations else "w",
            encoding="utf-8",
        ) as f:
            raw_config = yaml.dump(automations, allow_unicode=True, sort_keys=False)
            f.write("\n" + raw_config)

        await hass.services.async_call(automation.config.DOMAIN, SERVICE_RELOAD)
        hass.bus.async_fire(
            EVENT_AUTOMATION_REGISTERED,
            {"automation_config": config, "raw_config": raw_config},
        )
        return "Success"

    async def get_history(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        start_time = arguments.get("start_time")
        end_time = arguments.get("end_time")
        entity_ids = arguments.get("entity_ids", [])
        include_start_time_state = arguments.get("include_start_time_state", True)
        significant_changes_only = arguments.get("significant_changes_only", True)
        minimal_response = arguments.get("minimal_response", True)
        no_attributes = arguments.get("no_attributes", True)

        now = dt_util.utcnow()
        one_day = timedelta(days=1)
        start_time = self.as_utc(start_time, now - one_day, "start_time not valid")
        end_time = self.as_utc(end_time, start_time + one_day, "end_time not valid")

        self.validate_entity_ids(hass, entity_ids, exposed_entities)

        with recorder.util.session_scope(hass=hass, read_only=True) as session:
            result = await recorder.get_instance(hass).async_add_executor_job(
                recorder_history.get_significant_states_with_session,
                hass,
                session,
                start_time,
                end_time,
                entity_ids,
                None,
                include_start_time_state,
                significant_changes_only,
                minimal_response,
                no_attributes,
            )

        return [[self.as_dict(item) for item in sublist] for sublist in result.values()]

    async def invoke_script(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """F-9 (Addendum 27): run a user-defined HA script by entity_id.

        Thin wrapper over the `script.turn_on` service call — formalizes
        the "run my X" invocation pattern as a first-class tool so the
        agent doesn't have to compose execute_services with the right
        domain/service/entity_id shape every time. Variables are passed
        through as script run variables.

        Result shape mirrors execute_service_single (ok/latency_ms/...).
        """
        return _model_action_disabled("invoke_script", "script", "turn_on")

        # Legacy implementation retained temporarily for migration reference.
        entity_id = (arguments.get("entity_id") or "").strip()
        if not entity_id:
            return {
                "ok": False,
                "error": "entity_id argument required (e.g. 'script.aurora_apply_virtual_knob')",
                "error_kind": "ValueError",
                "error_message": "entity_id argument required",
            }
        if not entity_id.startswith("script."):
            # Tolerate a bare script name (e.g. "aurora_apply_virtual_knob")
            # by prepending the domain. Common LLM shortcut.
            entity_id = f"script.{entity_id}"
        # Validate that the agent is actually allowed to touch this script.
        self.validate_entity_ids(hass, [entity_id], exposed_entities)
        if not hass.services.has_service("script", "turn_on"):
            raise ServiceNotFound("script", "turn_on")
        variables = arguments.get("variables") or {}
        if not isinstance(variables, dict):
            return {
                "ok": False,
                "error": f"variables must be a dict, got {type(variables).__name__}",
                "error_kind": "TypeError",
                "error_message": "variables must be a dict",
            }
        service_data = {"entity_id": entity_id}
        if variables:
            service_data["variables"] = variables
        t0 = time.monotonic()
        try:
            await hass.services.async_call(
                domain="script",
                service="turn_on",
                service_data=service_data,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "domain": "script",
                "service": "turn_on",
                "entity_id": entity_id,
                "stakes": "low",
                # Trust user-curated scripts: terse ack ("OK.") matches
                # F-7 low-stakes pattern. The script's NAME conveys the
                # action; ceremonial verbosity would be redundant.
                "ack_hint": "OK.",
            }
        except HomeAssistantError as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            _LOGGER.error(e)
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "domain": "script",
                "service": "turn_on",
                "entity_id": entity_id,
                "error": str(e),
                "error_kind": type(e).__name__,
                "error_message": str(e),
            }

    async def get_energy(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        energy_manager: energy.data.EnergyManager = await energy.async_get_manager(hass)
        if energy_manager.data is None:
            return {}
        # energy_manager.data is EnergyPreferences which is a TypedDict (already a dict)
        return dict(energy_manager.data)

    async def get_user_from_user_id(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if (
            llm_context is None
            or llm_context.context is None
            or llm_context.context.user_id is None
        ):
            return {"name": "Unknown"}
        user = await hass.auth.async_get_user(llm_context.context.user_id)
        user_name = (
            user.name
            if user and hasattr(user, "name") and user.name is not None
            else "Unknown"
        )
        return {"name": user_name}

    async def get_statistics(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        statistic_ids = arguments.get("statistic_ids", [])
        start_time_parsed = dt_util.parse_datetime(arguments["start_time"])
        end_time_parsed = dt_util.parse_datetime(arguments["end_time"])
        if start_time_parsed is None or end_time_parsed is None:
            raise HomeAssistantError("Invalid datetime format")
        start_time = dt_util.as_utc(start_time_parsed)
        end_time = dt_util.as_utc(end_time_parsed)

        return await recorder.get_instance(hass).async_add_executor_job(
            recorder.statistics.statistics_during_period,
            hass,
            start_time,
            end_time,
            statistic_ids,
            arguments.get("period", "day"),
            arguments.get("units"),
            arguments.get("types", {"change"}),
        )

    def as_utc(
        self, value: str | None, default_value: Any, parse_error_message: str
    ) -> Any:
        if value is None:
            return default_value

        parsed_datetime = dt_util.parse_datetime(value)
        if parsed_datetime is None:
            raise HomeAssistantError(parse_error_message)

        return dt_util.as_utc(parsed_datetime)

    def as_dict(self, state: State | dict[str, Any]) -> dict[str, Any]:
        if isinstance(state, State):
            return state.as_dict()
        return state

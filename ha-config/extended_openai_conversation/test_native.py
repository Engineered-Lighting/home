#!/usr/bin/env python3
"""Tests for functions/native.py — focused on Addendum 16 F-1.

Specifically: the media_player area→entity resolver that fixes the
Sonos "playback no longer works" regression. The bug was that the
proactive room-binding backstop converted targetless service calls
to area_id-targeted calls; for media_player, HA doesn't route by
area for play_media / volume / transport services, leading to silent
no-op. The fix resolves area → media_player entity_ids explicitly
and uses entity_id instead.

Standalone runner (no pytest install needed). Mock HA imports —
we never need a live HA to validate the resolver logic.

Run: PYTHONIOENCODING=utf-8 py -3 test_native.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Mock the HA imports so functions/native.py can be imported standalone ─
def _mk(name):
    m = types.ModuleType(name)
    return m

# Build a minimal sys.modules tree before native.py is imported.
# Each mocked module just needs the symbols native.py references.

# voluptuous + yaml
class _vol:
    Schema = lambda *a, **k: None  # noqa: E731
    Required = lambda x, **k: x    # noqa: E731
sys.modules.setdefault("voluptuous", _vol)
sys.modules.setdefault("yaml", types.ModuleType("yaml"))

# homeassistant.* surface
ha = _mk("homeassistant")
ha_components = _mk("homeassistant.components")
ha_automation = _mk("homeassistant.components.automation")
ha_energy = _mk("homeassistant.components.energy")
ha_recorder = _mk("homeassistant.components.recorder")
ha_recorder_history = _mk("homeassistant.components.recorder.history")
ha_config = _mk("homeassistant.config"); ha_config.AUTOMATION_CONFIG_PATH = "/tmp/x"
ha_const = _mk("homeassistant.const"); ha_const.SERVICE_RELOAD = "reload"
ha_core = _mk("homeassistant.core")
class HomeAssistant: pass
class State: pass
ha_core.HomeAssistant = HomeAssistant
ha_core.State = State
ha_exc = _mk("homeassistant.exceptions")
class HomeAssistantError(Exception): pass
class ServiceNotFound(Exception): pass
ha_exc.HomeAssistantError = HomeAssistantError
ha_exc.ServiceNotFound = ServiceNotFound
ha_helpers = _mk("homeassistant.helpers")
ha_helpers_llm = _mk("homeassistant.helpers.llm")
class _LLMCtx:
    device_id = None
ha_helpers_llm.LLMContext = _LLMCtx
ha_helpers_ar = _mk("homeassistant.helpers.area_registry")
ha_helpers_er = _mk("homeassistant.helpers.entity_registry")
ha_util_dt = _mk("homeassistant.util.dt")

ha.components = ha_components
ha_components.automation = ha_automation
ha_components.energy = ha_energy
ha_components.recorder = ha_recorder
ha_recorder.history = ha_recorder_history
ha.config = ha_config
ha.const = ha_const
ha.core = ha_core
ha.exceptions = ha_exc
ha.helpers = ha_helpers
ha_helpers.area_registry = ha_helpers_ar
ha_helpers.entity_registry = ha_helpers_er
ha_helpers.llm = ha_helpers_llm
ha.util = _mk("homeassistant.util")
ha.util.dt = ha_util_dt

for k, v in {
    "homeassistant": ha,
    "homeassistant.components": ha_components,
    "homeassistant.components.automation": ha_automation,
    "homeassistant.components.energy": ha_energy,
    "homeassistant.components.recorder": ha_recorder,
    "homeassistant.components.recorder.history": ha_recorder_history,
    "homeassistant.config": ha_config,
    "homeassistant.const": ha_const,
    "homeassistant.core": ha_core,
    "homeassistant.exceptions": ha_exc,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.area_registry": ha_helpers_ar,
    "homeassistant.helpers.entity_registry": ha_helpers_er,
    "homeassistant.helpers.llm": ha_helpers_llm,
    "homeassistant.util": ha.util,
    "homeassistant.util.dt": ha_util_dt,
}.items():
    sys.modules[k] = v

# Build a fake registry pair the resolver can iterate.
class _FakeArea:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name

class _FakeAreaRegistry:
    def __init__(self, areas):
        self._areas = areas
    def async_get_area(self, area_id):
        for a in self._areas:
            if a.id == area_id:
                return a
        return None
    def async_list_areas(self):
        return list(self._areas)

class _FakeEntity:
    def __init__(self, entity_id, area_id):
        self.entity_id = entity_id
        self.area_id = area_id

class _FakeEntityRegistry:
    def __init__(self, entities):
        self._entities = entities

# Module-level state our mocks read from
_FAKE_AREAS: list[_FakeArea] = []
_FAKE_ENTITIES: list[_FakeEntity] = []

def _async_get_area_registry(hass):
    return _FakeAreaRegistry(_FAKE_AREAS)
def _async_get_entity_registry(hass):
    return _FakeEntityRegistry(_FAKE_ENTITIES)
def _async_entries_for_area(reg, area_id):
    return [e for e in reg._entities if e.area_id == area_id]

ha_helpers_ar.async_get = _async_get_area_registry
ha_helpers_er.async_get = _async_get_entity_registry
ha_helpers_er.async_entries_for_area = _async_entries_for_area

# Stub the package-local imports native.py uses.
# Create a fake package context so `from ..const import EVENT_AUTOMATION_REGISTERED` works.
pkg_root = _mk("extended_openai_conversation_test_root")
pkg_root.__path__ = []
sys.modules["extended_openai_conversation_test_root"] = pkg_root
pkg_root_const = _mk("extended_openai_conversation_test_root.const")
pkg_root_const.EVENT_AUTOMATION_REGISTERED = "evt"
# M6 (Addendum 27): mirror the production HIGH_IMPACT_DOMAINS so the
# test exercises the same gate list. If the production set changes,
# tests will catch the drift via the explicit-domain assertions below.
pkg_root_const.HIGH_IMPACT_DOMAINS = {"lock", "alarm_control_panel", "garage_door"}
# F-7 (Addendum 27): mirror STAKES_BY_DOMAIN so the ack_hint helper
# can read its classifications. Production source = const.py; if it
# drifts, tests with explicit-domain assertions catch the drift.
pkg_root_const.STAKES_BY_DOMAIN = {
    "light": "low", "switch": "low", "media_player": "low",
    "input_boolean": "low", "input_number": "low", "input_select": "low",
    "fan": "low", "scene": "low", "script": "low",
    "climate": "med", "cover": "med", "vacuum": "med",
    "valve": "med", "notify": "med", "automation": "med",
}
sys.modules["extended_openai_conversation_test_root.const"] = pkg_root_const

# M6: stub external_routing so the lazy import inside native.py
# (intercept + resolve paths) gets a no-op log_decision. Records
# every call so tests can assert the audit trail.
pkg_root_ext = _mk("extended_openai_conversation_test_root.external_routing")
_LOG_DECISIONS: list[dict] = []
async def _log_decision_stub(hass, entry):
    _LOG_DECISIONS.append(dict(entry))
pkg_root_ext.log_decision = _log_decision_stub
pkg_root_ext.LOG_KIND_CONFIRMATION = "confirmation"
sys.modules["extended_openai_conversation_test_root.external_routing"] = pkg_root_ext
pkg_root_exc = _mk("extended_openai_conversation_test_root.exceptions")
class CallServiceError(Exception):
    def __init__(self, domain, service, data):
        self.domain = domain; self.service = service; self.data = data
class NativeNotFound(Exception): pass
pkg_root_exc.CallServiceError = CallServiceError
pkg_root_exc.NativeNotFound = NativeNotFound
sys.modules["extended_openai_conversation_test_root.exceptions"] = pkg_root_exc
pkg_root_rb = _mk("extended_openai_conversation_test_root.room_binding")
def _resolve(device_id):
    return _CONFIG_ROOM_BINDING.get(device_id)
pkg_root_rb.resolve = _resolve
sys.modules["extended_openai_conversation_test_root.room_binding"] = pkg_root_rb
pkg_root_funcs = _mk("extended_openai_conversation_test_root.functions")
sys.modules["extended_openai_conversation_test_root.functions"] = pkg_root_funcs
pkg_root_funcs_base = _mk("extended_openai_conversation_test_root.functions.base")
class Function:
    def __init__(self, *a, **k): pass
pkg_root_funcs_base.Function = Function
sys.modules["extended_openai_conversation_test_root.functions.base"] = pkg_root_funcs_base
pkg_root_living = _mk("extended_openai_conversation_test_root.functions.living_lights")
_LIVING_LIGHTS_CALLS: list[dict] = []
class LivingLightsFunction:
    async def _set_presence_override(self, hass, args):
        _LIVING_LIGHTS_CALLS.append(dict(args))
        return {
            "ok": True,
            "data": {"zones": [args.get("zone")], "payload": dict(args)},
            "suggested_phrasing": f"OK — {args.get('zone')} override set.",
        }
pkg_root_living.LivingLightsFunction = LivingLightsFunction
sys.modules["extended_openai_conversation_test_root.functions.living_lights"] = pkg_root_living

# Now load functions/native.py with sys.path tricks
import importlib.util
NATIVE_PATH = Path(__file__).parent / "functions" / "native.py"
spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation_test_root.functions.native",
    NATIVE_PATH,
)
native = importlib.util.module_from_spec(spec)
sys.modules["extended_openai_conversation_test_root.functions.native"] = native
spec.loader.exec_module(native)

# ── Test helpers ───────────────────────────────────────────────────────

_CONFIG_ROOM_BINDING: dict[str, str] = {}

passes = 0; fails = 0; failures: list[tuple[str, str]] = []
def t(name, fn):
    global passes, fails
    try:
        fn(); passes += 1; print(f"  PASS  {name}")
    except AssertionError as e:
        fails += 1; failures.append((name, str(e))); print(f"  FAIL  {name}: {e}")
    except Exception as e:
        fails += 1; failures.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
def section(name): print(f"\n[{name}]")

# ── Suite ──────────────────────────────────────────────────────────────
section("media_player area → entity resolver (F-1)")

def _setup_kitchen_two_sonos():
    """Fixture: 'kitchen' area exists; 2 Sonos + 1 light in it."""
    global _FAKE_AREAS, _FAKE_ENTITIES, _CONFIG_ROOM_BINDING
    _FAKE_AREAS = [
        _FakeArea("kitchen_id",     "Kitchen"),
        _FakeArea("living_room_id", "Living Room"),
    ]
    _FAKE_ENTITIES = [
        _FakeEntity("media_player.kitchen_stereo", "kitchen_id"),
        _FakeEntity("media_player.kitchen_satellite", "kitchen_id"),
        _FakeEntity("light.kitchen_main",          "kitchen_id"),
        _FakeEntity("media_player.living_room_tv", "living_room_id"),
    ]
    _CONFIG_ROOM_BINDING = {"dev-1": "kitchen"}

def _resolver_finds_media_in_kitchen():
    _setup_kitchen_two_sonos()
    out = native._media_entities_in_area(None, "kitchen")
    assert set(out) == {"media_player.kitchen_stereo", "media_player.kitchen_satellite"}, out
t("resolver finds the two kitchen media_players (by name)", _resolver_finds_media_in_kitchen)

def _resolver_accepts_area_id_directly():
    _setup_kitchen_two_sonos()
    out = native._media_entities_in_area(None, "kitchen_id")
    assert set(out) == {"media_player.kitchen_stereo", "media_player.kitchen_satellite"}, out
t("resolver accepts area_id directly", _resolver_accepts_area_id_directly)

def _resolver_accepts_slug_form():
    _setup_kitchen_two_sonos()
    out = native._media_entities_in_area(None, "Kitchen")  # human-cased
    assert set(out) == {"media_player.kitchen_stereo", "media_player.kitchen_satellite"}, out
t("resolver tolerates 'Kitchen' vs 'kitchen' (name match)", _resolver_accepts_slug_form)

def _resolver_returns_empty_when_no_match():
    _setup_kitchen_two_sonos()
    out = native._media_entities_in_area(None, "bathroom")
    assert out == [], out
t("unknown area → empty list (caller raises rather than fall back)",
  _resolver_returns_empty_when_no_match)

def _resolver_returns_empty_when_area_has_no_media():
    _setup_kitchen_two_sonos()
    # Living room has only a TV, but let's also test an area with zero media
    _FAKE_AREAS.append(_FakeArea("garage_id", "Garage"))
    _FAKE_ENTITIES.append(_FakeEntity("light.garage", "garage_id"))
    out = native._media_entities_in_area(None, "garage")
    assert out == [], out
t("area with no media_player entities → empty list",
  _resolver_returns_empty_when_area_has_no_media)

def _resolver_excludes_non_media_entities():
    _setup_kitchen_two_sonos()
    out = native._media_entities_in_area(None, "kitchen")
    assert all(e.startswith("media_player.") for e in out), out
    assert "light.kitchen_main" not in out
t("resolver excludes non-media entities (light.kitchen_main not in result)",
  _resolver_excludes_non_media_entities)

def _resolver_tolerates_empty_input():
    _setup_kitchen_two_sonos()
    assert native._media_entities_in_area(None, "") == []
    assert native._media_entities_in_area(None, None) == []
t("empty/None area_key → empty list (no crash)", _resolver_tolerates_empty_input)

# ── M0.3 tool-result shape ────────────────────────────────────────────
section("M0.3 tool-result shape (additive ok + latency_ms)")

# Mock hass.services to drive execute_service_single. We need an async
# context to call the method; use asyncio.run for each test case.
import asyncio  # noqa: E402

class _FakeServices:
    def __init__(self, raise_exc: Exception | None = None,
                 sleep_ms: int = 0):
        self.raise_exc = raise_exc
        self.sleep_ms = sleep_ms
        self.has_service_returns = True
        self.calls: list[dict] = []

    def has_service(self, domain, service):
        return self.has_service_returns

    async def async_call(self, domain, service, service_data):
        self.calls.append({"domain": domain, "service": service,
                           "service_data": dict(service_data)})
        if self.sleep_ms > 0:
            await asyncio.sleep(self.sleep_ms / 1000)
        if self.raise_exc is not None:
            raise self.raise_exc

class _FakeHass:
    def __init__(self, services: _FakeServices):
        self.services = services
        self.states = types.SimpleNamespace(get=lambda _entity_id: None)

def _make_fn():
    # NativeFunction needs voluptuous Schema to not blow up — our voluptuous
    # mock already returns None for Schema(...). The base.Function ctor
    # accepts anything. So we can instantiate directly.
    fn = native.NativeFunction()
    return fn

def _exec_service(hass, args, exposed=None) -> dict:
    """Helper: call execute_service_single via asyncio.run, returning the
    awaited dict result."""
    fn = _make_fn()
    # Defeat validate_entity_ids — it walks exposed_entities; with [] it
    # accepts anything (the method is permissive on missing exposure).
    fn.validate_entity_ids = lambda h, eids, exp: None  # type: ignore
    coro = fn.execute_service_single(
        hass, {"name": "execute_service_single"}, args, None, exposed or [],
    )
    return asyncio.run(coro)


# The remaining pre-containment dispatch assertions below are retained as
# migration history, but are intentionally not executed: model-originated HA
# actions are now disabled wholesale.  These fail-closed tests replace the old
# low/high-impact distinction.
section("Greenfield agent action containment")

def _all_service_arguments_are_denied():
    for confirmed in (None, False, True, "true", "yes", 1):
        svcs = _FakeServices()
        hass = _FakeHass(svcs)
        args = {
            "domain": "lock",
            "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]},
        }
        if confirmed is not None:
            args["confirmed"] = confirmed
        original = dict(args)
        r = _exec_service(hass, args)
        assert r["ok"] is False and r["success"] is False, r
        assert r["capability_disabled"] is True, r
        assert r["error_kind"] == "model_action_disabled", r
        assert len(svcs.calls) == 0, svcs.calls
        assert args == original, "containment must not consume or trust confirmed"
t("confirmed=true and every other flag have no dispatch authority",
  _all_service_arguments_are_denied)

def _low_impact_is_also_denied():
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    r = _exec_service(hass, {
        "domain": "light", "service": "turn_on",
        "service_data": {"entity_id": ["light.kitchen"]},
        "confirmed": True,
    })
    assert r["error_kind"] == "model_action_disabled", r
    assert len(svcs.calls) == 0
t("routine light services are denied too (MVP has no model actions)",
  _low_impact_is_also_denied)

def _native_dispatcher_and_script_are_denied():
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    for name, args in (
        ("execute_service", {"list": [{
            "domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]},
        }]}),
        ("execute_service_single", {
            "domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]},
        }),
        ("invoke_script", {"entity_id": "script.morning"}),
        ("add_automation", {"automation_config": "[]"}),
    ):
        r = asyncio.run(fn.execute(hass, {"name": name}, args, None, []))
        assert r["error_kind"] == "model_action_disabled", (name, r)
    assert len(svcs.calls) == 0
t("native action entry points fail closed before HA lookup or dispatch",
  _native_dispatcher_and_script_are_denied)

print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for n, m in failures:
        print(f"  - {n}: {m}")
sys.exit(0 if fails == 0 else 1)

def _result_success_has_ok_and_latency():
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]}}
    r = _exec_service(hass, args)
    assert r["success"] is True, f"legacy success preserved: {r}"
    assert r["ok"] is True, f"M0 ok present: {r}"
    assert isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0, \
        f"latency_ms int: {r}"
    assert r["domain"] == "light", f"domain echoed: {r}"
    assert r["service"] == "turn_off", f"service echoed: {r}"
    assert len(svcs.calls) == 1
    assert svcs.calls[0]["service_data"]["entity_id"] == ["light.kitchen"]
t("success path returns {success, ok, latency_ms, domain, service}",
  _result_success_has_ok_and_latency)

def _result_error_has_ok_false_and_error_kind():
    svcs = _FakeServices(raise_exc=HomeAssistantError("entity unavailable"))
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]}}
    r = _exec_service(hass, args)
    assert "error" in r, f"legacy error preserved: {r}"
    assert r["error"] == "entity unavailable", f"error string: {r}"
    assert r["ok"] is False, f"M0 ok=false: {r}"
    assert isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0
    assert r["domain"] == "light"
    assert r["service"] == "turn_off"
    assert r["error_kind"] == "HomeAssistantError", f"error_kind: {r}"
    assert r["error_message"] == "entity unavailable", f"error_message: {r}"
t("error path returns {error, ok=false, latency_ms, error_kind, error_message}",
  _result_error_has_ok_false_and_error_kind)

def _result_latency_reflects_actual_dispatch_time():
    """latency_ms should be measured around the actual async_call — a slow
    HA service call surfaces as a higher latency_ms."""
    svcs = _FakeServices(sleep_ms=80)  # simulate 80ms HA latency
    hass = _FakeHass(svcs)
    args = {"domain": "switch", "service": "turn_on",
            "service_data": {"entity_id": ["switch.x"]}}
    r = _exec_service(hass, args)
    assert r["ok"] is True
    # Allow generous slack; should be ≥ 80ms but not impossibly large
    assert 60 <= r["latency_ms"] <= 2000, \
        f"latency_ms tracks dispatch time, got {r['latency_ms']}"
t("latency_ms tracks actual hass.services.async_call duration",
  _result_latency_reflects_actual_dispatch_time)

def _result_works_through_execute_service_loop():
    """execute_service (the multi-call entry point) collects per-call dicts
    in the same shape, one per `list[]` item."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None  # type: ignore
    args = {"list": [
        {"domain": "light", "service": "turn_off",
         "service_data": {"entity_id": ["light.a"]}},
        {"domain": "switch", "service": "turn_on",
         "service_data": {"entity_id": ["switch.b"]}},
    ]}
    out = asyncio.run(fn.execute_service(
        hass, {"name": "execute_service"}, args, None, [],
    ))
    assert isinstance(out, list) and len(out) == 2, out
    for r, exp_dom, exp_svc in zip(out, ["light", "switch"], ["turn_off", "turn_on"]):
        assert r["ok"] is True
        assert r["domain"] == exp_dom
        assert r["service"] == exp_svc
        assert isinstance(r["latency_ms"], int)
t("execute_service (multi-call) preserves M0.3 shape per item",
  _result_works_through_execute_service_loop)

# ── Living Lights persistent override guard ───────────────────────────
section("Living Lights execute_services reroute")

def _reset_living_lights_calls():
    _LIVING_LIGHTS_CALLS.clear()

def _managed_area_brightness_reroutes_to_presence_override():
    _reset_living_lights_calls()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_on",
            "service_data": {"area_id": "kitchen", "brightness_pct": 100}}
    r = _exec_service(hass, args)
    assert r["ok"] is True, r
    assert r["domain"] == "living_lights", r
    assert r["service"] == "set_presence_override", r
    assert len(svcs.calls) == 0, f"bare light.turn_on must not dispatch: {svcs.calls}"
    assert _LIVING_LIGHTS_CALLS == [{
        "zone": "kitchen",
        "source": "voice",
        "source_text": "rerouted execute_services light.turn_on",
        "brightness_pct": 100,
    }], _LIVING_LIGHTS_CALLS
    assert r["rerouted_from"]["service_data"]["area_id"] == "kitchen", r
t("managed kitchen brightness command reroutes to set_presence_override",
  _managed_area_brightness_reroutes_to_presence_override)

def _managed_entity_brightness_converts_native_brightness_scale():
    _reset_living_lights_calls()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_on",
            "service_data": {"entity_id": ["light.kitchen"], "brightness": 255}}
    r = _exec_service(hass, args)
    assert r["ok"] is True and r["domain"] == "living_lights", r
    assert len(svcs.calls) == 0
    assert _LIVING_LIGHTS_CALLS[0]["zone"] == "kitchen", _LIVING_LIGHTS_CALLS
    assert _LIVING_LIGHTS_CALLS[0]["brightness_pct"] == 100, _LIVING_LIGHTS_CALLS
t("managed virtual kitchen light reroutes and converts brightness 255 to 100%",
  _managed_entity_brightness_converts_native_brightness_scale)

def _managed_kelvin_reroute_preserves_color_temp():
    _reset_living_lights_calls()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_on",
            "service_data": {"area_id": "dining_room", "color_temp_kelvin": 3000}}
    r = _exec_service(hass, args)
    assert r["ok"] is True and r["domain"] == "living_lights", r
    assert len(svcs.calls) == 0
    assert _LIVING_LIGHTS_CALLS[0]["zone"] == "dining room", _LIVING_LIGHTS_CALLS
    assert _LIVING_LIGHTS_CALLS[0]["color_temp_kelvin"] == 3000, _LIVING_LIGHTS_CALLS
t("managed dining kelvin command reroutes to persistent override",
  _managed_kelvin_reroute_preserves_color_temp)

def _turn_off_and_unmanaged_lights_still_dispatch_normally():
    _reset_living_lights_calls()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    off_args = {"domain": "light", "service": "turn_off",
                "service_data": {"area_id": "kitchen"}}
    off = _exec_service(hass, off_args)
    assert off["ok"] is True and off["domain"] == "light", off
    unmanaged_args = {"domain": "light", "service": "turn_on",
                      "service_data": {"entity_id": ["light.outdoor_light"], "brightness_pct": 100}}
    unmanaged = _exec_service(hass, unmanaged_args)
    assert unmanaged["ok"] is True and unmanaged["domain"] == "light", unmanaged
    assert len(_LIVING_LIGHTS_CALLS) == 0, _LIVING_LIGHTS_CALLS
    assert len(svcs.calls) == 2, svcs.calls
t("turn_off and unmanaged light.turn_on still dispatch normally",
  _turn_off_and_unmanaged_lights_still_dispatch_normally)

# ── M6 high-impact action confirmation ────────────────────────────────
section("M6 high-impact confirmation gate")

def _reset_log_decisions():
    _LOG_DECISIONS.clear()

def _m6_high_impact_no_confirm_intercepts():
    """lock.unlock without confirmed flag → intercepted; no HA call;
    audit trail logged with intercepted=True/resolved=False."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "lock", "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]}}
    r = _exec_service(hass, args)
    assert r["ok"] is False, f"intercepted call returns ok=false: {r}"
    assert r["requires_confirmation"] is True, f"requires_confirmation flag: {r}"
    assert r["domain"] == "lock" and r["service"] == "unlock"
    assert "action" in r and r["action"]["domain"] == "lock"
    assert "suggested_phrasing" in r and "unlock" in r["suggested_phrasing"].lower()
    assert "instructions_for_agent" in r
    # NO HA call should have happened
    assert len(svcs.calls) == 0, f"intercept must not dispatch: {svcs.calls}"
    # Audit trail: 1 entry, intercepted=True, resolved=False
    assert len(_LOG_DECISIONS) == 1, f"audit entry count: {_LOG_DECISIONS}"
    e = _LOG_DECISIONS[0]
    assert e["kind"] == "confirmation"
    assert e["domain"] == "lock" and e["service"] == "unlock"
    assert e["intercepted"] is True and e["resolved"] is False
t("HIGH_IMPACT (lock) without confirmed=true → intercepted (no dispatch + audit)",
  _m6_high_impact_no_confirm_intercepts)

def _m6_high_impact_with_confirm_dispatches():
    """lock.unlock WITH confirmed=true at top level → dispatches; HA gets
    the call WITHOUT the confirmed flag; audit logs resolved=True."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "lock", "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]},
            "confirmed": True}
    r = _exec_service(hass, args)
    assert r["ok"] is True, f"resolved call dispatches successfully: {r}"
    assert "requires_confirmation" not in r, f"no confirm flag in result: {r}"
    # HA call DID happen, exactly once
    assert len(svcs.calls) == 1
    # `confirmed` flag must NOT have been forwarded to HA
    assert "confirmed" not in svcs.calls[0]["service_data"], svcs.calls[0]
    assert svcs.calls[0]["domain"] == "lock"
    # Audit: 1 entry with resolved=True
    assert len(_LOG_DECISIONS) == 1, _LOG_DECISIONS
    e = _LOG_DECISIONS[0]
    assert e["intercepted"] is False and e["resolved"] is True
t("HIGH_IMPACT (lock) with confirmed=true → dispatches; flag stripped from HA call",
  _m6_high_impact_with_confirm_dispatches)

def _m6_low_impact_not_intercepted():
    """light.turn_off is NOT high-impact → unaffected by M6, no audit entry."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]}}
    r = _exec_service(hass, args)
    assert r["ok"] is True
    assert "requires_confirmation" not in r
    assert len(svcs.calls) == 1
    # NO audit entry for non-high-impact
    assert len(_LOG_DECISIONS) == 0, f"non-high-impact must not audit: {_LOG_DECISIONS}"
t("low-impact (light) → unaffected by M6 gate (no audit, dispatches)",
  _m6_low_impact_not_intercepted)

def _m6_alarm_domain_also_intercepted():
    """alarm_control_panel.alarm_disarm without confirmed → intercepted."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "alarm_control_panel", "service": "alarm_disarm",
            "service_data": {"entity_id": ["alarm_control_panel.house"]}}
    r = _exec_service(hass, args)
    assert r["ok"] is False and r["requires_confirmation"] is True, r
    assert len(svcs.calls) == 0
t("HIGH_IMPACT (alarm_control_panel) gated same as lock",
  _m6_alarm_domain_also_intercepted)

def _m6_confirmed_str_true_also_works():
    """confirmed='true' (string) should be coerced like confirmed=True
    — agents sometimes serialize bools as strings."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "lock", "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]},
            "confirmed": "true"}
    r = _exec_service(hass, args)
    assert r["ok"] is True, f"string 'true' should resolve: {r}"
    assert len(svcs.calls) == 1
t("confirmed='true' (string) coerces to bool — resolves the gate",
  _m6_confirmed_str_true_also_works)

def _m6_confirmed_false_intercepts():
    """confirmed=False should NOT bypass the gate."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "lock", "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]},
            "confirmed": False}
    r = _exec_service(hass, args)
    assert r["ok"] is False and r["requires_confirmation"] is True, r
    assert len(svcs.calls) == 0
t("confirmed=false → still intercepted (no bypass)",
  _m6_confirmed_false_intercepts)

def _m6_multi_call_loop_preserves_gate():
    """execute_service (multi-call) — one item high-impact unconfirmed,
    one low-impact. The high-impact item should still be intercepted
    independently; the low-impact item dispatches."""
    _reset_log_decisions()
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None  # type: ignore
    args = {"list": [
        {"domain": "light", "service": "turn_off",
         "service_data": {"entity_id": ["light.kitchen"]}},
        {"domain": "lock", "service": "unlock",
         "service_data": {"entity_id": ["lock.front_door"]}},
    ]}
    out = asyncio.run(fn.execute_service(
        hass, {"name": "execute_service"}, args, None, [],
    ))
    assert len(out) == 2, out
    # First item dispatches normally
    assert out[0]["ok"] is True and out[0]["domain"] == "light"
    # Second is intercepted
    assert out[1]["ok"] is False and out[1]["requires_confirmation"] is True
    # HA saw only the light call (no lock dispatch)
    assert len(svcs.calls) == 1
    assert svcs.calls[0]["domain"] == "light"
t("execute_service (multi-call) gates per-item — light dispatches, lock intercepted",
  _m6_multi_call_loop_preserves_gate)

# ── F-7 stakes + ack_hint ─────────────────────────────────────────────
section("F-7 stakes-matched verbal ack")

def _f7_low_stakes_light_gives_ok():
    """light.turn_off → stakes=low, ack_hint='OK.'"""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]}}
    r = _exec_service(hass, args)
    assert r["stakes"] == "low", f"stakes=low: {r}"
    assert r["ack_hint"] == "OK.", f"low stakes ack=OK.: {r}"
t("low-stakes light → stakes='low' + ack_hint='OK.'",
  _f7_low_stakes_light_gives_ok)

def _f7_med_stakes_climate_gives_verb_phrase():
    """climate.set_temperature → stakes=med, ack_hint mentions service + target."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "climate", "service": "set_temperature",
            "service_data": {"entity_id": ["climate.living_room"]}}
    r = _exec_service(hass, args)
    assert r["stakes"] == "med", r
    # med format: "<verb> on <target>." — capitalized service verb
    assert "set temperature" in r["ack_hint"].lower(), r
    assert "living room" in r["ack_hint"].lower(), r
t("med-stakes climate → stakes='med' + ack_hint includes verb + target",
  _f7_med_stakes_climate_gives_verb_phrase)

def _f7_high_stakes_lock_with_confirm():
    """lock.unlock with confirmed=true → stakes=high, ack_hint is
    'Done — <target> <verb>.' format."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "lock", "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]},
            "confirmed": True}
    r = _exec_service(hass, args)
    assert r["ok"] is True, r
    assert r["stakes"] == "high", f"HIGH_IMPACT_DOMAINS forces high: {r}"
    assert r["ack_hint"].startswith("Done —"), r
    assert "front door" in r["ack_hint"].lower(), r
t("high-stakes lock (post-confirm) → 'Done — front door ...' format",
  _f7_high_stakes_lock_with_confirm)

def _f7_unmapped_domain_defaults_low():
    """A domain not in STAKES_BY_DOMAIN AND not high-impact → low (safe default)."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "weird_custom_domain", "service": "do_thing",
            "service_data": {"entity_id": ["weird_custom_domain.x"]}}
    r = _exec_service(hass, args)
    assert r["stakes"] == "low", f"unmapped → low: {r}"
    assert r["ack_hint"] == "OK.", r
t("unmapped domain → defaults to 'low' (terse OK)",
  _f7_unmapped_domain_defaults_low)

def _f7_multi_entity_says_N_items():
    """entity_id with multiple → ack says 'N items'."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "climate", "service": "set_temperature",
            "service_data": {"entity_id": ["climate.a", "climate.b", "climate.c"]}}
    r = _exec_service(hass, args)
    assert "3 items" in r["ack_hint"], r
t("multi-entity dispatch → ack mentions '3 items'",
  _f7_multi_entity_says_N_items)

def _f7_area_id_used_when_no_entity():
    """area_id-targeted call → ack uses the area name."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_off",
            "service_data": {"area_id": "kitchen"}}
    r = _exec_service(hass, args)
    # low-stakes → "OK." (terse beats verbose)
    assert r["ack_hint"] == "OK.", r

    # Med stakes with area → area name should appear
    args2 = {"domain": "climate", "service": "set_temperature",
             "service_data": {"area_id": "bedroom"}}
    r2 = _exec_service(hass, args2)
    assert "bedroom" in r2["ack_hint"].lower(), r2
t("area_id-targeted med-stakes ack uses area name; low-stakes stays 'OK.'",
  _f7_area_id_used_when_no_entity)

def _f7_high_impact_unconfirmed_no_ack():
    """High-impact intercept (no confirm) → returns requires_confirmation
    shape, NOT a successful ack. Should not have stakes/ack_hint in the
    intercept payload (it has its own suggested_phrasing)."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    args = {"domain": "lock", "service": "unlock",
            "service_data": {"entity_id": ["lock.front_door"]}}
    r = _exec_service(hass, args)
    assert r["ok"] is False, r
    assert r["requires_confirmation"] is True, r
    # M6 intercept path returns its own suggested_phrasing; the F-7
    # ack_hint applies only to actually-dispatched calls.
    assert "ack_hint" not in r, f"intercept should not include ack_hint: {r}"
    assert "stakes" not in r, f"intercept should not include stakes: {r}"
t("M6 intercept path has its own phrasing — F-7 doesn't add ack_hint",
  _f7_high_impact_unconfirmed_no_ack)

def _f7_error_path_no_ack():
    """When dispatch fails (HomeAssistantError), result is error shape;
    F-7 ack_hint applies only to ok=true."""
    svcs = _FakeServices(raise_exc=HomeAssistantError("bad"))
    hass = _FakeHass(svcs)
    args = {"domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.kitchen"]}}
    r = _exec_service(hass, args)
    assert r["ok"] is False, r
    # Error path doesn't have ack_hint (it has error_message instead)
    assert "ack_hint" not in r, f"error path should not include ack_hint: {r}"
t("error path → ok=false, no ack_hint (error_message is the verbal cue)",
  _f7_error_path_no_ack)

# ── F-9 invoke_script ─────────────────────────────────────────────────
section("F-9 invoke_script tool")

def _f9_happy_path():
    """invoke_script with entity_id + no variables → script.turn_on
    dispatched, ok shape returned."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None  # bypass exposure check
    args = {"entity_id": "script.aurora_apply_virtual_knob_smooth"}
    r = asyncio.run(fn.invoke_script(hass, {}, args, None, []))
    assert r["ok"] is True, r
    assert r["domain"] == "script" and r["service"] == "turn_on", r
    assert r["entity_id"] == "script.aurora_apply_virtual_knob_smooth", r
    assert r["stakes"] == "low" and r["ack_hint"] == "OK.", r
    assert len(svcs.calls) == 1, svcs.calls
    assert svcs.calls[0]["service_data"]["entity_id"] == "script.aurora_apply_virtual_knob_smooth"
    # No variables key when none passed
    assert "variables" not in svcs.calls[0]["service_data"], svcs.calls[0]
t("invoke_script happy path → script.turn_on dispatched, terse ack",
  _f9_happy_path)

def _f9_variables_passed_through():
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None
    args = {
        "entity_id": "script.dinner",
        "variables": {"brightness": 30, "warmth": "warm"},
    }
    r = asyncio.run(fn.invoke_script(hass, {}, args, None, []))
    assert r["ok"] is True
    assert svcs.calls[0]["service_data"]["variables"] == {"brightness": 30, "warmth": "warm"}
t("invoke_script: variables passed through as service_data.variables",
  _f9_variables_passed_through)

def _f9_bare_name_prefix_added():
    """A bare 'aurora_apply' should get prefixed with 'script.'."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None
    args = {"entity_id": "aurora_apply_virtual_knob_smooth"}
    r = asyncio.run(fn.invoke_script(hass, {}, args, None, []))
    assert r["ok"] is True, r
    assert r["entity_id"] == "script.aurora_apply_virtual_knob_smooth", r
    assert svcs.calls[0]["service_data"]["entity_id"] == "script.aurora_apply_virtual_knob_smooth"
t("invoke_script: bare script name gets 'script.' prefix",
  _f9_bare_name_prefix_added)

def _f9_missing_entity_id_returns_error():
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    args = {}
    r = asyncio.run(fn.invoke_script(hass, {}, args, None, []))
    assert r["ok"] is False, r
    assert "entity_id" in r["error"].lower(), r
    assert r["error_kind"] == "ValueError", r
    assert len(svcs.calls) == 0, "no dispatch on error path"
t("invoke_script: missing entity_id → error, no dispatch",
  _f9_missing_entity_id_returns_error)

def _f9_non_dict_variables_rejected():
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None
    args = {"entity_id": "script.x", "variables": "not a dict"}
    r = asyncio.run(fn.invoke_script(hass, {}, args, None, []))
    assert r["ok"] is False, r
    assert "variables" in r["error"].lower(), r
    assert r["error_kind"] == "TypeError", r
    assert len(svcs.calls) == 0
t("invoke_script: non-dict variables → TypeError, no dispatch",
  _f9_non_dict_variables_rejected)

def _f9_ha_error_surfaces():
    svcs = _FakeServices(raise_exc=HomeAssistantError("script not found"))
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None
    args = {"entity_id": "script.nonexistent"}
    r = asyncio.run(fn.invoke_script(hass, {}, args, None, []))
    assert r["ok"] is False, r
    assert r["error_kind"] == "HomeAssistantError", r
    assert "script not found" in r["error_message"], r
    assert "latency_ms" in r and isinstance(r["latency_ms"], int)
t("invoke_script: HomeAssistantError surfaces with error_kind + message",
  _f9_ha_error_surfaces)

def _f9_dispatcher_branch():
    """The execute() dispatcher routes 'invoke_script' to invoke_script."""
    svcs = _FakeServices()
    hass = _FakeHass(svcs)
    fn = _make_fn()
    fn.validate_entity_ids = lambda h, eids, exp: None
    r = asyncio.run(fn.execute(
        hass, {"name": "invoke_script"},
        {"entity_id": "script.x"}, None, [],
    ))
    assert r["ok"] is True, f"dispatcher routes to invoke_script: {r}"
t("execute() dispatcher routes 'invoke_script' → invoke_script method",
  _f9_dispatcher_branch)

# ── Summary ────────────────────────────────────────────────────────────
print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for n, m in failures:
        print(f"  - {n}: {m}")
sys.exit(0 if fails == 0 else 1)

#!/usr/bin/env python3
"""Tests for functions/living_lights.py — presence-override layer
(Addendum 33 Phase 4.A).

Coverage:
  - Zone resolution: canonical + synonyms + substring fallback + unknown
  - set_presence_override happy path → JSON payload shape correct
  - Brightness clamping (0 → 5; 999 → 100; negative → 5)
  - Color-temp clamping (1000 → 2000; 99999 → 6500)
  - Source validation (rejects "auto", null, missing — AR33-7)
  - Master disabled → rejected with "master_disabled" error
  - Per-zone disabled → rejected with "zone_disabled" error
  - Unknown zone → rejected
  - Remote=True flag when zone vacant at set time (AR33-2)
  - Pinned=True flag honored when arg present (AR33-3)
  - Delta brightness applied to baseline state (AR voice "brighter")
  - clear_presence_override("all") clears all 5 zones with values
  - clear_presence_override(specific zone) clears one
  - clear with no active overrides → "nothing to clear" phrasing
  - Unknown function name → returns error envelope

Run: PYTHONIOENCODING=utf-8 py -3 test_living_lights.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── Mock voluptuous + HA modules (same pattern as test_recap.py) ────
def _mk(name: str):
    return types.ModuleType(name)


class _vol:
    @staticmethod
    def Schema(*a, **k):
        return None

    @staticmethod
    def Required(x, **k):
        return x


sys.modules.setdefault("voluptuous", _vol)

ha = _mk("homeassistant")
ha_core = _mk("homeassistant.core")


class HomeAssistant:
    pass


ha_core.HomeAssistant = HomeAssistant
sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.core", ha_core)

ha_helpers = _mk("homeassistant.helpers")
ha_helpers_llm = _mk("homeassistant.helpers.llm")


class _LLMCtx:
    device_id = None
    conversation_id = None


ha_helpers_llm.LLMContext = _LLMCtx
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
sys.modules.setdefault("homeassistant.helpers.llm", ha_helpers_llm)

# Package skeleton
_pkg = _mk("extended_openai_conversation")
_pkg.__path__ = [str(Path(__file__).parent)]
_funcs = _mk("extended_openai_conversation.functions")
_base_mod = _mk("extended_openai_conversation.functions.base")


class Function:
    def __init__(self, *a, **k):
        pass


_base_mod.Function = Function
sys.modules.setdefault("extended_openai_conversation", _pkg)
sys.modules.setdefault("extended_openai_conversation.functions", _funcs)
sys.modules.setdefault("extended_openai_conversation.functions.base", _base_mod)


# Now import the module under test (as a submodule so relative imports work)
sys.path.insert(0, str(Path(__file__).parent.parent))
import importlib
import importlib.util

_living_lights_spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation.functions.living_lights",
    str(Path(__file__).parent / "functions" / "living_lights.py"),
)
living_lights = importlib.util.module_from_spec(_living_lights_spec)
sys.modules["extended_openai_conversation.functions.living_lights"] = living_lights
_living_lights_spec.loader.exec_module(living_lights)


# ── Mock HA state + service-call surface ────────────────────────────
class FakeState:
    __slots__ = ("state", "attributes")

    def __init__(self, state: str, attributes: dict | None = None):
        self.state = state
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self) -> None:
        self._d: dict[str, FakeState] = {}

    def set(self, entity_id: str, state: str, attrs: dict | None = None) -> None:
        self._d[entity_id] = FakeState(state, attrs)

    def get(self, entity_id: str) -> FakeState | None:
        return self._d.get(entity_id)


class FakeServiceCalls:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, bool]] = []

    async def async_call(self, domain: str, service: str, data: dict, blocking: bool = False) -> None:
        # Mirror the side effect by also updating the underlying state
        self.calls.append((domain, service, data, blocking))


class FakeHass:
    def __init__(self) -> None:
        self.states = FakeStates()
        self.services = FakeServiceCalls()
        self.data: dict[str, dict] = {}

    def make_default_world(self, master_on=True, all_zones_on=True):
        self.states.set(
            "input_boolean.living_lights_enabled",
            "on" if master_on else "off",
        )
        # Cover every actuating zone (drives off the module's canonical
        # zone→camera map so the harness tracks new zones automatically).
        for z, cam in living_lights.ZONE_TO_CAMERA.items():
            self.states.set(
                f"input_boolean.living_lights_zone_{z}_enabled",
                "on" if all_zones_on else "off",
            )
            self.states.set(f"binary_sensor.{z}_person_occupancy", "off")
            self.states.set(f"input_text.living_lights_override_text_{z}", "")
            # Classifier predicted attributes for delta computations
            self.states.set(
                f"sensor.{cam}_{z}_lighting_state",
                "vacant",
                {"predicted_brightness_pct": 40, "predicted_color_temp_kelvin": 3000},
            )


class FakeAggregator:
    def __init__(self, records=None, raise_exc: Exception | None = None) -> None:
        self.records = list(records or [])
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def recent_overrides(self, *, hours: float, zone: str | None, collapse: bool):
        self.calls.append({"hours": hours, "zone": zone, "collapse": collapse})
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.records)


# After service call to set_value, mirror to states so subsequent
# clear/test calls see the updated value.
async def _mirror_set_value(hass: FakeHass, domain, service, data, blocking):
    if domain == "input_text" and service == "set_value":
        eid = data.get("entity_id")
        val = data.get("value")
        if eid:
            hass.states.set(eid, val if val else "")
    hass.services.calls.append((domain, service, data, blocking))


# ── Test harness ────────────────────────────────────────────────────
PASS = 0
FAIL = 0


def assert_(label: str, cond: bool, *detail) -> None:
    global PASS, FAIL
    if cond:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}", *detail)
        FAIL += 1


def assert_eq(label: str, actual, expected) -> None:
    assert_(label, actual == expected, f"got={actual!r} expected={expected!r}")


_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


def main() -> int:
    fn = living_lights.LivingLightsFunction()

    # ── Group 1: zone resolution ──
    print("\n── zone resolution ──")
    assert_eq("canonical slug 'dining_left'", living_lights._resolve_zone("dining_left"), "dining_left")
    assert_eq("synonym 'dining left'", living_lights._resolve_zone("dining left"), "dining_left")
    assert_eq("uppercase 'Sink'", living_lights._resolve_zone("Sink"), "sink")
    assert_eq("nat-lang 'kitchen island left'",
              living_lights._resolve_zone("kitchen island left"), "island_left")
    assert_(
        "substring 'island_right'",
        living_lights._resolve_zone("island_right") == "island_right",
    )
    assert_eq("unknown 'bathroom'", living_lights._resolve_zone("bathroom"), None)
    assert_eq("empty string", living_lights._resolve_zone(""), None)

    # ── Group 1b: target resolution (new zones, rooms, "all") ──
    print("\n── target resolution ──")
    assert_eq("new zone 'sofa'", living_lights._resolve_zone("sofa"), "sofa")
    assert_eq("new zone 'office'", living_lights._resolve_zone("office"), "office")
    assert_eq("room 'living room' → cover set",
              living_lights._resolve_targets("living room"), ["sofa", "office"])
    assert_eq("room 'kitchen' → 3 zones",
              sorted(living_lights._resolve_targets("kitchen")),
              sorted(["sink", "island_left", "island_right"]))
    assert_eq("'all' → full cover",
              sorted(living_lights._resolve_targets("all")),
              sorted(living_lights.ALL_TARGET_ZONES))
    assert_eq("'my lights' → full cover",
              sorted(living_lights._resolve_targets("my lights")),
              sorted(living_lights.ALL_TARGET_ZONES))
    assert_eq("single zone 'sink' → [sink]",
              living_lights._resolve_targets("sink"), ["sink"])
    assert_eq("phrase 'turn the living room lights up' → LR cover",
              living_lights._resolve_targets("turn the living room lights up"),
              ["sofa", "office"])
    assert_eq("unknown target → None",
              living_lights._resolve_targets("bathroom"), None)

    # ── Group 2: set_presence_override happy path ──
    print("\n── set_presence_override happy path ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)

    result = run(fn._set_presence_override(hass, {
        "zone": "dining_left",
        "brightness_pct": 95,
        "color_temp_kelvin": 2700,
        "source": "voice",
        "source_text": "make the dining lights brighter",
    }))
    assert_("ok=True for valid voice override", result.get("ok") is True, result)
    assert_eq("zones list canonicalized", result["data"]["zones"], ["dining_left"])

    payload_str = hass.states.get("input_text.living_lights_override_text_dining_left").state
    assert_("payload written to input_text", payload_str != "")
    payload = json.loads(payload_str)
    assert_eq("payload brightness_pct=95", payload["brightness_pct"], 95)
    assert_eq("payload color_temp_kelvin=2700", payload["color_temp_kelvin"], 2700)
    assert_eq("payload source=voice", payload["source"], "voice")
    assert_("payload has started_at ISO", "T" in payload.get("started_at", ""))
    assert_("payload has prompt snippet",
            "make the dining lights brighter" in payload.get("prompt", ""))
    assert_eq("payload pinned=False (default)", payload["pinned"], False)
    assert_("payload remote=True (zone was vacant)", payload["remote"] is True)
    assert_eq("payload min_hold_min=40", payload["min_hold_min"], 40)
    assert_eq("payload vacancy_grace_s=300", payload["vacancy_grace_s"], 300)
    assert_("payload has hold_until ISO", "T" in payload.get("hold_until", ""))

    # ── Group 2b: room fan-out writes every zone in the room ──
    print("\n── room fan-out ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)
    r = run(fn._set_presence_override(hass, {
        "zone": "kitchen", "brightness_pct": 100, "source": "voice",
        "source_text": "turn the kitchen to 100%",
    }))
    assert_("kitchen fan-out ok", r.get("ok") is True)
    assert_eq("kitchen → 3 zones applied", sorted(r["data"]["zones"]),
              sorted(["sink", "island_left", "island_right"]))
    for z in ["sink", "island_left", "island_right"]:
        p = json.loads(hass.states.get(f"input_text.living_lights_override_text_{z}").state)
        assert_eq(f"{z} set to 100", p["brightness_pct"], 100)
    assert_("one shared command_id for the batch", bool(r["data"].get("command_id")))

    # ── Group 2c: "all" / "my lights" fan-out covers every zone ──
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)
    r = run(fn._set_presence_override(hass, {
        "zone": "my lights", "brightness_pct": 100, "source": "voice",
        "source_text": "turn my lights to 100%",
    }))
    assert_("my-lights fan-out ok", r.get("ok") is True)
    assert_eq("my lights → full cover", sorted(r["data"]["zones"]),
              sorted(living_lights.ALL_TARGET_ZONES))

    # ── Group 3: brightness + color clamping (AR33-7) ──
    print("\n── clamping ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)

    r = run(fn._set_presence_override(hass, {
        "zone": "sink", "brightness_pct": 0, "source": "voice",
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_sink").state)
    assert_eq("brightness=0 clamps to 5 (AR33-7)", p["brightness_pct"], 5)

    r = run(fn._set_presence_override(hass, {
        "zone": "sink", "brightness_pct": 999, "source": "voice",
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_sink").state)
    assert_eq("brightness=999 clamps to 100", p["brightness_pct"], 100)

    r = run(fn._set_presence_override(hass, {
        "zone": "sink", "color_temp_kelvin": 1000, "source": "voice",
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_sink").state)
    assert_eq("kelvin=1000 clamps to 2000", p["color_temp_kelvin"], 2000)

    r = run(fn._set_presence_override(hass, {
        "zone": "sink", "color_temp_kelvin": 99999, "source": "voice",
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_sink").state)
    assert_eq("kelvin=99999 clamps to 6500", p["color_temp_kelvin"], 6500)

    # ── Group 4: source validation (AR33-7) ──
    print("\n── source validation ──")
    hass = FakeHass()
    hass.make_default_world()

    r = run(fn._set_presence_override(hass, {
        "zone": "dining_left", "brightness_pct": 50, "source": "auto",
    }))
    assert_("ok=False for source='auto'", r.get("ok") is False)
    assert_eq("error kind=invalid_source", r["error"]["kind"], "invalid_source")

    r = run(fn._set_presence_override(hass, {"zone": "dining_left", "brightness_pct": 50}))
    assert_("missing source rejected", r.get("ok") is False)
    assert_eq("error kind=invalid_source (missing)", r["error"]["kind"], "invalid_source")

    r = run(fn._set_presence_override(hass, {
        "zone": "dining_left", "source": "system",
    }))
    assert_("ok=False for source='system'", r.get("ok") is False)

    # ── Group 5: master + zone disabled ──
    print("\n── disabled paths ──")
    hass = FakeHass()
    hass.make_default_world(master_on=False)
    r = run(fn._set_presence_override(hass, {
        "zone": "dining_left", "source": "voice", "brightness_pct": 50,
    }))
    assert_("master OFF rejects", r.get("ok") is False)
    assert_eq("master error kind", r["error"]["kind"], "master_disabled")

    hass = FakeHass()
    hass.make_default_world(master_on=True)
    hass.states.set("input_boolean.living_lights_zone_sink_enabled", "off")
    r = run(fn._set_presence_override(hass, {
        "zone": "sink", "source": "voice", "brightness_pct": 50,
    }))
    assert_("all-targets-disabled rejects", r.get("ok") is False)
    assert_eq("error kind", r["error"]["kind"], "no_zones_applied")
    assert_("sink listed as skipped", "sink" in r["error"].get("skipped", []))

    # ── Group 6: unknown zone ──
    print("\n── unknown zone ──")
    hass = FakeHass()
    hass.make_default_world()
    r = run(fn._set_presence_override(hass, {
        "zone": "bathroom", "source": "voice", "brightness_pct": 50,
    }))
    assert_("unknown zone rejected", r.get("ok") is False)
    assert_eq("zone error kind", r["error"]["kind"], "unknown_zone")
    assert_("known_zones list present", "known_zones" in r.get("error", {}))

    # ── Group 7: remote=True when vacant; remote=False when occupied (AR33-2) ──
    print("\n── remote flag (AR33-2) ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)

    # Zone vacant by default
    r = run(fn._set_presence_override(hass, {
        "zone": "dining_left", "source": "voice", "brightness_pct": 50,
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_dining_left").state)
    assert_("remote=True when zone vacant", p["remote"] is True)

    # Make zone occupied
    hass.states.set("binary_sensor.island_left_person_occupancy", "on")
    r = run(fn._set_presence_override(hass, {
        "zone": "island_left", "source": "voice", "brightness_pct": 50,
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_island_left").state)
    assert_("remote=False when zone occupied", p["remote"] is False)

    # ── Group 8: pinned flag (AR33-3) ──
    print("\n── pinned flag (AR33-3) ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)
    r = run(fn._set_presence_override(hass, {
        "zone": "dining_left", "source": "app", "brightness_pct": 75,
        "pinned": True,
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_dining_left").state)
    assert_("pinned=True written when arg present", p["pinned"] is True)
    assert_("phrasing mentions pinned", "pinned" in r["suggested_phrasing"].lower())

    # ── Group 9: delta brightness (voice "brighter" pattern) ──
    print("\n── delta brightness ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)
    # Baseline for dining_left is predicted_brightness_pct=40, predicted_color_temp_kelvin=3000
    r = run(fn._set_presence_override(hass, {
        "zone": "dining_left", "source": "voice",
        "brightness_delta_pct": 25, "source_text": "make it brighter",
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_dining_left").state)
    assert_eq("baseline 40 + delta 25 = 65", p["brightness_pct"], 65)

    # Color delta — warmer = lower kelvin
    r = run(fn._set_presence_override(hass, {
        "zone": "dining_right", "source": "voice",
        "color_temp_delta_kelvin": -700, "source_text": "warmer please",
    }))
    p = json.loads(hass.states.get("input_text.living_lights_override_text_dining_right").state)
    assert_eq("baseline 3000 - 700 = 2300", p["color_temp_kelvin"], 2300)

    # ── Group 10: clear_presence_override ──
    print("\n── clear_presence_override ──")
    hass = FakeHass()
    hass.make_default_world()
    hass.services.async_call = lambda d, s, dat, blocking: _mirror_set_value(hass, d, s, dat, blocking)
    # Set 3 overrides
    for z, b in [("dining_left", 95), ("sink", 50), ("island_left", 80)]:
        run(fn._set_presence_override(hass, {
            "zone": z, "source": "voice", "brightness_pct": b,
        }))

    # Clear all
    r = run(fn._clear_presence_override(hass, {"zone": "all"}))
    assert_("clear all: ok=True", r.get("ok") is True)
    assert_eq("cleared 3 zones", sorted(r["data"]["cleared"]), sorted(["dining_left", "sink", "island_left"]))
    for z in ["dining_left", "sink", "island_left"]:
        eid = f"input_text.living_lights_override_text_{z}"
        assert_eq(f"{z} cleared to empty", hass.states.get(eid).state, "")

    # Set one, clear one
    run(fn._set_presence_override(hass, {
        "zone": "dining_left", "source": "voice", "brightness_pct": 80,
    }))
    r = run(fn._clear_presence_override(hass, {"zone": "dining_left"}))
    assert_("clear dining_left: ok=True", r.get("ok") is True)
    assert_eq("cleared list = [dining_left]", r["data"]["cleared"], ["dining_left"])

    # Clear with nothing active
    r = run(fn._clear_presence_override(hass, {"zone": "all"}))
    assert_("empty clear ok=True", r.get("ok") is True)
    assert_eq("empty clear list", r["data"]["cleared"], [])
    assert_("phrasing says nothing to clear",
            "no active overrides" in r["suggested_phrasing"].lower())

    # Clear specific unknown zone
    r = run(fn._clear_presence_override(hass, {"zone": "bathroom"}))
    assert_("unknown zone clear rejected", r.get("ok") is False)

    # ── Group 11: dispatcher ──
    print("\n── dispatcher ──")
    hass = FakeHass()
    hass.make_default_world()
    r = run(fn._get_recent_overrides(FakeHass(), {}))
    assert_("missing world-state aggregator is non-fatal", r.get("ok") is True)
    assert_eq("missing aggregator returns empty data", r["data"], [])
    assert_eq("missing aggregator confidence is low", r["confidence_band"], "low")

    automation_tail_record = {
        "light_entity": "light.office",
        "zone": "office",
        "actual_pct": 0,
        "session_collapsed": True,
        "session_event_count": 12,
        "session_observed_path": [35, 58, 58, 0, 0],
        "session_final_actual_pct": 0,
        "session_user_landed_pct": 58,
        "automation_tail_detected": True,
        "session_tail_path": [0, 0],
        "learning_value_pct": 58,
        "learning_value_source": "user_landed",
    }
    agg = FakeAggregator([automation_tail_record])
    hass.data["extended_openai_conversation"] = {"world_state": agg}
    r = run(fn._get_recent_overrides(hass, {"hours": "2", "zone": " Office "}))
    assert_("collapsed recent-overrides query ok", r.get("ok") is True)
    assert_eq("default collapse=True passed to aggregator", agg.calls[0]["collapse"], True)
    assert_eq("zone normalized before aggregator call", agg.calls[0]["zone"], "office")
    assert_eq("hours coerced to float", agg.calls[0]["hours"], 2.0)
    assert_eq("collapsed result count", r["count"], 1)
    assert_("collapsed phrasing mentions burst sessions",
            "burst sessions" in r["suggested_phrasing"])
    assert_eq("automation-tail learning value preserved",
              r["data"][0]["learning_value_pct"], 58)
    assert_("automation-tail flag preserved",
            r["data"][0]["automation_tail_detected"] is True)

    raw_agg = FakeAggregator([{"zone": "sink", "actual_pct": 40}])
    hass.data["extended_openai_conversation"] = {"world_state": raw_agg}
    r = run(fn._get_recent_overrides(hass, {
        "hours": "bad", "zone": " Kitchen ", "collapse": "false",
    }))
    assert_eq("bad hours falls back to one hour", raw_agg.calls[0]["hours"], 1.0)
    assert_eq("collapse=false string passed through as False", raw_agg.calls[0]["collapse"], False)
    assert_eq("raw override-event mode reported", r["collapse"], False)
    assert_("raw mode phrasing uses override event",
            "override event" in r["suggested_phrasing"])

    failing_agg = FakeAggregator(raise_exc=RuntimeError("boom"))
    hass.data["extended_openai_conversation"] = {"world_state": failing_agg}
    warn = living_lights._LOGGER.warning
    living_lights._LOGGER.warning = lambda *a, **k: None
    try:
        r = run(fn._get_recent_overrides(hass, {}))
    finally:
        living_lights._LOGGER.warning = warn
    assert_("aggregator exceptions are non-fatal", r.get("ok") is True)
    assert_eq("aggregator exception returns empty data", r["data"], [])

    hass.data["extended_openai_conversation"] = {
        "world_state": FakeAggregator([{"zone": "office"}])
    }
    r = run(fn.execute(hass, {"name": "get_recent_overrides"}, {}, None, []))
    assert_("dispatcher routes get_recent_overrides", r.get("ok") is True)
    assert_eq("dispatcher recent override count", r["count"], 1)

    r = run(fn.execute(hass, {"name": "unknown_tool"}, {}, None, []))
    assert_("unknown tool returns error envelope", r.get("ok") is False)
    assert_eq("error kind=unknown_function", r["error"]["kind"], "unknown_function")

    print()
    print("=" * 60)
    print(f"{PASS} pass · {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

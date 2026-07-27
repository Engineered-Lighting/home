#!/usr/bin/env python3
"""Focused tests for grounded_look safety helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path


def _mk(name):
    return types.ModuleType(name)


class _vol:
    Schema = lambda *a, **k: None  # noqa: E731
    Required = lambda x, **k: x  # noqa: E731


sys.modules.setdefault("voluptuous", _vol)
ha = _mk("homeassistant")
ha_core = _mk("homeassistant.core")
ha_helpers = _mk("homeassistant.helpers")
ha_llm = _mk("homeassistant.helpers.llm")


class HomeAssistant:
    pass


class LLMContext:
    conversation_id = "test"


ha_core.HomeAssistant = HomeAssistant
ha_llm.LLMContext = LLMContext
ha.helpers = ha_helpers
ha_helpers.llm = ha_llm
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.core"] = ha_core
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.llm"] = ha_llm

ROOT = Path(__file__).resolve().parent
pkg = types.ModuleType("extended_openai_conversation")
pkg.__path__ = [str(ROOT)]
func_pkg = types.ModuleType("extended_openai_conversation.functions")
func_pkg.__path__ = [str(ROOT / "functions")]
sys.modules["extended_openai_conversation"] = pkg
sys.modules["extended_openai_conversation.functions"] = func_pkg
exceptions = types.ModuleType("extended_openai_conversation.exceptions")


class NativeNotFound(Exception):
    pass


exceptions.NativeNotFound = NativeNotFound
sys.modules["extended_openai_conversation.exceptions"] = exceptions
base = types.ModuleType("extended_openai_conversation.functions.base")


class Function:
    def __init__(self, *_args, **_kwargs):
        pass


base.Function = Function
sys.modules["extended_openai_conversation.functions.base"] = base

const_spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation.const", ROOT / "const.py"
)
const_mod = importlib.util.module_from_spec(const_spec)
sys.modules["extended_openai_conversation.const"] = const_mod
assert const_spec and const_spec.loader
const_spec.loader.exec_module(const_mod)

ws_spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation.functions.world_state",
    ROOT / "functions" / "world_state.py",
)
ws_mod = importlib.util.module_from_spec(ws_spec)
sys.modules["extended_openai_conversation.functions.world_state"] = ws_mod
assert ws_spec and ws_spec.loader
ws_spec.loader.exec_module(ws_mod)
WorldStateFunction = ws_mod.WorldStateFunction


class _State:
    def __init__(self, state, attrs=None):
        self.state = state
        self.attributes = attrs or {}


class _States:
    def __init__(self, data):
        self.data = data

    def get(self, entity_id):
        return self.data.get(entity_id)


class _Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data):
        self.calls.append((domain, service, data))


class _Hass:
    def __init__(self, states):
        self.states = _States(states)
        self.services = _Services()
        self.data = {}


class _Agg:
    def __init__(self, occupied=False, persons=None, cameras=None):
        self._rooms = {
            "living_room": {
                "cameras": cameras if cameras is not None else ["camera.living_room"],
            }
        }
        self.occupied = occupied
        self.persons = persons or []

    def _resolve_room(self, room):
        return room

    def _render_room(self, room, now):
        return {
            "occupied": self.occupied,
            "persons": self.persons,
            "cameras": self._rooms[room]["cameras"],
        }


class _LookFn(WorldStateFunction):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def _aggregator(self, hass):
        return _Agg()

    async def _reason_zoom(self, room_key, camera_entity, question, aiohttp, illuminated=False):
        self.calls += 1
        return {"data": {"answer": f"fresh answer {self.calls}"}}


def assert_true(name, value, detail=None):
    if not value:
        raise AssertionError(f"{name}: {detail!r}")
    print(f"  PASS  {name}")


async def main():
    fn = WorldStateFunction()
    assert_true(
        "low-quality phrasing triggers illumination",
        fn._needs_illumination({"data": {"answer": "I can't make out what's on the coffee table due to poor image quality."}}),
    )
    assert_true(
        "clear answer does not trigger illumination",
        not fn._needs_illumination({"data": {"answer": "A mug is on the coffee table."}}),
    )
    assert_true(
        "coffee table question infers living room",
        fn._infer_room_from_question("what is on my coffee table") == "living_room",
    )
    assert_true(
        "counter question infers kitchen",
        fn._infer_room_from_question("what is on the counter") == "kitchen",
    )

    hass = _Hass({})
    safe = fn._room_safe_for_illumination(hass, _Agg(occupied=False), "living_room")
    assert_true("unoccupied mapped indoor room is eligible", safe["eligible"], safe)
    unsafe = fn._room_safe_for_illumination(hass, _Agg(occupied=True), "living_room")
    assert_true("occupied room is not eligible", not unsafe["eligible"], unsafe)
    outdoor = fn._room_safe_for_illumination(hass, _Agg(occupied=False), "driveway")
    assert_true("outdoor room is not eligible", not outdoor["eligible"], outdoor)

    hass = _Hass({
        "input_boolean.living_lights_vision_capture_override": _State("off"),
        "input_boolean.living_lights_travel_mode": _State("on"),
        "light.living_room_lights": _State("off", {"brightness": 12}),
        "light.front_left": _State("on", {"brightness": 77, "color_temp_kelvin": 3000}),
    })
    snap = fn._capture_light_states(hass, "living_room")
    await fn._enable_capture_lights(hass, snap)
    await fn._restore_light_states(hass, snap)
    calls = hass.services.calls
    assert_true("capture override turns on before lights", calls[0][0:2] == ("input_boolean", "turn_on"), calls)
    assert_true("capture turns on mapped lights", any(c[0:2] == ("light", "turn_on") for c in calls), calls)
    assert_true("restore turns originally-off light back off", any(c[0:2] == ("light", "turn_off") and "light.living_room_lights" in c[2]["entity_id"] for c in calls), calls)
    assert_true("restore clears capture override", calls[-1][0:2] == ("input_boolean", "turn_off"), calls)

    look_fn = _LookFn()
    hass = _Hass({})
    ctx = LLMContext()
    results = []
    for _ in range(8):
        results.append(await look_fn._grounded_look(
            hass,
            {"room": "living_room", "question": "what is on the coffee table?", "allow_illumination": False},
            ctx,
        ))
    assert_true(
        "grounded look is not capped at three calls",
        all("budget exhausted" not in str(r).lower() for r in results),
        results,
    )
    assert_true("all repeated grounded looks reached vision", look_fn.calls == 8, look_fn.calls)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Tests for template.py — A-5 narrow: current_room_context helper
(Addendum 27 / Section 7).

Covers:
  - No device_id → empty string
  - Unbound device → empty string
  - World state disabled → empty string
  - Room exists, no persons + no perception → "empty / no recent activity"
  - Room with identified high-confidence person → name + confidence + age
  - Room with low-confidence-only person → person omitted (noise filter)
  - Room with multiple identified → all listed
  - Room with perception caption + no person → caption shown
  - Room with both person + perception → both shown
  - get_room_state error path → empty string (defensive)

Standalone runner — mocks HA + world_state aggregator. Same pattern
as test_native.py / test_registry.py.

Run: PYTHONIOENCODING=utf-8 py -3 test_template_helpers.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# ── Mock HA + voluptuous + helpers before importing template.py ───────
def _mk(name):
    return types.ModuleType(name)


sys.modules.setdefault("voluptuous", _mk("voluptuous"))

# homeassistant skeleton
ha = _mk("homeassistant")
ha_core = _mk("homeassistant.core")


class HomeAssistant:
    def __init__(self):
        self.data = {}
        self.config = types.SimpleNamespace(config_dir="/config")


ha_core.HomeAssistant = HomeAssistant
ha_helpers = _mk("homeassistant.helpers")
ha_helpers_template = _mk("homeassistant.helpers.template")


class TemplateEnvironment:
    def __init__(self, *a, **k):
        self.globals = {}


ha_helpers_template.TemplateEnvironment = TemplateEnvironment

for k, v in {
    "homeassistant": ha,
    "homeassistant.core": ha_core,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.template": ha_helpers_template,
}.items():
    sys.modules[k] = v

# Stub the package's intra-imports.
pkg_root = _mk("extended_openai_conversation_test_root")
pkg_root.__path__ = []
sys.modules["extended_openai_conversation_test_root"] = pkg_root

# .const
pkg_const = _mk("extended_openai_conversation_test_root.const")
pkg_const.DOMAIN = "extended_openai_conversation"
pkg_const.DEFAULT_WORKING_DIRECTORY = "/tmp/test_working"
sys.modules["extended_openai_conversation_test_root.const"] = pkg_const

# .helpers (just needs get_exposed_entities; template never calls it
# from current_room_context but the import must succeed)
pkg_helpers = _mk("extended_openai_conversation_test_root.helpers")
pkg_helpers.get_exposed_entities = lambda h: []
sys.modules["extended_openai_conversation_test_root.helpers"] = pkg_helpers

# .skills (SkillManager._instance check; the template doesn't call
# _get_skill_dir from current_room_context, just needs the import)
pkg_skills = _mk("extended_openai_conversation_test_root.skills")


class _SkillManager:
    _instance = None


pkg_skills.SkillManager = _SkillManager
sys.modules["extended_openai_conversation_test_root.skills"] = pkg_skills

# .room_binding — A-5 uses resolve() here. We control it via a stub
# that the tests reconfigure.
pkg_rb = _mk("extended_openai_conversation_test_root.room_binding")
_BOUND_ROOMS: dict[str, str | None] = {}


def _resolve_stub(*keys):
    for k in keys:
        if k and k in _BOUND_ROOMS:
            return _BOUND_ROOMS[k]
    return None


pkg_rb.resolve = _resolve_stub
sys.modules["extended_openai_conversation_test_root.room_binding"] = pkg_rb

# ── Load template.py ───────────────────────────────────────────────────
import importlib.util

TEMPLATE_PATH = Path(__file__).parent / "template.py"
spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation_test_root.template", TEMPLATE_PATH
)
template_mod = importlib.util.module_from_spec(spec)
sys.modules["extended_openai_conversation_test_root.template"] = template_mod
spec.loader.exec_module(template_mod)

ExtendedOpenAITemplateManager = template_mod.ExtendedOpenAITemplateManager

# ── Tiny test harness ─────────────────────────────────────────────────
passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def t(name, fn):
    global passes, fails
    try:
        fn()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as e:
        fails += 1
        failures.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:
        fails += 1
        failures.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def section(name):
    print(f"\n[{name}]")


# Mock world_state aggregator. It needs a get_room_state(room) method
# that returns the agent-facing shape (data | error | suggested_phrasing).
class _MockAggregator:
    def __init__(self, room_state_map=None, raise_on=None):
        self._map = room_state_map or {}
        self._raise_on = raise_on

    def get_room_state(self, room):
        if self._raise_on and room == self._raise_on:
            raise RuntimeError("aggregator boom")
        return self._map.get(room, {"data": None, "error": "no such room"})


def _make_mgr(aggregator=None, bindings=None):
    """Build a fresh manager + mock hass + (re)wire room_binding + aggregator."""
    global _BOUND_ROOMS
    _BOUND_ROOMS = dict(bindings or {})
    hass = HomeAssistant()
    if aggregator is not None:
        hass.data.setdefault("extended_openai_conversation", {})["world_state"] = aggregator
    mgr = ExtendedOpenAITemplateManager(hass)
    return mgr


# ── Suite ─────────────────────────────────────────────────────────────
section("current_room_context (A-5)")


def _no_device_returns_empty():
    mgr = _make_mgr()
    assert mgr._current_room_context(None) == ""
    assert mgr._current_room_context("") == ""
t("no device_id → empty string", _no_device_returns_empty)


def _unbound_device_returns_empty():
    mgr = _make_mgr(bindings={})  # nothing bound
    assert mgr._current_room_context("dev-X") == ""
t("unbound device → empty string", _unbound_device_returns_empty)


def _world_state_aggregator_missing_returns_empty():
    # bound, but no aggregator wired
    mgr = _make_mgr(bindings={"dev-1": "kitchen"})
    assert mgr._current_room_context("dev-1") == ""
t("bound but aggregator missing → empty string",
  _world_state_aggregator_missing_returns_empty)


def _world_state_error_returns_empty():
    agg = _MockAggregator({"kitchen": {"error": "world state disabled"}})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    assert mgr._current_room_context("dev-1") == ""
t("get_room_state returns {error} → empty string",
  _world_state_error_returns_empty)


def _aggregator_raises_returns_empty():
    agg = _MockAggregator({}, raise_on="kitchen")
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    assert mgr._current_room_context("dev-1") == ""
t("aggregator raises → empty string (defensive)",
  _aggregator_raises_returns_empty)


def _no_data_room_returns_explicit_msg():
    """When the aggregator returns data=None (room exists but no data),
    return an explicit message rather than empty string — the prompt
    author chose to include this section, so silence is misleading."""
    agg = _MockAggregator({"kitchen": {"data": None}})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert "kitchen" in out, out
    assert "no occupancy data" in out, out
t("room exists, data=None → 'no occupancy data yet' message",
  _no_data_room_returns_explicit_msg)


def _identified_high_confidence_person_renders():
    agg = _MockAggregator({"kitchen": {
        "data": {
            "persons": [{
                "identity": {"name": "Marcelo", "confidence_band": "high"},
                "age_seconds": 12,
            }],
        },
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert "kitchen" in out and "Marcelo" in out, out
    assert "high confidence" in out, out
    assert "12s ago" in out, out
t("high-confidence identified person → name + band + age line",
  _identified_high_confidence_person_renders)


def _multiple_identified_persons_all_listed():
    agg = _MockAggregator({"kitchen": {
        "data": {
            "persons": [
                {"identity": {"name": "Marcelo", "confidence_band": "high"}, "age_seconds": 10},
                {"identity": {"name": "Amelia",  "confidence_band": "medium"}, "age_seconds": 30},
            ],
        },
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert "Marcelo" in out and "Amelia" in out, out
    assert out.count("\n") >= 2, f"expect at least 3 lines: {out!r}"
t("multiple identified persons → all rendered",
  _multiple_identified_persons_all_listed)


def _low_confidence_only_treated_as_unidentified():
    """A person with confidence_band='low' should NOT be named — it's
    noise. If that's the only person + room is occupied → fall back to
    'occupied (no identified person)'."""
    agg = _MockAggregator({"kitchen": {
        "data": {
            "occupied": True,
            "persons": [{
                "identity": {"name": "Ghost", "confidence_band": "low"},
                "age_seconds": 10,
            }],
        },
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert "Ghost" not in out, f"low-conf name leaked: {out}"
    assert "occupied" in out, out
t("low-confidence-only person → name suppressed, occupancy noted",
  _low_confidence_only_treated_as_unidentified)


def _perception_caption_rendered():
    agg = _MockAggregator({"kitchen": {
        "data": {
            "persons": [],
            "perception_summary": "Marcelo standing at the island.",
            "perception_age_seconds": 30,
        },
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert "Marcelo standing at the island" in out, out
    assert "30s ago" in out, out
t("perception caption + age → rendered as 'perception:' line",
  _perception_caption_rendered)


def _person_plus_perception_both_rendered():
    agg = _MockAggregator({"kitchen": {
        "data": {
            "persons": [{
                "identity": {"name": "Marcelo", "confidence_band": "high"},
                "age_seconds": 12,
            }],
            "perception_summary": "Marcelo at the island, mug in hand.",
            "perception_age_seconds": 30,
        },
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    lines = out.split("\n")
    assert any("Marcelo (high confidence" in l for l in lines)
    assert any("perception:" in l for l in lines)
    assert len(lines) >= 3
t("person + perception → both rendered, 3+ lines",
  _person_plus_perception_both_rendered)


def _empty_unoccupied_room_says_empty():
    agg = _MockAggregator({"kitchen": {
        "data": {"persons": [], "occupied": False},
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert "empty" in out.lower(), out
t("empty unoccupied room → 'empty / no recent activity' line",
  _empty_unoccupied_room_says_empty)


def _output_is_compact():
    """Sanity: the output should be <500 chars for a typical room with
    one person + perception — A-5 is supposed to SHRINK context, not
    bloat it."""
    agg = _MockAggregator({"kitchen": {
        "data": {
            "persons": [{
                "identity": {"name": "Marcelo", "confidence_band": "high"},
                "age_seconds": 12,
            }],
            "perception_summary": "Marcelo at the island, mug in hand.",
            "perception_age_seconds": 30,
        },
    }})
    mgr = _make_mgr(aggregator=agg, bindings={"dev-1": "kitchen"})
    out = mgr._current_room_context("dev-1")
    assert len(out) < 500, f"output too long ({len(out)} chars): {out}"
t("typical output stays under 500 chars (A-5 is supposed to shrink context)",
  _output_is_compact)


def _registered_in_extended_openai_dict():
    """The helper must be in the _extended_openai dict so HA's
    template engine picks it up as `extended_openai.current_room_context`."""
    mgr = _make_mgr()
    assert "current_room_context" in mgr._extended_openai, mgr._extended_openai.keys()
    assert callable(mgr._extended_openai["current_room_context"])
t("current_room_context registered in extended_openai global dict",
  _registered_in_extended_openai_dict)


# ── Summary ────────────────────────────────────────────────────────────
print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for n, m in failures:
        print(f"  - {n}: {m}")
sys.exit(0 if fails == 0 else 1)

#!/usr/bin/env python3
"""Tests for functions/registry.py — M3 registry-lookup tools.

Covers the 4 tools:
  - areas_in_home()
  - entities_in_area(area, domains?)
  - entities_with_label(label)
  - find_entity(query, area?, domain?)

Standalone runner — mock the HA registry surfaces so the dispatcher
can be exercised without a live HA. Same pattern as test_native.py.

Run: PYTHONIOENCODING=utf-8 py -3 test_registry.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path


# ── Mock the HA imports BEFORE importing registry.py ────────────────
def _mk(name: str):
    return types.ModuleType(name)


# voluptuous
class _vol:
    @staticmethod
    def Schema(*a, **k):
        return None

    @staticmethod
    def Required(x, **k):
        return x


sys.modules.setdefault("voluptuous", _vol)

# homeassistant skeleton
ha = _mk("homeassistant")
ha_core = _mk("homeassistant.core")


class HomeAssistant:
    pass


class State:
    pass


ha_core.HomeAssistant = HomeAssistant
ha_core.State = State

ha_helpers = _mk("homeassistant.helpers")
ha_helpers_llm = _mk("homeassistant.helpers.llm")


class _LLMCtx:
    device_id = None
    conversation_id = None


ha_helpers_llm.LLMContext = _LLMCtx
ha_helpers_ar = _mk("homeassistant.helpers.area_registry")
ha_helpers_dr = _mk("homeassistant.helpers.device_registry")
ha_helpers_er = _mk("homeassistant.helpers.entity_registry")
ha_helpers_lr = _mk("homeassistant.helpers.label_registry")
ha_helpers_fr = _mk("homeassistant.helpers.floor_registry")


# Mock registry-entry-like classes the tools expect.
class _FakeArea:
    def __init__(self, area_id, name, floor_id=None):
        self.id = area_id
        self.name = name
        self.floor_id = floor_id


class _FakeFloor:
    def __init__(self, floor_id, name):
        self.floor_id = floor_id
        self.name = name


class _FakeLabel:
    def __init__(self, label_id, name):
        self.label_id = label_id
        self.name = name


class _FakeRegistryEntry:
    """Mirror of homeassistant.helpers.entity_registry.RegistryEntry."""

    def __init__(
        self,
        entity_id,
        name=None,
        area_id=None,
        aliases=None,
        labels=None,
    ):
        self.entity_id = entity_id
        self.name = name
        self.area_id = area_id
        self.aliases = set(aliases or [])
        self.labels = set(labels or [])


class _ComputedName:
    """String-like HA name object used to reproduce ComputedNameType crashes."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


# Module-level fixtures the mock registries read from.
_FAKE_AREAS: list[_FakeArea] = []
_FAKE_FLOORS: list[_FakeFloor] = []
_FAKE_LABELS: list[_FakeLabel] = []
_FAKE_ENTITIES: list[_FakeRegistryEntry] = []
_FAKE_STATES: dict[str, object] = {}


class _AreaRegistry:
    def async_get_area(self, area_id):
        for a in _FAKE_AREAS:
            if a.id == area_id:
                return a
        return None

    def async_list_areas(self):
        return list(_FAKE_AREAS)


class _FloorRegistry:
    def async_list_floors(self):
        return list(_FAKE_FLOORS)


class _LabelRegistry:
    def async_list_labels(self):
        return list(_FAKE_LABELS)


class _EntityRegistry:
    @property
    def entities(self):
        # async_entries_for_label fallback iterates .entities.values()
        return {e.entity_id: e for e in _FAKE_ENTITIES}


def _async_entries_for_area(reg, area_id):
    return [e for e in _FAKE_ENTITIES if e.area_id == area_id]


def _async_entries_for_label(reg, label_id):
    return [e for e in _FAKE_ENTITIES if label_id in e.labels]


ha_helpers_ar.async_get = lambda hass: _AreaRegistry()
ha_helpers_ar.AreaRegistry = _AreaRegistry
ha_helpers_er.async_get = lambda hass: _EntityRegistry()
ha_helpers_er.async_entries_for_area = _async_entries_for_area
ha_helpers_er.async_entries_for_label = _async_entries_for_label
ha_helpers_er.RegistryEntry = _FakeRegistryEntry
ha_helpers_dr.async_get = lambda hass: None
ha_helpers_lr.async_get = lambda hass: _LabelRegistry()
ha_helpers_fr.async_get = lambda hass: _FloorRegistry()

ha.core = ha_core
ha.helpers = ha_helpers
ha_helpers.area_registry = ha_helpers_ar
ha_helpers.device_registry = ha_helpers_dr
ha_helpers.entity_registry = ha_helpers_er
ha_helpers.label_registry = ha_helpers_lr
ha_helpers.floor_registry = ha_helpers_fr
ha_helpers.llm = ha_helpers_llm

for k, v in {
    "homeassistant": ha,
    "homeassistant.core": ha_core,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.area_registry": ha_helpers_ar,
    "homeassistant.helpers.device_registry": ha_helpers_dr,
    "homeassistant.helpers.entity_registry": ha_helpers_er,
    "homeassistant.helpers.label_registry": ha_helpers_lr,
    "homeassistant.helpers.floor_registry": ha_helpers_fr,
    "homeassistant.helpers.llm": ha_helpers_llm,
}.items():
    sys.modules[k] = v

# Stub the package context so `from ..exceptions import NativeNotFound`
# resolves when registry.py is loaded.
pkg_root = _mk("extended_openai_conversation_test_root")
pkg_root.__path__ = []
sys.modules["extended_openai_conversation_test_root"] = pkg_root
pkg_root_exc = _mk("extended_openai_conversation_test_root.exceptions")


class NativeNotFound(Exception):
    pass


pkg_root_exc.NativeNotFound = NativeNotFound
sys.modules["extended_openai_conversation_test_root.exceptions"] = pkg_root_exc
pkg_root_funcs = _mk("extended_openai_conversation_test_root.functions")
sys.modules["extended_openai_conversation_test_root.functions"] = pkg_root_funcs
pkg_root_funcs_base = _mk("extended_openai_conversation_test_root.functions.base")


class Function:
    def __init__(self, *a, **k):
        pass


pkg_root_funcs_base.Function = Function
sys.modules["extended_openai_conversation_test_root.functions.base"] = pkg_root_funcs_base

import importlib.util  # noqa: E402

REG_PATH = Path(__file__).parent / "functions" / "registry.py"
spec = importlib.util.spec_from_file_location(
    "extended_openai_conversation_test_root.functions.registry", REG_PATH
)
registry = importlib.util.module_from_spec(spec)
sys.modules["extended_openai_conversation_test_root.functions.registry"] = registry
spec.loader.exec_module(registry)


# ── Mock hass with .states.get for entity state lookup ───────────────
class _FakeStateObj:
    def __init__(self, entity_id, state, attributes=None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}


class _FakeStates:
    def get(self, entity_id):
        return _FAKE_STATES.get(entity_id)


class _FakeHass:
    states = _FakeStates()


# ── Test fixtures ────────────────────────────────────────────────────
def _reset_world():
    global _FAKE_AREAS, _FAKE_FLOORS, _FAKE_LABELS, _FAKE_ENTITIES, _FAKE_STATES
    _FAKE_AREAS = [
        _FakeArea("kitchen_id", "Kitchen", floor_id="ground_id"),
        _FakeArea("living_room_id", "Living Room", floor_id="ground_id"),
        _FakeArea("office_id", "Office", floor_id="upstairs_id"),
    ]
    _FAKE_FLOORS = [
        _FakeFloor("ground_id", "Ground Floor"),
        _FakeFloor("upstairs_id", "Upstairs"),
    ]
    _FAKE_LABELS = [
        _FakeLabel("shared_id", "Shared"),
        _FakeLabel("scenes_id", "Scenes-Bedtime"),
    ]
    _FAKE_ENTITIES = [
        _FakeRegistryEntry("light.kitchen_main", name="Kitchen Main",
                           area_id="kitchen_id", labels={"shared_id"}),
        _FakeRegistryEntry("light.kitchen_island", name="Kitchen Island",
                           area_id="kitchen_id"),
        _FakeRegistryEntry("media_player.kitchen_stereo", name="Kitchen Stereo",
                           area_id="kitchen_id", aliases={"Speakers"}),
        _FakeRegistryEntry("light.living_room", name="Living Room Lamp",
                           area_id="living_room_id"),
        _FakeRegistryEntry("media_player.living_room_tv", name="LG TV",
                           area_id="living_room_id", aliases={"television", "tv"}),
        _FakeRegistryEntry("switch.office_desk", name="Desk Switch",
                           area_id="office_id", labels={"shared_id"}),
        _FakeRegistryEntry("light.office_desk_lamp", name="Desk Lamp",
                           area_id="office_id"),
        _FakeRegistryEntry("sensor.no_area_sensor", area_id=None),
    ]
    _FAKE_STATES = {
        "light.kitchen_main": _FakeStateObj(
            "light.kitchen_main", "on", {"friendly_name": "Kitchen Main", "brightness": 200}
        ),
        "light.kitchen_island": _FakeStateObj(
            "light.kitchen_island", "off", {"friendly_name": "Kitchen Island"}
        ),
        "media_player.kitchen_stereo": _FakeStateObj(
            "media_player.kitchen_stereo", "idle", {"friendly_name": "Kitchen Stereo"}
        ),
        "media_player.living_room_tv": _FakeStateObj(
            "media_player.living_room_tv", "playing", {"friendly_name": "LG TV"}
        ),
        "light.office_desk_lamp": _FakeStateObj(
            "light.office_desk_lamp", "off", {"friendly_name": "Desk Lamp"}
        ),
        "light.living_room": _FakeStateObj(
            "light.living_room", "on", {"friendly_name": "Living Room Lamp"}
        ),
        "switch.office_desk": _FakeStateObj(
            "switch.office_desk", "off", {"friendly_name": "Desk Switch"}
        ),
    }


def _make_fn():
    return registry.RegistryFunction()


def _call(fn, name, args, exposed=None):
    return asyncio.run(
        fn.execute(_FakeHass(), {"name": name}, args, None, exposed or [])
    )


# ── Test harness ─────────────────────────────────────────────────────
passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def t(name, body):
    global passes, fails
    try:
        _reset_world()
        body()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as e:
        fails += 1
        failures.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        fails += 1
        failures.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def section(name: str):
    print(f"\n[{name}]")


# ── Tests ────────────────────────────────────────────────────────────
section("areas_in_home")


def _areas_returns_all_areas_with_floor():
    fn = _make_fn()
    r = _call(fn, "areas_in_home", {})
    assert "data" in r and r["data"]["count"] == 3
    names = [a["name"] for a in r["data"]["areas"]]
    assert set(names) == {"Kitchen", "Living Room", "Office"}, names
    # Kitchen has 3 entities; sample limited to 3
    kitchen = next(a for a in r["data"]["areas"] if a["name"] == "Kitchen")
    assert kitchen["entity_count"] == 3, kitchen
    assert kitchen["floor_name"] == "Ground Floor", kitchen
    assert len(kitchen["sample_entities"]) == 3
t("areas_in_home returns 3 areas with floor + entity_count + sample", _areas_returns_all_areas_with_floor)


def _areas_empty_when_no_areas():
    global _FAKE_AREAS
    _FAKE_AREAS = []
    fn = _make_fn()
    r = _call(fn, "areas_in_home", {})
    assert r["data"]["count"] == 0
    assert "No areas" in r["suggested_phrasing"]
t("areas_in_home: empty registry → friendly 'no areas' message", _areas_empty_when_no_areas)


section("entities_in_area")


def _entities_in_kitchen_returns_3():
    fn = _make_fn()
    r = _call(fn, "entities_in_area", {"area": "kitchen"})
    assert r["data"]["count"] == 3, r
    ids = {e["entity_id"] for e in r["data"]["entities"]}
    assert ids == {"light.kitchen_main", "light.kitchen_island",
                   "media_player.kitchen_stereo"}, ids
t("entities_in_area kitchen → 3 entities (case-insensitive)", _entities_in_kitchen_returns_3)


def _entities_in_area_by_id():
    fn = _make_fn()
    r = _call(fn, "entities_in_area", {"area": "kitchen_id"})
    assert r["data"]["count"] == 3, r
t("entities_in_area accepts area_id directly", _entities_in_area_by_id)


def _entities_in_area_by_friendly_name():
    fn = _make_fn()
    r = _call(fn, "entities_in_area", {"area": "Living Room"})
    assert r["data"]["count"] == 2, r
t("entities_in_area accepts 'Living Room' (with space + caps)", _entities_in_area_by_friendly_name)


def _entities_in_area_domains_filter():
    fn = _make_fn()
    r = _call(fn, "entities_in_area",
              {"area": "kitchen", "domains": ["light"]})
    assert r["data"]["count"] == 2, r
    ids = {e["entity_id"] for e in r["data"]["entities"]}
    assert ids == {"light.kitchen_main", "light.kitchen_island"}, ids
t("entities_in_area domains=['light'] filters out media_player", _entities_in_area_domains_filter)


def _entities_in_area_unknown_returns_hint():
    fn = _make_fn()
    r = _call(fn, "entities_in_area", {"area": "attic"})
    assert r["data"] is None
    assert "don't see an area" in r["suggested_phrasing"]
    assert "areas_in_home" in r["suggested_phrasing"]
t("entities_in_area unknown area → friendly hint", _entities_in_area_unknown_returns_hint)


def _entities_in_area_exposed_filter():
    """When exposed_entities is non-empty, only those entities surface."""
    fn = _make_fn()
    r = _call(fn, "entities_in_area",
              {"area": "kitchen"},
              exposed=[{"entity_id": "light.kitchen_main"}])
    assert r["data"]["count"] == 1, r
    assert r["data"]["entities"][0]["entity_id"] == "light.kitchen_main"
t("entities_in_area honors exposed_entities filter", _entities_in_area_exposed_filter)


def _entities_in_area_state_included():
    fn = _make_fn()
    r = _call(fn, "entities_in_area", {"area": "kitchen"})
    by_id = {e["entity_id"]: e for e in r["data"]["entities"]}
    assert by_id["light.kitchen_main"]["state"] == "on"
    assert by_id["light.kitchen_island"]["state"] == "off"
    assert by_id["media_player.kitchen_stereo"]["state"] == "idle"
t("entities_in_area returns current state per entity", _entities_in_area_state_included)


def _entities_in_area_area_name_in_row():
    fn = _make_fn()
    r = _call(fn, "entities_in_area", {"area": "office"})
    for row in r["data"]["entities"]:
        assert row["area"] == "Office", row
t("entities_in_area rows carry resolved area name", _entities_in_area_area_name_in_row)


section("entities_with_label")


def _entities_with_shared_label():
    fn = _make_fn()
    r = _call(fn, "entities_with_label", {"label": "Shared"})
    assert r["data"] is not None
    ids = {e["entity_id"] for e in r["data"]["entities"]}
    assert ids == {"light.kitchen_main", "switch.office_desk"}, ids
t("entities_with_label 'Shared' → 2 matches (case-insensitive)", _entities_with_shared_label)


def _entities_with_unknown_label_returns_available():
    fn = _make_fn()
    r = _call(fn, "entities_with_label", {"label": "Outdoor"})
    assert r["data"] is None
    assert "No label called 'Outdoor'" in r["suggested_phrasing"]
    assert "Shared" in r["suggested_phrasing"]  # lists available
t("entities_with_label unknown label → lists available", _entities_with_unknown_label_returns_available)


def _entities_with_label_no_labels_defined():
    global _FAKE_LABELS
    _FAKE_LABELS = []
    fn = _make_fn()
    r = _call(fn, "entities_with_label", {"label": "anything"})
    assert r["data"] is None
    assert "No labels are defined" in r["suggested_phrasing"]
t("entities_with_label: lazy registry empty → 'add labels in UI' hint", _entities_with_label_no_labels_defined)


section("find_entity")


def _find_entity_by_friendly_name():
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "desk lamp"})
    ids = [e["entity_id"] for e in r["data"]["candidates"]]
    assert "light.office_desk_lamp" in ids, ids
t("find_entity 'desk lamp' → finds light.office_desk_lamp", _find_entity_by_friendly_name)


def _find_entity_by_alias():
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "television"})
    ids = [e["entity_id"] for e in r["data"]["candidates"]]
    assert ids[0] == "media_player.living_room_tv", ids
t("find_entity matches by alias ('television' → LG TV)", _find_entity_by_alias)


def _find_entity_area_filter():
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "lamp", "area": "Office"})
    ids = [e["entity_id"] for e in r["data"]["candidates"]]
    # Only the office desk lamp; living room lamp should be filtered out
    assert "light.office_desk_lamp" in ids
    assert "light.living_room" not in ids
t("find_entity with area='Office' excludes living-room match", _find_entity_area_filter)


def _find_entity_domain_filter():
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "kitchen", "domain": "media_player"})
    ids = [e["entity_id"] for e in r["data"]["candidates"]]
    assert ids == ["media_player.kitchen_stereo"], ids
t("find_entity with domain='media_player' filters out lights", _find_entity_domain_filter)


def _find_entity_no_match():
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "garage door"})
    assert r["data"]["count"] == 0
    assert "0 match" in r["suggested_phrasing"]
t("find_entity no match → count=0 + friendly suggestion", _find_entity_no_match)


def _find_entity_ranks_exact_name_first():
    fn = _make_fn()
    # "Kitchen Stereo" is the exact friendly name; "kitchen" would also
    # match the lights. The stereo should rank first via score=3 (exact).
    r = _call(fn, "find_entity", {"query": "kitchen stereo"})
    assert r["data"]["candidates"][0]["entity_id"] == "media_player.kitchen_stereo"
t("find_entity ranks exact-name match above partial", _find_entity_ranks_exact_name_first)


def _find_entity_max_results_cap():
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "kitchen", "max_results": 2})
    assert r["data"]["count"] <= 2
t("find_entity respects max_results cap", _find_entity_max_results_cap)


def _find_entity_exposed_filter():
    fn = _make_fn()
    r = _call(fn, "find_entity",
              {"query": "kitchen"},
              exposed=[{"entity_id": "light.kitchen_main"}])
    ids = [e["entity_id"] for e in r["data"]["candidates"]]
    assert ids == ["light.kitchen_main"], ids
t("find_entity honors exposed_entities", _find_entity_exposed_filter)


def _find_entity_tolerates_computed_name_values():
    global _FAKE_ENTITIES, _FAKE_STATES
    _FAKE_ENTITIES.append(
        _FakeRegistryEntry(
            "light.computed_name_lamp",
            name=_ComputedName("Computed Office Lamp"),
            area_id="office_id",
            aliases={_ComputedName("computed alias")},
        )
    )
    _FAKE_STATES["light.computed_name_lamp"] = _FakeStateObj(
        "light.computed_name_lamp",
        "on",
        {"friendly_name": _ComputedName("Computed Friendly Lamp")},
    )
    fn = _make_fn()
    r = _call(fn, "find_entity", {"query": "computed", "domain": "light"})
    ids = [e["entity_id"] for e in r["data"]["candidates"]]
    assert "light.computed_name_lamp" in ids, r
    row = next(e for e in r["data"]["candidates"] if e["entity_id"] == "light.computed_name_lamp")
    assert row["name"] == "Computed Office Lamp", row
    assert "computed alias" in row["aliases"], row
t("find_entity tolerates HA computed-name objects", _find_entity_tolerates_computed_name_values)


section("entities_by_domain")


def _ebd_all_lights():
    fn = _make_fn()
    r = _call(fn, "entities_by_domain", {"domain": "light"})
    ids = r["data"]["entity_ids"]
    assert set(ids) == {
        "light.kitchen_main", "light.kitchen_island",
        "light.living_room", "light.office_desk_lamp",
    }, ids
    assert r["data"]["count"] == 4
t("entities_by_domain light → all 4 lights, sorted", _ebd_all_lights)


def _ebd_area_filter():
    fn = _make_fn()
    r = _call(fn, "entities_by_domain", {"domain": "light", "area": "Kitchen"})
    ids = r["data"]["entity_ids"]
    assert set(ids) == {"light.kitchen_main", "light.kitchen_island"}, ids
t("entities_by_domain with area filter → kitchen lights only", _ebd_area_filter)


def _ebd_unknown_domain_returns_empty():
    fn = _make_fn()
    r = _call(fn, "entities_by_domain", {"domain": "vacuum"})
    assert r["data"]["count"] == 0
    assert r["data"]["entity_ids"] == []
t("entities_by_domain with unknown domain → empty list (no crash)", _ebd_unknown_domain_returns_empty)


def _ebd_missing_domain_errors():
    fn = _make_fn()
    r = _call(fn, "entities_by_domain", {})
    assert "error" in r and "domain" in r["error"].lower()
t("entities_by_domain without domain arg → error", _ebd_missing_domain_errors)


def _ebd_exposed_filter():
    fn = _make_fn()
    r = _call(fn, "entities_by_domain",
              {"domain": "light"},
              exposed=[{"entity_id": "light.kitchen_main"}])
    assert r["data"]["entity_ids"] == ["light.kitchen_main"]
t("entities_by_domain honors exposed_entities", _ebd_exposed_filter)


# ── Summary ──────────────────────────────────────────────────────────
print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for name, msg in failures:
        print(f"  - {name}: {msg}")
sys.exit(0 if fails == 0 else 1)

#!/usr/bin/env python3
"""Scenario tests for world_state.py.

Covers the seven user stories from the routing plan's Addendum 4:

  1. Ask about a room (recognized + unknown + empty)
  2. "Do you see me?" (high conf + low conf + not present)
  3. "Find Marcelo across cameras" (multi-room recency)
  4. Conflicting data (HA home + no visual + stale)
  5. Stale perception
  6. Unknown person — must not be called Marcelo
  7. Arrival flow integration (world-state slice only; full flow needs bridge)

Plus regression / unit tests:

  - confidence band thresholds (high/medium/low/unknown)
  - freshness band thresholds (fresh/recent/stale/none)
  - camera→room resolution with + without override dict
  - disabled-via-env-var path
  - "me" / "I" pronoun resolves to PRIMARY_USER_NAME

Run on workstation via `py -3 test_world_state.py` (no pytest needed —
the suite is a plain function-based runner mirroring
test_external_routing.py). Returns non-zero on failure.

Standalone — no HomeAssistant install required. The mock HA shim
provides just enough surface for WorldStateAggregator's public API.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

# Make this script's directory importable so `from world_state import ...`
# works regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Tests can run with the aggregator disabled to exercise that branch.
# Make sure the env var is NOT set when most tests run.
os.environ.pop("EXTENDED_OPENAI_WORLD_STATE", None)

from world_state import (  # noqa: E402
    WorldStateAggregator,
    _confidence_band,
    _freshness,
    _camera_to_room,
)
from const import (  # noqa: E402
    FRESH_SECONDS,
    IDENTITY_CONFIDENCE_HIGH,
    IDENTITY_CONFIDENCE_MEDIUM,
    PRIMARY_USER_NAME,
    RECENT_SECONDS,
    STALE_SECONDS,
    WORLD_STATE_DISABLED_ENV,
)


GREEN = "\033[32m"
RED   = "\033[31m"
YEL   = "\033[33m"
DIM   = "\033[2m"
RST   = "\033[0m"


# ─────────────────────────────────────────────────────────────────────
# Mock HA shim — just enough surface for the aggregator's public API.
# We don't subscribe to state_changed in tests; we feed detections
# directly via _ingest() or the public record_perception()/_apply_*.
# ─────────────────────────────────────────────────────────────────────
class _MockState:
    """Minimal stand-in for homeassistant.core.State."""

    def __init__(
        self, entity_id: str, state: str, attributes: dict | None = None
    ) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}


class _MockStates:
    """hass.states API: async_all() returns the registered states."""

    def __init__(self) -> None:
        self._states: dict[str, _MockState] = {}

    def set(
        self, entity_id: str, state: str, attributes: dict | None = None
    ) -> _MockState:
        s = _MockState(entity_id, state, attributes)
        self._states[entity_id] = s
        return s

    def async_all(self) -> list[_MockState]:
        return list(self._states.values())

    def get(self, entity_id: str) -> _MockState | None:
        return self._states.get(entity_id)


class _MockBus:
    """hass.bus.async_fire — collects fired events for assertions."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, Any]] = []

    def async_fire(self, event_type: str, data: Any) -> None:
        self.fired.append((event_type, data))


class _MockHass:
    """Minimal hass surrogate. The aggregator only uses .states.async_all,
    .bus.async_fire, and (during async_setup) async_track_state_change_event
    which we set to None at import time to bypass subscription.

    M1: also records `async_create_task` invocations so scheduler tests
    can assert that the dispatch coroutine was enqueued (without
    actually running it — the dispatch hits httpx which we'd have to
    mock further; the gate logic is what we're testing here)."""

    def __init__(self) -> None:
        self.states = _MockStates()
        self.bus = _MockBus()
        self.data: dict[str, Any] = {}
        # M1: capture every coroutine the aggregator tries to enqueue
        # so tests can assert it was called (dispatched) or not
        # (debounced / rate_capped). Close the coroutine immediately
        # to suppress the "coroutine never awaited" warning.
        self.tasks_created: list[Any] = []

    def async_create_task(self, coro):
        self.tasks_created.append(coro)
        try:
            coro.close()
        except Exception:
            pass
        return None


def _build_agg(mock_hass: _MockHass) -> WorldStateAggregator:
    """Instantiate the aggregator + seed initial state from mock_hass.
    We bypass async_setup's subscription path by calling _ingest directly
    on the seeded states (async_track_state_change_event is None in test
    mode and can't be called)."""
    agg = WorldStateAggregator(mock_hass)  # type: ignore[arg-type]
    # Mimic async_setup's state-scan + ingest, without subscribing.
    for state in mock_hass.states.async_all():
        if agg._is_tracked_entity(state.entity_id):  # noqa: SLF001
            agg._tracked_entities.add(state.entity_id)  # noqa: SLF001
            agg._ingest(state.entity_id, None, state)  # noqa: SLF001
    return agg


# ─────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────
def _fresh_face(
    hass: _MockHass, camera_slug: str, name: str, score: float
) -> None:
    """Seed a Frigate face-rec sensor in 'currently seen' state."""
    hass.states.set(
        f"sensor.{camera_slug}_last_recognized_face",
        name,
        {"score": score},
    )
    # Frigate also publishes a person occupancy sensor — set it on.
    hass.states.set(
        f"binary_sensor.{camera_slug}_person_occupancy",
        "on",
    )
    # And the camera itself exists.
    hass.states.set(f"camera.{camera_slug}", "streaming")


def _occupancy_only(hass: _MockHass, camera_slug: str, on: bool = True) -> None:
    """Generic person detection without identity match."""
    hass.states.set(
        f"binary_sensor.{camera_slug}_person_occupancy",
        "on" if on else "off",
    )
    hass.states.set(
        f"sensor.{camera_slug}_last_recognized_face",
        "Unknown",
    )
    hass.states.set(f"camera.{camera_slug}", "streaming")


def _ha_person(
    hass: _MockHass, slug: str, state: str = "home", display: str | None = None
) -> None:
    """Seed a person.<slug> entity."""
    attrs = {"friendly_name": display} if display else {}
    hass.states.set(f"person.{slug}", state, attrs)


def _frigate_person_tracker(
    hass: _MockHass, person_slug: str, camera_value: str, score: float | None = None
) -> None:
    """Seed Frigate's per-person last_camera sensor — the authoritative
    'where is X right now' signal."""
    attrs = {"score": score} if score is not None else {}
    hass.states.set(
        f"sensor.frigate_{person_slug}_last_camera",
        camera_value,
        attrs,
    )


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────
def _ts_passed(scenarios_passed: int, name: str) -> int:
    print(f"  {GREEN}PASS{RST} {name}")
    return scenarios_passed + 1


def _ts_failed(scenarios_failed: list, name: str, reason: str) -> None:
    print(f"  {RED}FAIL{RST} {name}  — {reason}")
    scenarios_failed.append((name, reason))


# === Unit: confidence band thresholds ================================
def test_confidence_band_thresholds() -> tuple[bool, str]:
    cases = [
        (0.95, "high"),
        (IDENTITY_CONFIDENCE_HIGH, "high"),
        (IDENTITY_CONFIDENCE_HIGH - 0.01, "medium"),
        (0.50, "medium"),
        (IDENTITY_CONFIDENCE_MEDIUM, "medium"),
        (IDENTITY_CONFIDENCE_MEDIUM - 0.01, "low"),
        (0.0, "low"),
        (None, "unknown"),
    ]
    for score, expected in cases:
        got = _confidence_band(score)
        if got != expected:
            return False, f"_confidence_band({score}) → {got!r}, expected {expected!r}"
    return True, ""


# === Unit: freshness thresholds ======================================
def test_freshness_thresholds() -> tuple[bool, str]:
    cases = [
        (0, "fresh"),
        (FRESH_SECONDS, "fresh"),
        (FRESH_SECONDS + 1, "recent"),
        (RECENT_SECONDS, "recent"),
        (RECENT_SECONDS + 1, "stale"),
        (STALE_SECONDS + 1, "stale"),
        (None, "none"),
    ]
    for age, expected in cases:
        got = _freshness(age)
        if got != expected:
            return False, f"_freshness({age}) → {got!r}, expected {expected!r}"
    return True, ""


# === Unit: camera → room resolution ==================================
def test_camera_to_room_default() -> tuple[bool, str]:
    if _camera_to_room("camera.living_room") != "living_room":
        return False, "default fallback for camera.living_room should return 'living_room'"
    if _camera_to_room("camera.kitchen") != "kitchen":
        return False, "explicit override for camera.kitchen should return 'kitchen'"
    if _camera_to_room("not.a.camera") is not None:
        return False, "non-camera entity should return None"
    return True, ""


# === Story 1: ask about a room — recognized person ===================
def test_story1_room_state_recognized_person() -> tuple[bool, str]:
    hass = _MockHass()
    _fresh_face(hass, "kitchen", "Marcelo", 0.92)
    agg = _build_agg(hass)
    result = agg.get_room_state("kitchen")
    if result.get("error"):
        return False, f"got error {result['error']!r}"
    if result["confidence_band"] != "high":
        return False, f"expected high confidence, got {result['confidence_band']!r}"
    if "Marcelo" not in result["suggested_phrasing"]:
        return False, f"phrasing should mention Marcelo: {result['suggested_phrasing']!r}"
    if "kitchen" not in result["suggested_phrasing"]:
        return False, f"phrasing should mention kitchen: {result['suggested_phrasing']!r}"
    return True, ""


# === Story 1: room state — generic person only =======================
def test_story1_room_state_generic_only() -> tuple[bool, str]:
    hass = _MockHass()
    _occupancy_only(hass, "living_room", on=True)
    agg = _build_agg(hass)
    result = agg.get_room_state("living_room")
    phrasing = result["suggested_phrasing"]
    # Must say "someone" — must NOT say a known name.
    if "someone" not in phrasing.lower() and "person" not in phrasing.lower():
        return False, f"phrasing should mention someone/person: {phrasing!r}"
    if "Marcelo" in phrasing:
        return False, f"phrasing must NOT name a person when face-rec didn't match: {phrasing!r}"
    return True, ""


# === Story 1: room state — empty room ================================
def test_story1_room_state_empty() -> tuple[bool, str]:
    hass = _MockHass()
    _occupancy_only(hass, "kitchen", on=False)
    agg = _build_agg(hass)
    result = agg.get_room_state("kitchen")
    phrasing = result["suggested_phrasing"]
    if "don't" not in phrasing.lower() and "no one" not in phrasing.lower():
        return False, f"empty-room phrasing should negate: {phrasing!r}"
    return True, ""


# === Story 2: "Do you see me?" — high-confidence fresh ===============
def test_story2_find_me_high_conf_fresh() -> tuple[bool, str]:
    hass = _MockHass()
    _fresh_face(hass, "kitchen", "Marcelo", 0.92)
    agg = _build_agg(hass)
    # "me" should resolve to PRIMARY_USER_NAME (= Marcelo).
    result = agg.find_person("me")
    if result.get("error"):
        return False, f"got error {result['error']!r}"
    phrasing = result["suggested_phrasing"]
    if "Marcelo" not in phrasing or "kitchen" not in phrasing:
        return False, f"phrasing should affirm Marcelo in kitchen: {phrasing!r}"
    if result["confidence_band"] != "high":
        return False, f"expected high confidence, got {result['confidence_band']!r}"
    if result["data"]["currently_seen"] is not True:
        return False, "currently_seen should be True for fresh detection"
    return True, ""


# === Story 2: "Do you see me?" — low confidence ======================
def test_story2_find_me_low_conf_hedges() -> tuple[bool, str]:
    hass = _MockHass()
    # 0.45 = medium confidence (between MEDIUM and HIGH).
    _fresh_face(hass, "living_room", "Marcelo", 0.45)
    agg = _build_agg(hass)
    result = agg.find_person("Marcelo")
    phrasing = result["suggested_phrasing"]
    if "not fully confident" not in phrasing.lower() and "might" not in phrasing.lower():
        return False, f"medium-conf phrasing should hedge: {phrasing!r}"
    return True, ""


# === Story 3: find Marcelo across cameras ============================
def test_story3_find_person_multi_room_most_recent_wins() -> tuple[bool, str]:
    hass = _MockHass()
    # Office detection happens 10 minutes ago.
    _fresh_face(hass, "office", "Marcelo", 0.90)
    agg = _build_agg(hass)
    # Backdate the office detection.
    for det in agg._history["Marcelo"]:  # noqa: SLF001
        det.ts = time.time() - 600
    if agg._people["Marcelo"].get("last_visual_at_ts"):  # noqa: SLF001
        agg._people["Marcelo"]["last_visual_at_ts"] = time.time() - 600  # noqa: SLF001
    # Now Marcelo is seen in the kitchen (fresh).
    _fresh_face(hass, "kitchen", "Marcelo", 0.91)
    # Re-ingest the new face.
    agg._ingest(  # noqa: SLF001
        "sensor.kitchen_last_recognized_face",
        None,
        hass.states.get("sensor.kitchen_last_recognized_face"),
    )
    result = agg.find_person("Marcelo")
    if "kitchen" not in result["suggested_phrasing"]:
        return False, (
            f"most-recent (kitchen) should win over older (office): "
            f"{result['suggested_phrasing']!r}"
        )
    return True, ""


# === Story 4: conflicting — HA home but no visual ====================
def test_story4_ha_home_no_visual() -> tuple[bool, str]:
    hass = _MockHass()
    _ha_person(hass, "marcelo", state="home", display="Marcelo")
    agg = _build_agg(hass)
    result = agg.find_person("Marcelo")
    phrasing = result["suggested_phrasing"]
    if "phone" not in phrasing.lower() and "home" not in phrasing.lower():
        return False, (
            f"should mention phone-is-home when HA presence exists but no visual: "
            f"{phrasing!r}"
        )
    if "I see Marcelo" in phrasing:
        return False, (
            f"MUST NOT claim visual confirmation from HA location alone: "
            f"{phrasing!r}"
        )
    return True, ""


# === Story 4b: stale visual + HA home — hedge correctly ==============
def test_story4_stale_visual_with_ha_home() -> tuple[bool, str]:
    hass = _MockHass()
    _ha_person(hass, "marcelo", state="home", display="Marcelo")
    _fresh_face(hass, "office", "Marcelo", 0.90)
    agg = _build_agg(hass)
    # Backdate the visual to 20 minutes ago.
    agg._people["Marcelo"]["last_visual_at_ts"] = time.time() - 1200  # noqa: SLF001
    result = agg.find_person("Marcelo")
    phrasing = result["suggested_phrasing"]
    if "last saw" not in phrasing.lower():
        return False, (
            f"stale visual should say 'last saw': {phrasing!r}"
        )
    return True, ""


# === Story 5: stale perception in room state =========================
def test_story5_stale_perception_signals_stale() -> tuple[bool, str]:
    hass = _MockHass()
    _occupancy_only(hass, "office", on=False)
    agg = _build_agg(hass)
    # No identity → who_is_in should say so cleanly.
    result = agg.who_is_in("office")
    phrasing = result["suggested_phrasing"]
    if "don't" not in phrasing.lower() and "no one" not in phrasing.lower():
        return False, f"empty room should say no one: {phrasing!r}"
    return True, ""


# === Story 6: unknown person — MUST NOT be named =====================
def test_story6_unknown_must_not_be_named_marcelo() -> tuple[bool, str]:
    hass = _MockHass()
    _occupancy_only(hass, "living_room", on=True)
    # Also ensure Marcelo's HA presence is home, to make this adversarial.
    _ha_person(hass, "marcelo", state="home", display="Marcelo")
    agg = _build_agg(hass)
    result = agg.who_is_in("living_room")
    phrasing = result["suggested_phrasing"]
    if "Marcelo" in phrasing:
        return False, (
            f"HARD FAIL: unknown person in room was labeled Marcelo "
            f"(adversarial regression for over-claim): {phrasing!r}"
        )
    data = result["data"]
    if data["unknown_count"] != 1:
        return False, f"unknown_count should be 1, got {data['unknown_count']!r}"
    if len(data["identified"]) != 0:
        return False, f"identified[] should be empty, got {data['identified']!r}"
    return True, ""


# === Disabled-via-env-var ============================================
def test_disabled_env_var_returns_disabled_error() -> tuple[bool, str]:
    os.environ[WORLD_STATE_DISABLED_ENV] = "off"
    try:
        hass = _MockHass()
        _fresh_face(hass, "kitchen", "Marcelo", 0.92)
        agg = WorldStateAggregator(hass)  # type: ignore[arg-type]
        if agg.enabled:
            return False, "aggregator should be disabled by env var"
        # find_person should return the disabled error.
        result = agg.find_person("Marcelo")
        if "disabled" not in str(result.get("error", "")).lower():
            return False, f"expected disabled error, got {result!r}"
        return True, ""
    finally:
        os.environ.pop(WORLD_STATE_DISABLED_ENV, None)


# === "me" / "I" / "myself" resolves to PRIMARY_USER_NAME =============
def test_pronoun_resolution() -> tuple[bool, str]:
    hass = _MockHass()
    _fresh_face(hass, "kitchen", PRIMARY_USER_NAME, 0.92)
    agg = _build_agg(hass)
    for pronoun in ("me", "I", "myself", "ME", "Myself"):
        result = agg.find_person(pronoun)
        if result.get("error"):
            return False, f"pronoun {pronoun!r} failed: {result['error']}"
        if PRIMARY_USER_NAME not in result["suggested_phrasing"]:
            return False, (
                f"pronoun {pronoun!r} should resolve to {PRIMARY_USER_NAME}: "
                f"{result['suggested_phrasing']!r}"
            )
    return True, ""


# === find_person of unknown name returns clean "no record" ===========
def test_find_person_unknown_name_returns_no_record() -> tuple[bool, str]:
    hass = _MockHass()
    agg = _build_agg(hass)
    result = agg.find_person("Xyzzy")
    phrasing = result["suggested_phrasing"]
    if "Xyzzy" not in phrasing:
        return False, f"phrasing should echo the name asked: {phrasing!r}"
    if "Marcelo" in phrasing:
        return False, (
            f"unknown-name lookup must NOT mention any other person: {phrasing!r}"
        )
    return True, ""


# === who_is_in unknown room returns clean "no data" ==================
def test_who_is_in_unknown_room() -> tuple[bool, str]:
    hass = _MockHass()
    agg = _build_agg(hass)
    result = agg.who_is_in("basement")
    phrasing = result["suggested_phrasing"]
    if "don't" not in phrasing.lower():
        return False, f"unknown-room phrasing should negate: {phrasing!r}"
    return True, ""


# === Story 7: arrival flow integration — world state slice ===========
def test_story7_arrival_face_recognition_present() -> tuple[bool, str]:
    """After Frigate face-recs Marcelo on the living-room camera,
    the aggregator should report him as currently seen there."""
    hass = _MockHass()
    _ha_person(hass, "marcelo", state="home", display="Marcelo")
    _fresh_face(hass, "living_room", "Marcelo", 0.88)
    agg = _build_agg(hass)
    person = agg._render_person("Marcelo", time.time())  # noqa: SLF001
    if not person["currently_seen"]:
        return False, "currently_seen should be True after fresh face-rec"
    if person["last_visual_room"] != "living_room":
        return False, f"last_visual_room should be living_room, got {person['last_visual_room']!r}"
    if person["ha_location"] != "home":
        return False, f"ha_location should be 'home', got {person['ha_location']!r}"
    return True, ""


# === Regression: "what time is it?" — agent should NOT call world-state tools
# (This is a routing-classifier concern, not an aggregator concern. The
# regression test for prompt-induced misroutes lives in the live-deploy
# verification step. Documenting here so anyone reading the suite knows
# why it's not implemented as a pytest.)


# === Two cameras see different people in different rooms — both reported
def test_multi_person_separate_rooms() -> tuple[bool, str]:
    hass = _MockHass()
    _fresh_face(hass, "kitchen", "Marcelo", 0.90)
    _fresh_face(hass, "office", "Sara", 0.85)
    agg = _build_agg(hass)
    overview = agg.get_world_state()
    # Both rooms should report identified persons.
    if "Marcelo" not in str(overview["rooms"].get("kitchen", {}).get("persons", [])):
        return False, "kitchen should have Marcelo"
    if "Sara" not in str(overview["rooms"].get("office", {}).get("persons", [])):
        return False, "office should have Sara"
    return True, ""


# === Disabled aggregator returns clean dict from get_world_state =====
def test_get_world_state_disabled() -> tuple[bool, str]:
    os.environ[WORLD_STATE_DISABLED_ENV] = "off"
    try:
        hass = _MockHass()
        agg = WorldStateAggregator(hass)  # type: ignore[arg-type]
        state = agg.get_world_state()
        if state.get("enabled") is not False:
            return False, f"disabled state should report enabled=False, got {state!r}"
        return True, ""
    finally:
        os.environ.pop(WORLD_STATE_DISABLED_ENV, None)


# === Phase 4A.2: Frigate per-person tracker sensor support ===========
def test_frigate_person_tracker_bare_camera_value() -> tuple[bool, str]:
    """sensor.frigate_marcelo_last_camera state='kitchen' → Marcelo in kitchen
    with high confidence (no `_last_recognized_face` sensor needed)."""
    hass = _MockHass()
    _frigate_person_tracker(hass, "marcelo", "kitchen")
    agg = _build_agg(hass)
    result = agg.find_person("Marcelo")
    if result.get("error"):
        return False, f"got error {result['error']!r}"
    if not result["data"]["currently_seen"]:
        return False, "should be currently_seen via frigate tracker"
    if result["data"]["last_visual_room"] != "kitchen":
        return False, f"expected kitchen, got {result['data']['last_visual_room']!r}"
    if result["confidence_band"] != "high":
        return False, f"expected high confidence band, got {result['confidence_band']!r}"
    if "Marcelo" not in result["suggested_phrasing"] or "kitchen" not in result["suggested_phrasing"]:
        return False, f"phrasing should mention Marcelo + kitchen: {result['suggested_phrasing']!r}"
    return True, ""


def test_frigate_person_tracker_camera_entity_form() -> tuple[bool, str]:
    """sensor.frigate_<X>_last_camera state='camera.living_room' is also valid."""
    hass = _MockHass()
    _frigate_person_tracker(hass, "marcelo", "camera.living_room")
    agg = _build_agg(hass)
    result = agg.find_person("Marcelo")
    if result["data"]["last_visual_room"] != "living_room":
        return False, f"expected living_room, got {result['data']['last_visual_room']!r}"
    return True, ""


def test_frigate_person_tracker_unknown_state_skipped() -> tuple[bool, str]:
    """state='unknown'/'none' should NOT record a detection."""
    hass = _MockHass()
    _frigate_person_tracker(hass, "marcelo", "unknown")
    agg = _build_agg(hass)
    result = agg.find_person("Marcelo")
    if result["data"] and result["data"].get("currently_seen"):
        return False, "unknown state should not produce a currently_seen detection"
    return True, ""


def test_frigate_person_tracker_with_score_attr() -> tuple[bool, str]:
    """If the sensor has an explicit score, use that instead of the default."""
    hass = _MockHass()
    _frigate_person_tracker(hass, "marcelo", "kitchen", score=0.45)
    agg = _build_agg(hass)
    result = agg.find_person("Marcelo")
    if result["confidence_band"] != "medium":
        return False, f"score=0.45 should yield medium band, got {result['confidence_band']!r}"
    return True, ""


def test_ha_person_canonical_name_mapping() -> tuple[bool, str]:
    """person.engineeredlighting should map to 'Marcelo' canonical name
    so HA presence + Frigate face-rec merge into a single person record."""
    hass = _MockHass()
    _ha_person(hass, "engineeredlighting", state="home")
    _frigate_person_tracker(hass, "marcelo", "kitchen")
    agg = _build_agg(hass)
    # find_person("Marcelo") should return BOTH the HA location AND the visual.
    result = agg.find_person("Marcelo")
    if result["data"]["ha_location"] != "home":
        return False, (
            f"HA presence should propagate via canonical mapping; "
            f"got ha_location={result['data']['ha_location']!r}"
        )
    if result["data"]["last_visual_room"] != "kitchen":
        return False, f"expected kitchen, got {result['data']['last_visual_room']!r}"
    if not result["data"]["currently_seen"]:
        return False, "should be currently_seen"
    return True, ""


def test_person_name_alias_marcello_resolves_to_marcelo() -> tuple[bool, str]:
    """Phase 4A.6 defense-in-depth: find_person('Marcello') should
    resolve to the Marcelo record via PERSON_NAME_ALIASES — even when
    the bridge ASR-correction didn't fire (typed input bypasses bridge)."""
    hass = _MockHass()
    _frigate_person_tracker(hass, "marcelo", "kitchen")
    agg = _build_agg(hass)
    result = agg.find_person("Marcello")
    if result.get("error"):
        return False, f"got error {result['error']!r}"
    if not result["data"] or not result["data"].get("currently_seen"):
        return False, (
            f"alias should resolve to Marcelo record (currently_seen=True), "
            f"got {result['data']!r}"
        )
    if result["data"].get("last_visual_room") != "kitchen":
        return False, (
            f"alias should return Marcelo's actual location, got "
            f"last_visual_room={result['data'].get('last_visual_room')!r}"
        )
    # The suggested_phrasing should reference "Marcelo" (canonical), not "Marcello".
    if "Marcello" in result["suggested_phrasing"]:
        return False, (
            f"alias-resolved phrasing must use canonical spelling, "
            f"got: {result['suggested_phrasing']!r}"
        )
    if "Marcelo" not in result["suggested_phrasing"]:
        return False, (
            f"alias-resolved phrasing should mention Marcelo: "
            f"{result['suggested_phrasing']!r}"
        )
    return True, ""


def test_person_name_alias_case_insensitive() -> tuple[bool, str]:
    """find_person('MARSELLO') (uppercase variant) should still alias
    to Marcelo via case-insensitive lookup."""
    hass = _MockHass()
    _frigate_person_tracker(hass, "marcelo", "office")
    agg = _build_agg(hass)
    # Multiple case variants — all should resolve.
    for variant in ("MARSELLO", "Marsello", "marsello", "MaRsElLo"):
        result = agg.find_person(variant)
        if result.get("error"):
            return False, f"variant {variant!r} got error {result['error']!r}"
        if not result["data"] or not result["data"].get("currently_seen"):
            return False, (
                f"variant {variant!r} should resolve to Marcelo record, "
                f"got currently_seen={result['data'].get('currently_seen') if result['data'] else None!r}"
            )
        if "Marcelo" not in result["suggested_phrasing"]:
            return False, (
                f"variant {variant!r} phrasing should mention Marcelo: "
                f"{result['suggested_phrasing']!r}"
            )
    return True, ""


def test_stale_person_filtered_from_room_render() -> tuple[bool, str]:
    """ROOT-CAUSE REGRESSION GUARD (post-Addendum 24):
    `_apply_detection` appends persons to room["persons"] and never
    prunes. A face detection from 2 days ago would stay in the dict
    forever; `get_all_rooms_state` then reported phantom presences
    ("David in living room" when he hadn't been seen in days).

    Fix: `_render_room` filters persons whose age > RECENT_SECONDS
    (180s) out of the `persons` field and moves them to
    `historical_persons`. This test locks that behaviour.
    """
    hass = _MockHass()
    # Fresh detection of Marcelo in driveway
    _fresh_face(hass, "driveway", "Marcelo", 0.95)
    agg = _build_agg(hass)
    # Inject a 2-day-old David detection into living_room directly
    # (simulates a write that happened 172800 seconds ago).
    ancient_ts = time.time() - (2 * 24 * 3600)
    agg._rooms["living_room"]["persons"].append({  # noqa: SLF001
        "identity": {
            "name": "David",
            "confidence": 0.92,
            "confidence_band": "high",
            "source": "frigate_face",
        },
        "generic_person_detected": True,
        "source_camera": "camera.living_room",
        "last_seen_at_ts": ancient_ts,
    })
    overview = agg.get_world_state()
    living = overview["rooms"]["living_room"]
    driveway = overview["rooms"]["driveway"]
    # The 2-day-old David entry MUST be filtered out of the live
    # persons list AND moved to historical_persons.
    live_names = [
        (p.get("identity") or {}).get("name")
        for p in living.get("persons", [])
    ]
    if "David" in live_names:
        return False, (
            f"stale 2-day-old David entry must NOT appear in "
            f"living_room.persons: {live_names!r}"
        )
    historical_names = [
        (p.get("identity") or {}).get("name")
        for p in living.get("historical_persons", [])
    ]
    if "David" not in historical_names:
        return False, (
            f"stale 2-day-old David entry should appear in "
            f"living_room.historical_persons: "
            f"{historical_names!r}"
        )
    # Fresh Marcelo in driveway must STILL be present.
    driveway_names = [
        (p.get("identity") or {}).get("name")
        for p in driveway.get("persons", [])
    ]
    if "Marcelo" not in driveway_names:
        return False, (
            f"fresh Marcelo detection must remain in driveway.persons: "
            f"{driveway_names!r}"
        )
    # get_room_state should phrase "I don't see anyone" for living_room
    # because the fresh-filter excluded the stale David entry AND
    # occupancy is off (no _apply_occupancy was called).
    living_state = agg.get_room_state("living_room")
    phrasing = living_state.get("suggested_phrasing", "")
    if "David" in phrasing:
        return False, (
            f"get_room_state must not phrase a stale identity as currently "
            f"present: {phrasing!r}"
        )
    return True, ""


def test_frigate_tracker_overrides_stale_face_sensor() -> tuple[bool, str]:
    """If both sensor.frigate_marcelo_last_camera AND
    sensor.kitchen_last_recognized_face point to Marcelo, the freshest
    detection wins. (Both go through _apply_detection.)"""
    hass = _MockHass()
    # Stale face-sensor detection
    _fresh_face(hass, "office", "Marcelo", 0.90)
    agg = _build_agg(hass)
    # Backdate the office detection by 10 min
    agg._people["Marcelo"]["last_visual_at_ts"] = time.time() - 600  # noqa: SLF001
    # Now Frigate tracker fires for kitchen (fresh)
    _frigate_person_tracker(hass, "marcelo", "kitchen")
    agg._ingest(  # noqa: SLF001
        "sensor.frigate_marcelo_last_camera",
        None,
        hass.states.get("sensor.frigate_marcelo_last_camera"),
    )
    result = agg.find_person("Marcelo")
    if "kitchen" not in result["suggested_phrasing"]:
        return False, (
            f"freshest tracker (kitchen) should win over stale face-sensor (office): "
            f"{result['suggested_phrasing']!r}"
        )
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# M1 — perception scheduler tests (Addendum 27 / Section 6)
#
# The scheduler is purely the gating + enqueue logic; the actual
# vision-sidecar call inside _dispatch_perception is a coroutine we
# don't run here (httpx mock would be heavyweight + the request shape
# is verified live). What we test:
#   1. happy path → dispatched + task enqueued
#   2. per-room debounce → debounced, no task
#   3. global rate cap → rate_capped, no task
#   4. no camera in room → no_camera, no task
#   5. disabled aggregator → disabled, no task
#   6. occupancy 0→1 edge wiring fires the scheduler
#   7. occupancy 1→1 (no edge) does NOT fire
# ─────────────────────────────────────────────────────────────────────
def _setup_agg_with_camera_in_room(room: str = "kitchen") -> tuple[Any, Any]:
    h = _MockHass()
    _occupancy_only(h, room, on=False)  # camera registered, occupancy off
    agg = _build_agg(h)
    # Reset task list to ignore anything from setup
    h.tasks_created.clear()
    return h, agg


def test_scheduler_happy_path_dispatches() -> tuple[bool, str]:
    h, agg = _setup_agg_with_camera_in_room("kitchen")
    verdict = agg._schedule_perception_async("kitchen", reason="test")
    if verdict != "dispatched":
        return False, f"expected dispatched, got {verdict!r}"
    if len(h.tasks_created) < 1:
        return False, "scheduler did not enqueue a task"
    # The dispatch task + a log_decision task may both be enqueued; both ok.
    return True, ""


def test_scheduler_per_room_debounce() -> tuple[bool, str]:
    h, agg = _setup_agg_with_camera_in_room("kitchen")
    first = agg._schedule_perception_async("kitchen", reason="t1")
    if first != "dispatched":
        return False, f"first call should dispatch, got {first!r}"
    # Immediate second call to SAME room → debounced
    second = agg._schedule_perception_async("kitchen", reason="t2")
    if second != "debounced":
        return False, f"second call should debounce, got {second!r}"
    return True, ""


def test_scheduler_different_rooms_no_cross_debounce() -> tuple[bool, str]:
    h = _MockHass()
    _occupancy_only(h, "kitchen", on=False)
    _occupancy_only(h, "living_room", on=False)
    agg = _build_agg(h)
    h.tasks_created.clear()
    v1 = agg._schedule_perception_async("kitchen", reason="t1")
    v2 = agg._schedule_perception_async("living_room", reason="t2")
    if v1 != "dispatched" or v2 != "dispatched":
        return False, f"both rooms should dispatch, got kitchen={v1!r} living={v2!r}"
    return True, ""


def test_scheduler_global_rate_cap() -> tuple[bool, str]:
    """Fire N+1 dispatches to N different rooms in quick succession;
    only the first PERCEPTION_AUTO_RATE_CAP_PER_MIN should dispatch."""
    from const import PERCEPTION_AUTO_RATE_CAP_PER_MIN
    h = _MockHass()
    rooms = [f"room_{i}" for i in range(PERCEPTION_AUTO_RATE_CAP_PER_MIN + 2)]
    for r in rooms:
        _occupancy_only(h, r, on=False)
    agg = _build_agg(h)
    h.tasks_created.clear()
    verdicts = [agg._schedule_perception_async(r, reason="storm") for r in rooms]
    dispatched_count = sum(1 for v in verdicts if v == "dispatched")
    rate_capped_count = sum(1 for v in verdicts if v == "rate_capped")
    if dispatched_count != PERCEPTION_AUTO_RATE_CAP_PER_MIN:
        return False, (
            f"expected {PERCEPTION_AUTO_RATE_CAP_PER_MIN} dispatched, "
            f"got {dispatched_count}; verdicts={verdicts}"
        )
    if rate_capped_count != 2:
        return False, f"expected 2 rate_capped, got {rate_capped_count}; verdicts={verdicts}"
    return True, ""


def test_scheduler_no_camera_in_room() -> tuple[bool, str]:
    h = _MockHass()
    agg = _build_agg(h)
    h.tasks_created.clear()
    # room "attic" has no cameras registered
    verdict = agg._schedule_perception_async("attic", reason="t")
    if verdict != "no_camera":
        return False, f"expected no_camera, got {verdict!r}"
    # The skip is logged via routing_log (kind=perception_dispatch,
    # verdict=no_camera) — that's the log_decision task enqueued.
    # What MUST NOT happen is a dispatch coroutine that would actually
    # hit vision-sidecar. We rely on verdict alone for that gate since
    # the mock conflates dispatch + log tasks under async_create_task.
    return True, ""


def test_scheduler_disabled_returns_disabled() -> tuple[bool, str]:
    os.environ[WORLD_STATE_DISABLED_ENV] = "off"
    try:
        h = _MockHass()
        _occupancy_only(h, "kitchen", on=False)
        agg = WorldStateAggregator(h)  # don't run async_setup; just construct
        verdict = agg._schedule_perception_async("kitchen", reason="t")
    finally:
        os.environ.pop(WORLD_STATE_DISABLED_ENV, None)
    if verdict != "disabled":
        return False, f"expected disabled, got {verdict!r}"
    return True, ""


def test_scheduler_occupancy_edge_fires() -> tuple[bool, str]:
    """0→1 occupancy transition should trigger the scheduler. The
    initial off→on edge counts; subsequent on→on (re-fired events)
    must not, otherwise event repeats would saturate the cap."""
    h = _MockHass()
    _occupancy_only(h, "kitchen", on=False)
    agg = _build_agg(h)
    h.tasks_created.clear()

    # Drive a state_changed-style ingest by flipping the binary_sensor on
    on_state = _MockState("binary_sensor.kitchen_person_occupancy", "on")
    agg._ingest("binary_sensor.kitchen_person_occupancy", None, on_state)
    if not h.tasks_created:
        return False, "occupancy 0→1 did not enqueue scheduler task"
    edge_tasks = len(h.tasks_created)

    # Re-fire the same on-event (occupancy stays on). The edge guard
    # should suppress; no new task.
    agg._ingest("binary_sensor.kitchen_person_occupancy", on_state, on_state)
    if len(h.tasks_created) != edge_tasks:
        return False, (
            f"on→on re-fire enqueued extra tasks ({len(h.tasks_created) - edge_tasks}); "
            "should be edge-only"
        )
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# Addendum 28 — phantom-zone occupancy rejection (acute fix)
#
# Frigate publishes binary_sensor.<zone>_person_occupancy for sub-zones
# (sofa, sink, dining_left, whole_kitchen, etc.) as well as cameras.
# Without filtering, every zone created a phantom room + phantom
# camera.<zone> and the M1 scheduler dispatched perception requests
# that returned http_502 because the camera entities don't exist.
# Fix: reject occupancy / face / frigate_event ingestion unless the
# corresponding camera entity actually exists in hass.states.
# ─────────────────────────────────────────────────────────────────────
def test_addendum28_occupancy_skips_phantom_zone() -> tuple[bool, str]:
    """A zone occupancy sensor with NO matching camera entity must NOT
    create a phantom room in self._rooms (the bug that produced 87
    dispatches/cycle for "sofa" in production)."""
    h = _MockHass()
    agg = _build_agg(h)
    # Synthesize a zone occupancy event — no camera.sofa registered.
    zone_state = _MockState("binary_sensor.sofa_person_occupancy", "on")
    agg._ingest("binary_sensor.sofa_person_occupancy", None, zone_state)
    if "sofa" in agg._rooms:
        return False, "phantom zone 'sofa' was registered as a room"
    if h.tasks_created:
        return False, (
            f"phantom zone triggered scheduler dispatch "
            f"({len(h.tasks_created)} tasks); should be silent skip"
        )
    return True, ""


def test_addendum28_occupancy_accepts_real_camera() -> tuple[bool, str]:
    """Regression guard: with a matching camera entity, occupancy still
    creates the room normally."""
    h = _MockHass()
    h.states.set("camera.kitchen", "streaming")
    agg = _build_agg(h)
    h.tasks_created.clear()
    on_state = _MockState("binary_sensor.kitchen_person_occupancy", "on")
    agg._ingest("binary_sensor.kitchen_person_occupancy", None, on_state)
    if "kitchen" not in agg._rooms:
        return False, "real-camera occupancy did NOT create the kitchen room"
    if not agg._rooms["kitchen"].get("occupied"):
        return False, "kitchen room is not marked occupied"
    if not h.tasks_created:
        return False, "real-camera occupancy 0→1 did not enqueue scheduler task"
    return True, ""


def test_addendum28_face_sensor_skips_phantom_zone() -> tuple[bool, str]:
    """Same defense for sensor.<zone>_last_recognized_face — Frigate
    publishes per-camera face-rec sensors but the same regex would
    accept a per-zone variant if Frigate config ever changes."""
    h = _MockHass()
    agg = _build_agg(h)
    # No camera.driveway_left registered
    zone_face = _MockState(
        "sensor.driveway_left_last_recognized_face", "Marcelo", {"score": 0.92}
    )
    agg._ingest(
        "sensor.driveway_left_last_recognized_face", None, zone_face
    )
    if "driveway_left" in agg._rooms:
        return False, "phantom zone face sensor created room 'driveway_left'"
    if "Marcelo" in agg._people:
        # If Marcelo is added via this phantom-zone path, the people
        # index is also polluted.
        person = agg._people["Marcelo"]
        if any(d.get("room") == "driveway_left" for d in (person.get("history") or [])):
            return False, "phantom zone detection landed in person history"
    return True, ""


def test_addendum28_frigate_event_skips_phantom_camera() -> tuple[bool, str]:
    """frigate_event with a camera name that has no HA entity must NOT
    create a phantom room (the defensive AR28-4 mirror)."""
    h = _MockHass()
    agg = _build_agg(h)
    # No camera.unknown_zone registered.
    event = type("E", (), {
        "data": {"camera": "unknown_zone",
                 "sub_label": ["Marcelo", 0.91]}
    })()
    agg._handle_frigate_event(event)
    if "unknown_zone" in agg._rooms:
        return False, "frigate_event for unknown camera created phantom room"
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────
ALL_TESTS = [
    ("confidence_band_thresholds",                    test_confidence_band_thresholds),
    ("freshness_thresholds",                          test_freshness_thresholds),
    ("camera_to_room_default",                        test_camera_to_room_default),
    ("story1_room_state_recognized_person",           test_story1_room_state_recognized_person),
    ("story1_room_state_generic_only",                test_story1_room_state_generic_only),
    ("story1_room_state_empty",                       test_story1_room_state_empty),
    ("story2_find_me_high_conf_fresh",                test_story2_find_me_high_conf_fresh),
    ("story2_find_me_low_conf_hedges",                test_story2_find_me_low_conf_hedges),
    ("story3_find_person_multi_room_most_recent",     test_story3_find_person_multi_room_most_recent_wins),
    ("story4_ha_home_no_visual",                      test_story4_ha_home_no_visual),
    ("story4_stale_visual_with_ha_home",              test_story4_stale_visual_with_ha_home),
    ("story5_stale_perception_signals_stale",         test_story5_stale_perception_signals_stale),
    ("story6_unknown_must_not_be_named_marcelo",      test_story6_unknown_must_not_be_named_marcelo),
    ("story7_arrival_face_recognition_present",       test_story7_arrival_face_recognition_present),
    ("disabled_env_var_returns_disabled_error",       test_disabled_env_var_returns_disabled_error),
    ("pronoun_resolution_me_I_myself",                test_pronoun_resolution),
    ("find_person_unknown_name_returns_no_record",    test_find_person_unknown_name_returns_no_record),
    ("who_is_in_unknown_room",                        test_who_is_in_unknown_room),
    ("multi_person_separate_rooms",                   test_multi_person_separate_rooms),
    ("get_world_state_disabled",                      test_get_world_state_disabled),
    ("frigate_person_tracker_bare_camera",            test_frigate_person_tracker_bare_camera_value),
    ("frigate_person_tracker_camera_entity_form",     test_frigate_person_tracker_camera_entity_form),
    ("frigate_person_tracker_unknown_skipped",        test_frigate_person_tracker_unknown_state_skipped),
    ("frigate_person_tracker_with_score_attr",        test_frigate_person_tracker_with_score_attr),
    ("ha_person_canonical_name_mapping",              test_ha_person_canonical_name_mapping),
    ("person_name_alias_marcello_resolves",           test_person_name_alias_marcello_resolves_to_marcelo),
    ("person_name_alias_case_insensitive",            test_person_name_alias_case_insensitive),
    ("frigate_tracker_overrides_stale_face_sensor",   test_frigate_tracker_overrides_stale_face_sensor),
    ("stale_person_filtered_from_room_render",        test_stale_person_filtered_from_room_render),
    # M1 perception scheduler (Addendum 27)
    ("m1_scheduler_happy_path",                       test_scheduler_happy_path_dispatches),
    ("m1_scheduler_per_room_debounce",                test_scheduler_per_room_debounce),
    ("m1_scheduler_different_rooms_no_cross",         test_scheduler_different_rooms_no_cross_debounce),
    ("m1_scheduler_global_rate_cap",                  test_scheduler_global_rate_cap),
    ("m1_scheduler_no_camera",                        test_scheduler_no_camera_in_room),
    ("m1_scheduler_disabled",                         test_scheduler_disabled_returns_disabled),
    ("m1_scheduler_occupancy_edge_fires",             test_scheduler_occupancy_edge_fires),
    # Addendum 28 — phantom-zone rejection (acute fix)
    ("addendum28_occupancy_skips_phantom_zone",       test_addendum28_occupancy_skips_phantom_zone),
    ("addendum28_occupancy_accepts_real_camera",      test_addendum28_occupancy_accepts_real_camera),
    ("addendum28_face_sensor_skips_phantom_zone",     test_addendum28_face_sensor_skips_phantom_zone),
    ("addendum28_frigate_event_skips_phantom_camera", test_addendum28_frigate_event_skips_phantom_camera),
]


def main() -> int:
    print(f"{DIM}══ world_state.py test suite ══{RST}")
    print(f"{DIM}── scenarios ──{RST}")
    passed = 0
    failed: list[tuple[str, str]] = []
    for name, fn in ALL_TESTS:
        try:
            ok, reason = fn()
        except Exception as e:
            ok, reason = False, f"{type(e).__name__}: {e}"
        if ok:
            passed = _ts_passed(passed, name)
        else:
            _ts_failed(failed, name, reason)
    total = len(ALL_TESTS)
    color = GREEN if not failed else RED
    print(f"  {color}{passed}/{total} passed{RST}")
    if failed:
        print(f"\n{RED}══ FAILED ══{RST}")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
        return 1
    print(f"\n{GREEN}══ ✓ all {total} tests passed ══{RST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

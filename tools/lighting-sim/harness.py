"""Living Lights simulation harness.

Loads the real Home Assistant packages (generated and hand-written) into a
Home Assistant core test instance, replaces the light domain with a fake that
records every call and mirrors it into entity state, and steps a fake clock
through a timeline of input sensor changes. Nothing here re-implements the
lighting logic: the automations, template sensors and Jinja that run are the
files that get deployed.

Requires the ha-sim venv (Home Assistant + pytest-homeassistant-custom-component).
Used by test_sim.py; not imported by production code.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import asyncio
from typing import Any

import yaml
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

SKIP_DOMAINS = {"mqtt", "shell_command", "adaptive_lighting", "logger", "sensor", "rest", "homeassistant"}
SETUP_ORDER = ["input_boolean", "input_number", "input_text", "input_datetime", "input_select",
               "template", "script", "automation"]
ZONE_CAMERA = {
    "dining_left": "dining_room", "dining_right": "dining_room", "whole_dining_room": "dining_room",
    "sink": "kitchen", "island_left": "kitchen", "island_right": "kitchen", "whole_kitchen": "kitchen",
    "sofa": "living_room", "front_left": "living_room", "weights": "living_room", "office": "living_room",
    "front_door": "living_room", "whole_living_room": "living_room",
    "workshop_zone": "workshop", "e28": "driveway",
}
CAMERAS = sorted(set(ZONE_CAMERA.values()))
LIGHTS = ["light.office", "light.front_left", "light.front_right", "light.rear_left", "light.rear_right",
          "light.sink", "light.island_left", "light.island_right", "light.dining_table_left",
          "light.dining_table_right", "light.living_room_lights", "light.kitchen_lights", "light.kitchen",
          "light.dining_room_lights", "light.dining_room", "light.outdoor_light",
          "light.ambient_light_left_mss110_main_channel", "light.ambient_light_right_mss110_main_channel"]
MOCKED_SERVICES = [("logbook", "log"), ("conversation", "process"), ("assist_satellite", "announce"),
                   ("switch", "turn_on"), ("switch", "turn_off"), ("media_player", "media_pause"),
                   ("notify", "notify"), ("notify", "mobile_app_iphone"), ("homeassistant", "update_entity")]
# Production-like toggle values. Unknown toggles keep the package initial.
TOGGLES_ON = ["input_boolean.living_lights_enabled", "input_boolean.living_lights_gradient_enabled",
              "input_boolean.user_at_home", "input_boolean.living_lights_working_hours_enabled",
              "input_boolean.living_lights_morning_energize_enabled"]
TOGGLES_OFF = ["input_boolean.living_lights_shadow", "input_boolean.living_lights_travel_mode",
               "input_boolean.living_lights_asleep", "input_boolean.homeai_sleep", "input_boolean.homeai_movie",
               "input_boolean.living_lights_articulate_overrides",
               "input_boolean.living_lights_actuate_from_belief_changes",
               "input_boolean.living_lights_evidence_engine_enabled", "input_boolean.living_lights_woke_up_today",
               "input_boolean.living_lights_morning_greeting_enabled", "input_boolean.living_lights_anticipated_enabled"]
TZ = "America/Los_Angeles"


def load_packages(root: pathlib.Path) -> dict[str, Any]:
    """Merge every package the way Home Assistant's packages loader does for
    the domains the simulation supports (lists concatenate, dicts merge)."""
    merged: dict[str, Any] = {}
    files = sorted((root / "packages").glob("*.yaml"))
    proactive = root / "homeai_proactive.yaml"
    if proactive.exists():
        files.append(proactive)
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for domain, cfg in doc.items():
            if domain in SKIP_DOMAINS:
                continue
            if isinstance(cfg, list):
                merged.setdefault(domain, []).extend(cfg)
            elif isinstance(cfg, dict):
                merged.setdefault(domain, {}).update(cfg)
    return merged


class FakeLights:
    """Records light.turn_on / turn_off and mirrors them into entity state so
    templates that read is_state(light, 'on') see what the pilots did."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _targets(call: ServiceCall) -> list[str]:
        raw = call.data.get("entity_id")
        if raw is None:
            raw = (call.data.get("target") or {}).get("entity_id")
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        return list(raw)

    async def turn_on(self, call: ServiceCall) -> None:
        now = dt_util.now()
        for entity_id in self._targets(call):
            prev = self.hass.states.get(entity_id)
            prev_state = prev.state if prev else "off"
            attrs = dict(prev.attributes) if prev else {}
            pct = call.data.get("brightness_pct")
            if pct is not None:
                attrs["brightness"] = round(float(pct) * 255 / 100)
            elif "brightness" in call.data:
                attrs["brightness"] = call.data["brightness"]
            if "color_temp_kelvin" in call.data:
                attrs["color_temp_kelvin"] = call.data["color_temp_kelvin"]
            self.hass.states.async_set(entity_id, "on", attrs)
            self.calls.append({"t": now.isoformat(), "service": "turn_on", "entity": entity_id,
                               "from": prev_state, "brightness_pct": pct,
                               "color_temp_kelvin": call.data.get("color_temp_kelvin"),
                               "transition": call.data.get("transition"),
                               "context_parent": call.context.parent_id})

    async def turn_off(self, call: ServiceCall) -> None:
        now = dt_util.now()
        for entity_id in self._targets(call):
            prev = self.hass.states.get(entity_id)
            attrs = dict(prev.attributes) if prev else {}
            self.hass.states.async_set(entity_id, "off", attrs)
            self.calls.append({"t": now.isoformat(), "service": "turn_off", "entity": entity_id,
                               "from": prev.state if prev else "off", "brightness_pct": 0,
                               "context_parent": call.context.parent_id})

    def install(self) -> None:
        self.hass.services.async_register("light", "turn_on", self.turn_on)
        self.hass.services.async_register("light", "turn_off", self.turn_off)
        for light in LIGHTS:
            if self.hass.states.get(light) is None:
                self.hass.states.async_set(light, "off", {})


class Recorder:
    """Keeps every state change of the entities the report cares about."""

    WATCH_PREFIXES = ("input_boolean.living_lights_asleep", "input_boolean.living_lights_woke_up_today",
                      "binary_sensor.living_lights_any_occupied", "sensor.living_lights_profile",
                      "input_text.living_lights_override_text_")

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.changes: list[dict[str, Any]] = []
        self.classifier: list[dict[str, Any]] = []
        hass.bus.async_listen("state_changed", self._on_change)

    def _on_change(self, event) -> None:
        entity_id = event.data["entity_id"]
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if new is None:
            return
        if old is not None and old.state == new.state:
            return
        row = {"t": dt_util.now().isoformat(), "entity": entity_id,
               "from": old.state if old else None, "to": new.state}
        if entity_id.startswith(self.WATCH_PREFIXES):
            self.changes.append(row)
        elif entity_id.endswith("_lighting_state"):
            row["brightness_pct"] = new.attributes.get("predicted_brightness_pct")
            self.classifier.append(row)


async def setup_sim(hass: HomeAssistant, packages_root: pathlib.Path,
                    initial_inputs: dict[str, str] | None = None) -> tuple[FakeLights, Recorder, dict]:
    """Load the packages. `initial_inputs` (sensor states such as Frigate
    occupancy, the TV, presence) are written BEFORE the template sensors and
    automations exist, so their creation is not seen as a state change: a
    person entity created as `home` would otherwise fire the return-home
    backstop 90 s into every scenario."""
    if hasattr(hass.config, "async_set_time_zone"):
        await hass.config.async_set_time_zone(TZ)
    else:  # pragma: no cover
        hass.config.set_time_zone(TZ)
    hass.loop.slow_callback_duration = 1e9   # the frozen clock makes every step look slow
    for domain, service in MOCKED_SERVICES:
        async_mock_service(hass, domain, service)
    for domain in ("shell_command",):
        for name in ("living_lights_capture_preference", "living_lights_append_preference_log",
                     "living_lights_append_activity_log", "living_lights_append_perception",
                     "living_lights_append_log", "living_lights_rotate_perception_log"):
            async_mock_service(hass, domain, name)
    lights = FakeLights(hass)
    lights.install()
    for entity_id, state in (initial_inputs or {}).items():
        if not entity_id.startswith("input_"):
            set_input(hass, entity_id, state)
    recorder = Recorder(hass)
    config = load_packages(packages_root)
    for domain in SETUP_ORDER:
        if domain not in config:
            continue
        ok = await async_setup_component(hass, domain, {domain: config[domain]})
        assert ok, f"{domain} failed to set up from {packages_root}"
    await hass.async_block_till_done()
    return lights, recorder, config


async def settle(hass: HomeAssistant, rounds: int = 80) -> None:
    """Drain ready callbacks and tasks without waiting on timers.

    hass.async_block_till_done() cannot be used once inputs flow: the event
    loop clock is frozen, so a pilot's `delay` step (a call_later handle)
    never becomes due on its own and the wait never returns. Zero-length
    sleeps let every ready callback, state listener, template render and
    automation task run; timers fire when run_timeline advances the clock
    and calls async_fire_time_changed.
    """
    for _ in range(rounds):
        await asyncio.sleep(0)


async def set_toggle(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    if hass.states.get(entity_id) is None:
        return
    await hass.services.async_call("input_boolean", "turn_on" if on else "turn_off",
                                   {"entity_id": entity_id}, blocking=True)


async def apply_production_toggles(hass: HomeAssistant) -> None:
    for entity_id in TOGGLES_ON:
        await set_toggle(hass, entity_id, True)
    for entity_id in TOGGLES_OFF:
        await set_toggle(hass, entity_id, False)
    await settle(hass)


def set_input(hass: HomeAssistant, entity_id: str, state: str, attributes: dict | None = None) -> None:
    """Feed an input sensor (Frigate occupancy, motion, TV, presence...)."""
    prev = hass.states.get(entity_id)
    attrs = dict(prev.attributes) if prev else {}
    if attributes:
        attrs.update(attributes)
    hass.states.async_set(entity_id, state, attrs)


async def apply_input(hass: HomeAssistant, entity_id: str, state: str) -> None:
    if entity_id.startswith("input_boolean."):
        await set_toggle(hass, entity_id, state == "on")
    else:
        set_input(hass, entity_id, state)


def default_inputs() -> dict[str, str]:
    inputs = {}
    for zone, cam in ZONE_CAMERA.items():
        inputs[f"binary_sensor.{zone}_person_occupancy"] = "off"
        inputs[f"sensor.{cam}_{zone}_avg_speed"] = "0"
    for cam in CAMERAS:
        inputs[f"binary_sensor.{cam}_person_occupancy"] = "off"
        inputs[f"binary_sensor.{cam}_motion"] = "off"
        inputs[f"binary_sensor.anticipated_{cam}"] = "off"
    inputs["media_player.lg_tv"] = "off"
    inputs["person.engineeredlighting"] = "home"
    inputs["sensor.living_room_person_count"] = "0"
    return inputs


class Timeline:
    """A start time, initial inputs, and timed input changes."""

    def __init__(self, start: dt.datetime, end: dt.datetime, initial: dict[str, str] | None = None):
        self.start, self.end = start, end
        self.initial = {**default_inputs(), **(initial or {})}
        self.events: list[tuple[dt.datetime, str, str]] = []

    def at(self, when: dt.datetime | str, entity_id: str, state: str) -> "Timeline":
        if isinstance(when, str):
            when = dt.datetime.fromisoformat(when)
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.start.tzinfo)
        self.events.append((when, entity_id, state))
        return self

    def occupy(self, zone: str, start: dt.datetime, end: dt.datetime) -> "Timeline":
        """Raw zone occupancy plus camera-level occupancy and motion for the window."""
        cam = ZONE_CAMERA[zone]
        for ent in (f"binary_sensor.{zone}_person_occupancy", f"binary_sensor.{cam}_person_occupancy",
                    f"binary_sensor.{cam}_motion"):
            self.at(start, ent, "on")
            self.at(end, ent, "off")
        return self


async def run_timeline(hass: HomeAssistant, freezer, timeline: Timeline, *, step_s: int = 20,
                       quiet_step_s: int = 60, minute_hook=None, progress=None) -> list[dict[str, Any]]:
    """Advance the fake clock from start to end, firing due timers and
    applying input changes at their times. Returns per-minute snapshots.

    `freezer` is a plain freezegun factory (loop clock frozen too). Timers
    fire only when the clock is moved and async_fire_time_changed runs the
    due handles, so a pilot's 2 s `delay` completes at the next step, at
    most one step late. Steps are `step_s` while an input change is within five
    minutes, `quiet_step_s` otherwise; every scheduled trigger still fires at
    its own time because time_changed fires all due timers in order.
    """
    import sys
    import time as _time
    freezer.move_to(timeline.start)
    async_fire_time_changed(hass, timeline.start)
    for entity_id, state in timeline.initial.items():
        if entity_id.startswith("input_"):      # helpers exist only after setup
            await apply_input(hass, entity_id, state)
        elif hass.states.get(entity_id) is None:
            set_input(hass, entity_id, state)
    await settle(hass)
    events = sorted(timeline.events, key=lambda e: e[0])
    snapshots: list[dict[str, Any]] = []
    now = timeline.start
    idx = 0
    next_minute = now + dt.timedelta(minutes=1)
    next_report = now + dt.timedelta(hours=1)
    wall0 = _time.monotonic()
    near = dt.timedelta(minutes=5)
    while now < timeline.end:
        busy = idx < len(events) and events[idx][0] - now <= near
        step = dt.timedelta(seconds=step_s if busy else quiet_step_s)
        target = min(now + step, timeline.end)
        if idx < len(events) and events[idx][0] < target:
            target = max(events[idx][0], now)
        freezer.move_to(target)
        async_fire_time_changed(hass, target)
        await settle(hass)
        while idx < len(events) and events[idx][0] <= target:
            _, entity_id, state = events[idx]
            await apply_input(hass, entity_id, state)
            idx += 1
        await settle(hass)
        now = target
        if now >= next_minute:
            snapshots.append(snapshot(hass))
            if minute_hook:
                minute_hook(hass, now)
            next_minute = now + dt.timedelta(minutes=1)
        if now >= next_report:
            msg = f"[sim] {now.isoformat()} wall {_time.monotonic() - wall0:.0f}s"
            print(msg, file=sys.stderr, flush=True)
            if progress:
                progress(msg)
            next_report = now + dt.timedelta(hours=1)
    return snapshots


def snapshot(hass: HomeAssistant) -> dict[str, Any]:
    lit = {}
    for light in LIGHTS:
        st = hass.states.get(light)
        if st is not None and st.state == "on":
            lit[light] = round(st.attributes.get("brightness", 0) * 100 / 255)
    return {"t": dt_util.now().isoformat(),
            "asleep": (hass.states.get("input_boolean.living_lights_asleep") or {}).state
            if hass.states.get("input_boolean.living_lights_asleep") else None,
            "any_occupied": (hass.states.get("binary_sensor.living_lights_any_occupied").state
                             if hass.states.get("binary_sensor.living_lights_any_occupied") else None),
            "profile": hass.states.get("sensor.living_lights_profile").state
            if hass.states.get("sensor.living_lights_profile") else None,
            "lights_on": lit}

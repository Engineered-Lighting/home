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
                   ("media_player", "media_pause"), ("mqtt", "publish"),
                   ("notify", "notify"), ("notify", "mobile_app_iphone"), ("homeassistant", "update_entity")]
TV_OFF_STATES = ("off", "standby", "unavailable", "unknown")   # what the generator will treat as TV off
HEARTBEAT = "sensor.lighting_publisher_heartbeat"
TV_WATCHING = "binary_sensor.living_lights_tv_watching"
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
                pct = round(float(call.data["brightness"]) * 100 / 255)
            elif attrs.get("brightness", 0) == 0:
                # A bare turn_on (no level) on a light that was off at level 0
                # comes up at full, as Home Assistant restores no zero level.
                attrs["brightness"] = 255
            if "color_temp_kelvin" in call.data:
                attrs["color_temp_kelvin"] = call.data["color_temp_kelvin"]
            # Home Assistant semantics: turn_on with brightness 0 turns the light
            # off; decided from the level in THIS call, never from stale attributes.
            to_state = "off" if (pct is not None and float(pct) == 0) else "on"
            self.hass.states.async_set(entity_id, to_state, attrs)
            self.calls.append({"t": now.isoformat(), "service": "turn_on", "entity": entity_id,
                               "from": prev_state, "to": to_state, "brightness_pct": pct,
                               "color_temp_kelvin": call.data.get("color_temp_kelvin"),
                               "transition": call.data.get("transition"),
                               "context_id": call.context.id,
                               "context_parent": call.context.parent_id})

    async def turn_off(self, call: ServiceCall) -> None:
        now = dt_util.now()
        for entity_id in self._targets(call):
            prev = self.hass.states.get(entity_id)
            attrs = dict(prev.attributes) if prev else {}
            self.hass.states.async_set(entity_id, "off", attrs)
            self.calls.append({"t": now.isoformat(), "service": "turn_off", "entity": entity_id,
                               "from": prev.state if prev else "off", "to": "off", "brightness_pct": 0,
                               "context_id": call.context.id,
                               "context_parent": call.context.parent_id})

    def install(self) -> None:
        self.hass.services.async_register("light", "turn_on", self.turn_on)
        self.hass.services.async_register("light", "turn_off", self.turn_off)
        for light in LIGHTS:
            if self.hass.states.get(light) is None:
                self.hass.states.async_set(light, "off", {})


class FakeSwitches:
    """Records switch.turn_on / turn_off (the ambient strips) and mirrors state."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.calls: list[dict[str, Any]] = []

    async def _handle(self, call: ServiceCall, to_state: str) -> None:
        now = dt_util.now()
        for entity_id in FakeLights._targets(call):
            prev = self.hass.states.get(entity_id)
            self.hass.states.async_set(entity_id, to_state, dict(prev.attributes) if prev else {})
            self.calls.append({"t": now.isoformat(), "service": f"turn_{to_state}", "entity": entity_id,
                               "from": prev.state if prev else "off", "to": to_state,
                               "context_id": call.context.id, "context_parent": call.context.parent_id})

    async def turn_on(self, call: ServiceCall) -> None:
        await self._handle(call, "on")

    async def turn_off(self, call: ServiceCall) -> None:
        await self._handle(call, "off")

    def install(self) -> None:
        self.hass.services.async_register("switch", "turn_on", self.turn_on)
        self.hass.services.async_register("switch", "turn_off", self.turn_off)


class Recorder:
    """Keeps every state change of the entities the report cares about."""

    WATCH_PREFIXES = ("input_boolean.living_lights_asleep", "input_boolean.living_lights_woke_up_today",
                      "binary_sensor.living_lights_any_occupied", "sensor.living_lights_profile",
                      "input_text.living_lights_override_text_", "binary_sensor.living_lights_tv_playing",
                      "binary_sensor.living_lights_tv_watching", "binary_sensor.living_lights_publisher_fresh",
                      "sensor.living_lights_asleep_estimator", "media_player.lg_tv")

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.changes: list[dict[str, Any]] = []
        self.classifier: list[dict[str, Any]] = []
        self.context_owner: dict[str, str] = {}   # context id -> automation/script entity
        hass.bus.async_listen("state_changed", self._on_change)
        hass.bus.async_listen("automation_triggered", self._on_automation)
        hass.bus.async_listen("script_started", self._on_script)

    def _on_automation(self, event) -> None:
        self.context_owner[event.context.id] = event.data.get("entity_id", "automation.?")

    def _on_script(self, event) -> None:
        self.context_owner[event.context.id] = event.data.get("entity_id", "script.?")

    def owner_of(self, context_id: str | None, parent_id: str | None) -> str | None:
        return self.context_owner.get(context_id) or self.context_owner.get(parent_id)

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
    switches = FakeSwitches(hass)
    switches.install()
    lights.switches = switches          # one handle for the tests; call lists stay separate
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


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    """Set an input_number helper through its service (as a slider drag would)."""
    if hass.states.get(entity_id) is None:
        return
    await hass.services.async_call("input_number", "set_value",
                                   {"entity_id": entity_id, "value": value}, blocking=True)


async def apply_input(hass: HomeAssistant, entity_id: str, state: str) -> None:
    if entity_id.startswith("input_boolean."):
        await set_toggle(hass, entity_id, state == "on")
    elif entity_id.startswith("input_number."):
        await set_number(hass, entity_id, float(state))
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
        # Publisher heartbeat: off by default (legacy house). publisher() turns it
        # on; dead_from stops it, which is how "publisher dead" scenarios look.
        self.heartbeat = False
        self.heartbeat_dead_from: dt.datetime | None = None

    def _when(self, when: dt.datetime | str) -> dt.datetime:
        if isinstance(when, str):
            when = dt.datetime.fromisoformat(when)
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.start.tzinfo)
        return when

    def at(self, when: dt.datetime | str, entity_id: str, state: str) -> "Timeline":
        self.events.append((self._when(when), entity_id, state))
        return self

    def occupy(self, zone: str, start: dt.datetime, end: dt.datetime) -> "Timeline":
        """Raw zone occupancy plus camera-level occupancy and motion for the window."""
        cam = ZONE_CAMERA[zone]
        for ent in (f"binary_sensor.{zone}_person_occupancy", f"binary_sensor.{cam}_person_occupancy",
                    f"binary_sensor.{cam}_motion"):
            self.at(start, ent, "on")
            self.at(end, ent, "off")
        return self

    def tv(self, when: dt.datetime | str, state: str) -> "Timeline":
        """media_player.lg_tv state: on, playing, paused, standby, off, unavailable, unknown."""
        return self.at(when, "media_player.lg_tv", state)

    def belief(self, when: dt.datetime | str, entity_id: str, state: str) -> "Timeline":
        """A belief entity as the publisher would publish it (fed as plain state,
        because the sim drops the mqtt domain): binary_sensor.living_lights_tv_watching,
        sensor.<camera>_<zone>_activity, sensor.living_lights_asleep_estimator."""
        return self.at(when, entity_id, state)

    def publisher(self, alive: bool = True, dead_from: dt.datetime | str | None = None) -> "Timeline":
        """Refresh sensor.lighting_publisher_heartbeat every minute (until dead_from)."""
        self.heartbeat = alive
        self.heartbeat_dead_from = self._when(dead_from) if dead_from else None
        return self

    def derive_tv_watching(self, *, grace_s: int = 600, hold_s: int = 1800) -> "Timeline":
        """Level-0 oracle for the belief the publisher will compute from Jev.

        tv_watching is ON while the TV is on (state not in TV_OFF_STATES) and
        the sofa is occupied, or was occupied within `hold_s` (the AWAY_HOLD:
        an errand keeps the room dark). It also turns on when the TV comes on
        within `grace_s` after the sofa was last occupied. It is OFF otherwise
        (TV off, or nobody on the sofa for longer than the hold). Appends the
        belief edges to this timeline; scenarios that need a different belief
        add their own edges after calling this.
        """
        tv = self.initial.get("media_player.lg_tv", "off")
        sofa = self.initial.get("binary_sensor.sofa_person_occupancy", "off") == "on"
        sofa_last_off: dt.datetime | None = None
        events = sorted(self.events, key=lambda e: e[0])
        marks = sorted({e[0] for e in events} | {self.start})
        # evaluation instants: every input edge plus the hold/grace expiries
        extra = []
        for when, ent, st in events:
            if ent == "binary_sensor.sofa_person_occupancy" and st == "off":
                extra.append(when + dt.timedelta(seconds=hold_s))
                extra.append(when + dt.timedelta(seconds=grace_s))
        instants = sorted(set(marks) | set(extra))
        state = "off"
        derived: list[tuple[dt.datetime, str]] = []
        idx = 0
        for t in instants:
            while idx < len(events) and events[idx][0] <= t:
                _, ent, st = events[idx]
                if ent == "media_player.lg_tv":
                    tv = st
                elif ent == "binary_sensor.sofa_person_occupancy":
                    was = sofa
                    sofa = st == "on"
                    if was and not sofa:
                        sofa_last_off = t
                idx += 1
            tv_on = tv not in TV_OFF_STATES
            since_off = ((t - sofa_last_off).total_seconds()
                         if (not sofa and sofa_last_off is not None) else None)
            if tv_on and sofa:
                watching = True                                   # WATCHING
            elif tv_on and since_off is not None:
                watching = since_off <= (hold_s if state == "on" else grace_s)   # AWAY_HOLD / late TV-on
            else:
                watching = False                                  # TV off, or nobody near the sofa
            new = "on" if watching else "off"
            if new != state:
                derived.append((t, new))
                state = new
        for t, st in derived:
            if t == self.start:
                self.initial[TV_WATCHING] = st
            else:
                self.at(t, TV_WATCHING, st)
        self.initial.setdefault(TV_WATCHING, "off")
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
    if timeline.heartbeat:
        set_input(hass, HEARTBEAT, timeline.start.isoformat())
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
            if timeline.heartbeat and (timeline.heartbeat_dead_from is None or now < timeline.heartbeat_dead_from):
                set_input(hass, HEARTBEAT, now.isoformat())
                await settle(hass)
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
    def state_of(entity_id: str):
        st = hass.states.get(entity_id)
        return st.state if st is not None else None

    switches_on = sorted(s.entity_id for s in hass.states.async_all("switch") if s.state == "on")
    return {"t": dt_util.now().isoformat(),
            "asleep": state_of("input_boolean.living_lights_asleep"),
            "any_occupied": state_of("binary_sensor.living_lights_any_occupied"),
            "profile": state_of("sensor.living_lights_profile"),
            "tv": state_of("media_player.lg_tv"),
            "tv_playing": state_of("binary_sensor.living_lights_tv_playing"),
            "tv_watching": state_of(TV_WATCHING),
            "lights_on": lit, "switches_on": switches_on}

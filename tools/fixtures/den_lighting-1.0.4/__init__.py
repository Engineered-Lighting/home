"""One HA-local automatic writer; ordinary HA light controls remain direct."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
import math

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.core import Context, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .policy import Controller, LIGHTS, Scene
from .readback import read_scene

DOMAIN = "den_lighting"
OCCUPANCY = "binary_sensor.den_oak_person_occupancy"
INCUMBENT = "automation.den_lights_follow_person_occupancy"
_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({vol.Required(DOMAIN): vol.Schema({
    vol.Required("gaming_entity"): cv.entity_id,
})}, extra=vol.ALLOW_EXTRA)


def scene_from_state(state):
    if state is None or state.state not in {"on", "off"}:
        return None
    a = state.attributes
    b, k = a.get("brightness"), a.get("color_temp_kelvin")
    return Scene(state.state == "on", int(b) if isinstance(b, (int, float)) else None,
                 int(k) if isinstance(k, (int, float)) else None)


def parse_sample(raw, now):
    """Accept only a compact timestamped status; never infer idle from an error."""
    try:
        if not isinstance(raw, str) or len(raw) > 255:
            return "unknown", now
        v = json.loads(raw)
        if set(v) != {"status", "ts"} or v["status"] not in {"playing", "idle", "unknown"}:
            return "unknown", now
        ts = v["ts"]
        if type(ts) not in (float, int) or not math.isfinite(ts) or not -5 <= now - ts <= 45:
            return "unknown", now
        return v["status"], ts
    except (TypeError, ValueError):
        return "unknown", now


def command_scene(service, data, current):
    """Predict a user's full target before automatic commands can be dispatched."""
    on = service != "turn_off" and (not current.on if service == "toggle" else True)
    b, k = current.brightness, current.kelvin
    if "brightness_pct" in data:
        b = round(float(data["brightness_pct"]) * 255 / 100)
    elif "brightness" in data:
        b = int(data["brightness"])
    elif "brightness_step_pct" in data:
        b = round((b or 0) + float(data["brightness_step_pct"]) * 255 / 100)
    elif "brightness_step" in data:
        b = (b or 0) + int(data["brightness_step"])
    if b is not None:
        b = max(0, min(255, b))
        if b == 0 and any(key.startswith("brightness") for key in data):
            on = False
    if "color_temp_kelvin" in data:
        k = int(data["color_temp_kelvin"])
    elif data.get("color_temp"):
        k = round(1e6 / float(data["color_temp"]))
    return Scene(on, b, k)


def confirmed_match(target, actual):
    """Compare physical completion at Zigbee resolution, preserving one-step touches."""
    return (actual is not None and target.on == actual.on and
            (not target.on or ((target.brightness is None or actual.brightness == min(254, target.brightness)) and
             (target.kelvin is None or actual.kelvin is not None and
              round(1e6 / target.kelvin) == round(1e6 / actual.kelvin)))))


class DenRuntime:
    def __init__(self, hass, gaming_entity, controller, store, clock=time.time):
        self.hass, self.gaming_entity = hass, gaming_entity
        self.policy, self.store, self.clock = controller, store, clock
        self._listeners = set()
        self._unsub = []
        self._own = {}  # explicit context -> (fixture, target, expiry, revision)
        self._external = {}  # fixture -> (context, deadline, strong requested target)
        self._failures = {}
        self._repairs = set()
        self._readback = {}
        self._provisional = {}
        self._verify_tasks = {}
        self._read_limit = asyncio.Semaphore(2)
        self._task = None
        self._dirty = False
        self._stopping = False
        self._saved = None
        self._occupancy_source = "unqualified"
        self._occupancy_from_count = False
        self._frigate_available = None
        self._broker_available = None
        self.error = None

    def listen(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def notify(self):
        if self._stopping:
            return
        data = self.policy.dump()
        if data != self._saved:
            self._saved = data
            self.store.async_delay_save(self.policy.dump, 1)
        for listener in tuple(self._listeners):
            listener()

    @callback
    def wake(self):
        self.notify()
        if self._stopping:
            return
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_task(self._cycle())

    async def start(self):
        now = self.clock()
        for e in LIGHTS:
            self._observe(e, self.hass.states.get(e), now)
        self._occupancy_from_count = False
        # Frigate's HA occupancy entity defaults OFF after an HA restart. Its
        # unchanged, non-retained count may not arrive again for a stationary
        # person, so that initial OFF is not evidence of an empty room.
        value = self._occupancy_value()
        self._set_occupancy("on" if value == "on" else "unknown",
                            "ha_positive" if value == "on" else "unqualified")
        state = self.hass.states.get(self.gaming_entity)
        if state:
            self.policy.gaming_sample(*parse_sample(state.state, now), now)
        self._unsub.append(async_track_state_change_event(
            self.hass, list(LIGHTS) + [OCCUPANCY, self.gaming_entity, INCUMBENT], self.state_changed))
        self._unsub.append(self.hass.bus.async_listen("call_service", self.service_called))
        self._unsub.append(async_track_time_interval(self.hass, self.tick, timedelta(seconds=2)))
        self._unsub.append(mqtt.async_subscribe_connection_status(self.hass, self.mqtt_connection_changed))
        self._unsub.append(await mqtt.async_subscribe(self.hass, "frigate/available", self.mqtt_availability, qos=0))
        self._unsub.append(await mqtt.async_subscribe(self.hass, "frigate/den_oak/person", self.mqtt_count, qos=0))
        # The one-time listener removes itself before invoking stop; adding its
        # unsubscribe callback below would remove it twice during shutdown.
        self.hass.bus.async_listen_once("homeassistant_stop", self.stop)
        self.wake()

    def _occupancy_value(self):
        state = self.hass.states.get(OCCUPANCY)
        return state.state if state is not None else "unknown"

    def _set_occupancy(self, value, source):
        self._occupancy_source = source
        self.policy.set_occupancy(value, self.clock())

    def _observe(self, e, state, now):
        scene = scene_from_state(state)
        if scene:
            self.policy.observe(e, scene, now)
            minimum = state.attributes.get("min_color_temp_kelvin")
            if isinstance(minimum, (int, float)):
                self.policy.update_warmest(e, int(minimum))
        return scene

    def _own_record(self, context, now):
        for key in (getattr(context, "id", None), getattr(context, "parent_id", None)):
            record = self._own.get(key)
            if record and record[2] >= now:
                return record
        return None

    @callback
    def state_changed(self, event):
        e, state = event.data["entity_id"], event.data.get("new_state")
        now = self.clock()
        if e == OCCUPANCY:
            value = state.state if state else "unknown"
            if value not in {"on", "off"}:
                self._occupancy_from_count = False
                self._set_occupancy("unknown", "ha_unavailable")
            elif (value == "on" and not self._occupancy_from_count
                  and self._frigate_available is not False and self._broker_available is not False):
                self._set_occupancy("on", "ha_positive")
            # Known HA values must not overwrite an authoritative raw count.
            # In particular, OFF requires a new non-retained MQTT zero.
        elif e == self.gaming_entity:
            self.policy.gaming_sample(*parse_sample(state.state if state else "", now), now)
        elif e in LIGHTS:
            scene = self._observe(e, state, now)
            old = scene_from_state(event.data.get("old_state"))
            if scene is None or old is None or scene == old:
                self.wake()
                return
            own = self._own_record(state.context, now)
            # Exact requested acknowledgements may lose their context in a device
            # report. Only exact targets qualify; never an entire ramp envelope.
            if own is None:
                own = next((r for r in reversed(tuple(self._own.values()))
                            if r[0] == e and r[4] >= now and r[1].matches(scene)
                            and (not scene.on or r[1].brightness is None or r[1].brightness == scene.brightness)), None)
            external = self._external.get(e)
            if own:
                hold = self.policy.holds.get(e)
                repair_key = (e, hold.since) if hold else None
                if hold and e not in self._provisional and not hold.scene.matches(scene) and repair_key not in self._repairs:
                    self._repairs.add(repair_key)
                    self.policy.command_failed(e)
            elif external and now <= external[1]:
                # Commands with explicit target values hold that target through
                # intermediate device reports. Scenes without inner light calls
                # acquire their final full state from their context's reports.
                if not external[2]:
                    self.policy.manual(e, scene, now)
            else:
                phase = self._readback.get(e) or next((r for r in reversed(tuple(self._own.values()))
                                                      if r[0] == e and r[4] >= now), None)
                if phase:
                    # Pause this fixture without learning a device's intermediate
                    # fade report as a new normal preference. Uncached completion
                    # readback resolves the provisional hold; explicit commands
                    # cancel this path immediately and remain authoritative.
                    if e not in self.policy.holds or e in self._provisional:
                        self.policy.manual(e, scene, now, update_normal=False)
                        self._provisional[e] = self.policy.holds[e]
                else:
                    self.policy.manual(e, scene, now)
        self.wake()

    def _resolve(self, domain, service, data, context):
        if data.get("entity_id") == "all":
            return set(LIGHTS)
        call = ServiceCall(self.hass, domain, service, data, context=context)
        try:
            # HA2026.10 moved target expansion out of helpers.service.
            from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
            found = async_extract_referenced_entity_ids(self.hass, TargetSelection(data), True)
        except ImportError:
            from homeassistant.helpers.service import async_extract_referenced_entity_ids
            found = async_extract_referenced_entity_ids(self.hass, call, True)
        ids = found.referenced | found.indirectly_referenced
        if domain == "scene":
            expanded = set()
            for e in ids:
                state = self.hass.states.get(e)
                if state:
                    expanded.update(state.attributes.get("entity_id", []))
            ids |= expanded
        return ids & set(LIGHTS)

    @callback
    def service_called(self, event):
        now = self.clock()
        if self._own_record(event.context, now):
            return
        domain, service = event.data.get("domain"), event.data.get("service")
        data = event.data.get("service_data") or {}
        if domain not in {"light", "homeassistant", "scene"} or service not in {"turn_on", "turn_off", "toggle", "apply"}:
            return
        try:
            if domain == "scene" and service == "apply":
                entities = set(data.get("entities", {})) & set(LIGHTS)
            else:
                entities = self._resolve(domain, service, data, event.context)
            for e in entities:
                current = self.policy.observed.get(e)
                if current is None:
                    continue
                detail = data
                action = service
                strong = domain != "scene"
                if domain == "scene" and service == "apply":
                    detail = data["entities"][e]
                    if isinstance(detail, str):
                        detail = {"state": detail}
                    action = "turn_off" if detail.get("state") == "off" else "turn_on"
                    strong = True
                target = command_scene(action, detail, current) if strong else current
                self._provisional.pop(e, None)
                self._readback.pop(e, None)
                self.policy.manual(e, target, now)
                self._external[e] = (event.context.id, now + float(detail.get("transition", 2)) + 3, strong)
                self._failures.pop(e, None)
            if entities:
                self.wake()
        except (ValueError, TypeError, KeyError, AttributeError):
            _LOGGER.warning("Unable to resolve Den command; subsequent state changes remain protected")

    @callback
    def mqtt_zero(self, message):
        # Retain the old internal entry point for existing diagnostics.
        self.mqtt_count(message)

    @callback
    def mqtt_count(self, message):
        # Total counts include stationary people. No time-based expiry is used:
        # Frigate publishes them on changes, rather than as a periodic sample.
        if message.retain or self._frigate_available is False or self._broker_available is False:
            return
        payload = message.payload
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("ascii")
            except UnicodeDecodeError:
                return
        if not isinstance(payload, str):
            return
        payload = payload.strip()
        if not payload or len(payload) > 32 or not payload.isascii() or not payload.isdecimal():
            return
        self._occupancy_from_count = True
        self._set_occupancy("on" if int(payload) > 0 else "off", "raw_person_count")
        self.wake()

    @callback
    def mqtt_availability(self, message):
        payload = message.payload
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("ascii")
            except UnicodeDecodeError:
                payload = "unknown"
        available = isinstance(payload, str) and payload.strip() == "online"
        if not available:
            self._occupancy_from_count = False
            self._set_occupancy("unknown", "frigate_unavailable")
        elif self._frigate_available is False:
            self._set_occupancy("unknown", "unqualified")
        self._frigate_available = available
        self.wake()

    @callback
    def mqtt_connection_changed(self, connected):
        if not connected or self._broker_available is False:
            self._occupancy_from_count = False
            self._set_occupancy("unknown", "unqualified" if connected else "broker_unavailable")
        self._broker_available = bool(connected)
        self.wake()

    @callback
    def tick(self, _now=None):
        now = self.clock()
        self._own = {k: v for k, v in self._own.items() if v[2] >= now}
        self._external = {k: v for k, v in self._external.items() if v[1] >= now}
        self._repairs = {key for key in self._repairs if key[0] in self.policy.holds and self.policy.holds[key[0]].since == key[1]}
        for e, record in tuple(self._readback.items()):
            if now >= record[1] and (e not in self._verify_tasks or self._verify_tasks[e].done()):
                self._verify_tasks[e] = self.hass.async_create_task(self._verify_readback(e, record))
        self.wake()

    async def _refresh_scene(self, entity_id):
        async with self._read_limit:
            return await read_scene(self.hass, entity_id)

    async def _verify_readback(self, e, record):
        target, deadline, revision = record
        try:
            actual = await self._refresh_scene(e)
        except HomeAssistantError:
            if self._readback.get(e) == record:
                self._readback.pop(e)
                self.error = "light_readback_failed:" + e
                # Ambiguous changes stay protected; no speculative correction.
                if e not in self._provisional:
                    self.policy.command_failed(e)
            self.wake()
            return
        if self._stopping or self._readback.get(e) != record:
            return  # a newer explicit command canceled this verification
        self._readback.pop(e)
        self.policy.observe(e, actual, self.clock())
        provisional = self._provisional.pop(e, None)
        if provisional is not None and self.policy.holds.get(e) is provisional:
            if confirmed_match(target, actual):
                self.policy.resume([e], self.clock())
            else:
                self.policy.manual(e, actual, self.clock())
        if confirmed_match(target, actual):
            self._failures.pop(e, None)
            if self.error and self.error.endswith(":" + e):
                self.error = None
        elif self.policy.desired(e, self.clock()) == target:
            self.error = "light_readback_mismatch:" + e
            self.policy.command_failed(e)
        self.wake()

    def owner_clear(self):
        s = self.hass.states.get(INCUMBENT)
        return s is not None and s.state == "off"

    async def set_enabled(self, value):
        if value and not self.owner_clear():
            raise HomeAssistantError("Disable the incumbent Den occupancy automation before enabling this controller")
        self.policy.set_enabled(value, self.clock())
        await self.store.async_save(self.policy.dump())
        self.wake()

    async def set_gaming_enabled(self, value):
        if value:
            sample = self.hass.states.get(self.gaming_entity)
            status, _ = parse_sample(sample.state if sample else "", self.clock())
            if status == "unknown":
                raise HomeAssistantError("A fresh HASS.Agent gaming sample is required")
        self.policy.set_gaming_enabled(value, self.clock())
        await self.store.async_save(self.policy.dump())
        self.wake()

    async def resume(self, entity_ids=None):
        if entity_ids is not None and not set(entity_ids) <= set(LIGHTS):
            raise HomeAssistantError("Only the four Victoria Den fixtures can be resumed")
        for e in entity_ids if entity_ids is not None else LIGHTS:
            self._provisional.pop(e, None)
            self._readback.pop(e, None)
        self.policy.resume(entity_ids, self.clock())
        self._failures.clear()
        for e in entity_ids if entity_ids is not None else LIGHTS:
            target = self.policy.desired(e, self.clock())
            if self.policy.enabled and target is not None and target.matches(self.policy.observed.get(e)):
                self._readback[e] = (target, self.clock(), self.policy.revision)
        self.tick()

    async def _cycle(self):
        while self._dirty and not self._stopping:
            self._dirty = False
            if self.policy.enabled and not self.owner_clear():
                self.error = "incumbent_automation_not_disabled"
                self.notify()
                continue
            for cmd in self.policy.reconcile(self.clock()):
                state = self.hass.states.get(cmd.entity_id)
                if (not self.policy.enabled or not self.owner_clear() or cmd.revision != self.policy.revision
                        or self.policy.desired(cmd.entity_id, self.clock()) != cmd.target
                        or scene_from_state(state) is None):
                    self.policy.command_failed(cmd.entity_id)
                    continue
                signature = (cmd.target, cmd.revision)
                previous, count = self._failures.get(cmd.entity_id, (None, 0))
                if previous == signature and count >= 2:
                    continue
                context = Context()
                self._own[context.id] = (cmd.entity_id, cmd.target, self.clock() + cmd.transition + 30,
                                         cmd.revision, self.clock() + cmd.transition + 3)
                data = {"entity_id": cmd.entity_id, "transition": cmd.transition}
                if cmd.target.on:
                    if cmd.target.brightness is not None:
                        data["brightness"] = cmd.target.brightness
                    if cmd.target.kelvin is not None:
                        data["color_temp_kelvin"] = cmd.target.kelvin
                try:
                    await self.hass.services.async_call("light", "turn_on" if cmd.target.on else "turn_off",
                                                        data, blocking=True, context=context)
                    self.error = None
                    count = count + 1 if previous == signature else 1
                    self._failures[cmd.entity_id] = (signature, count)
                    self._readback[cmd.entity_id] = (cmd.target, self.clock() + cmd.transition + 3, cmd.revision)
                    self.hass.bus.async_fire("den_lighting_command", {
                        "entity_id": cmd.entity_id, "revision": cmd.revision,
                        "target": {"on": cmd.target.on, "brightness": cmd.target.brightness, "kelvin": cmd.target.kelvin},
                        "context_id": context.id})
                except HomeAssistantError:
                    count = count + 1 if previous == signature else 1
                    self._failures[cmd.entity_id] = (signature, count)
                    self.error = "light_command_failed:" + cmd.entity_id
                    self.policy.command_failed(cmd.entity_id)
                    _LOGGER.warning("Den light command failed for %s (attempt %s/2)", cmd.entity_id, count)
            self.notify()

    async def stop(self, _event=None):
        if self._stopping:
            return
        self._stopping = True
        for unsub in self._unsub:
            unsub()
        if self._task and not self._task.done():
            self._task.cancel()
        for task in self._verify_tasks.values():
            if not task.done():
                task.cancel()
        await self.store.async_save(self.policy.dump())

    def status(self):
        return {**self.policy.status(self.clock()), "error": self.error, "gaming_entity": self.gaming_entity,
                "occupancy_source": self._occupancy_source,
                "pending_fixtures": list(self._readback), "provisional_fixtures": list(self._provisional)}


async def async_setup(hass, config):
    store = Store(hass, 1, DOMAIN + ".state")
    try:
        policy = Controller.restore(await store.async_load())
    except (ValueError, KeyError, TypeError):
        _LOGGER.error("Den lighting storage is invalid; refusing to enable automatic control")
        return False
    runtime = DenRuntime(hass, config[DOMAIN]["gaming_entity"], policy, store)
    hass.data[DOMAIN] = runtime

    async def resume(call):
        await runtime.resume(call.data.get("entity_id"))

    hass.services.async_register(DOMAIN, "resume_automatic", resume,
        schema=vol.Schema({vol.Optional("entity_id"): vol.All(cv.ensure_list, [vol.In(LIGHTS)])}))
    await async_load_platform(hass, "switch", DOMAIN, {}, config)
    await async_load_platform(hass, "sensor", DOMAIN, {}, config)
    await runtime.start()
    return True

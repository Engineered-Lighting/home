"""Actual HA event bus/services/target expansion, with simulated bulb IO only.

Run with python3.14 (the workstation HA dependency runtime), not Python3.12.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.mqtt.const import MQTT_CONNECTION_STATE
from homeassistant.helpers import area_registry, entity_registry, device_registry
from homeassistant.helpers.dispatcher import async_dispatcher_send
from den_lighting import DenRuntime, INCUMBENT, OCCUPANCY, command_scene, parse_sample, scene_from_state
from den_lighting.policy import Controller, LIGHTS, Scene


class Store:
    def __init__(self):
        self.data = None

    def async_delay_save(self, fn, delay):
        self.data = fn()

    async def async_save(self, data):
        self.data = data


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.now = 1000
        self.policy = Controller()
        self.runtime = DenRuntime(self.hass, "sensor.test_gaming", self.policy, Store(), lambda: self.now)
        async def physical_readback(e):
            return scene_from_state(self.hass.states.get(e))
        self.runtime._refresh_scene = physical_readback
        self.calls = []
        self.hass.states.async_set(INCUMBENT, "off")
        self.hass.states.async_set(OCCUPANCY, "on")
        for e in LIGHTS:
            self.hass.states.async_set(e, "on", {"brightness": 254, "color_temp_kelvin": 3000})
            self.policy.observe(e, Scene(True, 254, 3000), self.now)
        self.policy.set_occupancy("on", self.now)
        self.policy.set_enabled(True, self.now)
        self.policy.set_gaming_enabled(True, self.now)
        self.unsub = [self.hass.bus.async_listen("call_service", self.runtime.service_called),
                      self.hass.bus.async_listen("state_changed", self.filter_state)]
        self.hass.services.async_register("light", "turn_on", self.light)
        self.hass.services.async_register("light", "turn_off", self.light)

    async def asyncTearDown(self):
        for unsub in self.unsub:
            unsub()
        await self.runtime.stop()
        await self.hass.async_stop()
        self.tmp.cleanup()

    @callback
    def filter_state(self, event):
        if event.data["entity_id"] in LIGHTS + (OCCUPANCY, "sensor.test_gaming", INCUMBENT):
            self.runtime.state_changed(event)

    async def light(self, call):
        self.calls.append(call)
        ids = self.runtime._resolve("light", call.service, call.data, call.context)
        for e in ids:
            state = self.hass.states.get(e)
            attrs = dict(state.attributes)
            if "brightness" in call.data:
                attrs["brightness"] = call.data["brightness"]
            if "brightness_pct" in call.data:
                attrs["brightness"] = round(call.data["brightness_pct"] * 255 / 100)
            if "color_temp_kelvin" in call.data:
                attrs["color_temp_kelvin"] = call.data["color_temp_kelvin"]
            self.hass.states.async_set(e, "on" if call.service == "turn_on" else "off", attrs, context=call.context)

    async def drain(self):
        await asyncio.wait_for(self.hass.async_block_till_done(), 5)

    async def play(self):
        self.policy.gaming_sample("playing", 1000, 1000)
        self.now = 1010
        self.policy.gaming_sample("playing", 1010, 1010)
        self.runtime.wake()
        await self.drain()

    async def start_with_occupancy(self, occupancy, *, lights_on=True):
        # Replace the test's preinstalled event listeners with start()'s real
        # listeners, and present initial states before that startup boundary.
        for unsub in self.unsub:
            unsub()
        self.unsub = []
        self.hass.states.async_set(OCCUPANCY, occupancy)
        for e in LIGHTS:
            self.hass.states.async_set(e, "on" if lights_on else "off",
                                       {"brightness": 254, "color_temp_kelvin": 3000} if lights_on else {})
        await self.drain()
        self.mqtt_handlers = {}

        async def subscribe(hass, topic, message_callback, **kwargs):
            self.mqtt_handlers[topic] = message_callback
            return lambda: None

        with patch("den_lighting.mqtt.async_subscribe", side_effect=subscribe):
            await self.runtime.start()
        await self.drain()

    async def receive_mqtt(self, topic, payload, *, retain=False):
        self.mqtt_handlers[topic](types.SimpleNamespace(topic=topic, payload=payload, retain=retain))
        await self.drain()

    async def test_real_service_ack_does_not_become_manual_hold(self):
        await self.play()
        self.assertEqual(len(self.calls), 4)
        self.assertFalse(self.policy.holds)
        self.assertTrue(all(self.hass.states.get(e).attributes["brightness"] == 38 for e in LIGHTS))

    async def test_manual_slider_burst_sticks_and_other_fixtures_stay_dim(self):
        await self.play()
        for v in (39, 120, 153):
            self.now += 1
            await self.hass.services.async_call("light", "turn_on", {"entity_id": LIGHTS[0], "brightness": v},
                                                 blocking=True, context=Context(user_id="owner"))
            await self.drain()
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.policy.holds[LIGHTS[0]].scene.brightness, 153)
        self.assertEqual(self.hass.states.get(LIGHTS[0]).attributes["brightness"], 153)
        self.assertEqual(len(self.calls), 7)
        self.assertTrue(all(self.hass.states.get(e).attributes["brightness"] == 38 for e in LIGHTS[1:]))

    async def test_physical_unattributed_change_is_held(self):
        await self.play()
        self.now += 10
        self.hass.states.async_set(LIGHTS[0], "off", {}, context=Context())
        await self.drain()
        self.assertFalse(self.policy.holds[LIGHTS[0]].scene.on)
        self.assertEqual(self.hass.states.get(LIGHTS[0]).state, "off")

    async def test_area_target_arms_all_four_holds(self):
        await area_registry.async_get(self.hass).async_load()
        await entity_registry.async_get(self.hass).async_load()
        devices = device_registry.async_get(self.hass)
        devices.async_setup()
        await devices.async_load()
        area = area_registry.async_get(self.hass).async_create("Den")
        registry = entity_registry.async_get(self.hass)
        for e in LIGHTS:
            old = self.hass.states.get(e)
            self.hass.states.async_remove(e)
            entry = registry.async_get_or_create("light", "test", e, suggested_object_id=e.split(".")[1])
            registry.async_update_entity(entry.entity_id, area_id=area.id)
            self.assertEqual(entry.entity_id, e)
            self.hass.states.async_set(e, old.state, dict(old.attributes))
        await self.play()
        await self.hass.services.async_call("light", "turn_on", {"area_id": area.id, "brightness": 100},
                                             blocking=True, context=Context(user_id="owner"))
        await self.drain()
        self.assertEqual(set(self.policy.holds), set(LIGHTS))
        self.assertTrue(all(h.scene.brightness == 100 for h in self.policy.holds.values()))

    async def test_scene_apply_holds_selected_only(self):
        await self.play()
        context = Context(user_id="owner")
        self.runtime.service_called(types.SimpleNamespace(context=context, data={
            "domain": "scene", "service": "apply", "service_data": {"entities": {
                LIGHTS[0]: {"state": "on", "brightness": 150, "color_temp_kelvin": 2500},
                LIGHTS[1]: "off"}}}))
        await self.drain()
        self.assertEqual(set(self.policy.holds), set(LIGHTS[:2]))
        self.assertEqual(self.policy.holds[LIGHTS[0]].scene, Scene(True, 150, 2500))
        self.assertFalse(self.policy.holds[LIGHTS[1]].scene.on)

    async def test_late_old_automatic_report_cannot_replace_manual_target(self):
        await self.play()
        own = self.calls[0].context
        self.now += 1
        await self.hass.services.async_call("light", "turn_on", {"entity_id": LIGHTS[0], "brightness": 153},
                                             blocking=True, context=Context(user_id="owner"))
        await self.drain()
        self.hass.states.async_set(LIGHTS[0], "on", {"brightness": 38, "color_temp_kelvin": 1000}, context=own)
        await self.drain()
        self.assertEqual(self.policy.holds[LIGHTS[0]].scene.brightness, 153)
        self.assertEqual(self.hass.states.get(LIGHTS[0]).attributes["brightness"], 153)

    async def test_manual_during_dispatch_invalidates_remaining_queued_commands(self):
        fired = False
        original = self.light

        async def interrupt(call):
            nonlocal fired
            await original(call)
            if not fired:
                fired = True
                self.runtime.service_called(types.SimpleNamespace(context=Context(user_id="owner"), data={
                    "domain": "light", "service": "turn_on", "service_data": {"entity_id": LIGHTS[1], "brightness": 160}}))
        self.hass.services.async_register("light", "turn_on", interrupt)
        await self.play()
        self.assertEqual(self.policy.holds[LIGHTS[1]].scene.brightness, 160)
        self.assertFalse(any(c.data.get("entity_id") == LIGHTS[1] and c.data.get("brightness") == 38 for c in self.calls))

    async def test_incumbent_interlock_blocks_activation_and_writes(self):
        self.hass.states.async_set(INCUMBENT, "on")
        with self.assertRaises(HomeAssistantError):
            await self.runtime.set_enabled(True)
        await self.play()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.runtime.error, "incumbent_automation_not_disabled")

    async def test_light_service_failures_are_bounded_to_two(self):
        async def fail(call):
            self.calls.append(call)
            raise HomeAssistantError("simulated failure")
        self.hass.services.async_register("light", "turn_on", fail)
        await self.play()
        for _ in range(5):
            self.runtime.tick()
            await self.drain()
        self.assertEqual(len(self.calls), 8)
        self.assertTrue(self.runtime.error.startswith("light_command_failed:"))

    async def test_retained_mqtt_zero_never_establishes_vacancy(self):
        self.runtime.mqtt_zero(types.SimpleNamespace(retain=True, payload="0"))
        self.assertEqual(self.policy.occupancy, "on")

    async def test_startup_default_off_does_not_establish_vacancy(self):
        # Frigate's HA entity initializes OFF after HA restarts, even while
        # Frigate retains a stationary person and emits no changed MQTT count.
        await self.start_with_occupancy("off")
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.policy.occupancy, "unknown")
        self.assertTrue(all(self.hass.states.get(e).state == "on" for e in LIGHTS))

    async def test_startup_ha_on_is_positive_presence(self):
        await self.start_with_occupancy("on")
        self.assertEqual(self.policy.occupancy, "on")
        self.assertEqual(self.runtime.status()["occupancy_source"], "ha_positive")
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])

    async def test_fresh_mqtt_zero_qualifies_full_vacancy_grace(self):
        await self.start_with_occupancy("off")
        self.now = 1050
        await self.receive_mqtt("frigate/den_oak/person", "0")
        self.assertEqual(self.policy.occupancy, "off")
        self.now = 1100
        await self.receive_mqtt("frigate/den_oak/person", "0")
        self.now = 1169
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])
        self.now = 1170
        self.runtime.tick()
        await self.drain()
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call.service == "turn_off" for call in self.calls))

    async def test_raw_person_count_restores_presence_despite_stale_ha_off(self):
        await self.start_with_occupancy("off", lights_on=False)
        await self.receive_mqtt("frigate/den_oak/person", "1")
        self.assertEqual(self.policy.occupancy, "on")
        self.assertEqual(self.runtime.status()["occupancy_source"], "raw_person_count")
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call.service == "turn_on" for call in self.calls))
        # An HA OFF attribute update is still not a new vacancy observation.
        self.hass.states.async_set(OCCUPANCY, "off", {"friendly_name": "Den occupancy"})
        await self.drain()
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.policy.occupancy, "on")
        self.assertEqual(len(self.calls), 4)

    async def test_retained_or_malformed_counts_never_qualify_startup(self):
        await self.start_with_occupancy("off")
        for payload in ("0", "1"):
            await self.receive_mqtt("frigate/den_oak/person", payload, retain=True)
        for payload in ("", "-1", "+1", "1.0", "NaN", "{}", "true", "\u0661", b"\xff", "1" * 40):
            await self.receive_mqtt("frigate/den_oak/person", payload)
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.policy.occupancy, "unknown")
        self.assertEqual(self.calls, [])

    async def test_raw_zero_is_authoritative_despite_stale_ha_on(self):
        await self.start_with_occupancy("on")
        await self.receive_mqtt("frigate/den_oak/person", "0")
        self.hass.states.async_set(OCCUPANCY, "on", {"friendly_name": "Den occupancy"})
        await self.drain()
        self.assertEqual(self.policy.occupancy, "off")
        self.now += 120
        self.runtime.tick()
        await self.drain()
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call.service == "turn_off" for call in self.calls))

    async def test_frigate_offline_invalidates_occupancy_until_fresh_count(self):
        await self.start_with_occupancy("on")
        await self.receive_mqtt("frigate/den_oak/person", "1")
        await self.receive_mqtt("frigate/available", "offline", retain=True)
        self.assertEqual(self.policy.occupancy, "unknown")
        for payload in ("0", "1"):
            await self.receive_mqtt("frigate/den_oak/person", payload)
            self.assertEqual(self.policy.occupancy, "unknown")
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])
        await self.receive_mqtt("frigate/available", "online", retain=True)
        self.assertEqual(self.policy.occupancy, "unknown")
        await self.receive_mqtt("frigate/den_oak/person", b"1")
        self.assertEqual(self.policy.occupancy, "on")

    async def test_broker_disconnect_invalidates_occupancy(self):
        await self.start_with_occupancy("on")
        async_dispatcher_send(self.hass, MQTT_CONNECTION_STATE, False)
        await self.drain()
        self.assertEqual(self.policy.occupancy, "unknown")
        await self.receive_mqtt("frigate/den_oak/person", "0")
        self.assertEqual(self.policy.occupancy, "unknown")
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])
        async_dispatcher_send(self.hass, MQTT_CONNECTION_STATE, True)
        await self.drain()
        self.assertEqual(self.policy.occupancy, "unknown")
        await self.receive_mqtt("frigate/den_oak/person", "1")
        self.assertEqual(self.policy.occupancy, "on")

    async def test_outage_restarts_vacancy_grace_from_fresh_zero(self):
        await self.start_with_occupancy("off")
        await self.receive_mqtt("frigate/den_oak/person", "0")
        self.now += 119
        await self.receive_mqtt("frigate/available", "offline")
        self.now += 10
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])
        await self.receive_mqtt("frigate/available", "online")
        await self.receive_mqtt("frigate/den_oak/person", "0")
        self.now += 119
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])
        self.now += 1
        self.runtime.tick()
        await self.drain()
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call.service == "turn_off" for call in self.calls))

    async def test_ha_unavailable_invalidates_qualified_occupancy(self):
        await self.start_with_occupancy("off")
        await self.receive_mqtt("frigate/den_oak/person", "1")
        self.hass.states.async_set(OCCUPANCY, "unavailable")
        await self.drain()
        self.assertEqual(self.policy.occupancy, "unknown")
        self.hass.states.async_set(OCCUPANCY, "off")
        await self.drain()
        self.assertEqual(self.policy.occupancy, "unknown")
        self.now += 121
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.calls, [])

    async def test_successful_service_without_readback_is_bounded_and_visible(self):
        async def no_report(call):
            self.calls.append(call)
        self.hass.services.async_register("light", "turn_on", no_report)
        await self.play()
        for i in range(4):
            self.now += 6
            self.policy.gaming_sample("playing", self.now, self.now)
            self.runtime.tick()
            await self.drain()
        self.assertEqual(len(self.calls), 8)
        self.assertTrue(self.runtime.error.startswith("light_readback_mismatch:"))

    async def test_small_physical_change_after_automatic_ack_is_not_swallowed(self):
        await self.play()
        self.now += 1
        self.hass.states.async_set(LIGHTS[0], "on", {"brightness": 39, "color_temp_kelvin": 1000}, context=Context())
        await self.drain()
        self.assertEqual(self.policy.holds[LIGHTS[0]].scene.brightness, 39)

    async def test_contextless_intermediate_fade_report_cannot_poison_normal_scene(self):
        await self.play()
        # Real restore trigger; the actual service path writes254 optimistically,
        # then a contextless intermediate ZHA report arrives before fade finishes.
        for t in (1020, 1030, 1050):
            self.now = t
            self.policy.gaming_sample("idle", t, t)
        self.runtime.wake()
        await self.drain()
        self.now = 1051
        self.hass.states.async_set(LIGHTS[0], "on", {"brightness": 242, "color_temp_kelvin": 3000}, context=Context())
        await self.drain()
        # A transition report may pause the fixture provisionally, but it cannot
        # become the baseline that the next session restores.
        self.assertEqual(self.policy.normal[LIGHTS[0]].brightness, 254)

    async def test_provisional_fade_report_clears_only_after_confirmed_completion(self):
        await self.play()
        self.now = 1011
        self.hass.states.async_set(LIGHTS[0], "on", {"brightness": 39, "color_temp_kelvin": 1000}, context=Context())
        await self.drain()
        self.assertIn(LIGHTS[0], self.policy.holds)
        async def confirmed(e):
            return Scene(True, 38, self.policy.warmest[e])
        self.runtime._refresh_scene = confirmed
        self.now = 1016
        self.runtime.tick()
        await self.drain()
        self.assertNotIn(LIGHTS[0], self.policy.holds)
        self.assertFalse(self.runtime._provisional)

    async def test_real_physical_adjustment_after_fade_stays_held(self):
        await self.play()
        self.now = 1011
        self.hass.states.async_set(LIGHTS[0], "on", {"brightness": 39, "color_temp_kelvin": 1000}, context=Context())
        await self.drain()
        self.now = 1016
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.policy.holds[LIGHTS[0]].scene.brightness, 39)

    async def test_explicit_manual_command_cancels_provisional_verification(self):
        await self.play()
        self.now = 1011
        self.hass.states.async_set(LIGHTS[0], "on", {"brightness": 39, "color_temp_kelvin": 1000}, context=Context())
        await self.drain()
        await self.hass.services.async_call("light", "turn_on", {"entity_id": LIGHTS[0], "brightness": 153},
                                             blocking=True, context=Context(user_id="owner"))
        await self.drain()
        self.now = 1016
        self.runtime.tick()
        await self.drain()
        self.assertEqual(self.policy.holds[LIGHTS[0]].scene.brightness, 153)
        self.assertNotIn(LIGHTS[0], self.runtime._provisional)

    async def test_one_time_shutdown_listener_does_not_unsubscribe_itself_twice(self):
        with patch("den_lighting.mqtt.async_subscribe", new=AsyncMock(return_value=lambda: None)):
            await self.runtime.start()
        with self.assertNoLogs("homeassistant.core", level="ERROR"):
            self.hass.bus.async_fire("homeassistant_stop")
            await self.drain()
        self.assertTrue(self.runtime._stopping)
        self.assertIsNotNone(self.runtime.store.data)


class ParsingTests(unittest.TestCase):
    def test_invalid_samples_fail_unknown(self):
        for v in ("unavailable", "[]", "null", '{"status":"idle","ts":true}',
                  '{"status":"idle","ts":1}', '{"status":"idle","ts":NaN}',
                  '{"status":"idle","ts":9999999999}', '{"status":"idle","ts":1000,"extra":1}'):
            self.assertEqual(parse_sample(v, 1000)[0], "unknown", v)

    def test_brightness_and_warmth_command_target(self):
        self.assertEqual(command_scene("turn_on", {"brightness_pct": 15, "color_temp_kelvin": 2202}, Scene(False, 254, 3000)), Scene(True, 38, 2202))
        self.assertFalse(command_scene("turn_on", {"brightness": 0}, Scene(True, 100, 3000)).on)


if __name__ == "__main__":
    unittest.main()

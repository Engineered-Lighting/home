"""Fake HA lifecycle and pure MQTT event tests, not installed HA acceptance.

Only public API shapes from pinned HA 2026.9.3 are stubbed. No HA install,
broker, configured home, model or network connection is required.
"""
import asyncio
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / "ha-config/custom_components/shared_home_context"


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


class FakeSensor:
    def __init__(self):
        self.callbacks = []

    async def async_added_to_hass(self):
        self.callbacks = []
        self.writes = 0

    def async_on_remove(self, callback):
        self.callbacks.append(callback)

    def async_write_ha_state(self):
        self.writes += 1

    async def async_remove(self):
        while self.callbacks:
            self.callbacks.pop()()


class FakeBus:
    def __init__(self):
        self.listeners = []

    def async_listen_once(self, event, callback):
        item = (event, callback)
        self.listeners.append(item)

        def remove():
            if item in self.listeners:
                self.listeners.remove(item)
        return remove


@contextmanager
def component_modules():
    mqtt = module("homeassistant.components.mqtt")
    mqtt.active = {}
    mqtt.subscriptions = []
    mqtt.fail_topic = None
    mqtt.available = True
    mqtt.connected = True
    mqtt.wait_calls = 0
    mqtt.connection_callback = None
    mqtt.block_topic = None
    mqtt.block_entered = None

    async def wait_client(hass):
        mqtt.wait_calls += 1
        return mqtt.available
    mqtt.async_wait_for_mqtt_client = wait_client
    mqtt.is_connected = lambda hass: mqtt.connected

    def subscribe_connection(hass, callback):
        mqtt.connection_callback = callback
        return lambda: setattr(mqtt, "connection_callback", None)
    mqtt.async_subscribe_connection_status = subscribe_connection

    async def subscribe(hass, topic, callback, *, qos, encoding):
        assert qos == 0 and encoding is None
        assert mqtt.wait_calls > 0
        if topic == mqtt.block_topic:
            mqtt.block_entered.set()
            await asyncio.Event().wait()
        if topic == mqtt.fail_topic:
            raise RuntimeError("synthetic subscription failure")
        mqtt.subscriptions.append(topic)
        mqtt.active[topic] = callback
        return lambda: mqtt.active.pop(topic, None)
    mqtt.async_subscribe = subscribe
    event = module("homeassistant.helpers.event")
    event.timer = None

    def interval(hass, callback, duration, **kwargs):
        assert duration.total_seconds() == 1
        assert kwargs["cancel_on_shutdown"] is True
        event.timer = callback
        return lambda: setattr(event, "timer", None)
    event.async_track_time_interval = interval
    discovery = module("homeassistant.helpers.discovery")
    discovery.calls = []

    async def load(hass, platform, domain, info, config):
        discovery.calls.append((platform, domain, info))
    discovery.async_load_platform = load
    # Schema syntax is inert in these lifecycle tests. Runtime async_setup also
    # enforces disabled-by-default and exact pilot identity independently.
    vol = module("voluptuous", Schema=lambda schema, **kwargs: schema,
                 Optional=lambda name, **kwargs: name, In=lambda items: items, ALLOW_EXTRA=object())
    cv = module("homeassistant.helpers.config_validation", boolean=bool)
    stubs = {
        "voluptuous": vol,
        "homeassistant": module("homeassistant"),
        "homeassistant.components": module("homeassistant.components", mqtt=mqtt),
        "homeassistant.components.mqtt": mqtt,
        "homeassistant.components.sensor": module("homeassistant.components.sensor", SensorEntity=FakeSensor),
        "homeassistant.const": module("homeassistant.const", EVENT_HOMEASSISTANT_STOP="homeassistant_stop"),
        "homeassistant.core": module("homeassistant.core", callback=lambda function: function),
        "homeassistant.exceptions": module("homeassistant.exceptions", PlatformNotReady=RuntimeError),
        "homeassistant.helpers": module("homeassistant.helpers", config_validation=cv),
        "homeassistant.helpers.config_validation": cv,
        "homeassistant.helpers.discovery": discovery,
        "homeassistant.helpers.entity": module("homeassistant.helpers.entity", EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic")),
        "homeassistant.helpers.event": event,
    }
    package_name = "_shared_home_context_test"
    with patch.dict(sys.modules, stubs):
        try:
            spec = importlib.util.spec_from_file_location(package_name, COMPONENT / "__init__.py",
                                                         submodule_search_locations=[str(COMPONENT)])
            component = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = component
            spec.loader.exec_module(component)
            sensor_spec = importlib.util.spec_from_file_location(package_name + ".sensor", COMPONENT / "sensor.py")
            sensor = importlib.util.module_from_spec(sensor_spec)
            sys.modules[package_name + ".sensor"] = sensor
            sensor_spec.loader.exec_module(sensor)
            yield component, sensor, mqtt, event, discovery
        finally:
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    sys.modules.pop(name, None)


def availability(session="adapter-1", generation=2, online=True):
    return {"site_id": "victoria", "camera_id": "den_oak", "adapter_session": session,
            "occupancy_generation": generation, "online": online}


def observation(**changes):
    value = {"request_id": "request-1", "site_id": "victoria", "device_id": "den-adapter",
             "camera_id": "den_oak", "room_id": "den", "adapter_session": "adapter-1", "sequence": 1,
             "occupancy_generation": 2, "capture_session": "camera-1", "frame_id": "42",
             "observed_at": 1000, "valid_until": 1180, "uncertainty_seconds": .2,
             "description": "A person is seated.", "authority": "untrusted_observation", "status": "ok",
             "model_revision": "a" * 64, "configuration_revision": "rev-1"}
    value.update(changes)
    return value


class GeneratedConsumerTests(unittest.TestCase):
    def test_vendored_consumer_exactly_matches_canonical_ast_extraction(self):
        spec = importlib.util.spec_from_file_location("build_ha_consumer", ROOT / "tools/shared-home/build_ha_consumer.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        self.assertEqual((COMPONENT / "consumer.py").read_text(encoding="utf8"), generator.render_consumer())

    def test_manifest_has_no_extra_runtime_dependencies(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf8"))
        self.assertEqual(manifest["dependencies"], ["mqtt"])
        self.assertEqual(manifest["requirements"], [])


class BridgeTests(unittest.TestCase):
    def setUp(self):
        context = component_modules()
        self.component, self.sensor, self.mqtt, self.event, self.discovery = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.bridge = self.sensor.ObservationBridge()

    def receive(self, topic, value, *, unix=1000, mono=0, retained=False):
        payload = value if isinstance(value, bytes) else json.dumps(value).encode()
        return self.bridge.receive(topic, payload, retained=retained, now_unix=unix, now_mono=mono)

    def ready(self):
        self.assertTrue(self.receive(self.sensor.AVAILABILITY_TOPIC, availability()))
        self.assertTrue(self.receive(self.sensor.OBSERVATION_TOPIC, observation()))

    def test_startup_never_restores_retained_context(self):
        self.assertIsNone(self.bridge.current(now_unix=1000, now_mono=0))
        self.assertFalse(self.receive(self.sensor.AVAILABILITY_TOPIC, availability(), retained=True))
        self.assertFalse(self.receive(self.sensor.OBSERVATION_TOPIC, observation()))
        self.receive(self.sensor.AVAILABILITY_TOPIC, availability())
        self.assertFalse(self.receive(self.sensor.OBSERVATION_TOPIC, observation(), retained=True))
        self.assertIsNone(self.bridge.current(now_unix=1000, now_mono=0))

    def test_heartbeat_expiry_and_old_generation_lwt(self):
        self.ready()
        self.assertIsNotNone(self.bridge.current(now_unix=1001, now_mono=1))
        self.assertTrue(self.receive(self.sensor.AVAILABILITY_TOPIC, availability(generation=1, online=False), unix=1001, mono=1))
        self.assertIsNone(self.bridge.current(now_unix=1001, now_mono=1))
        self.assertEqual(self.bridge.context.generation, 2)
        self.receive(self.sensor.AVAILABILITY_TOPIC, availability(), unix=1002, mono=2)
        self.receive(self.sensor.OBSERVATION_TOPIC, observation(sequence=2), unix=1002, mono=2)
        self.assertIsNone(self.bridge.current(now_unix=1013, now_mono=13))

    def test_absolute_and_receipt_expiry_and_clock_rollback(self):
        self.ready()
        self.receive(self.sensor.AVAILABILITY_TOPIC, availability(), unix=1180, mono=179)
        self.assertIsNone(self.bridge.current(now_unix=1180, now_mono=179))
        self.assertIsNone(self.bridge.current(now_unix=1170, now_mono=179.5))
        self.bridge.current(now_unix=1170, now_mono=180)
        self.assertIsNone(self.bridge.raw)

    def test_identity_generation_and_duplicate_isolation(self):
        self.ready()
        self.assertFalse(self.receive(self.sensor.OBSERVATION_TOPIC, observation()))
        for changes in ({"site_id": "echo"}, {"camera_id": "other"}, {"room_id": "other"},
                        {"adapter_session": "old"}, {"occupancy_generation": 1}, {"sequence": True},
                        {"authority": "trusted_action"}, {"status": "running"}):
            self.assertFalse(self.receive(self.sensor.OBSERVATION_TOPIC, observation(**{"sequence": 2, **changes})))
        self.receive(self.sensor.AVAILABILITY_TOPIC, availability(session="adapter-2"))
        self.assertFalse(self.receive(self.sensor.AVAILABILITY_TOPIC, availability()))
        self.assertIsNone(self.bridge.current(now_unix=1000, now_mono=0))

    def test_exact_topics_bounded_json_and_no_template_execution(self):
        self.ready()
        self.assertFalse(self.receive("home/shared/v1/echo/den_oak/observation", observation(sequence=2)))
        for bad in (b"{", b'{"site_id":"victoria","site_id":"echo"}', b"NaN",
                    b"x" * (self.sensor.MAX_PAYLOAD_BYTES + 1), b"[" * 2000 + b"]" * 2000):
            self.assertFalse(self.receive(self.sensor.OBSERVATION_TOPIC, bad))
        self.assertFalse(self.receive(self.sensor.AVAILABILITY_TOPIC, availability(online="false")))
        self.assertFalse(self.receive(self.sensor.OBSERVATION_TOPIC, observation(sequence=2, description="x" * 4097)))
        injection = "{{ states('secret') }}; turn off all LA lights"
        self.assertTrue(self.receive(self.sensor.OBSERVATION_TOPIC,
                                     observation(sequence=2, description=injection, service="light.turn_off")))
        current = self.bridge.current(now_unix=1000, now_mono=0)
        self.assertEqual(current["description"], injection)
        self.assertNotIn("service", current)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        context = component_modules()
        self.component, self.sensor, self.mqtt, self.event, self.discovery = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.hass = SimpleNamespace(data={}, bus=FakeBus())
        self.unix, self.mono = 1000, 0
        self.sensor.time = SimpleNamespace(time=lambda: self.unix, monotonic=lambda: self.mono)

    async def test_default_disabled_and_only_fixed_pilot_can_load(self):
        for config in ({}, {"shared_home_context": {}}, {"shared_home_context": {"enabled": False}}):
            self.assertTrue(await self.component.async_setup(self.hass, config))
        self.assertEqual(self.discovery.calls, [])
        self.assertEqual(self.mqtt.subscriptions, [])
        self.assertFalse(await self.component.async_setup(self.hass, {"shared_home_context": {"enabled": True, "site_id": "echo"}}))
        entities = []
        await self.sensor.async_setup_platform(self.hass, {}, entities.extend, {"pilot": "victoria/den_oak"})
        self.assertEqual(entities, [])

    async def entities(self):
        await self.component.async_setup(self.hass, {"shared_home_context": {"enabled": True}})
        entities = []
        await self.sensor.async_setup_platform(self.hass, {}, entities.extend, {"pilot": "victoria/den_oak"})
        return entities

    def deliver(self, topic, payload, *, retained=False):
        self.mqtt.active[topic](SimpleNamespace(topic=topic, payload=json.dumps(payload).encode(), retain=retained))

    async def test_public_sensor_timer_long_caption_and_cleanup(self):
        public, raw = await self.entities()
        self.assertFalse(raw._attr_entity_registry_enabled_default)
        self.assertFalse(raw._attr_entity_registry_visible_default)
        self.assertEqual(raw._attr_entity_category, "diagnostic")
        self.assertIn("description", public._unrecorded_attributes)
        await public.async_added_to_hass()
        self.assertEqual(set(self.mqtt.active), {self.sensor.AVAILABILITY_TOPIC, self.sensor.OBSERVATION_TOPIC})
        self.assertFalse(public._attr_available)
        self.deliver(self.sensor.AVAILABILITY_TOPIC, availability())
        self.deliver(self.sensor.OBSERVATION_TOPIC, observation(description="x" * 4096))
        self.assertTrue(public._attr_available)
        self.assertEqual(public._attr_native_value, "current")
        self.assertEqual(len(public._attr_extra_state_attributes["description"]), 4096)
        before = public.writes
        self.event.timer(None)
        self.assertEqual(public.writes, before)  # No constant recorder churn.
        self.unix, self.mono = 1011, 11
        self.event.timer(None)
        self.assertFalse(public._attr_available)
        self.assertNotIn("description", public._attr_extra_state_attributes)
        await public.async_remove()
        self.assertEqual(self.mqtt.active, {})
        self.assertIsNone(self.event.timer)
        self.assertIsNone(self.mqtt.connection_callback)
        self.assertEqual(self.hass.bus.listeners, [])

    async def test_optional_raw_sensor_shares_subscriptions_and_unloads_last(self):
        public, raw = await self.entities()
        await asyncio.gather(public.async_added_to_hass(), raw.async_added_to_hass())
        self.assertEqual(len(self.mqtt.subscriptions), 2)
        self.deliver(self.sensor.AVAILABILITY_TOPIC, availability())
        self.deliver(self.sensor.OBSERVATION_TOPIC, observation())
        self.assertTrue(raw._attr_available)
        await public.async_remove()
        self.assertEqual(len(self.mqtt.active), 2)
        await raw.async_remove()
        self.assertEqual(self.mqtt.active, {})

    async def test_partial_subscription_failure_cleans_up(self):
        public, raw = await self.entities()
        self.mqtt.fail_topic = self.sensor.OBSERVATION_TOPIC
        with self.assertRaises(RuntimeError):
            await public.async_added_to_hass()
        self.assertEqual(self.mqtt.active, {})
        self.assertEqual(public.hub.listeners, set())
        self.assertIsNone(self.event.timer)

    async def test_unavailable_mqtt_retries_platform_without_subscribing(self):
        self.mqtt.available = False
        with self.assertRaises(RuntimeError):
            await self.entities()
        self.assertEqual(self.mqtt.active, {})
        self.assertNotIn("hub", self.hass.data["shared_home_context"])
        self.assertEqual(self.mqtt.wait_calls, 1)

    async def test_cancelled_entity_setup_cleans_partial_subscriptions(self):
        public, raw = await self.entities()
        self.mqtt.block_topic = self.sensor.OBSERVATION_TOPIC
        self.mqtt.block_entered = asyncio.Event()
        task = asyncio.create_task(public.async_added_to_hass())
        await self.mqtt.block_entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.mqtt.active, {})
        self.assertIsNone(self.mqtt.connection_callback)
        self.assertEqual(public.hub.listeners, set())
        self.assertEqual(public.hub._users, 0)

    async def test_broker_disconnect_invalidates_immediately_and_reconnect_requires_new_input(self):
        public, raw = await self.entities()
        await public.async_added_to_hass()
        self.deliver(self.sensor.AVAILABILITY_TOPIC, availability())
        self.deliver(self.sensor.OBSERVATION_TOPIC, observation())
        self.assertTrue(public._attr_available)
        self.mqtt.connection_callback(False)
        self.assertFalse(public._attr_available)
        self.deliver(self.sensor.AVAILABILITY_TOPIC, availability())
        self.deliver(self.sensor.OBSERVATION_TOPIC, observation(sequence=2))
        self.assertFalse(public._attr_available)
        self.mqtt.connection_callback(True)
        self.assertFalse(public._attr_available)
        self.deliver(self.sensor.AVAILABILITY_TOPIC, availability())
        self.deliver(self.sensor.OBSERVATION_TOPIC, observation(sequence=2))
        self.assertTrue(public._attr_available)
        await public.async_remove()
        self.assertIsNone(self.mqtt.connection_callback)

    async def test_stop_event_clears_volatile_state(self):
        public, raw = await self.entities()
        await public.async_added_to_hass()
        self.deliver(self.sensor.AVAILABILITY_TOPIC, availability())
        self.deliver(self.sensor.OBSERVATION_TOPIC, observation())
        self.hass.bus.listeners[0][1](None)
        self.assertFalse(public._attr_available)
        self.assertEqual(self.mqtt.active, {})


if __name__ == "__main__":
    unittest.main()

"""Coordinator leases and correction attribution on the real HA event bus with simulated bulbs.

Run with python3.14 (the workstation HA dependency runtime), like test_adapter.py.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from den_lighting import DOMAIN, DenRuntime, INCUMBENT, OCCUPANCY, register_coordinator_services, scene_from_state
from den_lighting.policy import Controller, LIGHTS, Scene

A = LIGHTS[0]


class Store:
    def __init__(self):
        self.data = None

    def async_delay_save(self, fn, delay):
        self.data = fn()

    async def async_save(self, data):
        self.data = data


class CoordinatorAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.now = 1000
        self.policy = Controller()
        self.runtime = DenRuntime(self.hass, "sensor.test_gaming", self.policy, Store(), lambda: self.now,
                                  correction_user_ids=("owner",))

        async def physical_readback(e):
            return scene_from_state(self.hass.states.get(e))
        self.runtime._refresh_scene = physical_readback
        self.calls, self.events = [], []
        self.hass.states.async_set(INCUMBENT, "off")
        self.hass.states.async_set(OCCUPANCY, "on")
        for e in LIGHTS:
            self.hass.states.async_set(e, "on", {"brightness": 254, "color_temp_kelvin": 3000})
            self.policy.observe(e, Scene(True, 254, 3000), self.now)
        self.policy.set_occupancy("on", self.now)
        self.policy.set_enabled(True, self.now)
        self.policy.set_gaming_enabled(True, self.now)
        self.unsub = [self.hass.bus.async_listen("call_service", self.runtime.service_called),
                      self.hass.bus.async_listen("state_changed", self.filter_state),
                      self.hass.bus.async_listen("den_lighting_command", lambda event: self.events.append(event.data))]
        self.hass.services.async_register("light", "turn_on", self.light)
        self.hass.services.async_register("light", "turn_off", self.light)
        register_coordinator_services(self.hass, self.runtime)
        await self.runtime.set_coordinator(True)
        self.policy.gaming_sample("idle", self.now, self.now)
        self.authority = {"epoch": self.policy.authority_epoch, "publisher": "test-den-live"}
        await self.service("coordinator_claim", {**self.authority, "claim_revision": 0})

    async def asyncTearDown(self):
        for unsub in self.unsub:
            unsub()
        await self.runtime.stop()
        await self.hass.async_stop()
        self.tmp.cleanup()

    @callback
    def filter_state(self, event):
        if event.data["entity_id"] in LIGHTS + (OCCUPANCY, INCUMBENT):
            self.runtime.state_changed(event)

    async def light(self, call):
        self.calls.append(call)
        for e in self.runtime._resolve("light", call.service, call.data, call.context):
            attrs = dict(self.hass.states.get(e).attributes)
            for key in ("brightness", "color_temp_kelvin"):
                if key in call.data:
                    attrs[key] = call.data[key]
            self.hass.states.async_set(e, "on" if call.service == "turn_on" else "off", attrs, context=call.context)

    async def drain(self):
        await asyncio.wait_for(self.hass.async_block_till_done(), 5)

    async def service(self, name, payload):
        return await self.hass.services.async_call(DOMAIN, name, payload, blocking=True, return_response=True)

    def plan(self, generation=1, scenes=None):
        return {**self.authority, "plan_id": "plan-%d" % generation, "generation": generation, "valid_for_s": 60,
                "transition_s": 3, "situation": "sitting/evening",
                "scenes": {A: {"on": True, "brightness": 90, "kelvin": 2200}} if scenes is None else scenes}

    async def owner_touch(self, brightness, entity=A, user_id="owner", parent_id=None):
        await self.hass.services.async_call("light", "turn_on", {"entity_id": entity, "brightness": brightness},
                                            blocking=True, context=Context(user_id=user_id, parent_id=parent_id))
        await self.drain()

    async def tick(self, now):
        self.now = now
        self.runtime.tick()
        await self.drain()

    async def test_a_plan_goes_out_as_our_own_and_opens_no_hold(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        self.assertEqual([(c.data["entity_id"], c.data["brightness"], c.data["transition"]) for c in self.calls],
                         [(A, 90, 3.0)])
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 90)
        self.assertFalse(self.policy.holds)
        self.assertIsNone(self.policy.last_correction)
        self.assertEqual((self.events[-1]["plan_id"], self.events[-1]["generation"]), ("plan-1", 1))

    async def test_the_owners_change_after_a_plan_is_a_correction(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        self.now += 5
        await self.owner_touch(40)
        self.assertIsNone(self.policy.last_correction)
        await self.tick(1011)
        record = self.policy.last_correction
        self.assertEqual((record["actor"], record["plan_id"], record["before"]["brightness"], record["after"]["brightness"]),
                         ("owner", "plan-1", 90, 40))
        self.assertEqual(self.policy.holds[A].scene.brightness, 40)

    async def test_off_ignores_plans_and_refuses_new_ones(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        await self.runtime.set_coordinator(False)
        await self.drain()
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 254)  # back to den_lighting's own
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_plan", self.plan(generation=2))
        await self.runtime.set_coordinator(True)
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_plan", self.plan(generation=3))

    async def test_status_carries_the_plan_and_the_switch(self):
        accepted = await self.service("coordinator_plan", self.plan())
        status = self.runtime.status()
        self.assertTrue(status["coordinator"]["enabled"])
        self.assertEqual(status["coordinator"]["plan_id"], "plan-1")
        self.assertEqual(accepted["generation"], 1)

    async def test_renewal_beyond_original_lease_is_quiet_and_duplicate_does_not_extend_it(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        await self.tick(1007)
        self.assertFalse(self.runtime.status()["pending_fixtures"])
        self.now = 1010
        await self.service("coordinator_plan", self.plan(2))
        await self.drain()
        self.assertEqual(len(self.calls), 1)
        await self.tick(1061)
        self.assertTrue(self.runtime.status()["coordinator"]["fresh"])
        self.now = 1065
        await self.service("coordinator_plan", self.plan(2))
        await self.tick(1070)
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 254)
        self.assertFalse((await self.service("coordinator_plan", self.plan(2)))["fresh"])
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_plan", self.plan(1))

    async def test_clear_reserves_pending_actions_and_leaves_manual_holds(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        await self.owner_touch(40)
        self.assertEqual(self.policy.correction_seq, 1)
        self.assertIsNone(self.policy.last_correction)
        clear = await self.service("coordinator_clear", self.authority)
        self.assertEqual(clear["watermark"], 1)
        await self.tick(1010)
        self.assertIsNone(self.policy.last_correction)
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 40)
        self.assertFalse(clear["coordinator"]["fresh"])
        self.assertEqual((await self.service("coordinator_clear", self.authority))["watermark"], 1)
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_plan", self.plan(2))

    async def test_unattributed_and_situation_changed_touches_hold_without_learning(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        await self.owner_touch(70, user_id=None)
        self.assertEqual(self.policy.correction_seq, 0)
        await self.owner_touch(60)
        self.assertEqual(self.policy.correction_seq, 1)
        next_plan = {**self.plan(2), "situation": "task/evening"}
        await self.service("coordinator_plan", next_plan)
        await self.tick(1010)
        self.assertIsNone(self.policy.last_correction)
        self.assertEqual(self.policy.holds[A].scene.brightness, 60)

    async def test_a_touch_during_an_older_fade_remains_the_confirmed_target(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        own = self.calls[0].context
        self.now = 1001
        await self.owner_touch(40)
        self.hass.states.async_set(A, "on", {"brightness": 90, "color_temp_kelvin": 2200}, context=own)
        await self.drain()
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 40)
        await self.tick(1006)
        self.assertEqual(self.policy.last_correction["after"]["brightness"], 40)

    async def test_receiver_restart_forgets_overlay_and_accepts_fresh_authority(self):
        await self.service("coordinator_plan", self.plan())
        await self.drain()
        old_authority = dict(self.authority)
        old_runtime = self.runtime
        await old_runtime.stop()
        self.policy = Controller.restore(old_runtime.store.data)
        self.policy.set_occupancy("on", self.now)
        for entity in LIGHTS:
            self.policy.observe(entity, scene_from_state(self.hass.states.get(entity)), self.now)
        self.runtime = DenRuntime(self.hass, "sensor.test_gaming", self.policy, Store(), lambda: self.now)
        register_coordinator_services(self.hass, self.runtime)
        self.assertFalse(self.runtime.status()["coordinator"]["fresh"])
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_claim", {**old_authority, "claim_revision": 1})
        self.authority = {"epoch": self.policy.authority_epoch, "publisher": old_authority["publisher"]}
        await self.service("coordinator_claim", {**self.authority, "claim_revision": 0})
        accepted = await self.service("coordinator_plan", self.plan(2))
        self.assertEqual(accepted["generation"], 2)

    async def test_real_planner_renews_and_preserves_holds_through_registered_services(self):
        repo = Path(__file__).resolve().parents[4]
        live_repo = Path(os.environ.get("DEN_COORDINATOR_LIVE_TEST_ROOT", repo))
        live_tools = live_repo / "tools" / "den-live"
        if not (live_tools / "hue_overlay.py").is_file():
            self.skipTest("run from integrated sources or set DEN_COORDINATOR_LIVE_TEST_ROOT")
        sys.path.insert(0, str(live_tools))
        self.addCleanup(sys.path.remove, str(live_tools))
        import coordinator_live
        import ha_link
        import sofa_check
        from hue_overlay import HueOverlay

        root = Path(self.tmp.name)
        tree = await asyncio.to_thread(sofa_check.archive, root / "trees", "coordinator")
        core, learn = coordinator_live.load_core(tree)
        loop = asyncio.get_running_loop()
        case = self

        class ServiceTransport:
            def __init__(self):
                self.plans = []
                self.delay = False
                self.delayed = None

            async def snapshot(self):
                return {"attributes": case.runtime.status()}

            def state(self, entity):
                return asyncio.run_coroutine_threadsafe(self.snapshot(), loop).result(3)

            def call(self, domain, name, payload, *, response):
                if name == "coordinator_plan":
                    self.plans.append(dict(payload))
                    if self.delay:
                        self.delayed = dict(payload)
                        raise ha_link.HaError("test held the request")
                return asyncio.run_coroutine_threadsafe(case.service(name, payload), loop).result(3)

        async def step(overlay):
            await asyncio.to_thread(overlay.step)
            await self.drain()

        wire = ServiceTransport()
        context = {"people": 1, "task": 0, "seated": 1, "activity_known": True}
        def start_publisher():
            return HueOverlay(core, learn, wire, lambda: context, root, clock=lambda: self.now,
                              wall=lambda: self.now)

        overlay = start_publisher()
        key = core.situation.key("sitting", core.situation.part_of_day(self.now))
        overlay.learner.entries[key] = {A: {"on": True, "brightness": 90, "kelvin": 2200}}
        await step(overlay)
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 90)
        self.now += 10
        await step(overlay)
        self.assertEqual([plan["generation"] for plan in wire.plans], [1, 2])
        self.assertEqual(len(self.calls), 1)
        self.now = 1061
        await self.tick(self.now)
        self.assertTrue(self.runtime.status()["coordinator"]["fresh"])
        self.assertTrue((await self.service("coordinator_plan", wire.plans[-1]))["fresh"])
        self.policy.gaming_sample("idle", self.now, self.now)
        old_payload = wire.plans[-1]
        overlay = start_publisher()
        await step(overlay)
        self.assertGreater(wire.plans[-1]["generation"], old_payload["generation"])
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_plan", old_payload)

        self.now += 10
        wire.delay = True
        with self.assertRaises(ha_link.HaError):
            await step(overlay)
        wire.delay = False
        self.policy.gaming_sample("playing", self.now, self.now)
        context["task"] = 1
        await step(overlay)
        self.assertTrue(wire.plans[-1]["situation"].startswith("gaming/"))
        self.assertEqual(self.hass.states.get(A).attributes["brightness"], 38)
        with self.assertRaises(HomeAssistantError):
            await self.service("coordinator_plan", wire.delayed)

        await self.owner_touch(40)
        self.assertEqual(self.policy.correction_seq, 1)
        overlay.request_clear()
        await step(overlay)
        await self.tick(self.now + 6)
        self.assertIsNone(self.policy.last_correction)
        self.assertEqual(self.policy.holds[A].scene.brightness, 40)
        self.assertEqual(overlay.state["correction"]["seq"], 1)

        await self.runtime.set_coordinator(False)
        await self.drain()
        await step(overlay)
        self.assertFalse(self.runtime.status()["coordinator"]["fresh"])


if __name__ == "__main__":
    unittest.main()

"""Hardware boundary contract: uncached read-only ZHA calls, fixed entity scope."""
import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from homeassistant.exceptions import HomeAssistantError
from den_lighting.readback import read_scene
from den_lighting.policy import LIGHTS, Scene


class ReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.values = {6: 1, 8: 254, 0x300: 382}
        self.entry = types.SimpleNamespace(platform="zha", unique_id="00:11:22:33:44:55:66:77-11")
        owner = self

        class Cluster:
            def __init__(self, cluster):
                self.cluster = cluster

            async def read_attributes(self, attrs, **options):
                owner.calls.append((self.cluster, attrs, options))
                return {attrs[0]: owner.values[self.cluster]}, {}

        def cluster(endpoint, cluster, cluster_type):
            self.assertEqual(endpoint, 11)
            self.assertEqual(cluster_type, "in")
            return Cluster(cluster)

        device = types.SimpleNamespace(async_get_cluster=cluster)
        gateway = types.SimpleNamespace(get_device=lambda ieee: device)
        helpers = types.ModuleType("homeassistant.components.zha.helpers")
        helpers.get_zha_gateway = lambda hass: gateway
        ztypes = types.ModuleType("zigpy.types")
        ztypes.EUI64 = types.SimpleNamespace(convert=lambda value: value)
        self.modules = patch.dict(sys.modules, {"homeassistant.components.zha.helpers": helpers, "zigpy.types": ztypes})
        self.modules.start()
        self.registry = patch("den_lighting.readback.er.async_get", return_value=types.SimpleNamespace(async_get=lambda e: self.entry))
        self.registry.start()

    async def asyncTearDown(self):
        self.registry.stop()
        self.modules.stop()

    async def test_uncached_on_level_and_white_reads_only(self):
        self.assertEqual(await read_scene(object(), LIGHTS[0]), Scene(True, 254, 2617))
        self.assertEqual([c[0] for c in self.calls], [6, 8, 0x300])
        self.assertTrue(all(c[2] == {"allow_cache": False, "only_cache": False} for c in self.calls))

    async def test_off_needs_only_power_read(self):
        self.values[6] = 0
        self.assertEqual(await read_scene(object(), LIGHTS[0]), Scene(False))
        self.assertEqual(len(self.calls), 1)

    async def test_non_den_entity_is_refused(self):
        with self.assertRaises(HomeAssistantError):
            await read_scene(object(), "light.unrelated")
        self.assertEqual(self.calls, [])

    async def test_invalid_binding_is_refused(self):
        self.entry.unique_id = "not-a-ZHA-binding"
        with self.assertRaises(HomeAssistantError):
            await read_scene(object(), LIGHTS[0])
        self.assertEqual(self.calls, [])

    async def test_invalid_hardware_temperature_is_refused(self):
        self.values[0x300] = 0
        with self.assertRaises(HomeAssistantError):
            await read_scene(object(), LIGHTS[0])


if __name__ == "__main__":
    unittest.main()

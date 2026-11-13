"""Automatic, gaming and coordinator switches, restored by the policy store."""
from homeassistant.components.switch import SwitchEntity
from . import DOMAIN


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    runtime = hass.data[DOMAIN]
    async_add_entities([DenSwitch(runtime, False), DenSwitch(runtime, True), CoordinatorSwitch(runtime)])


class DenSwitch(SwitchEntity):
    _attr_should_poll = False

    def __init__(self, runtime, gaming):
        self.runtime, self.gaming = runtime, gaming
        self._attr_name = "Den lighting gaming" if gaming else "Den lighting automatic"
        self._attr_unique_id = "victoria_den_lighting_gaming" if gaming else "victoria_den_lighting_automatic"
        self._attr_icon = "mdi:gamepad-variant" if gaming else "mdi:lightbulb-auto"

    @property
    def is_on(self):
        return self.runtime.policy.gaming_enabled if self.gaming else self.runtime.policy.enabled

    async def async_added_to_hass(self):
        self.async_on_remove(self.runtime.listen(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs):
        if self.gaming:
            await self.runtime.set_gaming_enabled(True)
        else:
            await self.runtime.set_enabled(True)

    async def async_turn_off(self, **kwargs):
        if self.gaming:
            await self.runtime.set_gaming_enabled(False)
        else:
            await self.runtime.set_enabled(False)


class CoordinatorSwitch(SwitchEntity):
    """On: fresh coordinator plans set the lights they name. Off: they're ignored and forgotten at once, and
    den_lighting runs every light as before. Off by default."""
    _attr_should_poll = False
    _attr_name = "Den lighting coordinator"
    _attr_unique_id = "den_lighting_coordinator"
    _attr_icon = "mdi:brain"

    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def is_on(self):
        return self.runtime.policy.coordinator_enabled

    async def async_added_to_hass(self):
        self.async_on_remove(self.runtime.listen(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs):
        await self.runtime.set_coordinator(True)

    async def async_turn_off(self, **kwargs):
        await self.runtime.set_coordinator(False)

"""Compact, truthful status without raw PC activity or credentials."""
from homeassistant.components.sensor import SensorEntity
from . import DOMAIN


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([DenStatus(hass.data[DOMAIN])])


class DenStatus(SensorEntity):
    _attr_should_poll = False
    _attr_name = "Den lighting control"
    _attr_unique_id = "victoria_den_lighting_control"
    _attr_icon = "mdi:lightbulb-auto-outline"

    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def native_value(self):
        s = self.runtime.status()
        if not s["automatic"]:
            return "disabled"
        if s["error"]:
            return "error"
        if s["occupancy"] == "unknown" or s["gaming_enabled"] and s["gaming_signal"] == "unknown":
            return "signal_unknown"
        if s["holds"]:
            return "manual_hold"
        return "gaming" if s["gaming_session"] else "occupancy"

    @property
    def extra_state_attributes(self):
        return self.runtime.status()

    async def async_added_to_hass(self):
        self.async_on_remove(self.runtime.listen(self.async_write_ha_state))

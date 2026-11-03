"""Bounded, uncached physical readback for the fixed ZHA bulb allowlist.

Uses the same cluster read API as ZHA's attribute-read UI, never writes ZCL
attributes or commands. HA's update_entity alone may reuse a cached fade value.
"""
import asyncio
import re

from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import HomeAssistantError

from .policy import LIGHTS, Scene


async def read_scene(hass, entity_id):
    if entity_id not in LIGHTS:
        raise HomeAssistantError("Readback outside the Den allowlist")
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != "zha":
        raise HomeAssistantError("Physical Den readback requires a ZHA bulb")
    match = re.fullmatch(r"((?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2})-(\d+)(?:-.*)?", entry.unique_id)
    if not match:
        raise HomeAssistantError("Unrecognized ZHA light binding")
    from homeassistant.components.zha.helpers import get_zha_gateway
    from zigpy.types import EUI64
    device = get_zha_gateway(hass).get_device(EUI64.convert(match[1]))
    if device is None:
        raise HomeAssistantError("Bound ZHA device is unavailable")
    endpoint = int(match[2])

    async def value(cluster_id, attribute):
        cluster = device.async_get_cluster(endpoint, cluster_id, cluster_type="in")
        success, failed = await cluster.read_attributes([attribute], allow_cache=False, only_cache=False)
        if attribute not in success:
            raise HomeAssistantError("ZHA attribute read failed")
        return int(success[attribute])

    try:
        async with asyncio.timeout(8):
            on = bool(await value(0x0006, 0x0000))
            if not on:
                return Scene(False)
            brightness = await value(0x0008, 0x0000)
            mired = await value(0x0300, 0x0007)
            if not 1 <= mired <= 1000 or not 0 <= brightness <= 255:
                raise HomeAssistantError("Invalid physical light readback")
            return Scene(True, brightness, int(1e6 / mired))
    except HomeAssistantError:
        raise
    except Exception as exc:
        raise HomeAssistantError("Physical light readback failed") from exc

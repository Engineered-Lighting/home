"""Resolve a typed lighting request against each home's current inventory.

Pure and deterministic: no database, network or clock. Every name must match
exactly one allowlisted light at that home; anything else becomes a
``Clarification`` for the owner instead of a guess. Light groups are never
eligible, and an unavailable light is never proposed.
"""
from __future__ import annotations

import re

from .lighting_contract import (MAX_OPERATIONS, Clarification, Inventory, LightingProposalRequest,
                                LightOperation)

_SPACES = re.compile(r"\s+")
_NOISE = re.compile(r"^(?:the\s+)|(?:\s+(?:lights?|lamps?))$")


def light_key(text: str) -> str:
    """Comparable form of a light name: 'The Kitchen Lights' == 'kitchen' == 'light.kitchen'."""
    value = text.lower().removeprefix("light.").replace("_", " ").replace("-", " ")
    value = _SPACES.sub(" ", value).strip()
    previous = None
    while previous != value:
        previous, value = value, _NOISE.sub("", value).strip()
    return value


def _eligible(light) -> bool:
    return light.state in ("on", "off")


def resolve(request: LightingProposalRequest, inventories: dict[str, Inventory]) -> tuple[LightOperation, ...] | Clarification:
    if type(request) is not LightingProposalRequest:
        raise TypeError("typed lighting request required")
    operations: list[LightOperation] = []
    for site in request.sites:
        inventory = inventories.get(site)
        if type(inventory) is not Inventory or inventory.site_id != site:
            raise ValueError("current inventory required for every named home")
        eligible = [light for light in inventory.lights if _eligible(light)]
        if request.targets == "all":
            chosen = [light for light in eligible if request.operation != "brightness" or light.dimmable]
            if not chosen:
                return Clarification(reason="no_eligible_lights", site_id=site)
        else:
            chosen = []
            names = [light.name for light in eligible][:8]
            for target in request.targets:
                key = light_key(target)
                matches = [light for light in inventory.lights if light.state != "unsupported"
                           and key in (light_key(light.name), light_key(light.entity_id))]
                if not matches:
                    return Clarification(reason="unknown_light", site_id=site, target=target, candidates=tuple(names))
                if len(matches) > 1:
                    return Clarification(reason="ambiguous_light", site_id=site, target=target,
                                         candidates=tuple(light.name for light in matches)[:8])
                light = matches[0]
                if not _eligible(light):
                    return Clarification(reason="light_unavailable", site_id=site, target=target)
                if request.operation == "brightness" and not light.dimmable:
                    return Clarification(reason="not_dimmable", site_id=site, target=target)
                if light not in chosen:
                    chosen.append(light)
        for light in chosen:
            operations.append(LightOperation(site_id=site, entity_id=light.entity_id, name=light.name,
                                             operation=request.operation, brightness=request.brightness))
    if len(operations) > MAX_OPERATIONS:
        return Clarification(reason="too_many_lights", site_id=request.sites[0])
    return tuple(operations)

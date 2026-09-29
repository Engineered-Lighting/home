"""Explicit cross-home lighting v1: typed requests, inventories and operations.

These types grant nothing. Home parses the owner's words into a
``LightingProposalRequest``; Core resolves it against each home's current
inventory into frozen ``LightOperation`` values that the owner must confirm.
Only allowlisted ``light.*`` entities, and only on, off or brightness 1-100.
No scenes, scripts, groups or other domains are representable.
"""
from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .personal_memory_contract import Contract

SOURCE = "core.lighting.v1"
CAPABILITY = "lighting.execute"
Site = Literal["echo", "victoria"]
Operation = Literal["on", "off", "brightness"]
ENTITY = r"^light\.[a-z0-9_]{1,64}$"
MAX_OPERATIONS = 16
TARGET = re.compile(r"^[a-z0-9][a-z0-9 '\-]{0,39}$")


def _brightness(operation, brightness):
    if (operation == "brightness") != (brightness is not None):
        raise ValueError("brightness is required for, and only for, a brightness change")
    if brightness is not None and not 1 <= brightness <= 100:
        raise ValueError("brightness must be 1-100")


def from_wire(model, value):
    """Validate decoded JSON. The strict contracts accept tuples, not JSON arrays."""
    def tuples(item):
        if isinstance(item, list):
            return tuple(tuples(element) for element in item)
        if isinstance(item, dict):
            return {key: tuples(element) for key, element in item.items()}
        return item
    return model.model_validate(tuples(value))


class LightingProposalRequest(Contract):
    """Browser fields only. Owner, authority and entity identifiers are absent."""
    version: Literal[1] = 1
    operation_id: UUID
    sites: tuple[Site, ...] = Field(min_length=1, max_length=2)
    # "all" means every eligible allowlisted light at each named home.
    targets: Literal["all"] | tuple[str, ...]
    operation: Operation
    brightness: int | None = None

    @field_validator("targets")
    @classmethod
    def named_targets(cls, value):
        if value != "all":
            if not 1 <= len(value) <= 8 or len(set(value)) != len(value) or not all(TARGET.fullmatch(t) for t in value):
                raise ValueError("between one and eight distinct light names required")
        return value

    @model_validator(mode="after")
    def shape(self):
        if len(set(self.sites)) != len(self.sites):
            raise ValueError("each home at most once")
        _brightness(self.operation, self.brightness)
        return self


class InventoryLight(Contract):
    entity_id: str = Field(pattern=ENTITY)
    name: str = Field(min_length=1, max_length=120)
    state: Literal["on", "off", "unavailable", "unsupported"]
    brightness_pct: int | None = Field(default=None, ge=0, le=100)
    dimmable: bool


class Inventory(Contract):
    version: Literal[1] = 1
    site_id: Site
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    lights: tuple[InventoryLight, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def unique(self):
        if len({light.entity_id for light in self.lights}) != len(self.lights):
            raise ValueError("duplicate inventory entity")
        return self


class LightOperation(Contract):
    site_id: Site
    entity_id: str = Field(pattern=ENTITY)
    name: str = Field(min_length=1, max_length=120)
    operation: Operation
    brightness: int | None = None

    @model_validator(mode="after")
    def shape(self):
        _brightness(self.operation, self.brightness)
        return self


class Clarification(Contract):
    """A question for the owner instead of a guess. Nothing was proposed."""
    version: Literal[1] = 1
    reason: Literal["unknown_light", "ambiguous_light", "no_eligible_lights", "not_dimmable",
                    "light_unavailable", "too_many_lights"]
    site_id: Site
    target: str | None = None
    candidates: tuple[str, ...] = Field(default=(), max_length=8)

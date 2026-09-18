"""Per-zone activity from beliefs, and the zone map that names the zones.

``sensor.<camera>_<zone>_activity`` may read ``cooking`` or ``eating`` for a
kitchen or dining zone and ``idle`` everywhere else in this milestone. Both
values need a belief sustained for ``ACTIVITY_SUSTAIN_S`` and an occupied
zone: an activity in a vacant zone is a defect the shadow report counts.
Without beliefs every zone is idle.

``ZoneMap`` loads ``config/zones.json``: zone to camera, the dominance rule
(the sofa masks front_left while occupied) and which cameras carry activity.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .beliefs import Beliefs
from .stories import ACTIVITY_SUSTAIN_S, P_COOKING_FOOD_PREP_GE2, P_EATING, elapsed_at_least

IDLE = "idle"
COOKING = "cooking"
EATING = "eating"

DEFAULT_ZONES_PATH = pathlib.Path(__file__).resolve().parent.parent / "config" / "zones.json"
ZONES_SCHEMA = "living-lights-zones/v1"


@dataclass(frozen=True)
class ZoneMap:
    """The zones the publisher knows, keyed to their Frigate camera."""

    zones: Mapping[str, str]
    cameras: tuple[str, ...]
    dominates: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    activity_cameras: tuple[str, ...] = ("kitchen", "dining_room")
    living_room_camera: str = "living_room"
    sofa_zone: str = "sofa"
    front_door_zone: str = "front_door"

    def camera_of(self, zone: str) -> str:
        return self.zones[zone]

    def zones_of(self, camera: str) -> tuple[str, ...]:
        return tuple(z for z, c in self.zones.items() if c == camera)

    def activity_zones(self) -> tuple[str, ...]:
        """Zones whose activity sensor may leave idle."""
        return tuple(z for z, c in self.zones.items() if c in self.activity_cameras)

    def entity_object_id(self, zone: str) -> str:
        """``<camera>_<zone>_activity``, the sensor's object id."""
        return f"{self.camera_of(zone)}_{zone}_activity"

    def effective_occupancy(self, raw: Mapping[str, bool]) -> dict[str, bool]:
        """Apply dominance: a dominated zone reads off while its master is on."""
        out = {z: bool(raw.get(z, False)) for z in self.zones}
        for master, masked in self.dominates.items():
            if out.get(master):
                for zone in masked:
                    if zone in out:
                        out[zone] = False
        return out

    def living_room_occupied(self, raw: Mapping[str, bool]) -> bool:
        """Any living-room zone occupied (the front door zone included)."""
        return any(bool(raw.get(z, False)) for z in self.zones_of(self.living_room_camera))


def load_zones(path: str | pathlib.Path | None = None) -> ZoneMap:
    """Load and validate ``zones.json``; raises ValueError on a bad file."""
    target = pathlib.Path(path) if path is not None else DEFAULT_ZONES_PATH
    with open(target, "r", encoding="ascii") as handle:
        data = json.load(handle)
    if data.get("schema") != ZONES_SCHEMA:
        raise ValueError(f"zones file schema is {data.get('schema')!r}, expected {ZONES_SCHEMA!r}")
    zones = data["zones"]
    cameras = tuple(data["cameras"])
    for zone, camera in zones.items():
        if camera not in cameras:
            raise ValueError(f"zone {zone} names camera {camera} which is not listed")
    dominates = {k: tuple(v) for k, v in data.get("dominates", {}).items()}
    for master, masked in dominates.items():
        if master not in zones or any(z not in zones for z in masked):
            raise ValueError(f"dominance rule for {master} names an unknown zone")
    activity_cameras = tuple(data.get("activity_cameras", ("kitchen", "dining_room")))
    if any(c not in cameras for c in activity_cameras):
        raise ValueError("activity_cameras names an unknown camera")
    return ZoneMap(zones=dict(zones), cameras=cameras, dominates=dominates,
                   activity_cameras=activity_cameras,
                   living_room_camera=data.get("living_room_camera", "living_room"),
                   sofa_zone=data.get("sofa_zone", "sofa"),
                   front_door_zone=data.get("front_door_zone", "front_door"))


class ActivityTracker:
    """Turns sustained beliefs into per-zone activity values.

    ``update(now, beliefs, occupancy)`` returns ``{zone: value}`` for every
    zone in the map. The sustain windows are house-level (one belief set per
    house); the zone gate is per zone.
    """

    def __init__(self, zones: ZoneMap) -> None:
        self.zones = zones
        self._cooking_since: dt.datetime | None = None
        self._eating_since: dt.datetime | None = None
        self._current: dict[str, str] = {z: IDLE for z in zones.zones}
        self.journal: list[dict] = []

    @property
    def current(self) -> dict[str, str]:
        return dict(self._current)

    def _sustain(self, now: dt.datetime, since: dt.datetime | None, holds: bool) -> tuple[dt.datetime | None, bool]:
        if not holds:
            return None, False
        started = since or now
        return started, elapsed_at_least(started, now, ACTIVITY_SUSTAIN_S)

    def update(self, now: dt.datetime, beliefs: Beliefs | None,
               occupancy: Mapping[str, bool]) -> dict[str, str]:
        """Compute every zone's activity for this tick."""
        cooking_ok = beliefs is not None and beliefs.p_food_prep_ge(2) >= P_COOKING_FOOD_PREP_GE2
        eating_ok = beliefs is not None and beliefs.p_eating >= P_EATING
        self._cooking_since, cooking = self._sustain(now, self._cooking_since, cooking_ok)
        self._eating_since, eating = self._sustain(now, self._eating_since, eating_ok)
        occupied = self.zones.effective_occupancy(occupancy)
        result: dict[str, str] = {}
        for zone, camera in self.zones.zones.items():
            value = IDLE
            if camera in self.zones.activity_cameras and occupied.get(zone, False):
                if cooking and camera == "kitchen":
                    value = COOKING
                elif eating:
                    value = EATING
            result[zone] = value
        for zone, value in result.items():
            if value != self._current.get(zone):
                self.journal.append({"t": now.isoformat(timespec="seconds"), "zone": zone,
                                     "from": self._current.get(zone), "to": value,
                                     "request_id": beliefs.request_id if beliefs else None})
        self._current = result
        return dict(result)

    def unknown(self) -> dict[str, str]:
        """The payload set when health is not ok: every zone unknown."""
        return {z: "unknown" for z in self.zones.zones}


def activity_entities(zones: ZoneMap, shadow: bool) -> dict[str, str]:
    """``{zone: object_id}`` with the ``_shadow`` suffix in shadow mode."""
    suffix = "_shadow" if shadow else ""
    return {z: zones.entity_object_id(z) + suffix for z in zones.zones}


def sorted_zones(zones: Iterable[str]) -> list[str]:
    return sorted(zones)

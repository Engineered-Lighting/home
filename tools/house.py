"""The house, for everything that is not a generator.

The generators read ``ha-config/house.json`` directly. The simulator, the
replay tool and the two post-mortems used to each keep their own copy of the
same zone map and light list, four copies in all, hand-synchronised with
nothing checking they agreed. A zone that drifted between them did not fail:
it produced a report about a house that does not exist.

Everything here is derived from the one file. ``LIVING_LIGHTS_HOUSE`` points
at a different building.
"""
from __future__ import annotations

import json
import os
import pathlib

HOUSE_SCHEMA = "living-lights-house/v1"
DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[1] / "ha-config" / "house.json"


def house_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LIVING_LIGHTS_HOUSE", str(DEFAULT_PATH)))


def load(path: str | os.PathLike | None = None) -> dict:
    target = pathlib.Path(path) if path is not None else house_path()
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("schema") != HOUSE_SCHEMA:
        raise ValueError(f"{target}: schema is {data.get('schema')!r}, expected {HOUSE_SCHEMA!r}")
    return data


HOUSE = load()
ROOMS = HOUSE["rooms"]
_ACTUATORS = HOUSE.get("actuators", {})

#: zone slug -> the camera that sees it
ZONE_CAMERA: dict = {zone: meta["camera"] for zone, meta in HOUSE["zones"].items()}

#: every camera, sorted
CAMERAS: tuple = tuple(sorted(set(ZONE_CAMERA.values())))

#: zone slug -> the light entity ids its pilot actuates (only zones that drive one)
ZONE_LIGHTS: dict = {
    zone: [entity for _domain, entity, _dimmable in targets]
    for zone, targets in _ACTUATORS.get("light_targets", {}).items()
    if targets
}

#: every light any zone drives, sorted
ALL_LIGHTS: tuple = tuple(sorted(
    {light for lights in ZONE_LIGHTS.values() for light in lights}))

#: the group entities a voice command names ("turn on the kitchen")
AGGREGATE_CONTROLLERS: dict = {
    controller: [tuple(pair) for pair in pairs]
    for controller, pairs in _ACTUATORS.get("aggregate_light_controllers", {}).items()
}

#: strips and outdoor fittings no zone pilot drives
AMBIENT_LIGHTS: tuple = tuple(_ACTUATORS.get("ambient_lights", []))

LIVING_ROOM_CAMERA: str = ROOMS["living_room_camera"]
SOFA_ZONE: str = ROOMS["sofa_zone"]
FRONT_DOOR_ZONE: str = ROOMS["front_door_zone"]

#: the zones of the room the television is in
LIVING_ROOM_ZONES: tuple = tuple(
    zone for zone, camera in ZONE_CAMERA.items() if camera == LIVING_ROOM_CAMERA)

#: the lights in that room, the room's own group entity included
_LIVING_ROOM_GROUPS = {
    controller for controller, pairs in AGGREGATE_CONTROLLERS.items()
    if pairs and all(camera == LIVING_ROOM_CAMERA for _zone, camera in pairs)
}
LIVING_ROOM_LIGHTS: tuple = tuple(sorted(
    {light for zone in LIVING_ROOM_ZONES for light in ZONE_LIGHTS.get(zone, [])}
    | _LIVING_ROOM_GROUPS))

#: a zone somewhere else that a person can walk to during a film
ERRAND_ZONES: tuple = tuple(z for z in ZONE_LIGHTS if z not in LIVING_ROOM_ZONES)

TV_ENTITY: str = HOUSE["entities"]["movie_media_player"]

#: the Frigate occupancy sensor for the sofa, as the packages name it
SOFA_OCCUPANCY: str = f"binary_sensor.{SOFA_ZONE}_person_occupancy"
SOFA_STABLE: str = (f"binary_sensor.{LIVING_ROOM_CAMERA}_{SOFA_ZONE}"
                    "_person_occupancy_stable")


#: everything the simulator has to model: zone lights, group entities, ambient
EVERY_LIGHT: tuple = tuple(sorted(
    set(ALL_LIGHTS) | set(AGGREGATE_CONTROLLERS) | set(AMBIENT_LIGHTS)))

"""The MQTT facts: Home Assistant's mirror and Frigate's person topics.

Two parsers, no I/O. The caller hands every MQTT message to ``apply`` with the
time it arrived; both objects then answer questions about the house.

``MirrorState`` reads what the deployed ``living_lights_mqtt_mirror.yaml``
package publishes: one retained topic per entity,
``living_lights/mirror/<domain>/<object_id>``, carrying
``{entity_id, state, changed_at, attributes}``, plus
``living_lights/mirror/heartbeat`` once a minute. Any mirrored entity is
accepted, not only today's list, so a later mirror addition (the asleep
writer, for one) needs no code change here.

``FrigateState`` reads ``frigate/<camera>/person`` (a person count per camera)
and ``frigate/<camera>/<zone>/person`` (a person count per zone). Only the
cameras named in ``config/zones.json`` count: a person on the driveway is not
somebody in the house. Zone names are matched case-insensitively because
Frigate's configured names are capitalised where Home Assistant's slugs are
not. ``stable_occupied`` reproduces the generated
``binary_sensor.<camera>_<zone>_person_occupancy_stable``: occupied, or
unoccupied for less than the 90 s hold.

Raw motion is never read here, and no payload is ever logged: a malformed
message is counted, not echoed.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Mapping

from ..activity import ZoneMap

# Spelled out as literals rather than built from the base: the QA registry
# audit reads these constants straight out of the source with
# ``ast.literal_eval`` to write the topic schema in
# ``docs/qa/home-app-feature-audit.md``. ``test_inputs`` holds them consistent.
MIRROR_BASE = "living_lights/mirror"
MIRROR_TOPIC = "living_lights/mirror/#"
MIRROR_HEARTBEAT_TOPIC = "living_lights/mirror/heartbeat"
FRIGATE_BASE = "frigate"
FRIGATE_CAMERA_TOPIC = "frigate/+/person"
FRIGATE_ZONE_TOPIC = "frigate/+/+/person"
FRIGATE_PERSON_TOPICS = (FRIGATE_CAMERA_TOPIC, FRIGATE_ZONE_TOPIC)

STABLE_OCCUPANCY_HOLD_S = 90
"""The generator's ``STABLE_OCCUPANCY_HOLD_SECONDS``
(``tools/build-living-lights-yaml.py``): a zone that has just emptied still
reads occupied for this long. This is input conditioning that copies a Home
Assistant sensor, not a story duration, which is why it does not live in
``stories.py``."""

TV_ENTITY = "media_player.lg_tv"
AT_HOME_ENTITY = "input_boolean.user_at_home"
ASLEEP_ENTITY = "input_boolean.living_lights_asleep"
PROFILE_ENTITY = "sensor.living_lights_profile"
ACTUATE_ENTITY = "input_boolean.living_lights_actuate_from_belief_changes"
EGRESS_ENTITY = "input_boolean.living_lights_typesafe_egress_enabled"
FROM_ESTIMATOR_ENTITY = "input_boolean.living_lights_asleep_from_estimator"
ASLEEP_WRITER_ENTITY = "input_text.living_lights_asleep_writer"
"""Not in today's mirror list. Read if it ever appears; until then the
estimator sees no writer and its manual hold stays inert (see README)."""

COMMAND_HELPER_PREFIX = "input_text.living_lights_zone_"
COMMAND_HELPER_SUFFIX = "_last_command_id"
UNKNOWN_STATES = ("unknown", "unavailable", "none", "")


def parse_time(value: object) -> dt.datetime | None:
    """Parse an ISO 8601 stamp from the mirror; None when unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _decode(payload: object) -> str | None:
    """MQTT payloads reach us as bytes; return text or None."""
    if isinstance(payload, bytes):
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        return payload
    return None


def _as_bool(state: str | None) -> bool | None:
    if state is None:
        return None
    value = state.strip().lower()
    if value in ("on", "true", "home"):
        return True
    if value in ("off", "false", "not_home", "away"):
        return False
    return None


@dataclass(frozen=True)
class MirrorEntity:
    """One mirrored Home Assistant entity."""

    entity_id: str
    state: str
    changed_at: dt.datetime | None
    attributes: Mapping[str, object] = field(default_factory=dict)
    received_at: dt.datetime | None = None

    @property
    def known(self) -> bool:
        return self.state.strip().lower() not in UNKNOWN_STATES


class MirrorState:
    """Everything Home Assistant tells the publisher, by entity id."""

    def __init__(self) -> None:
        self.entities: dict[str, MirrorEntity] = {}
        self.heartbeat_at: dt.datetime | None = None
        self.last_message_at: dt.datetime | None = None
        self.messages = 0
        self.malformed = 0

    @staticmethod
    def topics() -> tuple[str, ...]:
        return (MIRROR_TOPIC,)

    # --- ingest ---------------------------------------------------------------

    def apply(self, topic: str, payload: object, now: dt.datetime) -> str | None:
        """Take one mirror message. Returns ``heartbeat``, ``entity`` or None."""
        if not topic.startswith(MIRROR_BASE + "/"):
            return None
        text = _decode(payload)
        if text is None:
            self.malformed += 1
            return None
        self.messages += 1
        self.last_message_at = now
        if topic == MIRROR_HEARTBEAT_TOPIC:
            stamp = parse_time(text)
            # A heartbeat we cannot parse still proves the mirror is alive.
            self.heartbeat_at = stamp or now
            return "heartbeat"
        rest = topic[len(MIRROR_BASE) + 1:]
        parts = rest.split("/")
        if len(parts) != 2 or not all(parts):
            self.malformed += 1
            return None
        entity_id = parts[0] + "." + parts[1]
        try:
            data = json.loads(text)
        except ValueError:
            self.malformed += 1
            return None
        if not isinstance(data, dict):
            self.malformed += 1
            return None
        state = data.get("state")
        if not isinstance(state, str):
            self.malformed += 1
            return None
        attributes = data.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        self.entities[entity_id] = MirrorEntity(
            entity_id=entity_id, state=state, changed_at=parse_time(data.get("changed_at")),
            attributes=attributes, received_at=now)
        return "entity"

    # --- questions ------------------------------------------------------------

    def entity(self, entity_id: str) -> MirrorEntity | None:
        return self.entities.get(entity_id)

    def state_of(self, entity_id: str) -> str | None:
        found = self.entities.get(entity_id)
        return found.state if found is not None else None

    def changed_at(self, entity_id: str) -> dt.datetime | None:
        found = self.entities.get(entity_id)
        return found.changed_at if found is not None else None

    def bool_of(self, entity_id: str) -> bool | None:
        return _as_bool(self.state_of(entity_id))

    @property
    def tv_state(self) -> str | None:
        return self.state_of(TV_ENTITY)

    @property
    def profile(self) -> str | None:
        state = self.state_of(PROFILE_ENTITY)
        if state is None or state.strip().lower() in UNKNOWN_STATES:
            return None
        return state.strip().lower()

    @property
    def user_at_home(self) -> bool | None:
        return self.bool_of(AT_HOME_ENTITY)

    @property
    def user_at_home_changed(self) -> dt.datetime | None:
        return self.changed_at(AT_HOME_ENTITY)

    @property
    def asleep_latch(self) -> bool | None:
        return self.bool_of(ASLEEP_ENTITY)

    @property
    def asleep_latch_changed(self) -> dt.datetime | None:
        return self.changed_at(ASLEEP_ENTITY)

    @property
    def asleep_writer(self) -> str | None:
        state = self.state_of(ASLEEP_WRITER_ENTITY)
        if state is None or state.strip().lower() in UNKNOWN_STATES:
            return None
        return state.strip()

    @property
    def actuate_from_beliefs(self) -> bool | None:
        return self.bool_of(ACTUATE_ENTITY)

    @property
    def egress_enabled(self) -> bool | None:
        return self.bool_of(EGRESS_ENTITY)

    @property
    def asleep_from_estimator(self) -> bool | None:
        return self.bool_of(FROM_ESTIMATOR_ENTITY)

    def command_helpers(self) -> dict[str, MirrorEntity]:
        """The per-zone last-command helpers, by zone slug."""
        out: dict[str, MirrorEntity] = {}
        for entity_id, item in self.entities.items():
            if entity_id.startswith(COMMAND_HELPER_PREFIX) and entity_id.endswith(COMMAND_HELPER_SUFFIX):
                zone = entity_id[len(COMMAND_HELPER_PREFIX):-len(COMMAND_HELPER_SUFFIX)]
                out[zone] = item
        return out

    def last_command_at(self) -> dt.datetime | None:
        """When a person last commanded a zone's lights, per the helpers.

        The helper carries an opaque command id, so the publisher cannot tell
        a brighten from a dim. It counts every explicit command as the
        estimator's ``last_brighten``: somebody issuing a lighting command is
        somebody awake, which is the legacy reading, and it only ever delays
        or clears the latch. The shadow report counts how often it fires.
        """
        newest: dt.datetime | None = None
        for item in self.command_helpers().values():
            if not item.known or item.changed_at is None:
                continue
            if newest is None or item.changed_at > newest:
                newest = item.changed_at
        return newest

    def as_dict(self) -> dict:
        """Counters for ``/healthz``; no entity values."""
        return {"entities": len(self.entities), "messages": self.messages,
                "malformed": self.malformed}


class FrigateState:
    """Person counts per camera and per zone, from Frigate's MQTT topics."""

    def __init__(self, zones: ZoneMap, stable_hold_s: float = STABLE_OCCUPANCY_HOLD_S) -> None:
        self.zones = zones
        self.stable_hold_s = float(stable_hold_s)
        self._zone_by_lower = {z.lower(): z for z in zones.zones}
        self._camera_count: dict[str, int] = {}
        self._camera_changed: dict[str, dt.datetime] = {}
        self._zone_count: dict[str, int] = {}
        self._zone_changed: dict[str, dt.datetime] = {}
        self.last_message_at: dt.datetime | None = None
        self.messages = 0
        self.ignored = 0
        self.malformed = 0

    @staticmethod
    def topics() -> tuple[str, ...]:
        return FRIGATE_PERSON_TOPICS

    # --- ingest ---------------------------------------------------------------

    def apply(self, topic: str, payload: object, now: dt.datetime) -> str | None:
        """Take one Frigate message. Returns ``camera``, ``zone`` or None."""
        parts = topic.split("/")
        if len(parts) < 3 or parts[0] != FRIGATE_BASE or parts[-1] != "person":
            return None
        camera = parts[1]
        if camera not in self.zones.cameras:
            self.ignored += 1
            return None
        text = _decode(payload)
        if text is None:
            self.malformed += 1
            return None
        try:
            count = int(float(text.strip()))
        except (TypeError, ValueError):
            self.malformed += 1
            return None
        self.messages += 1
        self.last_message_at = now
        if len(parts) == 3:
            return self._store(self._camera_count, self._camera_changed, camera, count, now, "camera")
        if len(parts) == 4:
            zone = self._zone_by_lower.get(parts[2].lower())
            if zone is None or self.zones.camera_of(zone) != camera:
                self.ignored += 1
                return None
            return self._store(self._zone_count, self._zone_changed, zone, count, now, "zone")
        self.ignored += 1
        return None

    def _store(self, counts: dict, changed: dict, key: str, count: int,
               now: dt.datetime, kind: str) -> str:
        if counts.get(key) != count:
            counts[key] = count
            changed[key] = now
        return kind

    # --- questions ------------------------------------------------------------

    def camera_person(self, camera: str) -> bool:
        return self._camera_count.get(camera, 0) > 0

    def camera_changed(self, camera: str) -> dt.datetime | None:
        return self._camera_changed.get(camera)

    def camera_seen(self, camera: str) -> bool:
        """True once this camera has reported any count at all."""
        return camera in self._camera_count

    def zone_occupied(self, zone: str) -> bool:
        return self._zone_count.get(zone, 0) > 0

    def zone_changed(self, zone: str) -> dt.datetime | None:
        return self._zone_changed.get(zone)

    def occupancy(self) -> dict[str, bool]:
        """Raw per-zone occupancy for every zone in the map."""
        return {zone: self.zone_occupied(zone) for zone in self.zones.zones}

    def stable_occupied(self, zone: str, now: dt.datetime) -> bool:
        """Occupied, or unoccupied for less than the 90 s hold."""
        if self.zone_occupied(zone):
            return True
        changed = self._zone_changed.get(zone)
        if changed is None:
            return False
        return (now - changed).total_seconds() < self.stable_hold_s

    def sofa_stable(self, now: dt.datetime) -> bool:
        return self.stable_occupied(self.zones.sofa_zone, now)

    def front_door_occupied(self) -> bool:
        return self.zone_occupied(self.zones.front_door_zone)

    def front_door_changed(self) -> dt.datetime | None:
        return self.zone_changed(self.zones.front_door_zone)

    def living_room_occupied(self) -> bool:
        return self.zones.living_room_occupied(self.occupancy())

    def as_dict(self) -> dict:
        """Counters for ``/healthz``; no zone values."""
        return {"messages": self.messages, "ignored": self.ignored,
                "malformed": self.malformed, "cameras_seen": sorted(self._camera_count)}

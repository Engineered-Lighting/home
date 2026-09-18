"""The entity set and its Home Assistant MQTT discovery payloads.

Four kinds of entity, exactly as the deployed packages expect them:

- ``binary_sensor.living_lights_tv_watching`` with the attributes
  ``state_machine``, ``p_attention``, ``since`` and ``request_id``;
- ``sensor.<camera>_<zone>_activity``, one per zone in ``config/zones.json``;
- ``sensor.living_lights_asleep_estimator``;
- ``sensor.lighting_publisher_heartbeat``, republished every 60 s (the
  generated ``binary_sensor.living_lights_publisher_fresh`` turns off 180 s
  after the last one).

``PUBLISHER_MODE=shadow`` appends ``_shadow`` to every object id and unique
id, the heartbeat included, so a shadow run is invisible to every generated
template: nothing in Home Assistant reads ``sensor.lighting_publisher_
heartbeat_shadow``, so ``publisher_fresh`` stays off and the legacy path keeps
the house. The state topics derive from the object id, so the two modes never
share a retained topic, and each mode has its own availability topic and its
own last will.

An entity is removed from Home Assistant by publishing an empty retained
payload to its discovery topic; ``removal_messages()`` builds exactly that,
for the rollback in the runbook.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..activity import ZoneMap

DISCOVERY_PREFIX = "homeassistant"
TOPIC_BASE = "living_lights/publisher"
SHADOW_SUFFIX = "_shadow"
AVAILABILITY_ONLINE = "online"
AVAILABILITY_OFFLINE = "offline"
DISCOVERY_REPUBLISH_S = 600
UNIQUE_PREFIX = "lighting_publisher_"

TV_OBJECT_ID = "living_lights_tv_watching"
ASLEEP_OBJECT_ID = "living_lights_asleep_estimator"
HEARTBEAT_OBJECT_ID = "lighting_publisher_heartbeat"
PAYLOAD_ON = "ON"
PAYLOAD_OFF = "OFF"
PAYLOAD_NONE = "None"
"""Home Assistant's MQTT platforms read the literal string ``None`` as "no
state": that is how a binary sensor is set to unknown, since any payload that
is neither ``payload_on`` nor ``payload_off`` would simply be ignored."""
UNKNOWN_STATE = "unknown"
"""What a sensor carries while health is down."""


@dataclass(frozen=True)
class Entity:
    """One published entity: where it lives and what its discovery says."""

    key: str
    component: str
    object_id: str
    name: str
    state_topic: str
    discovery_topic: str
    attributes_topic: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        return UNIQUE_PREFIX + self.object_id

    def discovery_payload(self, availability_topic: str) -> dict:
        """The discovery config Home Assistant reads."""
        payload = {
            "name": self.name,
            "object_id": self.object_id,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
            "availability_topic": availability_topic,
            "payload_available": AVAILABILITY_ONLINE,
            "payload_not_available": AVAILABILITY_OFFLINE,
        }
        if self.attributes_topic:
            payload["json_attributes_topic"] = self.attributes_topic
        payload.update(self.extra)
        return payload


@dataclass(frozen=True)
class EntitySet:
    """Every entity of one mode, plus the topics the mode owns."""

    mode: str
    shadow: bool
    availability_topic: str
    tv: Entity
    asleep: Entity
    heartbeat: Entity
    activity: dict[str, Entity]

    def all(self) -> tuple[Entity, ...]:
        """Every entity, in a stable order."""
        return (self.tv, self.asleep, self.heartbeat,
                *(self.activity[zone] for zone in sorted(self.activity)))

    def belief_entities(self) -> tuple[Entity, ...]:
        """Everything that goes ``unknown`` when health drops (not the heartbeat)."""
        return (self.tv, self.asleep, *(self.activity[zone] for zone in sorted(self.activity)))

    def state_topics(self) -> tuple[str, ...]:
        topics: list[str] = []
        for entity in self.all():
            topics.append(entity.state_topic)
            if entity.attributes_topic:
                topics.append(entity.attributes_topic)
        return tuple(topics)

    def discovery_messages(self) -> tuple[tuple[str, str], ...]:
        """``(topic, payload)`` pairs to publish retained on every connect."""
        return tuple((entity.discovery_topic,
                      json.dumps(entity.discovery_payload(self.availability_topic),
                                 sort_keys=True, ensure_ascii=True))
                     for entity in self.all())

    def removal_messages(self) -> tuple[tuple[str, str], ...]:
        """``(topic, "")`` pairs: an empty retained payload removes an entity."""
        return tuple((entity.discovery_topic, "") for entity in self.all())


def _suffix(shadow: bool) -> str:
    return SHADOW_SUFFIX if shadow else ""


def _titled(text: str) -> str:
    return text.replace("_", " ").title()


def build_entities(zones: ZoneMap, shadow: bool, topic_base: str = TOPIC_BASE,
                   discovery_prefix: str = DISCOVERY_PREFIX) -> EntitySet:
    """Build the entity set for one mode. ``shadow`` suffixes every object id."""
    suffix = _suffix(shadow)
    tag = " (shadow)" if shadow else ""
    base = topic_base.rstrip("/")
    availability = f"{base}/availability{suffix}"

    def make(key: str, component: str, object_id: str, name: str,
             attributes: bool = False, extra: dict | None = None) -> Entity:
        return Entity(
            key=key, component=component, object_id=object_id, name=name,
            state_topic=f"{base}/{object_id}/state",
            attributes_topic=f"{base}/{object_id}/attributes" if attributes else None,
            discovery_topic=f"{discovery_prefix}/{component}/{object_id}/config",
            extra=dict(extra or {}))

    tv = make("tv_watching", "binary_sensor", TV_OBJECT_ID + suffix,
              "Living Lights TV watching" + tag, attributes=True,
              extra={"payload_on": PAYLOAD_ON, "payload_off": PAYLOAD_OFF,
                     "icon": "mdi:television-play"})
    asleep = make("asleep_estimator", "sensor", ASLEEP_OBJECT_ID + suffix,
                  "Living Lights asleep estimator" + tag, attributes=True,
                  extra={"icon": "mdi:sleep"})
    heartbeat = make("heartbeat", "sensor", HEARTBEAT_OBJECT_ID + suffix,
                     "Lighting publisher heartbeat" + tag,
                     extra={"device_class": "timestamp", "icon": "mdi:heart-pulse"})
    activity: dict[str, Entity] = {}
    for zone in sorted(zones.zones):
        object_id = zones.entity_object_id(zone) + suffix
        activity[zone] = make(
            f"activity_{zone}", "sensor", object_id,
            f"{_titled(zones.camera_of(zone))} {_titled(zone)} activity" + tag,
            extra={"icon": "mdi:motion-sensor"})
    return EntitySet(mode="shadow" if shadow else "live", shadow=bool(shadow),
                     availability_topic=availability, tv=tv, asleep=asleep,
                     heartbeat=heartbeat, activity=activity)

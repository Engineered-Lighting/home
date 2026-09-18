"""What the publisher says on MQTT: discovery configs and retained state.

- ``discovery``: the entity set (object ids, unique ids, topics, the
  ``_shadow`` twins) and the Home Assistant MQTT discovery payloads;
- ``state``: retained publishing with change detection, the 60 s repeat, the
  startup clear and the clean-shutdown availability flip.

Neither module opens a socket: both take a client object with paho's
``publish`` signature, which the tests replace with a fake.
"""
from .discovery import (AVAILABILITY_OFFLINE, AVAILABILITY_ONLINE, DISCOVERY_PREFIX,
                        DISCOVERY_REPUBLISH_S, PAYLOAD_NONE, PAYLOAD_OFF, PAYLOAD_ON,
                        SHADOW_SUFFIX, UNKNOWN_STATE, Entity, EntitySet, build_entities)
from .state import STATE_REPEAT_S, StatePublisher

__all__ = [
    "AVAILABILITY_ONLINE", "AVAILABILITY_OFFLINE", "DISCOVERY_PREFIX", "DISCOVERY_REPUBLISH_S",
    "SHADOW_SUFFIX", "PAYLOAD_ON", "PAYLOAD_OFF", "PAYLOAD_NONE", "UNKNOWN_STATE",
    "Entity", "EntitySet", "build_entities",
    "STATE_REPEAT_S", "StatePublisher",
]

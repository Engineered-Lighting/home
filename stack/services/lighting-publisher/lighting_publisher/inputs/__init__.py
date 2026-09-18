"""Where the publisher's facts come from: the MQTT mirror and the observer.

- ``mirror``: the retained Home Assistant mirror (``living_lights/mirror/...``)
  and the Frigate person topics, both parsed from MQTT messages only;
- ``observer``: a polled HTTP reading of the observer's typed presence.

The publisher holds no Home Assistant token: everything it knows about the
house arrives through these two adapters. Both are pure parsers driven by the
caller's clock, so the tests need no broker, no observer and no socket.
"""
from .mirror import (FRIGATE_PERSON_TOPICS, MIRROR_HEARTBEAT_TOPIC, MIRROR_TOPIC,
                     STABLE_OCCUPANCY_HOLD_S, FrigateState, MirrorEntity, MirrorState)
from .observer import (DEFAULT_OBSERVER_URL, ObserverCamera, ObserverClient, ObserverError,
                       ObserverReading, UrllibTransport)

__all__ = [
    "MIRROR_TOPIC", "MIRROR_HEARTBEAT_TOPIC", "FRIGATE_PERSON_TOPICS",
    "STABLE_OCCUPANCY_HOLD_S", "MirrorEntity", "MirrorState", "FrigateState",
    "DEFAULT_OBSERVER_URL", "ObserverCamera", "ObserverClient", "ObserverError",
    "ObserverReading", "UrllibTransport",
]

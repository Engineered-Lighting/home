"""lighting_publisher: the Living Lights belief publisher's state machines.

Pure, deterministic logic for milestone M4 (shadow publisher with no Jev, no
egress and no Home Assistant token):

- ``stories``: every timing constant of stories T and S;
- ``beliefs``: the ``BeliefSource`` seam (only ``NoBeliefs`` ships here);
- ``tv_machine``: story T, ``binary_sensor.living_lights_tv_watching``;
- ``estimator``: story S, ``sensor.living_lights_asleep_estimator``;
- ``activity``: the per-zone activity sensors and the zone map;
- ``health``: mirror fresh and observer fresh and MQTT up.

Nothing here opens a socket or reads a clock: every ``update`` takes ``now``.
The MQTT, observer and container plumbing live next to this package and call
into it; the Jev client arrives in M6 through ``lighting_beliefs.egress``.
"""
from .stories import HEARTBEAT_S, MIN_FLIP_INTERVAL_S
from .beliefs import BeliefSource, Beliefs, NoBeliefs
from .tv_machine import TvMachine, TvDecision
from .estimator import AsleepEstimator, EstimatorInputs, CameraSignal, AsleepDecision
from .activity import ActivityTracker, ZoneMap, load_zones
from .health import Health, HealthStatus

__all__ = [
    "HEARTBEAT_S", "MIN_FLIP_INTERVAL_S",
    "BeliefSource", "Beliefs", "NoBeliefs",
    "TvMachine", "TvDecision",
    "AsleepEstimator", "EstimatorInputs", "CameraSignal", "AsleepDecision",
    "ActivityTracker", "ZoneMap", "load_zones",
    "Health", "HealthStatus",
]
__version__ = "0.1.0"

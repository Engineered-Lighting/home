"""Victoria occupancy and zone bridge (lane AL-2).

Turns Frigate's MQTT person counts for the den camera (and its desk/sofa zones)
into `occupancy`, `zone` and `health` events for the lighting brain, following
interface contract v1. It never controls lights.
"""

__version__ = "0.1.0"

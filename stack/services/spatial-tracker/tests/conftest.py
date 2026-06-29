import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from model_store import ApartmentModel  # noqa: E402


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> float:
        self.t += dt
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


def make_model(extra_zones=None, extra_devices=None, camera_calib=None):
    """Two adjacent rooms (living_room 0..4 x 0..4, kitchen 4..8 x 0..4) plus
    an office, a tv + speaker in the living room and a desk in the office."""
    zones = [
        {"id": "living_room", "name": "Living Room",
         "floor_polygon": [[0, 0], [4, 0], [4, 4], [0, 4]],
         "ceiling_height_m": 2.6, "frigate_camera": "living_room"},
        {"id": "kitchen", "name": "Kitchen",
         "floor_polygon": [[4, 0], [8, 0], [8, 4], [4, 4]],
         "ceiling_height_m": 2.6, "frigate_camera": "kitchen"},
        {"id": "office", "name": "Office",
         "floor_polygon": [[0, 4], [4, 4], [4, 8], [0, 8]],
         "ceiling_height_m": 2.6, "frigate_camera": "workshop"},
    ] + (extra_zones or [])
    devices = [
        {"id": "tv_living", "type": "tv", "name": "Living Room TV",
         "ha_entity_id": "media_player.living_tv", "pos": [2.0, 0.2, 0.9],
         "room_id": "living_room"},
        {"id": "sonos_living", "type": "speaker", "name": "Sonos",
         "ha_entity_id": "media_player.sonos_living", "pos": [3.8, 3.8, 1.0],
         "room_id": "living_room"},
        {"id": "desk_office", "type": "other", "name": "Desk",
         "ha_entity_id": None, "pos": [1.0, 6.0, 0.75], "room_id": "office"},
    ] + (extra_devices or [])
    if camera_calib:
        devices.append(camera_calib)
    return ApartmentModel({
        "exists": True, "revision": 1, "schema_version": 1,
        "zones": zones, "devices": devices,
    })


@pytest.fixture
def model():
    return make_model()

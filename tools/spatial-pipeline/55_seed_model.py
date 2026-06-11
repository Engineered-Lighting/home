#!/usr/bin/env python3
"""55_seed_model.py — seed apartment_model v2: reasoned placements.

Sources:
- Light->room from the legacy spatial_model (scp'd from HAOS:
  data/model/spatial_model_legacy.json) — the user's own hand mapping.
- Zones + camera poses reasoned from the floorplan render matched against
  each camera's live snapshot (couch/door/window arrangement, galley
  direction, oval dining table). Workshop is EXCLUDED per user direction.

Everything is source="proposed", confidence 0.5 — refined in edit mode.
"""
from __future__ import annotations

import json
import math

from pipeline_util import APP_DATA, APP_SIM, MODEL

# ---- zones (apartment frame, meters; floorplan-derived) ----
ZONES = [
    {"id": "living_room", "name": "living room", "color": "#b8d8ff",
     "floor_polygon": [[7.4, 0.6], [13.6, 0.6], [13.6, 6.2], [7.4, 6.2]],
     "ceiling_height_m": 2.5, "frigate_camera": "living_room", "source": "seed"},
    {"id": "kitchen", "name": "kitchen", "color": "#a8ffd8",
     "floor_polygon": [[3.0, 2.6], [7.4, 2.6], [7.4, 6.5], [3.0, 6.5]],
     "ceiling_height_m": 2.4, "frigate_camera": "kitchen", "source": "seed"},
    {"id": "dining_room", "name": "dining room", "color": "#ffe2a8",
     "floor_polygon": [[3.0, 0.6], [7.4, 0.6], [7.4, 2.6], [3.0, 2.6]],
     "ceiling_height_m": 2.4, "frigate_camera": "dining_room", "source": "seed"},
]

# ---- lights: explicit reasoned spots (entity -> pos) ----
LIGHTS = {
    # dining: the oval table sits lower-center-left
    "light.dining_table_left":  [4.5, 1.5, 2.3],
    "light.dining_table_right": [5.5, 1.5, 2.3],
    # kitchen: island lights over the island block, sink light at the top counter
    "light.island_left":  [4.2, 3.6, 2.3],
    "light.island_right": [5.2, 3.6, 2.3],
    "light.sink":         [5.0, 6.0, 2.3],
    # living room: front = door wall (x~8), rear = far wall (x~12.6)
    "light.front_left":  [8.6, 5.0, 2.4],
    "light.front_right": [8.6, 1.8, 2.4],
    "light.rear_left":   [12.4, 5.0, 2.4],
    "light.rear_right":  [12.4, 1.8, 2.4],
    "light.office":      [10.5, 5.6, 2.4],
    "switch.ambient_light_left_mss110_main_channel":  [9.0, 1.4, 0.8],
    "switch.ambient_light_right_mss110_main_channel": [12.8, 1.4, 0.8],
    "switch.smart_plug_mini_mss110_main_channel":     [13.1, 3.2, 0.8],
    # workshop switches deliberately excluded (workshop is out of scope)
}

# ---- cameras: pose reasoned from snapshot<->floorplan matching ----
CAMERAS = [
    # looks across coffee table + couch toward the front door wall
    {"id": "living_room", "pos": [8.0, 5.8, 2.3], "look_at": [10.5, 1.5]},
    # perched by the fridge, looking down the galley toward the hallway
    {"id": "kitchen", "pos": [6.8, 5.5, 2.3], "look_at": [3.8, 5.6]},
    # corner view across the oval table toward the window wall
    {"id": "dining_room", "pos": [6.9, 4.0, 2.3], "look_at": [4.9, 1.4]},
]


def room_of(x, y):
    for z in ZONES:
        xs = [p[0] for p in z["floor_polygon"]]
        ys = [p[1] for p in z["floor_polygon"]]
        if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys):
            return z["id"]
    return None


def slug(s):
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def main():
    legacy = {}
    p = MODEL / "spatial_model_legacy.json"
    if p.is_file():
        legacy = json.loads(p.read_text(encoding="utf-8")).get("light_camera", {})

    devices = []
    for ent, pos in LIGHTS.items():
        room = legacy.get(ent) or room_of(pos[0], pos[1])
        if room == "workshop":
            continue
        devices.append({
            "id": f"dev-{slug(ent)}", "type": "light",
            "name": ent.split(".", 1)[1].replace("_mss110_main_channel", "")
                       .replace("_", " "),
            "ha_entity_id": ent, "pos": pos, "yaw_rad": 0,
            "height_preset": "ceiling" if pos[2] > 2 else "custom",
            "room_id": room or room_of(pos[0], pos[1]),
            "controllable": True, "confidence": 0.5, "source": "proposed",
        })

    for cam in CAMERAS:
        dx, dy = cam["look_at"][0] - cam["pos"][0], cam["look_at"][1] - cam["pos"][1]
        devices.append({
            "id": f"dev-camera_{cam['id']}", "type": "camera",
            "name": f"{cam['id'].replace('_', ' ')} cam",
            "ha_entity_id": f"camera.{cam['id']}",
            "pos": cam["pos"], "yaw_rad": round(math.atan2(dy, dx), 2),
            "height_preset": "wall", "room_id": cam["id"],
            "controllable": False, "confidence": 0.5, "source": "proposed",
            "camera": {"frigate_name": cam["id"]},
        })

    seed = {"schema_version": 1, "seeded": True, "zones": ZONES, "devices": devices}
    for d in (APP_SIM, APP_DATA):
        (d / "seed-model.json").write_text(json.dumps(seed, indent=1), encoding="utf-8")
    print(f"seed v2: {len(ZONES)} zones, {len(devices)} devices "
          f"({sum(1 for d in devices if d['type'] == 'light')} lights, "
          f"{sum(1 for d in devices if d['type'] == 'camera')} cameras) — workshop excluded")


if __name__ == "__main__":
    main()

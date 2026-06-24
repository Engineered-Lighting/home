#!/usr/bin/env python3
"""test-init-spatial-model.py — unit tests for the pure helpers in
init-spatial-model.py (Addendum 38 Phase 1, hand-drawn light map).

Covers grid sizing, the Frigate zone-coordinate parser, the light-target
set, and the light → home-camera map. Pure functions only — no Frigate,
no HA, no network (build_blank_model is exercised by running the script
itself). Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Windows stdout defaults to cp1252 — force UTF-8 for the arrows below.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "ism", HERE / "init-spatial-model.py")
ism = importlib.util.module_from_spec(_spec)             # type: ignore
_spec.loader.exec_module(ism)                            # type: ignore

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ───────────────────────── grid_dims ──────────────────────────

c = ism.grid_dims(1280, 720)
check("grid_dims 1280x720 → 32x18 cells, 40px square",
      c == (32, 18, 40, 40), str(c))

c = ism.grid_dims(1920, 1080)
check("grid_dims 1920x1080 → 32x18, 60px cells", c == (32, 18, 60, 60), str(c))

c = ism.grid_dims(640, 480)
check("grid_dims 640x480 (4:3) → 32 cols, square-ish cells",
      c[0] == 32 and abs(c[2] - c[3]) <= 1, str(c))


# ──────────────────── parse_zone_coordinates ──────────────────
# Frigate zone geometry shares the normalised [0,1] camera-frame space.

p = ism.parse_zone_coordinates("0.1,0.2,0.3,0.4,0.5,0.6")
check("parse_zone_coordinates: flat string → [[x,y],...]",
      p == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]], str(p))

check("parse_zone_coordinates: empty / None → []",
      ism.parse_zone_coordinates("") == []
      and ism.parse_zone_coordinates(None) == [], "")

p = ism.parse_zone_coordinates("0.1,0.2,0.3")
check("parse_zone_coordinates: odd count drops the unpaired tail",
      p == [[0.1, 0.2]], str(p))

check("parse_zone_coordinates: non-numeric token → []",
      ism.parse_zone_coordinates("0.1,bad,0.3,0.4") == [], "")

p = ism.parse_zone_coordinates([[0.1, 0.2], [0.3, 0.4]])
check("parse_zone_coordinates: already-paired list passes through",
      p == [[0.1, 0.2], [0.3, 0.4]], str(p))


# ───────────────────── light-target set ───────────────────────

targets = ism.load_calibration_targets()
expected_unique = len(set(targets))
check("load_calibration_targets: unique entities",
      len(targets) == expected_unique and expected_unique >= len(ism.EXTRA_CALIBRATION_TARGETS),
      str(len(targets)))
check("load_calibration_targets: neon plug INCLUDED",
      ism.NEON_PLUG in targets, "neon plug missing!")
check("load_calibration_targets: all EXTRA targets present",
      all(e in targets for e in ism.EXTRA_CALIBRATION_TARGETS),
      str(ism.EXTRA_CALIBRATION_TARGETS))


# ───────────────── light → home camera map ────────────────────

cam_map = ism.build_light_camera_map()
check("build_light_camera_map: every light target is mapped",
      all(e in cam_map for e in targets),
      str(sorted(set(targets) - set(cam_map))))
check("build_light_camera_map: dining table light → dining_room",
      cam_map.get("light.dining_table_left") == "dining_room",
      str(cam_map.get("light.dining_table_left")))
check("build_light_camera_map: sink → kitchen",
      cam_map.get("light.sink") == "kitchen", str(cam_map.get("light.sink")))
check("build_light_camera_map: office → living_room",
      cam_map.get("light.office") == "living_room",
      str(cam_map.get("light.office")))
check("build_light_camera_map: workshop light → workshop",
      cam_map.get("switch.workshop_light_left_mss110_main_channel")
      == "workshop",
      str(cam_map.get("switch.workshop_light_left_mss110_main_channel")))
check("build_light_camera_map: neon plug → living_room (office area)",
      cam_map.get(ism.NEON_PLUG) == "living_room",
      str(cam_map.get(ism.NEON_PLUG)))


print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(0 if _failed == 0 else 1)

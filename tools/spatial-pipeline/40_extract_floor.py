#!/usr/bin/env python3
"""40_extract_floor.py — walkable floor polygon for the tracker's ray-cast and
the app's sanity hull. 5 cm occupancy grid from near-floor mesh vertices ->
morphological close -> contours -> Douglas-Peucker -> floor.json (Z-up, meters).
"""
from __future__ import annotations

import json

import cv2
import numpy as np
import trimesh

from pipeline_util import APP_DATA, MODEL, OUT, sha8, update_manifest

CELL = 0.05
mesh = trimesh.load(OUT / "apartment_mesh_debug.glb", force="mesh")
v = np.asarray(mesh.vertices)
floor = v[np.abs(v[:, 2]) < 0.10][:, :2]
print(f"near-floor vertices: {len(floor):,}")

mn = floor.min(0) - CELL
mx = floor.max(0) + CELL
size = np.ceil((mx - mn) / CELL).astype(int) + 1
grid = np.zeros((size[1], size[0]), np.uint8)
ij = ((floor - mn) / CELL).astype(int)
grid[ij[:, 1], ij[:, 0]] = 255
grid = cv2.morphologyEx(grid, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
grid = cv2.morphologyEx(grid, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

contours, hierarchy = cv2.findContours(grid, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
polys = []
for k, c in enumerate(contours):
    if cv2.contourArea(c) < 40:  # < 0.1 m^2
        continue
    approx = cv2.approxPolyDP(c, 0.05 / CELL, True).reshape(-1, 2)
    pts = (approx * CELL + mn).round(3).tolist()
    outer = hierarchy[0][k][3] == -1
    polys.append({"points": pts, "outer": bool(outer),
                  "area_m2": round(cv2.contourArea(c) * CELL * CELL, 2)})

polys.sort(key=lambda p: -p["area_m2"])
out = {
    "frame": "z_up_metric_floor0",
    "walkable_polygon": polys[0]["points"] if polys else [],
    "holes": [p["points"] for p in polys[1:] if not p["outer"]],
    "area_m2": polys[0]["area_m2"] if polys else 0,
}
(MODEL / "floor.json").write_text(json.dumps(out), encoding="utf-8")
(APP_DATA / "floor.json").write_text(json.dumps(out), encoding="utf-8")
update_manifest({"floor.json": {"sha8": sha8(APP_DATA / "floor.json"),
                                "bytes": (APP_DATA / "floor.json").stat().st_size}})
print(f"walkable area: {out['area_m2']} m2, {len(out['walkable_polygon'])} vertices, "
      f"{len(out['holes'])} holes -> floor.json")

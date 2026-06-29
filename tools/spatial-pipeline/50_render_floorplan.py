#!/usr/bin/env python3
"""50_render_floorplan.py — top-down floorplan render from the white cloud.
Intensity-weighted XY rasterization, height-tinted: floor dark, furniture mid,
walls/ceiling-adjacent bright. Output: data/processed/floorplan.png (+ meta
with the px<->m mapping) — the edit-mode underlay and a placement reference.
"""
from __future__ import annotations

import json

import cv2
import numpy as np

from pipeline_util import APP_DATA, OUT

PX_PER_M = 120


def read_points(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        n = int([l for l in header.decode().splitlines()
                 if l.startswith("element vertex")][0].split()[-1])
        rec = np.fromfile(f, dtype=[("xyz", "<f4", 3), ("i", "u1")], count=n)
    return rec["xyz"], rec["i"]


pos, inten = read_points(APP_DATA / "points.ply")
mn = pos[:, :2].min(0) - 0.3
mx = pos[:, :2].max(0) + 0.3
size = np.ceil((mx - mn) * PX_PER_M).astype(int)
img = np.zeros((size[1], size[0]), np.float32)
cnt = np.zeros_like(img)

ij = ((pos[:, :2] - mn) * PX_PER_M).astype(int)
w = (0.35 + 0.65 * np.clip(pos[:, 2] / 2.2, 0, 1)) * (inten / 255.0)
np.add.at(img, (ij[:, 1], ij[:, 0]), w)
np.add.at(cnt, (ij[:, 1], ij[:, 0]), 1.0)
img = img / np.maximum(cnt, 1)
img = np.clip(img / np.percentile(img[img > 0], 97), 0, 1)
img8 = (np.flipud(img) * 255).astype(np.uint8)  # +y up in the image
out = cv2.cvtColor(img8, cv2.COLOR_GRAY2BGR)

# 1 m grid
for x in range(0, int(mx[0] - mn[0]) + 1):
    px = int((x - (mn[0] % 1 if mn[0] % 1 else 0) + (0 - mn[0]) % 1) * PX_PER_M)
for gx in np.arange(np.ceil(mn[0]), mx[0], 1.0):
    px = int((gx - mn[0]) * PX_PER_M)
    cv2.line(out, (px, 0), (px, out.shape[0]), (40, 40, 40), 1)
for gy in np.arange(np.ceil(mn[1]), mx[1], 1.0):
    py = out.shape[0] - 1 - int((gy - mn[1]) * PX_PER_M)
    cv2.line(out, (0, py), (out.shape[1], py), (40, 40, 40), 1)

cv2.imwrite(str(OUT / "floorplan.png"), out)
meta = {"px_per_m": PX_PER_M, "origin_xy": [float(mn[0]), float(mn[1])],
        "size_px": [int(size[0]), int(size[1])], "y_axis": "up"}
(OUT / "floorplan.json").write_text(json.dumps(meta, indent=2))
print(f"floorplan {size[0]}x{size[1]} px, origin {mn.round(2)}, {PX_PER_M} px/m")

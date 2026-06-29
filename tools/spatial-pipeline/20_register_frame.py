#!/usr/bin/env python3
"""20_register_frame.py — compute T_scan->apartment from the Scaniverse mesh.

Apartment frame: right-handed, Z-up, meters, floor plane z=0, origin at the
min-corner of the floor's oriented bbox, +X along the long wall axis.

Method: glTF is Y-up. Find near-horizontal, low-elevation, area-weighted faces
-> least-squares floor plane -> rotate normal to +Z -> floor to z=0 ->
minAreaRect yaw so the long axis is +X -> min-corner to origin.
"""
from __future__ import annotations

import json

import cv2
import numpy as np
import trimesh

from pipeline_util import MODEL, RAW, rotation_between, save_registration

mesh = trimesh.load(RAW / "scan_mesh.glb", force="mesh")
print(f"mesh: {len(mesh.vertices):,} verts, {len(mesh.faces):,} faces")
print(f"bbox (as stored, glTF Y-up): {mesh.bounds[0].round(2)} .. {mesh.bounds[1].round(2)}")

up = np.array([0.0, 1.0, 0.0])  # glTF Y-up
fn = mesh.face_normals
fc = mesh.triangles_center
fa = mesh.area_faces

heights = fc @ up
h_lo, h_hi = heights.min(), heights.max()
horiz = np.abs(fn @ up) > 0.90
low = heights < h_lo + 0.25 * (h_hi - h_lo)
cand = horiz & low
print(f"floor candidate faces: {cand.sum():,} ({fa[cand].sum():.1f} m2)")

# area-weighted plane fit on candidate centroids, 2 refinement rounds
pts = fc[cand]
w = fa[cand]
for round_ in range(3):
    centroid = np.average(pts, axis=0, weights=w)
    d = pts - centroid
    cov = (d * w[:, None]).T @ d
    normal = np.linalg.svd(cov)[0][:, -1]
    if normal @ up < 0:
        normal = -normal
    dist = np.abs(d @ normal)
    keep = dist < 0.02
    if round_ < 2:
        pts, w = pts[keep], w[keep]
rms = float(np.sqrt(np.average(dist[keep] ** 2, weights=w[keep])))
print(f"floor plane: normal={normal.round(4)}, inliers={keep.sum():,}, rms={rms*1000:.1f} mm")

# rotate floor normal -> +Z (this also converts the Y-up scene to Z-up)
R1 = rotation_between(normal, np.array([0.0, 0.0, 1.0]))
v_r = mesh.vertices @ R1.T
floor_z = np.average((pts[keep] @ R1.T)[:, 2], weights=w[keep])

# yaw from the floor footprint's oriented bbox (long axis -> +X)
floor_pts = v_r[np.abs(v_r[:, 2] - floor_z) < 0.06][:, :2].astype(np.float32)
rect = cv2.minAreaRect(floor_pts)
(cx, cy), (w_r, h_r), ang = rect
yaw = np.deg2rad(ang if w_r >= h_r else ang + 90.0)
R2 = np.array([[np.cos(-yaw), -np.sin(-yaw), 0], [np.sin(-yaw), np.cos(-yaw), 0], [0, 0, 1]])

R = R2 @ R1
v2 = mesh.vertices @ R.T
v2[:, 2] -= floor_z
mn = v2[:, :2].min(axis=0)
t = np.array([-mn[0], -mn[1], -floor_z])
# full T: x' = R x + t  (note floor_z subtraction folded into t via R-space)
T = np.eye(4)
T[:3, :3] = R
T[:3, 3] = [-mn[0], -mn[1], -floor_z]

v_apt = v2.copy()
v_apt[:, :2] -= mn
ext = v_apt.max(axis=0) - v_apt.min(axis=0)
print(f"apartment extents: {ext[0]:.2f} x {ext[1]:.2f} m footprint, {ext[2]:.2f} m height")
print(f"z range: {v_apt[:,2].min():.3f} .. {v_apt[:,2].max():.3f}")

save_registration({
    "T_mesh": T.tolist(),
    "T_splat": None,  # set by 25_transform_assets.py after alignment verify
    "floor_rms_mm": round(rms * 1000, 2),
    "extents_m": [round(float(x), 3) for x in ext],
    "source_up": "y",
    "frame": "z_up_metric_floor0",
})
print(f"wrote {MODEL / 'registration.json'}")

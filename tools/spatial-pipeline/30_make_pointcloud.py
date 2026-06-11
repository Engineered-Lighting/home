#!/usr/bin/env python3
"""30_make_pointcloud.py — generate the white point cloud for the app.

Fork of EngineeredLightingWebsite/scripts/convert_scan.py with the plan's
changes applied:
  - METRIC PRESERVED: the original's scale-longest-axis-to-8 step is DELETED.
  - points = Gaussian means from the splat PLY (alpha > 0.5) + mesh surface fill
  - ceiling cut at ABSOLUTE z > 2.2 m (not a percentile)
  - statistical outlier removal (20-NN, 2 sigma), sigma=0.003 m jitter
  - normals dropped; per-point intensity (u8) from splat luminance
  - 2 cm voxel dedupe, cap 1.2 M, shuffled for progressive-load aesthetics
  - output PLY is Z-up apartment frame ("homeapt-points v1 zup")

Outputs: app/data/apartment/points.ply (full) + app/src/assets/apartment/
points.sim.ply (<=180k, bundled sim fixture).
"""
from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from pipeline_util import (
    APP_DATA, APP_SIM, OUT, RAW, apply_T, load_registration, read_3dgs_ply,
    sha8, update_manifest, write_points_ply,
)

rng = np.random.default_rng(7)
reg = load_registration()
T_splat = np.array(reg["T_splat"])

pos, rgb, alpha = read_3dgs_ply(RAW / "scan_splat.ply")
keep = alpha > 0.5
pos, rgb = pos[keep], rgb[keep]
print(f"splat means after alpha filter: {len(pos):,}")
pos = apply_T(T_splat, pos).astype(np.float32)

# floaters + misalignment guard: keep only splat points near the mesh surface
mesh = trimesh.load(OUT / "apartment_mesh_debug.glb", force="mesh")
surf, _ = trimesh.sample.sample_surface(mesh, 250_000)
near_d, _ = cKDTree(surf).query(pos, k=1, workers=-1)
m = near_d < 0.20
pos, rgb = pos[m], rgb[m]
print(f"after mesh-proximity filter (<20 cm): {len(pos):,}")

# luminance -> intensity, slightly lifted so dark furniture still reads
lum = (0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2])
intensity = np.clip(60 + lum * 195, 0, 255)

# mesh surface fill (uniform; voxel dedupe below prevents doubling dense areas)
fill, _ = trimesh.sample.sample_surface(mesh, 400_000)
fill = np.asarray(fill, dtype=np.float32)
fill_int = np.full(len(fill), float(np.median(intensity)) * 0.85)

pos = np.vstack([pos, fill])
intensity = np.concatenate([intensity, fill_int])

# height trim: drop ceiling (z > 2.2) and below-floor junk (z < -0.1)
m = (pos[:, 2] <= 2.2) & (pos[:, 2] >= -0.10)
pos, intensity = pos[m], intensity[m]
print(f"after ceiling/floor trim: {len(pos):,}")

# statistical outlier removal: 20-NN mean distance, drop > mean + 2 sigma
tree = cKDTree(pos)
d, _ = tree.query(pos, k=21, workers=-1)
mean_d = d[:, 1:].mean(axis=1)
thresh = mean_d.mean() + 2.0 * mean_d.std()
m = mean_d < thresh
pos, intensity = pos[m], intensity[m]
print(f"after outlier removal: {len(pos):,}")

# 2 cm voxel dedupe (random representative via pre-shuffle)
order = rng.permutation(len(pos))
pos, intensity = pos[order], intensity[order]
vox = np.floor(pos / 0.02).astype(np.int64)
_, first = np.unique(vox, axis=0, return_index=True)
pos, intensity = pos[first], intensity[first]
print(f"after 2 cm voxel dedupe: {len(pos):,}")

# cap + jitter + shuffle (shuffle => progressive load materializes uniformly)
if len(pos) > 1_200_000:
    sel = rng.choice(len(pos), 1_200_000, replace=False)
    pos, intensity = pos[sel], intensity[sel]
pos = pos + rng.normal(0, 0.003, pos.shape).astype(np.float32)
order = rng.permutation(len(pos))
pos, intensity = pos[order], intensity[order]

ext = pos.max(0) - pos.min(0)
print(f"final: {len(pos):,} pts, extents {ext[0]:.2f} x {ext[1]:.2f} x {ext[2]:.2f} m")

write_points_ply(APP_DATA / "points.ply", pos, intensity)
n_sim = min(180_000, len(pos))
write_points_ply(APP_SIM / "points.sim.ply", pos[:n_sim], intensity[:n_sim])

update_manifest({
    "points.ply": {"sha8": sha8(APP_DATA / "points.ply"),
                   "bytes": (APP_DATA / "points.ply").stat().st_size,
                   "points": int(len(pos))},
    "points.sim.ply": {"sha8": sha8(APP_SIM / "points.sim.ply"),
                       "bytes": (APP_SIM / "points.sim.ply").stat().st_size,
                       "points": int(n_sim)},
})
print(f"wrote {APP_DATA / 'points.ply'} and {APP_SIM / 'points.sim.ply'}")

#!/usr/bin/env python3
"""27_make_display_mesh.py — dollhouse display mesh for the app's mesh mode.

Takes the full textured apartment mesh and removes ceiling faces (centroid
z > 2.03 m) so rooms read from the overview pose, plus crops to the same
command-center room-union as the cloud/splat. UVs are per-vertex, so face
removal preserves the texture atlas. Output: app/data/apartment/mesh.glb
(display); the untouched original stays at mesh_full.glb.
"""
from __future__ import annotations

import shutil

import numpy as np
import trimesh

from pipeline_util import APP_DATA, OUT

CORE_BOXES = [
    {"x": (2.55, 7.35), "y": (0.40, 6.60)},
    {"x": (7.35, 13.75), "y": (0.50, 6.35)},
]
CEILING_Z = 2.03

src = OUT / "apartment_mesh_debug.glb"
full_out = APP_DATA / "mesh_full.glb"
shutil.copy(src, full_out)

scene = trimesh.load(src, process=False)
meshes = scene.geometry if hasattr(scene, "geometry") else {"m": scene}
kept_total, removed_total = 0, 0
for name, mesh in meshes.items():
    cent = mesh.triangles_center
    keep = cent[:, 2] <= CEILING_Z
    core = np.zeros(len(cent), bool)
    for b in CORE_BOXES:
        core |= ((cent[:, 0] >= b["x"][0]) & (cent[:, 0] <= b["x"][1]) &
                 (cent[:, 1] >= b["y"][0]) & (cent[:, 1] <= b["y"][1]))
    keep &= core
    removed_total += int((~keep).sum())
    kept_total += int(keep.sum())
    mesh.update_faces(keep)

out = APP_DATA / "mesh.glb"
scene.export(out)
print(f"display mesh: kept {kept_total:,} faces, removed {removed_total:,} "
      f"(ceiling + out-of-core) -> {out} ({out.stat().st_size/1e6:.1f} MB)")

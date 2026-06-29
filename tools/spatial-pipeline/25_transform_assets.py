#!/usr/bin/env python3
"""25_transform_assets.py — transform mesh to apartment frame; globally register
the splat to the mesh (the audit + first run proved they do NOT share a frame).

Splat registration = gravity-align the splat independently (its own floor-plane
RANSAC) -> global yaw + XY translation search via FFT phase correlation of
floor-plan occupancy grids -> point-to-point ICP refinement. Residual gate 5 cm.
"""
from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from pipeline_util import (
    APP_DATA, OUT, RAW, apply_T, load_registration, read_3dgs_ply,
    rotation_between, save_registration, sha8, update_manifest,
)

rng = np.random.default_rng(3)
reg = load_registration()
T_mesh = np.array(reg["T_mesh"])

mesh = trimesh.load(RAW / "scan_mesh.glb", force="mesh")
mesh.apply_transform(T_mesh)
mesh.export(OUT / "apartment_mesh_debug.glb")
print(f"debug mesh: {len(mesh.faces):,} tris")
try:
    coll = mesh.simplify_quadric_decimation(face_count=50_000)
except BaseException as e:
    print(f"decimation unavailable ({e}); using full mesh")
    coll = mesh
coll.export(OUT / "apartment_collision.glb")
print(f"collision mesh: {len(coll.faces):,} tris")

surf_pts, _ = trimesh.sample.sample_surface(mesh, 200_000)
surf_tree = cKDTree(surf_pts)


def residual(p, n=8000):
    sel = rng.choice(len(p), min(n, len(p)), replace=False)
    d, _ = surf_tree.query(p[sel], k=1)
    return float(np.median(d))


# ---- load splat ----
pos_raw, rgb, alpha = read_3dgs_ply(RAW / "scan_splat.ply")
sp = pos_raw[alpha > 0.5]
print(f"splat: {len(pos_raw):,} gaussians, {len(sp):,} with alpha>0.5")
print(f"splat raw bbox: {sp.min(0).round(2)} .. {sp.max(0).round(2)}")

# ---- step 1: gravity-align the splat via its own dominant horizontal plane ----
sub = sp[rng.choice(len(sp), min(60_000, len(sp)), replace=False)]
best_inliers, best_plane = -1, None
for _ in range(800):
    tri = sub[rng.choice(len(sub), 3, replace=False)]
    n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    norm = np.linalg.norm(n)
    if norm < 1e-9:
        continue
    n = n / norm
    d = np.abs((sub - tri[0]) @ n)
    inl = (d < 0.04)
    cnt = int(inl.sum())
    if cnt > best_inliers:
        # require the plane's inliers to be spread out (a floor, not a wall patch)
        spread = sub[inl].std(axis=0)
        if np.sort(spread)[1] > 1.0:  # 2nd-largest std > 1 m
            best_inliers, best_plane = cnt, (n, tri[0], inl)

n, p0, inl = best_plane
# sign: most points should be ABOVE the floor
if np.median((sub - p0) @ n) < 0:
    n = -n
print(f"splat floor plane: {best_inliers:,}/{len(sub):,} inliers, normal {n.round(3)}")
Rg = rotation_between(n, np.array([0.0, 0.0, 1.0]))
sp_g = sub @ Rg.T
floor_z = np.median(sp_g[inl][:, 2])
sp_g[:, 2] -= floor_z

# ---- step 2: global yaw + XY via FFT phase correlation of occupancy grids ----
CELL = 0.10
PAD = 256  # cells; 25.6 m canvas fits both footprints


def occupancy(xy):
    g = np.zeros((PAD, PAD), np.float32)
    ij = np.floor(xy / CELL).astype(int) + PAD // 2
    ok = (ij >= 0).all(1) & (ij < PAD).all(1)
    g[ij[ok, 1], ij[ok, 0]] = 1.0
    return g


mesh_xy = surf_pts[surf_pts[:, 2] < 2.0][:, :2]
mesh_c = mesh_xy.mean(0)
G_mesh = occupancy(mesh_xy - mesh_c)
F_mesh = np.fft.rfft2(G_mesh)

body = sp_g[(sp_g[:, 2] > 0.05) & (sp_g[:, 2] < 2.0)]
body_c = body[:, :2].mean(0)

best = None
for yaw_deg in range(0, 360, 2):
    a = np.deg2rad(yaw_deg)
    Ry = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    xy = (body[:, :2] - body_c) @ Ry.T
    G = occupancy(xy)
    corr = np.fft.irfft2(F_mesh * np.conj(np.fft.rfft2(G)), s=G_mesh.shape)
    peak = float(corr.max())
    if best is None or peak > best[0]:
        iy, ix = np.unravel_index(int(corr.argmax()), corr.shape)
        # circular shift -> signed offset
        dy = iy if iy < PAD // 2 else iy - PAD
        dx = ix if ix < PAD // 2 else ix - PAD
        best = (peak, yaw_deg, dx * CELL, dy * CELL)

peak, yaw_deg, tx, ty = best
print(f"global search: yaw={yaw_deg} deg, shift=({tx:.2f},{ty:.2f}) m, score={peak:.0f}")

a = np.deg2rad(yaw_deg)
Ryaw3 = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
# compose: x_apt ~= Ryaw*(Rg*x - [0,0,floor_z] - [body_c,0]) + [tx,ty,0] + [mesh_c,0]
T0 = np.eye(4)
T0[:3, :3] = Ryaw3 @ Rg
shift = (Ryaw3 @ np.array([-body_c[0], -body_c[1], -floor_z]) +
         np.array([tx + mesh_c[0], ty + mesh_c[1], 0.0]))
T0[:3, 3] = shift

r0 = residual(apply_T(T0, sp))
print(f"after global init: median NN {r0*100:.1f} cm")

# ---- step 3: ICP refinement ----
T_splat = T0.copy()
sample = sp[rng.choice(len(sp), 12_000, replace=False)]
cur = apply_T(T_splat, sample)
for it in range(40):
    d, idx = surf_tree.query(cur, k=1)
    keep = d < max(0.08, float(np.percentile(d, 60)))
    src, dst = cur[keep], surf_pts[idx[keep]]
    sc, dc = src.mean(0), dst.mean(0)
    U, _, Vt = np.linalg.svd((src - sc).T @ (dst - dc))
    Rk = Vt.T @ U.T
    if np.linalg.det(Rk) < 0:
        Vt[-1] *= -1
        Rk = Vt.T @ U.T
    tk = dc - Rk @ sc
    cur = cur @ Rk.T + tk
    Tk = np.eye(4); Tk[:3, :3] = Rk; Tk[:3, 3] = tk
    T_splat = Tk @ T_splat
    if np.linalg.norm(tk) < 5e-5:
        break
final = residual(apply_T(T_splat, sp))
print(f"after ICP ({it+1} iters): median NN {final*100:.1f} cm")
status = "ok" if final <= 0.05 else "SUSPECT"
if status != "ok":
    print("WARNING: splat<->mesh residual > 5 cm — inspect visually before trusting")

reg["T_splat"] = T_splat.tolist()
reg["splat_mesh_residual_cm"] = round(final * 100, 2)
reg["splat_alignment"] = f"global yaw {yaw_deg}deg + ICP ({status})"
save_registration(reg)

import shutil
shutil.copy(OUT / "apartment_mesh_debug.glb", APP_DATA / "mesh.glb")
shutil.copy(OUT / "apartment_collision.glb", APP_DATA / "collision.glb")
# the app reads T_splat from frame.json to pose the SplatMesh (its absence
# leaves the splat in raw scan frame — engulfing the camera; learned the hard way)
shutil.copy(MODEL / "registration.json", APP_DATA / "frame.json")
update_manifest({
    "mesh.glb": {"sha8": sha8(APP_DATA / "mesh.glb"), "bytes": (APP_DATA / "mesh.glb").stat().st_size},
    "collision.glb": {"sha8": sha8(APP_DATA / "collision.glb"), "bytes": (APP_DATA / "collision.glb").stat().st_size},
})
print("done; T_splat stored")

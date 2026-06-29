#!/usr/bin/env python3
"""35_merge_room_scans.py — align dense per-room scans into the apartment
frame and stage their points for 30_make_pointcloud.py to merge.

The whole-home Scaniverse scan caps at ~580k splats spread over 5 rooms;
per-room captures carry the same budget for ONE room. This registers each
extra scan (gravity-align via its own floor RANSAC -> FFT yaw/XY occupancy
correlation against the mesh -> point-to-point ICP) and writes the surviving
points to data/model/extra_points.npz.

Usage: venv\\Scripts\\python 35_merge_room_scans.py <scan1.ply> [scan2.ply ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from pipeline_util import MODEL, OUT, apply_T, read_3dgs_ply, rotation_between

rng = np.random.default_rng(11)

mesh = trimesh.load(OUT / "apartment_mesh_debug.glb", force="mesh")
surf_pts, _ = trimesh.sample.sample_surface(mesh, 200_000)
surf_tree = cKDTree(surf_pts)


def residual(p, n=8000):
    sel = rng.choice(len(p), min(n, len(p)), replace=False)
    d, _ = surf_tree.query(p[sel], k=1)
    return float(np.median(d))


def register(sp: np.ndarray) -> tuple[np.ndarray, float]:
    """Global registration of a point set against the apartment mesh."""
    sub = sp[rng.choice(len(sp), min(60_000, len(sp)), replace=False)]
    best_inl, best = -1, None
    for _ in range(800):
        tri = sub[rng.choice(len(sub), 3, replace=False)]
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n = n / nn
        d = np.abs((sub - tri[0]) @ n)
        inl = d < 0.04
        cnt = int(inl.sum())
        if cnt > best_inl:
            spread = sub[inl].std(axis=0)
            if np.sort(spread)[1] > 0.8:
                best_inl, best = cnt, (n, tri[0], inl)
    n, p0, inl = best
    if np.median((sub - p0) @ n) < 0:
        n = -n
    Rg = rotation_between(n, np.array([0.0, 0.0, 1.0]))
    sp_g = sub @ Rg.T
    floor_z = np.median(sp_g[inl][:, 2])
    sp_g[:, 2] -= floor_z

    CELL, PAD = 0.10, 256
    def occupancy(xy):
        g = np.zeros((PAD, PAD), np.float32)
        ij = np.floor(xy / CELL).astype(int) + PAD // 2
        ok = (ij >= 0).all(1) & (ij < PAD).all(1)
        g[ij[ok, 1], ij[ok, 0]] = 1.0
        return g

    mesh_xy = surf_pts[surf_pts[:, 2] < 2.0][:, :2]
    mesh_c = mesh_xy.mean(0)
    F_mesh = np.fft.rfft2(occupancy(mesh_xy - mesh_c))
    body = sp_g[(sp_g[:, 2] > 0.05) & (sp_g[:, 2] < 2.0)]
    body_c = body[:, :2].mean(0)
    # A single-room scan correlates AMBIGUOUSLY against the whole-home
    # footprint (rooms resemble each other) — collect the top-K candidate
    # poses across all yaws and let ICP from each decide by final residual.
    cands = []
    for yaw_deg in range(0, 360, 2):
        a = np.deg2rad(yaw_deg)
        Ry = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        G = occupancy((body[:, :2] - body_c) @ Ry.T)
        corr = np.fft.irfft2(F_mesh * np.conj(np.fft.rfft2(G)), s=(PAD, PAD))
        # top 2 peaks per yaw
        flat = corr.ravel()
        for k in np.argpartition(flat, -2)[-2:]:
            iy, ix = divmod(int(k), PAD)
            dy = iy if iy < PAD // 2 else iy - PAD
            dx = ix if ix < PAD // 2 else ix - PAD
            cands.append((float(flat[k]), yaw_deg, dx * CELL, dy * CELL))
    cands.sort(reverse=True)
    cands = cands[:8]

    def icp_from(yaw_deg, tx, ty):
        a = np.deg2rad(yaw_deg)
        Ryaw = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        T = np.eye(4)
        T[:3, :3] = Ryaw @ Rg
        T[:3, 3] = (Ryaw @ np.array([-body_c[0], -body_c[1], -floor_z]) +
                    np.array([tx + mesh_c[0], ty + mesh_c[1], 0.0]))
        sample = sp[rng.choice(len(sp), 8_000, replace=False)]
        cur = apply_T(T, sample)
        for _ in range(40):
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
            T = Tk @ T
            if np.linalg.norm(tk) < 5e-5:
                break
        return T, residual(apply_T(T, sp))

    best_T, best_res = None, np.inf
    for peak, yaw_deg, tx, ty in cands:
        T, res = icp_from(yaw_deg, tx, ty)
        print(f"    candidate yaw={yaw_deg} shift=({tx:.1f},{ty:.1f}): "
              f"ICP residual {res*100:.1f} cm")
        if res < best_res:
            best_T, best_res = T, res
        if best_res < 0.04:
            break
    return best_T, best_res


all_pos, all_rgb = [], []
for path in sys.argv[1:]:
    p = Path(path)
    pos, rgb, alpha = read_3dgs_ply(p)
    keep = alpha > 0.35
    pos, rgb = pos[keep], rgb[keep]
    print(f"[{p.name}] {len(pos):,} splats after alpha filter")
    T, res = register(pos)
    print(f"[{p.name}] registered: median NN {res*100:.1f} cm "
          f"{'OK' if res <= 0.06 else 'SUSPECT — inspect before trusting'}")
    if res > 0.10:
        print(f"[{p.name}] residual too high — SKIPPED")
        continue
    pos_a = apply_T(T, pos).astype(np.float32)
    # keep only points near the mesh (kills the scan's own background floaters)
    d, _ = surf_tree.query(pos_a, k=1, workers=-1)
    m = d < 0.15
    all_pos.append(pos_a[m])
    all_rgb.append(rgb[m])
    print(f"[{p.name}] kept {int(m.sum()):,} mesh-proximate points")

if all_pos:
    pos = np.vstack(all_pos)
    rgb = np.vstack(all_rgb)
    np.savez_compressed(MODEL / "extra_points.npz", pos=pos, rgb=rgb)
    print(f"staged {len(pos):,} extra points -> {MODEL / 'extra_points.npz'}")
else:
    print("nothing staged")

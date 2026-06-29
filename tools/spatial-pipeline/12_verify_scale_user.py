#!/usr/bin/env python3
"""12_verify_scale_user.py — metric validation against the user's tape measures.

User ground truth (2026-06-11):
  ceiling 8'           = 2.4384 m
  apartment length 32'7" = 9.9314 m  (living-room wall across to far kitchen wall)
  sofa width 95"       = 2.4130 m
  dining table 72.75"  = 1.8479 m
  kitchen island 47x25.5" = 1.1938 x 0.6477 m
  coffee table 36.5x24.5" surface, 14.75" tall = 0.9271 x 0.6223 x 0.3747 m
  bedroom doorway 29" wide = 0.7366 m (bedroom is outside the crop — skipped)

Measures the same spans in the model (points cloud + full mesh) and prints a
per-span error table per the plan's 10_verify_scale design: uniform error >1%
-> global scale fix; non-uniform >2.5% -> investigate.
"""
from __future__ import annotations

import numpy as np
import trimesh

from pipeline_util import APP_DATA, OUT


def read_points(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        n = int([l for l in header.decode().splitlines()
                 if l.startswith("element vertex")][0].split()[-1])
        rec = np.fromfile(f, dtype=[("xyz", "<f4", 3), ("i", "u1")], count=n)
    return rec["xyz"]


P = read_points(APP_DATA / "points.ply")
results = []


def span_from_hist(vals, cell=0.02, min_frac=0.10):
    """Length of the densest contiguous run in a 1-D histogram."""
    lo, hi = vals.min(), vals.max()
    bins = np.arange(lo, hi + cell, cell)
    h, edges = np.histogram(vals, bins=bins)
    thresh = max(3, h.max() * min_frac)
    on = h >= thresh
    # longest contiguous run
    best, cur, cur_start, best_rng = 0, 0, 0, (0, 0)
    for i, v in enumerate(on):
        if v:
            if cur == 0:
                cur_start = i
            cur += 1
            if cur > best:
                best, best_rng = cur, (cur_start, i)
        else:
            cur = 0
    return edges[best_rng[1] + 1] - edges[best_rng[0]]


def pca_long_axis(pts2):
    c = pts2 - pts2.mean(0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    proj = c @ vt[0]
    return np.percentile(proj, 99.0) - np.percentile(proj, 1.0)


def largest_cluster(pts, cell=0.06):
    """Isolate the largest connected component (the slab boxes swallow
    neighboring furniture; PCA on the raw slab measures the whole strip)."""
    from scipy import ndimage
    mn = pts.min(0)
    ij = np.floor((pts - mn) / cell).astype(int)
    shape = ij.max(0) + 1
    grid = np.zeros(shape, bool)
    grid[tuple(ij.T)] = True
    lbl, n = ndimage.label(grid, structure=np.ones((3, 3, 3)))
    if n == 0:
        return pts
    sizes = ndimage.sum(grid, lbl, range(1, n + 1))
    keep = 1 + int(np.argmax(sizes))
    return pts[lbl[tuple(ij.T)] == keep]


# --- sofa: living room, seat/back band, against the south wall ---
m = ((P[:, 0] > 8.5) & (P[:, 0] < 13.6) & (P[:, 1] > 0.6) & (P[:, 1] < 2.4)
     & (P[:, 2] > 0.25) & (P[:, 2] < 0.75))
sofa = P[m]
if len(sofa) > 500:
    sofa = largest_cluster(sofa)
    results.append(("sofa width", pca_long_axis(sofa[:, :2]), 2.4130))

# --- kitchen island: counter-height block ---
m = ((P[:, 0] > 3.4) & (P[:, 0] < 6.2) & (P[:, 1] > 2.5) & (P[:, 1] < 4.6)
     & (P[:, 2] > 0.78) & (P[:, 2] < 0.95))
isl = P[m]
if len(isl) > 300:
    isl = largest_cluster(isl)
    results.append(("island long side", pca_long_axis(isl[:, :2]), 1.1938))
    results.append(("island short side", float(
        np.percentile((isl[:, :2] - isl[:, :2].mean(0)) @ np.linalg.svd(
            (isl[:, :2] - isl[:, :2].mean(0)), full_matrices=False)[2][1], 99)
        - np.percentile((isl[:, :2] - isl[:, :2].mean(0)) @ np.linalg.svd(
            (isl[:, :2] - isl[:, :2].mean(0)), full_matrices=False)[2][1], 1)),
        0.6477))

# --- dining table: table-height slab in the dining zone ---
m = ((P[:, 0] > 3.2) & (P[:, 0] < 7.2) & (P[:, 1] > 0.5) & (P[:, 1] < 2.5)
     & (P[:, 2] > 0.66) & (P[:, 2] < 0.80))
tbl = P[m]
if len(tbl) > 300:
    tbl = largest_cluster(tbl)
    results.append(("dining table length", pca_long_axis(tbl[:, :2]), 1.8479))

# --- coffee table HEIGHT: top plateau z in living-room center ---
m = ((P[:, 0] > 9.0) & (P[:, 0] < 12.0) & (P[:, 1] > 2.4) & (P[:, 1] < 4.6)
     & (P[:, 2] > 0.20) & (P[:, 2] < 0.55))
ct = P[m]
if len(ct) > 200:
    zh, ze = np.histogram(ct[:, 2], bins=np.arange(0.20, 0.55, 0.01))
    top_z = ze[np.argmax(zh)] + 0.005
    results.append(("coffee table height", float(top_z), 0.3747))

# --- ceiling height: full mesh z plateau above 2.2 ---
mesh = trimesh.load(OUT / "apartment_mesh_debug.glb", force="mesh")
v = mesh.vertices
core = v[(v[:, 0] > 3.0) & (v[:, 0] < 13.5) & (v[:, 1] > 0.6) & (v[:, 1] < 6.3)]
zh, ze = np.histogram(core[:, 2], bins=np.arange(2.0, 3.2, 0.02))
ceil_z = ze[np.argmax(zh)] + 0.01
results.append(("ceiling height", float(ceil_z), 2.4384))

# --- apartment length: interior wall faces along x (wall band z 0.5..1.8) ---
m = (P[:, 2] > 0.5) & (P[:, 2] < 1.8) & (P[:, 1] > 1.0) & (P[:, 1] < 5.8)
walls = P[m]
xh, xe = np.histogram(walls[:, 0], bins=np.arange(2.0, 16.0, 0.04))
thresh = xh.max() * 0.25
dense = np.where(xh >= thresh)[0]
west = xe[dense[0]] + 0.02
east_all = xe[dense[-1]] + 0.02
# also the east living wall EXCLUDING the office nook (x<14)
dense_main = dense[xe[dense] < 13.9]
east_main = xe[dense_main[-1]] + 0.02
results.append(("apartment length (incl office wall)", east_all - west, 9.9314))
results.append(("apartment length (to living east wall)", east_main - west, 9.9314))

print(f"{'span':38s} {'model':>8s} {'tape':>8s} {'error':>8s}")
for name, model_v, true_v in results:
    err = (model_v - true_v) / true_v * 100
    print(f"{name:38s} {model_v:8.3f} {true_v:8.3f} {err:+7.1f}%")

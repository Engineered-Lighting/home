#!/usr/bin/env python3
"""det_adaptive.py â€” Revopoint Miraco calibration-board detector, adaptive-threshold variant.

Pipeline (no SimpleBlobDetector):
  1. cv2.adaptiveThreshold (bright-over-local-mean) -> binary candidates
  2. connectedComponentsWithStats; filter by area / bbox aspect / extent
     (area / (w*h) â€” filled dots ~0.785, hollow ring fiducials much lower)
  3. spatial clustering (union-find over kNN edges, scale-adaptive radius)
  4. per cluster: RANSAC lattice fit (fit_all_lattices from
     81_capture_intrinsics_circlegrid.py)
  5. homography grow + validate: iteratively fit lattice->image homography,
     re-index every cluster point through H^-1, dedupe cells, and finally
     accept only if median reprojection error < 1.5 px, the lattice spans
     both axes, and cell occupancy is plausible (rejects random clutter).

detect_boards(img_gray) -> list of (img_pts Nx2 float32, lattice_idx Nx2 int)
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time

import cv2
import numpy as np
from scipy.spatial import cKDTree

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPE = os.path.dirname(_HERE)


def _load_circlegrid():
    path = os.path.join(_PIPE, "81_capture_intrinsics_circlegrid.py")
    spec = importlib.util.spec_from_file_location("circlegrid81", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cg = _load_circlegrid()
fit_all_lattices = _cg.fit_all_lattices
fit_lattice = _cg.fit_lattice

# ---------------------------------------------------------------- candidates

# (blockSize, C) configs merged together: small window resolves the far
# 4-6 px dots, larger window is stabler for the near board's big dots.
THRESH_CONFIGS = [(15, -7), (35, -9)]
AREA_MIN = 4
AREA_MAX = 1500
MAX_SIDE = 48
ASPECT_MAX = 2.2
EXTENT_MIN = 0.52        # filled circle ~0.785; rings (annulus CC) far lower
MERGE_BASE = 2.5         # px; dedupe radius floor across threshold configs


def extract_candidates(img: np.ndarray) -> np.ndarray:
    """All small bright filled components -> Nx2 float32 centroids.

    The two threshold configs see the same dot with slightly different
    components; duplicates are merged with a radius scaled to component size
    (keeping the larger-area copy â€” less centroid noise). Without this the
    leftover offset duplicates can form a translated ghost lattice."""
    all_pts, all_area, all_rad = [], [], []
    for block, c in THRESH_CONFIGS:
        bw = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, block, c)
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
        if n <= 1:
            continue
        s = stats[1:]
        cen = cent[1:]
        w = s[:, cv2.CC_STAT_WIDTH].astype(np.float64)
        h = s[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
        a = s[:, cv2.CC_STAT_AREA].astype(np.float64)
        keep = (
            (a >= AREA_MIN) & (a <= AREA_MAX)
            & (np.maximum(w, h) <= MAX_SIDE)
            & (np.maximum(w, h) <= ASPECT_MAX * np.minimum(w, h))
            & (a >= EXTENT_MIN * w * h)
        )
        if keep.any():
            all_pts.append(cen[keep])
            all_area.append(a[keep])
            all_rad.append(0.5 * np.maximum(w, h)[keep])
    if not all_pts:
        return np.empty((0, 2), np.float32)
    pts = np.concatenate(all_pts).astype(np.float32)
    area = np.concatenate(all_area)
    rad = np.concatenate(all_rad)
    # dedupe: visit by descending component area, drop neighbours within a
    # size-aware radius (always < half the lattice pitch even when tilted)
    tree = cKDTree(pts)
    order = np.argsort(-area)
    drop = np.zeros(len(pts), bool)
    for i in order:
        if drop[i]:
            continue
        r = max(MERGE_BASE, rad[i])
        for j in tree.query_ball_point(pts[i], r):
            if j != i:
                drop[j] = True
    return pts[~drop]

# ---------------------------------------------------------------- clustering

CLUSTER_K = 6            # neighbours examined per point
CLUSTER_SCALE = 2.3      # link if dist <= SCALE * min(nn_i, nn_j) + 2 px
CLUSTER_MIN = 12         # smallest cluster worth a lattice fit


def cluster_points(pts: np.ndarray) -> list[np.ndarray]:
    """Union-find clustering with a per-point adaptive link radius, so the
    near board (pitch ~30 px) and far boards (pitch ~8 px) both cohere
    without merging across the room."""
    n = len(pts)
    if n < CLUSTER_MIN:
        return []
    k = min(CLUSTER_K + 1, n)
    d, nbr = cKDTree(pts).query(pts, k=k)
    d1 = d[:, 1]
    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for m in range(1, k):
            j = int(nbr[i, m])
            if d[i, m] <= CLUSTER_SCALE * min(d1[i], d1[j]) + 2.0:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    roots = np.array([find(i) for i in range(n)])
    out = []
    for r in np.unique(roots):
        members = pts[roots == r]
        if len(members) >= CLUSTER_MIN:
            out.append(members)
    return out

# --------------------------------------------------- homography grow/validate

MIN_DOTS = 12            # lock threshold (target boards carry >= 20)
LAT_TOL = 0.33           # lattice-space residual to claim a cell; ring-arc
                         # ghost fragments live at ~0.35-0.5 lattice units
                         # (ring radius / pitch), real dots < ~0.2 + distortion
                         # — 0.40 was measured to poison the large board
CLAIM_MARGIN = 0.75      # lattice cells; pool points within the board quad
                         # (+margin) are consumed by that board
MED_ERR_PX = 1.5         # required median reprojection error
MIN_SPAN = 2             # lattice must span >= MIN_SPAN steps on both axes
MIN_OCCUPANCY = 0.35     # dots / lattice-bbox cells, measured on the core


def _largest_core(idx: np.ndarray) -> np.ndarray:
    """Boolean mask of the largest connected blob of occupied lattice cells.

    A real board is a dense contiguous block (ring fiducials punch single-cell
    holes, bridged by a 1-cell dilation); clutter that happens to round onto
    distant lattice cells is sparse and disconnected, so it gets dropped here
    instead of inflating the lattice bbox."""
    ii = idx.astype(int)
    mn = ii.min(axis=0)
    g = ii - mn
    grid = np.zeros((g[:, 1].max() + 1, g[:, 0].max() + 1), np.uint8)
    grid[g[:, 1], g[:, 0]] = 1
    dil = cv2.dilate(grid, np.ones((3, 3), np.uint8))
    ncc, lab = cv2.connectedComponents(dil, 8)
    if ncc <= 2:
        return np.ones(len(idx), bool)
    cell_lab = lab[g[:, 1], g[:, 0]]
    counts = np.bincount(cell_lab, minlength=ncc)
    counts[0] = 0
    return cell_lab == np.argmax(counts)


def _fit_h(idx: np.ndarray, pts: np.ndarray):
    if len(idx) < 6:
        return None
    H, _ = cv2.findHomography(np.asarray(idx, np.float32),
                              np.asarray(pts, np.float32), 0)
    return H


def _reproj(H, idx, pts):
    proj = cv2.perspectiveTransform(
        np.asarray(idx, np.float32).reshape(-1, 1, 2), H).reshape(-1, 2)
    return np.linalg.norm(proj - pts, axis=1)


def refine_board(pool: np.ndarray, seed_pts: np.ndarray, seed_idx: np.ndarray,
                 min_dots: int = MIN_DOTS):
    """Grow a RANSAC seed into a full perspective-correct board.

    The seed comes from an affine lattice model; under tilt its indices drift
    near the board edges. Iteratively: fit lattice->image homography on the
    current set, push every pool point through H^-1, round to integer cells,
    keep small residuals, dedupe cells. Returns (img_pts, idx, med_err) or
    None if the final set fails validation (false-lattice rejection)."""
    pts = np.asarray(pool, np.float32)
    cur_pts = np.asarray(seed_pts, np.float32)
    cur_idx = np.asarray(seed_idx, np.float64)
    for _ in range(5):
        H = _fit_h(cur_idx, cur_pts)
        if H is None:
            return None
        try:
            Hinv = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            return None
        lat = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), Hinv).reshape(-1, 2)
        r = np.round(lat)
        res = np.linalg.norm(lat - r, axis=1)
        sel = np.where(res < LAT_TOL)[0]
        if len(sel) < 4:
            return None
        # dedupe lattice cells, keeping the point with smallest residual
        order = sel[np.argsort(res[sel])]
        seen = set()
        keep = []
        for i in order:
            cell = (int(r[i, 0]), int(r[i, 1]))
            if cell not in seen:
                seen.add(cell)
                keep.append(i)
        keep = np.array(keep)
        cur_pts, cur_idx = pts[keep], r[keep]

    # keep only the dense connected core (drops lattice-coincident clutter)
    core = _largest_core(cur_idx)
    cur_pts, cur_idx = cur_pts[core], cur_idx[core]
    if len(cur_pts) < min_dots:
        return None
    H = _fit_h(cur_idx, cur_pts)
    if H is None:
        return None
    # trim: first shed gross outliers (>3 px â€” misclaims, arc fragments),
    # then shave the single worst dot at a time only while the median
    # reprojection error fails the lock criterion (fisheye leaves systematic
    # residuals at board edges; the median bound keeps the modelled majority)
    while True:
        err = _reproj(H, cur_idx, cur_pts)
        gross = err >= 3.0
        med = float(np.median(err))
        if not gross.any() and med < MED_ERR_PX:
            break
        if gross.any():
            keep = ~gross
        else:
            keep = np.ones(len(err), bool)
            keep[np.argmax(err)] = False
        cur_pts, cur_idx = cur_pts[keep], cur_idx[keep]
        if len(cur_pts) < min_dots:
            return None
        H = _fit_h(cur_idx, cur_pts)
        if H is None:
            return None
    span = cur_idx.max(axis=0) - cur_idx.min(axis=0)
    if span[0] < MIN_SPAN or span[1] < MIN_SPAN:
        return None
    occ = len(cur_pts) / float((span[0] + 1) * (span[1] + 1))
    if occ < MIN_OCCUPANCY:
        return None
    idx = (cur_idx - cur_idx.min(axis=0)).astype(int)
    return cur_pts.astype(np.float32), idx, med, H

# -------------------------------------------------------------------- public


def detect_boards(img_gray: np.ndarray, min_dots: int = MIN_DOTS,
                  return_stats: bool = False):
    """Detect Miraco dot boards. Returns [(img_pts Nx2 f32, lattice_idx Nx2 int)].

    Each entry is one physical board with a consistent integer lattice
    indexing, validated by a lattice->image homography with median
    reprojection error < 1.5 px."""
    if img_gray.ndim == 3:
        img_gray = cv2.cvtColor(img_gray, cv2.COLOR_BGR2GRAY)
    cand = extract_candidates(img_gray)
    boards = []
    stats = []
    for cluster in cluster_points(cand):
        pool = cluster
        # peel up to 2 lattices per spatial cluster
        for _ in range(2):
            if len(pool) < CLUSTER_MIN:
                break
            fit = fit_lattice(pool)
            if fit is None:
                break
            idx, inl = fit
            ref = refine_board(pool, pool[inl], idx[inl], min_dots=min_dots)
            if ref is None:
                # remove the seed inliers so a second peel can't loop forever
                pool = pool[~inl]
                continue
            img_pts, lat_idx, med, H = ref
            boards.append((img_pts, lat_idx))
            stats.append({"dots": len(img_pts), "med_err_px": med,
                          "center": img_pts.mean(axis=0).tolist()})
            # remove every pool point inside the board's quadrilateral:
            # ring-fiducial arc fragments (half-pitch ghosts), offset
            # duplicates, and glare blobs all live inside the board region
            # and must not seed a second lattice.
            try:
                Hinv = np.linalg.inv(H)
            except np.linalg.LinAlgError:
                Hinv = None
            if Hinv is not None and len(pool):
                lat = cv2.perspectiveTransform(
                    pool.reshape(-1, 1, 2).astype(np.float32), Hinv
                ).reshape(-1, 2)
                blat = cv2.perspectiveTransform(
                    img_pts.reshape(-1, 1, 2), Hinv).reshape(-1, 2)
                lo = blat.min(axis=0) - CLAIM_MARGIN
                hi = blat.max(axis=0) + CLAIM_MARGIN
                inside = np.all((lat >= lo) & (lat <= hi), axis=1)
                pool = pool[~inside]
            else:
                claimed = cKDTree(img_pts).query(pool)[0] < 0.5
                pool = pool[~claimed]
    if return_stats:
        return boards, stats, cand
    return boards


# ------------------------------------------------------------------ test rig

def _main():
    frame = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\Marcelo\AppData\Local\Temp\claude\camshots\kitchen_native.jpg"
    img = cv2.imread(frame, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f"cannot read {frame}")
        sys.exit(2)
    t0 = time.perf_counter()
    boards, stats, cand = detect_boards(img, return_stats=True)
    dt = (time.perf_counter() - t0) * 1000
    print(f"frame {img.shape[1]}x{img.shape[0]}  candidates={len(cand)}  "
          f"clusters>={CLUSTER_MIN}: {len(cluster_points(cand))}  "
          f"runtime={dt:.1f} ms")
    print(f"boards locked: {len(boards)}")
    for i, s in enumerate(stats):
        print(f"  board {i}: dots={s['dots']:3d}  med_err={s['med_err_px']:.3f} px"
              f"  center=({s['center'][0]:.0f},{s['center'][1]:.0f})")
    # debug overlay
    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for p in cand:
        cv2.circle(vis, tuple(np.int32(p)), 2, (0, 0, 255), 1)
    colors = [(0, 255, 0), (255, 200, 0), (0, 200, 255), (255, 0, 255),
              (0, 128, 255), (255, 255, 0)]
    for bi, (pts, idx) in enumerate(boards):
        col = colors[bi % len(colors)]
        for p, ij in zip(pts, idx):
            cv2.circle(vis, tuple(np.int32(p)), 4, col, 1)
        c = np.int32(pts.mean(axis=0))
        cv2.putText(vis, f"B{bi} n={len(pts)}", (c[0] - 30, c[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    out = os.path.join(_HERE, "_det_adaptive_debug.png")
    cv2.imwrite(out, vis)
    print(f"overlay -> {out}")


if __name__ == "__main__":
    _main()

#!/usr/bin/env python3
"""det_multiscale.py — multi-scale matched-filter detection of Revopoint Miraco
dot calibration boards in low-res security-camera frames.

Pipeline:
  1. upscale frame 2x (INTER_CUBIC) and correlate with a bank of white-disc
     templates (TM_CCOEFF_NORMED) covering dot diameters ~3-13 px native
  2. per-scale local maxima -> global non-max suppression -> subpixel peak +
     intensity-centroid refinement = dot candidates
  3. cluster candidates by adaptive nearest-neighbour linkage (each physical
     board has its own pixel pitch)
  4. per cluster: RANSAC integer-lattice fit (fit_all_lattices, imported from
     81_capture_intrinsics_circlegrid.py)
  5. homography growth + validation: LS homography lattice_idx -> img_pts,
     re-index every cluster point through H^-1, iterate; a board only locks if
     median reprojection error < 1.5 px and the lattice is dense & 2D
     (occupancy/hull checks kill random-clutter pseudo-lattices).

Public API:
    detect_boards(img_gray) -> [(img_pts Nx2 float32, lattice_idx Nx2 int), ...]
"""
from __future__ import annotations

import importlib.util
import os

import cv2
import numpy as np

# ---------------------------------------------------------------- import the
# existing RANSAC lattice indexer (module name starts with a digit, so load by
# path)
_HERE = os.path.dirname(os.path.abspath(__file__))
_CG_PATH = os.path.normpath(
    os.path.join(_HERE, "..", "81_capture_intrinsics_circlegrid.py"))


def _load_circlegrid():
    spec = importlib.util.spec_from_file_location("_circlegrid81", _CG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cg = _load_circlegrid()
fit_all_lattices = _cg.fit_all_lattices
fit_lattice = _cg.fit_lattice

# ---------------------------------------------------------------- tunables
SCALE = 2.0                       # cubic upscale factor before matching
DIAMS_UP = (6, 8, 10, 13, 16, 20, 25)   # disc template diameters (upscaled px)
NCC_THRESH = 0.50                 # per-scale correlation floor
CONTRAST_MIN = 0.08               # centre-vs-annulus contrast (0..1 intensity)
MAX_CANDS = 4000                  # keep top-N candidates by score
LINK_K = 2.4                      # cluster linkage = K * min(nn_i, nn_j)
MIN_DOTS = 12                     # smallest acceptable board lock
TOL_PX = 1.5                      # median reprojection gate (native px)
MED_TARGET = 1.25                 # trim until the median sits safely below TOL
MIN_OCCUPANCY = 0.45              # dots per unit hull-cell-area (density gate)


# ---------------------------------------------------------------- detection
def _disc_template(diam: float) -> np.ndarray:
    pad = max(3, int(round(diam * 0.7)))
    size = int(round(diam)) + 2 * pad
    if size % 2 == 0:
        size += 1
    t = np.zeros((size, size), np.float32)
    c = size // 2
    cv2.circle(t, (c, c), max(1, int(round(diam / 2))), 1.0, -1, cv2.LINE_AA)
    t = cv2.GaussianBlur(t, (0, 0), 0.15 * diam + 0.4)
    return t


def _subpix(r: np.ndarray, x: int, y: int) -> tuple[float, float]:
    """Quadratic peak interpolation on a response map."""
    dx = dy = 0.0
    if 0 < x < r.shape[1] - 1:
        denom = 2 * r[y, x] - r[y, x - 1] - r[y, x + 1]
        if denom > 1e-9:
            dx = float(np.clip((r[y, x + 1] - r[y, x - 1]) / (2 * denom), -0.6, 0.6))
    if 0 < y < r.shape[0] - 1:
        denom = 2 * r[y, x] - r[y - 1, x] - r[y + 1, x]
        if denom > 1e-9:
            dy = float(np.clip((r[y + 1, x] - r[y - 1, x]) / (2 * denom), -0.6, 0.6))
    return x + dx, y + dy


def _refine_centroid(up: np.ndarray, x: float, y: float, diam: float):
    """Intensity-weighted centroid of the bright blob around (x, y)."""
    r = int(round(diam / 2 + 2))
    x0, y0 = int(round(x)), int(round(y))
    h, w = up.shape
    if x0 - r < 0 or y0 - r < 0 or x0 + r + 1 > w or y0 + r + 1 > h:
        return x, y
    p = up[y0 - r:y0 + r + 1, x0 - r:x0 + r + 1].astype(np.float32)
    wgt = p - p.min()
    if wgt.max() <= 1e-6:
        return x, y
    thr = 0.45 * wgt.max()
    wgt = np.where(wgt >= thr, wgt - thr, 0.0)
    s = wgt.sum()
    if s <= 0:
        return x, y
    ys, xs = np.mgrid[-r:r + 1, -r:r + 1]
    return x0 + float((wgt * xs).sum() / s), y0 + float((wgt * ys).sum() / s)


_RING_MASKS: dict[int, tuple[np.ndarray, np.ndarray, int]] = {}


def _contrast(up: np.ndarray, x: float, y: float, diam: float):
    """(centre - annulus, annulus) mean intensities (0..1). Real dots sit on a
    dark board => large positive contrast AND a dark annulus; wood-grain/JPEG
    speckle that fools normalized correlation has almost none."""
    d = int(round(diam))
    if d not in _RING_MASKS:
        ra1 = max(2, int(round(d * 1.1)))
        yy, xx = np.mgrid[-ra1:ra1 + 1, -ra1:ra1 + 1]
        rr = np.hypot(xx, yy)
        _RING_MASKS[d] = (rr <= max(1.0, d * 0.25),
                          (rr >= d * 0.7) & (rr <= ra1), ra1)
    cm, am, ra1 = _RING_MASKS[d]
    h, w = up.shape
    x0, y0 = int(round(x)), int(round(y))
    if x0 - ra1 < 0 or y0 - ra1 < 0 or x0 + ra1 + 1 > w or y0 + ra1 + 1 > h:
        return 0.0, 1.0
    patch = up[y0 - ra1:y0 + ra1 + 1, x0 - ra1:x0 + ra1 + 1]
    ann = float(patch[am].mean())
    return float(patch[cm].mean()) - ann, ann


def detect_dot_candidates(img_gray: np.ndarray,
                          scale: float = SCALE,
                          diams=DIAMS_UP,
                          thresh: float = NCC_THRESH) -> np.ndarray:
    """Multi-scale matched filtering. Returns (N, 5)
    [x, y, score, diam_native, annulus_bg] in NATIVE pixel coordinates."""
    up = cv2.resize(img_gray, None, fx=scale, fy=scale,
                    interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    cands = []
    for d in diams:
        t = _disc_template(d)
        r = cv2.matchTemplate(up, t, cv2.TM_CCOEFF_NORMED)
        m = max(3, int(d * 0.7) | 1)
        local = cv2.dilate(r, np.ones((m, m), np.uint8))
        ys, xs = np.where((r >= local - 1e-7) & (r > thresh))
        off = (t.shape[0] - 1) / 2.0
        for y, x in zip(ys, xs):
            sx, sy = _subpix(r, int(x), int(y))
            cands.append((sx + off, sy + off, float(r[y, x]), float(d)))
    if not cands:
        return np.zeros((0, 5), np.float32)
    c = np.array(cands, np.float32)
    c = c[np.argsort(-c[:, 2])][:MAX_CANDS * 4]

    # global NMS across scales (keep strongest, suppress within 0.7 * diam)
    from scipy.spatial import cKDTree
    tree = cKDTree(c[:, :2])
    alive = np.ones(len(c), bool)
    keep = []
    for i in range(len(c)):
        if not alive[i]:
            continue
        keep.append(i)
        for j in tree.query_ball_point(c[i, :2], 0.7 * max(c[i, 3], 6.0)):
            if j != i:
                alive[j] = False
    c = c[keep]

    # absolute-contrast gate (normalized correlation is contrast-blind);
    # also record the annulus (local background) intensity per candidate
    ca = np.array([_contrast(up, x, y, d) for x, y, _, d in c], np.float32)
    c = np.hstack([c, ca[:, 1:2]])          # cols: x y score diam annulus
    c = c[ca[:, 0] >= CONTRAST_MIN][:MAX_CANDS]

    # centroid refinement on the upscaled image
    for i in range(len(c)):
        c[i, 0], c[i, 1] = _refine_centroid(up, c[i, 0], c[i, 1], c[i, 3])
    c[:, :2] /= scale
    c[:, 3] /= scale
    return c


# ---------------------------------------------------------------- clustering
def _components(pts: np.ndarray, k: float = LINK_K) -> list[np.ndarray]:
    """Adaptive single-linkage components: edge iff dist <= k*min(nn_i, nn_j)."""
    n = len(pts)
    if n == 0:
        return []
    from scipy.spatial import cKDTree
    tree = cKDTree(pts)
    d, _ = tree.query(pts, k=min(2, n))
    d1 = d[:, 1] if n > 1 else np.full(n, 1.0)
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in tree.query_ball_point(pts[i], k * d1[i]):
            if j > i and np.linalg.norm(pts[i] - pts[j]) <= k * min(d1[i], d1[j]):
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return [np.array(v) for v in comps.values()]


# ------------------------------------------------- homography grow + validate
def _hull_area(idx: np.ndarray) -> float:
    if len(idx) < 3:
        return 0.0
    pts = idx.astype(np.float32)
    if len(np.unique(pts[:, 0])) < 2 or len(np.unique(pts[:, 1])) < 2:
        return 0.0
    hull = cv2.convexHull(pts)
    return float(cv2.contourArea(hull))


def _largest_cell_block(idx: np.ndarray, reach: int = 2) -> np.ndarray:
    """Mask of the largest Chebyshev-<=reach connected component of occupied
    lattice cells. A physical board is one dense block; clutter that snuck
    through the residual gate sits on scattered extrapolated cells."""
    cells = [tuple(v) for v in idx.astype(int)]
    pos = {}
    for i, c in enumerate(cells):
        pos.setdefault(c, []).append(i)
    unvisited = set(pos.keys())
    best: list[tuple[int, int]] = []
    while unvisited:
        seed = unvisited.pop()
        comp = [seed]
        stack = [seed]
        while stack:
            ci, cj = stack.pop()
            for di in range(-reach, reach + 1):
                for dj in range(-reach, reach + 1):
                    nb = (ci + di, cj + dj)
                    if nb in unvisited:
                        unvisited.remove(nb)
                        comp.append(nb)
                        stack.append(nb)
        if len(comp) > len(best):
            best = comp
    bset = set(best)
    return np.array([c in bset for c in cells], bool)


def _homography_grow(cluster_pts: np.ndarray, seed_pts: np.ndarray,
                     seed_idx: np.ndarray, min_dots: int = MIN_DOTS,
                     tol_px: float = TOL_PX, ann: np.ndarray | None = None):
    """Refine a lattice seed into a full board lock via iterated homography
    re-indexing of the whole cluster. Returns (pts, idx, med_err) or None."""
    if len(seed_pts) < 4 or _hull_area(seed_idx) < 2.0:
        return None
    # robust init: the RANSAC lattice seed can carry clutter that happens to
    # sit near lattice positions — don't let it drag the starting homography
    from scipy.spatial import cKDTree
    dnn, _ = cKDTree(seed_pts).query(seed_pts, k=2)
    pitch0 = float(np.median(dnn[:, 1]))
    H, _ = cv2.findHomography(seed_idx.astype(np.float32), seed_pts,
                              cv2.RANSAC, max(1.5, 0.22 * pitch0))
    if H is None:
        return None
    cp = cluster_pts.astype(np.float32).reshape(-1, 1, 2)
    pts_f = idx_f = None
    for _ in range(5):
        try:
            Hinv = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            return None
        lat = cv2.perspectiveTransform(cp, Hinv.astype(np.float64)).reshape(-1, 2)
        lat_r = np.round(lat)
        proj = cv2.perspectiveTransform(
            lat_r.reshape(-1, 1, 2).astype(np.float64),
            H.astype(np.float64)).reshape(-1, 2)
        err = np.linalg.norm(proj - cluster_pts, axis=1)
        # local pitch (px per lattice step) to scale the assignment gate
        ctr = lat_r[np.argmin(err)]
        probe = np.array([ctr, ctr + [1, 0], ctr + [0, 1]], np.float64)
        pp = cv2.perspectiveTransform(probe.reshape(-1, 1, 2), H).reshape(-1, 2)
        pitch = 0.5 * (np.linalg.norm(pp[1] - pp[0]) + np.linalg.norm(pp[2] - pp[0]))
        gate = float(np.clip(0.28 * pitch, 1.2, 3.0))
        ok = err < gate
        if int(ok.sum()) < 4:
            return None
        # unique cell assignment: keep lowest-error point per lattice cell
        best: dict[tuple[int, int], int] = {}
        for i in np.where(ok)[0]:
            cell = (int(lat_r[i, 0]), int(lat_r[i, 1]))
            if cell not in best or err[i] < err[best[cell]]:
                best[cell] = i
        sel = np.array(sorted(best.values()))
        pts_f = cluster_pts[sel].astype(np.float32)
        idx_f = lat_r[sel].astype(np.float64)
        if _hull_area(idx_f) < 2.0:
            return None
        H2, _ = cv2.findHomography(idx_f.astype(np.float32), pts_f,
                                   cv2.RANSAC, max(1.5, 0.18 * pitch))
        if H2 is None:
            return None
        H = H2
    # background coherence: every dot of one physical board sits on the same
    # dark board material — border-frame/specular junk has a bright annulus
    if ann is not None:
        ann_f = ann[sel]
        med_a = float(np.median(ann_f))
        mad_a = float(np.median(np.abs(ann_f - med_a)))
        keep_a = np.abs(ann_f - med_a) <= max(0.15, 6.0 * mad_a)
        if int(keep_a.sum()) < 4:
            return None
        pts_f, idx_f = pts_f[keep_a], idx_f[keep_a]
        if _hull_area(idx_f) < 2.0:
            return None
    # one physical board = one dense block of lattice cells: drop scattered
    # extrapolated cells (off-board clutter with lucky residuals)
    block = _largest_cell_block(idx_f)
    if int(block.sum()) < 4:
        return None
    return _polish(pts_f[block], idx_f[block], min_dots, tol_px)


def _prune_sparse_margins(pts_f: np.ndarray, idx_f: np.ndarray,
                          min_frac: float = 0.4):
    """Drop boundary lattice rows/cols that are mostly empty — those are
    almost always off-board junk hanging beyond the physical edge."""
    while len(idx_f) >= 4:
        (i0, j0), (i1, j1) = idx_f.min(axis=0), idx_f.max(axis=0)
        ni, nj = i1 - i0 + 1, j1 - j0 + 1
        if ni < 3 or nj < 3:
            break
        margins = [
            ((idx_f[:, 0] == i0), (idx_f[:, 0] == i0).sum() / nj),
            ((idx_f[:, 0] == i1), (idx_f[:, 0] == i1).sum() / nj),
            ((idx_f[:, 1] == j0), (idx_f[:, 1] == j0).sum() / ni),
            ((idx_f[:, 1] == j1), (idx_f[:, 1] == j1).sum() / ni),
        ]
        mask, frac = min(margins, key=lambda m: m[1])
        if frac >= min_frac:
            break
        pts_f, idx_f = pts_f[~mask], idx_f[~mask]
    return pts_f, idx_f


def _polish(pts_f: np.ndarray, idx_f: np.ndarray, min_dots: int = MIN_DOTS,
            tol_px: float = TOL_PX, prune: bool = False):
    """(optional sparse-margin pruning) + LS homography refit + outlier
    shedding + gates. Per-point gate err < max(4*median, 1.0) px kills
    border/specular junk that landed near a lattice cell; the trim keeps the
    median safely under tol. Pruning only runs post-completion, so genuinely
    sparse far rows get a chance to fill in first."""
    pts_f = pts_f.astype(np.float32)
    idx_f = idx_f.astype(np.float64)
    if prune:
        pts_f, idx_f = _prune_sparse_margins(pts_f, idx_f)
    if len(pts_f) < 4 or _hull_area(idx_f) < 2.0:
        return None
    H, _ = cv2.findHomography(idx_f.astype(np.float32), pts_f, 0)
    if H is None:
        return None
    med = np.inf
    for _ in range(60):
        proj = cv2.perspectiveTransform(
            idx_f.reshape(-1, 1, 2), H.astype(np.float64)).reshape(-1, 2)
        err = np.linalg.norm(proj - pts_f, axis=1)
        med = float(np.median(err))
        drop = err >= max(4.0 * med, 1.0)
        if med >= MED_TARGET and len(pts_f) > max(min_dots, 8):
            worst = np.argsort(err)[-max(1, len(pts_f) // 20):]
            d2 = np.zeros(len(pts_f), bool)
            d2[worst] = True
            drop |= d2
        if not drop.any():
            break
        keep = ~drop
        if int(keep.sum()) < 4:
            return None
        pts_f, idx_f = pts_f[keep], idx_f[keep]
        if _hull_area(idx_f) < 2.0:
            return None
        H2, _ = cv2.findHomography(idx_f.astype(np.float32), pts_f, 0)
        if H2 is None:
            return None
        H = H2
    n = len(pts_f)
    hull = _hull_area(idx_f)
    span = idx_f.max(axis=0) - idx_f.min(axis=0)
    if (n < min_dots or med >= tol_px or hull < 4.0
            or span[0] < 2 or span[1] < 2
            or n < MIN_OCCUPANCY * hull):
        return None
    idx_out = idx_f.astype(int)
    idx_out -= idx_out.min(axis=0)
    return pts_f, idx_out, med


def _clip_to_border(img_f: np.ndarray, pts: np.ndarray, idx: np.ndarray):
    """Clip the lattice to the board's physical WHITE BORDER frame.

    Rectify the board into lattice space through H (one cell = G px). In the
    rectified image a dot row is a punctuated bright line (dark gaps between
    dots) but the border frame is a CONTINUOUS bright ridge, so the
    25th-percentile profile along each axis stays at background level inside
    the dot field and jumps at the border. Cells beyond the first ridge on
    each side are off-board (glints/border specks that survived everything
    else). Returns (pts, idx, changed)."""
    H, _ = cv2.findHomography(idx.astype(np.float32), pts.astype(np.float32), 0)
    if H is None:
        return pts, idx, False
    G, MARG = 16, 3.0          # px per cell, cells of margin to scan
    (i0, j0), (i1, j1) = idx.min(axis=0), idx.max(axis=0)
    W = int(round((i1 - i0 + 2 * MARG) * G))
    Hh = int(round((j1 - j0 + 2 * MARG) * G))
    if W > 4000 or Hh > 4000:
        return pts, idx, False
    # rect (u,v) -> lattice (i,j): i = u/G + i0 - MARG
    A = np.array([[1.0 / G, 0.0, i0 - MARG],
                  [0.0, 1.0 / G, j0 - MARG],
                  [0.0, 0.0, 1.0]], np.float64)
    M = np.linalg.inv(H.astype(np.float64) @ A)     # image -> rect
    rect = cv2.warpPerspective(img_f, M, (W, Hh))
    pad = int(MARG * G)
    interior = rect[pad:Hh - pad, pad:W - pad]
    if interior.size == 0:
        return pts, idx, False
    bg = float(np.median(interior))
    thr = bg + 0.15

    keep = np.ones(len(idx), bool)
    for axis in (0, 1):
        # profile along this axis, restricted to the dot-field band of the
        # other axis (so corners/whatever lies beyond doesn't pollute P25)
        if axis == 0:
            prof = np.percentile(rect[pad:Hh - pad, :], 25, axis=0)
            lo = i0
        else:
            prof = np.percentile(rect[:, pad:W - pad], 25, axis=1)
            lo = j0
        centre = int(round((np.median(idx[:, axis]) - lo + MARG) * G))
        for step in (1, -1):
            u = centre
            ridge = None
            while 0 <= u < len(prof):
                if prof[u] > thr:
                    ridge = u
                    break
                u += step
            if ridge is None:
                continue
            c_r = ridge / G + lo - MARG       # ridge position in cell units
            if step > 0:
                keep &= idx[:, axis] <= c_r - 0.35
            else:
                keep &= idx[:, axis] >= c_r + 0.35
    if keep.all() or int(keep.sum()) < 4:
        return pts, idx, False
    return pts[keep], idx[keep], True


def _complete_board(up: np.ndarray, scale: float, pts: np.ndarray,
                    idx: np.ndarray):
    """Recover dots the candidate stage missed: predict every empty lattice
    cell inside the locked range through H, centroid-refine there, and accept
    only if a real bright blob (contrast) sits where the lattice says it
    should. Ring-fiducial centres are lattice-centred, so they count too."""
    H, _ = cv2.findHomography(idx.astype(np.float32), pts.astype(np.float32), 0)
    if H is None:
        return pts, idx
    ctr = idx.mean(axis=0).astype(np.float64)
    probe = np.array([ctr, ctr + [1, 0], ctr + [0, 1]], np.float64)
    pp = cv2.perspectiveTransform(probe.reshape(-1, 1, 2), H).reshape(-1, 2)
    pitch = 0.5 * (np.linalg.norm(pp[1] - pp[0]) + np.linalg.norm(pp[2] - pp[0]))
    occ = {tuple(v) for v in np.asarray(idx, int)}
    diam_up = float(np.clip(0.45 * pitch, 2.5, 14.0)) * scale
    new_pts, new_idx = [], []
    (i0, j0), (i1, j1) = idx.min(axis=0), idx.max(axis=0)
    for i in range(int(i0), int(i1) + 1):
        for j in range(int(j0), int(j1) + 1):
            if (i, j) in occ:
                continue
            pr = cv2.perspectiveTransform(
                np.array([[[i, j]]], np.float64), H).reshape(2)
            rx, ry = _refine_centroid(up, pr[0] * scale, pr[1] * scale, diam_up)
            con, _ = _contrast(up, rx, ry, diam_up)
            if con < 0.6 * CONTRAST_MIN:
                continue
            nx, ny = rx / scale, ry / scale
            if np.hypot(nx - pr[0], ny - pr[1]) > 0.33 * pitch:
                continue
            new_pts.append((nx, ny))
            new_idx.append((i, j))
    if not new_pts:
        return pts, idx
    return (np.vstack([pts, np.array(new_pts, np.float32)]),
            np.vstack([np.asarray(idx, int), np.array(new_idx, int)]))


# ---------------------------------------------------------------- public API
def detect_boards(img_gray: np.ndarray, min_dots: int = MIN_DOTS,
                  verbose: bool = False):
    """Detect Miraco dot boards. Returns a list of
    (img_pts Nx2 float32, lattice_idx Nx2 int) — one entry per locked board."""
    cands = detect_dot_candidates(img_gray)
    if verbose:
        print(f"  candidates: {len(cands)}")
    if len(cands) < min_dots:
        return []
    pts = cands[:, :2].astype(np.float32)
    ann = cands[:, 4].astype(np.float32)
    up = cv2.resize(img_gray, None, fx=SCALE, fy=SCALE,
                    interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    img_f = img_gray.astype(np.float32) / 255.0

    boards = []
    for comp in _components(pts):
        if len(comp) < min_dots:
            continue
        cpts = pts[comp]
        fits = fit_all_lattices(cpts, max_boards=2, min_dots=12)
        for seed_pts, seed_idx in fits:
            got = _homography_grow(cpts, seed_pts, seed_idx,
                                   min_dots=min_dots, ann=ann[comp])
            if got is None:
                continue
            # clip to the physical white border BEFORE completion, so the
            # fill-in stays on the actual board
            p0, i0, _ = got
            pc, ic, changed = _clip_to_border(img_f, p0, i0)
            if changed:
                pol = _polish(pc, ic, min_dots=min_dots, prune=True)
                if pol is not None:
                    got = pol
                    p0, i0, _ = got
            # completion pass: pull in dots/rings the candidate stage missed,
            # then re-polish with sparse-margin pruning + final border clip
            p2, i2 = _complete_board(up, SCALE, p0, i0)
            pol = _polish(p2, i2, min_dots=min_dots, prune=True)
            if pol is not None:
                p3, i3, _ = pol
                pc, ic, changed = _clip_to_border(img_f, p3, i3)
                if changed:
                    pol2 = _polish(pc, ic, min_dots=min_dots, prune=True)
                    if pol2 is not None:
                        pol = pol2
                got = pol
            boards.append(got)

    # dedup: two locks sharing image area -> keep the bigger/cleaner one
    boards.sort(key=lambda b: (-len(b[0]), b[2]))
    final = []
    for p, idx, med in boards:
        c = p.mean(axis=0)
        rad = max(np.linalg.norm(p - c, axis=1).max(), 1.0)
        dup = False
        for p2, _ in final:
            c2 = p2.mean(axis=0)
            rad2 = max(np.linalg.norm(p2 - c2, axis=1).max(), 1.0)
            if np.linalg.norm(c - c2) < 0.8 * (rad + rad2):
                dup = True
                break
        if not dup:
            final.append((p, idx))
            if verbose:
                print(f"  LOCK n={len(p)} med_err={med:.3f}px "
                      f"center=({c[0]:.0f},{c[1]:.0f}) "
                      f"span={idx.max(0) - idx.min(0)}")
    return final


# ---------------------------------------------------------------- test runner
if __name__ == "__main__":
    import sys
    import time

    frame = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Users\Marcelo\AppData\Local\Temp\claude\camshots\kitchen_native.jpg")
    img = cv2.imread(frame, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise SystemExit(f"cannot read {frame}")
    t0 = time.time()
    boards = detect_boards(img, verbose=True)
    dt = (time.time() - t0) * 1000
    print(f"boards locked: {len(boards)}")
    print(f"dots per board: {[len(p) for p, _ in boards]}")
    print(f"runtime: {dt:.0f} ms")

    # clutter sanity: a board-free crop must score 0
    crop = img[430:720, 0:620]
    z = detect_boards(crop)
    print(f"clutter crop boards (expect 0): {len(z)}")

    # debug overlay
    dbg = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    cands = detect_dot_candidates(img)
    for x, y in cands[:, :2]:
        cv2.circle(dbg, (int(round(x)), int(round(y))), 2, (0, 0, 255), 1)
    colors = [(0, 255, 0), (0, 255, 255), (255, 128, 0), (255, 0, 255),
              (128, 255, 128)]
    for bi, (p, idx) in enumerate(boards):
        col = colors[bi % len(colors)]
        for (x, y), (i, j) in zip(p, idx):
            cv2.circle(dbg, (int(round(x)), int(round(y))), 4, col, 1)
        c = p.mean(axis=0).astype(int)
        cv2.putText(dbg, f"B{bi} n={len(p)}", (c[0] - 30, c[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    out = os.path.join(os.environ.get("TEMP", "."), "claude", "camshots",
                       "det_multiscale_debug.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, dbg)
    print(f"debug overlay: {out}")

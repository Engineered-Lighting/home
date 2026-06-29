#!/usr/bin/env python3
"""det_quadrect.py — board-first rectification detector for Revopoint Miraco
calibration dot boards in low-res security-camera frames.

Strategy (board-first, opposite of the dots-first 81_capture_intrinsics_circlegrid):
  1. Each board is a dark rectangle wrapped in a bright WHITE border — a large
     (>100 px) feature that survives 1280x720 even when individual dots are 4 px.
     Sweep several global dark thresholds; isolated dark convex blobs whose
     4-corner approximation looks like a quad are board candidates (the white
     border guarantees the interior stays an isolated blob at any threshold
     below the border brightness).
  2. Perspective-rectify each candidate interior to a canonical upscaled
     rectangle (small far boards get ~5x magnification -> 4 px dots become
     ~20 px blobs with clean centroids).
  3. Detect filled dots inside the rectified crop (adaptive threshold +
     connected components; hollow ring fiducials rejected by fill ratio).
  4. RANSAC an integer lattice on the rectified dot centers (adapted from
     fit_lattice in 81_capture_intrinsics_circlegrid.py, generalized to two
     independent basis vectors so imperfect aspect recovery still locks).
  5. Map dot centers back through the inverse warp to image coords and
     validate with a lattice->image homography: a board only counts if the
     median reprojection error < 1.5 px and the index set is truly 2-D.

Exposes:
    detect_boards(img_gray) -> list of (img_pts Nx2 float32, lattice_idx Nx2 int)
"""
from __future__ import annotations

import cv2
import numpy as np
from scipy.spatial import cKDTree

# ----------------------------------------------------------------------------- tunables
DARK_SWEEP = (45, 60, 75, 90, 105, 120, 135)   # global dark thresholds for quad finding
MIN_QUAD_AREA = 1200.0                          # px^2, interior blob
MAX_QUAD_AREA = 150000.0
MIN_QUAD_SIDE = 18.0                            # px, shortest quad side
MAX_ASPECT = 3.5
MIN_CONVEXITY = 0.82                            # blob area / hull area
RECT_TARGET = 300.0                             # rectified min-dimension target, px
RECT_MAX_SCALE = 6.0
MIN_DOTS = 12                                   # dots needed to even attempt a lock
MAX_MEDIAN_ERR = 1.5                            # px, homography reprojection gate
MIN_SPAN = 3                                    # distinct lattice values needed per axis


# ----------------------------------------------------------------------------- quads
def _order_corners(quad: np.ndarray) -> np.ndarray:
    """Order 4 points tl, tr, br, bl (consistent winding for getPerspectiveTransform)."""
    c = quad.mean(axis=0)
    ang = np.arctan2(quad[:, 1] - c[1], quad[:, 0] - c[0])
    quad = quad[np.argsort(ang)]                 # CCW in image coords (y down -> CW visually)
    s = quad.sum(axis=1)
    tl = int(np.argmin(s))
    return np.roll(quad, -tl, axis=0).astype(np.float32)


def _find_quads(img: np.ndarray) -> list[np.ndarray]:
    """Multi-threshold dark-blob sweep -> deduped list of candidate 4x2 quads."""
    quads, seen = [], set()
    for t in DARK_SWEEP:
        _, bw = cv2.threshold(img, t, 255, cv2.THRESH_BINARY_INV)
        cnts, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if not (MIN_QUAD_AREA <= area <= MAX_QUAD_AREA):
                continue
            hull = cv2.convexHull(cnt)
            ha = cv2.contourArea(hull)
            if ha <= 0 or area / ha < MIN_CONVEXITY:
                continue
            peri = cv2.arcLength(hull, True)
            quad = None
            for eps in (0.02, 0.03, 0.04, 0.06):
                ap = cv2.approxPolyDP(hull, eps * peri, True)
                if len(ap) == 4:
                    quad = ap.reshape(4, 2).astype(np.float32)
                    break
            if quad is None:
                continue
            sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
            if sides.min() < MIN_QUAD_SIDE or sides.max() / sides.min() > MAX_ASPECT:
                continue
            cx, cy = quad.mean(axis=0)
            key = (int(cx // 12), int(cy // 12), int(np.log1p(area) * 4))
            if key in seen:
                continue
            seen.add(key)
            quads.append(_order_corners(quad))
    return quads


# ----------------------------------------------------------------------------- rectify
def _rectify(img: np.ndarray, quad: np.ndarray):
    """Warp quad interior to canonical rectangle with margin. Returns
    (crop, Hmat image->rect, margin, W, H) or None."""
    tl, tr, br, bl = quad
    W0 = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    H0 = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    if min(W0, H0) < MIN_QUAD_SIDE:
        return None
    s = float(np.clip(RECT_TARGET / min(W0, H0), 1.0, RECT_MAX_SCALE))
    W, H = int(round(W0 * s)), int(round(H0 * s))
    m = max(10, int(round(0.08 * min(W, H))))
    dst = np.array([[m, m], [m + W, m], [m + W, m + H], [m, m + H]], np.float32)
    Hmat = cv2.getPerspectiveTransform(quad, dst)
    crop = cv2.warpPerspective(img, Hmat, (W + 2 * m, H + 2 * m),
                               flags=cv2.INTER_CUBIC)
    return crop, Hmat, m, W, H


# ----------------------------------------------------------------------------- dots
def _detect_dots(crop: np.ndarray, m: int, W: int, H: int) -> np.ndarray:
    """Bright filled dots inside the rectified interior. Rings rejected by fill
    ratio (annulus components are sparse in their bbox); JPEG mosquito-noise
    satellites rejected by brightness-above-local-background (they form a
    regular half-pitch pseudo-lattice if kept!). Returns Nx2 float32."""
    interior = crop[m:m + H, m:m + W]
    pitch_est = min(W, H) / 8.0                  # boards are ~7-11 cells across
    k = int(np.clip(pitch_est * 1.5, 31, 99)) | 1
    bg = cv2.medianBlur(interior, k)
    diff = interior.astype(np.int16) - bg.astype(np.int16)
    block = max(15, (int(min(W, H) / 6) | 1))
    th = cv2.adaptiveThreshold(interior, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, block, -12)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(th, 8)
    cand = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 9 or w > 0.85 * pitch_est or h > 0.85 * pitch_est:
            continue
        if w < 2 or h < 2 or max(w, h) / min(w, h) > 2.0:
            continue
        fill = area / float(w * h)
        if fill < 0.55:                          # rings/annuli & streaks fail
            continue
        # reject components hugging the interior edge (border bleed)
        if x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1:
            continue
        msk = lab[y:y + h, x:x + w] == i
        d = diff[y:y + h, x:x + w]
        score = float(d[msk].mean())
        # subpixel: intensity-above-background weighted centroid
        wgt = np.where(msk, np.clip(d, 0, None), 0).astype(np.float64)
        tot = wgt.sum()
        if tot <= 0:
            continue
        ys, xs = np.mgrid[y:y + h, x:x + w]
        cxp = float((xs * wgt).sum() / tot)
        cyp = float((ys * wgt).sum() / tot)
        cand.append((score, cxp, cyp))
    if not cand:
        return np.empty((0, 2), np.float32)
    scores = np.array([c[0] for c in cand])
    cutoff = max(12.0, 0.5 * float(np.percentile(scores, 95)))
    pts = [(cx, cy) for s, cx, cy in cand if s >= cutoff]
    if not pts:
        return np.empty((0, 2), np.float32)
    return np.asarray(pts, np.float32) + np.float32([m, m])


# ----------------------------------------------------------------------------- lattice
def _fit_lattice(pts: np.ndarray, iters: int = 600, tol_frac: float = 0.20):
    """RANSAC integer-lattice fit, adapted from fit_lattice in
    81_capture_intrinsics_circlegrid.py but with BOTH basis vectors sampled
    from real neighbour offsets (tolerates unequal x/y pitch after imperfect
    aspect recovery). Returns (idx Nx2 int, inlier mask) or None."""
    n = len(pts)
    if n < MIN_DOTS:
        return None
    rng = np.random.default_rng(0)
    k = min(6, n)
    d, nbr = cKDTree(pts).query(pts, k=k)
    typ = np.median(d[:, 1])
    best = None
    for _ in range(iters):
        i = int(rng.integers(n))
        ja, jb = rng.integers(1, k, size=2)
        a = pts[nbr[i, ja]] - pts[i]
        b = pts[nbr[i, jb]] - pts[i]
        na, nb_ = np.linalg.norm(a), np.linalg.norm(b)
        if not (0.5 * typ < na < 1.9 * typ and 0.5 * typ < nb_ < 1.9 * typ):
            continue
        cross = abs(a[0] * b[1] - a[1] * b[0])
        if cross < 0.5 * na * nb_:               # need a genuinely 2-D basis
            continue
        M = np.stack([a, b], axis=1)
        rel = (pts - pts[i]) @ np.linalg.inv(M).T
        ridx = np.round(rel)
        err = np.linalg.norm(rel - ridx, axis=1)
        inl = err < tol_frac
        score = int(inl.sum())
        if best is None or score > best[0]:
            best = (score, ridx.astype(int), inl)
    if best is None or best[0] < MIN_DOTS:
        return None
    _, idx, inl = best
    # refine: homography idx->pts on inliers, re-round everyone, re-score
    for _ in range(2):
        if inl.sum() < 4:
            return None
        Hm, _ = cv2.findHomography(idx[inl].astype(np.float32), pts[inl], 0)
        if Hm is None:
            return None
        back = cv2.perspectiveTransform(
            pts.reshape(-1, 1, 2).astype(np.float32),
            np.linalg.inv(Hm)).reshape(-1, 2)
        idx = np.round(back).astype(int)
        err = np.linalg.norm(back - idx, axis=1)
        inl = err < tol_frac
    if inl.sum() < MIN_DOTS:
        return None
    idx = idx - idx[inl].min(axis=0)
    return idx, inl


# ----------------------------------------------------------------------------- canonicalize
def _reduce_basis(idx: np.ndarray, pts: np.ndarray):
    """The RANSAC may lock a sheared (unimodular) basis — e.g. a=(1,0), b=(1,1)
    in true-lattice units — which indexes every dot but corrupts idx*pitch
    object coords. Fit the affine basis, Lagrange-Gauss reduce it to the
    shortest (true pitch) basis, and remap indices with the tracked unimodular
    transform. Returns canonical idx (min at 0)."""
    A = np.hstack([idx.astype(np.float64), np.ones((len(idx), 1))])
    sol, *_ = np.linalg.lstsq(A, pts.astype(np.float64), rcond=None)
    B = sol[:2]                                   # rows = basis vectors
    U = np.eye(2, dtype=np.int64)
    b = [B[0].copy(), B[1].copy()]
    for _ in range(32):
        if b[0] @ b[0] > b[1] @ b[1]:
            b = [b[1], b[0]]
            U = U[::-1].copy()
        mu = int(round((b[0] @ b[1]) / (b[0] @ b[0])))
        if mu == 0:
            break
        b[1] = b[1] - mu * b[0]
        U[1] = U[1] - mu * U[0]
    det = int(round(np.linalg.det(U)))
    if det not in (-1, 1):                        # should never happen
        return idx - idx.min(axis=0)
    Uinv = np.round(np.linalg.inv(U)).astype(np.int64)
    idx2 = idx.astype(np.int64) @ Uinv            # pts = idx2 @ (U @ B)
    # sign-normalize each axis (cosmetic, keeps spans positive & tight)
    Bred = U @ B
    for ax in range(2):
        if Bred[ax, np.argmax(np.abs(Bred[ax]))] < 0:
            idx2[:, ax] = -idx2[:, ax]
    return (idx2 - idx2.min(axis=0)).astype(int)


# ----------------------------------------------------------------------------- validate
def _validate(idx: np.ndarray, img_pts: np.ndarray):
    """Homography lattice->image gate. Returns (img_pts, idx, med_err) of the
    surviving inlier set, or None."""
    if len(idx) < MIN_DOTS:
        return None
    src = idx.astype(np.float32)
    Hm, mask = cv2.findHomography(src, img_pts, cv2.RANSAC, 3.0)
    if Hm is None:
        return None
    keep = mask.ravel().astype(bool)
    if keep.sum() < MIN_DOTS:
        return None
    src, dst, idx2 = src[keep], img_pts[keep], idx[keep]
    Hm, _ = cv2.findHomography(src, dst, 0)      # LSQ refit on inliers
    if Hm is None:
        return None
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), Hm).reshape(-1, 2)
    err = np.linalg.norm(proj - dst, axis=1)
    med = float(np.median(err))
    if med >= MAX_MEDIAN_ERR:
        return None
    # require genuine 2-D extent (a collinear point set fits any homography)
    if len(np.unique(idx2[:, 0])) < MIN_SPAN or len(np.unique(idx2[:, 1])) < MIN_SPAN:
        return None
    return dst.astype(np.float32), idx2.astype(int), med


# ----------------------------------------------------------------------------- public
def detect_boards(img_gray: np.ndarray, debug: dict | None = None):
    """Detect Miraco dot boards. Returns list of
    (img_pts Nx2 float32, lattice_idx Nx2 int), one entry per locked board."""
    if img_gray.ndim == 3:
        img_gray = cv2.cvtColor(img_gray, cv2.COLOR_BGR2GRAY)
    boards = []
    quads = _find_quads(img_gray)
    if debug is not None:
        debug["quads"] = quads
        debug["attempts"] = []
    for quad in quads:
        r = _rectify(img_gray, quad)
        if r is None:
            continue
        crop, Hmat, m, W, H = r
        dots = _detect_dots(crop, m, W, H)
        if debug is not None:
            debug["attempts"].append((quad, crop, dots))
        if len(dots) < MIN_DOTS:
            continue
        fit = _fit_lattice(dots)
        if fit is None:
            continue
        idx, inl = fit
        idx_c = _reduce_basis(idx[inl], dots[inl])
        rect_pts = dots[inl].reshape(-1, 1, 2)
        img_pts = cv2.perspectiveTransform(rect_pts, np.linalg.inv(Hmat)).reshape(-1, 2)
        v = _validate(idx_c, img_pts.astype(np.float32))
        if v is None:
            continue
        img_pts, idx2, med = v
        boards.append((img_pts, idx2, med))
    # dedupe overlapping locks of the same physical board (keep most dots)
    boards.sort(key=lambda b: -len(b[0]))
    final = []
    for pts, idx2, med in boards:
        dup = False
        for fpts, _, _ in final:
            tree = cKDTree(fpts)
            d, _ = tree.query(pts, k=1)
            if (d < 2.5).mean() > 0.3:
                dup = True
                break
        if not dup:
            final.append((pts, idx2, med))
    if debug is not None:
        debug["med_errs"] = [m for _, _, m in final]
    return [(p, i) for p, i, _ in final]


# ----------------------------------------------------------------------------- harness
if __name__ == "__main__":
    import sys, time
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frame = args[0] if args else \
        r"C:\Users\Marcelo\AppData\Local\Temp\claude\camshots\kitchen_native.jpg"
    img = cv2.imread(frame, cv2.IMREAD_GRAYSCALE)
    dbg = {}
    t0 = time.perf_counter()
    out = detect_boards(img, debug=dbg)
    meds = dbg["med_errs"]
    dt = (time.perf_counter() - t0) * 1000
    print(f"frame {frame}  {img.shape[1]}x{img.shape[0]}")
    print(f"quad candidates: {len(dbg['quads'])}, attempts with crops: {len(dbg['attempts'])}")
    print(f"BOARDS LOCKED: {len(out)}  ({dt:.0f} ms)")
    for k, ((pts, idx), med) in enumerate(zip(out, meds)):
        span = (idx.max(0) - idx.min(0)) + 1
        print(f"  board {k}: dots={len(pts)}  med_reproj={med:.3f}px  "
              f"lattice span {span[0]}x{span[1]}  center=({pts[:,0].mean():.0f},{pts[:,1].mean():.0f})")
    # debug overlay
    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for quad in dbg["quads"]:
        cv2.polylines(vis, [quad.astype(int)], True, (0, 128, 255), 1)
    cols = [(0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 0, 255), (0, 0, 255)]
    for k, (pts, idx) in enumerate(out):
        c = cols[k % len(cols)]
        for (x, y), (i, j) in zip(pts, idx):
            cv2.circle(vis, (int(round(x)), int(round(y))), 3, c, 1)
    p = r"C:\Users\Marcelo\AppData\Local\Temp\claude\det_dbg\overlay.png"
    cv2.imwrite(p, vis)
    print("overlay ->", p)
    if "--dump" in sys.argv:
        for n, (quad, crop, dots_r) in enumerate(dbg["attempts"]):
            cv = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            for x, y in dots_r:
                cv2.circle(cv, (int(round(x)), int(round(y))), 4, (0, 255, 0), 1)
            cx, cy = quad.mean(axis=0)
            fp = rf"C:\Users\Marcelo\AppData\Local\Temp\claude\det_dbg\att{n:02d}_c{int(cx)}x{int(cy)}_d{len(dots_r)}.png"
            cv2.imwrite(fp, cv)
        print(f"dumped {len(dbg['attempts'])} attempt crops")

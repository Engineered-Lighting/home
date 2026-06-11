#!/usr/bin/env python3
"""81_capture_intrinsics_circlegrid.py — intrinsics from the user's Revopoint
Miraco dot boards (no printing).

The boards carry filled white dots on a regular lattice, with coded ring
fiducials OCCUPYING some lattice cells — so OpenCV's findCirclesGrid (which
needs a complete grid) cannot lock. Instead:

  1. blob-detect the FILLED dots (rings rejected by convexity/circularity)
  2. RANSAC a 2D lattice: pick dot pairs to hypothesize the two basis vectors,
     fit a homography mapping integer lattice coords -> image, keep the
     hypothesis with the most inliers
  3. integer-index every inlier dot -> (i, j) * pitch = object point (z=0)
  4. frames with >= 20 indexed dots count; calibrateCamera on the collection

This also lifts findCirclesGrid's whole-board-visible requirement: partial
views contribute whatever dots they show.

Usage:
  venv\\Scripts\\python 81_capture_intrinsics_circlegrid.py --cam kitchen
      --pitch-mm <measured center-to-center spacing> [--target 40]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

import cv2
import numpy as np

FRIGATE = "http://192.168.0.125:5000"
TRACKER = "http://192.168.0.100:8098"


def blob_detector():
    p = cv2.SimpleBlobDetector_Params()
    p.filterByArea = True; p.minArea = 25; p.maxArea = 8000
    p.filterByCircularity = True; p.minCircularity = 0.78
    p.filterByConvexity = True; p.minConvexity = 0.88   # rings are arcs -> fail
    p.filterByInertia = True; p.minInertiaRatio = 0.55
    p.filterByColor = True; p.blobColor = 255
    return cv2.SimpleBlobDetector_create(p)


def fit_lattice(pts, iters=400, tol_frac=0.18):
    """RANSAC integer-lattice fit. Returns (idx Nx2 int, inlier mask) or None."""
    n = len(pts)
    if n < 12:
        return None
    rng = np.random.default_rng(0)
    best = None
    # estimate typical nearest-neighbour spacing for hypothesis sampling
    from scipy.spatial import cKDTree
    d, nbr = cKDTree(pts).query(pts, k=4)
    typ = np.median(d[:, 1])
    for _ in range(iters):
        i = rng.integers(n)
        j = nbr[i, rng.integers(1, 4)]
        a = pts[j] - pts[i]                      # basis 1 (one lattice step)
        if not (0.5 * typ < np.linalg.norm(a) < 1.8 * typ):
            continue
        b = np.array([-a[1], a[0]])              # perpendicular hypothesis
        M = np.stack([a, b], axis=1)
        try:
            Minv = np.linalg.inv(M)
        except np.linalg.LinAlgError:
            continue
        rel = (pts - pts[i]) @ Minv.T
        ridx = np.round(rel)
        err = np.linalg.norm(rel - ridx, axis=1)
        inl = err < tol_frac
        score = int(inl.sum())
        if best is None or score > best[0]:
            best = (score, ridx.astype(int), inl)
    if best is None or best[0] < 12:
        return None
    _, idx, inl = best
    idx = idx - idx[inl].min(axis=0)
    return idx, inl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", required=True)
    ap.add_argument("--pitch-mm", type=float, required=True)
    ap.add_argument("--target", type=int, default=40)
    ap.add_argument("--min-dots", type=int, default=20)
    args = ap.parse_args()

    det = blob_detector()
    pitch = args.pitch_mm / 1000.0
    obj_frames, img_frames = [], []
    img_size = None
    last_mean = None
    print(f"[{args.cam}] show a board; tilt/move it. Need {args.target} poses.")
    while len(obj_frames) < args.target:
        try:
            raw = urllib.request.urlopen(
                f"{FRIGATE}/api/{args.cam}/latest.jpg?h=1080", timeout=5).read()
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:
            time.sleep(0.5)
            continue
        img_size = (img.shape[1], img.shape[0])
        kps = det.detect(img)
        pts = np.array([k.pt for k in kps], np.float32)
        fit = fit_lattice(pts) if len(pts) else None
        if fit:
            idx, inl = fit
            if int(inl.sum()) >= args.min_dots:
                c = pts[inl]
                if last_mean is None or np.linalg.norm(c.mean(0) - last_mean) > 30:
                    obj = np.zeros((int(inl.sum()), 3), np.float32)
                    obj[:, :2] = idx[inl] * pitch
                    obj_frames.append(obj.tolist())
                    img_frames.append(c.tolist())
                    last_mean = c.mean(0)
                    print(f"  captured {len(obj_frames)}/{args.target} "
                          f"({int(inl.sum())} dots)")
        time.sleep(0.4)

    body = json.dumps({"object_points": obj_frames, "image_points": img_frames,
                       "image_size": list(img_size)}).encode()
    req = urllib.request.Request(
        f"{TRACKER}/calib/{args.cam}/intrinsics/solve", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    print(urllib.request.urlopen(req).read().decode())


if __name__ == "__main__":
    main()

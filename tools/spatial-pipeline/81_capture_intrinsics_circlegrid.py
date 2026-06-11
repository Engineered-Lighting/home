#!/usr/bin/env python3
"""81_capture_intrinsics_circlegrid.py — intrinsics capture using the user's
Revopoint Miraco dot-grid boards instead of a printed ChArUco.

The boards carry a regular grid of filled white dots plus coded ring
fiducials. OpenCV's findCirclesGrid handles the dot grid; the rings are
rejected by blob-shape filtering (they are hollow -> low convexity /
inertia). Unlike ChArUco, the FULL grid must be visible in a frame for it
to count — hold the board fully in view.

Usage:
  venv\\Scripts\\python 81_capture_intrinsics_circlegrid.py --cam kitchen
      --rows 6 --cols 9 --pitch-mm 30 [--target 45] [--asymmetric]

Polls Frigate latest.jpg like 80_capture_intrinsics.py, collects novel-pose
detections, then POSTs to the tracker:
  POST /calib/<cam>/intrinsics/solve  {object_points, image_points, image_size}
"""
from __future__ import annotations

import argparse
import time
import urllib.request

import cv2
import numpy as np

FRIGATE = "http://192.168.0.125:5000"
TRACKER = "http://192.168.0.100:8098"


def blob_detector(min_area=30):
    p = cv2.SimpleBlobDetector_Params()
    p.filterByArea = True
    p.minArea = min_area
    p.maxArea = 20000
    p.filterByCircularity = True
    p.minCircularity = 0.75       # rings score low (arc segments)
    p.filterByConvexity = True
    p.minConvexity = 0.85         # hollow rings fail convexity
    p.filterByInertia = True
    p.minInertiaRatio = 0.6
    p.filterByColor = True
    p.blobColor = 255             # white dots on black board
    return cv2.SimpleBlobDetector_create(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", required=True)
    ap.add_argument("--rows", type=int, required=True, help="dot rows")
    ap.add_argument("--cols", type=int, required=True, help="dot columns")
    ap.add_argument("--pitch-mm", type=float, required=True,
                    help="center-to-center dot spacing")
    ap.add_argument("--target", type=int, default=45)
    ap.add_argument("--asymmetric", action="store_true")
    args = ap.parse_args()

    size = (args.cols, args.rows)
    flags = (cv2.CALIB_CB_ASYMMETRIC_GRID if args.asymmetric
             else cv2.CALIB_CB_SYMMETRIC_GRID) | cv2.CALIB_CB_CLUSTERING
    det = blob_detector()

    # object points (meters, board plane z=0)
    pitch = args.pitch_mm / 1000.0
    objp = np.zeros((args.rows * args.cols, 3), np.float32)
    for r in range(args.rows):
        for c in range(args.cols):
            x = (2 * c + (r % 2)) * pitch / 2 if args.asymmetric else c * pitch
            objp[r * args.cols + c, :2] = (x, r * pitch)

    collected_img, collected_obj = [], []
    last_centers = None
    img_size = None
    print(f"[{args.cam}] hold a board fully in view; move it near/far/tilted. "
          f"Need {args.target} novel poses. Ctrl+C to stop early.")
    while len(collected_img) < args.target:
        try:
            raw = urllib.request.urlopen(
                f"{FRIGATE}/api/{args.cam}/latest.jpg?h=1080", timeout=5).read()
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:
            time.sleep(0.5)
            continue
        img_size = (img.shape[1], img.shape[0])
        ok, centers = cv2.findCirclesGrid(img, size, flags=flags, blobDetector=det)
        if ok:
            c = centers.reshape(-1, 2)
            if last_centers is None or np.linalg.norm(
                    c.mean(0) - last_centers.mean(0)) > 25 or abs(
                    cv2.contourArea(cv2.convexHull(c.astype(np.float32))) -
                    cv2.contourArea(cv2.convexHull(last_centers.astype(np.float32)))) > 2000:
                collected_img.append(c.tolist())
                collected_obj.append(objp.tolist())
                last_centers = c
                print(f"  captured {len(collected_img)}/{args.target}")
        time.sleep(0.4)

    import json
    body = json.dumps({"object_points": collected_obj,
                       "image_points": collected_img,
                       "image_size": list(img_size)}).encode()
    req = urllib.request.Request(
        f"{TRACKER}/calib/{args.cam}/intrinsics/solve", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    print(urllib.request.urlopen(req).read().decode())


if __name__ == "__main__":
    main()

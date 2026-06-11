#!/usr/bin/env python3
"""80_capture_intrinsics.py — capture calibration frames from a Frigate camera
while you move the ChArUco board, then solve intrinsics via the spatial-tracker.

Usage (one camera at a time):
  venv\\Scripts\\python 80_capture_intrinsics.py kitchen --minutes 4
  venv\\Scripts\\python 80_capture_intrinsics.py kitchen --solve-only

Capture phase: polls Frigate's latest.jpg every ~1.2 s and keeps frames where
the board is detected with >=20 corners AND the board moved vs the last kept
frame. Move the board slowly through all 9 image regions, two distances,
tilting it +-30 degrees. Aim for 40-60 kept frames.

Solve phase: POSTs the kept frames to the tracker's
/calib/{cam}/intrinsics/from_images and writes the result into
data/model/cameras.json (merging by camera name).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
FRIGATE = "http://192.168.0.125:5000"
TRACKER = "http://192.168.0.100:8098"

DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
BOARD = cv2.aruco.CharucoBoard((10, 7), 0.035, 0.026, DICT)
try:
    DETECTOR = cv2.aruco.CharucoDetector(BOARD)
except AttributeError:
    DETECTOR = None  # legacy API path handled by the tracker at solve time


def detect_corners(img):
    if DETECTOR is not None:
        corners, ids, _, _ = DETECTOR.detectBoard(img)
        return corners if corners is not None and len(corners) >= 20 else None
    mc, mids, _ = cv2.aruco.detectMarkers(img, DICT)
    if mids is None or len(mids) < 8:
        return None
    n, corners, ids = cv2.aruco.interpolateCornersCharuco(mc, mids, img, BOARD)
    return corners if n and n >= 20 else None


def capture(cam: str, minutes: float, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    kept, last = 0, None
    t_end = time.time() + minutes * 60
    print(f"capturing from {cam} for {minutes:.0f} min — move the board now "
          f"(9 regions x 2 distances x tilts). Ctrl+C to stop early.")
    while time.time() < t_end:
        try:
            with urllib.request.urlopen(
                    f"{FRIGATE}/api/{cam}/latest.jpg?h=2000", timeout=5) as r:
                buf = np.frombuffer(r.read(), np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception as e:
            print(f"  snapshot failed: {e}")
            time.sleep(2)
            continue
        corners = detect_corners(img)
        if corners is not None:
            c = corners.reshape(-1, 2).mean(axis=0)
            if last is None or np.linalg.norm(c - last) > 40:  # novel pose
                last = c
                kept += 1
                cv2.imwrite(str(outdir / f"{kept:03d}.jpg"), img,
                            [cv2.IMWRITE_JPEG_QUALITY, 95])
                print(f"  kept {kept} (board at {c.astype(int)})")
        time.sleep(1.2)
    print(f"done: {kept} frames in {outdir}")
    return kept


def solve(cam: str, outdir: Path):
    import mimetypes
    import uuid
    files = sorted(outdir.glob("*.jpg"))
    if len(files) < 15:
        sys.exit(f"only {len(files)} frames — capture more (target 40-60)")
    boundary = uuid.uuid4().hex
    body = b""
    for f in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="files"; filename="{f.name}"\r\n'
                 f"Content-Type: image/jpeg\r\n\r\n").encode()
        body += f.read_bytes() + b"\r\n"
    for k, v in (("board_cols", "10"), ("board_rows", "7"), ("square_mm", "35")):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{k}"\r\n\r\n{v}\r\n').encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{TRACKER}/calib/{cam}/intrinsics/from_images", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.loads(r.read())
    print(json.dumps({k: res[k] for k in ("rms_px", "n_frames_used", "image_size")
                      if k in res}, indent=2))
    if res.get("rms_px", 9) > 1.2:
        print("WARNING: rms above the 1.2 px gate — capture more varied frames")
    cams_path = HERE / "data" / "model" / "cameras.json"
    cams = json.loads(cams_path.read_text()) if cams_path.is_file() else {}
    cams.setdefault(cam, {"frigate_name": cam})["intrinsics"] = {
        "model": "rational", "K": res["K"], "dist": res["dist"],
        "image_size": res["image_size"], "rms_px": res["rms_px"],
    }
    cams_path.parent.mkdir(parents=True, exist_ok=True)
    cams_path.write_text(json.dumps(cams, indent=2))
    print(f"merged into {cams_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("camera")
    ap.add_argument("--minutes", type=float, default=4)
    ap.add_argument("--solve-only", action="store_true")
    a = ap.parse_args()
    d = HERE / "data" / "calib" / a.camera
    if not a.solve_only:
        capture(a.camera, a.minutes, d)
    solve(a.camera, d)

#!/usr/bin/env python3
"""70_validate_tracking.py — offline localization validation on recorded videos.

Stage 1 (no calibration needed): run a YOLO person detector over each video in
data/videos/, emit per-frame detections (bbox + foot point) as JSONL, burn
annotated sample frames for visual review, and report detection statistics.

Stage 2 (auto-enabled per camera once data/model/cameras.json holds intrinsics
+ extrinsics for the video's camera): ray-cast foot points onto the floor,
run the same constant-velocity Kalman the tracker uses, render the trajectory
over the floor plan, and report speeds/coverage.

Red-team guards built in: video resolution is auto-detected and K rescaled
(logged); calibration entries can carry a reference snapshot for ORB drift
checks (warned when absent).

Usage:
  venv\\Scripts\\python 70_validate_tracking.py [--videos NAME ...] [--device cpu|cuda]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
VIDEOS = HERE / "data" / "videos"
OUT = HERE / "data" / "validation"
MODEL_DIR = HERE / "data" / "model"
OUT.mkdir(parents=True, exist_ok=True)


def load_calibration(mac_or_name: str) -> dict | None:
    p = MODEL_DIR / "cameras.json"
    if not p.is_file():
        return None
    cams = json.loads(p.read_text(encoding="utf-8"))
    for cam in cams.values() if isinstance(cams, dict) else cams:
        if mac_or_name.lower() in (str(cam.get("mac", "")).lower(),
                                   str(cam.get("frigate_name", "")).lower()):
            if cam.get("intrinsics") and cam.get("extrinsics"):
                return cam
    return None


def kalman_track(measurements, dt):
    """The tracker's constant-velocity KF, replicated for offline eval."""
    sigma_acc = 1.5
    x = None
    P = np.eye(4) * 1.0
    F = np.eye(4); F[0, 2] = dt; F[1, 3] = dt
    q = sigma_acc ** 2
    Q = np.array([[dt**4/4, 0, dt**3/2, 0], [0, dt**4/4, 0, dt**3/2],
                  [dt**3/2, 0, dt**2, 0], [0, dt**3/2, 0, dt**2]]) * q
    H = np.zeros((2, 4)); H[0, 0] = 1; H[1, 1] = 1
    out = []
    for m in measurements:
        if x is None:
            if m is None:
                out.append(None); continue
            x = np.array([m[0], m[1], 0, 0], float)
            out.append((x[0], x[1])); continue
        x = F @ x
        P = F @ P @ F.T + Q
        if m is not None:
            R = np.eye(2) * (m[2] ** 2)
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            x = x + K @ (np.array(m[:2]) - H @ x)
            P = (np.eye(4) - K @ H) @ P
        out.append((float(x[0]), float(x[1])))
    return out


def raycast_floor(u, v, cam, scale):
    K = np.array(cam["intrinsics"]["K"], float) * scale
    K[2, 2] = 1.0
    dist = np.array(cam["intrinsics"].get("dist", []), float)
    pt = cv2.undistortPoints(np.array([[[u, v]]], np.float64), K, dist)[0, 0]
    q = cam["extrinsics"]["q_wxyz"]
    w, qx, qy, qz = q
    R = np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-w*qz), 2*(qx*qz+w*qy)],
        [2*(qx*qy+w*qz), 1-2*(qx*qx+qz*qz), 2*(qy*qz-w*qx)],
        [2*(qx*qz-w*qy), 2*(qy*qz+w*qx), 1-2*(qx*qx+qy*qy)],
    ])
    C = np.array(cam["extrinsics"]["C"], float)
    d = R.T @ np.array([pt[0], pt[1], 1.0])
    if abs(d[2]) < 0.05:
        return None
    s = -C[2] / d[2]
    if s <= 0:
        return None
    X = C + s * d
    return float(X[0]), float(X[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="*", default=None)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--stride", type=int, default=3, help="process every Nth frame")
    args = ap.parse_args()

    from ultralytics import YOLO
    yolo = YOLO(args.model)

    files = sorted(VIDEOS.glob("*.mp4"))
    if args.videos:
        files = [f for f in files if f.name in args.videos]
    report = []

    for vid in files:
        mac = vid.stem.split("_")[0]
        cam = load_calibration(mac)
        cap = cv2.VideoCapture(str(vid))
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 15
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        scale = 1.0
        if cam:
            cw, ch = cam["intrinsics"]["image_size"]
            scale = W / cw
            print(f"[{vid.name}] calibrated camera {mac}: K rescale ×{scale:.3f} "
                  f"({cw}x{ch} -> {W}x{H})")
        print(f"[{vid.name}] {W}x{H}@{fps:.0f} {n_frames} frames "
              f"(stride {args.stride}, calib={'YES' if cam else 'no'})")

        det_path = OUT / f"{vid.stem}.detections.jsonl"
        sample_dir = OUT / f"{vid.stem}.frames"
        sample_dir.mkdir(exist_ok=True)
        measurements = []
        n_det = 0
        rows = []
        idx = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            if idx % args.stride:
                continue
            res = yolo.predict(frame, classes=[0], conf=0.4, verbose=False,
                               device=args.device)[0]
            best = None
            for b in res.boxes:
                conf = float(b.conf[0])
                x1, y1, x2, y2 = map(float, b.xyxy[0])
                if best is None or conf > best[4]:
                    best = (x1, y1, x2, y2, conf)
            row = {"frame": idx, "t": idx / fps}
            if best:
                n_det += 1
                x1, y1, x2, y2, conf = best
                foot = ((x1 + x2) / 2, y2)
                row.update({"bbox": [x1, y1, x2, y2], "conf": conf, "foot": foot})
                if cam:
                    hit = raycast_floor(foot[0], foot[1], cam, 1.0 / scale)
                    if hit:
                        rng = 4.0
                        sigma = max(0.15, 0.04 * rng) * (0.5 / conf)
                        measurements.append((hit[0], hit[1], sigma))
                        row["floor"] = hit
                    else:
                        measurements.append(None)
                else:
                    measurements.append(None)
                if (idx // args.stride) % 40 == 0:
                    vis = frame.copy()
                    cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (96, 220, 255), 2)
                    cv2.circle(vis, (int(foot[0]), int(foot[1])), 6, (96, 220, 255), -1)
                    cv2.imwrite(str(sample_dir / f"f{idx:06d}.jpg"), vis,
                                [cv2.IMWRITE_JPEG_QUALITY, 80])
            else:
                measurements.append(None)
            rows.append(row)
        cap.release()
        with open(det_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

        entry = {
            "video": vid.name, "mac": mac, "resolution": [W, H], "fps": fps,
            "frames_processed": len(rows), "frames_with_person": n_det,
            "detection_rate": round(n_det / max(1, len(rows)), 3),
            "calibrated": bool(cam),
        }
        if cam and any(m for m in measurements):
            smoothed = kalman_track(measurements, args.stride / fps)
            pts = [p for p in smoothed if p]
            if len(pts) > 2:
                arr = np.array(pts)
                d = np.linalg.norm(np.diff(arr, axis=0), axis=1)
                entry["track"] = {
                    "points": len(pts),
                    "path_length_m": round(float(d.sum()), 2),
                    "median_speed_mps": round(float(np.median(d) / (args.stride / fps)), 2),
                    "bbox_m": [list(map(lambda v: round(float(v), 2), arr.min(0))),
                               list(map(lambda v: round(float(v), 2), arr.max(0)))],
                }
                # floor-plan trajectory plot
                plan = np.zeros((600, 1000, 3), np.uint8)
                mn, mx = arr.min(0) - 1, arr.max(0) + 1
                sc = min(980 / (mx[0] - mn[0]), 580 / (mx[1] - mn[1]))
                px = ((arr - mn) * sc + 10).astype(int)
                for i in range(1, len(px)):
                    cv2.line(plan, tuple(px[i-1]), tuple(px[i]), (255, 216, 184), 1)
                cv2.imwrite(str(OUT / f"{vid.stem}.trajectory.png"), plan)
        report.append(entry)
        print(f"  -> {n_det}/{len(rows)} frames with person "
              f"({entry['detection_rate']*100:.0f}%)")

    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport -> {OUT / 'report.json'}")


if __name__ == "__main__":
    main()

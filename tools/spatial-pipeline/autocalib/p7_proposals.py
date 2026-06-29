#!/usr/bin/env python3
"""P7: device-placement proposals from YOLO detections + calibrated rays."""
import json
import os
import urllib.request

import numpy as np
import cv2
import trimesh

AC = os.path.expanduser("~/autocalib")
CAMS = {
    "kitchen": "kitchen_auto_calib.json",
    "living_room": "living_auto_calib.json",
    "dining_room": "dining_room_auto_calib.json",
}
KEEP = {"tv", "couch", "chair", "potted plant", "refrigerator", "oven",
        "microwave", "sink", "dining table", "bed", "laptop", "vase"}
CONF_MIN = 0.35
DEDUPE_M = 0.6
GATE = {"x": (0.0, 15.0), "y": (0.0, 8.5), "z": (-0.2, 2.5)}
MIN_HIT_DIST = 0.2  # ignore degenerate hits right at the camera

# ---------------------------------------------------------------- mesh
scene = trimesh.load(os.path.join(AC, "mesh_full.glb"), force="scene")
mesh = trimesh.util.concatenate(
    [g for g in scene.dump() if isinstance(g, trimesh.Trimesh)])
try:
    from trimesh.ray.ray_pyembree import RayMeshIntersector
    ray_engine = RayMeshIntersector(mesh)
    engine_name = "embree"
except BaseException as e:  # noqa
    ray_engine = mesh.ray
    engine_name = type(mesh.ray).__name__
print(f"mesh: {len(mesh.vertices)} verts, {len(mesh.faces)} faces, "
      f"ray engine: {engine_name}")

# ---------------------------------------------------------------- yolo
from ultralytics import YOLO
model = YOLO(os.path.join(AC, "yolo11m.pt"))  # auto-downloads if missing


def ray_hit(origin, direction):
    """Nearest mesh hit along ray, or None."""
    locs, idx_ray, _ = ray_engine.intersects_location(
        origin[None], direction[None], multiple_hits=True)
    if len(locs) == 0:
        return None
    d = np.linalg.norm(locs - origin[None], axis=1)
    ok = d > MIN_HIT_DIST
    if not ok.any():
        return None
    return locs[ok][np.argmin(d[ok])]


proposals = []
for cam, calib_file in CAMS.items():
    calib = json.load(open(os.path.join(AC, calib_file)))
    K = np.array(calib["K"], dtype=np.float64)
    dist = np.array(calib["dist_k1k2p1p2k3"], dtype=np.float64)
    c2w = np.array(calib["cam_to_world_opencv"], dtype=np.float64)
    R = c2w[:3, :3]
    C = c2w[:3, 3]

    jpg = os.path.join(AC, f"p7_{cam}.jpg")
    urllib.request.urlretrieve(
        f"http://192.168.0.125:5000/api/{cam}/latest.jpg", jpg)
    img = cv2.imread(jpg)
    print(f"\n[{cam}] frame {img.shape[1]}x{img.shape[0]}  C={np.round(C,2)}")

    res = model.predict(img, conf=CONF_MIN, verbose=False)[0]
    names = res.names
    for box in res.boxes:
        label = names[int(box.cls.item())]
        conf = float(box.conf.item())
        if label not in KEEP or conf <= CONF_MIN:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        u = 0.5 * (x1 + x2)
        v = 0.5 * (y1 + y2) if label == "tv" else y2  # tv: center ray

        # undistort pixel -> normalized cam ray
        pt = np.array([[[u, v]]], dtype=np.float64)
        xn, yn = cv2.undistortPoints(pt, K, dist)[0, 0]
        d_cam = np.array([xn, yn, 1.0])
        d_cam /= np.linalg.norm(d_cam)
        d_world = R @ d_cam

        hit = ray_hit(C, d_world)
        status = "MISS" if hit is None else f"hit {np.round(hit,2)}"
        print(f"  {label:<13} conf={conf:.2f} px=({u:.0f},{v:.0f}) {status}")
        if hit is None:
            continue
        proposals.append({
            "type": label.replace(" ", "_"),
            "label": label,
            "conf": round(conf, 3),
            "cam": cam,
            "pos": [round(float(x), 3) for x in hit],
            "px": [round(float(u), 1), round(float(v), 1)],
        })

# ---------------------------------------------------------- gate + dedupe
def in_gate(p):
    x, y, z = p["pos"]
    return (GATE["x"][0] <= x <= GATE["x"][1]
            and GATE["y"][0] <= y <= GATE["y"][1]
            and GATE["z"][0] <= z <= GATE["z"][1])


gated = [p for p in proposals if in_gate(p)]
rejected = [p for p in proposals if not in_gate(p)]

gated.sort(key=lambda p: -p["conf"])
survivors = []
for p in gated:
    dup = any(s["type"] == p["type"]
              and np.linalg.norm(np.array(s["pos"]) - np.array(p["pos"])) < DEDUPE_M
              for s in survivors)
    if not dup:
        survivors.append(p)

out = os.path.join(AC, "proposals.json")
with open(out, "w") as f:
    json.dump(survivors, f, indent=2)

print(f"\nraw={len(proposals)}  gate-rejected={len(rejected)}  "
      f"dedupe-removed={len(gated)-len(survivors)}  survivors={len(survivors)}")
for p in rejected:
    print(f"  REJECTED {p['type']} {p['cam']} pos={p['pos']}")

print(f"\n{'type':<14}{'conf':<7}{'cam':<13}{'x':>7}{'y':>7}{'z':>7}   px")
print("-" * 64)
for p in sorted(survivors, key=lambda p: (p["type"], -p["conf"])):
    x, y, z = p["pos"]
    print(f"{p['type']:<14}{p['conf']:<7.2f}{p['cam']:<13}"
          f"{x:>7.2f}{y:>7.2f}{z:>7.2f}   {p['px']}")
print(f"\nwrote {out}")

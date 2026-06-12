"""Merge auto-calibration results into the apartment model (runs in tracker
container; calib JSONs docker-cp'd to /tmp/<cam>_auto_calib.json first)."""
import json
import os
import urllib.request

CAMS = ["kitchen", "living_room", "dining_room"]
base = os.environ["HA_URL"].rstrip("/") + "/api/extended_openai_conversation/apartment_model"
hdr = {"Authorization": "Bearer " + os.environ["HA_TOKEN"], "Content-Type": "application/json"}
m = json.load(urllib.request.urlopen(urllib.request.Request(base, headers=hdr)))

applied = []
for cam in CAMS:
    p = f"/tmp/{cam}_auto_calib.json"
    if not os.path.exists(p):
        continue
    c = json.load(open(p))
    # normalize key variants from auto_calibrate.py
    c["dist"] = c.get("dist") or c.get("dist_k1k2p1p2k3")
    c["q_wxyz"] = c.get("q_wxyz") or c.get("q_wxyz_world_to_cam")
    c["t"] = c.get("t") or c.get("t_world_to_cam")
    c["C"] = c.get("C") or c.get("C_world")
    dev = next((d for d in m.get("devices", [])
                if (d.get("camera") or {}).get("frigate_name") == cam
                or d.get("id") == f"dev-camera_{cam}"), None)
    if not dev:
        continue
    dev.setdefault("camera", {})
    dev["camera"]["intrinsics"] = {
        "K": c["K"], "dist": c["dist"], "image_size": [1280, 720],
        "rms_px": c["rms_px"], "source": "auto_scan_match"}
    dev["camera"]["extrinsics"] = {
        "q_wxyz": c["q_wxyz"], "t": c["t"], "C": c["C"],
        "rms_px": c["rms_px"], "source": "auto_scan_match"}
    dev["source"] = "calibrated"
    dev["confidence"] = 0.9
    # place the marker at the solved camera position
    dev["pos"] = [round(v, 3) for v in c["C"]]
    applied.append((cam, round(c["rms_px"], 2)))

req = urllib.request.Request(base, data=json.dumps(m).encode(), headers=hdr, method="POST")
res = json.load(urllib.request.urlopen(req))
print("applied:", applied, "| new revision:", res.get("revision"))

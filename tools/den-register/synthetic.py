"""Synthetic den scenes for the tests: a fictional room, camera and control points.

Builds an R-shaped take folder (take.json, frames.csv, frames/*.npz) whose
bundles hold a depth plane and a rectified left image rendered from a known
camera pose, so a fit must recover that pose. Nothing here is real data.
"""
import hashlib
import io
import json
import math
import struct
import zipfile
from pathlib import Path

W, H = 640, 400
K = [[450.0, 0.0, 320.0], [0.0, 450.0, 200.0], [0.0, 0.0, 1.0]]
SESSION = "synthetic-camera-session"


def rotation_xyz(ax, ay, az):
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    mul = lambda a, b: [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return mul(rz, mul(ry, rx))


def camera_pose():
    """room_from_camera for a camera high in a corner looking into the room.

    Camera axes (rectified left): +x right, +y down, +z forward. Room: z up.
    The camera sits at (0.4, 0.3, 2.1) m, looks along room +x+y, tilted down.
    """
    # Start from a camera looking along room +y with image-down = room -z.
    base = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]  # columns: cam x, y, z in room coords
    tilt = rotation_xyz(-math.radians(25), 0, 0)  # pitch down
    yaw = rotation_xyz(0, 0, -math.radians(40))
    mul = lambda a, b: [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    rotation = mul(yaw, mul(tilt, base))
    return rotation, [0.4, 0.3, 2.1]


def moved(pose=None, yaw_deg=0.0, pitch_deg=0.0, shift=(0.0, 0.0, 0.0)):
    """The pose after a small knock: a turn about the room's vertical, a pitch about the camera's x, a slide."""
    rotation, translation = pose or camera_pose()
    mul = lambda a, b: [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    rotation = mul(mul(rotation_xyz(0, 0, math.radians(yaw_deg)), rotation), rotation_xyz(math.radians(pitch_deg), 0, 0))
    return rotation, [translation[i] + shift[i] for i in range(3)]


def to_camera(rotation, translation, room_point):
    d = [room_point[i] - translation[i] for i in range(3)]
    return [sum(rotation[j][i] * d[j] for j in range(3)) for i in range(3)]


def project(camera_point):
    x, y, z = camera_point
    return K[0][0] * x / z + K[0][2], K[1][1] * y / z + K[1][2]


# Fictional control points in the room frame (metres): floor corners of
# furniture, a wall mark, a shelf edge. Ids are what an owner would write.
POINTS = {
    "p1": [2.6, 3.1, 0.0], "p2": [1.2, 3.8, 0.75], "p3": [3.4, 1.6, 0.45], "p4": [2.0, 2.0, 0.0],
    "c1": [3.0, 3.5, 1.2], "c2": [1.6, 2.6, 0.4],
}
FIT, CHECKS = ("p1", "p2", "p3", "p4"), ("c1", "c2")


def worksheet(points=None, fit=FIT, checks=CHECKS, offsets=None, camera_id="den-oak", scan=None):
    """An owner worksheet for the synthetic room; offsets add metres to measured points.

    scan maps ids to the position the owner read in the scan viewer, if any.
    """
    points = points or POINTS
    offsets, scan = offsets or {}, scan or {}
    rows = []
    for pid in list(fit) + list(checks):
        xyz = [points[pid][i] + offsets.get(pid, [0, 0, 0])[i] for i in range(3)]
        rows.append({"id": pid, "role": "fit" if pid in fit else "check", "label": f"synthetic mark {pid}",
                     "room_xyz_m": [round(v, 6) for v in xyz],
                     "measured_from": {"x": "wall at x = 0", "y": "wall at y = 0", "z": "floor"},
                     "measured_with": "tape", "scan_xyz_m": scan.get(pid)})
    return {"schema": "den-register-worksheet-1", "room_id": "den", "camera_id": camera_id,
            "frame": "z_up_metric_floor0",
            "references": {"x": "the synthetic wall at x = 0", "y": "the synthetic wall at y = 0", "z": "the floor"},
            "measured_by": "A", "measured_on": "2026-11-05", "points": rows}


# A fictional furnished room for the mount check: walls, floor, ceiling, a table and a sofa.
ROOM_SIZE = (4.572, 3.759, 2.438)
FURNITURE = (((1.5, 1.5, 0.0), (2.5, 2.3, 0.75)), ((3.6, 0.5, 0.0), (4.5, 2.5, 0.85)))


def _room_hit(origin, ray):
    best = math.inf
    for i in range(3):
        if ray[i] > 1e-12:
            best = min(best, (ROOM_SIZE[i] - origin[i]) / ray[i])
        elif ray[i] < -1e-12:
            best = min(best, -origin[i] / ray[i])
    for low, high in FURNITURE:
        near, far = -math.inf, math.inf
        for i in range(3):
            if abs(ray[i]) < 1e-12:
                if not low[i] <= origin[i] <= high[i]:
                    near, far = 1.0, 0.0
                    break
                continue
            a, b = (low[i] - origin[i]) / ray[i], (high[i] - origin[i]) / ray[i]
            near, far = max(near, min(a, b)), min(far, max(a, b))
        if 0 < near <= far:
            best = min(best, near)
    return best


def room_depth(pose=None, step=4):
    """Depth plane (mm, row-major) of the furnished room from this pose, one ray per step x step block."""
    rotation, origin = pose or camera_pose()
    depth = [0] * (W * H)
    for v0 in range(0, H, step):
        for u0 in range(0, W, step):
            pixel = ((u0 - K[0][2]) / K[0][0], (v0 - K[1][2]) / K[1][1], 1.0)
            ray = [sum(rotation[i][j] * pixel[j] for j in range(3)) for i in range(3)]  # pixel z is 1: s is depth
            mm = min(65535, int(round(_room_hit(origin, ray) * 1000)))
            right = min(u0 + step, W)
            for v in range(v0, min(v0 + step, H)):
                depth[v * W + u0:v * W + right] = [mm] * (right - u0)
    return depth


def npy(descr, shape, body):
    header = "{'descr': %r, 'fortran_order': False, 'shape': %s, }" % (descr, tuple(shape))
    header += " " * ((64 - (10 + len(header) + 1) % 64) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + body


def render(points=None, background_mm=3500, holes=(), marker_px=6, pose=None, background=None):
    """(depth uint16 LE bytes, left uint8 bytes, {id: (u, v, z_m)}) for the synthetic room.

    Each control point is a flat disc of its own depth around its pixel;
    holes is a list of ids whose disc has no depth (0). background is a depth
    plane to draw them on (room_depth), else a flat wall at background_mm.
    """
    points = points or POINTS
    rotation, translation = pose or camera_pose()
    depth = list(background) if background is not None else [background_mm] * (W * H)
    left = bytearray([70]) * (W * H)
    seen = {}
    for pid, room_point in points.items():
        cam = to_camera(rotation, translation, room_point)
        u, v = project(cam)
        if cam[2] <= 0 or not (0 <= u < W and 0 <= v < H):
            continue
        seen[pid] = (u, v, cam[2])
        value = 0 if pid in holes else int(round(cam[2] * 1000))
        for y in range(int(v) - marker_px, int(v) + marker_px + 1):
            for x in range(int(u) - marker_px, int(u) + marker_px + 1):
                if 0 <= x < W and 0 <= y < H and (x - u) ** 2 + (y - v) ** 2 <= marker_px ** 2:
                    depth[y * W + x] = value
                    left[y * W + x] = 230
    return struct.pack(f"<{W * H}H", *depth), bytes(left), seen


def metadata(seq=100, t_device=50.0, depth_seq=None, depth_t=None, intrinsics=True):
    def plane(width, height, s, t, k=None):
        frame = {"sequence": s, "device_timestamp_seconds": t, "host_monotonic_timestamp_seconds": t + 1000.0,
                 "width": width, "height": height, "type": "Type.RAW8", "exposure_duration_seconds": 0.008,
                 "transformation_valid": k is not None}
        if k is not None:
            frame["intrinsics"] = k
        return frame
    k = K if intrinsics else None
    return {"schema": 1, "session": SESSION, "captured_unix_ns": 1_800_000_000_000_000_000,
            "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure",
            "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
            "frames": {"rgb": plane(640, 360, seq, t_device),
                       "depth": plane(W, H, seq if depth_seq is None else depth_seq,
                                      t_device if depth_t is None else depth_t, k),
                       "left": plane(W, H, seq, t_device, k), "right": plane(W, H, seq, t_device, k)}}


def bundle(meta, depth, left):
    raw = json.dumps(meta, separators=(",", ":")).encode()
    arrays = {"depth_mm.npy": npy("<u2", (H, W), depth), "left.npy": npy("|u1", (H, W), left),
              "right.npy": npy("|u1", (H, W), left),
              "rgb_nv12.npy": npy("|u1", (540, 640), bytes([90]) * (640 * 360) + bytes([128]) * (640 * 180)),
              "metadata_json.npy": npy("|u1", (len(raw),), raw)}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, data in arrays.items():
            archive.writestr(name, data)
    return output.getvalue()


def write_take(folder, bundles, *, finished=True):
    """An R-shaped take folder holding these bundles, in order."""
    folder = Path(folder)
    (folder / "frames").mkdir(parents=True)
    lines = ["n,file,rgb_seq,t_s,t_host_s,t_device_s,exposure_s,npz_sha256,recv_pc_mono_s,recv_pc_unix,bytes"]
    for n, data in enumerate(bundles, 1):
        name = f"frames/{n:06d}-{99 + n}.npz"
        (folder / name).write_bytes(data)
        lines.append(f"{n},{name},{99 + n},{(n - 1) * 0.25:.6f},{1000 + n * 0.25!r},{50 + n * 0.25!r},0.008,"
                     f"{hashlib.sha256(data).hexdigest()},{100 + n * 0.25:.6f},{1800000000 + n * 0.25:.6f},"
                     f"{len(data)}")
    (folder / "frames.csv").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    cues = [{"cue": "begin", "text": "Begin.", "pc_mono_s": 100.0, "pc_unix": 1800000000.0},
            {"cue": "hold_phone_end", "text": "Hold the phone to the camera.",
             "pc_mono_s": 100.0 + 0.25 * (len(bundles) + 1), "pc_unix": 1800000001.0}]
    take = {"schema": "den-take-1", "take": folder.name, "script": "free", "cues": cues if finished else [],
            "ended_unix": 1800000100.0 if finished else None, "complete": finished}
    (folder / "take.json").write_text(json.dumps(take, indent=1) + "\n", encoding="utf-8")
    return folder

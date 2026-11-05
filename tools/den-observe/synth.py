"""Synthetic den scenes and R takes with known geometry. Standard library only.

Used by the tests and by `replay.py --demo`. It renders the rectified-left
depth of a simple room (floor, back wall, a flat standing 'person' panel)
seen from a known camera pose, writes R-format bundles with den-take's own
writer, a check file with a slate offset, and a RoomModel whose camera entry
is that pose, and gives the true 3D point and RGB pixel of every figure joint.
Nothing here touches a camera, a network or real footage.

Room frame: x, y on the floor, z up, metres, floor at z = 0.
"""
import io
import json
import math
import struct
import sys
import uuid
import zipfile
from pathlib import Path

import camgeom as g

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "den-take"))
import check  # noqa: E402
import take  # noqa: E402

LEFT_K = {"fx": 400.0, "fy": 400.0, "cx": 320.0, "cy": 200.0}
RGB_K = {"fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 180.0}
RGB_DISTORTION = [0.05, -0.02, 0.001, 0.0005, 0.0, 0.0, 0.0, 0.0]
RGB_FROM_LEFT = ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [-0.0375, 0.0, 0.0])
DEPTH_SIZE, RGB_SIZE = (640, 400), (640, 360)
BASELINE_M = 0.075
UNIX_OFFSET_S = 1.8e9 - 0.12  # the synthetic takes' true host-monotonic -> Unix offset
ARRIVAL_S = 0.05              # bundles arrive this long after capture
TAKE_ENDED_UNIX = 1_800_001_000.0  # take.json's ended_unix, unless a test says otherwise


def camera_pose(position=(0.0, 0.0, 2.2), tilt_deg=20.0):
    """room_from_left (rotation, translation): looking along +y, pitched down by tilt_deg."""
    c, s = math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))
    x_c, y_c, z_c = (1.0, 0.0, 0.0), (0.0, -s, -c), (0.0, c, -s)
    rotation = [[x_c[i], y_c[i], z_c[i]] for i in range(3)]
    return rotation, list(position)


# A flat figure on the panel plane y = PANEL_Y, room metres (x, z).
PANEL_Y, PANEL_X, PANEL_TOP = 3.0, (-0.3, 0.3), 1.75
WALL_Y = 5.0
JOINTS = {"nose": (0.0, 1.62), "left_eye": (0.03, 1.66), "right_eye": (-0.03, 1.66),
          "left_ear": (0.07, 1.63), "right_ear": (-0.07, 1.63),
          "left_shoulder": (0.18, 1.45), "right_shoulder": (-0.18, 1.45),
          "left_elbow": (0.22, 1.15), "right_elbow": (-0.22, 1.15),
          "left_wrist": (0.22, 0.88), "right_wrist": (-0.22, 0.88),
          "left_hip": (0.11, 0.95), "right_hip": (-0.11, 0.95),
          "left_knee": (0.10, 0.52), "right_knee": (-0.10, 0.52),
          "left_ankle": (0.10, 0.15), "right_ankle": (-0.10, 0.15)}


class Scene:
    def __init__(self, room_from_left=None, panel_dx=0.0, panel=True, left_k=None):
        self.room_from_left = room_from_left or camera_pose()
        self.left_from_room = g.inverse(*self.room_from_left)
        self.k_left = g.intrinsics(left_k or LEFT_K)
        self.k_rgb = g.intrinsics(RGB_K)
        self.d_rgb = g.distortion(RGB_DISTORTION)
        self.panel_dx, self.panel = panel_dx, panel

    def joint_room(self, name):
        x, z = JOINTS[name]
        return [x + self.panel_dx, PANEL_Y, z]

    def joint_left(self, name):
        return g.apply(*self.left_from_room, self.joint_room(name))

    def joint_rgb(self, name):
        return g.project(g.apply(*RGB_FROM_LEFT, self.joint_left(name)), self.k_rgb, self.d_rgb)

    def hit(self, origin, direction):
        """Distance along a room ray to the nearest surface, or None."""
        best = math.inf
        if self.panel and abs(direction[1]) > 1e-9:
            s = (PANEL_Y - origin[1]) / direction[1]
            x, z = origin[0] + s * direction[0], origin[2] + s * direction[2]
            if s > 0 and PANEL_X[0] + self.panel_dx <= x <= PANEL_X[1] + self.panel_dx and 0 <= z <= PANEL_TOP:
                best = min(best, s)
        if direction[2] < -1e-9:
            best = min(best, -origin[2] / direction[2])
        if direction[1] > 1e-9:
            best = min(best, (WALL_Y - origin[1]) / direction[1])
        return best if math.isfinite(best) else None

    def depth_mm(self, holes=()):
        """Rectified-left depth in millimetres (0 = none), row-major 640x400."""
        rotation, origin = self.room_from_left
        fx, fy, cx, cy, _ = self.k_left
        width, height = DEPTH_SIZE
        out = bytearray(width * height * 2)
        for v in range(height):
            for u in range(width):
                direction = g.mat_vec(rotation, [(u - cx) / fx, (v - cy) / fy, 1.0])
                s = self.hit(origin, direction)  # direction has unit left-z, so s is the depth
                mm = 0 if s is None else min(65535, int(round(s * 1000)))
                struct.pack_into("<H", out, 2 * (v * width + u), mm)
        for u0, v0, u1, v1 in holes:  # rectangles of invalid depth
            for v in range(v0, v1):
                out[2 * (v * width + u0):2 * (v * width + u1)] = bytes(2 * (u1 - u0))
        return bytes(out)


def npy(descr, shape, body):
    header = "{'descr': %r, 'fortran_order': False, 'shape': %s, }" % (descr, tuple(shape))
    header += " " * ((64 - (10 + len(header) + 1) % 64) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + body


def metadata(session, seq, t_rgb, t_depth, intrinsics=None):
    """Bundle metadata; intrinsics {"rgb": K, "depth": K} marks the frame's transformations valid."""
    def plane(width, height, t, k=None):
        out = {"sequence": seq, "device_timestamp_seconds": t - 3000.0, "host_monotonic_timestamp_seconds": t,
               "width": width, "height": height, "type": "Type.NV12", "exposure_duration_seconds": 0.008,
               "transformation_valid": k is not None}
        if k is not None:
            out["intrinsics"] = [[k["fx"], 0.0, k["cx"]], [0.0, k["fy"], k["cy"]], [0.0, 0.0, 1.0]]
        return out
    k = intrinsics or {}
    return {"schema": 1, "session": session, "captured_unix_ns": 1_800_000_000_000_000_000,
            "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure",
            "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
            "frames": {"rgb": plane(640, 360, t_rgb, k.get("rgb")), "depth": plane(640, 400, t_depth, k.get("depth")),
                       "left": plane(640, 400, t_depth), "right": plane(640, 400, t_depth)},
            "synchronization": {"method": "device_timestamp_group", "hardware_synchronized": False,
                                "threshold_ms": 20, "measured_skew_ms": round((t_rgb - t_depth) * 1000, 3),
                                "within_threshold": abs(t_rgb - t_depth) <= 0.02}}


def bundle(meta, depth):
    raw = json.dumps(meta, separators=(",", ":")).encode()
    arrays = {"depth_mm.npy": npy("<u2", (400, 640), depth),
              "left.npy": npy("|u1", (400, 640), bytes(256_000)),
              "right.npy": npy("|u1", (400, 640), bytes(256_000)),
              "rgb_nv12.npy": npy("|u1", (540, 640), bytes(640 * 360) + b"\x80" * (640 * 180)),
              "metadata_json.npy": npy("|u1", (len(raw),), raw)}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, data in arrays.items():
            archive.writestr(name, data)
    return output.getvalue()


def make_take(folder, frames, depth, *, intrinsics=None, ended_unix=TAKE_ENDED_UNIX):
    """Write an R take: frames is [(t_rgb, t_depth)] host-monotonic seconds; depth is
    one depth frame's bytes or a list with one per frame; RGB sequence numbers
    start at 1000. Returns the folder. It has no check file: see write_check."""
    folder = Path(folder)
    folder.mkdir(parents=True)
    session = "sess-" + uuid.uuid4().hex
    writer = take.TakeWriter(folder)
    for i, (t_rgb, t_depth) in enumerate(frames):
        meta = metadata(session, 1000 + i, t_rgb, t_depth, intrinsics)
        data = bundle(meta, depth[i] if isinstance(depth, list) else depth)
        writer.add(data, check.read_bundle(data)[0], t_rgb + ARRIVAL_S, UNIX_OFFSET_S + t_rgb + ARRIVAL_S)
    writer.close()
    take.write_json(folder / "take.json", {
        "schema": take.SCHEMA, "take": folder.name, "script": "free", "commissioning": False,
        "cues": [{"cue": "begin", "text": "Begin.", "pc_mono_s": 1.0, "pc_unix": 1.8e9}],
        "slates": [], "complete": True, "stop_reason": None, "counts": {"kept": len(writer.index)},
        "ended_unix": ended_unix,
        "sha256": {"frames.csv": check.file_sha256(folder / "frames.csv")}})
    return folder


def write_check(folder, *, verdict="PASS", offset_s=UNIX_OFFSET_S, period_s=10):
    """A check-N.json with the fields den-observe reads: verdict, the start slate's
    offset c (camera minus phone, mod period) and the slate period."""
    c = -(offset_s % period_s)
    c = (c + period_s / 2) % period_s - period_s / 2  # wrap into [-period/2, period/2)
    numbers = [int(p.stem.split("-")[1]) for p in Path(folder).glob("check-*.json")]
    path = Path(folder) / f"check-{1 + max(numbers, default=0)}.json"
    path.write_text(json.dumps({"schema": "den-take-check-1", "verdict": verdict,
                                "method": {"tolerances": {"slate_period_s": period_s}},
                                "reported_only": {"offset_c_mod_period_s": [round(c, 6), round(c, 6)]}}),
                    encoding="utf-8")
    return path


def camera_entry(*, camera_id="den-oak", version="2026-11-12", error_bound_m=0.03, room_from_left=None,
                 left_k=None):
    rotation, translation = room_from_left or camera_pose()
    matrix = [list(rotation[i]) + [translation[i]] for i in range(3)] + [[0, 0, 0, 1]]
    return {"camera_id": camera_id, "version": version, "method": "synthetic", "error_bound_m": error_bound_m,
            "intrinsics": {"source": "synthetic EEPROM", "rectified_left": dict(left_k or LEFT_K),
                           "rgb": dict(RGB_K), "rgb_distortion": list(RGB_DISTORTION),
                           "rgb_from_rectified_left": {"rotation": RGB_FROM_LEFT[0],
                                                       "translation_m": RGB_FROM_LEFT[1]},
                           "stereo_baseline_m": BASELINE_M, "depth_size": list(DEPTH_SIZE),
                           "rgb_size": list(RGB_SIZE)},
            "pose": {"camera_frame": "rectified_left", "room_from_camera": matrix},
            "photometric": {"k_L": None, "unit": "(cd/m²)/nc", "normalisation_version": None,
                            "calibration_id": None, "status": "uncalibrated"}}


def held_entry(*, registered=TAKE_ENDED_UNIX - 100.0, confirmed=TAKE_ENDED_UNIX + 100.0, **kwargs):
    """A camera entry with rung 1's mount record: registered before the take and confirmed after it."""
    entry = camera_entry(**kwargs)
    entry["mount"] = {"take": "synthetic-registration", "take_ended_unix": registered}
    if confirmed is not None:
        entry["confirmed"] = {"take": "synthetic-check", "take_ended_unix": confirmed}
    return entry


def room_model(cameras=None):
    """A RoomModel with schemas.json's top-level fields and the given camera entries."""
    return {"room_id": "den", "version": "den-synth-1", "frame": "z_up_metric_floor0", "surfaces": [],
            "cameras": cameras if cameras is not None else [camera_entry()], "fixtures": [], "landmarks": [],
            "provenance": {"cameras": {"method": "manual", "ts": None, "confidence": None}}}

"""Shared helpers for the spatial pipeline (manifest, PLY IO, transforms)."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"
OUT = HERE / "data" / "processed"
MODEL = HERE / "data" / "model"
APP_DATA = Path(r"C:\Claude\home\app\data\apartment")
APP_SIM = Path(r"C:\Claude\home\app\src\assets\apartment")

for d in (OUT, MODEL, APP_DATA, APP_SIM):
    d.mkdir(parents=True, exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def sha8(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def update_manifest(entries: dict) -> None:
    """Merge per-file entries into both manifest copies (app data + sim dir)."""
    for mdir in (APP_DATA, APP_SIM):
        mpath = mdir / "manifest.json"
        manifest = {"version": 1, "files": {}}
        if mpath.exists():
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
        manifest.setdefault("files", {}).update(entries)
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_registration() -> dict:
    return json.loads((MODEL / "registration.json").read_text(encoding="utf-8"))


def save_registration(reg: dict) -> None:
    (MODEL / "registration.json").write_text(json.dumps(reg, indent=2), encoding="utf-8")


def apply_T(T: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ np.asarray(T)[:3, :3].T + np.asarray(T)[:3, 3]


def read_3dgs_ply(path: Path):
    """Read a 3DGS splat PLY -> (positions Nx3 f32, rgb Nx3 f32 0..1, alpha N f32).

    Hand-rolled binary reader (plyfile also works, this avoids the dep being
    a bottleneck on 143MB files and gives explicit control of the layout).
    """
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        lines = header.decode("ascii").splitlines()
        n = next(int(l.split()[-1]) for l in lines if l.startswith("element vertex"))
        props = [l.split()[-1] for l in lines if l.startswith("property")]
        body = np.fromfile(f, dtype="<f4", count=n * len(props)).reshape(n, len(props))
    i = {p: k for k, p in enumerate(props)}
    pos = body[:, [i["x"], i["y"], i["z"]]].astype(np.float32)
    SH_C0 = 0.28209479177387814
    rgb = np.clip(0.5 + SH_C0 * body[:, [i["f_dc_0"], i["f_dc_1"], i["f_dc_2"]]], 0, 1)
    alpha = 1.0 / (1.0 + np.exp(-body[:, i["opacity"]]))
    return pos, rgb.astype(np.float32), alpha.astype(np.float32)


def write_points_ply(path: Path, pos: np.ndarray, intensity: np.ndarray) -> None:
    """Write the app's white-cloud PLY: x,y,z f32 + intensity u8, Z-up apartment frame.

    The custom in-app streaming parser (home-3d/pointcloud.js) depends on EXACTLY
    this header layout — keep the comment marker in sync.
    """
    n = len(pos)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment homeapt-points v1 zup metric\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar intensity\nend_header\n"
    ).encode("ascii")
    rec = np.zeros(n, dtype=[("xyz", "<f4", 3), ("i", "u1")])
    rec["xyz"] = pos.astype(np.float32)
    rec["i"] = np.clip(intensity, 0, 255).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(header)
        rec.tofile(f)


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation matrix taking unit vector a to unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else -np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))

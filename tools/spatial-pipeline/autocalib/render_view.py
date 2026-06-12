#!/usr/bin/env python3
"""Headless renderer for the apartment textured mesh (mesh_full.glb).

Renders COLOR + DEPTH from a given pos/lookat pose using pyrender (EGL,
GPU-headless). Apartment frame: Z-up, meters, floor z=0.

Output camera convention: OpenCV (+X right, +Y down, +Z forward).
pyrender internally uses the OpenGL convention (+X right, +Y up, -Z forward);
the conversion is c2w_gl = c2w_cv @ diag(1, -1, -1, 1).

Outputs:
  <prefix>.png        - color render (RGB)
  <prefix>_depth.npy  - float32 depth in meters (eye-space z), 0 = no hit
  <prefix>_meta.json  - K (3x3), cam-to-world 4x4 (OpenCV), sizes, fov,
                        round-trip proof error, render time

Backprojection helper: backproject(u, v, depth, K, c2w_cv) -> world xyz.
Proof: project mesh vertices with (K, pose), backproject them, require
max round-trip error < 1e-3 m. Runs on every invocation (--no-proof to skip).

Usage:
  PYOPENGL_PLATFORM=egl python3 render_view.py \
      --pos 7.0,5.6,2.3 --lookat 3.8,5.7,1.0 --fov-deg 75 \
      --w 1280 --h 720 --out /path/prefix
"""
import os
os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

import argparse
import json
import sys
import time

import numpy as np
if not hasattr(np, "infty"):          # numpy>=2 compat for pyrender 0.1.45
    np.infty = np.inf

import trimesh
import pyrender
from PIL import Image

CV_TO_GL = np.diag([1.0, -1.0, -1.0, 1.0])


# ----------------------------------------------------------------------------
# Pose / intrinsics math
# ----------------------------------------------------------------------------

def look_at_cv(pos, lookat, up_world=(0.0, 0.0, 1.0)):
    """Camera-to-world 4x4 in OpenCV convention (+Z forward, +Y down).

    Apartment Z is up. Right-handed: right x down = forward.
    """
    pos = np.asarray(pos, dtype=np.float64)
    lookat = np.asarray(lookat, dtype=np.float64)
    fwd = lookat - pos
    n = np.linalg.norm(fwd)
    if n < 1e-9:
        raise ValueError("pos and lookat coincide")
    fwd = fwd / n

    up = np.asarray(up_world, dtype=np.float64)
    right = np.cross(fwd, up)
    rn = np.linalg.norm(right)
    if rn < 1e-6:  # looking straight up/down: fall back to world +X as hint
        right = np.cross(fwd, np.array([1.0, 0.0, 0.0]))
        rn = np.linalg.norm(right)
    right = right / rn
    down = np.cross(fwd, right)  # unit; fwd x right = "down" in OpenCV frame

    c2w = np.eye(4, dtype=np.float64)
    c2w[:3, 0] = right
    c2w[:3, 1] = down
    c2w[:3, 2] = fwd
    c2w[:3, 3] = pos
    return c2w


def intrinsics(w, h, fov_deg):
    """K with fx = fy from horizontal FOV, cx = w/2, cy = h/2."""
    fx = (w / 2.0) / np.tan(np.deg2rad(fov_deg) / 2.0)
    fy = fx
    K = np.array([[fx, 0.0, w / 2.0],
                  [0.0, fy, h / 2.0],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    return K


def project(world_pts, K, c2w_cv):
    """World Nx3 -> (u, v, depth) using OpenCV camera. depth = eye-space z."""
    world_pts = np.asarray(world_pts, dtype=np.float64)
    w2c = np.linalg.inv(c2w_cv)
    pc = world_pts @ w2c[:3, :3].T + w2c[:3, 3]
    z = pc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = K[0, 0] * pc[:, 0] / z + K[0, 2]
        v = K[1, 1] * pc[:, 1] / z + K[1, 2]
    return u, v, z


def backproject(u, v, depth, K, c2w_cv):
    """Pixel (u, v) + depth (eye-space z, meters) -> world xyz.

    u, v, depth may be scalars or same-shape arrays. Returns (..., 3).
    """
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    depth = np.asarray(depth, dtype=np.float64)
    x = (u - K[0, 2]) / K[0, 0] * depth
    y = (v - K[1, 2]) / K[1, 1] * depth
    pc = np.stack(np.broadcast_arrays(x, y, depth), axis=-1)
    R = np.asarray(c2w_cv)[:3, :3]
    t = np.asarray(c2w_cv)[:3, 3]
    return pc @ R.T + t


def roundtrip_proof(mesh_world, K, c2w_cv, w, h, max_pts=50000):
    """Project mesh vertices, backproject, return (max_err, n_used)."""
    verts = mesh_world.vertices.view(np.ndarray).astype(np.float64)
    if len(verts) > max_pts:
        idx = np.random.default_rng(0).choice(len(verts), max_pts, replace=False)
        verts = verts[idx]
    u, v, z = project(verts, K, c2w_cv)
    keep = (z > 0.05) & (u >= 0) & (u < w) & (v >= 0) & (v < h)
    if keep.sum() == 0:
        return float("nan"), 0
    rec = backproject(u[keep], v[keep], z[keep], K, c2w_cv)
    err = np.linalg.norm(rec - verts[keep], axis=1)
    return float(err.max()), int(keep.sum())


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

def render(glb_path, pos, lookat, fov_deg, w, h, out_prefix,
           lit=False, znear=0.05, zfar=100.0, proof=True):
    t0 = time.time()
    tm_scene = trimesh.load(glb_path, force="scene")
    t_load = time.time() - t0

    K = intrinsics(w, h, fov_deg)
    c2w_cv = look_at_cv(pos, lookat)
    c2w_gl = c2w_cv @ CV_TO_GL

    scene = pyrender.Scene.from_trimesh_scene(
        tm_scene, ambient_light=np.array([1.0, 1.0, 1.0]))
    cam = pyrender.IntrinsicsCamera(
        fx=K[0, 0], fy=K[1, 1], cx=K[0, 2], cy=K[1, 2],
        znear=znear, zfar=zfar)
    scene.add(cam, pose=c2w_gl)

    flags = pyrender.RenderFlags.NONE
    if lit:
        light = pyrender.DirectionalLight(color=np.ones(3), intensity=3.0)
        scene.add(light, pose=c2w_gl)
    else:
        flags |= pyrender.RenderFlags.FLAT  # baked textures: no lighting

    t1 = time.time()
    r = pyrender.OffscreenRenderer(viewport_width=w, viewport_height=h)
    color, depth = r.render(scene, flags=flags)
    r.delete()
    t_render = time.time() - t1

    Image.fromarray(color).save(out_prefix + ".png")
    np.save(out_prefix + "_depth.npy", depth.astype(np.float32))

    valid = depth > 0
    rt_err, rt_n = (float("nan"), 0)
    if proof:
        mesh_world = tm_scene.to_mesh() if hasattr(tm_scene, "to_mesh") \
            else tm_scene.dump(concatenate=True)
        rt_err, rt_n = roundtrip_proof(mesh_world, K, c2w_cv, w, h)

    meta = {
        "width": w, "height": h, "fov_deg": fov_deg,
        "fov_axis": "horizontal",
        "K": K.tolist(),
        "cam_to_world_opencv": c2w_cv.tolist(),
        "cam_to_world_opengl": c2w_gl.tolist(),
        "camera_convention": "OpenCV: +X right, +Y down, +Z forward",
        "world_frame": "apartment Z-up, meters, floor z=0",
        "pos": list(map(float, pos)), "lookat": list(map(float, lookat)),
        "znear": znear, "zfar": zfar,
        "depth_units": "meters (eye-space z; 0 = no geometry)",
        "depth_valid_frac": float(valid.mean()),
        "depth_min": float(depth[valid].min()) if valid.any() else None,
        "depth_max": float(depth[valid].max()) if valid.any() else None,
        "roundtrip_max_err_m": rt_err,
        "roundtrip_n_points": rt_n,
        "load_time_s": round(t_load, 3),
        "render_time_s": round(t_render, 3),
        "total_time_s": round(time.time() - t0, 3),
    }
    with open(out_prefix + "_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def parse_vec3(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 3:
        raise argparse.ArgumentTypeError("expected x,y,z")
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mesh", default=os.path.expanduser(
        "~/autocalib/mesh_full.glb"))
    ap.add_argument("--pos", type=parse_vec3, required=True)
    ap.add_argument("--lookat", type=parse_vec3, required=True)
    ap.add_argument("--fov-deg", type=float, default=75.0)
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--out", required=True, help="output path prefix")
    ap.add_argument("--lit", action="store_true",
                    help="shaded render instead of flat textured")
    ap.add_argument("--no-proof", dest="proof", action="store_false")
    args = ap.parse_args()

    meta = render(args.mesh, args.pos, args.lookat, args.fov_deg,
                  args.w, args.h, args.out, lit=args.lit, proof=args.proof)
    print(json.dumps(meta, indent=2))

    if args.proof:
        if not (meta["roundtrip_max_err_m"] < 1e-3):
            print("FAIL: round-trip error %.3e >= 1e-3"
                  % meta["roundtrip_max_err_m"], file=sys.stderr)
            sys.exit(1)
        print("PROOF OK: project->backproject max err %.3e m over %d points"
              % (meta["roundtrip_max_err_m"], meta["roundtrip_n_points"]))


if __name__ == "__main__":
    main()

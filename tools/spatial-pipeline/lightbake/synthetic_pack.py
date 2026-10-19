"""A tiny procedural content pack for the public end-to-end test of steps 90/91.

A 4 x 3 x 2.5 m box room with a window opening in the y = 3 wall, bright
"exterior" splats beyond it, interior splats and points on the walls, a GLB
with an sRGB atlas, content-pack.json with SHA-256 assets, and an equipment
registration bound to it. No Den data.
"""
from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import numpy as np

from . import binding as BND

X, Y, Z = 4.0, 3.0, 2.5
HOLE = (1.5, 2.5, 1.0, 2.0)  # x0, x1, z0, z1 on the y = Y wall


def _room(n=16):
    walls = [([0, 0, 0], [X, 0, 0], [0, Y, 0]), ([0, 0, Z], [0, Y, 0], [X, 0, 0]),
             ([0, 0, 0], [0, 0, Z], [X, 0, 0]), ([0, Y, 0], [X, 0, 0], [0, 0, Z]),
             ([0, 0, 0], [0, Y, 0], [0, 0, Z]), ([X, 0, 0], [0, 0, Z], [0, Y, 0])]
    pos, uv, faces = [], [], []
    for w, (o, u, v) in enumerate(walls):
        o, u, v = map(np.array, (o, u, v))
        base = len(pos)
        for j in range(n + 1):
            for i in range(n + 1):
                pos.append(o + u * i / n + v * j / n)
                uv.append([(w % 3 + 0.01 + i / n * 0.98) / 3, (w // 3 + 0.01 + j / n * 0.98) / 2])
        for j in range(n):
            for i in range(n):
                c = o + u * (i + 0.5) / n + v * (j + 0.5) / n
                if w == 3 and HOLE[0] < c[0] < HOLE[1] and HOLE[2] < c[2] < HOLE[3]:
                    continue
                a = base + j * (n + 1) + i
                faces += [[a, a + 1, a + n + 2], [a, a + n + 2, a + n + 1]]
    pos, faces = np.array(pos, np.float32), np.array(faces, np.int32)
    fn = np.cross(pos[faces[:, 1]] - pos[faces[:, 0]], pos[faces[:, 2]] - pos[faces[:, 0]])
    inward = np.array([X / 2, Y / 2, Z / 2]) - pos[faces].mean(1)
    flip = (fn * inward).sum(1) < 0
    faces[flip] = faces[flip][:, ::-1]
    return pos, np.array(uv, np.float32), faces


def _glb(path, pos, uv, faces, png):
    blobs, views, acc = [], [], []

    def add(arr, ctype, typ, target=None):
        b = np.ascontiguousarray(arr).tobytes()
        off = sum(len(x) for x in blobs)
        blobs.append(b + b"\0" * (-len(b) % 4))
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(b)})
        if ctype is None:
            return len(views) - 1
        a = {"bufferView": len(views) - 1, "componentType": ctype, "count": len(arr), "type": typ}
        if typ == "VEC3":
            a["min"], a["max"] = arr.min(0).tolist(), arr.max(0).tolist()
        acc.append(a)
        return len(acc) - 1
    ip = add(pos, 5126, "VEC3")
    iu = add(uv, 5126, "VEC2")
    ii = add(faces.astype(np.uint32).ravel(), 5125, "SCALAR")
    iv = add(np.frombuffer(png, np.uint8), None, None)
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": ip, "TEXCOORD_0": iu}, "indices": ii, "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0}}],
            "textures": [{"source": 0}], "images": [{"bufferView": iv, "mimeType": "image/png"}],
            "accessors": acc, "bufferViews": views, "buffers": [{"byteLength": sum(len(b) for b in blobs)}]}
    j = json.dumps(gltf).encode()
    j += b" " * (-len(j) % 4)
    binb = b"".join(blobs)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(j) + 8 + len(binb))
    out += struct.pack("<II", len(j), 0x4E4F534A) + j + struct.pack("<II", len(binb), 0x004E4942) + binb
    Path(path).write_bytes(out)


def _splat_ply(path, P, enc_rgb, alpha):
    n = len(P)
    props = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    a = np.zeros((n, len(props)), "<f4")
    a[:, :3] = P
    a[:, 6:9] = (enc_rgb - 0.5) / 0.28209479177387814
    a[:, 9] = np.log(alpha / (1 - alpha))
    a[:, 10:13] = np.log(0.01)
    a[:, 13] = 1
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n + "".join(
        "property float %s\n" % p for p in props) + "end_header\n"
    Path(path).write_bytes(head.encode() + a.tobytes())


def _points_ply(path, P):
    rec = np.zeros(len(P), dtype=[("xyz", "<f4", 3), ("i", "u1")])
    rec["xyz"] = P
    rec["i"] = 128
    head = ("ply\nformat binary_little_endian 1.0\ncomment homeapt-points v1 zup metric\nelement vertex %d\n"
            "property float x\nproperty float y\nproperty float z\nproperty uchar intensity\nend_header\n") % len(P)
    Path(path).write_bytes(head.encode() + rec.tobytes())


ALBEDO = np.array([0.55, 0.45, 0.35])
WINDOW_ENC = np.array([0.9, 0.93, 0.97])


def _capture(pos, uv, faces, P_splat, tex_size, spp, device):
    """Render the capture state (daylight through the opening only) with the tool's own tracer."""
    from . import encode as ENC
    from . import tracer
    from .scan import mesh_normals

    fn, vn, _ = mesh_normals(pos, faces)
    x0, x1, z0, z1 = HOLE
    c = [np.array(v, float) for v in ([x0, Y - 0.01, z0], [x1, Y - 0.01, z0], [x1, Y - 0.01, z1], [x0, Y - 0.01, z1])]
    L = ENC.srgb_eotf(WINDOW_ENC)
    win = {"v0": np.array([c[0], c[0]]), "v1": np.array([c[1], c[2]]), "v2": np.array([c[2], c[3]]),
           "n": np.array([[0, -1.0, 0]] * 2), "L": np.array([L, L])}
    sc = tracer.Scene(device, pos, faces, uv, vn, fn, np.zeros(len(faces), np.int32), np.zeros((4, 4, 3), np.uint8),
                      window=win)
    alb = np.tile(np.round(ENC.srgb_oetf(ALBEDO) * 255).astype(np.uint8), (8, 8, 1))
    tface, tpos, tnrm = sc.raster(pos, tex_size)
    ok = tface >= 0
    rad = np.zeros((tex_size, tex_size, 3))
    rad[ok] = ALBEDO / np.pi * sc.basis(tpos[ok], tnrm[ok], alb, spp=spp, bounces=4)[:, -1, :]
    from .delight import dilate
    rad, _ = dilate(rad, ok, 4)
    face, dist, nrm, _ = sc.closest(P_splat)
    rs = ALBEDO / np.pi * sc.basis(P_splat, nrm, alb, spp=spp, bounces=4)[:, -1, :]
    return rad, rs


def make(root, revision="synthetic-pack-r1", seed=0, spp=256, device=None):
    from PIL import Image

    from . import encode as ENC
    from . import tracer

    rng = np.random.default_rng(seed)
    root = Path(root)
    site = root / "victoria"
    pack = site / revision
    pack.mkdir(parents=True)
    (site / "ACTIVE").write_text(revision)
    pos, uv, faces = _room()
    f = rng.integers(0, len(faces), 3000)
    b = rng.dirichlet([1, 1, 1], 3000)
    Pin = (pos[faces[f]] * b[:, :, None]).sum(1)
    rad, rs = _capture(pos, uv, faces, Pin, 64, spp, tracer.init(device))
    tex = np.round(np.clip(ENC.srgb_oetf(rad), 0, 1) * 255).astype(np.uint8).repeat(4, 0).repeat(4, 1)
    enc_in = np.round(np.clip(ENC.srgb_oetf(rs), 0, 1) * 255) / 255
    buf = io.BytesIO()
    Image.fromarray(tex).save(buf, "PNG")
    _glb(pack / "mesh.glb", pos, uv, faces, buf.getvalue())
    # exterior splats behind the window opening, bright and cool
    m = 1500
    t = rng.uniform(1.5, 4.0, m)
    hx, hz = rng.uniform(HOLE[0] + 0.05, HOLE[1] - 0.05, m), rng.uniform(HOLE[2] + 0.05, HOLE[3] - 0.05, m)
    O = np.array([X / 2, Y / 2, 1.2])
    d = np.stack([hx, np.full(m, Y), hz], 1) - O
    Pex = O + d * (1 + t / np.linalg.norm(d, axis=1))[:, None]
    enc_ex = np.tile(WINDOW_ENC, (m, 1))
    P = np.concatenate([Pin, Pex]).astype(np.float32)
    _splat_ply(pack / "apartment.ply", P, np.concatenate([enc_in, enc_ex]), np.full(len(P), 0.9))
    _splat_ply(pack / "apartment.mobile.ply", P[::3], np.concatenate([enc_in, enc_ex])[::3], np.full(len(P[::3]), 0.9))
    _points_ply(pack / "points.ply", Pin[:2000].astype(np.float32))
    (pack / "frame.json").write_text(json.dumps({"frame": "z_up_metric_floor0"}))
    (pack / "collision.glb").write_bytes((pack / "mesh.glb").read_bytes())
    assets = {}
    for name in ("mesh.glb", "apartment.ply", "apartment.mobile.ply", "points.ply", "frame.json", "collision.glb"):
        p = pack / name
        assets[name] = {"path": name, "bytes": p.stat().st_size, "sha256": BND.sha256_file(p)}
    cp = {"schemaVersion": 1, "siteId": "victoria", "revision": revision, "registrationVersion": 1,
          "sceneAvailable": True, "assets": assets}
    (pack / "content-pack.json").write_text(json.dumps(cp, indent=2))
    (site / (revision + ".provenance.json")).write_text(json.dumps({"interior": {"scan": {"ceilingM": Z}}}))
    bind, _, _ = BND.read_binding(pack)
    walls = [{"id": "wall-a", "label": "A", "normal_xy": [1, 0], "offset_m": 0.0},
             {"id": "wall-b", "label": "B", "normal_xy": [-1, 0], "offset_m": -X},
             {"id": "wall-c", "label": "C", "normal_xy": [0, 1], "offset_m": 0.0},
             {"id": "wall-d", "label": "D window", "normal_xy": [0, -1], "offset_m": -Y}]
    mounts = [{"id": f"mount-0{i + 1}", "kind": "mount", "position_m": p, "position_status": "approximate"}
              for i, p in enumerate([[0.8, 0.7, 2.44], [3.2, 0.7, 2.44], [3.2, 2.3, 2.44], [0.8, 2.3, 2.44]])]
    eq = {"schema": "home-equipment-registration-1", "version": 1, "revision": 1, "digest": "0" * 64,
          "binding": bind, "references": {"walls": walls}, "equipment": mounts}
    (root / "equipment.json").write_text(json.dumps(eq))
    return site, pack, root / "equipment.json"

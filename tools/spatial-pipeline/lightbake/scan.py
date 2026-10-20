"""Read-only loaders for the accepted pack (mesh.glb, splat PLYs, points.ply)
and the equipment registration that places the Den fixtures.

All files are opened with mode "rb"; nothing here writes.
"""
from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import numpy as np

SH_C0 = 0.28209479177387814
_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path):
    """mesh.glb -> dict(positions, uv, faces, texture, trace_positions, trace_uv, trace_faces, trace_source).

    `positions/uv/faces` are the primitive the scene renders (meshes[0].primitives[0]).
    Display cleanup can cut that primitive (the Den's dollhouse view drops parts of the
    room), while trimesh keeps the uncut scan as unreferenced accessors sharing the
    same atlas. If such an index/POSITION/TEXCOORD triple exists and contains every
    rendered vertex, it is returned as the `trace_*` geometry for ray tracing;
    otherwise the rendered primitive is used. Nothing is written.
    """
    from PIL import Image
    from scipy.spatial import cKDTree

    with open(path, "rb") as f:
        data = f.read()
    magic, _, _ = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("not a GLB")
    off, gltf, binary = 12, None, None
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:
            gltf = json.loads(chunk)
        elif ctype == 0x004E4942:
            binary = chunk
        off += 8 + clen

    def view(i):
        bv = gltf["bufferViews"][i]
        return binary[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]

    def accessor(i):
        a = gltf["accessors"][i]
        nc = _NC[a["type"]]
        arr = np.frombuffer(view(a["bufferView"]), dtype=_CT[a["componentType"]], count=a["count"] * nc,
                            offset=a.get("byteOffset", 0))
        return arr.reshape(a["count"], nc) if nc > 1 else arr

    prim = gltf["meshes"][0]["primitives"][0]
    ip, iu, ii = prim["attributes"]["POSITION"], prim["attributes"]["TEXCOORD_0"], prim["indices"]
    pos = accessor(ip).astype(np.float32)
    uv = accessor(iu).astype(np.float32)
    faces = accessor(ii).astype(np.int64).reshape(-1, 3).astype(np.int32)
    mat = gltf["materials"][prim["material"]]
    tex = gltf["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
    img = gltf["images"][tex["source"]]
    Image.MAX_IMAGE_PIXELS = None
    texture = np.asarray(Image.open(io.BytesIO(view(img["bufferView"]))).convert("RGB"))
    out = {"positions": pos, "uv": uv, "faces": faces, "texture": texture,
           "trace_positions": pos, "trace_uv": uv, "trace_faces": faces,
           "trace_source": {"primitive": "rendered", "accessors": [ii, ip, iu]}}
    A = gltf["accessors"]
    best = None
    for i in range(len(A) - 2):
        if i in (ip, iu, ii):
            continue
        a, b, c = A[i], A[i + 1], A[i + 2]
        if (a["type"], b["type"], c["type"]) == ("SCALAR", "VEC3", "VEC2") and b["count"] == c["count"]                 and a["count"] % 3 == 0 and a.get("max", [b["count"]])[0] < b["count"]                 and a["count"] > len(faces) * 3 and (best is None or a["count"] > A[best]["count"]):
            best = i
    if best is not None:
        tp = accessor(best + 1).astype(np.float32)
        d, _ = cKDTree(tp).query(pos)
        if float(d.max()) < 1e-3:
            out.update(trace_positions=tp, trace_uv=accessor(best + 2).astype(np.float32),
                       trace_faces=accessor(best).astype(np.int64).reshape(-1, 3).astype(np.int32),
                       trace_source={"primitive": "uncut scan accessors", "accessors": [best, best + 1, best + 2]})
    return out


def _ply(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line:
                raise ValueError("bad PLY header")
            header += line
        body = f.read()
    lines = header.decode("ascii").splitlines()
    n = next(int(l.split()[-1]) for l in lines if l.startswith("element vertex"))
    props = [(l.split()[1], l.split()[2]) for l in lines if l.startswith("property")]
    tmap = {"float": "<f4", "uchar": "u1", "double": "<f8", "int": "<i4", "uint": "<u4"}
    dt = np.dtype([(name, tmap[t]) for t, name in props])
    return np.frombuffer(body, dtype=dt, count=n)


def read_splats(path):
    """3DGS PLY -> positions, DC colour (unclipped, display-encoded), opacity, scale (metres)."""
    v = _ply(path)
    pos = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float32)
    dc = 0.5 + SH_C0 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1).astype(np.float64)
    alpha = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    scale = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], 1).astype(np.float64))
    return {"positions": pos, "dc": dc, "alpha": alpha, "scale": scale}


def read_points(path):
    v = _ply(path)
    return {"positions": np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float32),
            "intensity": v["intensity"].astype(np.uint8)}


def mesh_normals(pos, faces):
    """Face normals from winding, oriented into the room, and area-weighted vertex normals.

    Scaniverse meshes ship no normals. The winding is taken as authored; if the
    up-facing floor faces point down, the whole mesh is flipped.
    """
    p = pos.astype(np.float64)
    fn = np.cross(p[faces[:, 1]] - p[faces[:, 0]], p[faces[:, 2]] - p[faces[:, 0]])
    area2 = np.linalg.norm(fn, axis=1)
    fz = p[faces].mean(1)[:, 2]
    floor = (fz < 0.1) & (np.abs(fn[:, 2]) > 0.8 * np.maximum(area2, 1e-30))
    flipped = bool(floor.any() and np.sum(np.sign(fn[floor, 2]) * area2[floor]) < 0)
    if flipped:
        fn = -fn
    vn = np.zeros_like(p)
    for k in range(3):
        np.add.at(vn, faces[:, k], fn)
    fn = fn / np.maximum(area2, 1e-30)[:, None]
    vn = vn / np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-30)
    return fn.astype(np.float32), vn.astype(np.float32), flipped


# ------------------------------------------------------------------ equipment / emitters
def read_equipment(path, binding):
    """Equipment registration bound to the same pack. Returns (mounts, fixture ids, walls)."""
    with open(path, "rb") as f:
        doc = json.loads(f.read().decode("utf-8"))
    if doc.get("schema") != "home-equipment-registration-1":
        raise ValueError("not an equipment registration document")
    b = doc.get("binding") or {}
    if any(b.get(k) != v for k, v in binding.items()):
        raise ValueError("equipment registration is bound to a different pack")
    refs = doc["references"]
    walls = [{"id": w["id"], "label": w.get("label", ""), "normal_xy": np.array(w["normal_xy"], float),
              "offset": float(w["offset_m"])} for w in refs["walls"]]
    linked = {}
    for r in doc["equipment"]:
        if r["kind"] == "fixture":
            linked.setdefault(r["mounted_at_ref"], r)
    mounts = []
    for r in doc["equipment"]:
        if r["kind"] != "mount" or r.get("position_m") is None or r.get("position_status") == "missing":
            continue
        fx = linked.get(r["id"])
        mounts.append({"mount_id": r["id"], "fixture_id": fx["id"] if fx else r["id"] + "-virtual-fixture",
                       "position": np.array(r["position_m"], float), "position_status": r["position_status"]})
    return mounts, walls, {"revision": doc["revision"], "digest": doc["digest"]}


# Fixture profile defaults (home-den-fixtures.js defaultFixture; every field "assumed").
DOWN_FWHM_DEG = 60.0          # downlight_ring.beam_deg
RADIAL_HWHM_DEG = 30.0        # radials.beam_half_angle_deg
RADIAL_TILT_DEG = 0.0         # radials.elevation_tilt_deg
RADIAL_AZIMUTHS = [60.0 * i for i in range(6)]  # zoneAzimuth, default calibration


def lobe_peak_per_lumen(hwhm_deg, max_theta_deg=180.0):
    """Peak intensity (cd per lm) of a Gaussian lobe exp(-ln2 (theta/hwhm)^2), truncated."""
    th = np.radians(np.linspace(0, max_theta_deg, 20001))
    f = np.exp(-np.log(2) * (np.degrees(th) / hwhm_deg) ** 2)
    flux = np.trapezoid(f * 2 * np.pi * np.sin(th), th)
    return 1.0 / flux


def fixture_columns(mounts, emitter_drop_m=0.14):
    """28 unit-flux columns: 4 downs then 24 radials (fixture-major), all assumed photometry."""
    cols = []
    down_i0 = lobe_peak_per_lumen(DOWN_FWHM_DEG / 2, 90.0)
    rad_i0 = lobe_peak_per_lumen(RADIAL_HWHM_DEG)
    for m in mounts:
        cols.append({"kind": "downward", "fixture_id": m["fixture_id"], "mount_id": m["mount_id"], "zone": None,
                     "position": m["position"] - [0, 0, emitter_drop_m], "axis": np.array([0, 0, -1.0]),
                     "hwhm_deg": DOWN_FWHM_DEG / 2, "max_theta_deg": 90.0, "peak_cd_per_lm": down_i0})
    for m in mounts:
        for z, az in enumerate(RADIAL_AZIMUTHS, 1):
            a, t = np.radians(az), np.radians(RADIAL_TILT_DEG)
            axis = np.array([np.cos(a) * np.cos(t), np.sin(a) * np.cos(t), -np.sin(t)])
            cols.append({"kind": "radial", "fixture_id": m["fixture_id"], "mount_id": m["mount_id"], "zone": z,
                         "azimuth_deg": az, "position": m["position"] - [0, 0, emitter_drop_m], "axis": axis,
                         "hwhm_deg": RADIAL_HWHM_DEG, "max_theta_deg": 180.0, "peak_cd_per_lm": rad_i0})
    return cols


def wall_frame(walls):
    """Room corners (xy) from inward wall planes n.p >= offset, and an interior reference point."""
    corners = []
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            a, b = walls[i], walls[j]
            A = np.array([a["normal_xy"], b["normal_xy"]])
            if abs(np.linalg.det(A)) < 1e-6:
                continue
            c = np.linalg.solve(A, [a["offset"], b["offset"]])
            if all(w["normal_xy"] @ c >= w["offset"] - 1e-6 for w in walls):
                corners.append(c)
    corners = np.array(corners)
    return corners, corners.mean(0)

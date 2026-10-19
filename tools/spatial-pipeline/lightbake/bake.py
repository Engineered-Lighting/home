"""Step 91: path-traced unit light basis, 40 B/receiver encoding, OIDN lightmaps, manifest.

Columns (29): 4 downlights, 24 radials (fixture-major, zones 1..6), 1 window.
Fixture columns are irradiance per lumen of emitter flux (lx/lm) with assumed
Gaussian lobes from home-den-fixtures.js defaults; the window column is the
capture-state daylight through the detected apertures (capture-relative units,
white-balanced; its colour is lighting-manifest.json columns[28].colour_rgb).
The four robotic spots are live (L5) and are not baked.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import binding as BND
from . import encode as ENC
from . import ktx2, scan, tracer
from .delight import dilate
from .estimator import FLAGS, stop_error

SCHEMA = "home-lighting-layer-1"
DISCLOSURE = "Simulated · not photometric · assumed output"
BUDGET_RECEIVER_BYTES = 40
BUDGET_LIGHTMAP_BYTES = 64 * 1024 * 1024
SET_SOURCES = {"splat": "apartment.ply", "splat-mobile": "apartment.mobile.ply", "points": "points.ply"}


def _write(out, name, arr, files, **meta):
    p = out / name
    a = np.ascontiguousarray(arr)
    with open(p, "wb") as f:
        f.write(a.tobytes())
    files[name] = {"bytes": p.stat().st_size, "sha256": BND.sha256_file(p), "dtype": a.dtype.str,
                   "shape": list(a.shape), **meta}


def _write_ktx(out, name, arr, fmt, files, **meta):
    p = out / name
    ktx2.write(p, arr, fmt)
    files[name] = {"bytes": p.stat().st_size, "sha256": BND.sha256_file(p), "format": fmt,
                   "shape": list(np.shape(arr)), **meta}


def run(args):
    t0 = time.time()
    log = lambda *a: print(f"[{time.time() - t0:7.1f}s]", *a, flush=True)  # noqa: E731
    pack = Path(args.pack)
    out = Path(args.out) if args.out else BND.default_layer_dir(pack)
    BND.assert_outside(out, pack)
    work = out / "_work"
    before = BND.snapshot(pack)
    binding, assets, _ = BND.read_binding(pack)
    with open(work / "delight.json", "rb") as f:
        meta90 = json.loads(f.read())
    if meta90["binding"] != binding or meta90["pack_snapshot_after"] != BND.snapshot_digest(before):
        raise SystemExit("step 90 output is for different pack bytes; rerun 90_delight_layer.py")
    z = np.load(work / "delight.npz")
    mounts, walls, eq = scan.read_equipment(args.equipment, binding)
    if eq != meta90["equipment"]:
        raise SystemExit("equipment registration changed since step 90")
    cols = scan.fixture_columns(mounts, args.emitter_drop)
    K = len(cols) + 1
    c_win = z["c_win"].astype(np.float64)
    window = {k: z[f"window_{k}"] for k in ("v0", "v1", "v2", "n", "L")}
    window["L"] = window["L"] / c_win[None, :]  # white-balanced: colour is applied at runtime

    device = tracer.init(args.device)
    mesh = scan.read_glb(pack / "mesh.glb")
    sc = tracer.Scene(device, mesh["positions"], mesh["faces"], mesh["uv"], z["mesh_vn"], z["mesh_fn"],
                      z["glass"].astype(np.int32), np.zeros((4, 4, 3), np.uint8), window=window, fixtures=cols,
                      eps=args.ray_eps)
    alb_u8 = np.round(np.clip(ENC.srgb_oetf(z["mesh_rho"]), 0, 1) * 255).astype(np.uint8)
    del mesh["texture"]
    out.mkdir(parents=True, exist_ok=True)
    files, receivers, metrics = {}, {}, {}
    w_cap = np.zeros(K)
    w_cap[-1] = 1.0
    colours = np.ones((K, 3))
    colours[-1] = c_win

    for name, src in SET_SOURCES.items():
        P, N = z[f"{name}_positions"].astype(np.float64), z[f"{name}_normal"].astype(np.float64)
        rho, flags, R = z[f"{name}_rho"], z[f"{name}_flags"], z[f"{name}_R"]
        ts = time.time()
        B = sc.basis(P, N, alb_u8, spp=args.spp, bounces=args.bounces)
        log(name, "basis", B.shape, "%.1fs" % (time.time() - ts))
        Bs = ENC.lum(B)
        cs = ENC.column_scales(Bs)
        rec = ENC.encode_receivers(rho, N, Bs, B, flags, cs)
        set_files = {}
        for field, arr in rec.items():
            _write(out, f"{name}-{field}.bin", arr, set_files)
        files.update(set_files)
        nbytes = sum(f["bytes"] for f in set_files.values())
        dec = ENC.decode_receivers(rec, cs)
        good = (flags & (FLAGS["emitter"] | FLAGS["off_surface"] | FLAGS["clipped"])) == 0
        se = stop_error(ENC.relight(dec, w_cap, colours), R)
        se_exact = stop_error(rho / np.pi * np.einsum("nkc,kc->nc", B, w_cap[:, None] * colours), R)
        metrics[name] = {"capture_rerender_median_stop": float(np.median(se[good])),
                         "capture_rerender_p90_stop": float(np.percentile(se[good], 90)),
                         "capture_rerender_median_stop_all_receivers": float(np.median(se)),
                         "capture_rerender_median_stop_unencoded": float(np.median(se_exact[good])),
                         "clamp_hit_fraction": float(((flags[good] & FLAGS["unexplained_light"]) > 0).mean()),
                         "evaluated_receivers": int(good.sum())}
        receivers[name] = {"source": src, "source_sha256": assets[src]["sha256"], "count": int(len(P)),
                           "keyed_by": "vertex index in the source PLY", "bytes_per_receiver": nbytes / len(P),
                           "column_scale": cs.tolist(), "files": sorted(set_files),
                           **({"capture_radiance_source": "mesh atlas texel at the closest surface point "
                               "(points.ply carries no colour)"} if name == "points" else {})}
        log(name, json.dumps(metrics[name]))

    # ---------------------------------------------------------- mesh lightmaps
    Lm = args.lightmap_size
    tface, tpos, tnrm = sc.raster(mesh["positions"], Lm)
    valid = tface >= 0
    ts = time.time()
    B = sc.basis(tpos[valid].astype(np.float64), tnrm[valid].astype(np.float64), alb_u8, spp=args.mesh_spp,
                 bounces=args.bounces)
    log("lightmap basis", B.shape, "%.1fs" % (time.time() - ts))
    img = np.zeros((K, Lm, Lm, 3), np.float32)
    img[:, valid] = B.transpose(1, 0, 2)
    denoiser = None
    try:
        from .oidn import Denoiser
        denoiser = Denoiser(args.oidn_dir)
    except Exception as e:
        log("OIDN unavailable:", e)
    grown = valid
    for k in range(K):
        filled, grown = dilate(img[k], valid, 8)
        img[k] = denoiser.lightmap(filled) if denoiser else filled
    img[:, ~grown] = 0
    Bt = img[:, grown].transpose(1, 0, 2)
    Bts = ENC.lum(Bt)
    cs_m = ENC.column_scales(Bts)
    u8, s16 = ENC.encode_basis(Bts, cs_m)
    tint = ENC.encode_tint(ENC.bounce_tint(Bt, cs_m))
    basis_img = np.zeros((K, Lm, Lm, 1), np.uint8)
    basis_img[:, grown, 0] = u8.T
    scale_img = np.zeros((Lm, Lm, 1), np.float16)
    scale_img[grown, 0] = s16
    tint_img = np.zeros((Lm, Lm, 4), np.uint8)
    tint_img[grown, :3] = tint
    tint_img[..., 3] = 255
    mesh_files = {}
    _write_ktx(out, "mesh-basis-u8.ktx2", basis_img, "r8", mesh_files, layers=K)
    _write_ktx(out, "mesh-basis-scale.ktx2", scale_img, "r16f", mesh_files)
    _write_ktx(out, "mesh-bounce-tint.ktx2", tint_img, "rgba8", mesh_files)
    rho_img = z["mesh_rho"]
    S = rho_img.shape[0]
    alb_rgba = np.concatenate([alb_u8, np.full((S, S, 1), 255, np.uint8)], -1)
    _write_ktx(out, "mesh-albedo.ktx2", alb_rgba, "rgba8_srgb", mesh_files)
    _write_ktx(out, "mesh-flags.ktx2", z["mesh_flags"][..., None], "r8", mesh_files)
    _write(out, "mesh-normal-oct.bin", ENC.encode_normal_oct(z["mesh_vn"]), mesh_files,
           keyed_by="vertex index of meshes[0].primitives[0]")
    files.update(mesh_files)
    lightmap_bytes = sum(f["bytes"] for f in mesh_files.values())

    # mesh self-consistency at lightmap resolution
    f = S // Lm
    rho_lm = ENC.decode_albedo(alb_u8).reshape(Lm, f, Lm, f, 3).mean((1, 3))
    R_lm = z["mesh_R"].reshape(Lm, f, Lm, f, 3).mean((1, 3))
    Bdec = ENC.decode_basis(u8, s16, cs_m)
    E = ENC.decode_tint(tint) * (Bdec @ (w_cap[:, None] * colours))
    good = valid[grown] & (z["glass"][np.where(tface >= 0, tface, 0)][grown] == 0)
    flags_lm = z["mesh_flags"].reshape(Lm, f, Lm, f).max((1, 3))[grown]
    good &= (flags_lm & FLAGS["clipped"]) == 0
    se = stop_error(rho_lm[grown] / np.pi * E, R_lm[grown])
    metrics["mesh"] = {"capture_rerender_median_stop": float(np.median(se[good])),
                       "capture_rerender_p90_stop": float(np.percentile(se[good], 90)),
                       "evaluated_texels": int(good.sum()), "lightmap": Lm, "albedo_atlas": S}
    log("mesh", json.dumps(metrics["mesh"]))

    after = BND.snapshot(pack)
    identical = after == before
    budget = {"receiver_bytes": {n: r["bytes_per_receiver"] for n, r in receivers.items()},
              "receiver_limit": BUDGET_RECEIVER_BYTES, "lightmap_bytes": lightmap_bytes,
              "lightmap_limit": BUDGET_LIGHTMAP_BYTES}
    budget["ok"] = all(v <= BUDGET_RECEIVER_BYTES for v in budget["receiver_bytes"].values()) \
        and lightmap_bytes <= BUDGET_LIGHTMAP_BYTES
    columns = []
    for i, c in enumerate(cols):
        columns.append({"index": i, "kind": c["kind"], "fixture_id": c["fixture_id"], "mount_id": c["mount_id"],
                        "zone": c["zone"], "azimuth_deg": c.get("azimuth_deg"), "unit": "lx_per_lm",
                        "profile": {"type": "gaussian_lobe", "hwhm_deg": c["hwhm_deg"], "max_theta_deg": c["max_theta_deg"],
                                    "flux_lm": 1.0, "status": "assumed",
                                    "source": "home-den-fixtures.js defaultFixture (downlight_ring.beam_deg, "
                                              "radials.beam_half_angle_deg, radials.elevation_tilt_deg)"},
                        "emitter_origin": f"mount position (approximate) minus {args.emitter_drop} m (assumed)"})
    columns.append({"index": K - 1, "kind": "window", "unit": "capture_relative_irradiance",
                    "colour_rgb": c_win.tolist(),
                    "note": "weight 1 x colour_rgb reproduces the capture-state daylight; scale down for night ambient"})
    manifest = {
        "schema": SCHEMA, "version": 1, "site_id": "victoria",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "binding": binding,
        "pack_assets": {k: assets[k] for k in ("apartment.ply", "apartment.mobile.ply", "points.ply", "mesh.glb")},
        "load_rule": "refuse unless binding and every pack_assets sha256 match the active pack, "
                     "and every layer file matches files[*].sha256",
        "pack_integrity": {"files": len(before), "before_digest": BND.snapshot_digest(before),
                           "after_digest": BND.snapshot_digest(after), "identical": identical,
                           "step90_identical": meta90["pack_snapshot_before"] == meta90["pack_snapshot_after"]},
        "provenance": {"status": "modeled", "measured": False, "photometry": "assumed", "disclosure": DISCLOSURE,
                       "albedo": "de-lit from capture radiance (gathered capture-state bounce + window emitters); "
                                 "clamped; flagged", "capture_state": meta90["capture_state"]},
        "equipment": eq,
        "columns": columns,
        "encoding": {"receiver_record": "albedo rgb8 sRGB | normal-oct 2xu8 | basis-u8 x29 sqrt | basis-scale fp16 | "
                                        "bounce-tint rgb8 | flags u8 (one file per field)",
                     "decode": "b_k=(u8_k/255)^2*scale*column_scale_k; tint=t/lum(t); E=tint*sum_k w_k*c_k*b_k; "
                               "L=albedo/pi*E",
                     "lightmaps": "mesh-basis-u8 r8 array (29 layers) + r16f scale + rgba8 tint at lightmap size; "
                                  "mesh-albedo rgba8_srgb and mesh-flags r8 at albedo atlas size; UV = TEXCOORD_0",
                     "flags": FLAGS},
        "receivers": receivers,
        "mesh": {"source": "mesh.glb", "source_sha256": assets["mesh.glb"]["sha256"], "lightmap_size": Lm,
                 "albedo_size": S, "column_scale": cs_m.tolist(), "denoiser": denoiser.describe() if denoiser else None},
        "files": files,
        "bake": {"spp_receivers": args.spp, "spp_lightmap": args.mesh_spp, "bounces": args.bounces,
                 "capture_spp": meta90["params"]["capture_spp"], "tracer": "NVIDIA Warp BVH " + tracer.wp.config.version,
                 "device": str(device), "seconds": round(time.time() - t0, 1)},
        "step90": {k: meta90[k] for k in ("apertures", "exterior_splats", "results", "sensitivity",
                                          "mesh_normals_flipped", "window_luminance_median", "denoiser")},
        "metrics": metrics,
        "budget": budget,
    }
    with open(out / "lighting-manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    z.close()
    if not args.keep_work:
        shutil.rmtree(work)
    log("step 91 done; pack bytes identical:", identical, "budget ok:", budget["ok"])
    if not identical:
        raise SystemExit("PACK BYTES CHANGED during step 91")
    return manifest

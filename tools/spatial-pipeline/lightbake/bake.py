"""Step 91: path-traced unit light basis, 40 B/receiver encoding, per-vertex mesh layer, manifest.

Columns (29): 4 downlights, 24 radials (fixture-major, zones 1..6), 1 window.
Fixture columns are irradiance per lumen of emitter flux (lx/lm) with assumed
Gaussian lobes from home-den-fixtures.js defaults; the window column is the
capture-state daylight through the detected openings (capture-relative units,
white-balanced; its colour is lighting-manifest.json columns[28].colour_rgb).
The four robotic spots are live (L5) and are not baked.

Mesh: the Scaniverse atlas packs faces at about half a texel each at 2048^2
(15 % of the atlas area is covered by UV triangles), so an atlas lightmap
cannot represent this scan. The mesh layer is per vertex of the rendered
primitive (~3 cm spacing) and the shader forms the albedo at full atlas
resolution as clamp(pi * atlas / max(E_cap, floor), 0, rho_max).
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
from . import scan, tracer
from .estimator import FLAGS, stop_error

SCHEMA = "home-lighting-layer-1"
DISCLOSURE = "Simulated · not photometric · assumed output"
BUDGET_RECEIVER_BYTES = 40
BUDGET_MESH_BYTES = 64 * 1024 * 1024
SET_SOURCES = {"splat": "apartment.ply", "splat-mobile": "apartment.mobile.ply", "points": "points.ply"}


def _write(out, name, arr, files, **meta):
    p = out / name
    a = np.ascontiguousarray(arr)
    with open(p, "wb") as f:
        f.write(a.tobytes())
    files[name] = {"bytes": p.stat().st_size, "sha256": BND.sha256_file(p), "dtype": a.dtype.str,
                   "shape": list(a.shape), **meta}


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
    floor, rho_max = float(z["floor"]), float(meta90["rho_max"])

    device = tracer.init(args.device)
    mesh = scan.read_glb(pack / "mesh.glb")
    sc = tracer.Scene(device, z["allP"], z["allF"], z["allUV"], z["vn_all"], z["fn_all"], z["glass"].astype(np.int32),
                      mesh["texture"], window=window, fixtures=cols, eps=args.ray_eps, n_main=int(z["n_main"]),
                      aux_radiance=z["cap_rad"])
    sc.set_albedo_ratio(z["E_vertex"], floor, rho_max, aux_albedo=z["cap_alb"])
    out.mkdir(parents=True, exist_ok=True)
    files, receivers, metrics = {}, {}, {}
    w_cap = np.zeros(K)
    w_cap[-1] = 1.0
    colours = np.ones((K, 3))
    colours[-1] = c_win

    for name, src in SET_SOURCES.items():
        P, N = z[f"{name}_Pr"], z[f"{name}_normal"].astype(np.float64)
        rho, flags, R = z[f"{name}_rho"], z[f"{name}_flags"], z[f"{name}_R"]
        ts = time.time()
        B = sc.basis(P, N, spp=args.spp, bounces=args.bounces)
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

    # ---------------------------------------------------------- mesh: per rendered vertex
    vmap = z["render_vmap"]
    Pv = z["allP"][vmap].astype(np.float64)
    Nv = z["render_vn"].astype(np.float64)
    flags_v = z["render_flags"]
    ts = time.time()
    B = sc.basis(Pv, Nv, spp=args.mesh_spp, bounces=args.bounces)
    log("mesh vertices basis", B.shape, "%.1fs" % (time.time() - ts))
    Bs = ENC.lum(B)
    cs_m = ENC.column_scales(Bs)
    u8, s16 = ENC.encode_basis(Bs, cs_m)
    tint = ENC.encode_tint(ENC.bounce_tint(B, cs_m))
    E16 = np.minimum(z["render_E"], ENC.FP16_MAX).astype("<f2")
    mesh_files = {}
    kv = {"keyed_by": "vertex index of meshes[0].primitives[0]"}
    _write(out, "mesh-capture-irradiance.bin", E16, mesh_files, **kv)
    _write(out, "mesh-normal-oct.bin", ENC.encode_normal_oct(Nv), mesh_files, **kv)
    _write(out, "mesh-basis-u8.bin", u8, mesh_files, **kv)
    _write(out, "mesh-basis-scale.bin", s16.astype("<f2"), mesh_files, **kv)
    _write(out, "mesh-bounce-tint.bin", tint, mesh_files, **kv)
    _write(out, "mesh-flags.bin", flags_v, mesh_files, **kv)
    files.update(mesh_files)
    mesh_bytes = sum(f["bytes"] for f in mesh_files.values())

    # mesh self-consistency at atlas resolution, exactly as the shader would evaluate it
    rf = mesh["faces"]
    sf, bw, sR = z["samp_face"], z["samp_bary"], z["samp_R"]
    Ewin_v = ENC.decode_tint(tint) * (ENC.decode_basis(u8, s16, cs_m) @ (w_cap[:, None] * colours))
    Ecap_v = E16.astype(np.float64)
    tri = rf[sf]
    Ecap_s = (Ecap_v[tri] * bw[:, :, None]).sum(1)
    Ewin_s = (Ewin_v[tri] * bw[:, :, None]).sum(1)
    rho_s = np.clip(np.pi * sR / np.maximum(Ecap_s, floor), 0, rho_max)
    ok = ((flags_v[tri] & FLAGS["emitter"]).max(1) == 0) & (sR.max(1) < 1.0)
    se = stop_error(rho_s / np.pi * Ewin_s, sR)
    metrics["mesh"] = {"capture_rerender_median_stop": float(np.median(se[ok])),
                       "capture_rerender_p90_stop": float(np.percentile(se[ok], 90)),
                       "clamp_hit_fraction": float(((np.pi * sR / np.maximum(Ecap_s, floor)) > rho_max).any(1)[ok].mean()),
                       "evaluated_atlas_samples": int(ok.sum()), "vertices": int(len(vmap))}
    log("mesh", json.dumps(metrics["mesh"]))

    after = BND.snapshot(pack)
    identical = after == before
    budget = {"receiver_bytes": {n: r["bytes_per_receiver"] for n, r in receivers.items()},
              "receiver_limit": BUDGET_RECEIVER_BYTES, "mesh_bytes": mesh_bytes, "mesh_limit": BUDGET_MESH_BYTES,
              "mesh_bytes_per_vertex": mesh_bytes / len(vmap)}
    budget["ok"] = all(v <= BUDGET_RECEIVER_BYTES for v in budget["receiver_bytes"].values()) \
        and mesh_bytes <= BUDGET_MESH_BYTES
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
                    "note": "daylight and adjacent-room light through the detected openings at the capture state; "
                            "weight 1 x colour_rgb reproduces it; scale down for a night ambient"})
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
                     "mesh_record": "capture-irradiance fp16x3 | normal-oct 2xu8 | basis-u8 x29 | basis-scale fp16 | "
                                    "bounce-tint rgb8 | flags u8, per vertex of meshes[0].primitives[0]",
                     "mesh_albedo": "rho = clamp(pi * srgb_to_linear(atlas(uv)) / max(E_cap(interpolated), floor), "
                                    "0, rho_max) per fragment",
                     "flags": FLAGS},
        "receivers": receivers,
        "mesh": {"source": "mesh.glb", "source_sha256": assets["mesh.glb"]["sha256"], "vertices": int(len(vmap)),
                 "irradiance_floor": floor, "rho_max": rho_max, "column_scale": cs_m.tolist(),
                 "trace_geometry": meta90["trace_geometry"], "ceiling_cap": meta90["ceiling_cap"],
                 "files": sorted(mesh_files),
                 "why_per_vertex": "the atlas covers 15 % of its area with UV triangles of ~0.5 texel at 2048^2; "
                                   "an atlas lightmap cannot represent this scan"},
        "files": files,
        "bake": {"spp_receivers": args.spp, "spp_mesh_vertices": args.mesh_spp, "bounces": args.bounces,
                 "capture_spp": meta90["params"]["capture_spp"], "tracer": "NVIDIA Warp BVH " + tracer.wp.config.version,
                 "device": str(device), "seconds": round(time.time() - t0, 1)},
        "step90": {k: meta90[k] for k in ("apertures", "exterior_splats", "results", "sensitivity",
                                          "mesh_normals_flipped", "irradiance_floor")},
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

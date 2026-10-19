"""Step 90: capture-state de-light of the accepted pack into an additive layer.

1. mesh normals (the GLB ships none) and normals projected to splats/points;
2. window area emitters from the exterior splats seen through the openings;
3. capture-state irradiance per receiver = gather of the captured radiance
   (all bounce orders) + window emitters + any confirmed room lamps / sun;
4. clamped albedo rho = pi R / max(E, eps) with flags;
5. sensitivity of the result to the unanswered capture questions (Q1-Q4).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import binding as BND
from . import encode as ENC
from . import scan, tracer
from .estimator import FLAGS, neighbor_fill, recover_albedo

SETS = (("splat", "apartment.ply"), ("splat-mobile", "apartment.mobile.ply"), ("points", "points.ply"))


def cct_rgb(T):
    from .synthetic import cct_rgb as f
    return f(T)


# ------------------------------------------------------------------ windows
def detect_windows(P, alpha, R, dist, walls, ceiling, cell=0.05, margin=0.25, min_alpha=0.5, min_area=0.04,
                   inset=0.03, z_ref=1.2):
    """Window apertures and their radiance from exterior splats (projected through the walls)."""
    corners, center = scan.wall_frame(walls)
    n = np.array([w["normal_xy"] for w in walls])
    off = np.array([w["offset"] for w in walls])
    sd = P[:, :2] @ n.T - off
    ext = (sd.min(1) < -margin) & (dist > 0.10) & (P[:, 2] > -0.2) & (P[:, 2] < ceiling + 0.3)
    O = np.array([center[0], center[1], z_ref])
    E = P[ext].astype(np.float64)
    dv = E - O
    den = dv[:, :2] @ n.T
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(den < -1e-9, (off - O[:2] @ n.T) / den, np.inf)
    j = np.argmin(t, 1)
    tt = t[np.arange(len(j)), j]
    X = O + tt[:, None] * dv
    walls_out, quads, masks = [], {"v0": [], "v1": [], "v2": [], "n": [], "L": []}, {}
    a_ext, R_ext = alpha[ext], R[ext]
    for wi, w in enumerate(walls):
        tan = np.array([-w["normal_xy"][1], w["normal_xy"][0]])
        on = np.abs(corners @ w["normal_xy"] - w["offset"]) < 1e-6
        if on.sum() < 2:
            continue
        u0, u1 = (corners[on] @ tan).min(), (corners[on] @ tan).max()
        nu, nz = int(math.ceil((u1 - u0) / cell)), int(math.ceil(ceiling / cell))
        sel = (j == wi) & np.isfinite(tt)
        iu = np.floor((X[sel, :2] @ tan - u0) / cell).astype(int)
        iz = np.floor(X[sel, 2] / cell).astype(int)
        ok = (iu >= 0) & (iu < nu) & (iz >= 0) & (iz < nz)
        wsum = np.zeros((nz, nu))
        rsum = np.zeros((nz, nu, 3))
        np.add.at(wsum, (iz[ok], iu[ok]), a_ext[sel][ok])
        np.add.at(rsum, (iz[ok], iu[ok]), a_ext[sel][ok][:, None] * R_ext[sel][ok])
        mask = ndimage.binary_closing(wsum >= min_alpha, np.ones((3, 3)), iterations=2)
        mask = ndimage.binary_fill_holes(mask)
        lab, nl = ndimage.label(mask)
        if nl:
            sizes = ndimage.sum(mask, lab, range(1, nl + 1))
            keep = np.isin(lab, 1 + np.nonzero(sizes * cell * cell >= min_area)[0])
            mask &= keep
        if not mask.any():
            continue
        valid = wsum > 0
        rad = np.zeros_like(rsum)
        rad[valid] = rsum[valid] / wsum[valid, None]
        idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        rad = rad[idx[0], idx[1]]
        masks[w["id"]] = {"mask": mask, "u0": u0, "tan": tan, "cell": cell, "normal": w["normal_xy"], "offset": w["offset"]}
        lab, nl = ndimage.label(mask)
        for c in range(1, nl + 1):
            m = lab == c
            walls_out.append({"wall_id": w["id"], "wall_label": w.get("label", ""), "area_m2": float(m.sum() * cell * cell),
                              "mean_luminance": float(ENC.lum(rad[m]).mean()),
                              "clipped_input_fraction": None})
        zz, uu = np.nonzero(mask)
        nrm3 = np.array([w["normal_xy"][0], w["normal_xy"][1], 0.0])
        base = w["offset"] * nrm3 + inset * nrm3

        def pt(u, z):
            return base + np.array([tan[0], tan[1], 0.0]) * (u0 + u * cell)[:, None] + np.array([0, 0, 1.0]) * (z * cell)[:, None]
        p00, p10, p01, p11 = pt(uu, zz), pt(uu + 1, zz), pt(uu, zz + 1), pt(uu + 1, zz + 1)
        L = rad[zz, uu]
        for a, b, c_ in ((p00, p10, p11), (p00, p11, p01)):
            quads["v0"].append(a); quads["v1"].append(b); quads["v2"].append(c_)
            quads["n"].append(np.repeat(nrm3[None], len(a), 0)); quads["L"].append(L)
    window = {k: (np.concatenate(v) if v else np.zeros((0, 3))) for k, v in quads.items()}
    return window, walls_out, masks, int(ext.sum())


def in_aperture(P, masks, band=0.15):
    """Receivers (or face centroids) within `band` of a wall plane inside a detected aperture."""
    hit = np.zeros(len(P), bool)
    for m in masks.values():
        sd = P[:, :2] @ m["normal"] - m["offset"]
        near = np.abs(sd) < band
        iu = np.floor((P[:, :2] @ m["tan"] - m["u0"]) / m["cell"]).astype(int)
        iz = np.floor(P[:, 2] / m["cell"]).astype(int)
        H, W = m["mask"].shape
        ok = near & (iu >= 0) & (iu < W) & (iz >= 0) & (iz < H)
        hit[ok] |= m["mask"][iz[ok], iu[ok]]
    return hit


# ------------------------------------------------------------------ texture helpers
def linear_downsample(tex, size, lut):
    """8-bit sRGB atlas -> linear (size, size, 3) box average, plus per-texel clipped fraction."""
    H = tex.shape[0]
    f = H // size
    out = np.zeros((size, size, 3), np.float32)
    clip = np.zeros((size, size), np.float32)
    band = max(1, 256 // f)
    for r in range(0, size, band):
        blk = tex[r * f:(r + band) * f]
        lin = lut[blk]
        rows = blk.shape[0] // f
        out[r:r + rows] = lin.reshape(rows, f, size, f, 3).mean((1, 3))
        clip[r:r + rows] = (blk.max(2) >= 255).reshape(rows, f, size, f).mean((1, 3))
    return out, clip


def dilate(img, valid, iterations=8):
    img = img.copy()
    valid = valid.copy()
    for _ in range(iterations):
        if valid.all():
            break
        w = ndimage.uniform_filter(valid.astype(np.float32), 3, mode="nearest")
        s = np.stack([ndimage.uniform_filter(img[..., c] * valid, 3, mode="nearest") for c in range(img.shape[-1])], -1)
        grow = (~valid) & (w > 0)
        img[grow] = s[grow] / w[grow, None]
        valid = valid | grow
    return img, valid


# ------------------------------------------------------------------ sun (sensitivity only)
def sun_direction(walls, azimuth_deg, elevation_deg, wall_id, wall_bearing_deg):
    """Frame direction toward the sun from one wall's true-north outward bearing."""
    w = next(w for w in walls if w["id"] == wall_id)
    out = -w["normal_xy"]
    theta = math.atan2(out[1], out[0])
    phi = theta - math.radians(azimuth_deg - wall_bearing_deg)
    el = math.radians(elevation_deg)
    return np.array([math.cos(el) * math.cos(phi), math.cos(el) * math.sin(phi), math.sin(el)])


def bearing_check(walls, bearings, ref_id):
    """Angle between each wall's outward normal and its bearing, relative to the reference wall."""
    out = {}
    ref = next(w for w in walls if w["id"] == ref_id)
    th0 = math.atan2(-ref["normal_xy"][1], -ref["normal_xy"][0])
    for w in walls:
        if w["id"] not in bearings:
            continue
        th = math.atan2(-w["normal_xy"][1], -w["normal_xy"][0])
        pred = th0 - math.radians(bearings[w["id"]] - bearings[ref_id])
        out[w["id"]] = round(math.degrees((th - pred + math.pi) % (2 * math.pi) - math.pi), 1)
    return out


# ------------------------------------------------------------------ main
def run(args):
    t0 = time.time()
    pack = Path(args.pack)
    out = Path(args.out) if args.out else BND.default_layer_dir(pack)
    BND.assert_outside(out, pack)
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)
    before = BND.snapshot(pack)
    binding, assets, manifest = BND.read_binding(pack)
    mounts, walls, eq = scan.read_equipment(args.equipment, binding)
    prov = pack.parent / (pack.name + ".provenance.json")
    ceiling = 2.4
    if prov.exists():
        with open(prov, "rb") as f:
            ceiling = float(json.loads(f.read())["interior"]["scan"].get("ceilingM", ceiling))
    log = lambda *a: print(f"[{time.time() - t0:7.1f}s]", *a, flush=True)  # noqa: E731

    device = tracer.init(args.device)
    lut = tracer.srgb_lut()
    mesh = scan.read_glb(pack / "mesh.glb")
    fn, vn, flipped = scan.mesh_normals(mesh["positions"], mesh["faces"])
    log("mesh", len(mesh["positions"]), "verts", len(mesh["faces"]), "faces; flipped:", flipped)
    sc = tracer.Scene(device, mesh["positions"], mesh["faces"], mesh["uv"], vn, fn,
                      np.zeros(len(mesh["faces"]), np.int32), mesh["texture"], eps=args.ray_eps)

    sets = {}
    for name, fname in SETS:
        if name == "points":
            d = scan.read_points(pack / fname)
        else:
            d = scan.read_splats(pack / fname)
        face, dist, nrm, uvo = sc.closest(d["positions"], max_dist=1.0)
        d.update(face=face, dist=dist, normal=nrm, uv=uvo)
        sets[name] = d
        log(name, len(d["positions"]), "receivers projected; median distance %.3f m" % float(np.median(dist)))

    # windows from the exterior splats of the full splat set
    s = sets["splat"]
    R_splat = ENC.srgb_eotf(np.clip(s["dc"], 0, 1))
    window, apertures, masks, n_ext = detect_windows(s["positions"].astype(np.float64), s["alpha"], R_splat, s["dist"],
                                                    walls, ceiling, cell=args.window_cell)
    window["L"] = window["L"] * args.window_gain
    cent = mesh["positions"][mesh["faces"]].mean(1)
    glass = in_aperture(cent, masks)
    face_facing = np.zeros(len(fn), bool)
    for m in masks.values():
        face_facing |= np.abs(fn[:, :2] @ m["normal"]) > 0.6
    glass &= face_facing
    sc.glass = tracer.wp.array(glass.astype(np.int32), dtype=tracer.wp.int32, device=device)
    sc.set_window(window)
    yL = ENC.lum(window["L"]) if len(window["L"]) else np.zeros(1)
    c_win = (window["L"].mean(0) / max(ENC.lum(window["L"].mean(0)), 1e-12)) if len(window["L"]) else np.ones(3)
    log("exterior splats", n_ext, "apertures", len(apertures), "window tris", len(window["L"]), "glass faces", int(glass.sum()))

    lamps = []
    if args.room_lamps:
        with open(args.room_lamps, "rb") as f:
            lamps = [{"position": np.array(l["position"], float), "intensity_rgb": np.array(l["intensity_rgb"], float),
                      "label": l.get("label", "")} for l in json.loads(f.read())]

    results, sens = {}, {}
    for name, d in sets.items():
        P = d["positions"].astype(np.float64)
        on = d["dist"] < args.surface_tol
        Pr = P.copy()
        # receivers on the scan surface start from the projected surface point
        Nn = d["normal"].astype(np.float64)
        if name == "points":
            tex = mesh["texture"]
            H, W = tex.shape[:2]
            x = np.clip((d["uv"][:, 0] * W).astype(int), 0, W - 1)
            y = np.clip((d["uv"][:, 1] * H).astype(int), 0, H - 1)
            px = tex[y, x]
            R = lut[px].astype(np.float64)
            clipped = px.max(1) >= 255
        else:
            R = ENC.srgb_eotf(np.clip(d["dc"], 0, 1))
            clipped = d["dc"].max(1) >= 254.5 / 255
        Eb, Ew, esc = sc.gather_capture(Pr, Nn, spp=args.capture_spp)
        E_lamp = sc.point_lamps(Pr, Nn, lamps)
        E_cap = Eb + Ew + E_lamp
        rho, flags, st = recover_albedo(R, E_cap, eps_rel=args.eps_rel, rho_max=args.rho_max, clipped=clipped)
        exterior = (Pr[:, :2] @ np.array([w["normal_xy"] for w in walls]).T
                    - np.array([w["offset"] for w in walls])).min(1) < -0.10
        emitter = exterior | in_aperture(Pr, masks)
        flags[emitter] |= FLAGS["emitter"]
        flags[~on] |= FLAGS["off_surface"]
        flags[esc > 0.3] |= FLAGS["open_hemisphere"]
        rho, flags = neighbor_fill(rho, flags, Pr, Nn, clipped & on & ~emitter)
        d.update(R=R, E_cap=E_cap, Eb=Eb, Ew=Ew, rho=rho, flags=flags, clipped=clipped, emitter=emitter, esc=esc)
        good = on & ~emitter
        results[name] = {"count": int(len(P)), "on_surface_fraction": float(on.mean()),
                         "emitter_fraction": float(emitter.mean()), "clipped_fraction": float(clipped.mean()),
                         "clamp_hit_fraction": float((flags[good] & FLAGS["unexplained_light"] > 0).mean()),
                         "clamp_hit_fraction_all": st["clamp_hit_fraction"],
                         "low_irradiance_fraction": st["low_irradiance_fraction"],
                         "open_hemisphere_fraction": float((esc > 0.3).mean()),
                         "window_share_of_capture_irradiance_median": float(np.median(ENC.lum(Ew[good]) / np.maximum(ENC.lum(E_cap[good]), 1e-12))),
                         "albedo_luminance_median": float(np.median(ENC.lum(rho[good]))),
                         "albedo_luminance_p95": float(np.percentile(ENC.lum(rho[good]), 95))}
        log(name, json.dumps(results[name]))
        if name == "splat":
            sens = sensitivity(sc, d, good, walls, ceiling, args, R, Eb, Ew, E_lamp, rho)
            log("sensitivity", json.dumps(sens))

    # mesh: capture irradiance on the atlas, albedo at mesh_albedo_size
    S = args.mesh_albedo_size
    R_tex, clip_tex = linear_downsample(mesh["texture"], S, lut)
    tface, tpos, tnrm = sc.raster(mesh["positions"], S)
    valid = tface >= 0
    Pt, Nt = tpos[valid].astype(np.float64), tnrm[valid].astype(np.float64)
    Eb, Ew, esc = sc.gather_capture(Pt, Nt, spp=args.capture_spp_mesh)
    E_img = np.zeros((S, S, 3), np.float32)
    E_img[valid] = Eb + Ew + sc.point_lamps(Pt, Nt, lamps)
    denoiser = None
    try:
        from .oidn import Denoiser
        denoiser = Denoiser(args.oidn_dir)
        E_fill, _ = dilate(E_img, valid, 16)
        E_img = np.where(valid[..., None], denoiser.lightmap(E_fill), 0).astype(np.float32)
    except Exception as e:  # recorded in the manifest; the bake still completes
        log("OIDN unavailable for the capture lightmap:", e)
    rho_t, flags_t, st_t = recover_albedo(R_tex[valid], E_img[valid], eps_rel=args.eps_rel, rho_max=args.rho_max,
                                          clipped=clip_tex[valid] > 0)
    glass_t = glass[tface[valid]]
    flags_t[glass_t] |= FLAGS["emitter"]
    rho_img = np.zeros((S, S, 3), np.float32)
    rho_img[valid] = rho_t
    rho_img, filled = dilate(rho_img, valid, 8)
    flag_img = np.zeros((S, S), np.uint8)
    flag_img[valid] = flags_t
    results["mesh"] = {"atlas": S, "texel_coverage": float(valid.mean()),
                       "clamp_hit_fraction": float(((flags_t & FLAGS["unexplained_light"]) > 0)[~glass_t].mean()),
                       "glass_texel_fraction": float(glass_t.mean()),
                       "albedo_luminance_median": float(np.median(ENC.lum(rho_t[~glass_t])))}
    log("mesh", json.dumps(results["mesh"]))

    after = BND.snapshot(pack)
    if after != before:
        raise SystemExit("PACK BYTES CHANGED during step 90 - aborting")
    np.savez(work / "delight.npz",
             **{f"{n}_{k}": sets[n][k] for n in sets for k in ("positions", "normal", "dist", "rho", "flags", "R", "E_cap")},
             mesh_rho=rho_img, mesh_flags=flag_img, mesh_valid=valid, mesh_R=R_tex, mesh_vn=vn, mesh_fn=fn,
             glass=glass, **{f"window_{k}": v for k, v in window.items()}, c_win=c_win)
    meta = {"stage": "delit", "binding": binding, "pack_assets": assets, "pack_snapshot_before": BND.snapshot_digest(before),
            "pack_snapshot_after": BND.snapshot_digest(after), "pack_files": len(before),
            "equipment": eq, "mesh_normals_flipped": flipped, "ceiling_m": ceiling,
            "apertures": apertures, "exterior_splats": n_ext, "window_colour_rgb": c_win.tolist(),
            "window_luminance_median": float(np.median(yL)), "results": results, "sensitivity": sens,
            "denoiser": denoiser.describe() if denoiser else None,
            "capture_state": capture_state(args, lamps), "params": {k: (str(v) if isinstance(v, Path) else v)
                                                                     for k, v in vars(args).items()}}
    with open(work / "delight.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    log("step 90 done; pack bytes identical (%d files)" % len(before))
    return meta


def capture_state(args, lamps):
    return {
        "Q1_room_lamps": {"answer": "unknown", "assumed": "off" if not lamps else "as given", "count": len(lamps)},
        "Q1_blinds": {"answer": "unknown", "assumed": args.blinds,
                      "note": "apertures are detected from the exterior splats actually seen; recorded only"},
        "Q2_capture_time": {"answer": "unknown", "assumed": args.capture_time or "unknown",
                            "note": "only used to place the sensitivity sun"},
        "Q3_sky": {"answer": "unknown", "assumed": args.sky,
                   "note": "no direct-sun term in the baseline; see sensitivity.sun_*"},
        "Q4_glass_door_wall": {"answer": "wall A per the equipment wall labels", "assumed": args.glass_door_wall},
        "window_gain": args.window_gain,
        "window_model": "Lambertian area emitters on detected apertures; per-cell radiance = alpha-weighted mean of "
                        "exterior splat colours (sRGB-decoded); exterior clipping (~9 %) makes this a lower bound",
    }


def sensitivity(sc, d, good, walls, ceiling, args, R, Eb, Ew, E_lamp, rho0):
    """How much the albedo moves under each unanswered capture-state assumption."""
    P, N = d["positions"].astype(np.float64), d["normal"].astype(np.float64)
    base_E = Eb + Ew + E_lamp
    med = float(np.median(ENC.lum(base_E[good])))
    y0 = ENC.lum(rho0[good])

    def score(E):
        rho, flags, st = recover_albedo(R, E, eps_rel=args.eps_rel, rho_max=args.rho_max)
        dy = np.abs(np.log2(np.maximum(ENC.lum(rho[good]), 1e-6) / np.maximum(y0, 1e-6)))
        return {"median_abs_albedo_change_stop": float(np.median(dy)), "fraction_over_quarter_stop": float((dy > 0.25).mean()),
                "clamp_hit_fraction": float(((flags[good] & FLAGS["unexplained_light"]) > 0).mean())}

    out = {"window_gain_x0.5 (Q1 blinds / exterior exposure)": score(Eb + 0.5 * Ew + E_lamp),
           "window_gain_x2 (exterior clipping)": score(Eb + 2.0 * Ew + E_lamp)}
    corners, center = scan.wall_frame(walls)
    lamp_pos = np.array([center[0], center[1], ceiling - 0.05])
    unit = sc.point_lamps(P, N, [{"position": lamp_pos, "intensity_rgb": cct_rgb(2700)}])
    k = 0.5 * med / max(float(np.median(ENC.lum(unit[good]))), 1e-12)
    out["ceiling_lamp_on (Q1, explains ~1/3 of median light)"] = score(base_E + k * unit)
    bearings = parse_pairs(args.wall_bearings)
    if bearings and args.sun:
        ref = next(iter(bearings))
        for label, spec in parse_pairs(args.sun, str).items():
            az, el = map(float, spec.split("/"))
            to_sun = sun_direction(walls, az, el, ref, bearings[ref])
            cosv = sc.sun_cos(P, N, to_sun)
            Esun = (5.0 * med) * cosv[:, None] * cct_rgb(5300)[None, :]
            r = score(base_E + Esun)
            r["receivers_in_sun_fraction"] = float((cosv[good] > 0).mean())
            out[f"sun_{label} (Q2/Q3, direct sun at 5x median irradiance)"] = r
        out["bearing_consistency_deg"] = bearing_check(walls, bearings, ref)
    return out


def parse_pairs(text, cast=float):
    """'a=1,b=2' -> {'a': 1.0, 'b': 2.0} (insertion order kept; the first key is the reference)."""
    out = {}
    for item in (text or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = cast(v.strip())
    return out

"""Step 90: capture-state de-light of the accepted pack into an additive layer.

1. mesh normals (the GLB ships none) and normals projected to splats/points;
   rays are traced against the uncut scan (the rendered primitive may be cut
   for display) plus a ceiling cap built from the ceiling splats, because the
   Scaniverse mesh has no ceiling;
2. window area emitters from the exterior splats seen through the openings;
3. capture-state irradiance per receiver = gather of the captured radiance
   (all bounce orders) + window emitters + any confirmed room lamps / sun;
4. clamped albedo rho = pi R / max(E, eps) with flags; for the mesh the
   per-vertex capture irradiance is stored and the shader forms
   rho = clamp(pi * atlas / max(E_vertex, floor)) at full atlas resolution
   (the Scaniverse atlas packs faces at about half a texel each at 2048^2,
   so a lightmap or downsampled albedo atlas cannot represent it);
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
def detect_windows(P, alpha, R, dist, walls, ceiling, cell=0.05, margin=0.25, min_alpha=0.5, min_area=0.10,
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


# ------------------------------------------------------------------ ceiling cap
def ceiling_cap(P, alpha, R, walls, ceiling, cell=0.05, below=0.30, above=0.40):
    """A horizontal cap at the ceiling height with radiance from the ceiling-zone splats.

    The Scaniverse mesh has no ceiling, and a display-cut pack may drop the
    ceiling splats too. Two triangles cover the room's bounding box (normal -z);
    the radiance grid is their texture. Cells no splat observed are modeled
    later (see model_ceiling).
    """
    corners, _ = scan.wall_frame(walls)
    n = np.array([w["normal_xy"] for w in walls])
    off = np.array([w["offset"] for w in walls])
    lo = corners.min(0) - cell
    W, H = np.ceil((corners.max(0) + cell - lo) / cell).astype(int)
    sel = (P[:, 2] > ceiling - below) & (P[:, 2] < ceiling + above) & ((P[:, :2] @ n.T - off).min(1) > -0.05)
    ix = np.clip(((P[sel, 0] - lo[0]) / cell).astype(int), 0, W - 1)
    iy = np.clip(((P[sel, 1] - lo[1]) / cell).astype(int), 0, H - 1)
    wsum = np.zeros((H, W))
    rsum = np.zeros((H, W, 3))
    np.add.at(wsum, (iy, ix), alpha[sel])
    np.add.at(rsum, (iy, ix), alpha[sel][:, None] * R[sel])
    gx, gy = np.meshgrid(lo[0] + (np.arange(W) + 0.5) * cell, lo[1] + (np.arange(H) + 0.5) * cell)
    centres = np.stack([gx, gy], -1)
    inside = ((centres @ n.T) - off).min(-1) > 0
    valid = wsum > 1e-6
    rad = np.zeros((H, W, 3))
    if valid.any():
        rad[valid] = rsum[valid] / wsum[valid, None]
        idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        rad = rad[idx[0], idx[1]]
    x0, y0 = lo
    x1, y1 = lo + np.array([W, H]) * cell
    verts = np.array([[x0, y0, ceiling], [x0, y1, ceiling], [x1, y1, ceiling], [x1, y0, ceiling]], np.float32)
    faces = np.array([[0, 1, 2], [0, 2, 3]], np.int32)  # normal -z
    uv = np.array([[0, 0], [0, 1], [1, 1], [1, 0]], np.float32)
    centres3 = np.concatenate([centres, np.full(centres.shape[:2] + (1,), ceiling)], -1)
    return {"verts": verts, "faces": faces, "uv": uv, "radiance": rad, "inside": inside, "lo": lo, "cell": cell,
            "centres": centres3, "observed": valid,
            "stats": {"splats": int(sel.sum()), "cells_inside": int(inside.sum()),
                      "observed_fraction_inside": float(valid[inside].mean()) if inside.any() else 0.0}}


def _u8(lin):
    return np.round(np.clip(ENC.srgb_oetf(lin), 0, 1) * 255).astype(np.uint8)


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
    tp, tf, tuv = mesh["trace_positions"], mesh["trace_faces"], mesh["trace_uv"]
    fn_t, vn_t, flipped = scan.mesh_normals(tp, tf)
    log("render mesh", len(mesh["faces"]), "faces; trace geometry:", mesh["trace_source"]["primitive"],
        len(tf), "faces; flipped:", flipped)

    splats = scan.read_splats(pack / "apartment.ply")
    R_splat = ENC.srgb_eotf(np.clip(splats["dc"], 0, 1))
    cap = ceiling_cap(splats["positions"].astype(np.float64), splats["alpha"], R_splat, walls, ceiling)
    nT = len(tp)
    allP = np.concatenate([tp, cap["verts"]])
    allF = np.concatenate([tf, cap["faces"] + nT])
    allUV = np.concatenate([tuv, cap["uv"]])
    fn_all = np.concatenate([fn_t, np.tile([[0, 0, -1.0]], (2, 1))]).astype(np.float32)
    vn_all = np.concatenate([vn_t, np.tile([[0, 0, -1.0]], (4, 1))]).astype(np.float32)
    sc = tracer.Scene(device, allP, allF, allUV, vn_all, fn_all, np.zeros(len(allF), np.int32), mesh["texture"],
                      eps=args.ray_eps, n_main=len(tf), aux_radiance=cap["radiance"])

    sets = {}
    for name, fname in SETS:
        if name == "points":
            d = scan.read_points(pack / fname)
        else:
            d = splats if name == "splat" else scan.read_splats(pack / fname)
        face, dist, nrm, uvo, cp = sc.closest(d["positions"], max_dist=1.0)
        d.update(face=face, dist=dist, normal=nrm, uv=uvo, cp=cp)
        sets[name] = d
        log(name, len(d["positions"]), "receivers projected; median distance %.3f m" % float(np.median(dist)))

    s = sets["splat"]
    window, apertures, masks, n_ext = detect_windows(s["positions"].astype(np.float64), s["alpha"], R_splat,
                                                    s["dist"], walls, ceiling, cell=args.window_cell)
    L_measured = window["L"].copy()
    glass = in_aperture(allP[allF].mean(1), masks)
    facing = np.zeros(len(allF), bool)
    for m in masks.values():
        facing |= np.abs(fn_all[:, :2] @ m["normal"]) > 0.6
    glass &= facing
    glass[len(tf):] = False
    sc.set_glass(glass)
    c_win = (L_measured.mean(0) / max(ENC.lum(L_measured.mean(0)), 1e-12)) if len(L_measured) else np.ones(3)
    log("exterior splats", n_ext, "apertures", len(apertures), "window tris", len(window["L"]),
        "glass faces", int(glass.sum()))

    lamps = []
    if args.room_lamps:
        with open(args.room_lamps, "rb") as f:
            lamps = [{"position": np.array(l["position"], float), "intensity_rgb": np.array(l["intensity_rgb"], float),
                      "label": l.get("label", "")} for l in json.loads(f.read())]

    def capture(P, N):
        Eb, Ew, esc, back = sc.gather_capture(P, N, spp=args.capture_spp)
        return Eb, Ew, sc.point_lamps(P, N, lamps), esc, back

    # Ceiling: observed cells keep the splat radiance; unobserved cells are modeled. A flat ceiling
    # does not see itself, so its irradiance is a gather of measured surfaces and the windows only,
    # and L = rho_ceiling / pi * E in closed form (rho_ceiling assumed, recorded, sensitivity-tested).
    modeled = cap["inside"] & ~cap["observed"]
    obs = cap["inside"] & cap["observed"]
    cc = cap["centres"][cap["inside"]] - [0, 0, 0.005]

    def model_ceiling(albedo):
        Ebc, Ewc, Elc, _, _ = capture(cc, np.tile([[0, 0, -1.0]], (len(cc), 1)))
        E_c = np.zeros_like(cap["radiance"])
        E_c[cap["inside"]] = Ebc + Ewc + Elc
        rad = cap["radiance"].copy()
        rad[modeled] = albedo / np.pi * E_c[modeled]
        floor_c = args.eps_rel * float(np.median(ENC.lum(E_c[cap["inside"]]))) if cap["inside"].any() else 1e-6
        rho = np.full_like(rad, albedo)
        rho[obs] = np.clip(np.pi * rad[obs] / np.maximum(E_c[obs], floor_c), 0, args.rho_max)
        if cap["inside"].any():  # pad the quad outside the room from the nearest inside cell
            idx = ndimage.distance_transform_edt(~cap["inside"], return_distances=False, return_indices=True)
            rad, rho = rad[idx[0], idx[1]], rho[idx[0], idx[1]]
        sc.set_aux(rad, _u8(rho))
        return rad, rho

    # Window gain. The exterior seen through the glass was clipped and tone-compressed by the phone's
    # auto exposure, so exterior-splat radiance is a lower bound. "auto" fits one global gain >= 1 with
    # a white-patch prior: the brightest (100 - pct) % of unclipped, non-emitter splat surfaces have
    # albedo `white_patch_albedo`. The gray-card capture (L7) replaces this.
    sp = sets["splat"]
    on_s = sp["dist"] < args.surface_tol
    Pr_s = np.where(on_s[:, None], sp["cp"].astype(np.float64), sp["positions"].astype(np.float64))
    N_s = sp["normal"].astype(np.float64)
    ext_s = ((Pr_s[:, :2] @ np.array([w["normal_xy"] for w in walls]).T)
             - np.array([w["offset"] for w in walls])).min(1) < -0.10
    fit = on_s & ~(ext_s | in_aperture(Pr_s, masks)) & (sp["dc"].max(1) < 254.5 / 255)
    auto = str(args.window_gain).lower() == "auto"
    gain = 1.0 if auto else float(args.window_gain)
    gain_trace = []
    for it in range(4 if auto and len(L_measured) else 1):
        window["L"] = L_measured * gain
        sc.set_window(window)
        rad_cap, rho_cap = model_ceiling(args.ceiling_albedo)
        if not auto or not len(L_measured):
            break
        Eb, Ew, El, _, _ = capture(Pr_s[fit], N_s[fit])
        piR = np.pi * ENC.lum(R_splat[fit])

        def p_hi(gg):
            return float(np.percentile(piR / np.maximum(ENC.lum(Eb + (gg / gain) * Ew + El), 1e-9),
                                       args.white_patch_percentile))
        lo_g, hi_g = 1.0, 1e4
        if p_hi(lo_g) <= args.white_patch_albedo:
            new = 1.0
        else:
            for _ in range(60):
                mid = math.sqrt(lo_g * hi_g)
                lo_g, hi_g = (mid, hi_g) if p_hi(mid) > args.white_patch_albedo else (lo_g, mid)
            new = hi_g
        gain_trace.append(round(new, 4))
        converged = abs(math.log(new / gain)) < 0.01
        gain = new
        if converged:
            break
    if auto and len(L_measured):
        window["L"] = L_measured * gain
        sc.set_window(window)
        rad_cap, rho_cap = model_ceiling(args.ceiling_albedo)
    log("window gain", "fitted" if auto else "given", round(gain, 3), gain_trace)
    cap["stats"].update(modeled_fraction_inside=float(modeled[cap["inside"]].mean()) if cap["inside"].any() else 0.0,
                        modeled_albedo=args.ceiling_albedo,
                        radiance_luminance_median=float(np.median(ENC.lum(rad_cap[cap["inside"]])))
                        if cap["inside"].any() else 0.0)
    log("ceiling", json.dumps(cap["stats"]))

    # Mesh vertices first: their median sets the eps floor shared by every receiver and the shader.
    Ebv, Ewv, Elv, esc_v, back_v = capture(tp.astype(np.float64), vn_t.astype(np.float64))
    E_v = Ebv + Ewv + Elv
    vert_emitter = in_aperture(tp.astype(np.float64), masks)
    yv = ENC.lum(E_v[~vert_emitter])
    floor = args.eps_rel * float(np.median(yv[yv > 0]))
    log("mesh vertices: capture irradiance median %.4f, floor %.5f" % (float(np.median(yv)), floor))


    results, sens = {}, {}
    nwall = np.array([w["normal_xy"] for w in walls])
    owall = np.array([w["offset"] for w in walls])
    tex = mesh["texture"]
    TH, TW = tex.shape[:2]
    for name, d in sets.items():
        P = d["positions"].astype(np.float64)
        on = d["dist"] < args.surface_tol
        Pr = np.where(on[:, None], d["cp"].astype(np.float64), P)
        Nn = d["normal"].astype(np.float64)
        if name == "points":
            px = tex[np.clip((d["uv"][:, 1] * TH).astype(int), 0, TH - 1),
                     np.clip((d["uv"][:, 0] * TW).astype(int), 0, TW - 1)]
            R = lut[px].astype(np.float64)
            on_cap = d["face"] >= len(tf)
            if on_cap.any():  # points on the ceiling cap take the cap radiance
                c = ((d["cp"][on_cap, :2] - cap["lo"]) / cap["cell"]).astype(int)
                c = np.clip(c, 0, np.array(cap["radiance"].shape[1::-1]) - 1)
                R[on_cap] = cap["radiance"][c[:, 1], c[:, 0]]
            clipped = px.max(1) >= 255
        else:
            R = ENC.srgb_eotf(np.clip(d["dc"], 0, 1))
            clipped = d["dc"].max(1) >= 254.5 / 255
        Eb, Ew, El, esc, back = capture(Pr, Nn)
        E_cap = Eb + Ew + El
        rho, flags, st = recover_albedo(R, E_cap, rho_max=args.rho_max, clipped=clipped, floor=floor)
        exterior = ((Pr[:, :2] @ nwall.T) - owall).min(1) < -0.10
        emitter = exterior | in_aperture(Pr, masks)
        flags[emitter] |= FLAGS["emitter"]
        flags[~on] |= FLAGS["off_surface"]
        flags[(esc + back) > 0.3] |= FLAGS["open_hemisphere"]
        rho, flags = neighbor_fill(rho, flags, Pr, Nn, clipped & on & ~emitter)
        d.update(Pr=Pr, R=R, E_cap=E_cap, rho=rho, flags=flags)
        good = on & ~emitter
        results[name] = {"count": int(len(P)), "on_surface_fraction": float(on.mean()),
                         "emitter_fraction": float(emitter.mean()), "clipped_fraction": float(clipped.mean()),
                         "clamp_hit_fraction": float(((flags[good] & FLAGS["unexplained_light"]) > 0).mean()),
                         "clamp_hit_fraction_all": st["clamp_hit_fraction"],
                         "low_irradiance_fraction": st["low_irradiance_fraction"],
                         "escape_fraction_mean": float(esc.mean()), "back_face_fraction_mean": float(back.mean()),
                         "window_share_of_capture_irradiance_median":
                             float(np.median(ENC.lum(Ew[good]) / np.maximum(ENC.lum(E_cap[good]), 1e-12))),
                         "albedo_luminance_median": float(np.median(ENC.lum(rho[good]))),
                         "albedo_luminance_p95": float(np.percentile(ENC.lum(rho[good]), 95))}
        log(name, json.dumps(results[name]))
        if name == "splat":
            sens = sensitivity(sc, d, good, walls, ceiling, args, R, Eb, Ew, El, rho, floor)
            for alt in (0.6, 0.9):  # the modeled ceiling's assumed paint albedo
                if not modeled.any():
                    break
                r_alt = rad_cap * np.where(modeled[..., None] | ~cap["inside"][..., None], alt / args.ceiling_albedo, 1)
                sc.set_aux(r_alt, _u8(rho_cap))
                Eb2, Ew2, El2, _, _ = capture(Pr, Nn)
                rho2, fl2, _ = recover_albedo(R, Eb2 + Ew2 + El2, rho_max=args.rho_max, floor=floor)
                dy = np.abs(np.log2(np.maximum(ENC.lum(rho2[good]), 1e-6) / np.maximum(ENC.lum(rho[good]), 1e-6)))
                sens[f"ceiling_albedo_{alt} (modeled ceiling)"] = {
                    "median_abs_albedo_change_stop": float(np.median(dy)),
                    "fraction_over_quarter_stop": float((dy > 0.25).mean()),
                    "clamp_hit_fraction": float(((fl2[good] & FLAGS["unexplained_light"]) > 0).mean())}
            sc.set_aux(rad_cap, _u8(rho_cap))
            log("sensitivity", json.dumps(sens))

    # Rendered-mesh vertices copy the trace-vertex values (identical positions).
    from scipy.spatial import cKDTree
    dmap, vmap = cKDTree(tp).query(mesh["positions"])
    E_r = E_v[vmap]
    flags_r = np.zeros(len(vmap), np.uint8)
    flags_r[vert_emitter[vmap]] |= FLAGS["emitter"]
    flags_r[((esc_v + back_v) > 0.3)[vmap]] |= FLAGS["open_hemisphere"]
    flags_r[ENC.lum(E_r) < floor] |= FLAGS["low_irradiance"]
    # Atlas-resolution statistics on random surface samples of the rendered mesh.
    rng = np.random.default_rng(0)
    rf = mesh["faces"]
    ns = min(2_000_000, 8 * len(rf))
    sf = rng.integers(0, len(rf), ns)
    bw = rng.dirichlet([1, 1, 1], ns)
    suv = (mesh["uv"][rf[sf]] * bw[:, :, None]).sum(1)
    spx = tex[np.clip((suv[:, 1] * TH).astype(int), 0, TH - 1), np.clip((suv[:, 0] * TW).astype(int), 0, TW - 1)]
    sR = lut[spx].astype(np.float64)
    sE = (E_r[rf[sf]] * bw[:, :, None]).sum(1)
    srho = np.pi * sR / np.maximum(sE, floor)
    s_em = (flags_r[rf[sf]] & FLAGS["emitter"]).max(1) > 0
    hit = (srho > args.rho_max).any(1)
    results["mesh"] = {"vertices": int(len(vmap)), "vertex_match_max_mm": float(dmap.max() * 1000),
                       "trace_geometry": mesh["trace_source"]["primitive"], "atlas_samples": int(ns),
                       "clamp_hit_fraction": float(hit[~s_em].mean()),
                       "albedo_luminance_median": float(np.median(ENC.lum(np.clip(srho[~s_em], 0, args.rho_max)))),
                       "emitter_vertex_fraction": float(((flags_r & FLAGS["emitter"]) > 0).mean()),
                       "open_vertex_fraction": float(((flags_r & FLAGS["open_hemisphere"]) > 0).mean())}
    log("mesh", json.dumps(results["mesh"]))

    after = BND.snapshot(pack)
    if after != before:
        raise SystemExit("PACK BYTES CHANGED during step 90 - aborting")
    np.savez(work / "delight.npz",
             **{f"{n}_{k}": sets[n][k] for n in sets for k in ("Pr", "normal", "rho", "flags", "R", "E_cap")},
             allP=allP, allF=allF, allUV=allUV, fn_all=fn_all, vn_all=vn_all, n_main=len(tf), glass=glass,
             cap_rad=rad_cap, cap_alb=_u8(rho_cap), E_vertex=np.concatenate([E_v, np.ones((4, 3))]),
             floor=floor, render_vmap=vmap, render_E=E_r, render_flags=flags_r, render_vn=vn_t[vmap],
             samp_face=sf, samp_bary=bw, samp_R=sR,
             **{f"window_{k}": v for k, v in window.items()}, c_win=c_win)
    meta = {"stage": "delit", "binding": binding, "pack_assets": assets,
            "pack_snapshot_before": BND.snapshot_digest(before), "pack_snapshot_after": BND.snapshot_digest(after),
            "pack_files": len(before), "equipment": eq, "mesh_normals_flipped": flipped, "ceiling_m": ceiling,
            "trace_geometry": mesh["trace_source"], "ceiling_cap": cap["stats"], "apertures": apertures,
            "exterior_splats": n_ext, "window_colour_rgb": c_win.tolist(), "irradiance_floor": floor,
            "rho_max": args.rho_max, "results": results, "sensitivity": sens,
            "capture_state": capture_state(args, lamps, gain, gain_trace),
            "params": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}}
    with open(work / "delight.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    log("step 90 done; pack bytes identical (%d files)" % len(before))
    return meta

def capture_state(args, lamps, gain, gain_trace):
    return {
        "Q1_room_lamps": {"answer": "unknown", "assumed": "off" if not lamps else "as given", "count": len(lamps)},
        "Q1_blinds": {"answer": "unknown", "assumed": args.blinds,
                      "note": "apertures are detected from the exterior splats actually seen; recorded only"},
        "Q2_capture_time": {"answer": "unknown", "assumed": args.capture_time or "unknown",
                            "note": "only used to place the sensitivity sun"},
        "Q3_sky": {"answer": "unknown", "assumed": args.sky,
                   "note": "no direct-sun term in the baseline; see sensitivity.sun_*"},
        "Q4_glass_door_wall": {"answer": "unknown", "assumed": args.glass_door_wall},
        "window_gain": {"value": gain, "mode": "fitted" if str(args.window_gain).lower() == "auto" else "given",
                        "iterations": gain_trace,
                        "prior": f"white patch: p{args.white_patch_percentile:g} of unclipped, non-emitter splat albedo = "
                                 f"{args.white_patch_albedo} (gain >= 1; exterior radiance is a clipped lower bound)"},
        "ceiling": {"model": "observed ceiling splats where present; elsewhere L = rho/pi * gathered E "
                             "(a flat ceiling does not see itself)", "assumed_albedo": args.ceiling_albedo},
        "window_model": "Lambertian area emitters on detected apertures; per-cell radiance = alpha-weighted mean of "
                        "exterior splat colours (sRGB-decoded); exterior clipping (~9 %) makes this a lower bound",
    }


def sensitivity(sc, d, good, walls, ceiling, args, R, Eb, Ew, E_lamp, rho0, floor):
    """How much the albedo moves under each unanswered capture-state assumption."""
    P, N = d["Pr"], d["normal"].astype(np.float64)
    base_E = Eb + Ew + E_lamp
    med = float(np.median(ENC.lum(base_E[good])))
    y0 = ENC.lum(rho0[good])

    def score(E):
        rho, flags, st = recover_albedo(R, E, rho_max=args.rho_max, floor=floor)
        dy = np.abs(np.log2(np.maximum(ENC.lum(rho[good]), 1e-6) / np.maximum(y0, 1e-6)))
        return {"median_abs_albedo_change_stop": float(np.median(dy)), "fraction_over_quarter_stop": float((dy > 0.25).mean()),
                "clamp_hit_fraction": float(((flags[good] & FLAGS["unexplained_light"]) > 0).mean())}

    out = {"window_gain_x0.5_of_fitted (Q1 blinds / exterior exposure)": score(Eb + 0.5 * Ew + E_lamp),
           "window_gain_x2_of_fitted (exterior clipping)": score(Eb + 2.0 * Ew + E_lamp)}
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

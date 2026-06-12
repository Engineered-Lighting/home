#!/usr/bin/env python3
"""Auto-calibrate a fixed camera from the apartment mesh itself.

Per round:
  1. fetch the live frame from Frigate
  2. render the mesh from the current pose estimate (pyrender / EGL headless)
  3. SIFT match render <-> photo (ratio test + mutual cross-check; CLAHE
     fallback if < 60 matches), filtered by RANSAC fundamental matrix
  4. lift each render-side match to 3D through the render depth buffer
  5. solve: fov sweep + cv2.solvePnPRansac, then single-view
     cv2.calibrateCamera (USE_INTRINSIC_GUESS | FIX_PRINCIPAL_POINT)
     jointly refining fx, fy, k1, k2 + pose
  6. iterate from the refined pose (max --rounds, early stop on stable rms)

Outputs:
  <out>_calib.json    K, dist, q_wxyz (world->cam), t, C, rms, per-round log
  <out>_matches.jpg   drawMatches visualization of final-round inliers
  <out>_photo.jpg     the live frame used
  <out>_round<i>.png  render from each round's input pose

Usage:
  ~/autocalib/venv/bin/python ~/autocalib/auto_calibrate.py \
      --cam kitchen --seed-pos 7.0,5.6,2.3 --seed-lookat 3.8,5.7,1.0 \
      --fov-guess 75 --out ~/autocalib/kitchen_auto
"""
import os
import sys
import json
import time
import argparse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_view  # noqa: E402  sets PYOPENGL_PLATFORM=egl + np.infty shim
from render_view import look_at_cv, intrinsics, backproject, CV_TO_GL  # noqa

import numpy as np  # noqa: E402
import cv2  # noqa: E402
import trimesh  # noqa: E402
import pyrender  # noqa: E402

FRIGATE_DEFAULT = "http://192.168.0.125:5000"


# ----------------------------------------------------------------------------
# Persistent renderer (mesh loaded once, re-render is ~0.05 s)
# ----------------------------------------------------------------------------

class MeshRenderer:
    def __init__(self, glb_path, w, h, znear=0.05, zfar=100.0):
        tm_scene = trimesh.load(glb_path, force="scene")
        self.scene = pyrender.Scene.from_trimesh_scene(
            tm_scene, ambient_light=np.array([1.0, 1.0, 1.0]))
        self.r = pyrender.OffscreenRenderer(w, h)
        self.w, self.h = w, h
        self.znear, self.zfar = znear, zfar
        self.cam_node = None

    def render(self, K, c2w_cv):
        """Flat-textured RGB + eye-space-z depth (meters, 0 = no hit)."""
        cam = pyrender.IntrinsicsCamera(
            fx=K[0, 0], fy=K[1, 1], cx=K[0, 2], cy=K[1, 2],
            znear=self.znear, zfar=self.zfar)
        if self.cam_node is not None:
            self.scene.remove_node(self.cam_node)
        self.cam_node = self.scene.add(cam, pose=c2w_cv @ CV_TO_GL)
        color, depth = self.r.render(self.scene,
                                     flags=pyrender.RenderFlags.FLAT)
        return color, depth


# ----------------------------------------------------------------------------
# Photo fetch
# ----------------------------------------------------------------------------

def fetch_photo(frigate, cam, save_path):
    url = (f"{frigate}/api/{cam}/latest.jpg"
           f"?bbox=0&timestamp=0&zones=0&motion=0&regions=0&cache={time.time()}")
    with urllib.request.urlopen(url, timeout=10) as r:
        buf = r.read()
    img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"could not decode frame from {url}")
    cv2.imwrite(save_path, img)
    return img  # BGR


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------

def _gray(img, from_rgb=False, clahe=False):
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY if from_rgb
                     else cv2.COLOR_BGR2GRAY)
    if clahe:
        g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    return g


def sift_mutual_match(render_rgb, photo_bgr, clahe=False, ratio=0.75,
                      nfeatures=6000):
    """SIFT + Lowe ratio in both directions + mutual cross-check.

    Returns ptsA (render px, Nx2), ptsB (photo px, Nx2).
    """
    gA = _gray(render_rgb, from_rgb=True, clahe=clahe)
    gB = _gray(photo_bgr, from_rgb=False, clahe=clahe)
    sift = cv2.SIFT_create(nfeatures=nfeatures)
    kA, dA = sift.detectAndCompute(gA, None)
    kB, dB = sift.detectAndCompute(gB, None)
    if dA is None or dB is None or len(kA) < 8 or len(kB) < 8:
        return np.zeros((0, 2)), np.zeros((0, 2))
    bf = cv2.BFMatcher(cv2.NORM_L2)

    def ratio_dict(d1, d2):
        out = {}
        for pair in bf.knnMatch(d1, d2, k=2):
            if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance:
                out[pair[0].queryIdx] = pair[0].trainIdx
        return out

    ab = ratio_dict(dA, dB)
    ba = ratio_dict(dB, dA)
    pairs = [(i, j) for i, j in ab.items() if ba.get(j) == i]
    if not pairs:
        return np.zeros((0, 2)), np.zeros((0, 2))
    ptsA = np.array([kA[i].pt for i, _ in pairs], dtype=np.float64)
    ptsB = np.array([kB[j].pt for _, j in pairs], dtype=np.float64)
    return ptsA, ptsB


_LOFTR = None


def loftr_match(render_rgb, photo_bgr, conf_thresh=0.2, max_dim=640):
    """Dense learned matcher (kornia LoFTR, indoor_new weights).

    Handles the render-vs-photo appearance gap (baked warm lighting +
    photogrammetry blur vs live daylight) that defeats SIFT.
    Run near training resolution (640px): at 1024px match count collapses.
    NOTE: 'indoor'/'outdoor' checkpoints degrade to identity matches on
    same-layout pairs (positional prior) — verified on this scene; the
    perturbation self-check in main() guards against that failure mode.
    """
    global _LOFTR
    import torch
    import kornia.feature as KF

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if _LOFTR is None:
        _LOFTR = KF.LoFTR(pretrained="indoor_new").to(dev).eval()

    gA = _gray(render_rgb, from_rgb=True)
    gB = _gray(photo_bgr, from_rgb=False)
    h, w = gA.shape
    s = min(1.0, max_dim / max(h, w))
    nw = int(round(w * s / 8)) * 8
    nh = int(round(h * s / 8)) * 8
    sx, sy = w / nw, h / nh

    def to_t(g):
        g = cv2.resize(g, (nw, nh), interpolation=cv2.INTER_AREA)
        return torch.from_numpy(g)[None, None].float().to(dev) / 255.0

    for attempt in range(3):
        try:
            with torch.inference_mode():
                out = _LOFTR({"image0": to_t(gA), "image1": to_t(gB)})
            break
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            nw = int(round(nw * 0.75 / 8)) * 8
            nh = int(round(nh * 0.75 / 8)) * 8
            sx, sy = w / nw, h / nh
            if attempt == 2:
                raise
    conf = out["confidence"].cpu().numpy()
    k = conf > conf_thresh
    ptsA = out["keypoints0"].cpu().numpy()[k] * np.array([sx, sy])
    ptsB = out["keypoints1"].cpu().numpy()[k] * np.array([sx, sy])
    return ptsA.astype(np.float64), ptsB.astype(np.float64)


def match_round(render_rgb, photo_bgr, matcher="auto"):
    """SIFT (plain -> CLAHE fallback); LoFTR fallback/override per --matcher."""
    ptsA = ptsB = None
    used = None
    if matcher in ("auto", "sift"):
        ptsA, ptsB = sift_mutual_match(render_rgb, photo_bgr, clahe=False)
        used = "sift"
        if len(ptsA) < 60:
            pA2, pB2 = sift_mutual_match(render_rgb, photo_bgr, clahe=True)
            if len(pA2) > len(ptsA):
                ptsA, ptsB, used = pA2, pB2, "sift_clahe"
    if matcher == "loftr" or (matcher == "auto" and len(ptsA) < 60):
        try:
            pA3, pB3 = loftr_match(render_rgb, photo_bgr)
            if ptsA is None or len(pA3) > len(ptsA):
                ptsA, ptsB, used = pA3, pB3, "loftr"
        except Exception as e:  # torch/kornia missing or download failure
            print(f"[match] LoFTR unavailable: {e}", file=sys.stderr)
            if ptsA is None:
                raise
    return ptsA, ptsB, used


def fundamental_filter(ptsA, ptsB, thresh_px=3.0):
    if len(ptsA) < 8:
        return np.zeros(len(ptsA), dtype=bool)
    F, mask = cv2.findFundamentalMat(
        ptsA.astype(np.float32), ptsB.astype(np.float32),
        cv2.FM_RANSAC, thresh_px, 0.999)
    if F is None or mask is None:
        return np.zeros(len(ptsA), dtype=bool)
    return mask.ravel().astype(bool)


# ----------------------------------------------------------------------------
# Depth lift
# ----------------------------------------------------------------------------

def lift_matches(ptsA, depth, K_render, c2w_render,
                 max_window_range=0.20):
    """Render pixels -> world 3D via depth buffer.

    Rejects pixels on depth discontinuities (3x3 window has a hole or spans
    > max_window_range meters) — SIFT loves occlusion edges, where the
    z-buffer value is ambiguous.
    Returns (world_pts Nx3, keep_mask over ptsA).
    """
    h, w = depth.shape
    keep = np.zeros(len(ptsA), dtype=bool)
    world = np.zeros((len(ptsA), 3))
    for i, (u, v) in enumerate(ptsA):
        xi, yi = int(round(u)), int(round(v))
        if not (1 <= xi < w - 1 and 1 <= yi < h - 1):
            continue
        win = depth[yi - 1:yi + 2, xi - 1:xi + 2]
        if (win <= 0).any():
            continue
        if win.max() - win.min() > max_window_range:
            continue
        d = float(depth[yi, xi])
        world[i] = backproject(u, v, d, K_render, c2w_render)
        keep[i] = True
    return world, keep


# ----------------------------------------------------------------------------
# Solving
# ----------------------------------------------------------------------------

def reproj_rms(obj, img, rvec, tvec, K, dist):
    proj, _ = cv2.projectPoints(obj.reshape(-1, 1, 3), rvec, tvec, K, dist)
    e = np.linalg.norm(proj.reshape(-1, 2) - img, axis=1)
    return float(np.sqrt(np.mean(e ** 2))), e


def solve_pose(obj, img, w, h, K_candidates, dist_init, reproj_thresh=8.0,
               k1_candidates=(0.0,)):
    """(fov x k1)-sweep solvePnPRansac -> best model, calibrateCamera refine.

    dist_init None => sweep k1_candidates; else use dist_init as-is.
    Returns dict or None.
    """
    obj32 = obj.astype(np.float32)
    img32 = img.astype(np.float32)
    if dist_init is not None:
        dist_cands = [("carry", np.asarray(dist_init, np.float64).reshape(5, 1))]
    else:
        dist_cands = [(f"k1={k1:.2f}",
                       np.array([[k1], [0.], [0.], [0.], [0.]]))
                      for k1 in k1_candidates]
    best = None
    for tag, K in K_candidates:
        for dtag, dist in dist_cands:
            ok, rvec, tvec, inl = cv2.solvePnPRansac(
                obj32, img32, K.astype(np.float64), dist,
                iterationsCount=5000, reprojectionError=reproj_thresh,
                confidence=0.9999, flags=cv2.SOLVEPNP_ITERATIVE)
            if not ok or inl is None or len(inl) < 12:
                continue
            inl = inl.ravel()
            rms, _ = reproj_rms(obj[inl], img[inl], rvec, tvec, K, dist)
            score = (len(inl), -rms)
            if best is None or score > best["score"]:
                best = dict(score=score, tag=f"{tag},{dtag}", K=K.copy(),
                            dist=dist.copy(), rvec=rvec, tvec=tvec,
                            inliers=inl, rms=rms)
    if best is None:
        return None
    dist_init = best["dist"]

    # --- joint refine: fx, fy, k1, k2 + pose (single view) ---
    inl = best["inliers"]
    K0 = best["K"].astype(np.float64).copy()
    d0 = dist_init.copy()
    flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT |
             cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3)
    try:
        rms_cal, K1, d1, rvecs, tvecs = cv2.calibrateCamera(
            [obj[inl].astype(np.float32).reshape(-1, 1, 3)],
            [img[inl].astype(np.float32).reshape(-1, 1, 2)],
            (w, h), K0, d0, flags=flags)
        sane = (300 < K1[0, 0] < 3000 and 300 < K1[1, 1] < 3000 and
                abs(d1.ravel()[0]) < 1.5 and abs(d1.ravel()[1]) < 5.0 and
                rms_cal < max(2.0 * best["rms"], 10.0))
    except cv2.error:
        sane = False
    if sane:
        rvec, tvec = rvecs[0], tvecs[0]
        K_f, d_f, rms_f = K1, d1.reshape(-1), float(rms_cal)
        refined = True
    else:  # fall back: LM-polish the PnP pose, keep sweep K
        rvec, tvec = cv2.solvePnPRefineLM(
            obj32[inl], img32[inl], best["K"], dist_init,
            best["rvec"], best["tvec"])
        K_f = best["K"]
        d_f = (dist_init.reshape(-1) if dist_init is not None
               else np.zeros(5))
        rms_f, _ = reproj_rms(obj[inl], img[inl], rvec, tvec, K_f,
                              d_f.reshape(-1, 1))
        refined = False

    # final inlier accounting against the refined model (all RANSAC matches)
    _, errs = reproj_rms(obj, img, rvec, tvec, K_f, d_f.reshape(-1, 1))
    final_inl = np.where(errs < 4.0)[0]
    return dict(K=np.asarray(K_f), dist=np.asarray(d_f).reshape(-1),
                rvec=np.asarray(rvec).reshape(3), tvec=np.asarray(tvec).reshape(3),
                rms=rms_f, pnp_inliers=inl, final_inliers=final_inl,
                fov_tag=best["tag"], calib_refined=refined)


def pose_to_c2w(rvec, tvec):
    R, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))
    t = np.asarray(tvec, dtype=np.float64).reshape(3)
    C = -R.T @ t
    c2w = np.eye(4)
    c2w[:3, :3] = R.T
    c2w[:3, 3] = C
    return c2w, C, R


def quat_wxyz_from_R(R):
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s,
             (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s,
             (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s,
             0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s,
             (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    q = np.asarray(q)
    return q / np.linalg.norm(q)


def fov_h_deg(K, w):
    return float(np.rad2deg(2 * np.arctan(w / (2 * K[0, 0]))))


# ----------------------------------------------------------------------------
# Visualization
# ----------------------------------------------------------------------------

def save_match_viz(render_rgb, photo_bgr, ptsA, ptsB, inlier_idx, out_path,
                   scale=0.5):
    kA = [cv2.KeyPoint(float(x), float(y), 4) for x, y in ptsA[inlier_idx]]
    kB = [cv2.KeyPoint(float(x), float(y), 4) for x, y in ptsB[inlier_idx]]
    dm = [cv2.DMatch(i, i, 0.0) for i in range(len(kA))]
    viz = cv2.drawMatches(
        cv2.cvtColor(render_rgb, cv2.COLOR_RGB2BGR), kA, photo_bgr, kB, dm,
        None, matchColor=(0, 255, 0), singlePointColor=(0, 0, 255),
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
    viz = cv2.resize(viz, None, fx=scale, fy=scale,
                     interpolation=cv2.INTER_AREA)
    cv2.imwrite(out_path, viz, [cv2.IMWRITE_JPEG_QUALITY, 88])


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

def parse_vec3(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 3:
        raise argparse.ArgumentTypeError("expected x,y,z")
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cam", required=True)
    ap.add_argument("--seed-pos", type=parse_vec3, required=True)
    ap.add_argument("--seed-lookat", type=parse_vec3, required=True)
    ap.add_argument("--fov-guess", type=float, default=75.0)
    ap.add_argument("--frigate", default=FRIGATE_DEFAULT)
    ap.add_argument("--mesh", default=os.path.expanduser(
        "~/autocalib/mesh_full.glb"))
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--matcher", choices=("auto", "sift", "loftr"),
                    default="auto",
                    help="auto = SIFT first, LoFTR fallback if < 60 matches")
    ap.add_argument("--out", required=True, help="output path prefix")
    args = ap.parse_args()

    t0 = time.time()
    photo = fetch_photo(args.frigate, args.cam, args.out + "_photo.jpg")
    h, w = photo.shape[:2]
    print(f"[fetch] {args.cam}: {w}x{h}")

    renderer = MeshRenderer(args.mesh, w, h)
    c2w = look_at_cv(args.seed_pos, args.seed_lookat)
    K_render = intrinsics(w, h, args.fov_guess)
    K_photo, dist = None, None
    rounds_log = []
    last = None
    prev_rms = None

    for rnd in range(1, args.rounds + 1):
        color, depth = renderer.render(K_render, c2w)
        cv2.imwrite(args.out + f"_round{rnd}.png",
                    cv2.cvtColor(color, cv2.COLOR_RGB2BGR))

        ptsA, ptsB, mode = match_round(color, photo, args.matcher)
        n_raw = len(ptsA)
        # F-RANSAC is advisory: it cannot model lens distortion, so with a
        # wide-angle photo correct edge matches violate any F. Only apply it
        # when it keeps a healthy core; otherwise PnP-RANSAC (the correct
        # model: exact 3D points + intrinsics w/ distortion) does the gating.
        fmask = fundamental_filter(ptsA, ptsB, thresh_px=5.0)
        if fmask.sum() >= 40:
            ptsA, ptsB = ptsA[fmask], ptsB[fmask]
        n_ransac = len(ptsA)
        print(f"[round {rnd}] matches raw={n_raw} ({mode}) "
              f"F-RANSAC={int(fmask.sum())} used={n_ransac}")

        world, keep = lift_matches(ptsA, depth, K_render, c2w)
        obj, img = world[keep], ptsB[keep]
        ptsA_l = ptsA[keep]
        n_lift = len(obj)
        print(f"[round {rnd}] 3D-lifted={n_lift}")
        if n_lift < 12:
            print(f"[round {rnd}] FATAL: only {n_lift} liftable matches",
                  file=sys.stderr)
            break

        # K candidates: full fov sweep round 1, narrow sweep + carry later
        cands = []
        if K_photo is None:
            for f in range(50, 111, 5):
                cands.append((f"fov{f}", intrinsics(w, h, f)))
        else:
            cands.append(("carry", K_photo.copy()))
            f0 = fov_h_deg(K_photo, w)
            for df in (-6, -3, 3, 6):
                cands.append((f"fov{f0 + df:.0f}",
                              intrinsics(w, h, f0 + df)))
        sol = solve_pose(obj, img, w, h, cands,
                         dist if dist is not None else None,
                         k1_candidates=(0.0, -0.15, -0.30, -0.45))
        if sol is None:
            print(f"[round {rnd}] FATAL: PnP failed on all K candidates",
                  file=sys.stderr)
            break

        c2w, C, R_w2c = pose_to_c2w(sol["rvec"], sol["tvec"])
        K_photo, dist = sol["K"], sol["dist"].reshape(5, 1)
        K_render = K_photo.copy()  # render next round with refined pinhole K
        fov_now = fov_h_deg(K_photo, w)
        print(f"[round {rnd}] best={sol['fov_tag']} "
              f"calib_refined={sol['calib_refined']} "
              f"rms={sol['rms']:.3f}px inl={len(sol['pnp_inliers'])} "
              f"fov_h={fov_now:.1f} C=[{C[0]:.2f},{C[1]:.2f},{C[2]:.2f}]")

        rounds_log.append(dict(
            round=rnd, match_mode=mode, n_raw=n_raw, n_ransac=n_ransac,
            n_lifted=n_lift, n_pnp_inliers=int(len(sol["pnp_inliers"])),
            n_final_inliers=int(len(sol["final_inliers"])),
            fov_tag=sol["fov_tag"], calib_refined=bool(sol["calib_refined"]),
            rms_px=float(sol["rms"]), fov_h_deg=fov_now,
            C=[float(x) for x in C]))
        last = dict(sol=sol, ptsA=ptsA_l, ptsB=img, C=C, R=R_w2c,
                    render=color, rnd=rnd)

        if prev_rms is not None and abs(prev_rms - sol["rms"]) < 0.05 \
                and rnd >= 2:
            print(f"[round {rnd}] rms stable, stopping early")
            break
        prev_rms = sol["rms"]

    if last is None:
        out = dict(cam=args.cam, status="FAILED",
                   reason="insufficient matches / PnP failure",
                   rounds=rounds_log)
        with open(args.out + "_calib.json", "w") as f:
            json.dump(out, f, indent=2)
        print(json.dumps(out, indent=2))
        sys.exit(1)

    sol, C, R = last["sol"], last["C"], last["R"]
    q = quat_wxyz_from_R(R)

    # ---- perturbation self-check ------------------------------------------
    # Render from a pose deliberately offset from the solution; the solve
    # must come back to the solution, NOT stick to the offset render pose
    # (which is what identity-degenerate matching produces).
    pert_check = dict(passed=False, reason="solve failed")
    try:
        c2w_f = pose_to_c2w(sol["rvec"], sol["tvec"])[0]
        Cp = C + c2w_f[:3, 0] * 0.25 + np.array([0.0, 0.0, 0.10])
        a = np.deg2rad(3.0)
        Rz = np.array([[np.cos(a), -np.sin(a), 0],
                       [np.sin(a), np.cos(a), 0], [0, 0, 1.0]])
        c2w_p = np.eye(4)
        c2w_p[:3, :3] = Rz @ c2w_f[:3, :3]
        c2w_p[:3, 3] = Cp
        color_p, depth_p = renderer.render(K_photo, c2w_p)
        pA, pB, _ = match_round(color_p, photo, args.matcher)
        fm = fundamental_filter(pA, pB, thresh_px=5.0)
        if fm.sum() >= 40:
            pA, pB = pA[fm], pB[fm]
        wpts, kp = lift_matches(pA, depth_p, K_photo, c2w_p)
        chk = solve_pose(wpts[kp], pB[kp], w, h,
                         [("carry", K_photo.copy())], dist)
        if chk is not None:
            C_chk = pose_to_c2w(chk["rvec"], chk["tvec"])[1]
            d_sol = float(np.linalg.norm(C_chk - C))
            d_pert = float(np.linalg.norm(C_chk - Cp))
            pert_check = dict(
                passed=bool(d_sol < 0.30 and d_sol < d_pert),
                C_recovered=[float(x) for x in C_chk],
                dist_to_solution_m=d_sol, dist_to_perturbed_m=d_pert,
                n_inliers=int(len(chk["pnp_inliers"])), reason=None)
    except Exception as e:
        pert_check = dict(passed=False, reason=str(e))
    print(f"[check] perturbation: {pert_check}")

    out = dict(
        cam=args.cam, status="OK",
        image_size=[w, h],
        K=sol["K"].tolist(),
        dist_k1k2p1p2k3=sol["dist"].tolist(),
        q_wxyz_world_to_cam=q.tolist(),
        t_world_to_cam=sol["tvec"].tolist(),
        C_world=C.tolist(),
        cam_to_world_opencv=pose_to_c2w(sol["rvec"], sol["tvec"])[0].tolist(),
        rms_px=float(sol["rms"]),
        fov_h_deg=fov_h_deg(sol["K"], w),
        n_matches_final=int(len(last["ptsA"])),
        n_inliers_final=int(len(sol["final_inliers"])),
        seed_pos=args.seed_pos, seed_lookat=args.seed_lookat,
        seed_offset_m=float(np.linalg.norm(C - np.asarray(args.seed_pos))),
        perturbation_check=pert_check,
        rounds=rounds_log,
        total_time_s=round(time.time() - t0, 2),
    )
    with open(args.out + "_calib.json", "w") as f:
        json.dump(out, f, indent=2)

    # final-round visualization: render from FINAL pose, show its inliers
    save_match_viz(last["render"], photo, last["ptsA"], last["ptsB"],
                   sol["final_inliers"], args.out + "_matches.jpg")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

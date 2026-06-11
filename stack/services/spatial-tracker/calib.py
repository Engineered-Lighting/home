"""Camera calibration endpoints + pure solver helpers.

Endpoints (router built by :func:`build_router`, wired in main.py):

  GET  /calib/{cam}/snapshot                 fresh frame from Frigate
  POST /calib/{cam}/extrinsics               PnP from pixel<->world pairs
  POST /calib/{cam}/intrinsics/solve         501 (ChArUco capture not wired)
  POST /calib/{cam}/intrinsics/from_images   ChArUco chessboard images -> K/dist
  GET  /calib/{cam}/overlay.jpg              project collision_edges.npz onto frame

Extrinsics math: pixel pairs are undistorted with cv2.undistortPoints(P=K)
then solved with solvePnPRansac (SQPNP, falling back to EPNP/ITERATIVE for
OpenCV builds where SQPNP is not RANSAC-compatible) and refined with
solvePnPRefineLM.  Output: rotation as q_wxyz (world->camera, OpenCV
convention +Z forward +Y down), t, and camera center C = -R^T t in the
Z-up world frame.  Nothing is persisted here — the dashboard app merges the
result into the apartment model and POSTs it back to HA.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from tracker import CameraGeometry, R_to_quat_wxyz

log = logging.getLogger("spatial.calib")

BORDER_SAMPLE_STEP_PX = 40
MAX_COVERAGE_RANGE_M = 30.0


class CalibError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---- pure solver helpers ---------------------------------------------------

def solve_extrinsics(pairs: list[dict], K, dist,
                     intr_image_size: tuple[int, int],
                     provided_image_size: Optional[tuple[int, int]] = None,
                     walkable=None) -> dict:
    """PnP solve from {px:[u,v], xyz:[x,y,z]} pairs.

    ``provided_image_size`` is the resolution the clicks were made at; pixel
    coordinates are scaled to the intrinsics calibration resolution first.
    """
    if len(pairs) < 4:
        raise CalibError("need at least 4 point pairs for PnP", 400)
    K = np.asarray(K, dtype=np.float64).reshape(3, 3)
    dist = np.asarray(dist if dist is not None else [], dtype=np.float64).reshape(-1)
    if dist.size == 0:
        dist = np.zeros(5)

    px = np.array([p["px"] for p in pairs], dtype=np.float64)
    obj = np.array([p["xyz"] for p in pairs], dtype=np.float64)
    if provided_image_size:
        sx = intr_image_size[0] / float(provided_image_size[0])
        sy = intr_image_size[1] / float(provided_image_size[1])
        px = px * np.array([sx, sy])

    und = cv2.undistortPoints(px.reshape(-1, 1, 2), K, dist, P=K).reshape(-1, 2)

    rvec = tvec = inliers = None
    for flags in (getattr(cv2, "SOLVEPNP_SQPNP", None), cv2.SOLVEPNP_EPNP,
                  cv2.SOLVEPNP_ITERATIVE):
        if flags is None:
            continue
        try:
            ok, rvec, tvec, inliers = cv2.solvePnPRansac(
                obj, und.reshape(-1, 1, 2), K, None,
                flags=flags, reprojectionError=8.0, iterationsCount=300,
                confidence=0.999)
            if ok:
                break
        except cv2.error as e:
            log.debug("solvePnPRansac flags=%s failed: %s", flags, e)
            rvec = None
    if rvec is None:
        raise CalibError("solvePnPRansac failed on the provided pairs", 422)

    if inliers is None or len(inliers) < 4:
        inliers = np.arange(len(obj)).reshape(-1, 1)
    idx = inliers.reshape(-1)
    try:
        rvec, tvec = cv2.solvePnPRefineLM(
            obj[idx], und[idx].reshape(-1, 1, 2), K, None, rvec, tvec)
    except cv2.error as e:
        log.warning("solvePnPRefineLM failed (%s); keeping RANSAC pose", e)

    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
    err = np.linalg.norm(proj.reshape(-1, 2) - und, axis=1)
    rms = float(math.sqrt(float(np.mean(err ** 2))))

    R, _ = cv2.Rodrigues(rvec)
    t = tvec.reshape(3)
    C = (-R.T @ t).reshape(3)
    q = R_to_quat_wxyz(R)

    geom = CameraGeometry(K=K, dist=dist, image_size=intr_image_size, R=R, t=t)
    coverage = floor_coverage_polygon(geom, walkable)

    return {
        "rms_px": rms,
        "per_point_err_px": [float(e) for e in err],
        "inlier_indices": [int(i) for i in idx],
        "q_wxyz": q,
        "t": [float(v) for v in t],
        "C": [float(v) for v in C],
        "floor_coverage_polygon": coverage,
    }


def floor_coverage_polygon(geom: CameraGeometry, walkable=None
                           ) -> Optional[list[list[float]]]:
    """Cast rays through the image border, intersect z=0, clip to the
    walkable hull. None when too few border rays hit the floor."""
    w, h = geom.image_size
    border: list[tuple[float, float]] = []
    xs = list(np.arange(0, w + 1, BORDER_SAMPLE_STEP_PX))
    ys = list(np.arange(BORDER_SAMPLE_STEP_PX, h - BORDER_SAMPLE_STEP_PX + 1,
                        BORDER_SAMPLE_STEP_PX))
    border += [(x, 0.0) for x in xs]                    # top, left->right
    border += [(float(w), y) for y in ys]               # right, down
    border += [(x, float(h)) for x in reversed(xs)]     # bottom, right->left
    border += [(0.0, y) for y in reversed(ys)]          # left, up

    hits: list[tuple[float, float]] = []
    for u, v in border:
        try:
            _, d = geom.pixel_to_ray(u, v, from_detect=False)
        except Exception:
            continue
        X = geom.intersect_plane(d, z=0.0)
        if X is None:
            continue
        if float(np.linalg.norm(X - geom.C)) > MAX_COVERAGE_RANGE_M:
            continue
        hits.append((float(X[0]), float(X[1])))
    if len(hits) < 3:
        return None
    try:
        from shapely.geometry import Polygon
        poly = Polygon(hits)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if walkable is not None:
            poly = poly.intersection(walkable)
        if poly.is_empty:
            return None
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        return [[float(x), float(y)] for x, y in poly.exterior.coords]
    except Exception:
        log.exception("floor coverage polygon construction failed")
        return None


def calibrate_charuco(images: list[np.ndarray], board_cols: int, board_rows: int,
                      square_mm: float) -> dict:
    """ChArUco (DICT_5X5_100) intrinsics calibration with CALIB_RATIONAL_MODEL.

    Handles both the OpenCV >= 4.7 CharucoDetector API and the legacy
    cv2.aruco.interpolateCornersCharuco / calibrateCameraCharucoExtended API.
    """
    if not images:
        raise CalibError("no images provided", 400)
    square_m = square_mm / 1000.0
    marker_m = square_m * 0.75
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    image_size = None
    all_corners: list[np.ndarray] = []
    all_ids: list[np.ndarray] = []

    try:  # modern API (OpenCV >= 4.7)
        board = cv2.aruco.CharucoBoard((board_cols, board_rows), square_m,
                                       marker_m, dictionary)
        detector = cv2.aruco.CharucoDetector(board)
        legacy = False
    except AttributeError:  # legacy API
        board = cv2.aruco.CharucoBoard_create(board_cols, board_rows, square_m,
                                              marker_m, dictionary)
        detector = None
        legacy = True

    for img in images:
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if image_size is None:
            image_size = (gray.shape[1], gray.shape[0])
        elif (gray.shape[1], gray.shape[0]) != image_size:
            continue  # skip mixed-resolution frames
        if not legacy:
            ch_corners, ch_ids, _, _ = detector.detectBoard(gray)
        else:
            mk_corners, mk_ids, _ = cv2.aruco.detectMarkers(gray, dictionary)
            ch_corners = ch_ids = None
            if mk_ids is not None and len(mk_ids) > 0:
                _, ch_corners, ch_ids = cv2.aruco.interpolateCornersCharuco(
                    mk_corners, mk_ids, gray, board)
        if ch_corners is not None and ch_ids is not None and len(ch_corners) >= 6:
            all_corners.append(ch_corners)
            all_ids.append(ch_ids)

    if len(all_corners) < 3:
        raise CalibError(
            f"only {len(all_corners)} usable frames with >=6 ChArUco corners; "
            "need at least 3", 422)

    flags = cv2.CALIB_RATIONAL_MODEL
    if hasattr(cv2.aruco, "calibrateCameraCharucoExtended"):
        result = cv2.aruco.calibrateCameraCharucoExtended(
            charucoCorners=all_corners, charucoIds=all_ids, board=board,
            imageSize=image_size, cameraMatrix=None, distCoeffs=None,
            flags=flags)
        rms, K, dist = float(result[0]), result[1], result[2]
    else:
        obj_pts, img_pts = [], []
        for corners, ids in zip(all_corners, all_ids):
            op, ip = board.matchImagePoints(corners, ids)
            if op is not None and len(op) >= 6:
                obj_pts.append(op)
                img_pts.append(ip)
        if len(obj_pts) < 3:
            raise CalibError("matchImagePoints yielded too few frames", 422)
        rms, K, dist, _, _ = cv2.calibrateCamera(
            obj_pts, img_pts, image_size, None, None, flags=flags)
        rms = float(rms)

    return {
        "K": [[float(v) for v in row] for row in np.asarray(K).reshape(3, 3)],
        "dist": [float(v) for v in np.asarray(dist).reshape(-1)],
        "rms_px": rms,
        "n_frames_used": len(all_corners),
        "image_size": [int(image_size[0]), int(image_size[1])],
    }


def render_overlay(frame_bgr: np.ndarray, geom: CameraGeometry,
                   edges: np.ndarray) -> np.ndarray:
    """Project Nx2x3 world-frame segments onto the frame (in-place draw)."""
    rvec, _ = cv2.Rodrigues(geom.R)
    tvec = geom.t.reshape(3, 1)
    sx = frame_bgr.shape[1] / geom.image_size[0]
    sy = frame_bgr.shape[0] / geom.image_size[1]
    for seg in edges:
        a, b = np.asarray(seg[0], np.float64), np.asarray(seg[1], np.float64)
        za = float((geom.R @ (a - geom.C))[2])
        zb = float((geom.R @ (b - geom.C))[2])
        if za <= 0.05 or zb <= 0.05:
            continue  # behind / too close to the camera plane
        pts, _ = cv2.projectPoints(np.stack([a, b]).reshape(-1, 1, 3),
                                   rvec, tvec, geom.K, geom.dist)
        (ua, va), (ub, vb) = pts.reshape(2, 2)
        cv2.line(frame_bgr,
                 (int(round(ua * sx)), int(round(va * sy))),
                 (int(round(ub * sx)), int(round(vb * sy))),
                 (0, 255, 0), 2, cv2.LINE_AA)
    return frame_bgr


# ---- FastAPI router ----------------------------------------------------------

def build_router(ctx):
    """ctx needs: model_store, frigate_url, data_dir, http() -> aiohttp session."""
    from fastapi import APIRouter, File, Form, HTTPException, UploadFile
    from fastapi.responses import JSONResponse, Response

    router = APIRouter(prefix="/calib", tags=["calibration"])

    async def fetch_snapshot(cam: str) -> bytes:
        url = f"{ctx.frigate_url}/api/{cam}/latest.jpg"
        try:
            session = await ctx.http()
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    raise HTTPException(
                        502, f"Frigate snapshot {url} returned HTTP {resp.status}")
                return await resp.read()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"failed to fetch Frigate snapshot: {e}")

    def cam_intrinsics(cam: str) -> dict:
        dev = ctx.model_store.get_model().camera_device(cam)
        intr = ((dev or {}).get("camera") or {}).get("intrinsics") or {}
        if not intr.get("K") or not intr.get("image_size"):
            raise HTTPException(
                400, f"camera '{cam}' has no intrinsics in the apartment model; "
                     "calibrate intrinsics first")
        return intr

    @router.get("/{cam}/snapshot")
    async def snapshot(cam: str):
        data = await fetch_snapshot(cam)
        return Response(content=data, media_type="image/jpeg")

    @router.post("/{cam}/extrinsics")
    async def extrinsics(cam: str, body: dict):
        intr = cam_intrinsics(cam)
        pairs = body.get("pairs") or []
        image_size = body.get("image_size")
        if not isinstance(pairs, list) or any(
                "px" not in p or "xyz" not in p for p in pairs):
            raise HTTPException(400, "body must be {pairs:[{px:[u,v],xyz:[x,y,z]}...]}")
        try:
            result = solve_extrinsics(
                pairs, intr["K"], intr.get("dist"),
                tuple(intr["image_size"]),
                tuple(image_size) if image_size else None,
                walkable=ctx.model_store.walkable_hull(),
            )
        except CalibError as e:
            raise HTTPException(e.status, str(e))
        return JSONResponse(result)

    def _solve_generic(object_points, image_points, image_size) -> dict:
        """cv2.calibrateCamera over per-view object/image point lists."""
        objs = [np.asarray(o, np.float32) for o in object_points]
        imgs = [np.asarray(i, np.float32) for i in image_points]
        if len(objs) < 6:
            raise HTTPException(400, f"need >=6 views, got {len(objs)}")
        size = tuple(int(v) for v in image_size)
        rms, K, dist, _, _ = cv2.calibrateCamera(
            objs, imgs, size, None, None,
            flags=cv2.CALIB_FIX_K3 if len(objs) < 15 else 0)
        return {"rms_px": float(rms), "K": K.tolist(),
                "dist": dist.ravel().tolist(), "image_size": list(size),
                "views": len(objs)}

    @router.post("/{cam}/intrinsics/solve")
    async def intrinsics_solve(cam: str, body: Optional[dict] = None):
        body = body or {}
        if not body.get("object_points"):
            raise HTTPException(400, "body must carry object_points/image_points/"
                                     "image_size (or use /capture/snap + /capture/solve)")
        return JSONResponse(_solve_generic(
            body["object_points"], body["image_points"], body["image_size"]))

    # ---- interactive intrinsics capture (driven from the app's calibrate UI) ----
    _capture: dict = {}

    @router.post("/{cam}/capture/snap")
    async def capture_snap(cam: str):
        import base64
        try:
            from detector_boards import detect_boards
        except ImportError:
            raise HTTPException(501, "detector_boards module not deployed")
        data = await fetch_snapshot(cam)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        boards = detect_boards(img)
        sess = _capture.setdefault(cam, {"views": [], "means": []})
        vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        added = 0
        for pts, idx in boards:
            pts = np.asarray(pts, np.float32)
            mean = pts.mean(axis=0)
            dup = any(float(np.linalg.norm(mean - m)) < 30 for m in sess["means"])
            color = (90, 90, 240) if dup else (90, 220, 90)
            if not dup:
                obj = np.zeros((len(pts), 3), np.float32)
                obj[:, :2] = np.asarray(idx, np.float32) * 0.035
                sess["views"].append({"obj": obj.tolist(), "img": pts.tolist()})
                sess["means"].append(mean)
                sess["size"] = [img.shape[1], img.shape[0]]
                added += 1
            for q in pts:
                cv2.circle(vis, (int(q[0]), int(q[1])), 5, color, 2)
        small = cv2.resize(vis, (560, int(560 * img.shape[0] / img.shape[1])))
        ok, jpg = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return {"boards_found": len(boards), "new_views": added,
                "total_views": len(sess["views"]),
                "thumb": "data:image/jpeg;base64,"
                         + base64.b64encode(jpg.tobytes()).decode()}

    @router.post("/{cam}/capture/reset")
    async def capture_reset(cam: str):
        _capture.pop(cam, None)
        return {"ok": True}

    @router.post("/{cam}/capture/solve")
    async def capture_solve(cam: str):
        sess = _capture.get(cam)
        if not sess or not sess["views"]:
            raise HTTPException(400, "no captured views — snap some first")
        return JSONResponse(_solve_generic(
            [v["obj"] for v in sess["views"]],
            [v["img"] for v in sess["views"]], sess["size"]))

    @router.post("/{cam}/intrinsics/from_images")
    async def intrinsics_from_images(
        cam: str,
        files: list[UploadFile] = File(...),
        board_cols: int = Form(...),
        board_rows: int = Form(...),
        square_mm: float = Form(...),
    ):
        images = []
        for f in files:
            raw = await f.read()
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                images.append(img)
        if not images:
            raise HTTPException(400, "no decodable images uploaded")
        try:
            result = calibrate_charuco(images, board_cols, board_rows, square_mm)
        except CalibError as e:
            raise HTTPException(e.status, str(e))
        return JSONResponse(result)

    @router.get("/{cam}/overlay.jpg")
    async def overlay(cam: str):
        dev = ctx.model_store.get_model().camera_device(cam)
        geom = CameraGeometry.from_device(dev)
        if geom is None:
            raise HTTPException(404, f"camera '{cam}' is not calibrated "
                                     "(intrinsics+extrinsics required)")
        edges_path = Path(ctx.data_dir) / "model" / "collision_edges.npz"
        if not edges_path.is_file():
            raise HTTPException(404, f"no collision edges at {edges_path}")
        try:
            with np.load(edges_path) as npz:
                key = "edges" if "edges" in npz.files else npz.files[0]
                edges = np.asarray(npz[key], dtype=np.float64)
            if edges.ndim != 3 or edges.shape[1:] != (2, 3):
                raise ValueError(f"expected Nx2x3 array, got {edges.shape}")
        except Exception as e:
            raise HTTPException(422, f"failed to load collision edges: {e}")
        data = await fetch_snapshot(cam)
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise HTTPException(502, "snapshot could not be decoded")
        render_overlay(frame, geom, edges)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise HTTPException(500, "jpeg encode failed")
        return Response(content=buf.tobytes(), media_type="image/jpeg")

    return router

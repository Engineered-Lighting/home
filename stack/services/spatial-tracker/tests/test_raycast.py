"""Back-projection pipeline: synthetic camera with known K/R/t.

Forward-project a known floor point to pixels with the same model
(cv2.projectPoints via CameraGeometry.project), then run the service
pipeline backwards (scale -> undistort -> ray -> plane) and require the
recovered position to match to < 1e-6 m (zero distortion; the round trip is
then exact up to float precision).  A separate test covers a distorted lens
at millimeter tolerance, since cv2.undistortPoints inverts distortion
iteratively.
"""
import math

import numpy as np

from calib import solve_extrinsics
from tracker import (CameraGeometry, R_to_quat_wxyz, localize_detection,
                     quat_wxyz_to_R)

K = [[900.0, 0.0, 640.0], [0.0, 900.0, 360.0], [0.0, 0.0, 1.0]]
IMAGE_SIZE = (1280, 720)


def look_at_R(C, target):
    """World->camera rotation, OpenCV convention (+Z forward, +Y down),
    for a camera at C looking at `target` in a Z-up world."""
    C = np.asarray(C, float)
    f = np.asarray(target, float) - C
    f = f / np.linalg.norm(f)
    up = np.array([0.0, 0.0, 1.0])
    r = np.cross(f, up)
    r = r / np.linalg.norm(r)
    d = np.cross(f, r)  # camera +Y (down) in world coords
    return np.stack([r, d, f])  # rows = camera axes in world frame


def make_geom(C=(0.0, 0.0, 2.5), target=(3.0, 0.0, 0.0), dist=None,
              detect_res=None):
    R = look_at_R(C, target)
    q = R_to_quat_wxyz(R)
    return CameraGeometry(K=K, dist=dist, image_size=IMAGE_SIZE, q_wxyz=q,
                          C=list(C), detect_res=detect_res)


def test_quat_roundtrip():
    R = look_at_R((0, 0, 2.5), (3, 1, 0))
    q = R_to_quat_wxyz(R)
    assert np.allclose(quat_wxyz_to_R(q), R, atol=1e-12)


def test_floor_point_recovered_exactly():
    geom = make_geom()
    X = np.array([3.0, 0.4, 0.0])
    px = geom.project(X)
    assert px is not None
    u, v = px
    assert 0 <= u <= IMAGE_SIZE[0] and 0 <= v <= IMAGE_SIZE[1]

    # bbox whose bottom-center is the foot pixel (detect_res == image_size)
    box = (u - 40.0, v - 180.0, u + 40.0, v)
    loc = localize_detection(geom, box, score=1.0, stationary=False)
    assert loc is not None
    assert loc.method == "feet"
    assert np.linalg.norm(loc.pos - X) < 1e-6


def test_detect_resolution_scaling():
    # detections at 640x360, intrinsics calibrated at 1280x720
    geom = make_geom(detect_res=(640, 360))
    X = np.array([2.5, -0.3, 0.0])
    px = geom.project(X)  # image-res pixel
    u, v = px[0] / 2.0, px[1] / 2.0  # same point in detect-res coords
    box = (u - 20.0, v - 90.0, u + 20.0, v)
    loc = localize_detection(geom, box, score=1.0, stationary=False)
    assert loc is not None
    assert np.linalg.norm(loc.pos - X) < 1e-6


def test_distorted_lens_recovered_to_millimeters():
    dist = [-0.28, 0.07, 0.001, -0.0005, 0.0]
    geom = make_geom(dist=dist)
    X = np.array([3.2, 0.6, 0.0])
    px = geom.project(X)  # projectPoints applies the distortion
    u, v = px
    box = (u - 40.0, v - 180.0, u + 40.0, v)
    loc = localize_detection(geom, box, score=1.0, stationary=False)
    assert loc is not None
    assert np.linalg.norm(loc.pos - X) < 1e-3


def test_grazing_ray_rejected():
    # nearly horizontal camera: the center ray has |d_z| < 0.05
    geom = make_geom(C=(0, 0, 1.6), target=(100.0, 0.0, 1.59))
    C, d = geom.pixel_to_ray(640, 360, from_detect=True)
    assert abs(d[2]) < 0.05
    assert geom.intersect_plane(d, z=0.0) is None
    box = (600.0, 200.0, 680.0, 360.0)
    assert localize_detection(geom, box, score=0.9, stationary=False) is None


def test_backward_ray_rejected():
    # camera tilted up: floor plane is behind the ray (s <= 0)
    geom = make_geom(C=(0, 0, 1.6), target=(3.0, 0.0, 3.0))
    _, d = geom.pixel_to_ray(640, 360)
    assert d[2] > 0.05
    assert geom.intersect_plane(d, z=0.0) is None


def test_seated_heuristic_uses_seat_plane():
    geom = make_geom()
    # tall bbox (h/w > 1.6) + stationary, bottom NOT at the frame edge
    box = (600.0, 250.0, 680.0, 500.0)  # w=80, h=250, aspect 3.125

    # plausible foot ray-cast -> NO seated fallback (stationary *standing*
    # person keeps the foot-point fix)
    loc_standing = localize_detection(geom, box, score=1.0, stationary=True)
    assert loc_standing is not None
    assert loc_standing.method == "feet"

    # implausible foot hit (e.g. outside the walkable hull) -> seated:
    # bbox-center ray ∩ z=0.45, dropped to floor, sigma * sqrt(3)
    cx, cy = 640.0, 375.0
    C, d = geom.pixel_to_ray(cx, cy)
    s = (0.45 - C[2]) / d[2]
    expected = C + s * d
    loc = localize_detection(geom, box, score=1.0, stationary=True,
                             plausible=lambda p: False)
    assert loc is not None
    assert loc.method == "seated"
    assert loc.pos[2] == 0.0  # dropped to the floor
    assert math.isclose(loc.pos[0], expected[0], abs_tol=1e-9)
    assert math.isclose(loc.pos[1], expected[1], abs_tol=1e-9)
    assert not np.allclose(loc.pos, loc_standing.pos)
    base = max(0.15, 0.04 * loc.range_m) * 0.5
    assert math.isclose(loc.sigma_xy, base * math.sqrt(3.0), rel_tol=1e-9)

    # walking (not stationary) never arms the fallback: the feet measurement
    # is still returned and the caller's walkable post-check rejects it
    loc2 = localize_detection(geom, box, score=1.0, stationary=False,
                              plausible=lambda p: False)
    assert loc2.method == "feet"


def test_cropped_feet_fallback_hip_plane_and_inflated_sigma():
    geom = make_geom()
    h = IMAGE_SIZE[1]
    # bbox bottom within 2% of the frame bottom -> cropped-feet fallback
    box = (600.0, 300.0, 700.0, h - 5.0)
    cx, cy = (600.0 + 700.0) / 2.0, (300.0 + h - 5.0) / 2.0
    C, d = geom.pixel_to_ray(cx, cy)
    s = (0.9 - C[2]) / d[2]
    expected = C + s * d
    loc = localize_detection(geom, box, score=1.0, stationary=False)
    assert loc is not None
    assert loc.method == "cropped"
    assert math.isclose(loc.pos[0], expected[0], abs_tol=1e-9)
    assert math.isclose(loc.pos[1], expected[1], abs_tol=1e-9)
    # sigma inflated by sqrt(3) relative to the same range/score baseline
    base = max(0.15, 0.04 * loc.range_m) * 0.5
    assert math.isclose(loc.sigma_xy, base * math.sqrt(3.0), rel_tol=1e-9)


def test_solve_extrinsics_recovers_pose():
    geom = make_geom(C=(0.5, -0.2, 2.4), target=(3.0, 0.5, 0.0))
    rng = np.random.default_rng(7)
    pairs = []
    for _ in range(60):
        X = np.array([rng.uniform(1.0, 6.0), rng.uniform(-2.0, 2.0),
                      rng.uniform(0.0, 1.2)])
        px = geom.project(X)
        if px is None:
            continue
        u, v = px
        if not (0 <= u <= IMAGE_SIZE[0] and 0 <= v <= IMAGE_SIZE[1]):
            continue
        pairs.append({"px": [float(u), float(v)],
                      "xyz": [float(c) for c in X]})
        if len(pairs) >= 12:
            break
    assert len(pairs) >= 6
    result = solve_extrinsics(pairs, K, None, IMAGE_SIZE)
    assert result["rms_px"] < 0.5
    assert np.allclose(result["C"], geom.C, atol=1e-3)
    R_est = quat_wxyz_to_R(result["q_wxyz"])
    assert np.allclose(R_est, geom.R, atol=1e-3)
    assert len(result["per_point_err_px"]) == len(pairs)

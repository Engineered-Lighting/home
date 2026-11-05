#!/usr/bin/env python3
"""Probe: does den-register's check-mount notice a camera that turned enough to break 5 cm?

check-mount compares an 8x8 grid of per-cell median depths (scene.mount_signature) of a
later frame with the registration frame's, using scene.check_mount and the method's
proposed values (50 mm or 3% of depth per cell, more than half of the compared cells).
A floor seen by a level camera has the same depth image after any turn about the
vertical axis through the camera, so a pure yaw is invisible on floor cells.

This probe ray-casts a synthetic box room (4.572 x 3.759 x 2.438 m, with a table-like
and a sofa-like box) from den-register's synthetic camera pose, then from the same pose
turned by small yaws (and a few other small motions), and runs the tool's own
mount_signature + check_mount on the two depth planes. For each motion it prints the
verdict and how far the OLD registration now misplaces points in the working region.

FAIL: check-mount says "unchanged" ("still holds") while the old registration is off
by more than the 5 cm gate.

Run:  python3 probe_mount-check-blind-to-yaw.py <rung-1 checkout root>
"""
import array
import math
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-register"))
import scene, synthetic  # noqa: E402

W, H = scene.WIDTH, scene.HEIGHT
K = synthetic.K
ROOM = (4.572, 3.759, 2.438)
BOXES = [((1.5, 1.5, 0.0), (2.5, 2.3, 0.75)), ((3.6, 0.5, 0.0), (4.5, 2.5, 0.85))]
PROPOSED = dict(tolerance_mm=50, tolerance_fraction=0.03, changed_fraction=0.5, minimum_cells=16)


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def rot_cam(rotation, axis, deg):
    """Turn about the camera's own axis (0 = x, pitch; 2 = z, roll)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = [[1, 0, 0], [0, c, -s], [0, s, c]] if axis == 0 else [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return matmul(rotation, m)


def ray_hit(o, d):
    best = math.inf
    for i in range(3):  # room walls, floor, ceiling (inside the box)
        if d[i] > 1e-12:
            best = min(best, (ROOM[i] - o[i]) / d[i])
        elif d[i] < -1e-12:
            best = min(best, (0.0 - o[i]) / d[i])
    for lo, hi in BOXES:  # slab test
        t0, t1 = -math.inf, math.inf
        for i in range(3):
            if abs(d[i]) < 1e-12:
                if not lo[i] <= o[i] <= hi[i]:
                    t0, t1 = 1, 0
                    break
                continue
            a, b = (lo[i] - o[i]) / d[i], (hi[i] - o[i]) / d[i]
            t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 <= t1 and t0 > 0:
            best = min(best, t0)
    return best


def render(rotation, t):
    plane = array.array("H", bytes(2 * W * H))
    for v in range(H):
        for u in range(W):
            dc = ((u - K[0][2]) / K[0][0], (v - K[1][2]) / K[1][1], 1.0)
            dr = [sum(rotation[i][j] * dc[j] for j in range(3)) for i in range(3)]
            s = ray_hit(t, dr)  # dc[2] == 1, so the ray parameter is the depth Z
            plane[v * W + u] = min(65535, int(round(s * 1000))) if math.isfinite(s) else 0
    return plane


def displacement(r_old, t_old, r_new, t_new, p):
    """Where the old registration puts a room point seen by the moved camera."""
    cam = [sum(r_new[j][i] * (p[j] - t_new[j]) for j in range(3)) for i in range(3)]
    q = [sum(r_old[i][j] * cam[j] for j in range(3)) + t_old[i] for i in range(3)]
    return math.dist(p, q)


r0, t0 = synthetic.camera_pose()
base = scene.mount_signature({"depth": render(r0, t0)})
work_points = {"room centre 1.0 m up": [2.3, 1.9, 1.0], "seat 0.5 m up": [3.2, 2.6, 0.5],
               "far floor": [4.0, 3.2, 0.0]}
motions = [("yaw 1.0 deg", rz(1.0), [0, 0, 0]), ("yaw 2.0 deg", rz(2.0), [0, 0, 0]),
           ("yaw 3.0 deg", rz(3.0), [0, 0, 0]), ("yaw 5.0 deg", rz(5.0), [0, 0, 0]),
           ("pitch 1.0 deg", "pitch", [0, 0, 0]), ("roll 2.0 deg", "roll", [0, 0, 0]),
           ("shift 8 cm along the wall", None, [0.08 * math.cos(math.radians(40)), -0.08 * math.sin(math.radians(40)), 0])]
fails = 0
for name, turn, shift in motions:
    if turn == "pitch":
        r1 = rot_cam(r0, 0, 1.0)
    elif turn == "roll":
        r1 = rot_cam(r0, 2, 2.0)
    elif turn is None:
        r1 = r0
    else:
        r1 = matmul(turn, r0)
    t1 = [t0[i] + shift[i] for i in range(3)]
    got = scene.check_mount(base, scene.mount_signature({"depth": render(r1, t1)}), **PROPOSED)
    worst = max(displacement(r0, t0, r1, t1, p) for p in work_points.values())
    each = ", ".join(f"{k} {displacement(r0, t0, r1, t1, p) * 100:.1f} cm" for k, p in work_points.items())
    verdict = "FAIL" if got["status"] == "unchanged" and worst > 0.05 else "ok"
    fails += verdict == "FAIL"
    print(f"{verdict:4} {name:28} check-mount {got['status']:9} ({got['changed_cells']} of "
          f"{got['compared_cells']} cells changed); old registration off by: {each}")
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} motions break the 5 cm gate while check-mount says unchanged")

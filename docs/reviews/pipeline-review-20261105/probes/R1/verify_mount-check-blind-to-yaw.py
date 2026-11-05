#!/usr/bin/env python3
"""Verifier probe for finding mount-check-blind-to-yaw (rung 1, den-register).

Independent re-check of the claim that check-mount says 'unchanged' for small turns
and slides that move the registration by more than 5 cm, and of the secondary claim
that tightening the open mount slots helps only through walls and furniture.

It ray-casts a synthetic box room (no real data) from den-register's synthetic camera
pose, then from moved poses, and runs the tool's OWN scene.mount_signature and
scene.check_mount on the depth planes, at the proposed slot values and at tighter ones.
It also checks the displacement numbers against the closed form for a pure yaw about
the vertical axis through the camera, |q - p| = 2 * r_xy * sin(theta / 2).

Run:  python3 -B verify_mount-check-blind-to-yaw.py <rung-1 checkout root>
Prints one RESULT line per motion and tolerance setting, then a summary RESULT.
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
# A more cluttered room than the original probe: table, sofa, shelf, armchair.
BOXES = [((1.5, 1.5, 0.0), (2.5, 2.3, 0.75)), ((3.6, 0.5, 0.0), (4.5, 2.5, 0.85)),
         ((0.0, 3.3, 0.0), (1.2, 3.759, 1.8)), ((2.8, 2.9, 0.0), (3.6, 3.7, 0.9))]
SETTINGS = {"proposed 50mm/3%/0.5": dict(tolerance_mm=50, tolerance_fraction=0.03, changed_fraction=0.5,
                                         minimum_cells=16),
            "tight 20mm/1%/0.25": dict(tolerance_mm=20, tolerance_fraction=0.01, changed_fraction=0.25,
                                       minimum_cells=16),
            "very tight 10mm/0.5%/0.1": dict(tolerance_mm=10, tolerance_fraction=0.005, changed_fraction=0.1,
                                             minimum_cells=16)}


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def ray_hit(o, d):
    best = math.inf
    for i in range(3):
        if d[i] > 1e-12:
            best = min(best, (ROOM[i] - o[i]) / d[i])
        elif d[i] < -1e-12:
            best = min(best, (0.0 - o[i]) / d[i])
    for lo, hi in BOXES:
        t0, t1 = -math.inf, math.inf
        ok = True
        for i in range(3):
            if abs(d[i]) < 1e-12:
                if not lo[i] <= o[i] <= hi[i]:
                    ok = False
                    break
                continue
            a, b = (lo[i] - o[i]) / d[i], (hi[i] - o[i]) / d[i]
            t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if ok and t0 <= t1 and t0 > 0:
            best = min(best, t0)
    return best


def render(rotation, t):
    plane = array.array("H", bytes(2 * W * H))
    for v in range(H):
        for u in range(W):
            dc = ((u - K[0][2]) / K[0][0], (v - K[1][2]) / K[1][1], 1.0)
            dr = [sum(rotation[i][j] * dc[j] for j in range(3)) for i in range(3)]
            s = ray_hit(t, dr)
            plane[v * W + u] = min(65535, int(round(s * 1000))) if math.isfinite(s) else 0
    return plane


def displacement(r_old, t_old, r_new, t_new, p):
    cam = [sum(r_new[j][i] * (p[j] - t_new[j]) for j in range(3)) for i in range(3)]
    q = [sum(r_old[i][j] * cam[j] for j in range(3)) + t_old[i] for i in range(3)]
    return math.dist(p, q)


r0, t0 = synthetic.camera_pose()
base = scene.mount_signature({"depth": render(r0, t0)})
unmoved = scene.mount_signature({"depth": render(r0, t0)})
centre = [2.3, 1.9, 1.0]
r_xy = math.hypot(centre[0] - t0[0], centre[1] - t0[1])
fails = 0
for name, r1, t1, theta in (("yaw 1 deg", matmul(rz(1.0), r0), t0, 1.0),
                            ("yaw 2 deg", matmul(rz(2.0), r0), t0, 2.0),
                            ("yaw 5 deg", matmul(rz(5.0), r0), t0, 5.0),
                            ("slide 8 cm", r0, [t0[0] + 0.0613, t0[1] - 0.0514, t0[2]], None)):
    moved = scene.mount_signature({"depth": render(r1, t1)})
    off = displacement(r0, t0, r1, t1, centre)
    closed = f", closed form {2 * r_xy * math.sin(math.radians(theta) / 2) * 100:.1f} cm" if theta else ""
    for label, kw in SETTINGS.items():
        got = scene.check_mount(base, moved, **kw)
        bad = got["status"] == "unchanged" and off > 0.05
        fails += bad
        print(f"RESULT {'FAIL' if bad else 'ok  '} {name:11} {label:26} -> {got['status']:9} "
              f"({got['changed_cells']}/{got['compared_cells']} cells); room centre 1 m up off by "
              f"{off * 100:.1f} cm{closed}")
for label, kw in SETTINGS.items():
    print(f"RESULT sanity unmoved {label}: {scene.check_mount(base, unmoved, **kw)['status']}")
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} (motion, setting) pairs say unchanged with > 5 cm error")

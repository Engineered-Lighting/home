"""Check the den OAK-D Pro's metric depth against two tape measurements, through the running oak-den.

Run on the den Jetson as the camera service user (oak-den keeps running):
    ~/victoria-camera/venv/bin/python -I depth_tape_check.py [--captures 8] [--floor-tape 1.37] [--wall-tape 4.27]

Each capture is the per-pixel median of 30 /frame.npz depth planes. Only depth_mm and metadata_json are read;
RGB and the mono planes are never decoded, nothing is saved, and the camera token is read from camera.env and
never printed. It fits the floor plane (lens height above it) and the facing wall's plane (distance square to
it), both perpendicular distances, so the camera's pitch and yaw do not matter. It prints numbers only:
per-capture plane distances, their means and errors against the tapes, the floor-wall angle (90 for a square
room; a uniform scale cannot change it), the depth_correction the camera declares, and the factor that would
split the two tapes evenly, relative to the depth as served.
"""
import argparse
import io
import json
import math
import os
import time
import urllib.request
import warnings

import numpy as np

ENV = os.path.expanduser('~/victoria-camera/private/camera.env')


def camera_env():
    out = {}
    with open(ENV) as f:
        for line in f:
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.rstrip('\n').split('=', 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def median_depth(frames):
    env = camera_env()
    url = 'http://127.0.0.1:%s/frame.npz' % env.get('HTTP_PORT', '8090')
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + env.get('VICTORIA_CAMERA_TOKEN', '')})
    env = None
    planes, K, declared, failures = [], None, None, 0
    while len(planes) < frames:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                with np.load(io.BytesIO(response.read()), allow_pickle=False) as bundle:
                    planes.append(bundle['depth_mm'].astype(np.float32))
                    metadata = json.loads(bytes(bundle['metadata_json']).decode())
            K, declared = metadata['frames']['depth']['intrinsics'], metadata.get('depth_correction')
        except Exception:
            failures += 1
            if failures > 30:
                raise SystemExit('camera did not serve depth (is oak-den running?)')
            time.sleep(.5)
        time.sleep(.25)
    stack = np.stack(planes)
    stack[stack <= 0] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return np.nanmedian(stack, axis=0) / 1000.0, K, declared


def ransac(points, accept, iterations=3000):
    rng = np.random.default_rng(1)
    tolerance = 0.015 + 0.006 * points[:, 2]
    best = None
    for _ in range(iterations):
        a, b, c = points[rng.integers(len(points), size=3)]
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal)
        if length < 1e-9:
            continue
        normal = normal / length
        if not accept(normal):
            continue
        count = int(np.sum(np.abs(points @ normal - normal @ a) < tolerance))
        if best is None or count > best[0]:
            best = (count, normal, float(normal @ a))
    _, normal, distance = best
    return (normal, distance) if distance > 0 else (-normal, -distance)


def refine(points, normal):
    """Densest 1 cm layer, then trimmed SVD: robust to a rug edge or furniture feet."""
    distance = float(np.median(points @ normal))
    for _ in range(3):
        heights = points @ normal
        near = np.abs(heights - distance) < 0.10
        counts, edges = np.histogram(heights[near], bins=np.arange(distance - 0.10, distance + 0.1001, 0.01))
        mode = edges[np.argmax(counts)] + 0.005
        layer = points[np.abs(heights - mode) < 0.015 + 0.006 * points[:, 2]]
        centre = layer.mean(0)
        fitted = np.linalg.svd(layer - centre, full_matrices=False)[2][2]
        normal = fitted if fitted @ normal > 0 else -fitted
        distance = float(normal @ centre)
    return normal, distance


def floor_and_wall(Z, K):
    h, w = Z.shape
    v, u = np.mgrid[0:h, 0:w]
    rays = np.stack([(u - K[0][2]) / K[0][0], (v - K[1][2]) / K[1][1], np.ones((h, w))], -1)
    ok = np.isfinite(Z) & (Z > 0.3) & (Z < 8.0)
    P = rays * np.where(ok, Z, np.nan)[..., None]
    low = ok & (P[..., 1] > 0.4)
    nf, df = ransac(P[low][::2], lambda n: abs(n[1]) > 0.85)
    nf, df = refine(P[low & (np.abs(P @ nf - df) < 0.10)], nf)
    candidates = ok & ((P @ nf) < df - 0.25) & (P[..., 2] > 3.0)
    nw, dw = ransac(P[candidates][::2], lambda n: abs(n[2]) > 0.7 and abs(n @ nf) < 0.25)
    nw, dw = refine(P[candidates & (np.abs(P @ nw - dw) < 0.15)], nw)
    return df, dw, math.degrees(math.acos(min(1.0, abs(float(nf @ nw)))))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--captures', type=int, default=8)
    parser.add_argument('--frames', type=int, default=30)
    parser.add_argument('--floor-tape', type=float, default=1.37, help='lens height above the floor, metres')
    parser.add_argument('--wall-tape', type=float, default=4.27, help='lens to the facing wall, square to it, metres')
    args = parser.parse_args()
    rows, declared = [], None
    for index in range(args.captures):
        Z, K, declared = median_depth(args.frames)
        floor, wall, angle = floor_and_wall(Z, K)
        rows.append((floor, wall, angle))
        print(json.dumps({'capture': index, 'floor_m': round(floor, 3), 'wall_m': round(wall, 3),
                          'floor_wall_deg': round(angle, 2)}), flush=True)
    floor, wall, angle = (float(np.mean([r[i] for r in rows])) for i in range(3))
    print(json.dumps({
        'declared_depth_correction': declared, 'captures': args.captures,
        'floor_m': round(floor, 3), 'floor_sd_m': round(float(np.std([r[0] for r in rows])), 3),
        'floor_error_pct': round(100 * (floor / args.floor_tape - 1), 1),
        'wall_m': round(wall, 3), 'wall_sd_m': round(float(np.std([r[1] for r in rows])), 3),
        'wall_error_pct': round(100 * (wall / args.wall_tape - 1), 1),
        'floor_wall_deg': round(angle, 2),
        'factor_splitting_tapes_relative_to_served': round(2 / (floor / args.floor_tape + wall / args.wall_tape), 4)}))


if __name__ == '__main__':
    main()

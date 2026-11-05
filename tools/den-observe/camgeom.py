"""Camera math for den-observe. Standard library only.

Follows perception-lab's depth_association.py (project, the 8-term rational
distortion model, the RGB-from-rectified-left rigid chain) and geometry.py
(deproject, the rigid-transform checks) without numpy.

Camera frames: x right, y down, z forward, metres. The rectified-left (depth)
camera has no distortion; the RGB camera has its own intrinsics and up to 8
distortion terms (k1 k2 p1 p2 k3 k4 k5 k6, OpenCV order).
"""
import math


def finite(*values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def mat_vec(m, v):
    return [m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def det3(r):
    return (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
            - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
            + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))


def check_rigid(rotation, translation, what="transform"):
    """A proper rotation (orthonormal, det +1) and a finite translation in metres."""
    if (not isinstance(rotation, list) or len(rotation) != 3
            or any(not isinstance(row, list) or len(row) != 3 or not finite(*row) for row in rotation)
            or not isinstance(translation, list) or len(translation) != 3 or not finite(*translation)):
        raise ValueError(f"{what}: 3x3 rotation and 3-vector translation required")
    for i in range(3):
        for j in range(3):
            dot = sum(rotation[k][i] * rotation[k][j] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > 1e-4:
                raise ValueError(f"{what}: rotation must be orthonormal")
    if abs(det3(rotation) - 1.0) > 1e-4:
        raise ValueError(f"{what}: a reflection is not a rigid transform")
    if max(abs(v) for v in translation) > 100:
        raise ValueError(f"{what}: translation must be in metres")


def from_matrix(matrix, what="transform"):
    """(rotation, translation) from a 4x4 row-major homogeneous matrix."""
    if (not isinstance(matrix, list) or len(matrix) != 4
            or any(not isinstance(row, list) or len(row) != 4 for row in matrix)
            or matrix[3] != [0, 0, 0, 1]):
        raise ValueError(f"{what}: 4x4 homogeneous matrix required")
    rotation, translation = [list(row[:3]) for row in matrix[:3]], [row[3] for row in matrix[:3]]
    check_rigid(rotation, translation, what)
    return rotation, translation


def apply(rotation, translation, point):
    p = mat_vec(rotation, point)
    return [p[0] + translation[0], p[1] + translation[1], p[2] + translation[2]]


def inverse(rotation, translation):
    rt = transpose(rotation)
    t = mat_vec(rt, translation)
    return rt, [-t[0], -t[1], -t[2]]


def intrinsics(value, what="intrinsics"):
    """(fx, fy, cx, cy, skew) from a 3x3 matrix or an {fx, fy, cx, cy} object."""
    if isinstance(value, dict):
        fx, fy, cx, cy = (value.get(k) for k in ("fx", "fy", "cx", "cy"))
        skew = value.get("skew", 0.0)
    elif (isinstance(value, list) and len(value) == 3
          and all(isinstance(row, list) and len(row) == 3 for row in value)):
        (fx, skew, cx), (zero, fy, cy), last = value
        if not finite(zero, *last) or zero != 0 or list(last) != [0, 0, 1]:
            raise ValueError(f"{what}: pinhole matrix required")
    else:
        raise ValueError(f"{what}: 3x3 matrix or fx/fy/cx/cy required")
    if not finite(fx, fy, cx, cy, skew) or fx <= 0 or fy <= 0:
        raise ValueError(f"{what}: positive finite focal lengths required")
    return float(fx), float(fy), float(cx), float(cy), float(skew)


def distortion(value):
    terms = [float(v) for v in (value or [])]
    if len(terms) > 14 or not finite(*terms) or any(abs(v) > 1e-12 for v in terms[8:]):
        raise ValueError("only the 8-term rational distortion model is supported")
    return (terms + [0.0] * 8)[:8]


def distort(x, y, d):
    k1, k2, p1, p2, k3, k4, k5, k6 = d
    r2 = x * x + y * y
    radial = (1 + r2 * (k1 + r2 * (k2 + r2 * k3))) / (1 + r2 * (k4 + r2 * (k5 + r2 * k6)))
    return (x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x),
            y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y)


def project(point, k, d=None):
    """Camera point -> pixel (u, v), or None behind the camera. As depth_association.project."""
    if point[2] <= 1e-6:
        return None
    fx, fy, cx, cy, skew = k
    x, y = point[0] / point[2], point[1] / point[2]
    if d is not None:
        x, y = distort(x, y, d)
    return fx * x + skew * y + cx, fy * y + cy


def undistort(u, v, k, d=None, iterations=20):
    """Pixel -> normalized ray (x, y, 1). Fixed-point inversion as OpenCV's undistortPoints."""
    fx, fy, cx, cy, skew = k
    yd = (v - cy) / fy
    xd = (u - cx - skew * yd) / fx
    if d is None or not any(d):
        return [xd, yd, 1.0]
    k1, k2, p1, p2, k3, k4, k5, k6 = d
    x, y = xd, yd
    for _ in range(iterations):
        r2 = x * x + y * y
        icdist = (1 + r2 * (k4 + r2 * (k5 + r2 * k6))) / (1 + r2 * (k1 + r2 * (k2 + r2 * k3)))
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        x, y = (xd - dx) * icdist, (yd - dy) * icdist
    return [x, y, 1.0]


def deproject(u, v, z, k):
    """Rectified-left pixel at depth z (metres) -> camera point. As geometry.deproject."""
    fx, fy, cx, cy, skew = k
    y = (v - cy) / fy
    return [(u - cx - skew * y) / fx * z, y * z, z]


def ray_plane(origin, direction, normal, offset):
    """Point where origin + s*direction (s > 0) meets normal . p = offset, or None."""
    denominator = sum(n * d for n, d in zip(normal, direction))
    if abs(denominator) < 1e-9:
        return None
    s = (offset - sum(n * o for n, o in zip(normal, origin))) / denominator
    if s <= 0:
        return None
    return [o + s * d for o, d in zip(origin, direction)], s


def unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v] if n > 1e-12 else None


def cov_rotate(rotation, cov):
    """R C R^T for a 3x3 covariance."""
    return mat_mul(mat_mul(rotation, cov), transpose(rotation))


def cov_add(*covs):
    return [[sum(c[i][j] for c in covs) for j in range(3)] for i in range(3)]


def isotropic(sigma_m):
    s2 = sigma_m * sigma_m
    return [[s2, 0.0, 0.0], [0.0, s2, 0.0], [0.0, 0.0, s2]]


def ray_covariance(direction, sigma_along_m, sigma_across_m):
    """Covariance with sigma_along along a unit direction and sigma_across perpendicular to it."""
    u = unit(direction)
    a2, c2 = sigma_along_m ** 2, sigma_across_m ** 2
    return [[c2 * ((1.0 if i == j else 0.0) - u[i] * u[j]) + a2 * u[i] * u[j] for j in range(3)] for i in range(3)]


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        raise ValueError("median of nothing")
    middle = n // 2
    return ordered[middle] if n % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def quantile(values, q):
    """Linear-interpolation quantile, as numpy.quantile's default."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

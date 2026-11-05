"""Rigid camera-to-room fit for rung 1. Standard library only.

Follows perception-lab's spatial_validation.fit_rigid: a least-squares rigid
fit (no scale, det +1) from paired 3D points, its residuals, a second-principal
spread test against collinear points, and a leave-one-out bound so one point
cannot hide its own error. The rotation is Kabsch's least-squares answer,
computed with Horn's unit-quaternion form (a 4x4 symmetric eigenproblem), so
no numpy is needed and the result is always a proper rotation.

Points are [x, y, z] in metres. A transform is (R, t) with room = R @ cam + t.
"""
import math


def _check_points(points, name):
    if type(points) is not list or not points:
        raise ValueError(f"{name}: a non-empty list of points")
    for point in points:
        if (type(point) not in (list, tuple) or len(point) != 3
                or not all(type(v) in (int, float) and math.isfinite(v) for v in point)):
            raise ValueError(f"{name}: each point is three finite numbers")
    return [[float(v) for v in point] for point in points]


def centroid(points):
    n = len(points)
    return [sum(p[i] for p in points) / n for i in range(3)]


def jacobi_eigen(matrix, sweeps=100):
    """Eigenvalues and unit eigenvectors (columns) of a small symmetric matrix."""
    n = len(matrix)
    a = [list(map(float, row)) for row in matrix]
    v = [[float(i == j) for j in range(n)] for i in range(n)]
    scale = max(1e-300, max(abs(x) for row in a for x in row))
    for _ in range(sweeps):
        off = math.sqrt(sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j))
        if off <= 1e-15 * scale:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) <= 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    values = [a[i][i] for i in range(n)]
    vectors = [[v[k][i] for k in range(n)] for i in range(n)]
    return values, vectors


def spread(points):
    """RMS spread along the second principal direction, in metres.

    Zero for collinear points; below a floor it means the points cannot fix a
    rotation about the line through them.
    """
    points = _check_points(points, "points")
    if len(points) < 2:
        return 0.0
    c = centroid(points)
    d = [[p[i] - c[i] for i in range(3)] for p in points]
    cov = [[sum(q[i] * q[j] for q in d) / len(d) for j in range(3)] for i in range(3)]
    values = sorted(jacobi_eigen(cov)[0], reverse=True)
    return math.sqrt(max(0.0, values[1]))


def quaternion_matrix(w, x, y, z):
    return [[w * w + x * x - y * y - z * z, 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), w * w - x * x + y * y - z * z, 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), w * w - x * x - y * y + z * z]]


def kabsch(source, target):
    """(R, t) minimising sum |R s + t - t'|^2 over proper rotations."""
    source, target = _check_points(source, "source"), _check_points(target, "target")
    if len(source) != len(target) or len(source) < 3:
        raise ValueError("at least three paired points")
    cs, ct = centroid(source), centroid(target)
    s = [[0.0] * 3 for _ in range(3)]
    for p, q in zip(source, target):
        for i in range(3):
            for j in range(3):
                s[i][j] += (p[i] - cs[i]) * (q[j] - ct[j])
    (xx, xy, xz), (yx, yy, yz), (zx, zy, zz) = s
    n = [[xx + yy + zz, yz - zy, zx - xz, xy - yx],
         [yz - zy, xx - yy - zz, xy + yx, zx + xz],
         [zx - xz, xy + yx, -xx + yy - zz, yz + zy],
         [xy - yx, zx + xz, yz + zy, -xx - yy + zz]]
    values, vectors = jacobi_eigen(n)
    w, x, y, z = vectors[max(range(4), key=values.__getitem__)]
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    rotation = quaternion_matrix(w / norm, x / norm, y / norm, z / norm)
    translation = [ct[i] - sum(rotation[i][j] * cs[j] for j in range(3)) for i in range(3)]
    return rotation, translation


def apply(rotation, translation, point):
    return [sum(rotation[i][j] * point[j] for j in range(3)) + translation[i] for i in range(3)]


def matrix4(rotation, translation):
    return [list(rotation[i]) + [translation[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def split4(matrix):
    if (type(matrix) is not list or len(matrix) != 4
            or any(type(row) is not list or len(row) != 4 for row in matrix)
            or not all(type(v) in (int, float) and math.isfinite(v) for row in matrix for v in row)
            or [float(v) for v in matrix[3]] != [0.0, 0.0, 0.0, 1.0]):
        raise ValueError("a 4x4 rigid matrix with last row 0 0 0 1")
    return [[float(v) for v in row[:3]] for row in matrix[:3]], [float(row[3]) for row in matrix[:3]]


def is_rotation(rotation, tolerance=1e-6):
    for i in range(3):
        for j in range(3):
            dot = sum(rotation[k][i] * rotation[k][j] for k in range(3))
            if abs(dot - (i == j)) > tolerance:
                return False
    (a, b, c), (d, e, f), (g, h, i) = rotation
    return abs(a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g) - 1) <= tolerance


def fit(source, target):
    """Fit, residuals and leave-one-out errors (None with fewer than four pairs).

    With three pairs, leaving one out leaves two, which cannot fix a rotation,
    so the bound is not computed rather than guessed.
    """
    rotation, translation = kabsch(source, target)
    residuals = [math.dist(apply(rotation, translation, p), q) for p, q in zip(source, target)]
    loo = None
    if len(source) >= 4:
        loo = []
        for index in range(len(source)):
            keep = [k for k in range(len(source)) if k != index]
            r, t = kabsch([source[k] for k in keep], [target[k] for k in keep])
            loo.append(math.dist(apply(r, t, source[index]), target[index]))
    return {"rotation": rotation, "translation": translation, "residuals_m": residuals,
            "rms_m": math.sqrt(sum(r * r for r in residuals) / len(residuals)),
            "max_m": max(residuals), "loo_m": loo, "loo_max_m": None if loo is None else max(loo)}

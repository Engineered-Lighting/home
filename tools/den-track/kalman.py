"""A constant-velocity Kalman filter on the room floor plane (x, y in metres).

State [x, y, vx, vy]. Plain lists, standard library only, deterministic.
"""
import math


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def transpose(a):
    return [list(r) for r in zip(*a)]


def mat_add(a, b):
    return [[x + y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def mat_sub(a, b):
    return [[x - y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def symmetric(a):
    n = len(a)
    return [[(a[i][j] + a[j][i]) / 2 for j in range(n)] for i in range(n)]


def inv2(m):
    (a, b), (c, d) = m
    det = a * d - b * c
    if not det > 0:
        raise ValueError('covariance is not positive definite')
    return [[d / det, -b / det], [-c / det, a / det]], det


H = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
HT = transpose(H)


def process_noise(dt, accel_sigma):
    """Continuous white-noise acceleration, spectral density accel_sigma ** 2."""
    q = accel_sigma ** 2
    a, b, c = q * dt ** 3 / 3, q * dt ** 2 / 2, q * dt
    return [[a, 0, b, 0], [0, a, 0, b], [b, 0, c, 0], [0, b, 0, c]]


def predict(x, P, dt, accel_sigma, velocity_keep=1.0):
    """Predict dt seconds ahead. velocity_keep < 1 lets a coasting track slow
    down instead of sailing on (a person who stops behind the armchair)."""
    # The mean slows down; the covariance does not shrink with it, so the gate
    # still covers a person who kept walking.
    F = [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]]
    x2 = [x[0] + dt * x[2], x[1] + dt * x[3], velocity_keep * x[2], velocity_keep * x[3]]
    P2 = symmetric(mat_add(mat_mul(mat_mul(F, P), transpose(F)), process_noise(dt, accel_sigma)))
    return x2, P2


def innovation(x, P, z, R):
    """Innovation nu, its covariance S, S's inverse and determinant."""
    nu = [z[0] - x[0], z[1] - x[1]]
    S = [[P[0][0] + R[0][0], P[0][1] + R[0][1]], [P[1][0] + R[1][0], P[1][1] + R[1][1]]]
    S_inv, det = inv2(S)
    return nu, S, S_inv, det


def mahalanobis2(nu, S_inv):
    return (nu[0] * (S_inv[0][0] * nu[0] + S_inv[0][1] * nu[1])
            + nu[1] * (S_inv[1][0] * nu[0] + S_inv[1][1] * nu[1]))


def gaussian2(d2, det):
    """Density of a 2-D normal at Mahalanobis distance squared d2."""
    return math.exp(-0.5 * d2) / (2 * math.pi * math.sqrt(det))


def update(x, P, z, R):
    """Joseph-form update; returns the new (x, P)."""
    nu, S, S_inv, _ = innovation(x, P, z, R)
    PHt = mat_mul(P, HT)
    K = mat_mul(PHt, S_inv)
    x2 = [x[i] + K[i][0] * nu[0] + K[i][1] * nu[1] for i in range(4)]
    I_KH = mat_sub([[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)], mat_mul(K, H))
    P2 = mat_add(mat_mul(mat_mul(I_KH, P), transpose(I_KH)), mat_mul(mat_mul(K, R), transpose(K)))
    return x2, symmetric(P2)


def initial(z, R, speed_sigma):
    """A new track at z with unknown velocity."""
    v = speed_sigma ** 2
    return [z[0], z[1], 0.0, 0.0], [[R[0][0], R[0][1], 0, 0], [R[1][0], R[1][1], 0, 0], [0, 0, v, 0], [0, 0, 0, v]]

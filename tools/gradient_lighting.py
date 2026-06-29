#!/usr/bin/env python3
"""gradient_lighting.py — pure maths for Phase 3 gradient lighting.

Imported by build-living-lights-actuators.py (footprint-centroid baking at
generate time, the AR-3 separation check) and unit-tested by
tools/test-gradient-lighting.py.

The runtime falloff is re-implemented in the generated Jinja template;
gradient_factors() here is the reference spec that template must match.
"""

from __future__ import annotations

import math

# Gaussian falloff length (normalised frame). 0.6 — not the 0.3 the plan
# first guessed: the drawn sofa-light footprint centroids span ~0.33-0.60
# apart, and at lambda 0.3 the falloff is so sharp every light but the
# person's own floors out (near-binary). 0.6 yields a real 4-way gradient.
# Tunable live via input_number.living_lights_gradient_lambda.
GRADIENT_LAMBDA_DEFAULT = 0.6
GRADIENT_FLOOR_DEFAULT = 0.40    # a far light never drops below floor x base
# AR-3: if the brightest-light centroids are closer than this the gradient
# would mostly amplify position noise — flag the footprints for a redraw.
MIN_SEPARATION_WARN = 0.08


def polygon_centroid(poly):
    """Area-weighted (shoelace) centroid of a normalised polygon. Falls back
    to the vertex mean for a degenerate (zero-area or <3-vertex) polygon;
    None for empty input. Mirrors home-spatial-helpers.js polygonCentroid."""
    if not poly:
        return None
    pts = [(float(p[0]), float(p[1])) for p in poly
           if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None

    def _mean():
        sx = sum(p[0] for p in pts)
        sy = sum(p[1] for p in pts)
        return [round(sx / len(pts), 3), round(sy / len(pts), 3)]

    if len(pts) < 3:
        return _mean()
    a = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return _mean()
    return [round(cx / (3 * a), 3), round(cy / (3 * a), 3)]


def min_centroid_separation(centroids):
    """Smallest pairwise distance among centroid points (a dict or list of
    [x, y]). The AR-3 build-time check keys off this."""
    if isinstance(centroids, dict):
        pts = [p for p in centroids.values() if p]
    else:
        pts = [p for p in centroids if p]
    best = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if best is None or d < best:
                best = d
    return best


def gradient_factors(px, py, centroids,
                     lam=GRADIENT_LAMBDA_DEFAULT,
                     floor=GRADIENT_FLOOR_DEFAULT):
    """Per-light brightness factor in [floor, 1.0].

    centroids : {light_id: [cx, cy]} in the normalised [0,1] camera frame.
    px, py    : the person's normalised position; pass None or a negative
                value when position is unknown.

    Gaussian falloff exp(-d^2 / lam^2), then normalised so the nearest
    light is exactly 1.0 and every light is floored at `floor`. Position
    unknown -> every light 1.0 (uniform — exactly today's behaviour)."""
    ids = list(centroids.keys())
    if not ids or px is None or py is None or px < 0 or py < 0:
        return {i: 1.0 for i in ids}
    raw = {}
    for i in ids:
        c = centroids[i]
        d2 = (px - c[0]) ** 2 + (py - c[1]) ** 2
        raw[i] = math.exp(-d2 / (lam * lam))
    mx = max(raw.values())
    if mx <= 0:
        return {i: 1.0 for i in ids}
    return {i: max(floor, raw[i] / mx) for i in ids}

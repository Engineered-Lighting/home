#!/usr/bin/env python3
"""test-gradient-lighting.py — unit tests for the Phase 3 gradient-lighting
maths (tools/gradient_lighting.py).

Covers the shoelace centroid, the centroid-separation check, and the
falloff: nearest light -> 1.0, far light -> floor, centred person ->
near-uniform, position unknown / multi-person -> uniform. Exit 0 = pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "gradient_lighting", HERE / "gradient_lighting.py")
gl = importlib.util.module_from_spec(_spec)             # type: ignore
_spec.loader.exec_module(gl)                            # type: ignore

_passed = 0
_failed = 0


def check(name, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ───────────────────────── polygon_centroid ───────────────────────────

c = gl.polygon_centroid([[0, 0], [1, 0], [1, 1], [0, 1]])
check("polygon_centroid: unit square -> (0.5, 0.5)",
      c == [0.5, 0.5], str(c))
c = gl.polygon_centroid([[0, 0], [0.6, 0], [0, 0.6]])
check("polygon_centroid: right triangle -> (0.2, 0.2)",
      c == [0.2, 0.2], str(c))
check("polygon_centroid: <3 verts -> vertex mean",
      gl.polygon_centroid([[0, 0], [1, 1]]) == [0.5, 0.5])
check("polygon_centroid: empty -> None",
      gl.polygon_centroid([]) is None)
check("polygon_centroid: degenerate (collinear) -> vertex mean",
      gl.polygon_centroid([[0, 0], [1, 1], [2, 2]]) == [1.0, 1.0])

# ─────────────────────── min_centroid_separation ──────────────────────

check("min_centroid_separation: picks the closest pair",
      abs(gl.min_centroid_separation(
          {"a": [0, 0], "b": [0.3, 0], "c": [0.31, 0]}) - 0.01) < 1e-9,
      str(gl.min_centroid_separation(
          {"a": [0, 0], "b": [0.3, 0], "c": [0.31, 0]})))

# ── gradient_factors — real drawn sofa-light centroids (spatial_model) ─

SOFA = {
    "light.front_left":  [0.359, 0.896],
    "light.front_right": [0.233, 0.581],
    "light.rear_left":   [0.807, 0.488],
    "light.rear_right":  [0.489, 0.368],
}

# Position unknown -> uniform (every light 1.0).
f = gl.gradient_factors(-1, -1, SOFA)
check("gradient_factors: position unknown -> all 1.0 (uniform)",
      all(abs(v - 1.0) < 1e-9 for v in f.values()), str(f))
f = gl.gradient_factors(None, None, SOFA)
check("gradient_factors: None position -> all 1.0 (uniform)",
      all(abs(v - 1.0) < 1e-9 for v in f.values()), str(f))

# Person right at front_left's footprint centroid.
f = gl.gradient_factors(0.359, 0.896, SOFA)
check("gradient_factors: nearest light anchors at exactly 1.0",
      abs(f["light.front_left"] - 1.0) < 1e-9, str(f))
check("gradient_factors: every factor within [floor, 1.0]",
      all(gl.GRADIENT_FLOOR_DEFAULT - 1e-9 <= v <= 1.0 + 1e-9
          for v in f.values()), str(f))
check("gradient_factors: the farthest light is dimmed to the floor",
      abs(f["light.rear_left"] - gl.GRADIENT_FLOOR_DEFAULT) < 1e-9,
      str(f))
check("gradient_factors: near light strictly brighter than far light",
      f["light.front_left"] > f["light.front_right"] > f["light.rear_left"],
      str(f))

# Person at the rear_left end — the gradient must flip.
f2 = gl.gradient_factors(0.807, 0.488, SOFA)
check("gradient_factors: move to rear_left -> rear_left now 1.0",
      abs(f2["light.rear_left"] - 1.0) < 1e-9
      and f2["light.front_left"] < 0.6, str(f2))

# The user's sofa symptom: near-end vs far-end discrimination.
near = gl.gradient_factors(0.30, 0.85, SOFA)   # near front_left end
check("gradient_factors: sit at one end -> that end's light brightest",
      max(near, key=near.get) == "light.front_left", str(near))

# Multi-light edge cases.
check("gradient_factors: single light -> 1.0",
      gl.gradient_factors(0.1, 0.1, {"x": [0.9, 0.9]}) == {"x": 1.0})
check("gradient_factors: empty centroids -> empty",
      gl.gradient_factors(0.5, 0.5, {}) == {})

# floor is honoured even with a tiny lambda (very sharp falloff).
f3 = gl.gradient_factors(0.359, 0.896, SOFA, lam=0.05, floor=0.25)
check("gradient_factors: custom floor respected under sharp falloff",
      min(f3.values()) >= 0.25 - 1e-9
      and abs(f3["light.front_left"] - 1.0) < 1e-9, str(f3))

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(0 if _failed == 0 else 1)

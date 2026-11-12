"""How long one fixture move takes: a symmetric S-curve on one shared path parameter (coordinator plan C7).

The fixture's board (firmware v2) moves both axes along one shared path parameter s(t), 0 to 1, limited by
each axis's speed, acceleration and jerk, so both axes arrive together and the path is a straight line in
(pan, tilt). This file predicts that time from a fixture profile's per-axis limits. When the firmware chat
ships its reference duration function, it replaces scurve_time behind move_time's signature.

Standard library only. Pure: no clock, file or network.
"""
import math


def _time_to_speed(v, a, j):
    """Time to go from rest to speed v with acceleration limit a and jerk limit j (and back, by symmetry)."""
    return v / a + a / j if v >= a * a / j else 2.0 * math.sqrt(v / j)


def scurve_time(distance, v, a, j):
    """Seconds for a rest-to-rest move of `distance` with speed v, acceleration a and jerk j limits."""
    if distance <= 0:
        return 0.0
    if not (v > 0 and a > 0 and j > 0):
        raise ValueError("speed, acceleration and jerk limits must all be above 0")
    t_v = _time_to_speed(v, a, j)
    if distance >= v * t_v:  # reaches v: accelerate, cruise, decelerate
        return 2.0 * t_v + (distance - v * t_v) / v
    low, high = 0.0, v  # never reaches v: find the peak speed whose up-and-down covers the distance
    for _ in range(100):
        peak = (low + high) / 2.0
        if peak * _time_to_speed(peak, a, j) < distance:
            low = peak
        else:
            high = peak
    return 2.0 * _time_to_speed(high, a, j)


def move_time(delta_deg, axes):
    """Seconds for one move of (d_pan, d_tilt) degrees. axes: two {v_max, a_max, j_max} dicts (deg/s, deg/s^2,
    deg/s^3), pan first. The shared parameter is limited by whichever axis is tighter for this move."""
    moving = [(abs(d), axis) for d, axis in zip(delta_deg, axes) if abs(d) > 1e-9]
    if not moving:
        return 0.0
    v = min(axis["v_max"] / d for d, axis in moving)
    a = min(axis["a_max"] / d for d, axis in moving)
    j = min(axis["j_max"] / d for d, axis in moving)
    return scurve_time(1.0, v, a, j)


# The made-up profile for sockets with no real fixture (plan C4). Labelled fictional wherever it's used.
FICTIONAL_AXIS = {"v_max": 30.0, "a_max": 100.0, "j_max": 400.0}
FICTIONAL_PROFILE = {
    "label": "fictional-v2", "fictional": True,
    "axes": [dict(FICTIONAL_AXIS), dict(FICTIONAL_AXIS)],
    "range": {"pan": [-175.0, 175.0], "tilt": [-85.0, 85.0]},
    "lit_motion": True, "retarget_mid_move": False,
    "path_margin_cdeg": 50, "settle_s": 0.2, "beam_fwhm_deg": 20.0, "lumens": 450.0,
}

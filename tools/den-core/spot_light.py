"""How bright a spot is at a point, and the level that's enough (coordinator plan C6).

A port of Home's beam formula (app/src/home-3d/lit-model.mjs: candelaFromLumens, spotHalfAngle,
litDimmingFraction) and of den-sim's spotValue: candela from assumed lumens over the beam's solid angle,
times the cosine of incidence on the point's normal, over distance squared. Relative units, never lux:
the lumens are assumed until a fixture is measured.

Level rule (goals Q2, fixing Codex 19): the lowest level whose predicted value reaches the target, or full
when even full is short, with the shortfall reported.

Standard library only. Pure.
"""
import math

SPOT_HALF_ANGLE_PER_FWHM_DEG = 0.8
ASSUMED_SPOT_LUMENS = 450.0
UP = (0.0, 0.0, 1.0)
LEVELS = (1, 28, 57, 85, 113, 142, 170, 198, 200, 227, 254)  # den-sim's solve levels, read as Hue's 0-254
FULL = 254
_KAPPA, _EPSILON = 24389.0 / 27.0, 216.0 / 24389.0


def candela_from_lumens(lumens, half_angle_rad):
    solid = 2.0 * math.pi * (1.0 - math.cos(half_angle_rad))
    return lumens / solid if solid > 0 and math.isfinite(lumens) else 0.0


def spot_half_angle(fwhm_deg):
    return max(1.0, fwhm_deg or 20.0) * SPOT_HALF_ANGLE_PER_FWHM_DEG * math.pi / 180.0


def dimming_fraction(level):
    """Relative output at a 0-1 level: CIE lightness read inversely (lit-model.mjs's assumed curve)."""
    s = min(1.0, max(0.0, level)) if math.isfinite(level) else 0.0
    lightness = 100.0 * s
    return ((lightness + 16.0) / 116.0) ** 3 if lightness > _KAPPA * _EPSILON else lightness / _KAPPA


def spot_value(origin, point, normal=UP, lumens=ASSUMED_SPOT_LUMENS, fwhm_deg=20.0):
    """Relative light at `point` from a spot at full, aimed at it."""
    d = [point[i] - origin[i] for i in range(3)]
    d2 = sum(c * c for c in d)
    r = math.sqrt(d2)
    if r <= 0:
        return 0.0
    cos_incidence = max(0.0, -sum(d[i] * normal[i] for i in range(3)) / r)
    return candela_from_lumens(lumens, spot_half_angle(fwhm_deg)) * cos_incidence / max(d2, 0.01)


def level_for(full_value, target):
    """(level 0-1, predicted value, shortfall): the lowest level reaching target, else full."""
    for b in LEVELS:
        value = full_value * dimming_fraction(b / FULL)
        if value >= target:
            return b / FULL, value, 0.0
    return 1.0, full_value, max(0.0, target - full_value)

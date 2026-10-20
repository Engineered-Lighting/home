"""Capture-state de-lighting: clamped albedo from captured radiance and capture irradiance.

The scan stores radiance, not albedo. For a Lambertian receiver

    rho = pi * R_cap / max(E_cap, eps)

where E_cap is the capture-state irradiance. Spike 1 showed that iterating
albedo <-> bounce oscillates (39 % median albedo error), while *gathering the
captured colours themselves* as the capture-state indirect light,
E_cap_ind = G . R_cap, gives 5 % median error. Every backend in this package
therefore builds E_cap as

    E_cap = E_known_direct + E_window + gather(R_cap)

and this module turns it into albedo with the spike's recommended settings:
eps = 1-3 % of median irradiance, a physical albedo clamp, and a flag on every
receiver whose albedo hits the clamp ("unexplained light").
"""
from __future__ import annotations

import numpy as np

from .encode import lum

# Bit flags, one byte per receiver (documented in lighting-manifest.json).
FLAGS = {
    "unexplained_light": 1,   # albedo hit the upper clamp: more light than the model explains
    "clipped": 2,             # captured colour clipped; radiance is a lower bound
    "emitter": 4,             # window glass / exterior: an emitter, not a reflector
    "off_surface": 8,         # no scan surface within the projection tolerance
    "low_irradiance": 16,     # E_cap below the eps floor; albedo under-determined
    "open_hemisphere": 32,    # many gather rays escaped the scan (holes)
    "neighbor_fill": 64,      # albedo replaced from surface neighbours (clipped input)
}

DEFAULT_EPS_REL = 0.02
DEFAULT_RHO_MAX = 0.95


def recover_albedo(R_cap, E_cap, eps_rel=DEFAULT_EPS_REL, rho_max=DEFAULT_RHO_MAX, clipped=None, floor=None):
    """Per-channel clamped albedo and flags.

    R_cap, E_cap: (N,3) linear, same (arbitrary) radiometric unit up to pi.
    eps_rel: floor on E_cap as a fraction of the median irradiance luminance.
    Returns (rho (N,3) float64, flags (N,) uint8, stats dict).
    """
    R = np.clip(np.asarray(R_cap, np.float64), 0, None)
    E = np.clip(np.asarray(E_cap, np.float64), 0, None)
    yE = lum(E)
    med = float(np.median(yE[yE > 0])) if np.any(yE > 0) else 0.0
    floor = max(eps_rel * med, 1e-12) if floor is None else float(floor)  # shared floor across sets
    rho = np.pi * R / np.maximum(E, floor)
    high = (rho > rho_max).any(1)
    rho = np.clip(rho, 0, rho_max)
    flags = np.zeros(len(R), np.uint8)
    flags[high] |= FLAGS["unexplained_light"]
    flags[yE < floor] |= FLAGS["low_irradiance"]
    if clipped is not None:
        flags[np.asarray(clipped, bool)] |= FLAGS["clipped"]
    stats = {"median_irradiance": med, "eps_floor": floor, "eps_rel": eps_rel, "rho_max": rho_max,
             "clamp_hit_fraction": float(high.mean()) if len(R) else 0.0,
             "low_irradiance_fraction": float((yE < floor).mean()) if len(R) else 0.0}
    return rho, flags, stats


def neighbor_fill(rho, flags, positions, normals, bad_mask, k=12, radius=0.10, min_cos=0.8):
    """Replace albedo of `bad_mask` receivers by the median of good surface neighbours.

    Neighbours must be within `radius` metres and face the same way. Receivers
    with no good neighbour keep their clamped albedo. Sets the neighbor_fill flag.
    """
    from scipy.spatial import cKDTree

    bad = np.asarray(bad_mask, bool)
    good = ~bad
    if not bad.any() or not good.any():
        return rho, flags
    tree = cKDTree(positions[good])
    gi = np.nonzero(good)[0]
    bi = np.nonzero(bad)[0]
    d, j = tree.query(positions[bi], k=k, distance_upper_bound=radius)
    rho = rho.copy()
    flags = flags.copy()
    for r, (dd, jj) in enumerate(zip(np.atleast_2d(d), np.atleast_2d(j))):
        ok = np.isfinite(dd)
        if not ok.any():
            continue
        cand = gi[jj[ok]]
        cand = cand[(normals[cand] @ normals[bi[r]]) >= min_cos]
        if len(cand):
            rho[bi[r]] = np.median(rho[cand], axis=0)
            flags[bi[r]] |= FLAGS["neighbor_fill"]
    return rho, flags


# ------------------------------------------------------------------ metrics
def log_rmse(new, truth, delta_frac=0.01):
    """Spike 1's luminance logRMSE with a floor of 1 % of the mean target luminance."""
    Yt, Ye = lum(truth), lum(new)
    d = delta_frac * float(Yt.mean())
    lr = np.log(Ye + d) - np.log(Yt + d)
    return float(np.sqrt((lr ** 2).mean()))


def stop_error(new, ref, floor_frac=0.01):
    """Per-receiver |log2(lum(new)/lum(ref))| with a small floor; returns the array."""
    a, b = lum(new), lum(ref)
    f = floor_frac * float(np.median(b[b > 0])) if np.any(b > 0) else 1e-12
    return np.abs(np.log2((a + f) / (b + f)))

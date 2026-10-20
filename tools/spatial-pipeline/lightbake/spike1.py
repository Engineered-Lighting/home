"""Reproduce lighting spike 1 (exact case) through the L3 tool's own code path.

The synthetic room supplies ground truth. The tool's pieces under test are:

- estimator.recover_albedo on E_cap = known direct + gather(captured colours),
  the measured-radiance gather that spike 1 found decisive;
- the 40 B/receiver encoding (encode.encode_receivers / decode_receivers);
- relighting from the decoded layer, and re-rendering the capture state.

Gate (L3): exact-case logRMSE <= 0.2 on the relight targets, and the
capture-state re-render within 0.25 stop median of the shipped colours.

Run:  python -I -m lightbake.spike1   (from tools/spatial-pipeline)
"""
from __future__ import annotations

import json
import time

import numpy as np

from . import synthetic as S
from .encode import decode_receivers, encode_receivers, lum, relight, srgb_eotf, srgb_oetf, column_scales
from .estimator import log_rmse, recover_albedo, stop_error

TARGETS = ("one_spot", "night_ambient", "all_on_warm")


def capture_camera(R, rng):
    """Linear radiance -> stored 8-bit sRGB colour, as spike 1: auto exposure (p98 = 1),
    shot + read noise, sRGB OETF, 8-bit quantisation, clipping."""
    expo = 1.0 / np.percentile(lum(R), 98)
    x = R * expo
    x = x + rng.normal(size=x.shape) * (np.sqrt(0.0004 * np.maximum(x, 0)) + 0.0015)
    enc = np.round(np.clip(srgb_oetf(np.maximum(x, 0)), 0, 1) * 255) / 255
    return enc, expo


def reproduce(n=12000, n_patches=600, bounces=6, seed=11):
    t0 = time.time()
    sc = S.Scene()
    sp = sc.sample(n)
    P, Nrm, alb = sp["p"], sp["n"], sp["alb"]
    pidx = np.random.default_rng(5).choice(n, n_patches, replace=False)
    parea = sp["area"] * n / n_patches
    V = S.gather_vis(sc, P, Nrm, P[pidx], Nrm[pidx])
    D = S.direct_basis(sc, P, Nrm, sc.lights)
    T = S.patch_transport(sc, P[pidx], Nrm[pidx], parea)
    G = S.gather_matrix(P, Nrm, P[pidx], Nrm[pidx], parea, V)

    # ground truth (6-bounce radiosity with the true albedo)
    B_true = S.full_basis(P, Nrm, D, P[pidx], Nrm[pidx], parea, T, D[pidx], alb[pidx], G=G, bounces=bounces)
    W = {t: S.state(t, sc.lights) for t in ("capture",) + TARGETS}
    R_true = {t: alb / np.pi * S.irradiance(B_true, *W[t]) for t in W}

    enc, expo = capture_camera(R_true["capture"], np.random.default_rng(seed))
    R_cap = srgb_eotf(enc) / expo                     # shipped colours, linearised
    clipped = enc.max(1) >= 1.0

    # tool: capture irradiance = known direct + gather of the captured colours
    E_direct = S.irradiance(np.repeat(D[:, :, None], 3, 2), *W["capture"])
    E_cap = E_direct + S.gather_vis_apply(G, R_cap[pidx])
    rho, flags, stats = recover_albedo(R_cap, E_cap, clipped=clipped)

    # tool: basis with the recovered albedo, then the 40 B encoding
    B_est = S.full_basis(P, Nrm, D, P[pidx], Nrm[pidx], parea, T, D[pidx], rho[pidx], G=G, bounces=bounces)
    Bs = lum(B_est)
    cs = column_scales(Bs)
    rec = encode_receivers(rho, Nrm, Bs, B_est, flags, cs)
    dec = decode_receivers(rec, cs)
    n_bytes = sum(int(v.nbytes) for v in rec.values())

    out = {"n": n, "n_patches": n_patches, "bounces": bounces,
           "clipped_fraction": float(clipped.mean()),
           "albedo_err_median": float(np.median(np.abs(lum(rho) / lum(alb) - 1))),
           "albedo_err_p90": float(np.percentile(np.abs(lum(rho) / lum(alb) - 1), 90)),
           "clamp_hit_fraction": stats["clamp_hit_fraction"],
           "bytes_per_receiver": n_bytes / n, "targets": {}}
    for t in TARGETS:
        w, c = W[t]
        exact = rho / np.pi * S.irradiance(B_est, w, c)
        out["targets"][t] = {"logrmse_exact": log_rmse(exact, R_true[t]),
                             "logrmse_encoded": log_rmse(relight(dec, w, c), R_true[t])}
    recap = relight(dec, *W["capture"])
    se = stop_error(recap, R_cap)
    out["capture_rerender_median_stop"] = float(np.median(se))
    out["capture_rerender_p90_stop"] = float(np.percentile(se, 90))
    out["seconds"] = round(time.time() - t0, 1)
    return out


if __name__ == "__main__":
    print(json.dumps(reproduce(), indent=1))

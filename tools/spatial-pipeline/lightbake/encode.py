"""Byte encodings of the additive lighting layer (L3 -> L4 contract).

Per receiver (splat, point) the layer stores exactly 40 bytes:

    albedo        3 B  rgb8, sRGB-encoded linear albedo
    normal-oct    2 B  octahedral unit normal, 2 x u8
    basis-u8     29 B  sqrt-encoded scalar basis, one byte per column
    basis-scale   2 B  fp16 per-receiver scale
    bounce-tint   3 B  rgb8 chroma of the all-on basis sum
    flags         1 B  bit flags (see estimator.FLAGS)

Decoding (what the runtime blend does):

    b_k   = (u8_k / 255)^2 * scale * column_scale_k
    tint  = t / lum(t),  t = tint_u8 / 255
    E     = tint * sum_k w_k * c_k * b_k            (RGB, c_k = emitter colour)
    L_out = albedo / pi * E

The format is spike 2's winner ("scalar u8 sqrt + scale + tint", 40 B/splat,
0.8 % median luminance error, dE2000 1.54 on the synthetic room).
"""
from __future__ import annotations

import numpy as np

LUM = np.array([0.2126, 0.7152, 0.0722], np.float64)
FP16_MAX = 65504.0


def lum(rgb):
    rgb = np.asarray(rgb)
    return rgb[..., 0] * LUM[0] + rgb[..., 1] * LUM[1] + rgb[..., 2] * LUM[2]


def srgb_oetf(x):
    x = np.clip(np.asarray(x, np.float64), 0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def srgb_eotf(v):
    v = np.asarray(v, np.float64)
    return np.where(v <= 0.04045, v / 12.92, np.power((np.maximum(v, 0) + 0.055) / 1.055, 2.4))


# ------------------------------------------------------------------ albedo
def encode_albedo(rho):
    """Linear albedo (N,3) in [0,1] -> sRGB-encoded u8 (N,3)."""
    return np.round(np.clip(srgb_oetf(np.clip(rho, 0, 1)), 0, 1) * 255).astype(np.uint8)


def decode_albedo(u8):
    return srgb_eotf(np.asarray(u8, np.float64) / 255.0)


# ------------------------------------------------------------------ normals
def encode_normal_oct(n):
    """Unit normals (N,3) -> octahedral u8 (N,2)."""
    n = np.asarray(n, np.float64)
    n = n / np.maximum(np.abs(n).sum(1, keepdims=True), 1e-12)
    x, y, z = n[:, 0], n[:, 1], n[:, 2]
    ox = np.where(z >= 0, x, (1 - np.abs(y)) * np.where(x >= 0, 1, -1))
    oy = np.where(z >= 0, y, (1 - np.abs(x)) * np.where(y >= 0, 1, -1))
    return np.round((np.stack([ox, oy], 1) * 0.5 + 0.5) * 255).astype(np.uint8)


def decode_normal_oct(u8):
    o = np.asarray(u8, np.float64) / 255.0 * 2 - 1
    x, y = o[:, 0], o[:, 1]
    z = 1 - np.abs(x) - np.abs(y)
    t = np.clip(-z, 0, None)
    x = x + np.where(x >= 0, -t, t)
    y = y + np.where(y >= 0, -t, t)
    v = np.stack([x, y, z], 1)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


# ------------------------------------------------------------------ basis
def column_scales(B, q=95.0):
    """Global per-column normalisation so mixed-unit columns quantise alike."""
    B = np.asarray(B, np.float64)
    out = np.ones(B.shape[1])
    for k in range(B.shape[1]):
        nz = B[:, k][B[:, k] > 0]
        if len(nz):
            out[k] = float(np.percentile(nz, q))
    return np.where(out > 0, out, 1.0)


def encode_basis(B, col_scale):
    """Scalar basis (N,K) >= 0 -> (u8 (N,K), fp16 scale (N,)).

    The per-receiver scale is rounded up to fp16 before quantising, so every
    code is <= 255 and decoding never overshoots the stored row maximum.
    """
    Bn = np.clip(np.asarray(B, np.float64), 0, None) / np.asarray(col_scale, np.float64)[None, :]
    s = Bn.max(1)
    s16 = np.minimum(s, FP16_MAX).astype(np.float16)
    low = s16.astype(np.float64) < s
    s16[low] = np.nextafter(s16[low], np.float16(np.inf))
    sf = s16.astype(np.float64)
    safe = np.where(sf > 0, sf, 1.0)
    q = np.round(np.sqrt(np.clip(Bn / safe[:, None], 0, 1)) * 255)
    q[sf <= 0] = 0
    return q.astype(np.uint8), s16


def decode_basis(u8, scale16, col_scale):
    return (np.asarray(u8, np.float64) / 255.0) ** 2 * np.asarray(scale16, np.float64)[:, None] \
        * np.asarray(col_scale, np.float64)[None, :]


# ------------------------------------------------------------------ tint
def bounce_tint(B_rgb, col_scale):
    """All-on chroma per receiver: sum_k B_k,rgb / sum_k lum(B_k), columns normalised."""
    Bn = np.asarray(B_rgb, np.float64) / np.asarray(col_scale, np.float64)[None, :, None]
    s = Bn.sum(1)
    y = lum(s)
    out = np.ones_like(s)
    ok = y > 0
    out[ok] = s[ok] / y[ok, None]
    return out


def encode_tint(tint):
    t = np.clip(np.asarray(tint, np.float64), 0, None)
    m = t.max(1, keepdims=True)
    t = np.where(m > 0, t / np.maximum(m, 1e-12), 1.0)
    return np.round(t * 255).astype(np.uint8)


def decode_tint(u8):
    t = np.asarray(u8, np.float64) / 255.0
    y = lum(t)
    out = np.ones_like(t)
    ok = y > 0
    out[ok] = t[ok] / y[ok, None]
    return out


# ------------------------------------------------------------------ whole record
RECORD_FIELDS = (("albedo", 3), ("normal-oct", 2), ("basis-u8", None), ("basis-scale", 2),
                 ("bounce-tint", 3), ("flags", 1))


def bytes_per_receiver(n_columns):
    return 3 + 2 + n_columns + 2 + 3 + 1


def encode_receivers(rho, normals, B, B_rgb, flags, col_scale):
    """Encode one receiver set. Returns {field: ndarray} ready to write as raw bytes."""
    u8, s16 = encode_basis(B, col_scale)
    return {
        "albedo": encode_albedo(rho),
        "normal-oct": encode_normal_oct(normals),
        "basis-u8": u8,
        "basis-scale": s16.astype("<f2"),
        "bounce-tint": encode_tint(bounce_tint(B_rgb, col_scale)),
        "flags": np.asarray(flags, np.uint8),
    }


def decode_receivers(rec, col_scale):
    return {
        "albedo": decode_albedo(rec["albedo"]),
        "normal": decode_normal_oct(rec["normal-oct"]),
        "basis": decode_basis(rec["basis-u8"], rec["basis-scale"], col_scale),
        "tint": decode_tint(rec["bounce-tint"]),
        "flags": np.asarray(rec["flags"], np.uint8),
    }


def relight(decoded, weights, colours):
    """Decoded layer + per-column weight (K,) and colour (K,3) -> outgoing radiance (N,3)."""
    E = decoded["basis"] @ (np.asarray(weights, np.float64)[:, None] * np.asarray(colours, np.float64))
    return decoded["albedo"] / np.pi * decoded["tint"] * E

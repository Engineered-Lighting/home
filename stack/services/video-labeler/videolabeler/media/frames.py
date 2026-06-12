"""PyAV frame decode helpers for the M2 perception/keyframe pipeline.

EVERY heavy import (av, numpy, PIL) is inside a function: the API process
and the Windows test venv import this module's callers without ever pulling
the perception stack. Tests mock at the jobs layer and never call these.

Conventions:
  * Sampling for perception reads the 480p PROXY when ready (already
    rotation-normalized by ffmpeg autorotate); the ORIGINAL fallback and all
    keyframe decode apply rotation_deg here (PyAV does NOT autorotate).
  * rotation_deg follows the ffmpeg rotate tag: degrees the stored frame
    must be rotated CLOCKWISE for correct display -> np.rot90 with negative
    k (np.rot90 is counter-clockwise).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("videolabeler.media.frames")

MOTION_GRAY_W = 64          # downsample width for abs-diff motion energy
SHARPNESS_SCORE_W = 320     # downscale width for Laplacian scoring only


def rotate_frame(arr, rotation_deg: int):
    """Rotation-correct an HxWxC (or HxW) ndarray per the rotate-tag
    convention above."""
    import numpy as np
    k = (-(rotation_deg or 0) // 90) % 4
    return np.rot90(arr, k) if k else arr


def laplacian_var(gray) -> float:
    """Sharpness score: variance of a 4-neighbor Laplacian (pure numpy --
    no cv2/scipy dependency)."""
    g = gray.astype("float32")
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
           - 4.0 * g[1:-1, 1:-1])
    return float(lap.var())


def iter_sampled_frames(path: str, rotation_deg: int = 0,
                        sample_fps: float = 1.0):
    """Yield (t_seconds, rgb HxWx3 uint8, gray_small HxW float32) at
    ~sample_fps over the whole file, decoding sequentially (no seeks)."""
    import av
    step = 1.0 / max(sample_fps, 1e-6)
    with av.open(path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        next_t = 0.0
        for frame in container.decode(stream):
            t = frame.time
            if t is None or t + 1e-6 < next_t:
                continue
            next_t = t + step
            rgb = frame.to_ndarray(format="rgb24")
            gh = max(1, round(MOTION_GRAY_W * frame.height / max(frame.width, 1)))
            gray = frame.reformat(width=MOTION_GRAY_W, height=gh,
                                  format="gray").to_ndarray().astype("float32")
            yield t, rotate_frame(rgb, rotation_deg), rotate_frame(gray, rotation_deg)


def motion_fraction(gray_a, gray_b, threshold: float = 15.0) -> float:
    """Fraction of downsampled pixels whose abs diff exceeds threshold (of
    255) -- a 0..1 'how much of the scene moved' number that actually spans
    the range for in-home motion (a raw mean-abs-diff/255 hugs 0)."""
    import numpy as np
    if gray_a.shape != gray_b.shape:
        return 0.0
    return float(np.mean(np.abs(gray_a - gray_b) > threshold))


def extract_keyframes(path: str, rotation_deg: int, times: list,
                      sharp_radius_s: float = 0.5) -> list:
    """Exact-seek keyframe decode from the ORIGINAL at native res: for each
    requested t, decode [t - radius, t + radius] and keep the sharpest frame
    (Laplacian variance on a downscaled gray). Returns [(chosen_t, rgb), ...]
    in input order; entries the decoder could not reach fall back to the
    nearest decoded frame, or are skipped when nothing decodes."""
    import av
    out = []
    with av.open(path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        tb = stream.time_base
        for want in times:
            lo, hi = max(0.0, want - sharp_radius_s), want + sharp_radius_s
            container.seek(int(lo / tb), stream=stream)
            best, best_score, nearest, nearest_dt = None, -1.0, None, None
            for frame in container.decode(stream):
                t = frame.time
                if t is None:
                    continue
                if t > hi:
                    break
                dt = abs(t - want)
                if nearest_dt is None or dt < nearest_dt:
                    nearest, nearest_dt = (t, frame), dt
                if t < lo:
                    continue
                small = frame.reformat(
                    width=SHARPNESS_SCORE_W,
                    height=max(1, round(SHARPNESS_SCORE_W * frame.height
                                        / max(frame.width, 1))),
                    format="gray").to_ndarray()
                score = laplacian_var(small)
                if score > best_score:
                    best, best_score = (t, frame), score
            pick = best or nearest
            if pick is None:
                log.warning("no decodable frame near t=%.2fs in %s", want, path)
                continue
            t, frame = pick
            rgb = rotate_frame(frame.to_ndarray(format="rgb24"), rotation_deg)
            out.append((float(t), rgb))
    return out


def square_person_crop(rgb, bbox_norm, margin: float = 1.8):
    """Square crop around a normalized [x1,y1,x2,y2] person bbox with the
    given margin on the bbox's long side, clamped to the frame. Returns the
    cropped ndarray (None on a degenerate bbox)."""
    h, w = rgb.shape[:2]
    x1, y1, x2, y2 = bbox_norm
    bw, bh = (x2 - x1) * w, (y2 - y1) * h
    if bw <= 1 or bh <= 1:
        return None
    side = max(bw, bh) * margin
    cx, cy = (x1 + x2) / 2 * w, (y1 + y2) / 2 * h
    half = side / 2
    left = int(max(0, min(cx - half, w - side)))
    top = int(max(0, min(cy - half, h - side)))
    right = int(min(w, left + side))
    bottom = int(min(h, top + side))
    if right - left < 2 or bottom - top < 2:
        return None
    return rgb[top:bottom, left:right]


def save_jpeg(rgb, path: str, max_long_side: int, quality: int = 88) -> None:
    """Downscale (long side <= max_long_side) + write JPEG via PIL (a
    transitive ultralytics dep -- no extra requirement). .part + rename so a
    crash never leaves a half-written file the API would serve."""
    from PIL import Image
    img = Image.fromarray(rgb)
    long_side = max(img.size)
    if long_side > max_long_side:
        scale = max_long_side / long_side
        img = img.resize((max(1, round(img.width * scale)),
                          max(1, round(img.height * scale))),
                         Image.LANCZOS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    img.save(part, format="JPEG", quality=quality)
    os.replace(part, path)

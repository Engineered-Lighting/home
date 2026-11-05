"""What rung 1 reads from an R take. Standard library only.

From a take folder it reads take.json's schema and cues, frames.csv, and one
bundle that frames.csv lists (its sha256 checked). From that bundle it reads
the rectified left image, the depth plane (aligned to the rectified left
camera, not to RGB) and the metadata, through R's own .npy reader. Nothing
here opens a socket.
"""
import array
import csv
import hashlib
import io
import json
import math
import re
import sys
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEN_TAKE = HERE.parent / "den-take"
if str(DEN_TAKE) not in sys.path:
    sys.path.insert(0, str(DEN_TAKE))
import check  # noqa: E402  R's bundle and metadata reader

WIDTH, HEIGHT = 640, 400  # rectified left and depth planes
FRAME_FILE = re.compile(r"frames/[0-9]{6}-[0-9]+\.npz")
TAKE_SCHEMA = "den-take-1"


class Refused(Exception):
    """A named reason the registration cannot go on. args: (reason, detail)."""

    def __init__(self, reason, detail=""):
        super().__init__(reason, detail)
        self.reason, self.detail = reason, detail

    def __str__(self):
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---- the take -----------------------------------------------------------------

def read_take(folder):
    """take.json (finished, schema den-take-1) and frames.csv rows."""
    folder = Path(folder)
    try:
        take = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        with open(folder / "frames.csv", newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        frames = [{"n": int(r["n"]), "file": r["file"], "sha256": r["npz_sha256"],
                   "recv": float(r["recv_pc_mono_s"])} for r in rows]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("take_unreadable", "take.json or frames.csv is missing or malformed") from None
    if type(take) is not dict or take.get("schema") != TAKE_SCHEMA:
        raise Refused("take_unreadable", f"take.json schema is not {TAKE_SCHEMA}")
    if not take.get("cues") or take.get("ended_unix") is None:
        raise Refused("take_unreadable", "take.json was never finished; take.py stopped before saving")
    if not frames or [f["n"] for f in frames] != list(range(1, len(frames) + 1)):
        raise Refused("take_unreadable", "frames.csv frame numbers are not 1, 2, 3, ...")
    if not all(FRAME_FILE.fullmatch(f["file"]) for f in frames):
        raise Refused("take_unreadable", "frames.csv names a file outside the take's frames folder")
    return take, frames


def default_frame(take, frames):
    """The frame received nearest the middle of the activity part (begin to end slate)."""
    cues = {c.get("cue"): c.get("pc_mono_s") for c in take.get("cues") or [] if type(c) is dict}
    start, end = cues.get("begin"), cues.get("hold_phone_end")
    if type(start) not in (int, float) or type(end) not in (int, float) or end <= start:
        return frames[len(frames) // 2]["n"]
    middle = (start + end) / 2
    return min(frames, key=lambda f: abs(f["recv"] - middle))["n"]


# ---- one bundle ---------------------------------------------------------------

def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _intrinsics(frame):
    k = frame.get("intrinsics")
    if (type(k) is not list or len(k) != 3 or any(type(r) is not list or len(r) != 3 for r in k)
            or not all(_finite(v) for r in k for v in r)):
        return None
    fx, fy, cx, cy = float(k[0][0]), float(k[1][1]), float(k[0][2]), float(k[1][2])
    if fx <= 0 or fy <= 0 or k[0][1] != 0 or k[1][0] != 0 or [float(v) for v in k[2]] != [0.0, 0.0, 1.0]:
        return None
    return {"fx": fx, "fy": fy, "cx": cx, "cy": cy}


def read_bundle(data):
    """{meta, left (bytes), depth (array 'H'), intrinsics, left_frame, depth_frame} of one bundle.

    Refuses with a named reason: bundle_unreadable, intrinsics_missing, or
    frame_depth_different_times (the left image and the depth plane must be
    the same stereo frame; the click is on the left image).
    """
    try:
        meta = check.read_bundle(data)[0]  # R's checks: schema 1, session, rgb clock fields
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = {}
            for name, descr, shape in (("metadata_json.npy", "|u1", None),
                                       ("depth_mm.npy", "<u2", (HEIGHT, WIDTH)),
                                       ("left.npy", "|u1", (HEIGHT, WIDTH))):
                if archive.getinfo(name).file_size > 1 << 21:
                    raise check.BundleError(f"{name} is too large")
                got_descr, got_shape, body = check._npy(archive.read(name), name)
                if got_descr != descr or (shape and got_shape != shape):
                    raise check.BundleError(f"{name} is not {descr} {shape}")
                members[name] = body
        full = json.loads(members["metadata_json.npy"].decode("utf-8"))
    except (check.BundleError, zipfile.BadZipFile, KeyError, EOFError, zlib.error, ValueError) as exc:
        raise Refused("bundle_unreadable", str(exc) or type(exc).__name__) from None
    if (full.get("depth_alignment") != "rectified_left" or full.get("depth_units") != "millimetres"
            or full.get("invalid_depth") != 0):
        raise Refused("bundle_unreadable", "depth must be millimetres aligned to rectified_left, 0 invalid")
    frames = full.get("frames") if type(full.get("frames")) is dict else {}
    left, depth = frames.get("left"), frames.get("depth")
    if type(left) is not dict or type(depth) is not dict:
        raise Refused("bundle_unreadable", "metadata lacks the left or depth frame")
    for name, frame in (("left", left), ("depth", depth)):
        if (type(frame.get("sequence")) is not int or not _finite(frame.get("device_timestamp_seconds"))
                or (frame.get("width"), frame.get("height")) != (WIDTH, HEIGHT)):
            raise Refused("bundle_unreadable", f"{name} frame sequence, time or size missing")
    k = _intrinsics(depth) if depth.get("transformation_valid") is True else None
    if k is None:
        raise Refused("intrinsics_missing", "the depth frame carries no valid intrinsics")
    depth_plane = array.array("H")
    depth_plane.frombytes(members["depth_mm.npy"])
    if sys.byteorder == "big":
        depth_plane.byteswap()
    return {"meta": meta, "full": full, "left": members["left.npy"], "depth": depth_plane, "intrinsics": k,
            "left_frame": left, "depth_frame": depth}


def frame_depth_skew(bundle, max_skew_s):
    """Refuse a left image and a depth plane from different stereo frames."""
    left, depth = bundle["left_frame"], bundle["depth_frame"]
    skew = abs(left["device_timestamp_seconds"] - depth["device_timestamp_seconds"])
    if left["sequence"] != depth["sequence"] or skew > max_skew_s:
        raise Refused("frame_depth_different_times",
                      f"left #{left['sequence']} and depth #{depth['sequence']} are {skew * 1000:.1f} ms apart")
    return skew


def load_frame(folder, n):
    """(frame row, raw bundle bytes, parsed bundle) for frame n of a take."""
    take, frames = read_take(folder)
    row = next((f for f in frames if f["n"] == n), None)
    if row is None:
        raise Refused("frame_missing", f"frame {n} is not in frames.csv")
    try:
        data = (Path(folder) / row["file"]).read_bytes()
    except OSError:
        raise Refused("frame_missing", f"{row['file']} cannot be read") from None
    if sha256_bytes(data) != row["sha256"]:
        raise Refused("bundle_unreadable", f"frame {n}'s bundle does not match frames.csv")
    return take, row, data, read_bundle(data)


# ---- depth at a click ------------------------------------------------------------

def depth_at(bundle, u, v, *, window_px, min_valid_fraction):
    """Median depth (m) of valid pixels in a (2w+1)^2 window around (u, v), or Refused depth_missing."""
    if not (_finite(u) and _finite(v) and 0 <= u < WIDTH and 0 <= v < HEIGHT):
        raise Refused("click_outside_frame", f"({u}, {v}) is outside the {WIDTH}x{HEIGHT} frame")
    w, cu, cv = int(window_px), int(round(u)), int(round(v))
    plane, values, total = bundle["depth"], [], 0
    for y in range(cv - w, cv + w + 1):
        for x in range(cu - w, cu + w + 1):
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                total += 1
                value = plane[y * WIDTH + x]
                if value > 0:
                    values.append(value)
    if not values or len(values) < min_valid_fraction * total:
        raise Refused("depth_missing", f"{len(values)} of {total} pixels near ({u:.1f}, {v:.1f}) have depth")
    values.sort()
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {"z_m": median / 1000, "valid": len(values), "pixels": total,
            "spread_mm": values[-1] - values[0]}


def back_project(k, u, v, z_m):
    """Camera-frame point (rectified left: +x right, +y down, +z forward), metres."""
    return [(u - k["cx"]) * z_m / k["fx"], (v - k["cy"]) * z_m / k["fy"], z_m]


# ---- mount signature (spatial_validation.mount_signature, without numpy) ---------

def mount_signature(bundle, grid=(8, 8)):
    """Per-cell median of valid depth (mm), or None where under half the cell has depth."""
    rows, cols = grid
    plane, cells = bundle["depth"], []
    for r in range(rows):
        row = []
        y0, y1 = r * HEIGHT // rows, (r + 1) * HEIGHT // rows
        for c in range(cols):
            x0, x1 = c * WIDTH // cols, (c + 1) * WIDTH // cols
            values = sorted(v for y in range(y0, y1) for v in plane[y * WIDTH + x0:y * WIDTH + x1] if v > 0)
            size = (y1 - y0) * (x1 - x0)
            if len(values) >= 0.5 * size:
                m = len(values) // 2
                row.append(float(values[m]) if len(values) % 2 else (values[m - 1] + values[m]) / 2)
            else:
                row.append(None)
        cells.append(row)
    return {"grid": [rows, cols], "shape": [HEIGHT, WIDTH], "cells": cells}


def check_mount(signature, current, *, tolerance_mm, tolerance_fraction, changed_fraction, minimum_cells):
    """'changed', 'no_large_change' or 'unknown' (too few comparable cells), as in spatial_validation.

    A coarse alarm only. A turn or slide that moves room points by 5 to 40 cm can
    leave most cells' depth alone, so 'no_large_change' never says the camera stayed.
    """
    if current["shape"] != signature.get("shape") or current["grid"] != signature.get("grid"):
        return {"status": "changed", "reason": "depth_shape_changed", "compared_cells": 0, "changed_cells": 0}
    compared = changed = 0
    for reference_row, current_row in zip(signature["cells"], current["cells"]):
        for reference, value in zip(reference_row, current_row):
            if reference is None or value is None:
                continue
            compared += 1
            changed += abs(value - reference) > max(tolerance_mm, tolerance_fraction * reference)
    if compared < minimum_cells:
        status = "unknown"
    else:
        status = "changed" if changed > changed_fraction * compared else "no_large_change"
    return {"status": status, "compared_cells": compared, "changed_cells": changed}

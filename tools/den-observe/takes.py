"""Read an R take (den-take-1) for den-observe. Standard library only.

Reuses den-take's own bundle reader (check.py: zipfile and a small .npy header
parser, no numpy, no pickle) and adds the depth plane: depth_mm.npy, uint16,
640x400, millimetres, aligned to the rectified-left camera, 0 = invalid.
Every frame's RGB and depth times are the bundle's host-monotonic timestamps.
Only take.json, frames.csv, the newest check-N.json and the bundles are read.

Clock. Observations need Unix capture times; a take has host-monotonic ones.
Take.clock() gives unix = t_host + offset_s. With a PASS check whose start
slate was read, offset_s = -c + k * slate_period_s, where c is CHECK-V1's
offset (camera minus phone, phone time = Date.now()) and the whole periods k
come from frames.csv's arrival times (recv_pc_unix), which are within a few
seconds. Without one, offset_s is the median arrival offset, which places
capture late by the transport latency; such a run cannot be scored.
"""
import array
import csv
import io
import json
import re
import sys
import zipfile
import zlib
from pathlib import Path

import textfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "den-take"))
import check  # noqa: E402  (tools/den-take/check.py, unchanged)

TAKE_SCHEMA = check.TAKE_SCHEMA
DEPTH_SHAPE = (400, 640)  # rows, columns
BundleError = check.BundleError


class TakeRefused(Exception):
    """The take cannot be read; nothing is written."""


class Depth:
    """One rectified-left depth frame: millimetres, 0 = invalid."""

    __slots__ = ("width", "height", "mm", "t_host", "sequence")

    def __init__(self, width, height, mm, t_host, sequence):
        if len(mm) != width * height:
            raise ValueError("depth size does not match its shape")
        self.width, self.height, self.mm, self.t_host, self.sequence = width, height, mm, t_host, sequence

    def at(self, u, v):
        if 0 <= u < self.width and 0 <= v < self.height:
            return self.mm[v * self.width + u]
        return 0


def depth_from_bytes(body):
    values = array.array("H")
    values.frombytes(body)
    if sys.byteorder == "big":
        values.byteswap()
    return values


def read_frame(data):
    """(metadata dict, Depth) from one saved bundle's bytes."""
    meta, _ = check.read_bundle(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            raw_meta = json.loads(check._npy(archive.read("metadata_json.npy"), "metadata_json.npy")[2])
            if archive.getinfo("depth_mm.npy").file_size > 1 << 21:
                raise BundleError("depth_mm is too large")
            descr, shape, body = check._npy(archive.read("depth_mm.npy"), "depth_mm.npy")
    except (zipfile.BadZipFile, KeyError, EOFError, zlib.error) as exc:
        raise BundleError(f"bad bundle archive: {type(exc).__name__}") from None
    if descr != "<u2" or shape != DEPTH_SHAPE:
        raise BundleError("depth_mm must be 400x640 uint16")
    if raw_meta.get("depth_units") != "millimetres" or raw_meta.get("invalid_depth") != 0:
        raise BundleError("depth must be millimetres with 0 as invalid")
    if raw_meta.get("depth_alignment") != "rectified_left":
        raise BundleError("depth must be aligned to the rectified-left camera")
    plane = (raw_meta.get("frames") or {}).get("depth")
    if type(plane) is not dict:
        raise BundleError("metadata has no depth frame")
    t_depth, sequence = plane.get("host_monotonic_timestamp_seconds"), plane.get("sequence")
    if not check._finite(t_depth) or type(sequence) is not int:
        raise BundleError("depth sequence or timestamp missing")
    rgb_plane = (raw_meta.get("frames") or {}).get("rgb") or {}
    intrinsics = None
    if plane.get("transformation_valid") is True and rgb_plane.get("transformation_valid") is True:
        intrinsics = {"rectified_left": plane.get("intrinsics"), "rgb": rgb_plane.get("intrinsics")}
    meta = dict(meta, depth_seq=sequence, t_depth=float(t_depth), intrinsics=intrinsics,
                sync=raw_meta.get("synchronization"), raw=raw_meta)
    return meta, Depth(DEPTH_SHAPE[1], DEPTH_SHAPE[0], depth_from_bytes(body), float(t_depth), sequence)


def newest_check(folder):
    numbered = []
    for path in folder.glob("check-*.json"):
        match = re.fullmatch(r"check-([0-9]+)\.json", path.name)
        if match:
            numbered.append((int(match.group(1)), path))
    if not numbered:
        return None
    path = max(numbered)[1]
    out = {"file": path.name, "verdict": None, "c_s": None, "period_s": None}
    try:
        report = json.loads(textfile.read_text(path))
    except textfile.NotUtf8 as exc:  # never a quiet fall back to the arrival clock
        raise TakeRefused(str(exc)) from None
    except (OSError, ValueError):
        return out
    if type(report) is not dict:
        return out
    offsets = (report.get("reported_only") or {}).get("offset_c_mod_period_s") or [None]
    period = ((report.get("method") or {}).get("tolerances") or {}).get("slate_period_s")
    out.update(verdict=report.get("verdict"),
               c_s=offsets[0] if check._finite(offsets[0]) else None,
               period_s=period if check._finite(period) and period > 0 else None)
    return out


class Take:
    """take.json plus frames.csv, with bundles read on demand in frame order."""

    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        try:
            self.record = json.loads(textfile.read_text(self.folder / "take.json"))
            with io.StringIO(textfile.read_text(self.folder / "frames.csv"), newline="") as source:
                rows = list(csv.DictReader(source))
        except textfile.NotUtf8 as exc:
            raise TakeRefused(str(exc)) from None
        except (OSError, ValueError):
            raise TakeRefused("take.json or frames.csv is missing or malformed") from None
        if type(self.record) is not dict or self.record.get("schema") != TAKE_SCHEMA:
            raise TakeRefused(f"take.json schema is not {TAKE_SCHEMA}")
        if not self.record.get("cues"):
            raise TakeRefused("take.json was never finished; take.py stopped before saving")
        self.rows = []
        for row in rows:
            name = row.get("file", "")
            try:
                if not re.fullmatch(r"frames/[0-9]{6}-[0-9]+\.npz", name):
                    raise ValueError(name)
                self.rows.append({"file": name, "rgb_seq": int(row["rgb_seq"]), "t_host": float(row["t_host_s"]),
                                  "recv_unix": float(row["recv_pc_unix"])})
            except (KeyError, TypeError, ValueError):
                raise TakeRefused(f"frames.csv row {name!r} is malformed") from None
        if any(b["t_host"] <= a["t_host"] for a, b in zip(self.rows, self.rows[1:])):
            raise TakeRefused("frames.csv RGB times do not increase")
        self.check = newest_check(self.folder)
        self.complete = self.record.get("complete") is True

    def __len__(self):
        return len(self.rows)

    def clock(self):
        """{offset_s, source, check}: unix = t_host + offset_s (see the module note)."""
        if not self.rows:
            raise TakeRefused("the take has no frames")
        arrival = sorted(r["recv_unix"] - r["t_host"] for r in self.rows)
        approx = arrival[len(arrival) // 2]
        c = self.check or {}
        if c.get("verdict") == "PASS" and c.get("c_s") is not None and c.get("period_s"):
            period = c["period_s"]
            k = round((approx + c["c_s"]) / period)
            return {"offset_s": -c["c_s"] + k * period, "source": "slate", "check": c["file"]}
        return {"offset_s": approx, "source": "arrival", "check": c.get("file")}

    def frame(self, index):
        row = self.rows[index]
        meta, depth = read_frame((self.folder / row["file"]).read_bytes())
        if meta["rgb_seq"] != row["rgb_seq"] or meta["t_host"] != row["t_host"]:
            raise BundleError(f"{row['file']} does not match frames.csv")
        return meta, depth

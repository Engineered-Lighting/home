"""The owner's worksheet of control points. Standard library only.

One JSON file the owner fills in by hand: the room and camera ids, the room
frame, and each control point's measured room-frame position, the tape or
laser references it was measured from, and its role (`fit` or `check`). It is
read the same with CRLF or LF and with or without a UTF-8 byte-order mark (what
Notepad may save), and its hash is taken over LF text without the mark.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from scene import Refused

SCHEMA = "den-register-worksheet-1"
FRAME = "z_up_metric_floor0"
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # room and camera ids: no '@', so transform_id splits once
POINT_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
ROLES = ("fit", "check")
TOOLS = ("tape", "laser")
PEOPLE = ("A", "B")
TOP_KEYS = {"schema", "room_id", "camera_id", "frame", "references", "measured_by", "measured_on", "points"}
POINT_KEYS = {"id", "role", "label", "room_xyz_m", "measured_from", "measured_with", "scan_xyz_m", "note"}
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def lf_text(data):
    text = data.decode("utf-8-sig") if type(data) is bytes else data
    return text.replace("\r\n", "\n")


def sha256(data):
    return hashlib.sha256(lf_text(data).encode("utf-8")).hexdigest()


def _bad(detail):
    raise Refused("worksheet_invalid", detail)


def _xyz(value, where, *, allow_null):
    if value is None and allow_null:
        return None
    if (type(value) is not list or len(value) != 3
            or not all(type(v) in (int, float) and math.isfinite(v) for v in value)):
        _bad(f"{where} must be three numbers in metres, like [1.25, 2.4, 0.0]")
    return [float(v) for v in value]


def parse(text, *, need_positions=True):
    """The checked worksheet. need_positions=False lets the click page open it before measuring."""
    try:
        sheet = json.loads(lf_text(text), parse_constant=lambda _: _bad("NaN and Infinity are not numbers here"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _bad(f"not JSON ({exc})")
    if type(sheet) is not dict:
        _bad("the worksheet is one JSON object")
    if sheet.get("schema") != SCHEMA:
        _bad(f"schema must be {SCHEMA}")
    unknown = set(sheet) - TOP_KEYS
    if unknown:
        _bad(f"unknown keys {sorted(unknown)}; check their spelling")
    for key in ("room_id", "camera_id"):
        if type(sheet.get(key)) is not str or not ID.fullmatch(sheet[key]):
            _bad(f"{key} must be lower-case letters, digits and dashes, like den-oak")
    if sheet.get("frame") != FRAME:
        _bad(f"frame must be {FRAME}, the scan's room frame")
    if sheet.get("measured_by") not in PEOPLE:
        _bad("measured_by is A or B, never a name")
    if sheet.get("measured_on") is not None and (type(sheet["measured_on"]) is not str
                                                 or not DATE.fullmatch(sheet["measured_on"])):
        _bad("measured_on is a date like 2026-11-10")
    references = sheet.get("references")
    if type(references) is not dict or any(type(references.get(k)) is not str or not references[k].strip()
                                           for k in ("x", "y", "z")):
        _bad("references needs x, y and z: what each distance is measured from")
    points = sheet.get("points")
    if type(points) is not list or not points:
        _bad("points must list the control points")
    seen, out = set(), []
    for index, point in enumerate(points, 1):
        where = f"point {index}"
        if type(point) is not dict:
            _bad(f"{where} must be an object")
        unknown = set(point) - POINT_KEYS
        if unknown:
            _bad(f"{where}: unknown keys {sorted(unknown)}; check their spelling")
        pid = point.get("id")
        if type(pid) is not str or not POINT_ID.fullmatch(pid):
            _bad(f"{where}: id is 1 to 32 letters, digits, - or _")
        where = f"point {pid}"
        if pid in seen:
            _bad(f"{where} is listed twice")
        seen.add(pid)
        if point.get("role") not in ROLES:
            _bad(f"{where}: role is fit or check")
        if point.get("measured_with", "tape") not in TOOLS:
            _bad(f"{where}: measured_with is tape or laser")
        for key in ("label", "note"):
            if point.get(key) is not None and type(point[key]) is not str:
                _bad(f"{where}: {key} is text")
        measured_from = point.get("measured_from")
        if measured_from is not None and (type(measured_from) is not dict
                                          or any(type(v) is not str for v in measured_from.values())):
            _bad(f"{where}: measured_from maps x, y, z to the reference each was measured from")
        out.append({"id": pid, "role": point["role"], "label": point.get("label") or "",
                    "room_xyz_m": _xyz(point.get("room_xyz_m"), f"{where}: room_xyz_m",
                                       allow_null=not need_positions),
                    "scan_xyz_m": _xyz(point.get("scan_xyz_m"), f"{where}: scan_xyz_m", allow_null=True),
                    "measured_with": point.get("measured_with", "tape")})
    return {"room_id": sheet["room_id"], "camera_id": sheet["camera_id"], "frame": FRAME,
            "measured_by": sheet["measured_by"], "points": out, "sha256": sha256(text)}


def load(path, *, need_positions=True):
    try:
        data = Path(path).read_bytes()
    except OSError:
        raise Refused("worksheet_invalid", f"{path} cannot be read") from None
    return parse(data, need_positions=need_positions)


def template():
    """A worksheet to fill in: four fit points, two checks, positions left null."""
    def point(pid, role, label):
        return {"id": pid, "role": role, "label": label, "room_xyz_m": None,
                "measured_from": {"x": "", "y": "", "z": "floor"}, "measured_with": "tape",
                "scan_xyz_m": None, "note": ""}
    return {"schema": SCHEMA, "room_id": "den", "camera_id": "den-oak", "frame": FRAME,
            "references": {"x": "the wall at x = 0 in the scan (write which wall)",
                           "y": "the wall at y = 0 in the scan (write which wall)",
                           "z": "the floor"},
            "measured_by": "A", "measured_on": None,
            "points": [point("f1", "fit", "tape cross on the floor, far left"),
                       point("f2", "fit", "tape cross on the floor, far right"),
                       point("f3", "fit", "tape cross on a table or counter top"),
                       point("f4", "fit", "tape cross on a wall, about shoulder height"),
                       point("c1", "check", "tape cross on the floor, near the middle"),
                       point("c2", "check", "tape cross on another raised surface")]}

"""RoomModel and CalibrationRecord files, and transform_id resolution. Standard library only.

Field names follow docs/architecture/live-v1/schemas.json (`room_model`,
`calibration_record`). Both files are JSON Lines: one record per line, UTF-8,
LF. room-model.jsonl holds every RoomModel revision in order; the last line is
the current room. A camera entry, once written, never changes except that a
later revision may add `invalidated` to it. So an Observation's
`transform_id`, `<camera_id>@<version>`, resolves to exactly one entry.
"""
import json
import math
import re
from pathlib import Path

from scene import Refused
import rigid

FRAME = {"name": "z_up_metric_floor0", "unit": "m", "origin": "the minimum corner of the scan, on the floor",
         "axes": "x along the room's long axis, z up from the floor, y completing a right-handed frame"}
CAMERA_FRAME = "rectified_left"
CAMERA_AXES = "+x right, +y down, +z forward, metres"
PHOTOMETRIC_UNCALIBRATED = {"k_L": None, "unit": "(cd/m²)/nc", "normalisation_version": None,
                            "calibration_id": None, "status": "uncalibrated"}
CAMERA_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
TRAILING_NUMBER = re.compile(r"^(.*?)([0-9]+)$")


# ---- JSON Lines --------------------------------------------------------------

def read_jsonl(path, reason):
    """Every record in a JSON Lines file, oldest first; [] when it does not exist yet."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        text = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
        records = [json.loads(line) for line in text.split("\n") if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refused(reason, f"{path.name} does not read as JSON Lines ({exc})") from None
    if not all(type(r) is dict for r in records):
        raise Refused(reason, f"{path.name} holds a line that is not a JSON object")
    return records


def line(record):
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def append_jsonl(path, record):
    with open(path, "ab") as sink:
        sink.write(line(record))


# ---- the RoomModel -------------------------------------------------------------

def new_room_model(room_id):
    return {"room_id": room_id, "version": f"{room_id}-v0", "frame": dict(FRAME), "surfaces": [], "cameras": [],
            "fixtures": [], "landmarks": [], "provenance": {}}


def current(room_models, room_id):
    """The latest revision, or an empty one for a new file. A file for another room is refused."""
    if not room_models:
        return new_room_model(room_id)
    latest = room_models[-1]
    if latest.get("room_id") != room_id:
        raise Refused("room_model_unreadable", f"room-model.jsonl is for room {latest.get('room_id')!r}, "
                                               f"the worksheet for {room_id!r}")
    if type(latest.get("cameras")) is not list:
        raise Refused("room_model_unreadable", "the latest RoomModel has no cameras list")
    return latest


def next_room_version(version, room_id):
    match = TRAILING_NUMBER.match(version or "")
    return f"{match[1]}{int(match[2]) + 1}" if match else f"{version or room_id}-v1"


def camera_entry(room_model, camera_id):
    found = [c for c in room_model.get("cameras") or [] if type(c) is dict and c.get("camera_id") == camera_id]
    return found[-1] if found else None


def entries(room_models, camera_id):
    """(revision index, entry) for every camera entry of this camera in the file."""
    return [(i, c) for i, rm in enumerate(room_models) for c in rm.get("cameras") or []
            if type(c) is dict and c.get("camera_id") == camera_id]


def next_version(room_models, camera_id, date):
    """`YYYY-MM-DD.N`: N is one more than this camera's registrations in the file."""
    versions = {c.get("version") for _, c in entries(room_models, camera_id)}
    n = len(versions) + 1
    while f"{date}.{n}" in versions:
        n += 1
    return f"{date}.{n}"


def with_camera(room_model, entry, provenance):
    """A new revision of room_model with this camera's entry replaced (or added)."""
    revision = json.loads(json.dumps(room_model))
    revision["version"] = next_room_version(room_model.get("version"), room_model["room_id"])
    revision["cameras"] = [c for c in revision["cameras"] if c.get("camera_id") != entry["camera_id"]] + [entry]
    revision.setdefault("provenance", {})["cameras"] = provenance
    return revision


def invalidated_revision(room_model, camera_id, invalidated):
    revision = json.loads(json.dumps(room_model))
    revision["version"] = next_room_version(room_model.get("version"), room_model["room_id"])
    for entry in revision["cameras"]:
        if entry.get("camera_id") == camera_id:
            entry["invalidated"] = invalidated
    return revision


# ---- transform_id ----------------------------------------------------------------

def transform_id(camera_id, version):
    return f"{camera_id}@{version}"


def parse_transform_id(value):
    camera_id, sep, version = value.partition("@") if type(value) is str else ("", "", "")
    if not sep or not CAMERA_ID.fullmatch(camera_id) or not VERSION.fullmatch(version):
        raise Refused("transform_id_invalid", f"{value!r} is not <camera_id>@<version>")
    return camera_id, version


def resolve(room_models, value):
    """{entry, invalidated}: the one camera entry `<camera_id>@<version>` names.

    Every revision that carries the entry must carry the same entry, apart from
    `invalidated`, which a later revision may add. Refused with
    transform_id_invalid, transform_unknown or transform_ambiguous.
    """
    camera_id, version = parse_transform_id(value)
    found = [c for _, c in entries(room_models, camera_id) if c.get("version") == version]
    if not found:
        raise Refused("transform_unknown", f"no RoomModel camera entry is {value}")
    bare = {json.dumps({k: v for k, v in c.items() if k != "invalidated"}, sort_keys=True) for c in found}
    if len(bare) != 1:
        raise Refused("transform_ambiguous", f"{value} names {len(bare)} different camera entries")
    invalidated = next((c["invalidated"] for c in found if c.get("invalidated")), None)
    return {"entry": found[-1], "invalidated": invalidated}


def room_from_camera(entry):
    pose = entry.get("pose") if type(entry) is dict else None
    try:
        rotation, translation = rigid.split4(pose["room_from_camera"])
    except (TypeError, KeyError, ValueError):
        raise Refused("transform_unknown", "the camera entry has no numeric pose.room_from_camera") from None
    if not rigid.is_rotation(rotation):
        raise Refused("transform_unknown", "pose.room_from_camera is not a rigid transform")
    return rotation, translation


def to_room(entry, camera_xyz):
    rotation, translation = room_from_camera(entry)
    return rigid.apply(rotation, translation, camera_xyz)


def rounded(value, places=6):
    """Numbers rounded for the records (micrometres for metres), lists and dicts kept."""
    if type(value) is float:
        return round(value, places) if math.isfinite(value) else None
    if type(value) is list:
        return [rounded(v, places) for v in value]
    if type(value) is dict:
        return {k: rounded(v, places) for k, v in value.items()}
    return value

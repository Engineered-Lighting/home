"""RoomModel camera entries for den-observe. Standard library only.

An Observation's transform_id, `<camera_id>@<version>`, resolves to exactly
one `RoomModel.cameras` entry (schemas.json). As room_context.py does, a
missing or stale registration is refused, never guessed: see METHOD.md,
"Registration". The entry's inner form is den-observe-camera-1 (METHOD.md,
"Inputs"), which den-register writes. The file is den-register's
room-model.jsonl: JSON Lines, one RoomModel revision per line, the last line the
current room.
"""
import json
import math

import camgeom as g
import lift
import textfile

ROOM_FRAME = "z_up_metric_floor0"
INTRINSICS_TOLERANCE_PX = 1.0  # depth_association.camera_model's intrinsics_tolerance_px


class RegistrationRefused(Exception):
    """No usable registration: nothing is written."""


def _time(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _section(entry, name):
    return entry.get(name) if type(entry.get(name)) is dict else {}


def _last_with_invalidations(revisions):
    """The last revision, with an invalidation of one of its camera entries from any line of the file.

    An invalidation is for good, as rung 1's own resolve has it: two overlapping
    check-mount runs can leave a confirmation after one, and the last line alone
    would hide it.
    """
    def entries(revision):
        cameras = revision.get("cameras")
        return [c for c in cameras if type(c) is dict] if type(cameras) is list else []

    def key(entry):
        return json.dumps([entry.get("camera_id"), entry.get("version")])  # a hand-edited file can hold a list here

    gone = {key(entry): entry["invalidated"] for revision in revisions for entry in entries(revision)
            if entry.get("invalidated")}
    for entry in entries(revisions[-1]):
        if key(entry) in gone:
            entry["invalidated"] = gone[key(entry)]
    return revisions[-1]


def load(path):
    """The current RoomModel: the file's last revision. One document alone is a file of one revision.

    A line that does not read is refused, never skipped: an append cut short
    must not bring back the revision before it.
    """
    try:
        text = textfile.read_text(path).replace("\r\n", "\n")
    except textfile.NotUtf8 as exc:
        raise RegistrationRefused(str(exc)) from None
    except OSError:
        raise RegistrationRefused(f"no readable RoomModel at {path}") from None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        revisions = [json.loads(line) for line in text.split("\n") if line.strip()]
    except ValueError:
        raise RegistrationRefused(f"no readable RoomModel at {path}: a line is not JSON") from None
    if not revisions or not all(type(revision) is dict for revision in revisions):
        raise RegistrationRefused(f"no readable RoomModel at {path}: not one JSON object per line")
    return _last_with_invalidations(revisions)


def parse_transform_id(transform_id):
    if not isinstance(transform_id, str) or transform_id.count("@") != 1:
        raise RegistrationRefused(f"transform_id {transform_id!r} is not <camera_id>@<version>")
    camera_id, version = transform_id.split("@")
    if not camera_id or not version:
        raise RegistrationRefused(f"transform_id {transform_id!r} is not <camera_id>@<version>")
    return camera_id, version


def _cameras(room_model):
    if type(room_model) is not dict or type(room_model.get("cameras")) is not list:
        raise RegistrationRefused("the RoomModel has no cameras list")
    frame = room_model.get("frame")
    name = frame.get("id") or frame.get("name") if isinstance(frame, dict) else frame
    if frame is not None and name != ROOM_FRAME:
        raise RegistrationRefused(f"the RoomModel frame is {name!r}, not {ROOM_FRAME}")
    return [c for c in room_model["cameras"] if type(c) is dict]


def resolve(room_model, transform_id):
    """The one camera entry transform_id names; refused if none or several."""
    camera_id, version = parse_transform_id(transform_id)
    found = [c for c in _cameras(room_model) if c.get("camera_id") == camera_id and c.get("version") == version]
    if not found:
        raise RegistrationRefused(f"missing registration: no camera entry {transform_id}")
    if len(found) > 1:
        raise RegistrationRefused(f"{transform_id} names {len(found)} camera entries, not exactly one")
    return found[0]


def current_transform_id(room_model, camera_id):
    """camera_id@<newest version>; versions are ISO dates, which sort in time order."""
    versions = [c.get("version") for c in _cameras(room_model) if c.get("camera_id") == camera_id]
    versions = [v for v in versions if isinstance(v, str) and v]
    if not versions:
        raise RegistrationRefused(f"missing registration: no camera entry for {camera_id}")
    return f"{camera_id}@{max(versions)}"


class Camera:
    """A resolved, checked camera entry: its rig, its pose and its error bound."""

    def __init__(self, room_model, transform_id, *, max_error_bound_m):
        self.camera_id, self.version = parse_transform_id(transform_id)
        current = current_transform_id(room_model, self.camera_id)
        if current != transform_id:
            raise RegistrationRefused(f"stale registration: {transform_id} is not the current entry, {current}")
        entry = resolve(room_model, transform_id)
        if entry.get("invalidated"):
            raise RegistrationRefused(f"{transform_id} was invalidated by rung 1's mount check: the camera moved. "
                                      "Register again")
        bound = entry.get("error_bound_m")
        if type(bound) not in (int, float) or not math.isfinite(bound) or bound < 0:
            raise RegistrationRefused(f"{transform_id} has no error_bound_m: the registration is not validated")
        if bound > max_error_bound_m:
            raise RegistrationRefused(f"{transform_id} error_bound_m {bound} m is above rung 1's "
                                      f"{max_error_bound_m} m gate")
        k, pose = entry.get("intrinsics"), entry.get("pose")
        if type(k) is not dict or type(pose) is not dict:
            raise RegistrationRefused(f"{transform_id} intrinsics and pose are not in den-observe-camera-1 form")
        try:
            extrinsics = k["rgb_from_rectified_left"]
            self.rig = lift.Rig(left_k=k["rectified_left"], rgb_k=k["rgb"], rgb_distortion=k.get("rgb_distortion"),
                                rgb_from_left=(extrinsics["rotation"], extrinsics["translation_m"]),
                                rgb_size=k["rgb_size"], depth_size=k["depth_size"])
            if pose.get("camera_frame") != "rectified_left":
                raise ValueError("pose.camera_frame must be rectified_left")
            self.room_from_left = g.from_matrix(pose.get("room_from_camera"), "room_from_camera")
            baseline = k["stereo_baseline_m"]
            if not g.finite(baseline) or not 0.01 <= baseline <= 1.0:
                raise ValueError("stereo_baseline_m must be metres")
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistrationRefused(f"{transform_id} camera entry is not usable: {exc}") from None
        self.transform_id, self.error_bound_m, self.baseline_m = transform_id, float(bound), float(baseline)
        self.registered_unix = _time(_section(entry, "mount").get("take_ended_unix"))
        self.confirmed_unix = _time(_section(entry, "confirmed").get("take_ended_unix"))
        self.f_left, self.f_rgb = self.rig.left_k[0], self.rig.rgb_k[0]
        rgb_origin_left = g.apply(*self.rig.left_from_rgb, [0.0, 0.0, 0.0])
        self.rgb_origin_room = g.apply(*self.room_from_left, rgb_origin_left)

    def take_state(self, ended_unix, *, scored):
        """Where a take stands against rung 1's mount record, or refused (METHOD.md, "Registration").

        A confirmation vouches for the takes back to the registration's own, so a
        take older than the registration is refused, and a scored take must be
        vouched for. A registered entry always has the record; a hand-made one may not.
        """
        registered, confirmed = self.registered_unix, self.confirmed_unix
        if registered is None:
            if scored:
                raise RegistrationRefused(f"{self.transform_id} has no mount record from rung 1, so a scored run "
                                          "cannot tell where the camera was during this take")
            return {"status": "no_mount_record"}
        ended = _time(ended_unix)
        if ended is None:
            raise RegistrationRefused("take.json has no numeric ended_unix, so this take cannot be placed against "
                                      f"{self.transform_id}'s mount record")
        if ended < registered:
            raise RegistrationRefused(f"this take ended before the take {self.transform_id} was registered from, so "
                                      "the camera may have been somewhere else. Lift a take recorded since")
        held = confirmed is not None and ended <= confirmed
        if scored and not held:
            raise RegistrationRefused(f"rung 1 has not confirmed {self.transform_id} on this take or a later one, so "
                                      "the camera may have moved. Record a later take, click every mark, and run "
                                      "register.py check-mount")
        return {"status": "confirmed" if held else "not_confirmed", "take_ended_unix": ended,
                "registered_take_ended_unix": registered, "confirmed_take_ended_unix": confirmed}

    def check_frame(self, intrinsics):
        """A frame's own intrinsics, when it carries them, must match the entry's."""
        if intrinsics is None:
            return
        for name, reference in (("rectified_left", self.rig.left_k), ("rgb", self.rig.rgb_k)):
            try:
                frame_k = g.intrinsics(intrinsics.get(name), name)
            except ValueError:
                raise RegistrationRefused(f"a frame's {name} intrinsics are unreadable") from None
            if max(abs(a - b) for a, b in zip(frame_k, reference)) > INTRINSICS_TOLERANCE_PX:
                raise RegistrationRefused(f"stale registration: a frame's {name} intrinsics differ from "
                                          f"{self.transform_id} by more than {INTRINSICS_TOLERANCE_PX} px")

    def to_room(self, point_left):
        return g.apply(*self.room_from_left, point_left)

    def rotate(self, cov_left):
        return g.cov_rotate(self.room_from_left[0], cov_left)

    def rgb_ray_room(self, u, v):
        """(origin, unit direction) in the room frame of the RGB pixel's viewing ray."""
        ray = self.rig.rgb_ray(u, v)
        direction_left = g.mat_vec(self.rig.left_from_rgb[0], ray)
        return self.rgb_origin_room, g.unit(g.mat_vec(self.room_from_left[0], direction_left))

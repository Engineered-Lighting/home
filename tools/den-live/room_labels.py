"""The den's labels (den-room-labels-1): the owner's named boxes on the den's scan, kept here and served by stream.py.

Home's Labels tool (home-den-labels-view.mjs) reads and saves the one document through GET and PUT
/api/den-live/v1/homes/den/labels. The lighting coordinator, running in this process, reads them through a Store, and
the Qwen ask gets one line about the seated person from line_for(). Standard library only; nothing runs on import.

The frame is the den scan's room frame (room-model.jsonl's room_from_camera): Z up, metres, floor at z = 0, the frame
den-live's tracks use. A label is one upright box: centre_m, size_m = [width along its own x, depth along its own y,
height], and yaw_deg turning its own x from the scan's +x, counter-clockwise from above. Its own +y face is its front,
so front = (-sin yaw, cos yaw). The checks and their refusal words are the same as Home's home-den-labels-core.mjs
docProblem, in the same order.

Store keeps room-labels.json, written atomically, and room-labels-history.jsonl, the last 100 saves, as the way back.
A save is a compare-and-swap on the document's revision: a stale one gets 409 and the current document.
"""
import datetime as dt
import json
import math
import os
import re
import threading
import time
from pathlib import Path

SCHEMA = "den-room-labels-1"
KINDS = ("seat", "table", "picture", "machine", "storage", "screen", "opening", "lamp", "area", "other")
MAX_LABELS, MAX_BYTES, NAME_MAX, HISTORY_KEEP = 100, 65536, 40, 100
MIN_SIZE, MAX_SIZE = 0.02, 8.0
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-'&/().]*")
ID = re.compile(r"l-[a-z0-9-]{1,60}")
DOC_KEYS = ("schema", "home", "room_id", "content_revision", "revision", "saved_at", "labels")
LABEL_KEYS = ("id", "name", "kind", "box", "on_wall", "made_by", "status", "updated_at")
BOX_KEYS = ("centre_m", "size_m", "yaw_deg")
AT_KINDS = ("table", "machine", "storage", "screen")
FILE, HISTORY = "room-labels.json", "room-labels-history.jsonl"


def empty(content_revision=None):
    return {"schema": SCHEMA, "home": "den", "room_id": "den", "content_revision": content_revision, "revision": 0,
            "saved_at": None, "labels": []}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _triple(value, ok):
    return isinstance(value, list) and len(value) == 3 and all(_number(v) and ok(v) for v in value)


def _opt_str(value, most):
    return value is None or (isinstance(value, str) and len(value) <= most)


def _exact(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


def _yaw(value):
    v = math.fmod(round(float(value) * 10) / 10, 360.0)
    v = v + 360.0 if v < 0 else v
    return 0.0 if v >= 360.0 else v


def validate(doc):
    """(normalised_doc, None) for a document den-live keeps, else (None, reason). Yaw is normalised into [0, 360)."""
    if not _exact(doc, DOC_KEYS):
        return None, "key_unknown"
    if doc["schema"] != SCHEMA:
        return None, "schema_wrong"
    if doc["home"] != "den" or doc["room_id"] != "den":
        return None, "scope_wrong"
    if not _opt_str(doc["content_revision"], 128) or not _opt_str(doc["saved_at"], 40):
        return None, "field_invalid"
    if type(doc["revision"]) is not int or doc["revision"] < 0:
        return None, "revision_invalid"
    if not isinstance(doc["labels"], list):
        return None, "labels_invalid"
    if len(doc["labels"]) > MAX_LABELS:
        return None, "too_many_labels"
    ids, names, labels = set(), set(), []
    for label in doc["labels"]:
        if not _exact(label, LABEL_KEYS) or not _exact(label["box"], BOX_KEYS):
            return None, "key_unknown"
        if not isinstance(label["id"], str) or not ID.fullmatch(label["id"]):
            return None, "id_invalid"
        if label["id"] in ids:
            return None, "id_duplicate"
        ids.add(label["id"])
        name = label["name"]
        if isinstance(name, str) and len(name) > NAME_MAX:
            return None, "name_too_long"
        if not isinstance(name, str) or not NAME.fullmatch(name) or name != name.rstrip():
            return None, "name_invalid"
        if name.lower() in names:
            return None, "name_duplicate"
        names.add(name.lower())
        if label["kind"] not in KINDS:
            return None, "kind_unknown"
        box = label["box"]
        if (not _triple(box["centre_m"], lambda v: abs(v) <= 100) or not _triple(box["size_m"], lambda v: MIN_SIZE <= v <= MAX_SIZE)
                or not _number(box["yaw_deg"])):
            return None, "box_invalid"
        if type(label["on_wall"]) is not bool:
            return None, "field_invalid"
        if label["made_by"] not in ("owner", "system") or label["status"] not in ("confirmed", "suggested", "check"):
            return None, "field_invalid"
        if not _opt_str(label["updated_at"], 40):
            return None, "field_invalid"
        labels.append({**label, "box": {"centre_m": list(box["centre_m"]), "size_m": list(box["size_m"]), "yaw_deg": _yaw(box["yaw_deg"])}})
    normalised = {**doc, "labels": labels}
    if len(json.dumps(normalised, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        return None, "too_large"
    return normalised, None


class Store:
    """The den's one labels document in `folder`. Thread-safe: stream.py serves it from many threads."""

    def __init__(self, folder, *, now=time.time, say=print):
        self.folder, self.now, self.say = Path(folder), now, say
        self.path, self.history = self.folder / FILE, self.folder / HISTORY
        self.lock = threading.Lock()
        self.doc = self._load()

    def _load(self):
        if not self.path.exists():
            return empty()
        try:
            doc, reason = validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            doc, reason = None, type(error).__name__
        if doc is not None:
            return doc
        stamp = dt.datetime.fromtimestamp(self.now(), dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        aside = self.folder / f"room-labels.bad-{stamp}.json"
        try:
            os.replace(self.path, aside)
            self.say(f"den-live: the labels file was unreadable ({reason}); set aside as {aside.name}, starting with no labels")
        except OSError:
            self.say(f"den-live: the labels file was unreadable ({reason}) and couldn't be set aside; starting with no labels")
        return empty()

    def get(self):
        with self.lock:
            return json.loads(json.dumps(self.doc))

    def confirmed(self):
        """The labels the coordinator and the Qwen line use: confirmed ones only."""
        with self.lock:
            return [json.loads(json.dumps(label)) for label in self.doc["labels"] if label["status"] == "confirmed"]

    def put(self, doc):
        """(200, new_doc) | (409, current_doc) | (400, {"ok": False, "error": reason}) | (500, {"ok": False, ...})."""
        normalised, reason = validate(doc)
        if normalised is None:
            return 400, {"ok": False, "error": reason}
        with self.lock:
            if normalised["revision"] != self.doc["revision"]:
                return 409, json.loads(json.dumps(self.doc))
            saved_at = dt.datetime.fromtimestamp(self.now(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            new = {**normalised, "revision": self.doc["revision"] + 1, "saved_at": saved_at}
            text = json.dumps(new, separators=(",", ":"))
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_name(FILE + ".new")
                temp.write_text(text, encoding="utf-8")
                os.replace(temp, self.path)
            except OSError:
                return 500, {"ok": False, "error": "save_failed"}
            self.doc = new
            self._remember(text)
            return 200, json.loads(text)

    def _remember(self, text):
        """The last HISTORY_KEEP saves, one per line: the way back if a save goes wrong. A failure here loses no save."""
        try:
            lines = self.history.read_text(encoding="utf-8").splitlines() if self.history.exists() else []
            lines = (lines + [text])[-HISTORY_KEEP:]
            temp = self.history.with_name(HISTORY + ".new")
            temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(temp, self.history)
        except OSError:
            self.say("den-live: the labels history couldn't be written; the save itself stands")


# ---------------- the line (pure: no clock, no file, no global state) ----------------

def front_unit(label):
    a = math.radians(label["box"]["yaw_deg"])
    return (-math.sin(a), math.cos(a))


def _local(label, x, y):
    box = label["box"]
    a, dx, dy = math.radians(box["yaw_deg"]), x - box["centre_m"][0], y - box["centre_m"][1]
    return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)


def footprint(label):
    """The four floor corners: back left, back right, front right, front left."""
    box = label["box"]
    w, d = box["size_m"][0], box["size_m"][1]
    a = math.radians(box["yaw_deg"])
    corners = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, ly = sx * w / 2, sy * d / 2
        corners.append((box["centre_m"][0] + lx * math.cos(a) - ly * math.sin(a), box["centre_m"][1] + lx * math.sin(a) + ly * math.cos(a)))
    return corners


def foot_distance(label, xy):
    """Floor distance from a point to the label's footprint; 0 inside it."""
    lx, ly = _local(label, xy[0], xy[1])
    w, d = label["box"]["size_m"][0], label["box"]["size_m"][1]
    return math.hypot(max(abs(lx) - w / 2, 0.0), max(abs(ly) - d / 2, 0.0))


def _the(name):
    return name if re.match(r"(?i)(the|my|a|an)\s", name) else "the " + name


def near_phrase(labels, xy):
    """What's near a floor point, from confirmed labels: "on the desk chair, at the desk, beside the 3D printer", or None.

    on: the nearest seat within 0.25 m. at: the nearest table, machine, storage or screen within 0.6 m, other than the
    "on" one. in: the smallest area holding the point, only with no on or at. beside: up to 2 more labels within 1.0 m,
    nearest first, never an area or an opening."""
    rows = [(foot_distance(label, xy), label) for label in labels if label.get("status") == "confirmed"]

    def nearest(test, limit, key=lambda row: row[0]):
        hits = sorted((row for row in rows if test(row[1]) and row[0] <= limit), key=key)
        return hits[0][1] if hits else None

    on = nearest(lambda label: label["kind"] == "seat", 0.25)
    at = nearest(lambda label: label["kind"] in AT_KINDS and label is not on, 0.6)
    area = None if on or at else nearest(lambda label: label["kind"] == "area", 0.0,
                                         key=lambda row: row[1]["box"]["size_m"][0] * row[1]["box"]["size_m"][1])
    used = [label for label in (on, at, area) if label is not None]
    beside = [label for _distance, label in sorted(rows, key=lambda row: row[0])
              if _distance <= 1.0 and label["kind"] not in ("area", "opening") and all(label is not u for u in used)][:2]
    parts = []
    if on:
        parts.append("on " + _the(on["name"]))
    if at:
        parts.append("at " + _the(at["name"]))
    if area:
        parts.append("in " + _the(area["name"]))
    if beside:
        parts.append("beside " + " and ".join(_the(label["name"]) for label in beside))
    if not parts:
        return None
    return ", ".join(parts)[:160].rstrip(" ,") or None


def line_for(labels_doc, track, body=None, scan_revision=None):
    """The Qwen ask's line about one seated person, "Seated at the desk, beside the 3D printer.", or None.

    labels_doc: a whole den-room-labels-1 document. track: one den-live-people-1 track (its xy_m). body: that person's
    den-live-body-1 skeleton, unused for now and kept for hands. scan_revision: the scan den-live is registered on; a
    document placed on another one gives None. The caller decides who is seated and who gets asked about."""
    del body
    doc, _reason = validate(labels_doc) if labels_doc is not None else (None, None)
    if doc is None:
        return None
    if scan_revision is not None and doc["content_revision"] != scan_revision:
        return None
    xy = (track or {}).get("xy_m")
    if not isinstance(xy, (list, tuple)) or len(xy) != 2 or not all(_number(v) for v in xy):
        return None
    phrase = near_phrase(doc["labels"], xy)
    return f"Seated {phrase}." if phrase else None

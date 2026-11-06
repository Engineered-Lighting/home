"""den-detections-1: the detector-output rows den-observe reads. Standard library only.

One JSON object per line, one line per RGB frame the detector ran on:

    {"schema": "den-detections-1",
     "frame": {"capture_ts": 5120.3341, "clock": "host_monotonic", "rgb_seq": 812,
               "width": 640, "height": 360},
     "available_ts": 5120.6702,
     "detector": {"boxes": "yolo26s", "boxes_version": null,
                  "keypoints": "rtmpose-m", "keypoints_version": null, "keypoint_layout": "coco17"},
     "detections": [
       {"id": "812:0", "class": "person", "score": 0.91, "box_xyxy": [210.0, 40.5, 380.2, 352.0],
        "keypoints": [[296.1, 77.0, 0.88], null, ...]}]}

- capture_ts is the frame's own capture time in seconds on the named clock:
  host_monotonic (an R take's bundle time, frames.csv t_host_s) or unix (the
  live loop's observed_at). Rows of one file share one clock.
- available_ts, optional, is when the detector's result for that frame was
  complete, on the same clock. den-observe's freshness is available_ts minus
  capture_ts; a scored run needs it on every row.
- Boxes and keypoints are in source pixels of that frame (x right, y down).
- keypoints, when present, has exactly 17 entries in COCO-17 order; an entry
  is [u, v, score] or null when the detector gave no finite pixel. A detection
  without a keypoint model has "keypoints": null.
- Scores are the detector's own (box confidence; RTMPose SimCC response, which
  is not a probability). den-observe never reads them as probabilities.
- The *_version entries are optional (null when unknown) and become the
  Observation's source.model_version.

from_perception_lab() maps one perception-lab result (Ultralytics boxes plus
RTMPose pose_instances, joined by instance_id, as staged_specialist composes
them) to a row without running any model; the caller supplies the frame's
capture time, and the completion time if it has one, which those results do
not carry.
"""
import io
import json
import math

import textfile

SCHEMA = "den-detections-1"
CLOCKS = ("host_monotonic", "unix")
COCO17 = ("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
          "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
          "left_knee", "right_knee", "left_ankle", "right_ankle")
MAX_DETECTIONS = 64


class RowError(ValueError):
    """A detector row that does not follow den-detections-1."""


def _finite(*values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def _text(value, limit=128):
    return isinstance(value, str) and 1 <= len(value) <= limit


def validate(row):
    """Return the row unchanged if it follows den-detections-1, else raise RowError."""
    if type(row) is not dict or row.get("schema") != SCHEMA:
        raise RowError(f"schema must be {SCHEMA}")
    frame, detector, detections = row.get("frame"), row.get("detector"), row.get("detections")
    if type(frame) is not dict or type(detector) is not dict or type(detections) is not list:
        raise RowError("frame, detector and detections are required")
    if not _finite(frame.get("capture_ts")) or frame.get("clock") not in CLOCKS:
        raise RowError("frame needs a finite capture_ts on a named clock")
    if "rgb_seq" in frame and (type(frame["rgb_seq"]) is not int or frame["rgb_seq"] < 0):
        raise RowError("rgb_seq must be a non-negative integer")
    width, height = frame.get("width"), frame.get("height")
    if type(width) is not int or type(height) is not int or not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise RowError("frame width and height are required")
    if not _text(detector.get("boxes")) or detector.get("keypoint_layout", "coco17") != "coco17":
        raise RowError("detector needs a box model name and the coco17 keypoint layout")
    for key in ("boxes_version", "keypoints_version"):
        if detector.get(key) is not None and not _text(detector[key]):
            raise RowError(f"detector {key} must be a short string or null")
    if "available_ts" in row and row["available_ts"] is not None and (
            not _finite(row["available_ts"]) or row["available_ts"] < frame["capture_ts"]):
        raise RowError("available_ts must be finite and not before capture_ts")
    if len(detections) > MAX_DETECTIONS:
        raise RowError("too many detections in one frame")
    ids = set()
    for d in detections:
        if type(d) is not dict or not _text(d.get("id")) or d["id"] in ids:
            raise RowError("each detection needs a unique id")
        ids.add(d["id"])
        if not _text(d.get("class")) or not _finite(d.get("score")) or not 0 <= d["score"] <= 1:
            raise RowError(f"detection {d['id']}: class and a 0..1 score are required")
        box = d.get("box_xyxy")
        if (type(box) is not list or len(box) != 4 or not _finite(*box)
                or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
            raise RowError(f"detection {d['id']}: box_xyxy must lie inside the frame")
        points = d.get("keypoints")
        if points is None:
            continue
        if type(points) is not list or len(points) != len(COCO17):
            raise RowError(f"detection {d['id']}: keypoints must hold 17 COCO entries or be null")
        for point in points:
            if point is not None and (type(point) is not list or len(point) != 3 or not _finite(*point)):
                raise RowError(f"detection {d['id']}: a keypoint is [u, v, score] or null")
        if not _text(detector.get("keypoints")):
            raise RowError("keypoints need a keypoint model name")
    return row


def read_rows(path):
    """Validated rows of a .jsonl file, in file order; blank lines are skipped."""
    rows = []
    with io.StringIO(textfile.read_text(path), newline=None) as source:  # newline=None: as a text-mode open reads
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                rows.append(validate(json.loads(line)))
            except (ValueError, RowError) as exc:
                raise RowError(f"line {number}: {exc}") from None
    if any(b["frame"]["capture_ts"] <= a["frame"]["capture_ts"] for a, b in zip(rows, rows[1:])):
        raise RowError("capture_ts must increase from row to row")
    if len({row["frame"]["clock"] for row in rows}) > 1:
        raise RowError("every row of one file uses the same clock")
    return rows


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(validate(row), separators=(",", ":")) + "\n")


def from_perception_lab(result, *, capture_ts, available_ts=None, rgb_seq=None, clock="host_monotonic",
                        boxes_model="yolo26s", keypoints_model="rtmpose-m", boxes_version=None,
                        keypoints_version=None, width=None, height=None):
    """One perception-lab result -> one den-detections-1 row. Runs no model.

    result: Ultralytics normalize_result output ("boxes": x, y, w, h, label,
    confidence, instance_id) optionally merged with RTMPose normalize_pose
    output ("pose_instances": instance_id, layout coco17, joints with joint_id,
    pixel, score). A pose joins its box by instance_id only, never by position.
    """
    if type(result) is not dict or result.get("coordinate_space", "source_pixels") != "source_pixels":
        raise RowError("source-pixel detector result required")
    quality = result.get("quality") or {}
    width = width if width is not None else quality.get("image_width")
    height = height if height is not None else quality.get("image_height")
    poses = {}
    for pose in result.get("pose_instances") or []:
        if type(pose) is not dict or pose.get("layout") != "coco17" or pose.get("instance_id") in poses:
            raise RowError("one coco17 pose per instance_id required")
        poses[pose["instance_id"]] = pose
    detections = []
    for box in result.get("boxes") or []:
        x, y, w, h = (box.get(k) for k in ("x", "y", "w", "h"))
        if not _finite(x, y, w, h):
            raise RowError("finite source-pixel box required")
        entry = {"id": str(box.get("instance_id")), "class": box.get("label"), "score": box.get("confidence"),
                 "box_xyxy": [x, y, x + w, y + h], "keypoints": None}
        pose = poses.pop(box.get("instance_id"), None)
        if pose is not None:
            points = [None] * len(COCO17)
            for joint in pose.get("joints") or []:
                index, pixel, score = joint.get("joint_id"), joint.get("pixel"), joint.get("score")
                if type(index) is not int or not 0 <= index < len(COCO17):
                    raise RowError("coco17 joint_id required")
                if pixel is not None and _finite(*pixel) and _finite(score):
                    points[index] = [pixel[0], pixel[1], score]
            entry["keypoints"] = points
        detections.append(entry)
    if poses:
        raise RowError("a pose instance has no box with its instance_id")
    frame = {"capture_ts": capture_ts, "clock": clock, "width": width, "height": height}
    if rgb_seq is not None:
        frame["rgb_seq"] = rgb_seq
    detector = {"boxes": boxes_model, "boxes_version": boxes_version, "keypoint_layout": "coco17"}
    if any(d["keypoints"] is not None for d in detections):
        detector.update(keypoints=keypoints_model, keypoints_version=keypoints_version)
    row = {"schema": SCHEMA, "frame": frame, "detector": detector, "detections": detections}
    if available_ts is not None:
        row["available_ts"] = available_ts
    return validate(row)

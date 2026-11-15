"""The held-object pass: YOLO26s-seg's instance masks for holdable things near a wrist, beside the tracking detector.

detect_worker.py runs it on the same RGB frame den-detect's YOLO26s and RTMPose-m just used, after the tracking row is
already on its way (so tracking keeps its own timing and its own model, unchanged). It keeps only instances whose class
is holdable (body.HOLDABLE) and whose mask comes within NEAR_WRIST_PX of a wrist keypoint the pose model scored at least
WRIST_SCORE_MIN, as polygons in RGB pixels. body.py turns the one that meets measured depth near a wrist into the held
object's points and label.

The weights load only from the models folder, and only when their sha256 is the one pinned here (HELD_SHA256):
Ultralytics' yolo26s-seg.pt from its v8.4.0 release assets, 23,467,933 bytes, AGPL-3.0 as yolo26s.pt is (the owner's
yes, 2026-11-14). release.py ships them and install.sh puts them in the service's models folder, both held to this
pin. Without the file, or with another, the pass stays off and every frame is exactly as before.
"""
import hashlib
from pathlib import Path

from body import HOLDABLE, MAX_POLYGON

HELD_WEIGHTS = "yolo26s-seg.pt"
HELD_SHA256 = "3da1d83e31caec96f9300eb4064f4f62882c133c7c264d63dfe61a7c197837a4"   # GitHub's published digest
SCORE_MIN = 0.35
WRIST_SCORE_MIN = 0.3
NEAR_WRIST_PX = 80            # in the 640x360 RGB frame: generous, body.py applies the metric reach with depth
WRISTS = (9, 10)              # COCO-17 left and right wrist


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(models, device, *, pinned=HELD_SHA256, yolo=None):
    """(the segmenter, None) or (None, why it's off). why is words only."""
    if pinned is None:
        return None, "no held-object weights are pinned"
    path = Path(models) / HELD_WEIGHTS
    if not path.is_file():
        return None, "the held-object weights aren't in the models folder"
    if sha256(path) != pinned:
        return None, "the held-object weights aren't the pinned ones"
    if yolo is None:
        from ultralytics import YOLO as yolo
    return Segmenter(yolo(str(path), task="segment"), device), None


class Segmenter:
    def __init__(self, model, device):
        self.model, self.device = model, device

    def __call__(self, image, row, cv2):
        """Holdable instances near a wrist in this frame (instances()), from one segmentation pass on image."""
        result = self.model.predict(image, verbose=False, device=self.device, conf=SCORE_MIN)[0]
        return instances(result, row, cv2)


def _wrists(row):
    out = []
    for detection in (row or {}).get("detections") or []:
        keypoints = detection.get("keypoints") if isinstance(detection, dict) else None
        if not isinstance(keypoints, (list, tuple)) or len(keypoints) != 17:
            continue
        for index in WRISTS:
            point = keypoints[index]
            if (isinstance(point, (list, tuple)) and len(point) == 3
                    and all(type(v) in (int, float) for v in point) and point[2] >= WRIST_SCORE_MIN):
                out.append((float(point[0]), float(point[1])))
    return out


def instances(result, row, cv2):
    """[{label, score, polygon}] for holdable instances whose mask comes within NEAR_WRIST_PX of a scored wrist: an
    Ultralytics segment result's names, boxes.cls, boxes.conf and masks.xy, in the image's own pixels. Polygons over
    MAX_POLYGON vertices keep MAX_POLYGON of them, evenly spaced."""
    import numpy as np

    wrists = _wrists(row)
    masks = getattr(result, "masks", None)
    if not wrists or masks is None or getattr(result, "boxes", None) is None:
        return []
    names = result.names
    classes = [int(c) for c in result.boxes.cls.tolist()]
    scores = [float(s) for s in result.boxes.conf.tolist()]
    out = []
    for cls, score, polygon in zip(classes, scores, masks.xy):
        label = names.get(cls) if isinstance(names, dict) else names[cls]
        points = np.asarray(polygon, np.float32)
        if label not in HOLDABLE or score < SCORE_MIN or points.ndim != 2 or len(points) < 3:
            continue
        contour = points.reshape(-1, 1, 2)
        if max(cv2.pointPolygonTest(contour, wrist, True) for wrist in wrists) < -NEAR_WRIST_PX:
            continue
        if len(points) > MAX_POLYGON:
            points = points[np.linspace(0, len(points) - 1, MAX_POLYGON, dtype=int)]
        out.append({"label": label, "score": round(score, 3), "polygon": [[round(float(x), 1), round(float(y), 1)]
                                                                          for x, y in points]})
    return out

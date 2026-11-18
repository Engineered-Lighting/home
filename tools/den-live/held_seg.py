"""Pinned optional YOLO26s-seg masks in RGB pixels for this exact detector frame.

The separate optional worker owns model loading and inference. This adapter keeps person
masks and holdable instances near a supported wrist, with bounded finite polygons.
Weights are local-only, pinned to the candidate reviewed at 3591ba7c. Loading never
downloads or changes den-detect's tracking detector or pose model.
"""
import hashlib
import math
import time
from pathlib import Path

from body import HOLDABLE, MAX_POLYGON

HELD_WEIGHTS = "yolo26s-seg.pt"
HELD_SHA256 = "3da1d83e31caec96f9300eb4064f4f62882c133c7c264d63dfe61a7c197837a4"
SCORE_MIN, WRIST_SCORE_MIN = 0.35, 0.3
NEAR_WRIST_PX = 80
MAX_PERSON_MASKS, MAX_HELD_MASKS = 4, 8
WRISTS = (9, 10)
WIDTH, HEIGHT = 640, 360


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def weights_reason(models, pinned=HELD_SHA256):
    """Return fixed words when optional weights are unavailable, without importing model code."""
    if not pinned:
        return "no held-object weights are pinned"
    path = Path(models) / HELD_WEIGHTS
    if not path.is_file():
        return "the held-object weights aren't in the models folder"
    if sha256(path) != pinned:
        return "the held-object weights aren't the pinned ones"
    return None


def load(models, device="cpu", *, pinned=HELD_SHA256, yolo=None, state=None, predictor_factory=None):
    why = weights_reason(models, pinned)
    if why:
        return None, why
    if yolo is None:
        from ultralytics import YOLO as yolo
    segmenter = Segmenter(yolo(str(Path(models) / HELD_WEIGHTS), task="segment"), device, state=state,
                          predictor_factory=predictor_factory)
    try:
        import numpy as np
        segmenter.predict(np.zeros((HEIGHT, WIDTH, 3), np.uint8))
    except Exception as exc:
        return None, "the held-object pass failed its first run (%s)" % type(exc).__name__
    return segmenter, None


def cpu_mask_predictor(base=None):
    """Keep the pinned CPU crop, upsample and scaling semantics after CUDA network inference."""
    if base is None:
        from ultralytics.models.yolo.segment.predict import SegmentationPredictor
        base = SegmentationPredictor

    class CPUReconstructionPredictor(base):
        def construct_result(self, pred, img, orig_img, img_path, proto):
            return super().construct_result(pred.cpu(), img, orig_img, img_path,
                                            proto.cpu() if proto is not None else None)

    return CPUReconstructionPredictor


class Segmenter:
    def __init__(self, model, device, *, state=None, predictor_factory=None):
        self.model, self.device = model, device
        self.predictor_factory = predictor_factory or cpu_mask_predictor
        self.predictor = None

    def predict(self, image):
        options = {"verbose": False, "device": self.device, "conf": SCORE_MIN}
        if str(self.device).startswith("cuda"):
            if self.predictor is None:
                self.predictor = self.predictor_factory()
            options.update(predictor=self.predictor, half=False)
        return self.model.predict(image, **options)[0]

    def __call__(self, image, row, cv2):
        return instances(self.predict(image), row, cv2)


class Failures:
    """The live producer's type-only failure counts, logged at most once a minute."""
    def __init__(self, clock=time.monotonic, every_s=60.0):
        self.clock, self.every_s = clock, every_s
        self.since, self.names = clock(), {}

    def count(self, name=None):
        if name is not None:
            self.names[name] = self.names.get(name, 0) + 1
        now = self.clock()
        if now - self.since < self.every_s:
            return None
        line = None
        if self.names:
            line = "den-live: in the last %.0f s the held-object pass failed %d times (%s); those frames held nothing" % (
                now - self.since, sum(self.names.values()),
                ", ".join("%s %d" % (key, value) for key, value in sorted(self.names.items())))
        self.since, self.names = now, {}
        return line


def _wrists(row):
    out = []
    for detection in (row or {}).get("detections") or []:
        if not isinstance(detection, dict):
            continue
        points = detection.get("keypoints")
        if detection.get("class") != "person" or not isinstance(points, (list, tuple)) or len(points) != 17:
            continue
        for index in WRISTS:
            point = points[index]
            if (isinstance(point, (list, tuple)) and len(point) == 3
                    and all(type(v) in (int, float) and math.isfinite(v) for v in point)
                    and 0 <= point[0] < WIDTH and 0 <= point[1] < HEIGHT
                    and WRIST_SCORE_MIN <= point[2] <= 1):
                out.append((float(point[0]), float(point[1])))
    return out


def instances(result, row, cv2):
    """Bounded person and held-object polygons. Their identities remain frame-local."""
    import numpy as np

    out = {"person_masks": [], "held_masks": []}
    masks, boxes = getattr(result, "masks", None), getattr(result, "boxes", None)
    if masks is None or boxes is None:
        return out
    wrists = _wrists(row)
    names = result.names
    classes = boxes.cls.tolist()
    scores = boxes.conf.tolist()
    for cls, score, polygon in zip(classes, scores, masks.xy):
        if not math.isfinite(float(cls)) or int(cls) != cls or not math.isfinite(float(score)):
            continue
        label = names.get(int(cls)) if isinstance(names, dict) else names[int(cls)]
        target = "person_masks" if label == "person" else "held_masks"
        limit = MAX_PERSON_MASKS if label == "person" else MAX_HELD_MASKS
        if (label != "person" and (label not in HOLDABLE or not wrists)) or len(out[target]) >= limit:
            continue
        points = np.asarray(polygon, np.float32)
        if (not SCORE_MIN <= score <= 1 or points.ndim != 2 or points.shape[1] != 2
                or len(points) < 3 or not np.isfinite(points).all()):
            continue
        points[:, 0] = np.clip(points[:, 0], 0, WIDTH - 1)
        points[:, 1] = np.clip(points[:, 1], 0, HEIGHT - 1)
        if abs(cv2.contourArea(points)) < 1:
            continue
        if label != "person" and max(cv2.pointPolygonTest(points, wrist, True) for wrist in wrists) < -NEAR_WRIST_PX:
            continue
        if len(points) > MAX_POLYGON:
            points = points[np.linspace(0, len(points) - 1, MAX_POLYGON, dtype=int)]
        item = {"score": round(float(score), 3),
                "polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in points]}
        if label == "person":
            item["box_xyxy"] = [float(points[:, 0].min()), float(points[:, 1].min()),
                                float(points[:, 0].max()), float(points[:, 1].max())]
        else:
            item["label"] = label
        out[target].append(item)
    return out

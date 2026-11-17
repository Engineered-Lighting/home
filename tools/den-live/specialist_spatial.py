"""Pure exact-frame COCO landmark adapters for optional specialists. Standard library only.

Crop geometry and layout are trusted request inputs, echoed by the result. Source pixels are the complete native
RGB frame. The model-to-source affine includes the crop origin, scale, rotation and any letterbox translation.
The adapter produces separate evidence and never changes the main detector's COCO-17 row.
"""
import copy
import math
from dataclasses import dataclass

from contracts import PERCEPTION_S, FrameRef, PlaneRef

LAYOUT_POINTS = {"coco17_identity_v1": 17, "coco_wholebody133_first17_v1": 133}
NATIVE_VARIANCE_FLOOR = 16.0
MAX_POINTS = 133


class AdapterRefused(ValueError):
    """A fixed rejection name, without input data or exception text."""


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 128


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _immutable(value):
    return tuple(_immutable(v) for v in value) if isinstance(value, (list, tuple)) else value


def _size(value):
    return isinstance(value, tuple) and len(value) == 2 and all(type(v) is int and v > 0 for v in value)


def _plane(value):
    return (isinstance(value, PlaneRef) and type(value.seq) is int and value.seq >= 0
            and _finite(value.host_s) and value.host_s >= 0
            and (value.device_s is None or _finite(value.device_s) and value.device_s >= 0)
            and _size((value.width, value.height)))


def _frame(value):
    return (isinstance(value, FrameRef) and _text(value.service_run_id) and _text(value.camera_session)
            and type(value.camera_epoch) is int and value.camera_epoch >= 0
            and type(value.frame_id) is int and value.frame_id > 0 and value.mode == "rgb"
            and _plane(value.rgb) and (value.depth is None or _plane(value.depth))
            and (value.right is None or _plane(value.right)) and _text(value.transform_id)
            and _text(value.scan_revision) and _text(value.depth_calibration_sha256)
            and all(_finite(v) for v in (value.capture_mono, value.arrival_mono, value.capture_ts))
            and value.capture_mono >= 0 and value.arrival_mono >= 0)


@dataclass(frozen=True)
class SpecialistBinding:
    frame: FrameRef
    track_id: str
    association_revision: int
    detection_index: int
    detection_id: str
    observation_ids: tuple[str, ...]
    profile_sha256: str
    request_id: str
    engine_epoch: str
    parent_valid_until_mono: float
    deadline_s: float
    track_state: str = "confirmed"
    association_status: str = "confident"

    def __post_init__(self):
        if (not isinstance(self.observation_ids, (list, tuple)) or not 1 <= len(self.observation_ids) <= 64
                or not all(_text(v) for v in self.observation_ids)):
            raise AdapterRefused("binding_invalid")
        object.__setattr__(self, "observation_ids", _immutable(self.observation_ids))
        if (not _frame(self.frame) or not all(_text(v) for v in
                (self.track_id, self.detection_id, self.request_id, self.engine_epoch))
                or type(self.association_revision) is not int or self.association_revision < 0
                or type(self.detection_index) is not int or not 0 <= self.detection_index < 64
                or not isinstance(self.observation_ids, tuple) or not self.observation_ids
                or not all(_text(v) for v in self.observation_ids) or len(set(self.observation_ids)) != len(self.observation_ids)
                or not _sha(self.profile_sha256) or not _finite(self.parent_valid_until_mono)
                or not _finite(self.deadline_s) or not 0 < self.deadline_s <= PERCEPTION_S
                or not _text(self.track_state) or not _text(self.association_status)
                or not self.frame.capture_mono < self.parent_valid_until_mono <= self.frame.capture_mono + PERCEPTION_S):
            raise AdapterRefused("binding_invalid")

    @property
    def valid_until_mono(self):
        return min(self.parent_valid_until_mono, self.frame.capture_mono + self.deadline_s)


@dataclass(frozen=True)
class CropAffine:
    native_size: tuple[int, int]
    source_size: tuple[int, int]
    model_size: tuple[int, int]
    crop_xyxy: tuple[float, float, float, float]
    source_from_model: tuple[tuple[float, float, float], tuple[float, float, float]]

    def __post_init__(self):
        sizes = self.native_size, self.source_size, self.model_size
        if (any(not isinstance(size, (list, tuple)) or len(size) != 2
                or not all(type(v) is int and v > 0 for v in size) for size in sizes)
                or not isinstance(self.crop_xyxy, (list, tuple)) or len(self.crop_xyxy) != 4
                or not all(_finite(v) for v in self.crop_xyxy)
                or not isinstance(self.source_from_model, (list, tuple)) or len(self.source_from_model) != 2
                or any(not isinstance(row, (list, tuple)) or len(row) != 3 or not all(_finite(v) for v in row)
                       for row in self.source_from_model)):
            raise AdapterRefused("geometry_invalid")
        for name in ("native_size", "source_size", "model_size", "crop_xyxy", "source_from_model"):
            object.__setattr__(self, name, _immutable(getattr(self, name)))
        left, top, right, bottom = self.crop_xyxy
        width, height = self.source_size
        (a, b, _), (c, d, _) = self.source_from_model
        determinant = a * d - b * c
        if (not 0 <= left < right <= width or not 0 <= top < bottom <= height
                or not math.isfinite(determinant) or determinant == 0):
            raise AdapterRefused("geometry_invalid")
        if not all(math.isfinite(v / determinant) for v in (a, b, c, d)):
            raise AdapterRefused("geometry_invalid")


@dataclass(frozen=True)
class Coco17Layout:
    """Caller-validated layout for an approved profile, supplied with the trusted request."""
    token: str
    profile_sha256: str

    def __post_init__(self):
        if not isinstance(self.token, str) or self.token not in LAYOUT_POINTS or not _sha(self.profile_sha256):
            raise AdapterRefused("layout_invalid")


@dataclass(frozen=True)
class ModelLandmark:
    index: int
    xy: tuple[float, float]
    covariance: tuple[tuple[float, float], tuple[float, float]] | None
    score: float

    def __post_init__(self):
        if (not isinstance(self.xy, (list, tuple)) or len(self.xy) != 2
                or not all(_finite(v) for v in self.xy)
                or self.covariance is not None and (not isinstance(self.covariance, (list, tuple))
                    or len(self.covariance) != 2
                    or any(not isinstance(row, (list, tuple)) or len(row) != 2 or not all(_finite(v) for v in row)
                           for row in self.covariance))):
            raise AdapterRefused("landmark_invalid")
        object.__setattr__(self, "xy", _immutable(self.xy))
        object.__setattr__(self, "covariance", _immutable(self.covariance))


@dataclass(frozen=True)
class SpecialistResult:
    binding: SpecialistBinding
    crop: CropAffine
    layout_token: str
    landmarks: tuple[ModelLandmark, ...]

    def __post_init__(self):
        if not isinstance(self.landmarks, (list, tuple)) or len(self.landmarks) > MAX_POINTS:
            raise AdapterRefused("landmark_count")
        if not all(isinstance(point, ModelLandmark) for point in self.landmarks):
            raise AdapterRefused("landmark_invalid")
        object.__setattr__(self, "landmarks", _immutable(self.landmarks))


@dataclass(frozen=True)
class SourceLandmark:
    index: int
    pixel: tuple[float, float] | None = None
    covariance: tuple[tuple[float, float], tuple[float, float]] | None = None
    score: float | None = None
    xyz_room: tuple[float, float, float] | None = None
    covariance_room: tuple[tuple[float, ...], ...] | None = None
    status: str = "unsupported"
    lift: str = "none"
    provenance: str = "unsupported"
    reason: str | None = None


@dataclass(frozen=True)
class Adaptation:
    binding: SpecialistBinding
    valid_until_mono: float
    landmarks: tuple[SourceLandmark, ...] = ()
    reason: str | None = None


def _covariance2(value):
    if (not isinstance(value, tuple) or len(value) != 2
            or any(not isinstance(row, tuple) or len(row) != 2 or not all(_finite(v) for v in row) for row in value)):
        return False
    (a, b), (c, d) = value
    return b == c and a >= 0 and d >= 0 and a * d - b * b >= 0


def _minimum_eigenvalue(xx, xy, yy):
    return xx / 2 + yy / 2 - math.hypot(xx / 2 - yy / 2, xy)


def native_covariance(covariance, source_from_model, *, variance_floor=NATIVE_VARIANCE_FLOOR):
    """A Sigma A^T with the minimal isotropic shift enforcing the trusted native pixel variance floor."""
    covariance, affine = _immutable(covariance), _immutable(source_from_model)
    if (not _covariance2(covariance) or not _finite(variance_floor) or variance_floor < NATIVE_VARIANCE_FLOOR
            or not isinstance(affine, tuple) or len(affine) != 2
            or any(not isinstance(row, tuple) or len(row) != 3 or not all(_finite(v) for v in row) for row in affine)):
        raise AdapterRefused("covariance_invalid")
    (a, b, _), (c, d, _) = affine
    x, cross, y = covariance[0][0], covariance[0][1], covariance[1][1]
    try:
        xx = math.fsum((a * a * x, 2 * a * b * cross, b * b * y))
        xy = math.fsum((a * c * x, (a * d + b * c) * cross, b * d * y))
        yy = math.fsum((c * c * x, 2 * c * d * cross, d * d * y))
    except (ValueError, OverflowError):
        raise AdapterRefused("covariance_invalid") from None
    if not all(_finite(v) for v in (xx, xy, yy)):
        raise AdapterRefused("covariance_invalid")
    minimum = _minimum_eigenvalue(xx, xy, yy)
    if not _finite(minimum):
        raise AdapterRefused("covariance_invalid")
    shift = max(0.0, variance_floor - minimum)
    out_xx, out_yy = xx + shift, yy + shift
    if not all(_finite(v) for v in (out_xx, out_yy)):
        raise AdapterRefused("covariance_invalid")
    # One directed rounding correction preserves the common diagonal shift. If the floor is unrepresentable at
    # this numeric scale, withhold the point instead of returning a covariance below the promised floor.
    remaining = variance_floor - _minimum_eigenvalue(out_xx, xy, out_yy)
    if remaining > 0:
        shift = math.nextafter(shift + remaining, math.inf)
        out_xx, out_yy = xx + shift, yy + shift
    if (not all(_finite(v) for v in (out_xx, out_yy))
            or _minimum_eigenvalue(out_xx, xy, out_yy) < variance_floor):
        raise AdapterRefused("covariance_invalid")
    return ((out_xx, xy), (xy, out_yy))


def _mapped(point, geometry, variance_floor):
    if (not isinstance(point.xy, tuple) or len(point.xy) != 2 or not all(_finite(v) for v in point.xy)
            or not _finite(point.score) or point.score < 0):
        return None, None, "landmark_invalid"
    x, y = point.xy
    if not 0 <= x < geometry.model_size[0] or not 0 <= y < geometry.model_size[1]:
        return None, None, "outside_model"
    (a, b, tx), (c, d, ty) = geometry.source_from_model
    pixel = a * x + b * y + tx, c * x + d * y + ty
    left, top, right, bottom = geometry.crop_xyxy
    if (not all(_finite(v) for v in pixel) or not left <= pixel[0] < right or not top <= pixel[1] < bottom
            or not 0 <= pixel[0] < geometry.source_size[0] or not 0 <= pixel[1] < geometry.source_size[1]):
        return None, None, "outside_crop"
    try:
        covariance = native_covariance(point.covariance, geometry.source_from_model, variance_floor=variance_floor)
    except (AdapterRefused, ValueError, OverflowError):
        return pixel, None, "covariance_invalid"
    return pixel, covariance, None


def _room_covariance(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(not isinstance(row, (list, tuple)) or len(row) != 3 or not all(_finite(v) for v in row) for row in value)):
        return None
    if any(value[i][i] < 0 or any(abs(value[i][j] - value[j][i]) > 1e-9 for j in range(3)) for i in range(3)):
        return None
    scale = max(1e-15, max(abs(v) for row in value for v in row))
    if scale > 1e4:
        return None
    if any(value[i][i] * value[j][j] - value[i][j] ** 2 < -1e-9 * scale ** 2
           for i in range(3) for j in range(i)):
        return None
    (a, b, c), (_, d, e), (_, _, f) = value
    if a * d * f + 2 * b * c * e - a * e * e - d * c * c - f * b * b < -1e-9 * scale ** 3:
        return None
    return _immutable(value)


def _lifted(index, pixel, covariance, score, entry):
    status, lift = entry.get("status"), entry.get("lift")
    if status == "unsupported" and lift == "none":
        return SourceLandmark(index, pixel, covariance, score, reason="no_room_support")
    measured = status == "supported" and lift == "stereo"
    inferred = status == "uncertain" and isinstance(lift, str) and lift.startswith("plane:")
    if inferred:
        try:
            inferred = math.isfinite(float(lift.removeprefix("plane:")))
        except ValueError:
            inferred = False
    xyz, room_covariance = entry.get("xyz"), _room_covariance(entry.get("cov"))
    if (not (measured or inferred) or not isinstance(xyz, (list, tuple)) or len(xyz) != 3
            or not all(_finite(v) for v in xyz) or room_covariance is None or entry.get("j") != index):
        return SourceLandmark(index, pixel, covariance, score, reason="lift_invalid")
    return SourceLandmark(index, pixel, covariance, score, tuple(xyz), room_covariance, status, lift,
                          "measured" if measured else "inferred")


def adapt(result, *, expected, geometry, layout, now_mono, camera, depth, settings, lift_joint):
    """Adapt at most 133 points into separate native COCO-17 evidence for the current exact owner.

    Each point follows the existing observer's settings.score_min, currently 0.4. A head-direction consumer may
    apply its separate 0.5 landmark gate. Neither threshold promotes an absent covariance or missing landmark.
    """
    if not isinstance(expected, SpecialistBinding):
        raise AdapterRefused("binding_invalid")

    def refused(reason):
        return Adaptation(expected, expected.valid_until_mono, reason=reason)

    if expected.track_state != "confirmed" or expected.association_status != "confident":
        return refused("association_unsafe")
    if (not _finite(now_mono) or now_mono < 0 or now_mono < expected.frame.capture_mono
            or now_mono >= expected.valid_until_mono):
        return refused("expired")
    if not isinstance(result, SpecialistResult) or result.binding != expected:
        return refused("binding_mismatch")
    if not isinstance(geometry, CropAffine) or result.crop != geometry:
        return refused("geometry_mismatch")
    native_size = expected.frame.rgb.width, expected.frame.rgb.height
    if geometry.source_size != geometry.native_size or geometry.native_size != native_size:
        return refused("different_source_size")
    if (not isinstance(layout, Coco17Layout) or layout.profile_sha256 != expected.profile_sha256
            or result.layout_token != layout.token):
        return refused("layout_mismatch")
    rgb_size = getattr(getattr(camera, "rig", None), "rgb_size", None)
    if (getattr(camera, "transform_id", None) != expected.frame.transform_id
            or not isinstance(rgb_size, (list, tuple)) or tuple(rgb_size) != native_size):
        return refused("camera_mismatch")
    if (not all(_finite(getattr(settings, name, None)) for name in ("max_gap_s", "score_min", "pixel_sigma_px"))
            or min(settings.max_gap_s, settings.score_min, settings.pixel_sigma_px) < 0
            or not _finite(settings.pixel_sigma_px * settings.pixel_sigma_px) or not callable(lift_joint)):
        return refused("lift_context_invalid")
    plane = expected.frame.depth
    if depth is not None and (plane is None
            or (getattr(depth, "width", None), getattr(depth, "height", None)) != (plane.width, plane.height)
            or getattr(depth, "sequence", None) != plane.seq or getattr(depth, "t_host", None) != plane.host_s):
        return refused("depth_mismatch")
    points = result.landmarks
    if not isinstance(points, tuple) or len(points) > MAX_POINTS:
        return refused("landmark_count")
    indexed = {}
    for point in points:
        if (not isinstance(point, ModelLandmark) or type(point.index) is not int
                or not 0 <= point.index < LAYOUT_POINTS[layout.token] or point.index in indexed):
            return refused("landmark_indices")
        indexed[point.index] = point
    depth_ok = (depth is not None and plane is not None
                and abs(plane.host_s - expected.frame.rgb.host_s) <= settings.max_gap_s)
    landmarks = []
    variance_floor = max(4.0, settings.pixel_sigma_px) ** 2
    for index in range(17):
        point = indexed.get(index)
        if point is None:
            landmarks.append(SourceLandmark(index, reason="missing_landmark"))
            continue
        pixel, covariance, reason = _mapped(point, geometry, variance_floor)
        if reason:
            landmarks.append(SourceLandmark(index, pixel, covariance, reason=reason))
            continue
        if point.score < settings.score_min:
            landmarks.append(SourceLandmark(index, pixel, covariance, point.score, reason="low_score"))
            continue
        try:
            local_settings = copy.copy(settings)
            (xx, xy), (_, yy) = covariance
            largest = (xx + yy + math.hypot(xx - yy, 2 * xy)) / 2
            local_settings.pixel_sigma_px = max(4.0, settings.pixel_sigma_px, math.sqrt(largest))
            entry, _parts = lift_joint(camera, local_settings, depth, depth_ok, index, (*pixel, point.score), native_size)
            landmark = _lifted(index, pixel, covariance, point.score, entry)
        except Exception:
            landmark = SourceLandmark(index, pixel, covariance, point.score, reason="lift_failed")
        landmarks.append(landmark)
    return Adaptation(expected, expected.valid_until_mono, tuple(landmarks))

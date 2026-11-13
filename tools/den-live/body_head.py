"""Private RGB head and upper-neck evidence in rectified-left depth pixels."""
import math
from dataclasses import dataclass, fields
from itertools import product


# den-observe's COCO-17 order: nose, eyes, ears, then shoulders.
FACE = (0, 1, 2, 3, 4)
SHOULDERS = (5, 6)
ANCHORS = FACE + SHOULDERS


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class HeadProfile:
    """Commissioned limits for the subject and view this profile covers.

    Head radius encloses the complete head around the mean of the five face
    landmarks. Neck radius encloses the upper neck around the segment from that
    mean to the shoulder midpoint. Localization bounds anchor error in metres.
    Alignment bounds remaining projection error directly in depth pixels.
    Motion bounds movement over the qualified exposure separation.
    """

    head_radius_m: float
    neck_radius_m: float
    max_head_shoulder_m: float
    alignment_px: float
    localization_m: float
    max_motion_m_s: float
    max_separation_s: float

    def __post_init__(self):
        if any(not _finite(getattr(self, item.name)) or getattr(self, item.name) <= 0 for item in fields(self)):
            raise ValueError("head profile limits must be positive and finite")


def _mean(points):
    return tuple(sum(axis) / len(points) for axis in zip(*points))


def _measured_points(pose):
    """Use the supported stereo anchors from a single complete pose."""
    if (not isinstance(pose, dict) or pose.get("kind") != "keypoints"
            or pose.get("joint_set") != "coco17" or pose.get("validity") != "valid"
            or pose.get("visibility") != "visible"):
        return None
    joints = pose.get("joints_room")
    if not isinstance(joints, (tuple, list)):
        return None
    measured = {}
    for joint in joints:
        if not isinstance(joint, dict):
            return None
        index = joint.get("j")
        if type(index) is not int or index not in ANCHORS:
            continue
        xyz = joint.get("xyz")
        if (index in measured or joint.get("status") != "supported" or joint.get("lift") != "stereo"
                or not isinstance(xyz, (tuple, list)) or len(xyz) != 3 or not all(map(_finite, xyz))):
            return None
        measured[index] = xyz
    # Repeated stereo lifts can land on an occluder even when each keypoint
    # found depth. Require separate support for every face and neck anchor.
    if len({tuple(point) for point in measured.values()}) != len(ANCHORS):
        return None
    return measured


def _matches_native_pixels(pose, rig, points):
    """Check the lifted anchors against the pinned lift's RGB search support."""
    from lift import PATCH_RADIUS_PX

    box, pixels = pose.get("image_box"), pose.get("keypoints")
    if (not isinstance(box, (tuple, list)) or len(box) != 4 or not all(map(_finite, box))
            or not isinstance(pixels, (tuple, list)) or len(pixels) != 17):
        return False
    width, height = rig.rgb_size
    left, top, right, bottom = box
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        return False
    for index, point in points.items():
        pixel = pixels[index]
        if (not isinstance(pixel, (tuple, list)) or len(pixel) != 3 or not all(map(_finite, pixel))
                or not (left <= pixel[0] <= right and top <= pixel[1] <= bottom and 0 < pixel[2] <= 1)):
            return False
        try:
            reprojection = rig.to_rgb(point)
        except ZeroDivisionError:  # rational distortion is undefined for this point
            return False
        # den-observe rounds RGB pixels and metric positions for its records.
        # Reprojection is a consistency check, not the localization margin.
        if (reprojection is None or not all(map(_finite, reprojection))
                or math.dist(pixel[:2], reprojection) > PATCH_RADIUS_PX):
            return False
    return True


def _projection(volumes, intrinsics, expand_m):
    # Imported after Perceiver.Tools adds the pinned den-observe tree to sys.path.
    from camgeom import project

    pixels = []
    for anchors, radius in volumes:
        pad = radius + expand_m
        # Positive-depth pinhole coordinates attain their extrema at the box
        # corners. This bounds the enclosed sphere or capsule at any pose angle.
        axes = [(min(axis) - pad, max(axis) + pad) for axis in zip(*anchors)]
        if axes[2][0] <= 1e-6:
            return None
        pixels.extend(project(point, intrinsics) for point in product(*axes))
    columns, rows = zip(*pixels)
    bounds = min(columns), min(rows), max(columns), max(rows)
    return bounds if all(map(_finite, bounds)) else None


def _outward_margin(inner, outer):
    return max(inner[0] - outer[0], inner[1] - outer[1],
               outer[2] - inner[2], outer[3] - inner[3], 0.0)


def head_evidence(pose, camera, *, frame_key, binding, profile, separation_s):
    """Return private BodyInput evidence for the caller's same-frame observation.

    The caller binds the observation to the current track and supplies the full
    exposure separation, including timing uncertainty and readout. A fit that
    exceeds commissioning or leaves the registered depth image is unusable.
    """
    from camgeom import apply, inverse

    if (not isinstance(profile, HeadProfile) or frame_key is None
            or not isinstance(binding, dict) or binding.get("transform_id") != camera.transform_id
            or not isinstance(pose, dict) or pose.get("transform_id") != camera.transform_id
            or not _finite(separation_s) or not 0 <= separation_s <= profile.max_separation_s):
        return None
    measured = _measured_points(pose)
    if measured is None:
        return None
    face = [measured[index] for index in FACE]
    shoulders = [measured[index] for index in SHOULDERS]
    head, neck_base = _mean(face), _mean(shoulders)
    if (not 0 < math.dist(head, neck_base) <= profile.max_head_shoulder_m
            or max(math.dist(point, head) for point in face) > profile.head_radius_m):
        return None

    to_depth = inverse(*camera.room_from_left)
    points_left = {index: apply(*to_depth, point) for index, point in measured.items()}
    if not _matches_native_pixels(pose, camera.rig, points_left):
        return None
    head = apply(*to_depth, head)
    neck_base = apply(*to_depth, neck_base)
    volumes = (((head,), profile.head_radius_m), ((head, neck_base), profile.neck_radius_m))
    intrinsics = camera.rig.left_k
    bounds = _projection(volumes, intrinsics, 0)
    localized = _projection(volumes, intrinsics, profile.localization_m)
    moved = _projection(volumes, intrinsics, profile.localization_m + profile.max_motion_m_s * separation_s)
    if bounds is None or localized is None or moved is None:
        return None
    localization_px = _outward_margin(bounds, localized)
    motion_px = _outward_margin(localized, moved)
    padding = profile.alignment_px + localization_px + motion_px
    width, height = camera.rig.depth_size
    if not (0 <= bounds[0] - padding and bounds[2] + padding < width
            and 0 <= bounds[1] - padding and bounds[3] + padding < height):
        return None
    return {"frame_key": frame_key, "binding": binding, "bounds_px": list(bounds),
            "alignment_px": profile.alignment_px, "localization_px": localization_px, "motion_px": motion_px}

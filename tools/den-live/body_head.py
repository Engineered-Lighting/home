"""Private RGB head and upper-neck evidence in rectified-left depth pixels."""
import math
from dataclasses import dataclass, fields
from typing import Optional
from itertools import product


# den-observe's COCO-17 order: nose, eyes, ears, then shoulders.
FACE = (0, 1, 2, 3, 4)
SHOULDERS = (5, 6)
HIPS = (11, 12)
ANCHORS = FACE + SHOULDERS
# The shortest 2D torso, hips to shoulders in depth pixels, whose direction the shoulder-line cut trusts.
TORSO_AXIS_PX = 20
# A hip without a lift is placed from its 2D keypoint at the shoulders' depth when the detector gives it this score,
# even a little outside the RGB image (a desk or the frame's edge hides it; the detector extrapolates), up to this
# fraction of the image beyond each edge.
HIP_MIN_SCORE, HIP_OUTSIDE = 0.3, 0.25
# Seen from the front or back the torso axis crosses the shoulder line square: it may lean this far off square
# (cosine 0.57, 35 degrees) once the shoulder line is long enough in the image to say.
AXIS_MAX_COS, SHOULDER_LINE_MIN = 0.57, 0.4


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
    torso_cut_m, when set, turns on the shoulder-line cut: the qualified depth below the line through the shoulders,
    across the hip-to-shoulder axis, within which the head and upper neck can reach (a bowed head's chin).
    """

    head_radius_m: float
    neck_radius_m: float
    max_head_shoulder_m: float
    alignment_px: float
    localization_m: float
    max_motion_m_s: float
    max_separation_s: float
    torso_cut_m: Optional[float] = None

    def __post_init__(self):
        if any(not _finite(getattr(self, item.name)) or getattr(self, item.name) <= 0 for item in fields(self)
               if item.name != "torso_cut_m"):
            raise ValueError("head profile limits must be positive and finite")
        if self.torso_cut_m is not None and (not _finite(self.torso_cut_m) or not 0 < self.torso_cut_m <= 0.5):
            raise ValueError("the shoulder-line cut must be positive, finite and at most 0.5 m")


def _mean(points):
    return tuple(sum(axis) / len(points) for axis in zip(*points))


def torso_cut(pose, camera, *, frame_key, binding, profile, separation_s):
    """Private evidence for the shoulder-line cut on the caller's same-frame observation, or None.

    It needs both shoulders with this frame's lifts, and a hip: lifted, or else its 2D keypoint, scored at least
    HIP_MIN_SCORE, placed at the shoulders' depth (at a desk or behind a book a hip often has no depth). Each joint is
    placed in the rectified-left depth image; a lift that slipped onto the surface behind, or a hip at the shoulders'
    depth, moves it there by only the RGB-to-left parallax, a pixel or two, which the margin covers. The hips set only
    the line's direction, and that direction must cross the shoulder line nearly square when the shoulder line is long
    enough to judge. The line runs through the shoulder midpoint across the 2D torso axis. The margin is the qualified
    cut plus the localization and motion bounds, as the envelope's. No face landmark is needed, so it holds for a
    turned-away, bowed or reclined head, and for one the RGB view cuts off."""
    from camgeom import apply, inverse, project

    if (not isinstance(profile, HeadProfile) or profile.torso_cut_m is None or frame_key is None
            or not isinstance(binding, dict) or binding.get("transform_id") != camera.transform_id
            or not isinstance(pose, dict) or pose.get("transform_id") != camera.transform_id
            or pose.get("kind") != "keypoints" or pose.get("joint_set") != "coco17" or pose.get("validity") != "valid"
            or pose.get("visibility") not in ("visible", "partial")
            or not _finite(separation_s) or not 0 <= separation_s <= profile.max_separation_s):
        return None
    lifted, seen = {}, set()
    for joint in pose.get("joints_room") or ():
        if isinstance(joint, dict) and joint.get("j") in SHOULDERS + HIPS:
            if joint["j"] in seen:
                return None
            seen.add(joint["j"])
            xyz = joint.get("xyz")
            if (joint.get("status") == "supported" and isinstance(xyz, (tuple, list)) and len(xyz) == 3
                    and all(map(_finite, xyz))):
                lifted[joint["j"]] = xyz
    if not all(index in lifted for index in SHOULDERS):
        return None
    rig = camera.rig
    to_depth = inverse(*camera.room_from_left)
    pixels, rgb_depths = {}, []
    for index, xyz in lifted.items():
        point = apply(*to_depth, xyz)
        if point[2] <= 0.1:
            return None
        pixels[index] = project(point, rig.left_k)
        if index in SHOULDERS:
            rgb_depths.append(apply(rig.rotation, rig.translation, point)[2])
    keypoints = pose.get("keypoints")
    width, height = rig.rgb_size
    for index in HIPS:
        if index in lifted or not isinstance(keypoints, (tuple, list)) or len(keypoints) != 17:
            continue
        pixel = keypoints[index]
        if (isinstance(pixel, (tuple, list)) and len(pixel) == 3 and all(map(_finite, pixel))
                and HIP_MIN_SCORE <= pixel[2] <= 1 and -HIP_OUTSIDE * width <= pixel[0] < (1 + HIP_OUTSIDE) * width
                and -HIP_OUTSIDE * height <= pixel[1] < (1 + HIP_OUTSIDE) * height):
            placed = rig.left_pixel(rig.rgb_ray(pixel[0], pixel[1]), _mean([[z] for z in rgb_depths])[0])
            if placed is not None and all(map(_finite, placed)):
                pixels[index] = placed
    hip_pixels = [pixels[index] for index in HIPS if index in pixels]
    if not hip_pixels:
        return None
    shoulder = _mean([pixels[index] for index in SHOULDERS])
    hips = _mean(hip_pixels)
    axis = (shoulder[0] - hips[0], shoulder[1] - hips[1])
    length = math.hypot(*axis)
    if not all(map(_finite, shoulder)) or not TORSO_AXIS_PX <= length:
        return None
    across = (pixels[SHOULDERS[0]][0] - pixels[SHOULDERS[1]][0], pixels[SHOULDERS[0]][1] - pixels[SHOULDERS[1]][1])
    span = math.hypot(*across)
    if span >= SHOULDER_LINE_MIN * length and abs(axis[0] * across[0] + axis[1] * across[1]) > AXIS_MAX_COS * span * length:
        return None
    margin = profile.torso_cut_m + profile.localization_m + profile.max_motion_m_s * separation_s
    return {"frame_key": frame_key, "binding": binding,
            "cut": {"shoulder_px": [float(shoulder[0]), float(shoulder[1])],
                    "axis_px": [axis[0] / length, axis[1] / length], "margin_m": margin}}


def _measured_points(pose):
    """Use the supported stereo anchors from a single pose.

    den-observe calls a pose partial when its RGB box touches the image edge or a 2D torso joint is missing: on the
    sofa, legs leaving the frame or a hip behind a book. That says nothing about the head, so a partial pose is
    accepted here; head_evidence then requires the whole padded head to lie inside the RGB image."""
    if (not isinstance(pose, dict) or pose.get("kind") != "keypoints"
            or pose.get("joint_set") != "coco17" or pose.get("validity") != "valid"
            or pose.get("visibility") not in ("visible", "partial")):
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


def _head_inside_rgb(rig, head_left, radius_m):
    """True when every corner of the cube around the head, in the left camera's frame, projects inside the RGB image:
    the detector saw the whole head, so its face landmarks aren't guesses at a head the RGB view cuts off."""
    width, height = rig.rgb_size
    for corner in product(*[(axis - radius_m, axis + radius_m) for axis in head_left]):
        if corner[2] <= 1e-6:
            return False
        try:
            pixel = rig.to_rgb(corner)
        except ZeroDivisionError:   # rational distortion is undefined for this point
            return False
        if (pixel is None or not all(map(_finite, pixel))
                or not (0 <= pixel[0] < width and 0 <= pixel[1] < height)):
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
    if not _head_inside_rgb(camera.rig, head, profile.head_radius_m + profile.localization_m):
        return None
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

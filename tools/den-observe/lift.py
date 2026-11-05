"""Lift RGB pixels and regions to rectified-left 3D points. Standard library only.

Follows perception-lab without numpy:

- depth_association.py: depth is aligned to the rectified LEFT camera, never to
  RGB. Every depth pixel is deprojected at its own depth, moved into the RGB
  camera with the RGB-from-rectified-left transform and projected with the RGB
  intrinsics and distortion. Pixels that land in the RGB region are its support.
- head3d.DepthLifter.lift: a keypoint's support is the depth pixels landing
  within PATCH_RADIUS_PX of it; its point is the per-axis median of the front
  surface (front_cluster), with at least 3 support points.
- geometry.robust_support: a region's sample refuses a depth discontinuity and
  keeps MAD inliers; holes count against valid support.

Instead of projecting the whole 640x400 frame, only the depth pixels that can
reach the RGB pixel are visited: those near its epipolar segment between
Z_MIN_M and Z_MAX_M. The result is the same set the full projection selects.
"""
import math

import camgeom as g

PATCH_RADIUS_PX = 4.0          # head3d.PATCH_RADIUS_PX
MIN_JOINT_SUPPORT = 3          # head3d.DepthLifter.lift
FRONT_QUANTILE, FRONT_RELATIVE, FRONT_FLOOR_M = 0.2, 0.03, 0.04  # head3d.front_cluster
REGION_INSET = 0.15            # depth_association.region_mask
MIN_REGION_SUPPORT = 12        # geometry.robust_support
REGION_STRIDE = 2              # every 2nd depth pixel in each direction for regions
Z_MIN_M, Z_MAX_M = 0.3, 8.0    # depth search range along an RGB ray
TORSO = ("left_shoulder", "right_shoulder", "right_hip", "left_hip")


class Rig:
    """The camera entry's two cameras: rectified-left (depth) and RGB."""

    def __init__(self, *, left_k, rgb_k, rgb_distortion, rgb_from_left, rgb_size, depth_size):
        self.left_k = g.intrinsics(left_k, "rectified-left intrinsics")
        self.rgb_k = g.intrinsics(rgb_k, "RGB intrinsics")
        self.rgb_d = g.distortion(rgb_distortion)
        rotation, translation = rgb_from_left
        g.check_rigid(rotation, translation, "rgb_from_rectified_left")
        self.rotation, self.translation = rotation, translation
        self.left_from_rgb = g.inverse(rotation, translation)
        self.rgb_size, self.depth_size = tuple(rgb_size), tuple(depth_size)
        fl, fr = self.left_k, self.rgb_k
        self.scale = max(fl[0] / fr[0], fl[1] / fr[1])  # left pixels per RGB pixel

    def to_rgb(self, point_left):
        return g.project(g.apply(self.rotation, self.translation, point_left), self.rgb_k, self.rgb_d)

    def rgb_ray(self, u, v):
        return g.undistort(u, v, self.rgb_k, self.rgb_d)

    def left_pixel(self, ray, z):
        """The rectified-left pixel of the RGB-ray point at RGB depth z."""
        point = g.apply(*self.left_from_rgb, [ray[0] * z, ray[1] * z, z])
        return g.project(point, self.left_k)

    def segment(self, u, v):
        """Left pixels of the RGB pixel's ray from Z_MIN_M to Z_MAX_M, under 0.5 px apart."""
        ray = self.rgb_ray(u, v)
        near, far = self.left_pixel(ray, Z_MIN_M), self.left_pixel(ray, Z_MAX_M)
        if near is None or far is None:
            return []
        steps = max(2, int(math.ceil(2 * math.dist(near, far))) + 1)
        inverse_near, inverse_far = 1 / Z_MIN_M, 1 / Z_MAX_M
        out = []
        for i in range(steps + 1):
            z = 1 / (inverse_near + (inverse_far - inverse_near) * i / steps)
            pixel = self.left_pixel(ray, z)
            if pixel is not None:
                out.append(pixel)
        return out


def _candidates(rig, centres, radius_left, stride=1):
    width, height = rig.depth_size
    seen = set()
    reach = int(math.ceil(radius_left))
    for cu, cv in centres:
        iu, iv = int(round(cu)), int(round(cv))
        for y in range(max(0, iv - reach), min(height, iv + reach + 1)):
            if y % stride:
                continue
            for x in range(max(0, iu - reach), min(width, iu + reach + 1)):
                if x % stride or (x, y) in seen:
                    continue
                if (x - cu) ** 2 + (y - cv) ** 2 <= radius_left ** 2:
                    seen.add((x, y))
    return seen


def front_cluster(points):
    reference = g.quantile([p[2] for p in points], FRONT_QUANTILE)
    tolerance = max(FRONT_FLOOR_M, FRONT_RELATIVE * reference)
    return [p for p in points if abs(p[2] - reference) <= tolerance]


def per_axis_median(points):
    return [g.median([p[i] for p in points]) for i in range(3)]


def lift_pixel(rig, depth, u, v, *, radius_px=PATCH_RADIUS_PX, min_support=MIN_JOINT_SUPPORT):
    """Front-surface point (rectified-left frame) of the depth pixels landing near RGB (u, v)."""
    width, height = rig.rgb_size
    if not g.finite(u, v) or not (0 <= u <= width and 0 <= v <= height):
        return {"status": "unknown", "reason": "pixel_outside_rgb_frame", "support": 0}
    radius_left = radius_px * rig.scale + 1.5
    points = []
    for x, y in _candidates(rig, rig.segment(u, v), radius_left):
        mm = depth.at(x, y)
        if not mm:
            continue
        point = g.deproject(x, y, mm / 1000.0, rig.left_k)
        pixel = rig.to_rgb(point)
        if pixel is not None and (pixel[0] - u) ** 2 + (pixel[1] - v) ** 2 <= radius_px ** 2:
            points.append(point)
    if len(points) < min_support:
        return {"status": "unknown", "reason": "no_depth_support", "support": len(points)}
    front = front_cluster(points)
    if len(front) < min_support:
        return {"status": "unknown", "reason": "no_front_surface", "support": len(front)}
    centre = per_axis_median(front)
    spread = max(math.dist(p, centre) for p in front)
    return {"status": "measured", "xyz": centre, "support": len(front), "spread_m": spread}


def _inside(polygon, x, y):
    inside = False
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def person_region(box, joints):
    """The torso polygon when all four torso joints are supported, else the inset box.

    As depth_association.person_regions without masks: box is [x0, y0, x1, y1].
    """
    named = {j["name"]: j for j in joints or [] if j.get("status") == "supported" and j.get("pixel")}
    if all(name in named for name in TORSO):
        return "torso", [tuple(named[name]["pixel"]) for name in TORSO]
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * REGION_INSET, (y1 - y0) * REGION_INSET
    return "box", [(x0 + dx, y0 + dy), (x1 - dx, y0 + dy), (x1 - dx, y1 - dy), (x0 + dx, y1 - dy)]


def lift_region(rig, depth, polygon, *, minimum_support=MIN_REGION_SUPPORT, stride=REGION_STRIDE):
    """Robust support median (rectified-left frame) of the depth pixels landing in an RGB polygon."""
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    corners = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
    reach = []
    for u, v in corners:
        reach.extend(rig.segment(u, v))
    if not reach:
        return {"status": "unknown", "reason": "region_outside_depth_frame"}
    width, height = rig.depth_size
    margin = 2 + rig.scale
    lu = max(0, int(min(p[0] for p in reach) - margin))
    hu = min(width, int(max(p[0] for p in reach) + margin) + 1)
    lv = max(0, int(min(p[1] for p in reach) - margin))
    hv = min(height, int(max(p[1] for p in reach) + margin) + 1)
    measured, holes = [], []
    for y in range(lv - lv % stride, hv, stride):
        if y < lv:
            continue
        for x in range(lu - lu % stride, hu, stride):
            if x < lu:
                continue
            mm = depth.at(x, y)
            if not mm:
                holes.append((x, y))
                continue
            point = g.deproject(x, y, mm / 1000.0, rig.left_k)
            pixel = rig.to_rgb(point)
            if pixel is not None and _inside(polygon, *pixel):
                measured.append((mm, point))
    if len(measured) < minimum_support:
        return {"status": "unknown", "reason": "no_projected_support", "support": len(measured)}
    prior = g.median([mm for mm, _ in measured])
    hole_count = 0
    for x, y in holes:  # a hole counts when its ray at the region's depth lands inside
        pixel = rig.to_rgb(g.deproject(x, y, prior / 1000.0, rig.left_k))
        if pixel is not None and _inside(polygon, *pixel):
            hole_count += 1
    if len(measured) < (len(measured) + hole_count) * 0.5:
        return {"status": "unknown", "reason": "insufficient_valid_support", "support": len(measured)}
    values = sorted(mm for mm, _ in measured)
    n = len(values)
    centre = g.median(values)
    if values[(3 * n) // 4] - values[n // 4] > max(80.0, centre * 0.08):
        return {"status": "unknown", "reason": "depth_discontinuity", "support": n}
    mad = g.median([abs(v - centre) for v in values])
    limit = max(15.0, 3 * 1.4826 * mad)
    inliers = [point for mm, point in measured if abs(mm - centre) <= limit]
    if len(inliers) < minimum_support:
        return {"status": "unknown", "reason": "insufficient_inliers", "support": len(inliers)}
    xyz = per_axis_median(inliers)
    return {"status": "measured", "xyz": xyz, "support": len(inliers),
            "valid_fraction": len(measured) / (len(measured) + hole_count),
            "spread_m": max(math.dist(p, xyz) for p in inliers), "points": inliers}

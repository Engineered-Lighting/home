"""Metric evidence primitives. No camera access, model load, or implicit calibration.

Depth points are measured visible surfaces, not anatomical joint centres.
Camera coordinates: x right, y down, z forward, metres. Room axes belong to the
versioned transform; zone footprints explicitly select the map's floor axes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from numbers import Real
from typing import Sequence


def _finite(values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def robust_depth(depth_mm, roi, *, mask=None, visible=True, minimum_support=12,
                 max_age_s=0.5, age_s=0.0, skew_s=0.0, max_skew_s=0.04):
    """Sample an interior ROI; return uncertainty and refusal reasons, not one pixel.

    age/skew limits are experiment configuration, not camera accuracy claims.
    The reported spread is surface support spread, not physical calibration error.
    """
    bad = lambda reason: {"status": "unknown", "reason": reason, "depth_m": None}
    if (type(minimum_support) is not int or minimum_support<1
            or not _finite([max_age_s,max_skew_s]) or min(max_age_s,max_skew_s)<0):
        return bad("invalid_sampling_configuration")
    if not visible:
        return bad("occluded_surface")
    if not _finite([age_s, skew_s]) or age_s < 0 or age_s > max_age_s:
        return bad("depth_stale_or_clock_invalid")
    if abs(skew_s) > max_skew_s:
        return bad("unsynchronized")
    height = len(depth_mm)
    width = len(depth_mm[0]) if height else 0
    if not width or any(len(row) != width for row in depth_mm):
        return bad("invalid_depth_shape")
    if len(roi) != 4 or not _finite(roi):
        return bad("invalid_roi")
    x0, y0, x1, y1 = (int(v) for v in roi)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        return bad("roi_outside_depth_frame")
    if mask is not None and (len(mask) != height or any(len(row) != width for row in mask)):
        return bad("mask_frame_mismatch")
    # Avoid discontinuities at bounding-box edges, without destroying tiny ROIs.
    ix, iy = int((x1-x0)*0.15), int((y1-y0)*0.15)
    values = []
    selected = 0
    for y in range(y0+iy, y1-iy):
        for x in range(x0+ix, x1-ix):
            if mask is not None and not mask[y][x]:
                continue
            selected += 1
            value = depth_mm[y][x]
            if isinstance(value, Real) and not isinstance(value,bool) and math.isfinite(value) and 0 < value <= 65535:
                values.append(float(value))
    if len(values) < minimum_support or len(values) < selected*0.5:
        return bad("insufficient_valid_support")
    values.sort()
    centre = median(values)
    q1, q3 = values[len(values)//4], values[(3*len(values))//4]
    if q3-q1 > max(80.0, centre*0.08):
        return bad("depth_discontinuity")
    mad = median(abs(v-centre) for v in values)
    inliers = [v for v in values if abs(v-centre) <= max(15.0, 3*1.4826*mad)]
    if len(inliers) < minimum_support:
        return bad("insufficient_inliers")
    return {"status": "measured", "depth_m": median(inliers)/1000,
            "support": len(inliers), "valid_fraction": len(values)/max(1, selected),
            "spread_m": (max(inliers)-min(inliers))/1000,
            "surface_only": True, "age_s": age_s, "skew_s": skew_s}


def robust_support(depth_mm, roi, *, mask=None, visible=True, minimum_support=12,
                   max_age_s=0.5, age_s=0.0, skew_s=0.0, max_skew_s=0.04):
    """Vectorized robust_depth with identical decisions, plus accepted inlier pixels.

    Returns (sample, support). support is (u, v, depth_mm) numpy arrays of the
    accepted inliers, or None on abstention; arrays never enter records.
    """
    import numpy as np
    bad = lambda reason: ({"status": "unknown", "reason": reason, "depth_m": None}, None)
    if (type(minimum_support) is not int or minimum_support<1
            or not _finite([max_age_s,max_skew_s]) or min(max_age_s,max_skew_s)<0):
        return bad("invalid_sampling_configuration")
    if not visible:
        return bad("occluded_surface")
    if not _finite([age_s, skew_s]) or age_s < 0 or age_s > max_age_s:
        return bad("depth_stale_or_clock_invalid")
    if abs(skew_s) > max_skew_s:
        return bad("unsynchronized")
    depth = np.asarray(depth_mm)
    if depth.ndim != 2 or not depth.size or depth.dtype.kind not in "biuf":
        return bad("invalid_depth_shape")
    height, width = depth.shape
    if len(roi) != 4 or not _finite(roi):
        return bad("invalid_roi")
    x0, y0, x1, y1 = (int(v) for v in roi)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        return bad("roi_outside_depth_frame")
    if mask is not None:
        mask = np.asarray(mask)
        if mask.shape != depth.shape:
            return bad("mask_frame_mismatch")
    ix, iy = int((x1-x0)*0.15), int((y1-y0)*0.15)
    rows, cols = slice(y0+iy, y1-iy), slice(x0+ix, x1-ix)
    patch = depth[rows, cols]
    selected = np.ones(patch.shape, bool) if mask is None else mask[rows, cols].astype(bool)
    # Booleans are never depth, exactly as in the scalar sampler.
    valid = (np.zeros(patch.shape, bool) if depth.dtype.kind == "b"
             else np.isfinite(patch) & (patch > 0) & (patch <= 65535))
    chosen = selected & valid
    values = patch[chosen].astype(np.float64)
    count, total = int(values.size), int(selected.sum())
    if count < minimum_support or count < total*0.5:
        return bad("insufficient_valid_support")
    ordered = np.sort(values)
    centre = float(np.median(ordered))
    if ordered[(3*count)//4]-ordered[count//4] > max(80.0, centre*0.08):
        return bad("depth_discontinuity")
    mad = float(np.median(np.abs(ordered-centre)))
    keep = np.abs(values-centre) <= max(15.0, 3*1.4826*mad)
    inliers = values[keep]
    if inliers.size < minimum_support:
        return bad("insufficient_inliers")
    vy, vx = np.nonzero(chosen)
    sample = {"status": "measured", "depth_m": float(np.median(inliers))/1000,
              "support": int(inliers.size), "valid_fraction": count/max(1, total),
              "spread_m": float(inliers.max()-inliers.min())/1000,
              "surface_only": True, "age_s": age_s, "skew_s": skew_s}
    return sample, (vx[keep]+x0+ix, vy[keep]+y0+iy, inliers)


def horizontal_extent(points, matrix, *, floor_axes):
    """Floor-plane bound of measured support points around their median point.

    Only the rigid rotation is used, so vertical body extent never becomes
    horizontal uncertainty. Returns (representative_xyz, radius_m,
    vertical_extent_m); the representative stays in the source frame.
    """
    import numpy as np
    if floor_axes not in {"xy", "xz"}:
        raise ValueError("explicit floor axes required")
    transform_point({"frame": "source", "xyz_m": [0, 0, 0]}, matrix, source_frame="source",
                    target_frame="map", version="extent", calibration_error_m=0)
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.all(np.isfinite(points)):
        raise ValueError("finite support points required")
    representative = np.median(points, axis=0)
    offsets = (points-representative) @ np.asarray(matrix, dtype=np.float64)[:3, :3].T
    floor, up = ((0, 1), 2) if floor_axes == "xy" else ((0, 2), 1)
    radius = float(np.max(np.hypot(offsets[:, floor[0]], offsets[:, floor[1]])))
    vertical = float(np.ptp(offsets[:, up]))
    return [float(v) for v in representative], radius, vertical


def deproject(pixel, depth_m, intrinsics, *, source_frame, calibration_version):
    if not source_frame or not calibration_version:
        raise ValueError("source frame and calibration provenance required")
    fx, fy, cx, cy = (intrinsics[k] for k in ("fx", "fy", "cx", "cy"))
    u, v = pixel
    if not _finite([fx, fy, cx, cy, u, v, depth_m]) or min(fx, fy, depth_m) <= 0:
        raise ValueError("invalid metric projection")
    return {"xyz_m": [(u-cx)*depth_m/fx, (v-cy)*depth_m/fy, depth_m],
            "frame": source_frame, "calibration_version": calibration_version}


def transform_point(point, matrix, *, source_frame, target_frame, version,
                    calibration_error_m, source_uncertainty_m=0.0):
    if not source_frame or not target_frame or not version or point.get("frame") != source_frame:
        raise ValueError("coordinate frame provenance mismatch")
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise ValueError("4x4 metre transform required")
    if not _finite([v for row in matrix for v in row]) or matrix[3] != [0, 0, 0, 1]:
        raise ValueError("invalid homogeneous transform")
    # Reject scale/shear and accidental millimetre rotation matrices.
    for i in range(3):
        for j in range(3):
            dot = sum(matrix[k][i]*matrix[k][j] for k in range(3))
            if abs(dot-(1 if i == j else 0)) > 1e-4:
                raise ValueError("transform rotation must be orthonormal")
    r = matrix
    determinant = (r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])
                   -r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])
                   +r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0]))
    if abs(determinant-1) > 1e-4:
        raise ValueError("reflection is not a calibrated rigid transform")
    if not _finite([calibration_error_m, source_uncertainty_m]) or min(calibration_error_m, source_uncertainty_m) < 0:
        raise ValueError("measured uncertainty bounds required")
    p = point["xyz_m"]
    if len(p) != 3 or not _finite(p):
        raise ValueError("invalid source point")
    return {"xyz_m": [sum(matrix[i][k]*p[k] for k in range(3))+matrix[i][3] for i in range(3)],
            "frame": target_frame, "transform_version": version,
            "calibration_version": point.get("calibration_version"),
            "uncertainty_m": calibration_error_m+source_uncertainty_m,
            "method": "stereo_surface"}


def floor_projection(pixel, homography, *, calibration_version, reference_error_m,
                     target_frame=None, pixel_uncertainty_px=None):
    """RGB-only estimate valid for a visible floor contact, never for any bbox centre.

    With target_frame, the homography maps source pixels onto that map's XY
    floor (z_up_metric_floor0): the result is a framed map point [x, y, 0]
    whose uncertainty adds the local metres-per-pixel scale times the contact
    pixel uncertainty. It is normalized so floor pixels have positive w.
    Without target_frame the legacy unframed [x, 0, z] estimate is returned;
    it carries no frame and cannot enter room context.
    """
    if not calibration_version or not _finite([reference_error_m]) or reference_error_m < 0:
        raise ValueError("validated floor calibration required")
    if len(homography) != 3 or any(len(row) != 3 for row in homography):
        raise ValueError("3x3 floor homography required")
    u, v = pixel
    if not _finite([u, v]+[x for row in homography for x in row]):
        raise ValueError("nonfinite projection")
    q = [row[0]*u+row[1]*v+row[2] for row in homography]
    if target_frame is None:
        if abs(q[2]) < 1e-8:
            return {"status": "unknown", "reason": "near_horizon"}
        return {"status": "estimated", "xyz_m": [q[0]/q[2], 0.0, q[1]/q[2]],
                "method": "rgb_floor_contact", "calibration_version": calibration_version,
                "uncertainty_m": reference_error_m}
    if (not isinstance(target_frame, str) or not _finite([pixel_uncertainty_px])
            or not 0 <= pixel_uncertainty_px <= 64):
        raise ValueError("framed floor projection needs a map frame and pixel uncertainty")
    if q[2] <= 1e-8:
        return {"status": "unknown", "reason": "beyond_floor_horizon"}
    h = homography
    x, y = q[0]/q[2], q[1]/q[2]
    # Largest singular value of the pixel-to-floor Jacobian (metres/pixel).
    j = [(h[0][0]-x*h[2][0])/q[2], (h[0][1]-x*h[2][1])/q[2],
         (h[1][0]-y*h[2][0])/q[2], (h[1][1]-y*h[2][1])/q[2]]
    frobenius, determinant = sum(value*value for value in j), j[0]*j[3]-j[1]*j[2]
    scale = math.sqrt((frobenius+math.sqrt(max(0.0, frobenius*frobenius-4*determinant*determinant)))/2)
    return {"status": "estimated", "xyz_m": [x, y, 0.0], "frame": target_frame,
            "target_frame": target_frame, "method": "rgb_floor_contact",
            "calibration_version": calibration_version, "surface_only": False,
            "metres_per_pixel": scale,
            "uncertainty_m": reference_error_m+pixel_uncertainty_px*scale}


def _segment_distance(p, a, b):
    dx, dz = b[0]-a[0], b[1]-a[1]
    norm = dx*dx+dz*dz
    t = max(0.0, min(1.0, ((p[0]-a[0])*dx+(p[1]-a[1])*dz)/norm)) if norm else 0
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dz)


def zone_relation(point, polygon, *, site_id, map_site_id, map_version, floor_axes="xz"):
    if site_id != map_site_id or not map_version:
        raise ValueError("map must belong to source site and have a revision")
    if floor_axes not in {"xy","xz"} or len(polygon) < 3 or any(len(v) != 2 or not _finite(v) for v in polygon):
        raise ValueError("zone requires explicit floor axes and finite vertices")
    xyz = point["xyz_m"]
    uncertainty = point.get("uncertainty_m")
    if len(xyz)!=3 or not _finite(xyz) or uncertainty is None or not _finite([uncertainty]) or uncertainty < 0:
        return {"relation": "unknown", "reason": "missing_position_uncertainty"}
    p = [xyz[0], xyz[1 if floor_axes=="xy" else 2]]
    inside = False
    distance = math.inf
    for i, a in enumerate(polygon):
        b = polygon[(i+1) % len(polygon)]
        distance = min(distance, _segment_distance(p, a, b))
        if (a[1] > p[1]) != (b[1] > p[1]):
            crossing = (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]
            if p[0] < crossing:
                inside = not inside
    relation = "boundary" if distance <= uncertainty else ("inside" if inside else "outside")
    return {"relation": relation, "boundary_distance_m": distance,
            "uncertainty_m": uncertainty, "map_version": map_version}


def surface_proximity(point, bounds, *, near_m=0.5, map_version):
    """A conservative interval to a furniture volume; never asserts contact."""
    lo, hi = bounds
    xyz, uncertainty = point["xyz_m"], point.get("uncertainty_m")
    if (len(lo) != 3 or len(hi) != 3 or not _finite(lo+hi+xyz)
            or any(a > b for a, b in zip(lo, hi))):
        raise ValueError("invalid furniture bounds")
    if uncertainty is None or not _finite([uncertainty]) or uncertainty < 0:
        return {"relation": "unknown", "reason": "missing_position_uncertainty"}
    distance = math.sqrt(sum(max(a-p, 0, p-b)**2 for p, a, b in zip(xyz, lo, hi)))
    interval = [max(0, distance-uncertainty), distance+uncertainty]
    relation = "near" if interval[1] <= near_m else ("far" if interval[0] > near_m else "uncertain")
    return {"relation": relation, "distance_interval_m": interval,
            "map_version": map_version, "contact": "unobserved"}


@dataclass
class Track:
    id: str
    xyz_m: list
    observed_at: float


class TrackBank:
    """Short-lived geometric continuity; never assigns a person's identity.

    Gaps create new IDs. Greedy nearest association is a measured baseline, not a
    claim of multi-camera/re-identification accuracy.
    """
    def __init__(self, site_id, camera_id, epoch, *, max_gap_s=2.0, max_distance_m=0.8):
        self.prefix = f"{site_id}/{camera_id}/{epoch}"
        self.max_gap_s = max_gap_s
        self.max_distance_m = max_distance_m
        self.tracks = {}
        self.sequence = 0
        self.last_time = -math.inf

    def update(self, points: Sequence[Sequence[float]], observed_at):
        if not _finite([observed_at]) or observed_at <= self.last_time:
            raise ValueError("out-of-order or replayed track observation")
        if any(len(p) != 3 or not _finite(p) for p in points):
            raise ValueError("finite metric points required")
        self.last_time = observed_at
        self.tracks = {k: t for k, t in self.tracks.items() if observed_at-t.observed_at <= self.max_gap_s}
        candidates = sorted((math.dist(t.xyz_m, p), k, i)
                            for k, t in self.tracks.items() for i, p in enumerate(points))
        assigned, used = {}, set()
        for distance, key, index in candidates:
            if distance <= self.max_distance_m and key not in used and index not in assigned:
                assigned[index] = key
                used.add(key)
        result = []
        for index, point in enumerate(points):
            key = assigned.get(index)
            if key is None:
                self.sequence += 1
                key = f"{self.prefix}/{self.sequence}"
            self.tracks[key] = Track(key, list(point), observed_at)
            result.append({"track_id": key, "xyz_m": list(point), "identity_hypothesis": None,
                           "association": "continued" if index in assigned else "new"})
        return result

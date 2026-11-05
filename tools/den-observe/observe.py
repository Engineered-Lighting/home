"""The rung 2 writer: detector rows + R take frames + a camera entry -> Observations.

Standard library only. Field names are schemas.json's `observation` record,
plus `freshness` (slot freshness_field). How each value is made is METHOD.md.
"""
import math

import camgeom as g
import detections
import lift

SCHEMA_FIELDS = ("obs_id", "source", "capture_ts", "kind", "image_box", "keypoints", "joint_set", "joints_room",
                 "depth_ts", "label", "room_position", "transform_id", "score", "visibility", "validity")
EXTRA_FIELDS = ("freshness",)
JOINT_SET = "coco17"
TORSO = (5, 6, 11, 12)  # shoulders and hips: the torso reference point
PLANE_GROUP = {j: "head" for j in range(0, 5)}
PLANE_GROUP.update({j: "arms" for j in range(5, 11)})
PLANE_GROUP.update({j: "legs" for j in range(11, 15)})
PLANE_GROUP.update({j: "feet" for j in range(15, 17)})
EDGE_MARGIN_PX = 4  # depth_association.floor_contact's edge_margin_px
ZERO = [[0.0] * 3 for _ in range(3)]


class Settings:
    """The method values one run uses (closed, or proposed in an unscored run)."""

    def __init__(self, method):
        get = method.get
        self.max_gap_s, self.max_validity_s = get("max_depth_gap_s"), get("max_validity_s")
        self.pixel_sigma_px, self.disparity_sigma_px = get("pixel_sigma_px"), get("disparity_sigma_px")
        self.max_age_s, self.score_min = get("detector_max_age_s"), get("keypoint_score_min")
        self.planes = {name: (get(f"plane_{name}_h_m"), get(f"plane_{name}_sigma_m"))
                       for name in ("head", "arms", "legs", "feet")}
        self.plane_max_sigma_m = get("plane_max_sigma_m")


def _r(value, digits=6):
    return round(value, digits) + 0.0


def _vec(v, digits=6):
    return [_r(c, digits) for c in v]


def _mat(m, digits=9):
    return [[_r(c, digits) for c in row] for row in m]


def _scale(m, s):
    return [[c * s for c in row] for row in m]


# ---- one point ---------------------------------------------------------------

def stereo_point(camera, settings, point_left):
    """Room point and its covariance parts for a rectified-left stereo point."""
    z = point_left[2]
    along = z * z * settings.disparity_sigma_px / (camera.f_left * camera.baseline_m)
    across = settings.pixel_sigma_px * z / camera.f_rgb
    independent = camera.rotate(g.ray_covariance(point_left, along, across))
    return camera.to_room(point_left), independent, ZERO


def plane_point(camera, settings, j, u, v):
    """Room point where the joint's RGB ray meets its group's declared plane, or (None, why)."""
    group = PLANE_GROUP[j]
    height, sigma_h = settings.planes[group]
    origin, direction = camera.rgb_ray_room(u, v)
    hit = g.ray_plane(origin, direction, [0.0, 0.0, 1.0], height)
    if hit is None:
        return None, "ray_misses_plane"
    point, distance = hit
    along = sigma_h / abs(direction[2])
    if along > settings.plane_max_sigma_m:
        return None, "plane_too_oblique"
    independent = g.ray_covariance(direction, 0.0, settings.pixel_sigma_px * distance / camera.f_rgb)
    common = g.ray_covariance(direction, along, 0.0)
    return (point, independent, common, f"plane:{height:g}"), None


def lift_joint(camera, settings, depth, depth_ok, j, keypoint, size):
    """One joints_room entry and its covariance parts (None for an unsupported joint)."""
    width, height = size
    entry = {"j": j, "xyz": None, "cov": None, "status": "unsupported", "lift": "none"}
    if keypoint is None:
        return entry, None
    u, v, score = keypoint
    if score < settings.score_min or not (0 <= u <= width and 0 <= v <= height):
        return entry, None
    if depth_ok:
        sample = lift.lift_pixel(camera.rig, depth, u, v)
        if sample["status"] == "measured":
            xyz, independent, common = stereo_point(camera, settings, sample["xyz"])
            return _finish(entry, camera, xyz, independent, common, "supported", "stereo")
    found, _ = plane_point(camera, settings, j, u, v)
    if found is None:
        return entry, None
    xyz, independent, common, how = found
    return _finish(entry, camera, xyz, independent, common, "uncertain", how)


def _finish(entry, camera, xyz, independent, common, status, how):
    cal = g.isotropic(camera.error_bound_m)
    entry.update(xyz=_vec(xyz), cov=_mat(g.cov_add(independent, common, cal)), status=status, lift=how)
    return entry, (xyz, independent, common)


def torso_point(camera, lifted, joints):
    """Mean of the stereo torso joints, else of the plane ones, with its covariance."""
    for wanted in ("stereo", "plane"):
        used = [lifted[j] for j in TORSO if lifted.get(j) and joints[j]["lift"].split(":")[0] == wanted]
        if used:
            break
    else:
        return None
    n = len(used)
    xyz = [sum(p[0][i] for p in used) / n for i in range(3)]
    independent = _scale(g.cov_add(*(p[1] for p in used)), 1.0 / (n * n))
    common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / n)
    return xyz, g.cov_add(independent, common, g.isotropic(camera.error_bound_m))


def box_point(camera, settings, depth, box):
    """Robust median of the depth landing in the inset box, or (None, why)."""
    _, polygon = lift.person_region(box, None)
    sample = lift.lift_region(camera.rig, depth, polygon)
    if sample["status"] != "measured":
        return None, sample["reason"]
    xyz, independent, _ = stereo_point(camera, settings, sample["xyz"])
    return (xyz, g.cov_add(independent, g.isotropic(camera.error_bound_m))), None


# ---- one frame ---------------------------------------------------------------

def depth_state(row, meta, depth, settings):
    gap = row["frame"]["capture_ts"] - meta["t_depth"]  # both host-monotonic: exact
    if abs(gap) > settings.max_gap_s:
        return "too_old", gap
    if not any(depth.mm):
        return "absent", gap
    return "used", gap


def freshness(row, capture_ts, offset_s, settings, depth, gap):
    available = row.get("available_ts")
    age = None if available is None else available - row["frame"]["capture_ts"]
    status = "unknown" if age is None else ("expired" if age > settings.max_age_s else "fresh")
    return {"available_ts": None if available is None else _r(available + offset_s),
            "age_s": None if age is None else _r(age), "max_age_s": settings.max_age_s, "status": status,
            "valid_until": _r(capture_ts + settings.max_validity_s), "depth": depth,
            "depth_gap_s": _r(gap)}


def visibility(box, size, joints_2d):
    width, height = size
    x0, y0, x1, y1 = box
    torso = sum(1 for j in TORSO if joints_2d[j]) if joints_2d is not None else len(TORSO)
    if joints_2d is not None and torso == 0:
        return "occluded"
    edge = (x0 <= EDGE_MARGIN_PX or y0 <= EDGE_MARGIN_PX
            or x1 >= width - EDGE_MARGIN_PX or y1 >= height - EDGE_MARGIN_PX)
    return "partial" if edge or torso < len(TORSO) else "visible"


def observe(row, meta, depth, camera, settings, *, offset_s):
    """Observations for one detector row and its take frame (meta, depth)."""
    frame, detector = row["frame"], row["detector"]
    size = (frame["width"], frame["height"])
    if size != camera.rig.rgb_size:
        raise ValueError(f"row frame is {size}, the camera entry's RGB is {camera.rig.rgb_size}")
    capture_ts = frame["capture_ts"] + offset_s
    state, gap = depth_state(row, meta, depth, settings)
    fresh = freshness(row, capture_ts, offset_s, settings, state, gap)
    base = {"capture_ts": _r(capture_ts), "transform_id": camera.transform_id,
            "depth_ts": _r(meta["t_depth"] + offset_s)}
    out = []
    for index, d in enumerate(row["detections"]):
        keypoints = d.get("keypoints")
        kind = "keypoints" if keypoints is not None else ("person_box" if d["class"] == "person" else "object_box")
        model = detector.get("keypoints") if kind == "keypoints" else detector["boxes"]
        version = detector.get("keypoints_version") if kind == "keypoints" else detector.get("boxes_version")
        obs = {"obs_id": f"o-{camera.camera_id}-{int(round(capture_ts * 1000))}-{index}",
               "source": {"sensor_id": camera.camera_id, "model": model, "model_version": version},
               "kind": kind, **base, "image_box": _vec(d["box_xyxy"], 3), "score": d["score"]}
        if kind == "object_box":
            obs["label"] = d["class"]
        reason, point, joints_2d = None, None, None
        if kind == "keypoints":
            joints, lifted = [], {}
            for j, keypoint in enumerate(keypoints):
                entry, parts = lift_joint(camera, settings, depth, state == "used", j, keypoint, size)
                joints.append(entry)
                if parts is not None:
                    lifted[j] = parts
            joints_2d = [k is not None and k[2] >= settings.score_min and 0 <= k[0] <= size[0]
                         and 0 <= k[1] <= size[1] for k in keypoints]
            obs.update(keypoints=[list(k) if k is not None else [None, None, None] for k in keypoints],
                       joint_set=JOINT_SET, joints_room=joints)
            point = torso_point(camera, lifted, joints)
            if not any(joints_2d):
                reason = "no_supported_joints"
            elif point is None:
                reason = "no_room_position:no_torso_joint"
        elif state != "used":
            reason = f"no_room_position:depth_{state}"
        else:
            point, why = box_point(camera, settings, depth, d["box_xyxy"])
            if point is None:
                reason = f"no_room_position:{why}"
        if point is not None:
            xyz, cov = point
            obs["room_position"] = {"x": _r(xyz[0]), "y": _r(xyz[1]), "z": _r(xyz[2]), "cov": _mat(cov)}
            if reason is None and xyz[2] + 3 * math.sqrt(cov[2][2]) < 0:
                reason = "below_floor"
        if reason is None and fresh["status"] == "expired":
            reason = "detector_result_expired"
        obs["visibility"] = visibility(d["box_xyxy"], size, joints_2d)
        obs["validity"] = "valid" if reason is None else f"rejected:{reason}"
        obs["freshness"] = dict(fresh)
        out.append(obs)
    return out


# ---- the gate's per-record checks ---------------------------------------------

def check_record(obs, resolves):
    """Problems with one Observation against rung 2's gate (an empty list passes).

    resolves(transform_id) is True when the id names exactly one camera entry.
    """
    problems = []
    unknown = set(obs) - set(SCHEMA_FIELDS) - set(EXTRA_FIELDS)
    if unknown:
        problems.append(f"fields not in schemas.json: {sorted(unknown)}")
    if not g.finite(obs.get("capture_ts")):
        problems.append("no capture_ts")
    validity = obs.get("validity")
    if validity != "valid" and not (isinstance(validity, str) and validity.startswith("rejected:")
                                    and len(validity) > len("rejected:")):
        problems.append("no validity")
    fresh = obs.get("freshness")
    if type(fresh) is not dict or fresh.get("status") not in ("fresh", "expired", "unknown") \
            or fresh.get("depth") not in ("used", "too_old", "absent"):
        problems.append("no freshness")
    position = obs.get("room_position")
    if position is not None:
        cov = position.get("cov")
        if not g.finite(position.get("x"), position.get("y"), position.get("z")) or not _covariance(cov):
            problems.append("room_position without a usable covariance")
    elif validity == "valid":
        problems.append("valid without a room_position")
    if not resolves(obs.get("transform_id")):
        problems.append("transform_id does not resolve to one camera entry")
    if obs.get("kind") == "keypoints":
        joints = obs.get("joints_room")
        if obs.get("joint_set") != JOINT_SET or type(joints) is not list or len(joints) != len(detections.COCO17):
            problems.append("keypoints without 17 joints_room entries")
        else:
            for entry in joints:
                if entry.get("status") == "unsupported":
                    if entry.get("xyz") is not None or entry.get("cov") is not None or entry.get("lift") != "none":
                        problems.append(f"joint {entry.get('j')}: unsupported but not null")
                elif entry.get("status") in ("supported", "uncertain"):
                    if not (isinstance(entry.get("xyz"), list) and g.finite(*entry["xyz"])
                            and _covariance(entry.get("cov"))):
                        problems.append(f"joint {entry.get('j')}: no position or covariance")
                    if entry.get("lift") != "stereo" and not str(entry.get("lift")).startswith("plane:"):
                        problems.append(f"joint {entry.get('j')}: lift is not stereo or plane:<h>")
                else:
                    problems.append(f"joint {entry.get('j')}: unknown status")
        if not g.finite(obs.get("depth_ts")):
            problems.append("keypoints without depth_ts")
        if fresh and fresh.get("depth") != "used" and any(e.get("lift") == "stereo" for e in joints or []):
            problems.append("stereo lift from depth that was not used")
    return problems


def _covariance(cov):
    """A finite, symmetric 3x3 with a positive diagonal and positive leading minors."""
    if (type(cov) is not list or len(cov) != 3 or any(type(r) is not list or len(r) != 3 for r in cov)
            or not g.finite(*(c for r in cov for c in r))):
        return False
    if any(abs(cov[i][j] - cov[j][i]) > 1e-9 for i in range(3) for j in range(3)):
        return False
    minor2 = cov[0][0] * cov[1][1] - cov[0][1] * cov[1][0]
    return cov[0][0] > 0 and minor2 > 0 and g.det3(cov) > 0

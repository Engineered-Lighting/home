"""Explicit, site-bound camera registration into Home's existing room frame.

Home Apartment maps normally use z_up_metric_floor0 (XY floor). No axis swap,
origin, camera pose or physical accuracy is inferred from that name alone.
"""
from copy import deepcopy
from geometry import transform_point, zone_relation, _finite
from reasoning import digest


def apartment_map(model, binding):
    """Normalize a provisioned Home model without changing its persisted geometry.

    Binding comes from the site registry, never a model response or camera label.
    Content-pack revisions are distinct from editable Apartment model revisions.
    """
    if (not binding.get("site_id") or not binding.get("map_id") or binding.get("units")!="metres"
            or model.get("meta",{}).get("frame")!="z_up_metric_floor0"
            or str(model.get("revision"))!=binding.get("revision") or digest(model)!=binding.get("model_sha256")):
        raise ValueError("provisioned Apartment model binding does not match")
    zones=[]
    targets=[]
    for zone in model.get("zones",[]):
        polygon=zone.get("floor_polygon")
        if not zone.get("id") or not isinstance(polygon,list) or len(polygon)<3 or any(len(p)!=2 or not _finite(p) for p in polygon):
            raise ValueError("invalid Apartment floor polygon")
        zones.append({"id":zone["id"],"label":zone.get("name",zone["id"]),"polygon":deepcopy(polygon)})
    for target in model.get("targets",[]):
        position=target.get("pos")
        if not target.get("id") or not isinstance(position,list) or len(position)!=3 or not _finite(position):
            raise ValueError("invalid Apartment target position")
        targets.append({"id":target["id"],"label":target.get("name",target["id"]),"pos":list(position),**_target_extent(target)})
    return {"site_id":binding["site_id"],"id":binding["map_id"],"revision":binding["revision"],
            "frame":"z_up_metric_floor0","units":"metres","floor_axes":"xy","zones":zones,"targets":targets,
            "model_sha256":binding["model_sha256"]}


def _target_extent(target):
    """Optional named-anchor fields, validated when present (den room map, 2026-09-30).

    category/shape are Home's target words (e.g. 'monitor', 'point'/'box'/'surface');
    radius_m bounds a point anchor; size_m is Home's surface [w, h] or a box's
    full room-axis extents [x, y, z]. Absent fields stay absent.
    """
    extra={}
    for key in ("category","shape"):
        if key in target:
            if not isinstance(target[key],str) or not 1<=len(target[key])<=64:
                raise ValueError("invalid Apartment target annotation")
            extra[key]=target[key]
    if "radius_m" in target:
        if not _finite([target["radius_m"]]) or not 0<target["radius_m"]<=10:
            raise ValueError("invalid Apartment target radius")
        extra["radius_m"]=target["radius_m"]
    if "size_m" in target:
        size=target["size_m"]
        if not isinstance(size,list) or len(size) not in (2,3) or not _finite(size) or not all(0<v<=50 for v in size):
            raise ValueError("invalid Apartment target size")
        extra["size_m"]=list(size)
    return extra


def validate_registration(registration, room_map):
    required=("site_id","camera_id","source_frame","target_frame","calibration_version",
              "mount_revision","transform_version","map_id","map_version")
    if any(not isinstance(registration.get(k),str) or not registration[k] for k in required):
        raise ValueError("complete camera/map registration required")
    if (registration.get("status")!="validated" or registration.get("units")!="metres"
            or room_map.get("units")!="metres" or room_map.get("floor_axes") not in {"xy","xz"}):
        raise ValueError("validated metric registration and floor axes required")
    if any(registration[k]!=room_map.get(map_key) for k,map_key in (
            ("site_id","site_id"),("map_id","id"),("map_version","revision"),("target_frame","frame"))):
        raise ValueError("registration does not match this map revision and site")
    if not registration.get("validation_receipt"):
        raise ValueError("physical registration validation receipt required")
    if len(room_map.get("zones",[]))>500 or len(room_map.get("targets",[]))>1000:
        raise ValueError("map context exceeds bounds")
    # Reuse the rigid-transform validator, including reflection/scale rejection.
    transform_point({"frame":registration["source_frame"],"xyz_m":[0,0,0]},registration["matrix"],
        source_frame=registration["source_frame"],target_frame=registration["target_frame"],
        version=registration["transform_version"],calibration_error_m=registration["error_bound_m"])
    # Optional RGB fallback: RGB source pixels -> this map's floor, validated
    # separately from the stereo transform and bound to the same mount.
    homography=registration.get("floor_homography")
    if homography is not None:
        matrix=homography.get("matrix") if isinstance(homography,dict) else None
        if (not isinstance(matrix,list) or len(matrix)!=3 or any(not isinstance(r,list) or len(r)!=3 or not _finite(r) for r in matrix)
                or any(not isinstance(homography.get(k),str) or not homography[k] for k in ("id","validation_receipt","source_frame"))
                or any(type(homography.get(k)) is not int or not 1<=homography[k]<=4096 for k in ("width","height"))
                or not _finite([homography.get("error_bound_m")]) or homography["error_bound_m"]<0):
            raise ValueError("validated RGB floor homography required")


def build_room_context(points, registration, room_map, *, as_of):
    """Return positions and zone relations in the map's native metric frame.

    Input points are explicitly measured visible surfaces (camera frame,
    'stereo_surface') or RGB floor-contact estimates already in the map frame
    ('rgb_floor_contact', from the validated floor homography), never inferred
    joints, contacts or identities. Missing/stale registration is rejected.
    """
    validate_registration(registration,room_map)
    if not _finite([as_of]):
        raise ValueError("explicit observation/replay cutoff required")
    if len(points)>64:
        raise ValueError("too many tracked surfaces")
    people=[]
    memberships=[]
    unavailable=[]
    seen=set()
    homography=registration.get("floor_homography") or {}
    for point in points:
        method=point.get("method")
        stereo=method=="stereo_surface"
        for key,expected in (("site_id",registration["site_id"]),("camera_id",registration["camera_id"]),
                ("calibration_version",registration["calibration_version"] if stereo else homography.get("id")),
                ("mount_revision",registration["mount_revision"])):
            if expected is None or point.get(key)!=expected:
                raise ValueError("source or camera calibration changed")
        if method=="rgb_floor_contact" and (point.get("frame")!=registration["target_frame"] or point.get("floor_contact") is not True):
            raise ValueError("RGB floor contact must already be in the registered map frame")
        if (not point.get("track_id") or method not in {"stereo_surface","rgb_floor_contact"}
                or point.get("arm") not in {"a","b"} or point.get("status")!=("measured" if stereo else "estimated")
                or not _finite([point.get("uncertainty_m"),point.get("observed_at"),point.get("available_at"),point.get("valid_until")])
                or point["uncertainty_m"]<0 or point["valid_until"]<point["observed_at"]
                or point["available_at"]<point["observed_at"]):
            raise ValueError("measured track surface provenance required")
        identity=(point["arm"],point["track_id"])
        if identity in seen:
            raise ValueError("duplicate arm/track surface")
        seen.add(identity)
        if not point["observed_at"]<=as_of<=point["valid_until"]:
            unavailable.append({"track_id":point["track_id"],"arm":point["arm"],"reason":"outside_valid_observation_interval"})
            continue
        if point["available_at"]>as_of:
            unavailable.append({"track_id":point["track_id"],"arm":point["arm"],"reason":"not_available_at_cutoff"})
            continue
        if stereo:
            transformed=transform_point(point,registration["matrix"],source_frame=registration["source_frame"],
                target_frame=registration["target_frame"],version=registration["transform_version"],
                calibration_error_m=registration["error_bound_m"],source_uncertainty_m=point["uncertainty_m"])
        else:
            # Homography error already bounds pixel-to-map error; no second transform.
            if len(point.get("xyz_m") or [])!=3 or not _finite(point["xyz_m"]):
                raise ValueError("finite RGB floor contact required")
            transformed={"xyz_m":list(point["xyz_m"]),"frame":point["frame"],"uncertainty_m":point["uncertainty_m"]}
        person={"id":point["arm"]+":"+point["track_id"],"track_id":point["track_id"],"arm":point["arm"].upper(),
            "pos":transformed["xyz_m"],"uncertainty_m":transformed["uncertainty_m"],"method":method,
            "observed_at":point["observed_at"],"available_at":point["available_at"],"valid_until":point["valid_until"],"identity":None,
            "frame":transformed["frame"],"calibration_version":point["calibration_version"],
            "transform_version":registration["transform_version"],"mount_revision":registration["mount_revision"]}
        if not stereo:person["homography_version"]=homography["id"]
        people.append(person)
        for zone in room_map.get("zones",[]):
            relation=zone_relation(transformed,zone["polygon"],site_id=point["site_id"],map_site_id=room_map["site_id"],
                map_version=room_map["revision"],floor_axes=room_map["floor_axes"])
            memberships.append({"arm":point["arm"],"track_id":point["track_id"],"zone_id":zone["id"],
                "observed_at":point["observed_at"],"available_at":point["available_at"],"valid_until":point["valid_until"],
                "calibration_version":point["calibration_version"],"transform_version":registration["transform_version"],
                "method":method,**relation})
    return {"site_id":registration["site_id"],"camera_id":registration["camera_id"],"map_id":room_map["id"],
            "map_version":room_map["revision"],"coordinate_frame":room_map["frame"],"units":"metres",
            "floor_axes":room_map["floor_axes"],"people":people,"memberships":memberships,"unavailable":unavailable,"as_of":as_of,
            "zones":deepcopy(room_map.get("zones",[])),"targets":deepcopy(room_map.get("targets",[])),
            "registration_version":registration["transform_version"],"validation_receipt":registration["validation_receipt"]}

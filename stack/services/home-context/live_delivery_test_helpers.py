"""Fictional stationary-delivery public fixtures; no household assets or hosts."""
import copy

from journal import digest
from live_test_helpers import (GEOMETRY_METHOD, MANIFEST, REGISTRATION, SCOPE, geometry,
                               reference, unavailable)
from live_scene_test_helpers import scene_worker_fixture
from live_worker_test_helpers import describe


def delivery_geometry(seq=1, mode="supplied"):
    payload = geometry("synthetic-delivery-geometry-" + str(seq), seq=seq)
    template = copy.deepcopy(payload["parts"]["work"])
    template["uncertainty"]["bound_at_observation_m"] = .001
    template["uncertainty"]["components_m"] = {"capture_association": .00025, "depth": .00025, "registration": .00025, "estimator": .00025}
    template["uncertainty"]["age"].update(max_speed_m_s=0, max_accel_m_s2=0)
    def box(center, half):
        return {"kind": "aabb", "center_m": center, "half_extents_m": half}
    shapes = {
        "work": box([0, 0, 1], [.25, .20, .08]),
        "body": {"kind": "opaque_primitives", "items": [{"part_id": "torso", **box([1.5, 0, 1.1], [.18, .15, .5])}]},
        "head": box([1.5, 0, 1.8], [.12, .12, .15]),
        "hand_left": box([-.45, 0, 1], [.025, .025, .025]),
        "hand_right": box([.45, 0, 1], [.025, .025, .025]),
        "protection": {"kind": "protected_primitives", "policy_ref": reference("synthetic-head-protection"),
                       "items": [{"part_id": "head-keepout", **box([1.5, 0, 1.8], [.14, .14, .17])}]},
    }
    for name, shape in shapes.items():
        part = copy.deepcopy(template)
        part["shape"] = shape
        part["method_ref"] = copy.deepcopy(GEOMETRY_METHOD)
        payload["parts"][name] = part
    if mode in {"unobservable", "unavailable"}:
        return unavailable(payload)
    if mode == "missing_protection":
        payload["parts"]["protection"] = {key: copy.deepcopy(value) for key, value in payload["parts"]["body_forward"].items()}
    elif mode == "head_in_beam":
        payload["parts"]["head"]["shape"]["center_m"] = [0, -.5, 1.5]
        payload["parts"]["protection"]["shape"]["items"][0]["center_m"] = [0, -.5, 1.5]
    elif mode == "body_blocked":
        payload["parts"]["body"]["shape"]["items"][0].update(center_m=[0, -.5, 1.5], half_extents_m=[.6, .3, .6])
    elif mode != "supplied":
        raise ValueError("unknown explicit fictional geometry mode")
    return payload


def delivery_policy():
    return {"schema": "home-live-task-delivery-policy-1", "version": 1,
            "policy_ref": reference("synthetic-delivery-policy"), "registration": copy.deepcopy(REGISTRATION),
            "fixture": {"id": "synthetic-task-fixture", "fixture_pose_ref": reference("synthetic-task-fixture-pose"),
                        "family_ref": reference("synthetic-fixture-family"), "assessment_ref": reference("synthetic-optical-assessment"),
                        "origin_m": [0, -2, 3], "local_to_room": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                        "optics": {"envelope_half_angle_deg": 12, "max_range_m": 8, "pointing_error_deg": .1,
                                   "full_dark_upper_fraction": 0, "outside_envelope_upper_fraction": 0},
                        "mechanics": {"pan_min_deg": -180, "pan_max_deg": 180, "tilt_min_deg": 0, "tilt_max_deg": 90,
                                      "max_velocity_deg_s": 120, "max_acceleration_deg_s2": 240, "max_jerk_deg_s3": 1000,
                                      "settle_s": .2, "fade_s": 1}, "initial": {"pan_deg": 0, "tilt_deg": 0}},
            "room": {"assessment_ref": reference("synthetic-room-assessment"), "collision_ref": reference("synthetic-collision-asset"),
                     "geometry_digest": REGISTRATION["geometry_digest"], "complete": True,
                     "usable_bounds": {"kind": "aabb", "center_m": [0, 0, 1.5], "half_extents_m": [4, 4, 1.5]},
                     "occluders": [], "valid_until": 1800100000},
            "protection": {"assessment_ref": reference("synthetic-protection-assessment"), "policy_ref": reference("synthetic-head-protection"), "max_exposure_fraction": 0},
            "requirements_ref": reference("synthetic-task-requirements"), "required_body_parts": ["torso"],
            "coverage": {"grid_size": 9, "min_fraction": .7, "assessment_ref": reference("synthetic-coverage-assessment")}}


def delivery_worker_fixture():
    service, grants, config = scene_worker_fixture()
    source = service["manifest"]["sources"]["synthetic-source"]
    fixture = delivery_geometry()
    for name in ("work", "body", "head", "hand_left", "hand_right", "protection"):
        bound = copy.deepcopy(fixture["parts"][name]["uncertainty"])
        del bound["age"]["origin_at"]
        source["part_policies"][name] = bound
    source["primitive_ids"] = {"body": ["torso"], "protection": ["head-keepout"]}
    source["protection_policy_refs"] = [reference("synthetic-head-protection")]
    config["source_pins"] = {name: digest(value) for name, value in service["manifest"]["sources"].items()}
    config["delivery_policy"] = delivery_policy()
    description = describe(config)
    manifest = service["manifest"]
    manifest.update(schema="home-live-ingress-manifest-4", version=4,
                    delivery_interface_digest=description["delivery_interface_digest"])
    manifest["worker"].update(config_digest=description["config_digest"], source_pins=config["source_pins"],
                              delivery_policy=copy.deepcopy(config["delivery_policy"]),
                              delivery_schema_pins=description["delivery_schema_pins"])
    service.update(schema="home-live-ingress-config-4", version=4)
    service["manifest_ref"] = {"id": "synthetic-delivery-manifest", "digest": digest(manifest)}
    MANIFEST.update(service["manifest_ref"])
    config.update(manifest_ref=copy.deepcopy(service["manifest_ref"]), live_manifest=copy.deepcopy(manifest))
    from schema_registry import LIVE_DELIVERY_VERSIONS, schema_digest
    for grant in grants:
        for binding in grant["live_bindings"]:
            binding["manifest_ref"] = copy.deepcopy(service["manifest_ref"])
        if "worker_commit" in grant["operations"]:
            grant["payload_types"] += list(LIVE_DELIVERY_VERSIONS)
        elif "intent" in grant["operations"]:
            grant["payload_types"].append("home-live-shadow-decision-3")
        grant["schema_pins"] = {name: schema_digest(name, profile="home-live-shadow-1") for name in grant["payload_types"]}
    return service, grants, config

"""Additive stationary virtual delivery authority; no physical transport.

The journal independently checks attribution, conservative geometry, and the
timestamped readback. Worker admission does not establish real-world accuracy.
"""
from __future__ import annotations

import json
import math
import copy
import re

from journal import bounded_json, canonical, digest, finite, identifier
from live_ingress_validation import _array, _exact, _integer, _nonnegative, _ref, _registration, _shape, _sha
from live_scene import (SCENE_SCHEMAS, LiveSceneWorkerBoundary, validate_scene_worker_grant,
                        validate_scene_worker_policy)
from live_worker import reject

DELIVERY_OUTPUTS = frozenset({"home-live-engagement-state-1", "home-live-shadow-decision-3"})
DELIVERY_SCHEMAS = frozenset({"home-live-shadow-decision-3", "home-live-worker-checkpoint-3",
                              "home-live-worker-commit-3", "home-live-input-cut-3"})
VERSION = "home-live-task-delivery-1.0.0"
STABLE_VERSION = "home-live-task-delivery-2.0.0"
LIFECYCLE_VERSION = "home-live-task-delivery-3.0.0"
CONSERVATIVE_VERSION = "home-live-task-delivery-4.0.0"
STABLE_DELIVERY_OUTPUTS = frozenset({"home-live-engagement-state-1", "home-live-shadow-decision-4"})
STABLE_DELIVERY_SCHEMAS = frozenset({"home-live-shadow-decision-4", "home-live-worker-checkpoint-4",
                                    "home-live-worker-commit-4", "home-live-input-cut-4"})
EPS = 1e-10


def _capture_metadata(payload, *, identity, registration):
    """Exclude only matching closed capture metadata, never a person."""
    association, key = payload.get('association', {}), payload.get('head', {}).get('key', {})
    names = {'work', 'body', 'head', 'hand_left', 'hand_right', 'protection', 'body_forward', 'head_forward'}
    return (payload.get('schema') == 'home-live-geometry-1' and payload.get('version') == 1
            and all(payload.get(k) == v for k, v in identity.items())
            and payload.get('registration') == registration
            and payload.get('person_ref') is None and payload.get('subject_ref') is None
            and payload.get('status') == 'unobservable' and payload.get('reason') in {'pose_unavailable', 'missing_measurement_bound'}
            and association.get('person_status') == association.get('subject_status') == 'not_applicable'
            and association.get('candidate_person_refs') == association.get('candidate_subject_refs') == []
            and payload.get('method_ref') is not None and association.get('method_ref') == payload['method_ref']
            and key == dict(source_id=payload.get('source_id'), payload_type='home-live-geometry-1', person_ref=None,
                            subject_ref=None, predicate='geometry_bundle', method_ref=payload['method_ref'])
            and set(payload.get('parts', {})) == names
            and all(set(part) == {'status', 'reason', 'support_refs', 'observed_at', 'valid_until'}
                    and part['status'] == 'unobservable' and part['reason'] == payload['reason']
                    and isinstance(part['support_refs'], list) and part['observed_at'] == payload['observed_at']
                    and finite(part['valid_until']) and finite(payload['valid_until']) and part['valid_until'] >= payload['valid_until'] for part in payload['parts'].values()))


def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    length = _norm(a)
    if length <= EPS:
        raise ValueError("degenerate direction")
    return [x / length for x in a]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _angle(a, b):
    return math.acos(max(-1, min(1, _dot(_unit(a), _unit(b)))))


def _primitive_bounds(shape, radius):
    if shape["kind"] == "aabb":
        return {"center_m": shape["center_m"], "half_extents_m": [x + radius for x in shape["half_extents_m"]]}
    if shape["kind"] == "capsule":
        return {"center_m": [(a + b) / 2 for a, b in zip(shape["a_m"], shape["b_m"])],
                "half_extents_m": [abs(a - b) / 2 + shape["radius_m"] + radius for a, b in zip(shape["a_m"], shape["b_m"])]}
    raise ValueError("unsupported primitive")


def _corners(box):
    return [[box["center_m"][i] + sign[i] * box["half_extents_m"][i] for i in range(3)]
            for sign in ((a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1))]


def _area(polygon):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))) / 2 if len(polygon) >= 3 else 0


def _hull(points):
    points = sorted(set(map(tuple, points)))
    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _clip(polygon, clip):
    result = list(polygon)
    for a, b in zip(clip, clip[1:] + clip[:1]):
        incoming, result = result, []
        if not incoming:
            break
        def side(point):
            return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        old, old_distance = incoming[-1], side(incoming[-1])
        for point in incoming:
            distance = side(point)
            if (distance >= -EPS) != (old_distance >= -EPS):
                ratio = old_distance / (old_distance - distance)
                result.append([old[0] + ratio * (point[0] - old[0]), old[1] + ratio * (point[1] - old[1])])
            if distance >= -EPS:
                result.append(point)
            old, old_distance = point, distance
    return result


def _inside(point, polygon):
    return all((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= -EPS for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def _projection(box, origin, normal, u, v):
    vectors = [_sub(point, origin) for point in _corners(box)]
    depths = [_dot(vector, normal) for vector in vectors]
    if min(depths) <= EPS:
        return None, min(depths), max(depths)
    return _hull([[_dot(vector, u) / depth, _dot(vector, v) / depth] for vector, depth in zip(vectors, depths)]), min(depths), max(depths)


def _direction(pan, tilt, rotation):
    pan, tilt = math.radians(pan), math.radians(tilt)
    local = [math.sin(tilt) * math.cos(pan), math.sin(tilt) * math.sin(pan), -math.cos(tilt)]
    return [sum(rotation[i * 3 + j] * local[j] for j in range(3)) for i in range(3)]


def _coverage(work, occluders, policy, pan, tilt):
    fixture, grid = policy["fixture"], policy["coverage"]["grid_size"]
    origin, optics = fixture["origin_m"], fixture["optics"]
    normal = _unit(_sub(work["center_m"], origin))
    u = _unit(_cross(normal, [0, 0, 1] if abs(normal[2]) < .9 else [0, 1, 0]))
    v = _cross(normal, u)
    hull, minimum_depth, maximum_depth = _projection(work, origin, normal, u, v)
    if hull is None:
        raise ValueError("fixture intersects target uncertainty")
    if policy['version'] >= 4:
        ray_min = [min(origin[i],work['center_m'][i]-work['half_extents_m'][i]) for i in range(3)]
        ray_max = [max(origin[i],work['center_m'][i]+work['half_extents_m'][i]) for i in range(3)]
        occluders = [box for box in occluders if not any(box['center_m'][i]+box['half_extents_m'][i] < ray_min[i]
                    or box['center_m'][i]-box['half_extents_m'][i] > ray_max[i] for i in range(3))]
    projections = [_projection(box, origin, normal, u, v) for box in occluders]
    axis = _direction(pan, tilt, fixture["local_to_room"])
    lo = [min(point[i] for point in hull) for i in range(2)]
    hi = [max(point[i] for point in hull) for i in range(2)]
    clear = blocked = uncertain = total = 0
    count = 0
    range_ok = all(_norm(_sub(point, origin)) <= optics["max_range_m"] for point in _corners(work))
    for x in range(grid):
        for y in range(grid):
            x0, x1 = (lo[0] + (hi[0] - lo[0]) * fraction / grid for fraction in (x, x + 1))
            y0, y1 = (lo[1] + (hi[1] - lo[1]) * fraction / grid for fraction in (y, y + 1))
            rectangle = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            cell = _clip(hull, rectangle)
            weight = _area(cell)
            if weight <= 1e-16:
                continue
            count += 1
            total += weight
            middle = [(x0 + x1) / 2, (y0 + y1) / 2]
            ray = _unit([normal[i] + u[i] * middle[0] + v[i] * middle[1] for i in range(3)])
            radius = max(_angle(ray, [normal[i] + u[i] * corner[0] + v[i] * corner[1] for i in range(3)]) for corner in rectangle)
            if not range_ok or _angle(ray, axis) + radius > math.radians(optics["envelope_half_angle_deg"] - optics["pointing_error_deg"]) + EPS:
                uncertain += weight
                continue
            state = "clear"
            for obstacle, near, far in projections:
                if far <= 0 or near >= maximum_depth - EPS:
                    continue
                if obstacle is None:
                    state = "uncertain"
                    continue
                if _area(_clip(cell, obstacle)) <= 1e-16:
                    continue
                if far < minimum_depth - EPS and all(_inside(point, obstacle) for point in cell):
                    state = "blocked"
                    break
                state = "uncertain"
            if state == "clear":
                clear += weight
            elif state == "blocked":
                blocked += weight
            else:
                uncertain += weight
    if total <= 1e-16:
        raise ValueError("empty work silhouette")
    return {"clear_fraction": clear / total, "blocked_fraction": blocked / total,
            "uncertain_fraction": uncertain / total, "total_cells": count}


def _protected(boxes, policy, pan, tilt, level):
    fixture, protection = policy["fixture"], policy["protection"]
    optics = fixture["optics"]
    if level * optics["outside_envelope_upper_fraction"] > protection["max_exposure_fraction"] + EPS:
        return False
    origin, axis = fixture["origin_m"], _direction(pan, tilt, fixture["local_to_room"])
    angle = math.radians(optics["envelope_half_angle_deg"] + optics["pointing_error_deg"])
    for box in boxes:
        vector = _sub(box["center_m"], origin)
        distance, radius = _norm(vector), _norm(box["half_extents_m"])
        if policy['version'] < 4 and distance - radius > optics["max_range_m"]:
            continue
        if distance <= (max(radius,EPS) if policy['version']>=4 else radius) or _angle(vector, axis) <= angle + math.asin(min(1, radius / distance)) + EPS:
            return False
    return True


def _vector(value, count=3):
    if not isinstance(value, list) or len(value) != count or not all(finite(x) for x in value):
        raise ValueError("finite fixed vector required")


def validate_delivery_policy(policy, sources, scene_policy, *, allow_stable=False):
    _exact(policy, {"schema", "version", "policy_ref", "registration", "fixture", "room", "protection", "requirements_ref", "required_body_parts", "coverage"})
    expected = 2 if allow_stable else 1
    if policy["schema"] != "home-live-task-delivery-policy-" + str(expected) or type(policy["version"]) is not int or policy["version"] != expected:
        raise ValueError("explicit stationary delivery policy required")
    _ref(policy["policy_ref"])
    _ref(policy["requirements_ref"])
    _registration(policy["registration"])
    if not any(source["registration"] == policy["registration"] for source in sources.values()):
        raise ValueError("delivery registration must match an authorized spatial source")
    fixture = policy["fixture"]
    _exact(fixture, {"id", "fixture_pose_ref", "family_ref", "assessment_ref", "origin_m", "local_to_room", "optics", "mechanics", "initial"})
    identifier(fixture["id"], "fixture_id")
    for key in ("fixture_pose_ref", "family_ref", "assessment_ref"):
        _ref(fixture[key])
    _vector(fixture["origin_m"])
    _vector(fixture["local_to_room"], 9)
    matrix = fixture["local_to_room"]
    rows = [matrix[n:n + 3] for n in (0, 3, 6)]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    determinant = (matrix[0] * (matrix[4] * matrix[8] - matrix[5] * matrix[7])
                   - matrix[1] * (matrix[3] * matrix[8] - matrix[5] * matrix[6])
                   + matrix[2] * (matrix[3] * matrix[7] - matrix[4] * matrix[6]))
    if abs(determinant - 1) > 1e-8 or any(abs(dot(a, b) - (1 if i == j else 0)) > 1e-8 for i, a in enumerate(rows) for j, b in enumerate(rows)):
        raise ValueError("fixture mounting transform must be explicit proper rigid rotation")
    optics = fixture["optics"]
    _exact(optics, {"envelope_half_angle_deg", "max_range_m", "pointing_error_deg", "full_dark_upper_fraction", "outside_envelope_upper_fraction"})
    if (not all(finite(x) for x in optics.values()) or not 0 < optics["envelope_half_angle_deg"] <= 89
            or not 0 <= optics["pointing_error_deg"] <= 30 or not 0 < optics["max_range_m"] <= 100
            or optics["full_dark_upper_fraction"] != 0 or not 0 <= optics["outside_envelope_upper_fraction"] <= 1):
        raise ValueError("complete bounded assessed optical envelope and full virtual darkness required")
    mechanics = fixture["mechanics"]
    _exact(mechanics, {"pan_min_deg", "pan_max_deg", "tilt_min_deg", "tilt_max_deg", "max_velocity_deg_s", "max_acceleration_deg_s2", "max_jerk_deg_s3", "settle_s", "fade_s"})
    if (not all(finite(x) for x in mechanics.values()) or not -720 <= mechanics["pan_min_deg"] < mechanics["pan_max_deg"] <= 720
            or not 0 <= mechanics["tilt_min_deg"] < mechanics["tilt_max_deg"] <= 180
            or not 0 < mechanics["max_velocity_deg_s"] <= 1000 or not 0 < mechanics["max_acceleration_deg_s2"] <= 10000
            or not 0 < mechanics["max_jerk_deg_s3"] <= 100000
            or not 0 <= mechanics["settle_s"] <= 30 or not 0 < mechanics["fade_s"] <= 30):
        raise ValueError("bounded assessed virtual mechanics required")
    _exact(fixture["initial"], {"pan_deg", "tilt_deg"})
    if any(not finite(fixture["initial"][axis + "_deg"]) or not mechanics[axis + "_min_deg"] <= fixture["initial"][axis + "_deg"] <= mechanics[axis + "_max_deg"] for axis in ("pan", "tilt")):
        raise ValueError("commissioned initial dark virtual pose required")
    room = policy["room"]
    _exact(room, {"assessment_ref", "collision_ref", "geometry_digest", "complete", "usable_bounds", "occluders", "valid_until"})
    _ref(room["assessment_ref"])
    _ref(room["collision_ref"])
    _sha(room["geometry_digest"])
    if room["complete"] is not True or room["geometry_digest"] != policy["registration"]["geometry_digest"]:
        raise ValueError("complete assessed canonical room geometry required")
    _shape(room["usable_bounds"])
    if room["usable_bounds"]["kind"] != "aabb":
        raise ValueError("explicit assessed usable room bounds required")
    _nonnegative(room["valid_until"], positive=True)
    _array(room["occluders"], 256)
    for obstacle in room["occluders"]:
        _exact(obstacle, {"id", "shape"})
        identifier(obstacle["id"], "occluder_id")
        _shape(obstacle["shape"])
        if obstacle["shape"]["kind"] not in {"aabb", "capsule"}:
            raise ValueError("bounded conservative room primitives required")
    if len({obstacle["id"] for obstacle in room["occluders"]}) != len(room["occluders"]):
        raise ValueError("unique canonical room occluder ids required")
    protection = policy["protection"]
    _exact(protection, {"assessment_ref", "policy_ref", "max_exposure_fraction"})
    _ref(protection["assessment_ref"])
    _ref(protection["policy_ref"])
    if not finite(protection["max_exposure_fraction"]) or not 0 <= protection["max_exposure_fraction"] <= 1:
        raise ValueError("bounded aggregate protection threshold required")
    _array(policy["required_body_parts"], 12, nonempty=True)
    for name in policy["required_body_parts"]:
        identifier(name, "required_body_part")
    coverage = policy["coverage"]
    _exact(coverage, {"grid_size", "min_fraction", "assessment_ref"})
    _integer(coverage["grid_size"], 3, 15)
    _ref(coverage["assessment_ref"])
    if coverage["grid_size"] % 2 != 1 or not finite(coverage["min_fraction"]) or not 0 < coverage["min_fraction"] <= 1:
        raise ValueError("explicit independent coverage threshold and odd bounded grid required")
    if any(example["requirements_ref"] != policy["requirements_ref"] or example["nominal"]["fixture_family_ref"] != fixture["family_ref"] for example in scene_policy["catalog"]["examples"]):
        raise ValueError("every approved stationary example must bind assessed requirements and fixture family")
    bounded_json(policy, 65536)


def validate_delivery_worker_policy(manifest, registry):
    policy = manifest["worker"]
    if (not registry.delivery_interface_digest or manifest["delivery_interface_digest"] != registry.delivery_interface_digest
            or policy.get("delivery_schema_pins") != registry.delivery_schema_pins):
        raise ValueError("exact additive delivery interface and schema pins required")
    base = dict(manifest)
    base["worker"] = {key: value for key, value in policy.items() if key not in {"delivery_schema_pins", "delivery_policy"}}
    validate_scene_worker_policy(base, registry)
    if set(policy) != set(base["worker"]) | {"delivery_schema_pins", "delivery_policy"}:
        raise ValueError("closed delivery worker policy required")
    validate_delivery_policy(policy["delivery_policy"], manifest["sources"], policy["scene_policy"])


def validate_delivery_grant(grant, config):
    if grant.payload_types & (DELIVERY_SCHEMAS - DELIVERY_OUTPUTS) and "worker_commit" not in grant.operations:
        raise ValueError("private delivery state requires the commissioned worker capability")
    if "home-live-shadow-decision-3" in grant.payload_types and "home-live-geometry-1" not in grant.payload_types:
        raise ValueError("complete delivery decision requires separate geometry authority")


def validate_delivery_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not DELIVERY_SCHEMAS <= grant.payload_types:
        raise ValueError("delivery worker requires the complete explicit delivery contract closure")


def validate_stable_delivery_worker_policy(manifest, registry):
    policy = manifest["worker"]
    if (not registry.stable_delivery_interface_digest
            or manifest["delivery_interface_digest"] != registry.stable_delivery_interface_digest
            or policy.get("delivery_schema_pins") != registry.stable_delivery_schema_pins):
        raise ValueError("exact retained-delivery interface and schema pins required")
    base = dict(manifest)
    base["worker"] = {key: value for key, value in policy.items() if key not in {"delivery_schema_pins", "delivery_policy"}}
    validate_scene_worker_policy(base, registry)
    if set(policy) != set(base["worker"]) | {"delivery_schema_pins", "delivery_policy"}:
        raise ValueError("closed retained-delivery worker policy required")
    validate_delivery_policy(policy["delivery_policy"], manifest["sources"], policy["scene_policy"], allow_stable=True)


def validate_stable_delivery_grant(grant, config):
    if grant.payload_types & (STABLE_DELIVERY_SCHEMAS - STABLE_DELIVERY_OUTPUTS) and "worker_commit" not in grant.operations:
        raise ValueError("private retained-delivery state requires the commissioned worker capability")
    if "home-live-shadow-decision-4" in grant.payload_types and "home-live-geometry-1" not in grant.payload_types:
        raise ValueError("complete retained-delivery decisions require separate geometry authority")


def validate_stable_delivery_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not STABLE_DELIVERY_SCHEMAS <= grant.payload_types:
        raise ValueError("retained-delivery worker requires the complete explicit contract closure")


class LiveDeliveryWorkerBoundary(LiveSceneWorkerBoundary):
    schema_types = SCENE_SCHEMAS | DELIVERY_SCHEMAS | LiveSceneWorkerBoundary.schema_types
    output_types = DELIVERY_OUTPUTS
    decision_type = "home-live-shadow-decision-3"
    checkpoint_type = "home-live-worker-checkpoint-3"
    commit_type = "home-live-worker-commit-3"
    cut_type = "home-live-input-cut-3"
    version = 3
    snapshot_version = VERSION
    worker_version = "home-live-context-worker-3.0.0"
    interface_attribute = "delivery_interface_digest"
    stable_delivery = False
    module_names = LiveSceneWorkerBoundary.module_names | {"app/src/home-live-task-delivery.js"}

    def __init__(self, ingress):
        super().__init__(ingress)
        self.delivery_policy = self.policy["delivery_policy"]

    def configuration_description(self, nested):
        description = super().configuration_description(nested)
        description.update(version=self.worker_version, delivery_policy=self.delivery_policy,
                           delivery_schema_pins=self.policy["delivery_schema_pins"], delivery_interface_digest=getattr(self.j.schemas, self.interface_attribute))
        return description

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if cp["delivery_schema_pins"] != self.policy["delivery_schema_pins"]:
            reject("checkpoint_conflict", "delivery checkpoint pins differ from the commissioned stage")
        return super()._checkpoint(cp, commit, next_seq, old, now)

    def settle(self, commit, grant, publications, decision, engagements, old):
        super().settle(commit, grant, publications, decision, engagements, old)
        self._delivery(commit, publications, decision, old)

    def _delivery(self, commit, publications, decision, old):
        cp, t, policy = commit["checkpoint"], decision["t"], self.delivery_policy
        snapshot = cp["state"]["virtual_execution"]
        state = snapshot["state"]
        if (snapshot["identity"] != self.ingress.identity or snapshot["version"] != self.snapshot_version
                or snapshot["config_digest"] != digest({"version": self.snapshot_version, "identity": self.ingress.identity, "policy": policy,
                                                         **({"lifecycle_policy": self.policy["lifecycle_policy"]} if getattr(self, "lifecycle_delivery", False) else {}),
                                                         "max_output_age_s": self.policy["max_output_age_s"]})
                or state["t"] != t or state["seq"] != decision["seq"] or state["authority_generation"] != cp["authority_generation"]):
            reject("invalid_delivery_checkpoint", "delivery snapshot must match the atomic scope, configuration, clock and authority")
        known, unresolved = self._hazards(state, publications, old, t)
        allocation, execution, lease = (decision[name] for name in ("allocation", "execution", "display_lease"))
        if (allocation["geometry_cut_digest"] != decision["input_cut"]["cut_digest"] or lease["sample_at"] != t
                or lease["authority_generation"] != cp["authority_generation"]):
            reject("invalid_delivery_reference", "delivery and lease must bind their exact input cut and authority")
        if execution["status"] == "unavailable":
            if (allocation["status"] != "unavailable" or lease["status"] != "withdrawn" or lease["valid_until"] != t
                    or lease["geometry_refs"] or lease["protection_refs"] or lease["reason"] != execution["reason"]
                    or allocation["reason"] != execution["reason"] or state["plan"] is not None
                    or state["last_readback"] is not None or state["last_samples"] or state["last_valid_until"] is not None):
                reject("invalid_delivery_withdrawal", "unavailable delivery cannot preserve an achieved state or active display lease")
            if state["execution_starts"] != (old["state"]["virtual_execution"]["state"]["execution_starts"] if old else 0):
                reject("invalid_execution_plan", "withdrawing delivery cannot erase its prior execution starts")
            return
        if (allocation["status"] != "allocated" or lease["status"] != "current" or lease["reason"] != "qualified_virtual_samples"
                or execution["sample_at"] != t or execution["interval_start"] != t or execution["interval_end"] != t
                or len(execution["samples"]) != 1 or execution["samples"][0] != {"t": t, "readback": execution["readback"]}
                or state["last_samples"] != execution["samples"] or len(execution["readback"]) != 1
                or state["last_readback"] != execution["readback"][0]):
            reject("invalid_execution_sample", "only the current actually sampled virtual state may be published as achieved")
        goal, scene = decision["goal"], decision["scene"]
        if goal["status"] != "requested" or scene["status"] != "selected":
            reject("invalid_delivery_goal", "current focused delivery requires the supported task need and selected composition")
        if unresolved:
            reject("invalid_delivery_geometry", "unresolved person association remains a protection hazard")
        until = lease["valid_until"]
        domains = [policy['modeled_emission']['valid_until'], policy['observation_coverage']['valid_until']] if getattr(self, 'conservative_delivery', False) else []
        if (not t < until <= min(t + self.policy["max_output_age_s"], goal["valid_until"], scene["valid_until"], policy["room"]["valid_until"], *domains)
                or execution["valid_until"] != until or allocation["valid_until"] != until or state["last_valid_until"] != until):
            reject("invalid_display_lease", "display must expire at its earliest scene, goal, geometry or assessed-room dependency")
        raw = [publication for publication in publications.values() if publication["payload_type"] == "home-live-geometry-1"
               and self.ingress._current_payload(publication["payload"], t)
               and not (getattr(self, 'conservative_delivery', False)
                        and _capture_metadata(publication['payload'], identity=self.ingress.identity,
                                              registration=policy['registration']))]
        references = [self.ingress._publication_ref(publication) for publication in raw]
        if (not references or lease["geometry_refs"] != references or lease["protection_refs"] != references
                or any(reference not in decision["input_cut"]["geometry_refs"] for reference in references)):
            reject("invalid_delivery_reference", "every current room geometry, including other people, must contribute its exact publication reference")
        targets = [publication for publication in raw if publication["payload"]["person_ref"] == goal["person_ref"]
                   and publication["payload"]["subject_ref"] == goal["subject_ref"]]
        if len(targets) != 1 or self.ingress._publication_ref(targets[0]) not in goal["geometry_refs"]:
            reject("invalid_delivery_reference", "one qualified person-subject work geometry must match the actual task goal")
        target = targets[0]["payload"]
        occluders = [_primitive_bounds(obstacle["shape"], 0) for obstacle in policy["room"]["occluders"]]
        protected, work = [], None
        room = policy["room"]["usable_bounds"]
        def inside_room(box):
            return all(abs(box["center_m"][i] - room["center_m"][i]) + box["half_extents_m"][i] <= room["half_extents_m"][i] + EPS for i in range(3))
        if not inside_room({"center_m": policy["fixture"]["origin_m"], "half_extents_m": [0, 0, 0]}):
            reject("invalid_delivery_geometry", "fixture must remain inside the assessed usable room")
        for publication in raw:
            geometry = publication["payload"]
            association, source = geometry["association"], self.ingress.sources[geometry["source_id"]]
            if (geometry["status"] != "supplied" or geometry["registration"] != policy["registration"]
                    or association["person_status"] != "qualified" or association["candidate_person_refs"]
                    or geometry is target and (association["subject_status"] != "qualified" or association["candidate_subject_refs"])):
                reject("invalid_delivery_geometry", "unobservable, ambiguous or differently registered people cannot disappear from the protection assessment")
            if until > min(geometry["valid_until"], geometry["observed_at"] + source["max_age_s"]) - geometry["clock"]["uncertainty_s"]:
                reject("invalid_display_lease", "display cannot refresh the original source observation")
            names = ["body", "head", "hand_left", "hand_right", "protection"] + (["work"] if geometry is target else [])
            for name in names:
                part = geometry["parts"][name]
                error = geometry["clock"]["uncertainty_s"]
                if part['status'] != 'supplied' or part['observed_at'] > t:
                    reject('invalid_delivery_geometry', 'every required anatomy and work bound must be supplied at the decision cut')
                expiry = part['valid_until']-error if getattr(self,'conservative_delivery',False) else part['valid_until']
                age_expiry = part['uncertainty']['age']['origin_at']+part['uncertainty']['age']['max_age_s']
                if getattr(self,'conservative_delivery',False):
                    age_expiry -= error
                compared_until = until if getattr(self,'conservative_delivery',False) else until+error
                if (part["status"] != "supplied" or part["observed_at"] > t or compared_until > expiry
                        or compared_until > age_expiry or getattr(self,'conservative_delivery',False)
                        and (until+error>part['valid_until'] or until+error>part['uncertainty']['age']['origin_at']+part['uncertainty']['age']['max_age_s'])):
                    reject("invalid_delivery_geometry", "every required anatomical and work bound must remain qualified through the display lease")
                uncertainty = part["uncertainty"]
                age = uncertainty["age"]
                dt = until + error - age["origin_at"]
                radius = uncertainty["bound_at_observation_m"] + age["max_speed_m_s"] * dt + .5 * age["max_accel_m_s2"] * dt * dt
                shape = part["shape"]
                if name == "body":
                    if any(required not in {item["part_id"] for item in shape["items"]} for required in policy["required_body_parts"]):
                        reject("invalid_delivery_geometry", "modeled body coverage must include every commissioned anatomical primitive")
                if name == "protection" and shape["policy_ref"] != policy["protection"]["policy_ref"]:
                    reject("invalid_delivery_geometry", "protection shape must name the commissioned aggregate protection policy")
                for item in shape["items"] if name in {"body", "protection"} else [shape]:
                    box = _primitive_bounds(item, radius)
                    if not inside_room(box):
                        reject("invalid_delivery_geometry", "uncertain geometry must remain inside the assessed usable room")
                    if name == "work":
                        work = box
                    else:
                        occluders.append(box)
                        if name in {"head", "protection"}:
                            protected.append(box)
        current_people = self.current_people(publications, t)
        geometry_people = {canonical(publication["payload"]["person_ref"]) for publication in raw}
        if not (set(current_people) | known) <= geometry_people:
            reject("invalid_delivery_geometry", "a known person without current qualified geometry remains an unresolved protection hazard")
        fixture = policy["fixture"]
        if all(abs(fixture["origin_m"][axis] - work["center_m"][axis]) <= work["half_extents_m"][axis] + EPS for axis in range(3)):
            reject("invalid_delivery_geometry", "work uncertainty containing the fixture has no qualified aiming envelope")
        if (allocation["fixture_ids"] != [fixture["id"]] or allocation["policy_refs"] != [policy["policy_ref"]]
                or len(allocation["assignments"]) != 1 or execution["assignments"] != allocation["assignments"]
                or execution["assessment_refs"] != [fixture["assessment_ref"], policy["room"]["assessment_ref"], policy["protection"]["assessment_ref"], policy["coverage"]["assessment_ref"],
                    *([policy['modeled_emission']['assessment_ref'],policy['observation_coverage']['assessment_ref']] if getattr(self,'conservative_delivery',False) else [])]):
            reject("invalid_delivery_assignment", "one assessed virtual fixture must preserve exact policy and assessment references")
        command = allocation["assignments"][0]
        if (command["fixture_id"] != fixture["id"] or command["fixture_pose_ref"] != fixture["fixture_pose_ref"]
                or command["goal_id"] != goal["goal_id"] or command["nominal"] != goal["nominal"]):
            reject("invalid_delivery_assignment", "allocation cannot replace a selected scene, nominal setting, or commissioned fixture pose")
        displacement = _sub(target["parts"]["work"]["shape"]["center_m"], fixture["origin_m"])
        if _norm(displacement) <= EPS:
            reject("invalid_delivery_geometry", "work at the fixture origin has no qualified aiming direction")
        delta = _unit(displacement)
        matrix = fixture["local_to_room"]
        local = [sum(matrix[row * 3 + col] * delta[row] for row in range(3)) for col in range(3)]
        expected_aim = {"pan_deg": math.degrees(math.atan2(local[1], local[0])), "tilt_deg": math.degrees(math.atan2(math.hypot(local[0], local[1]), -local[2]))}
        mechanics = fixture["mechanics"]
        if getattr(self,'conservative_delivery',False):
            choices = [expected_aim['pan_deg']+360*k for k in range(-4,5)
                       if mechanics['pan_min_deg'] <= expected_aim['pan_deg']+360*k <= mechanics['pan_max_deg']]
            if choices:
                expected_aim['pan_deg'] = min(choices,key=lambda pan:(abs(pan-fixture['initial']['pan_deg']),pan))
        previous = old["state"]["virtual_execution"]["state"] if old else None
        retained = (self.stable_delivery and previous is not None and previous["plan"] is not None
                    and previous["authority_generation"] == state["authority_generation"]
                    and previous["last_valid_until"] is not None and t < previous["last_valid_until"]
                    and (state["plan"] == previous["plan"] or getattr(self, "lifecycle_delivery", False)
                         and self._compatible_level_change(previous["plan"], state["plan"])))
        if not retained and any(abs(command[key] - value) > 1e-7 for key, value in expected_aim.items()):
            reject("invalid_delivery_assignment", "stationary aim must resolve the exact registered work region")
        if any(not mechanics[axis + "_min_deg"] <= command[axis + "_deg"] <= mechanics[axis + "_max_deg"] for axis in ("pan", "tilt")):
            reject("invalid_delivery_assignment", "aim exceeds the commissioned mechanical envelope")
        level_upper = max(goal["nominal"]["brightness_fraction"], (state.get("brightness_transition") or {}).get("from_fraction", 0)) if getattr(self, "lifecycle_delivery", False) else goal["nominal"]["brightness_fraction"]
        if (not _protected(protected, policy, command["pan_deg"], command["tilt_deg"], level_upper)
                or any(_norm(_sub(point, fixture["origin_m"])) > fixture["optics"]["max_range_m"] for point in _corners(work))):
            reject("invalid_delivery_protection", "full emitted optical envelope and uncertainty must protect every head and protection volume")
        coverage = _coverage(work, occluders, policy, command["pan_deg"], command["tilt_deg"])
        if (coverage["clear_fraction"] + EPS < policy["coverage"]["min_fraction"] or len(allocation["candidates"]) != 1):
            reject("invalid_delivery_coverage", "a clear central ray cannot establish conservative work-volume coverage")
        candidate = allocation["candidates"][0]
        if (candidate["fixture_id"] != fixture["id"] or candidate["status"] != "feasible" or candidate["reason"] != "coverage_met"
                or candidate["approach"] != "unranked" or any(abs(candidate[key] - command[key]) > 1e-7 for key in ("pan_deg", "tilt_deg"))
                or candidate["coverage"]["total_cells"] != coverage["total_cells"]
                or any(abs(candidate["coverage"][key] - coverage[key]) > 1e-7 for key in ("clear_fraction", "blocked_fraction", "uncertain_fraction"))):
            reject("invalid_delivery_coverage", "candidate coverage must match the independently computed conservative angular silhouette")
        self._mechanics(state, old, decision, command, target, publications)

    def _hazards(self, state, publications, old, t):
        previous = old["state"]["virtual_execution"]["state"] if old else None
        known = {canonical(person): person for person in previous["known_persons"]} if previous else {}
        anchors = dict(previous["person_observed_through"]) if previous else {}
        sticky = previous["unresolved_person"] if previous else False
        rows = [publication["payload"] for publication in publications.values() if publication["payload_type"] in self.input_types
                and publication["payload_type"] != "home-live-intent-event-1"]
        association_unresolved = False
        for payload in rows:
            if not payload["observed_at"] <= payload["available_at"] <= t:
                continue
            association = payload["association"]
            if association["person_status"] == "unresolved":
                association_unresolved = True
                if not association["candidate_person_refs"]:
                    sticky = True
                for person in association["candidate_person_refs"]:
                    known[canonical(person)] = person
                    anchors[digest(person)] = max(anchors.get(digest(person), 0), payload["observed_at"] + payload["clock"]["uncertainty_s"])
            person = payload["person_ref"]
            if person is not None and not (payload.get("kind") == "person_presence" and payload.get("value") == "observed_absent"):
                known[canonical(person)] = person
                anchors[digest(person)] = max(anchors.get(digest(person), 0), payload["observed_at"] + payload["clock"]["uncertainty_s"])
        for payload in rows:
            if (payload["schema"] == "home-live-fact-1" and payload["status"] == "supplied"
                    and payload["kind"] == "person_presence" and payload["value"] == "observed_absent"
                    and payload["association"]["person_status"] == "qualified"
                    and self.ingress._current_payload(payload, t)
                    and payload["observed_at"] - payload["clock"]["uncertainty_s"] > anchors.get(digest(payload["person_ref"]), -1)
                    and not any(geometry["schema"] == "home-live-geometry-1" and geometry["status"] == "supplied"
                                and geometry["person_ref"] == payload["person_ref"] and self.ingress._current_payload(geometry, t) for geometry in rows)):
                known.pop(canonical(payload["person_ref"]), None)
                anchors.pop(digest(payload["person_ref"]), None)
        if len(known) > 128:
            sticky = True
        actual = {canonical(person) for person in state["known_persons"]}
        if (len(known) <= 128 and actual != set(known) or len(known) > 128 and (len(actual) != 128 or not actual <= set(known))
                or state["unresolved_person"] is not sticky
                or state["person_observed_through"] != {digest(known[key]): anchors[digest(known[key])] for key in actual}):
            reject("invalid_delivery_hazards", "source loss, track reuse and cold starts cannot erase independently remembered person hazards")
        return actual, sticky or association_unresolved

    def _mechanics(self, state, old, decision, command, target, publications=None):
        t, policy = decision["t"], self.delivery_policy
        plan, fixture = state["plan"], policy["fixture"]
        if plan is None:
            reject("invalid_execution_sample", "sampled execution requires its durable bounded motion plan")
        previous = old["state"]["virtual_execution"]["state"] if old else None
        previous_plan = previous["plan"] if (previous and previous["authority_generation"] == state["authority_generation"]
                                             and previous["last_valid_until"] is not None and t < previous["last_valid_until"]) else None
        brightness_only = self._brightness_transition(state, previous, previous_plan, decision, publications) if getattr(self, "lifecycle_delivery", False) else False
        geometry_signature = (previous_plan["geometry_signature"] if self.stable_delivery and (plan == previous_plan or brightness_only)
                              else digest({"person_ref": decision["goal"]["person_ref"], "subject_ref": decision["goal"]["subject_ref"], "work": target["parts"]["work"]["shape"]}))
        key = digest({"geometry_signature": geometry_signature, "goal_id": command["goal_id"], "scene_id": decision["scene"]["scene_id"], "nominal": command["nominal"]})
        if (plan["geometry_signature"] != geometry_signature or plan["key"] != key or plan["person_ref"] != decision["goal"]["person_ref"]
                or plan["subject_ref"] != decision["goal"]["subject_ref"] or plan["goal_id"] != command["goal_id"]
                or plan["scene_id"] != decision["scene"]["scene_id"] or plan["nominal"] != command["nominal"]
                or plan["to_pan_deg"] != command["pan_deg"] or plan["to_tilt_deg"] != command["tilt_deg"]):
            reject("invalid_execution_plan", "virtual motion must preserve the actual stationary work, beneficiary, scene and command")
        restarted = previous_plan is None or not brightness_only and (previous_plan["key"] != key or plan["started_at"] == t and plan != previous_plan)
        if previous_plan is not None and previous_plan["key"] == key:
            if plan != previous_plan:
                reject("invalid_execution_plan", "continued stationary delivery cannot rewrite or restart its previous committed trajectory")
        if restarted and (plan["started_at"] != t or plan["from_pan_deg"] != fixture["initial"]["pan_deg"] or plan["from_tilt_deg"] != fixture["initial"]["tilt_deg"]):
            reject("invalid_execution_plan", "a new discontinuous virtual plan starts at the current tick in the commissioned dark pose")
        prior_starts = previous["execution_starts"] if previous else 0
        if state["execution_starts"] != prior_starts + (1 if restarted else 0):
            reject("invalid_execution_plan", "same-domain recovery must preserve its cumulative virtual execution starts")
        expected_continuity = "initial_dark_start" if restarted and prior_starts == 0 else "discontinuous_dark_reset" if restarted else "continuous"
        if decision["execution"]["continuity"] != expected_continuity:
            reject("invalid_execution_plan", "readback must distinguish bounded continuity from a discontinuous virtual reset")
        mechanics = fixture["mechanics"]
        if plan["settle_s"] != mechanics["settle_s"] or plan["fade_s"] != mechanics["fade_s"] or not plan["started_at"] <= t:
            reject("invalid_execution_plan", "settling and fade must preserve the assessed timing policy")
        motion = plan["motion"]
        if motion["started_at"] != plan["started_at"] or motion["duration_s"] != plan["duration_s"]:
            reject("invalid_execution_plan", "motion polynomial clock differs from its durable plan")
        duration = plan["duration_s"]
        if not .1 <= duration <= 30:
            reject("invalid_execution_plan", "bounded nonzero virtual travel duration required")
        delta = max(abs(plan["to_pan_deg"] - plan["from_pan_deg"]), abs(plan["to_tilt_deg"] - plan["from_tilt_deg"]))
        required_duration = max(.1, 1.875 * delta / mechanics["max_velocity_deg_s"], math.sqrt(6 * delta / mechanics["max_acceleration_deg_s2"]), (60 * delta / mechanics["max_jerk_deg_s3"]) ** (1 / 3))
        if abs(duration - required_duration) > 1e-7:
            reject("invalid_execution_plan", "trajectory must obey bounded velocity, acceleration and jerk over its entire duration")
        elapsed = t - plan["started_at"]
        moving = elapsed < duration
        settling = not moving and elapsed < duration + plan["settle_s"]
        fade = max(0, min(1, (elapsed - duration - plan["settle_s"]) / plan["fade_s"]))
        sample = {"fixture_id": fixture["id"], "fixture_pose_ref": fixture["fixture_pose_ref"],
                  "brightness_fraction": 0 if moving or settling else plan["nominal"]["brightness_fraction"] * fade,
                  "cct_kelvin": plan["nominal"]["cct_kelvin"], "phase": "moving" if moving else "settling" if settling else "fading" if fade < 1 else "lit"}
        if getattr(self, "lifecycle_delivery", False):
            transition = state["brightness_transition"]
            if decision["execution"].get("brightness_transition") != transition:
                reject("invalid_brightness_transition", "public transition must match the exact durable segment")
            if transition is not None and not (moving or settling):
                progress = 1 if transition["duration_s"] == 0 else max(0, min(1, (t - transition["started_at"]) / transition["duration_s"]))
                level = transition["from_fraction"] + (transition["to_fraction"] - transition["from_fraction"]) * progress
                sample["brightness_fraction"] = min(sample["brightness_fraction"], level) if transition["from_fraction"] == 0 else level
                if progress < 1:
                    sample["phase"] = "fading"
        def value(coefficients, at):
            return sum(coefficient * at ** power for power, coefficient in enumerate(coefficients))
        def derivative(coefficients):
            return [coefficient * power for power, coefficient in enumerate(coefficients) if power]
        for axis in ("pan", "tilt"):
            start, end = plan["from_" + axis + "_deg"], plan["to_" + axis + "_deg"]
            displacement = end - start
            expected = [start, 0, 0, 10 * displacement / duration ** 3, -15 * displacement / duration ** 4, 6 * displacement / duration ** 5]
            coefficients = motion[axis + "_coefficients"]
            if any(abs(a - b) * duration ** power > 1e-7 for power, (a, b) in enumerate(zip(expected, coefficients))):
                reject("invalid_execution_plan", "virtual trajectory must be the exact bounded rest-to-rest quintic")
            sample[axis + "_deg"] = value(coefficients, elapsed) if moving else end
            sample[axis + "_velocity_deg_s"] = value(derivative(coefficients), elapsed) if moving else 0
            sample[axis + "_acceleration_deg_s2"] = value(derivative(derivative(coefficients)), elapsed) if moving else 0
        actual = state["last_readback"]
        if (any(actual[key] != value for key, value in sample.items() if not finite(value))
                or any(abs(actual[key] - value) > 1e-7 for key, value in sample.items() if finite(value))
                or decision["execution"]["motion"] != (motion if moving else None)):
            reject("invalid_execution_sample", "achieved readback cannot jump to a proposal or skip dark motion, settling or fade")


class StableLiveDeliveryWorkerBoundary(LiveDeliveryWorkerBoundary):
    """Config5 only: retain a prior feasible endpoint against every current cut."""
    schema_types = LiveDeliveryWorkerBoundary.schema_types | STABLE_DELIVERY_SCHEMAS
    output_types = STABLE_DELIVERY_OUTPUTS
    decision_type = "home-live-shadow-decision-4"
    checkpoint_type = "home-live-worker-checkpoint-4"
    commit_type = "home-live-worker-commit-4"
    cut_type = "home-live-input-cut-4"
    version = 4
    snapshot_version = STABLE_VERSION
    worker_version = "home-live-context-worker-4.0.0"
    interface_attribute = "stable_delivery_interface_digest"
    stable_delivery = True


LIFECYCLE_DELIVERY_SCHEMAS = frozenset({"home-live-shadow-decision-5", "home-live-worker-checkpoint-5",
    "home-live-worker-commit-5", "home-live-input-cut-5", "home-live-intent-event-2", "home-live-person-lifecycle-1"})
LIFECYCLE_DELIVERY_OUTPUTS = frozenset({"home-live-engagement-state-1", "home-live-shadow-decision-5"})


def validate_lifecycle_delivery_worker_policy(manifest, registry):
    worker = manifest["worker"]
    if (not registry.lifecycle_interface_digest or manifest["lifecycle_interface_digest"] != registry.lifecycle_interface_digest
            or worker.get("lifecycle_schema_pins") != registry.lifecycle_schema_pins):
        raise ValueError("exact lifecycle interface and schema pins required")
    policy = worker["delivery_policy"]
    if (policy.get("schema") != "home-live-task-delivery-policy-3" or policy.get("version") != 3
            or not finite(policy.get("brightness_transition_s")) or not 0 <= policy["brightness_transition_s"] <= 30):
        raise ValueError("explicit bounded brightness transition required")
    base = copy.deepcopy(manifest)
    del base["worker"]["lifecycle_policy"]
    del base["worker"]["lifecycle_schema_pins"]
    base["worker"]["delivery_policy"].update(schema="home-live-task-delivery-policy-2", version=2)
    del base["worker"]["delivery_policy"]["brightness_transition_s"]
    validate_stable_delivery_worker_policy(base, registry)


def validate_lifecycle_delivery_grant(grant, config):
    if grant.payload_types & (LIFECYCLE_DELIVERY_SCHEMAS - LIFECYCLE_DELIVERY_OUTPUTS - {"home-live-intent-event-2", "home-live-person-lifecycle-1"}) and "worker_commit" not in grant.operations:
        raise ValueError("private lifecycle state requires the commissioned worker capability")
    if "home-live-shadow-decision-5" in grant.payload_types and "home-live-geometry-1" not in grant.payload_types:
        raise ValueError("full lifecycle delivery decision requires geometry authority")


def validate_lifecycle_delivery_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not LIFECYCLE_DELIVERY_SCHEMAS <= grant.payload_types:
        raise ValueError("lifecycle worker requires the complete explicit contract closure")


class LifecycleLiveDeliveryWorkerBoundary(StableLiveDeliveryWorkerBoundary):
    """Config6 independently verifies brightness segments and attributable hazard recovery."""
    schema_types = LiveDeliveryWorkerBoundary.schema_types | LIFECYCLE_DELIVERY_SCHEMAS
    output_types = LIFECYCLE_DELIVERY_OUTPUTS
    input_types = LiveDeliveryWorkerBoundary.input_types | {"home-live-person-lifecycle-1", "home-live-intent-event-2"}
    decision_type = "home-live-shadow-decision-5"
    checkpoint_type = "home-live-worker-checkpoint-5"
    commit_type = "home-live-worker-commit-5"
    cut_type = "home-live-input-cut-5"
    version = 5
    snapshot_version = LIFECYCLE_VERSION
    scene_state_version = "home-live-scene-state-2.0.0"
    supports_brightness = True
    intent_types = frozenset({"home-live-intent-event-1", "home-live-intent-event-2"})
    external_intent_kinds = frozenset({"clear_person_hazards"})
    worker_version = "home-live-context-worker-5.0.0"
    lifecycle_delivery = True
    module_names = StableLiveDeliveryWorkerBoundary.module_names | {"app/src/home-live-person-lifecycle.js"}

    def configuration_description(self, nested):
        result = super().configuration_description(nested)
        result.update(scene_state_version=self.scene_state_version,lifecycle_policy=self.policy["lifecycle_policy"], lifecycle_schema_pins=self.policy["lifecycle_schema_pins"],
                      lifecycle_interface_digest=self.j.schemas.lifecycle_interface_digest)
        return result

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if cp["lifecycle_schema_pins"] != self.policy["lifecycle_schema_pins"]:
            reject("checkpoint_conflict", "lifecycle pins must match commissioned configuration")
        return super()._checkpoint(cp, commit, next_seq, old, now)

    @staticmethod
    def _compatible_level_change(before, after):
        return (before is not None and after is not None
                and {k: v for k, v in before.items() if k not in {"key", "nominal", "goal_id"}} == {k: v for k, v in after.items() if k not in {"key", "nominal", "goal_id"}}
                and before["nominal"]["cct_kelvin"] == after["nominal"]["cct_kelvin"]
                and before["nominal"]["fixture_family_ref"] == after["nominal"]["fixture_family_ref"])

    def _brightness_transition(self, state, previous, prior_plan, decision, publications):
        transition, plan, t = state["brightness_transition"], state["plan"], decision["t"]
        changed = prior_plan is not None and prior_plan["nominal"] != plan["nominal"]
        if changed:
            refs = decision["scene"]["correction_refs"]
            matches = [p for p in publications.values() if len(refs) == 1 and p["payload"]["id"] == refs[0]
                       and p["payload_type"] == "home-live-intent-event-2" and p["payload"]["kind"] == "set_brightness"]
            correction = matches[0] if len(matches) == 1 else None
            if (correction is None or correction["payload"]["value"] != {"scene_id": plan["scene_id"],
                    "episode_id": decision["scene"]["episode_id"], "subject_ref": plan["subject_ref"],
                    "brightness_fraction": plan["nominal"]["brightness_fraction"]}
                    or correction["payload"]["target"] != plan["person_ref"]):
                reject("invalid_brightness_transition", "brightness changes require the exact current scene correction ancestry")
            expected = {"started_at": t, "duration_s": self.delivery_policy["brightness_transition_s"],
                        "from_fraction": previous["last_readback"]["brightness_fraction"],
                        "to_fraction": plan["nominal"]["brightness_fraction"], "intent_ref": self.ingress._publication_ref(correction)}
            if not self._compatible_level_change(prior_plan, plan) or transition != expected:
                reject("invalid_brightness_transition", "brightness changes preserve the actual aim and start from its prior sampled level")
        elif transition != (previous["brightness_transition"] if prior_plan is not None else None):
            reject("invalid_brightness_transition", "unchanged or reset aim cannot invent a brightness segment")
        return changed

    def validate_external_intent_result(self, intent, result, *, commit, publications, decision, engagements, old):
        if intent["schema"] != "home-live-intent-event-2" or intent["kind"] != "clear_person_hazards":
            reject("invalid_application_result", "only explicit external hazard commands enter this branch")
        state = commit["checkpoint"]["state"]["virtual_execution"]["state"]
        self._hazards(state, publications, old, decision["t"], decision=decision)
        matches = [r for r in state["clearance_settlements"] if r["id"] == intent["id"] and r["digest"] == digest(intent)]
        if len(matches) != 1 or any(result[k] != matches[0][k] for k in ("id", "status", "reason")):
            reject("invalid_application_result", "clearance settlement must have its independently verified exact effect")

    def _hazards(self, state, publications, old, t, decision=None):
        previous = old["state"]["virtual_execution"]["state"] if old else None
        hazards = {h["hazard_ref"]["id"]: copy.deepcopy(h) for h in previous["hazards"]} if previous else {}
        resolutions = copy.deepcopy(previous["hazard_resolutions"]) if previous else []
        settlements = copy.deepcopy(previous["clearance_settlements"]) if previous else []
        policy = self.policy["lifecycle_policy"]
        refs = {p["payload"]["id"]: self.ingress._publication_ref(p) for p in publications.values()}
        rows = sorted((p for p in publications.values() if p["payload_type"] in {"home-live-fact-1", "home-live-geometry-1", "home-live-person-lifecycle-1"}), key=lambda p: p["cursor"])
        def seal(body, key):
            return {"hazard_ref": {"id": key, "digest": digest(body)}, **body}
        if previous is None:
            key = "hazard:history:" + digest(self.ingress.identity)[:48]
            hazards[key] = seal({"kind": "unreconciled_session_history", "person_ref": None, "origin_ref": None, "source_id": None,
                                 "source_generation": None, "capture_epoch": None, "observed_through": None, "created_at": t}, key)
        def fresh(payload):
            source = self.ingress.sources[payload["source_id"]]
            return (payload["observed_at"] <= payload["available_at"] <= t
                    and t < min(payload["valid_until"], payload["observed_at"] + source["max_age_s"]) - payload["clock"]["uncertainty_s"])
        def remember(person, payload, unknown=False):
            ref, through = refs[payload["id"]], payload["observed_at"] + payload["clock"]["uncertainty_s"]
            key = "hazard:" + (("unknown:" + digest(ref)) if unknown else ("person:" + digest(person)))[:100]
            if any((r["hazard"]["origin_ref"] == ref if unknown else r["hazard"]["kind"] == "known_person"
                    and r["hazard"]["person_ref"] == person and through <= r["cleared_through"]) for r in resolutions):
                return
            prior = hazards.get(key)
            if prior and through <= prior["observed_through"]:
                return
            body = {"kind": "unattributed" if unknown else "known_person", "person_ref": None if unknown else person,
                    "origin_ref": ref, "source_id": payload["source_id"], "source_generation": payload["source_generation"],
                    "capture_epoch": payload.get("head", {}).get("position", {}).get("capture_epoch", payload["clock"].get("source_epoch")),
                    "observed_through": through, "created_at": prior["created_at"] if prior else t}
            hazards[key] = seal(body, key)
        for publication in rows:
            payload = publication["payload"]
            if not payload["observed_at"] <= payload["available_at"] <= t:
                continue
            if payload["schema"] == "home-live-person-lifecycle-1":
                if payload["target_person_ref"] is None and payload["disposition"] in {"observation_gap", "association_reset"}:
                    remember(None, payload, True)
                continue
            association = payload["association"]
            if association["person_status"] == "unresolved":
                if not association["candidate_person_refs"]:
                    remember(None, payload, True)
                for candidate in association["candidate_person_refs"]:
                    remember(candidate, payload)
            person = payload["person_ref"]
            if person is not None and not (payload.get("kind") == "person_presence" and payload.get("value") == "observed_absent"):
                remember(person, payload)
        def contradictory(hazard):
            return any(p["payload_type"] == "home-live-geometry-1" and fresh(p["payload"]) and p["payload"]["status"] == "supplied"
                       and p["payload"]["association"]["person_status"] == "qualified"
                       and (hazard["kind"] != "known_person" or p["payload"]["person_ref"] == hazard["person_ref"]) for p in rows)
        def resolve(hazard, ref, method, through):
            if len(resolutions) >= 128:
                reject("invalid_delivery_hazards", "resolution ledger exhaustion cannot discard immutable history")
            resolutions.append({"hazard": copy.deepcopy(hazard), "resolution_ref": ref, "method": method, "resolved_at": t, "cleared_through": through})
            del hazards[hazard["hazard_ref"]["id"]]
        if len(hazards) > 128:
            reject("invalid_delivery_hazards", "hazard exhaustion refuses without silently dropping occupants")
        for p in rows:
            payload = p["payload"]
            if (payload["schema"] != "home-live-person-lifecycle-1" or not fresh(payload) or payload["target_person_ref"] is None
                    or payload["disposition"] not in {"absence_qualified", "departure_qualified"}
                    or not any(all(payload[k] == method[k] for k in ("method_ref", "qualification_ref", "applicability_ref", "coverage_assessment_ref")) for method in policy["absence_methods"])
                    or not payload["basis_refs"]):
                continue
            lower = payload["observed_at"] - payload["clock"]["uncertainty_s"]
            for hazard in list(hazards.values()):
                if hazard["kind"] == "known_person" and hazard["person_ref"] == payload["target_person_ref"] and lower > hazard["observed_through"] and not contradictory(hazard):
                    resolve(hazard, refs[payload["id"]], "qualified_absence", lower)
        intents = sorted((p["payload"] for p in publications.values() if p["payload_type"] == "home-live-intent-event-2" and p["payload"]["kind"] == "clear_person_hazards"), key=lambda p: refs[p["id"]]["cursor"])
        results = []
        for intent in intents:
            prior = next((r for r in settlements if r["id"] == intent["id"]), None)
            if prior:
                if prior["digest"] != digest(intent):
                    reject("invalid_delivery_hazards", "immutable clearance identity conflict")
                results.append({k: prior[k] for k in ("id", "status", "reason")})
                continue
            if intent["available_at"] > t or intent["set_at"] > t:
                continue
            value, commissioned, reason, targets = intent["value"], policy["operator_clearance"], None, []
            if intent["valid_until"] <= t:
                reason = "clearance_intent_expired"
            elif commissioned is None:
                reason = "applicable_clearance_policy_required"
            elif (any(value[k] != commissioned[k] for k in ("policy_ref", "applicability_ref", "procedure_ref", "room_assessment_ref"))
                  or value["room_assessment_ref"] != self.delivery_policy["room"]["assessment_ref"] or not t < self.delivery_policy["room"]["valid_until"]):
                reason = "clearance_policy_mismatch"
            elif value["inspection_at"] > intent["set_at"] or t - value["inspection_at"] >= commissioned["max_age_s"]:
                reason = "clearance_inspection_expired_or_future"
            else:
                targets = [next((h for h in hazards.values() if h["hazard_ref"] == ref), None) for ref in value["hazard_refs"]]
                if any(h is None for h in targets):
                    reason = "clearance_hazard_ref_stale"
                elif any(value["inspection_at"] <= (h["observed_through"] if h["observed_through"] is not None else h["created_at"]) for h in targets):
                    reason = "clearance_inspection_not_newer"
                elif any(contradictory(h) for h in targets):
                    reason = "clearance_contradicted_by_geometry"
            if len(settlements) >= 128:
                reject("invalid_delivery_hazards", "clearance settlement exhaustion refuses")
            if reason is None:
                for hazard in targets:
                    resolve(hazard, refs[intent["id"]], "operator_clearance", value["inspection_at"])
            result = {"id": intent["id"], "status": "rejected" if reason else "applied", "reason": reason or "explicit_hazards_cleared"}
            settlements.append({**result, "digest": digest(intent), "intent_ref": refs[intent["id"]], "decision_at": t, "hazard_refs": value["hazard_refs"]})
            results.append(result)
        expected = list(hazards.values())
        people = [h for h in expected if h["kind"] == "known_person"]
        public = {"schema": "home-live-person-hazards-1", "version": 1, "items": expected, "resolutions": resolutions}
        if (state["hazards"] != expected or state["hazard_resolutions"] != resolutions or state["clearance_settlements"] != settlements
                or state["known_persons"] != [h["person_ref"] for h in people]
                or state["person_observed_through"] != {digest(h["person_ref"]): h["observed_through"] for h in people}
                or state["unresolved_person"] is not any(h["kind"] != "known_person" for h in expected)):
            reject("invalid_delivery_hazards", "hazards, anchors and immutable settlements must independently follow the exact causal input cut")
        if decision is not None and (decision["person_hazards"] != public or any(r not in [{k: x[k] for k in ("id", "status", "reason")} for x in decision["intent_results"]] for r in results)):
            reject("invalid_delivery_hazards", "public hazard projection and command results must match independent authority")
        return {canonical(h["person_ref"]) for h in people}, any(h["kind"] != "known_person" for h in expected)

    def _delivery(self, commit, publications, decision, old):
        state = commit["checkpoint"]["state"]["virtual_execution"]["state"]
        if state["plan"] is None and state["brightness_transition"] is not None:
            reject("invalid_brightness_transition", "withdrawn motion cannot retain a brightness segment")
        self._hazards(state, publications, old, decision["t"], decision=decision)
        return super()._delivery(commit, publications, decision, old)


CONSERVATIVE_DELIVERY_SCHEMAS = frozenset({'home-live-shadow-decision-6', 'home-live-worker-checkpoint-6',
    'home-live-worker-commit-6', 'home-live-input-cut-6', 'home-live-intent-event-2', 'home-live-person-lifecycle-1'})
CONSERVATIVE_DELIVERY_OUTPUTS = frozenset({'home-live-engagement-state-1', 'home-live-shadow-decision-6'})


def _validate_conservative_domains(policy):
    """An authored virtual boundary is separate from useful optical range.

    The AABB contains a superset of every modeled emitted cone in this room.
    Room collision completeness is never person-observation qualification.
    """
    emission, observation = policy['modeled_emission'], policy['observation_coverage']
    _exact(emission, {'assessment_ref', 'range_m', 'valid_until'})
    _ref(emission['assessment_ref'])
    if not finite(emission['range_m']) or not policy['fixture']['optics']['max_range_m'] <= emission['range_m'] <= 100:
        raise ValueError('assessed virtual emission boundary must contain useful work range')
    _nonnegative(emission['valid_until'])
    _exact(observation, {'assessment_ref', 'method_ref', 'qualification_ref', 'applicability_ref', 'bounds', 'valid_until'})
    for name in ('assessment_ref', 'method_ref', 'qualification_ref', 'applicability_ref'):
        _ref(observation[name])
    _nonnegative(observation['valid_until'])
    _exact(observation['bounds'], {'kind', 'center_m', 'half_extents_m'})
    _shape(observation['bounds'])
    if observation['bounds']['kind'] != 'aabb':
        raise ValueError('person-observation coverage requires registered AABB bounds')
    room, origin = policy['room']['usable_bounds'], policy['fixture']['origin_m']
    for i in range(3):
        room_min, room_max = room['center_m'][i]-room['half_extents_m'][i], room['center_m'][i]+room['half_extents_m'][i]
        if not room_min <= origin[i] <= room_max:
            raise ValueError('fixture must be inside assessed story room')
        domain_min, domain_max = max(room_min,origin[i]-emission['range_m']), min(room_max,origin[i]+emission['range_m'])
        if (observation['bounds']['center_m'][i]-observation['bounds']['half_extents_m'][i] > domain_min + EPS
                or observation['bounds']['center_m'][i]+observation['bounds']['half_extents_m'][i] < domain_max - EPS):
            raise ValueError('modeled emission outside qualified person-observation coverage')


def _validate_conservative_surface(policy):
    """Match policy4's closed JS commissioning domain; old readers stay exact."""
    def opaque(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}',value):
            raise ValueError('opaque delivery identifier of at most 160 characters required')
    def visit(value):
        if isinstance(value,dict):
            if set(value)=={'id','digest'}:
                opaque(value['id']);_ref(value)
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(policy)
    registration=policy['registration']
    opaque(registration['registration_ref']);opaque(registration['geometry_revision'])
    _array(registration['coverage_refs'],32,nonempty=True)
    _array(registration['transform_chain_refs'],16,nonempty=True)
    for value in registration['coverage_refs']:opaque(value)
    opaque(policy['fixture']['id'])
    for value in policy['required_body_parts']:opaque(value)
    for obstacle in policy['room']['occluders']:opaque(obstacle['id'])
    for shape in [policy['room']['usable_bounds'],*(row['shape'] for row in policy['room']['occluders'])]:
        _exact(shape,{'kind','center_m','half_extents_m'} if shape.get('kind')=='aabb' else {'kind','a_m','b_m','radius_m'})
        if shape.get('kind') not in {'aabb','capsule'}:raise ValueError('closed conservative room primitive required')


def validate_conservative_delivery_worker_policy(manifest, registry):
    worker, policy = manifest['worker'], manifest['worker']['delivery_policy']
    if (not registry.conservative_delivery_interface_digest
            or manifest.get('conservative_delivery_interface_digest') != registry.conservative_delivery_interface_digest
            or worker.get('conservative_delivery_schema_pins') != registry.conservative_delivery_schema_pins):
        raise ValueError('exact conservative delivery interface and schema pins required')
    if policy.get('schema') != 'home-live-task-delivery-policy-4' or type(policy.get('version')) is not int or policy['version'] != 4:
        raise ValueError('explicit conservative delivery policy4 required')
    _validate_conservative_domains(policy)
    _validate_conservative_surface(policy)
    base = copy.deepcopy(manifest)
    del base['worker']['conservative_delivery_schema_pins']
    base['worker']['delivery_policy'].update(schema='home-live-task-delivery-policy-3', version=3)
    del base['worker']['delivery_policy']['modeled_emission']
    del base['worker']['delivery_policy']['observation_coverage']
    validate_lifecycle_delivery_worker_policy(base, registry)


def validate_conservative_delivery_grant(grant, config):
    if grant.payload_types & (CONSERVATIVE_DELIVERY_SCHEMAS - CONSERVATIVE_DELIVERY_OUTPUTS - {'home-live-intent-event-2','home-live-person-lifecycle-1'}) and 'worker_commit' not in grant.operations:
        raise ValueError('private conservative delivery state requires worker capability')
    if 'home-live-shadow-decision-6' in grant.payload_types and 'home-live-geometry-1' not in grant.payload_types:
        raise ValueError('complete conservative delivery decision requires separate geometry authority')


def validate_conservative_delivery_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not CONSERVATIVE_DELIVERY_SCHEMAS <= grant.payload_types:
        raise ValueError('conservative delivery worker requires complete contract closure')


class ConservativeLiveDeliveryWorkerBoundary(LifecycleLiveDeliveryWorkerBoundary):
    """Config7 only: exact capture typing and independently bounded virtual scope."""
    schema_types = LiveDeliveryWorkerBoundary.schema_types | CONSERVATIVE_DELIVERY_SCHEMAS
    output_types = CONSERVATIVE_DELIVERY_OUTPUTS
    decision_type = 'home-live-shadow-decision-6'
    checkpoint_type = 'home-live-worker-checkpoint-6'
    commit_type = 'home-live-worker-commit-6'
    cut_type = 'home-live-input-cut-6'
    version = 6
    snapshot_version = CONSERVATIVE_VERSION
    scene_state_version = 'home-live-scene-state-3.0.0'
    worker_version = 'home-live-context-worker-6.0.0'
    conservative_delivery = True

    def configuration_description(self, nested):
        result = super().configuration_description(nested)
        result.update(conservative_delivery_schema_pins=self.policy['conservative_delivery_schema_pins'],
                      conservative_delivery_interface_digest=self.j.schemas.conservative_delivery_interface_digest)
        return result

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if cp['conservative_delivery_schema_pins'] != self.policy['conservative_delivery_schema_pins']:
            reject('checkpoint_conflict', 'conservative delivery pins must match commissioned configuration')
        return super()._checkpoint(cp, commit, next_seq, old, now)

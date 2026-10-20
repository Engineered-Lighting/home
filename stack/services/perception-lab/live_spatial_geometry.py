"""Bounded, single-pose numeric geometry from an already qualified capture.

This module neither acquires data nor assigns identity or engagement. Policy
values are commissioned inputs, not accuracy inferred from a pose response.
RGB joints are lifted through the registered stereo projection; RGB pixels are
never used as rectified-left depth indices. Results and joint summaries remain
local until the spatial producer adds the original evidence and time lineage.
"""
import math

import numpy as np

from depth_association import camera_model
from head3d import DepthLifter
from room_facts import to_room


PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection',
         'body_forward', 'head_forward')
BODY_SEGMENTS = (
    ('upper_arm_left', 5, 7), ('forearm_left', 7, 9),
    ('upper_arm_right', 6, 8), ('forearm_right', 8, 10),
    ('thigh_left', 11, 13), ('shin_left', 13, 15),
    ('thigh_right', 12, 14), ('shin_right', 14, 16),
)
BODY_IDS = frozenset(('torso',) + tuple(item[0] for item in BODY_SEGMENTS))


def _finite(values):
    return all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in values)


def pairing_skew_s(numeric):
    """Upper bound on RGB/depth relative lag, retaining the common anchor.

    UTC anchor uncertainty is already in the capture clock. It is correlated
    between these two exposures; the independent drift/quantization/semantics
    terms still enlarge the metric association interval.
    """
    originals = numeric['original_receipts']
    rgb = next(row['rgb_receipt'] for row in originals if row['component_id'] == 'rgb')
    depth = next(row['depth_clock_mapping'] for row in originals if row['component_id'] == 'depth')
    delta = abs(numeric['source_metadata']['frames']['depth']['device_timestamp_seconds']
                - rgb['frame_device_ns'] / 1_000_000_000)
    relative = sum(value for key, value in depth['uncertainty_components_s'].items() if key != 'anchor_s')
    # Reuse of an original depth component retains its original mapped time.
    # Include any original-map displacement instead of refreshing that root.
    mapped_delta = abs(numeric['capture']['observed_at'] - numeric['capture']['depth_observed_at'])
    return math.nextafter(max(delta, mapped_delta) + relative, math.inf)


def pairing_budget_ok(numeric, bound):
    if bound is None or bound.get('model') != 'conservative_additive_radius-1':
        return False
    try:
        lag = pairing_skew_s(numeric)
        age = bound['age']
        displacement = age['max_speed_m_s'] * lag + .5 * age['max_accel_m_s2'] * lag * lag
        return math.isfinite(displacement) and bound['components_m']['capture_association'] >= displacement
    except (KeyError, TypeError, ValueError, StopIteration):
        return False  # Missing original pairing support never grants geometry.


def _capsule(a, b, radius, part_id=None):
    shape = {'kind': 'capsule', 'a_m': list(a), 'b_m': list(b), 'radius_m': radius}
    if part_id is not None:
        shape['part_id'] = part_id
    return shape


def _aabb(center, half):
    return {'kind': 'aabb', 'center_m': list(center), 'half_extents_m': list(half)}


def _segment_distance(point, a, b):
    point, a, b = (np.asarray(value) for value in (point, a, b))
    axis = b - a
    length_squared = float(np.dot(axis, axis))
    along = float(np.dot(point - a, axis)) / length_squared if length_squared else 0
    closest = a + max(0., min(1., along)) * axis
    return float(np.linalg.norm(point - closest))


def _pose_joints(pose, width, height):
    """Refuse duplicate/malformed slots rather than retaining a convenient one."""
    if not isinstance(pose, dict) or pose.get('layout') != 'coco17':
        return None
    entries = pose.get('joints')
    if not isinstance(entries, list) or len(entries) > 17:
        return None
    selected, seen = {}, set()
    for joint in entries:
        if not isinstance(joint, dict):
            return None
        joint_id = joint.get('joint_id')
        if type(joint_id) is not int or not 0 <= joint_id < 17 or joint_id in seen:
            return None
        seen.add(joint_id)
        if joint.get('status') != 'supported':
            continue
        pixel = joint.get('pixel')
        if (not isinstance(pixel, (list, tuple)) or len(pixel) != 2
                or not _finite(pixel) or not 0 <= pixel[0] < width
                or not 0 <= pixel[1] < height):
            return None
        selected[joint_id] = pixel
    return selected


def estimate_geometry(numeric, pose_instance, policy, source):
    """Return coarse shapes and local support summaries, never activity truth.

    The caller validates the closed commissioned policy and capture/pose
    association before invoking this function. Missing required joints or
    measurement bounds withhold a whole part. No missing anatomy is silently
    represented by an empty obstacle list.
    """
    unavailable = lambda reason: {'reason': reason}
    parts = {name: unavailable('pose_unavailable') for name in PARTS}
    summaries = [dict(joint_id=joint_id, status='unobservable', xyz_m=None,
                      support=0, spread_m=None, reason='pose_unavailable')
                 for joint_id in range(17)]
    result = {'parts': parts, 'joints': summaries, 'anchor_m': None}
    model = camera_model(numeric['source_metadata'], numeric['projection'],
                         intrinsics_tolerance_px=numeric['intrinsics_tolerance_px'])
    width, height = model['rgb_size']
    joints = _pose_joints(pose_instance, width, height)
    box = pose_instance.get('crop_xyxy') if isinstance(pose_instance, dict) else None
    if (joints is None or not isinstance(box, (list, tuple)) or len(box) != 4
            or not _finite(box) or not 0 <= box[0] < box[2] <= width
            or not 0 <= box[1] < box[3] <= height):
        return result
    lifter = DepthLifter(numeric['depth_mm'], model, rgb_roi=box)
    lifted = {}
    sampling = policy['sampling']
    matrix = numeric['registration']['room_from_rectified_left']
    for joint_id, pixel in joints.items():
        sample = lifter.lift(*pixel, radius_px=sampling['radius_px'],
                             min_support=sampling['min_support'])
        summary = summaries[joint_id]
        summary['support'] = sample['support']
        if sample['status'] != 'measured':
            summary['reason'] = 'insufficient_depth_support'
            continue
        summary['spread_m'] = sample['spread_m']
        if not _finite([sample['spread_m']]) or sample['spread_m'] > sampling['max_spread_m']:
            summary['reason'] = 'depth_spread_exceeded'
            continue
        point = [float(value) for value in to_room(matrix, sample['xyz'])]
        if not _finite(point):
            summary['reason'] = 'insufficient_depth_support'
            continue
        summary.update(status='measured', xyz_m=point, reason=None)
        lifted[joint_id] = point

    torso_points = [lifted[joint_id] for joint_id in (5, 6, 11, 12) if joint_id in lifted]
    if torso_points:
        result['anchor_m'] = np.median(np.asarray(torso_points), axis=0).tolist()

    def allowed(name, required):
        """Require both supported samples and their commissioned error budget."""
        bound = source['part_policies'][name]
        if bound is None:
            parts[name] = unavailable('missing_measurement_bound')
            return False
        if any(joint_id not in lifted for joint_id in required):
            parts[name] = unavailable('pose_unavailable')
            return False
        spread = max(summaries[joint_id]['spread_m'] for joint_id in required)
        components = bound['components_m']
        if (components['depth'] < spread + numeric['projection']['error_bound_m']
                or components['registration'] < numeric['registration']['error_bound_m']
                or not pairing_budget_ok(numeric, bound)):
            parts[name] = unavailable('missing_measurement_bound')
            return False
        return True

    anatomy = policy['anatomy']
    if allowed('work', (9, 10)):
        left, right = np.asarray(lifted[9]), np.asarray(lifted[10])
        parts['work'] = {'shape': _aabb(((left + right) / 2).tolist(),
                                      (np.abs(left - right) / 2 + policy['work_margin_m']).tolist())}
    if allowed('body', tuple(range(5, 17))):
        if set(source['primitive_ids']['body']) != BODY_IDS:
            parts['body'] = unavailable('missing_measurement_bound')
        else:
            shoulder = ((np.asarray(lifted[5]) + lifted[6]) / 2).tolist()
            hip = ((np.asarray(lifted[11]) + lifted[12]) / 2).tolist()
            if any(_segment_distance(lifted[joint_id], shoulder, hip) > anatomy['torso_radius_m']
                   for joint_id in (5, 6, 11, 12)):
                parts['body'] = unavailable('missing_measurement_bound')
            else:
                items = [_capsule(shoulder, hip, anatomy['torso_radius_m'], 'torso')]
                items.extend(_capsule(lifted[a], lifted[b], anatomy['limb_radius_m'], name)
                             for name, a, b in BODY_SEGMENTS)
                parts['body'] = {'shape': {'kind': 'opaque_primitives', 'items': items}}
    for name, joint_id in (('hand_left', 9), ('hand_right', 10)):
        if allowed(name, (joint_id,)):
            parts[name] = {'shape': _capsule(lifted[joint_id], lifted[joint_id], anatomy['hand_radius_m'])}

    face = tuple(range(5))

    def head_enclosure():
        points = np.asarray([lifted[joint_id] for joint_id in face])
        center = np.mean(points, axis=0)
        half = np.asarray(anatomy['head_half_extents_m'])
        # A declared enclosure that excludes its own observed anchors is not a
        # qualified fallback. Neither shrink nor quietly enlarge that policy.
        return center.tolist(), bool(np.all(np.abs(points - center) <= half))

    if allowed('head', face):
        center, encloses = head_enclosure()
        parts['head'] = ({'shape': _aabb(center, anatomy['head_half_extents_m'])} if encloses
                         else unavailable('missing_measurement_bound'))
    if allowed('protection', face):
        center, encloses = head_enclosure()
        if (set(source['primitive_ids']['protection']) != {'head_exclusion'}
                or not source['protection_policy_refs'] or not encloses):
            parts['protection'] = unavailable('missing_measurement_bound')
        else:
            half = (np.asarray(anatomy['head_half_extents_m']) + anatomy['protection_margin_m']).tolist()
            item = dict(_aabb(center, half), part_id='head_exclusion')
            parts['protection'] = {'shape': {'kind': 'protected_primitives',
                                           'policy_ref': dict(source['protection_policy_refs'][0]), 'items': [item]}}

    # Shoulder orientation defines an unoriented horizontal body axis only.
    # Its sign cannot prove which side is the person's front.
    parts['body_forward'] = unavailable('body_direction_unavailable')
    direction = source['part_policies']['body_forward']
    body_bound = source['part_policies']['body']
    if direction is None or body_bound is None:
        parts['body_forward'] = unavailable('missing_measurement_bound')
    elif all(joint_id in lifted for joint_id in (5, 6)):
        spread = max(summaries[joint_id]['spread_m'] for joint_id in (5, 6))
        components = body_bound['components_m']
        transverse = np.asarray(lifted[6]) - lifted[5]
        transverse[2] = 0
        baseline = float(np.linalg.norm(transverse))
        positional_error = body_bound['bound_at_observation_m']
        ratio = 2 * positional_error / baseline if baseline else math.inf
        if (components['depth'] >= spread + numeric['projection']['error_bound_m']
                and components['registration'] >= numeric['registration']['error_bound_m']
                and pairing_budget_ok(numeric, body_bound)
                and baseline >= anatomy['min_shoulder_baseline_m']
                and ratio < 1 and math.asin(ratio) <= direction['angular_error_rad']):
            normal = np.cross(transverse / baseline, np.asarray([0., 0., 1.]))
            parts['body_forward'] = {'shape': {'kind': 'direction', 'unit_vector': normal.tolist(),
                                               'front_back': 'ambiguous'}}
    parts['head_forward'] = unavailable('missing_measurement_bound' if source['part_policies']['head_forward'] is None
                                        else 'head_direction_unvalidated')
    return result


def candidate_geometry(estimate, policy, source):
    """Unqualified local shape hypotheses from already lifted numeric samples.

    This is deliberately separate from ``parts`` admission above. It does not
    manufacture an uncertainty bound, person association or supplied wire part.
    Missing samples remain missing; measured calibration remains mandatory.
    """
    output = {name: {'status': 'unobservable', 'reason': 'pose_unavailable'} for name in PARTS}
    joints = {j['joint_id']: np.asarray(j['xyz_m']) for j in estimate['joints'] if j['status'] == 'measured'}
    anatomy = policy['anatomy']
    def put(name, shape):
        output[name] = {'status': 'candidate', 'shape': shape, 'qualification': 'not_assessed'}
    if all(i in joints for i in (9, 10)):
        a, b = joints[9], joints[10]
        put('work', _aabb(((a + b) / 2).tolist(), (np.abs(a - b) / 2 + policy['work_margin_m']).tolist()))
    if all(i in joints for i in range(5, 17)):
        shoulder, hip = (joints[5] + joints[6]) / 2, (joints[11] + joints[12]) / 2
        items = [_capsule(shoulder, hip, anatomy['torso_radius_m'], 'torso')]
        items.extend(_capsule(joints[a], joints[b], anatomy['limb_radius_m'], name) for name, a, b in BODY_SEGMENTS)
        put('body', {'kind': 'opaque_primitives', 'items': items})
    for name, i in (('hand_left', 9), ('hand_right', 10)):
        if i in joints:
            put(name, _capsule(joints[i], joints[i], anatomy['hand_radius_m']))
    if all(i in joints for i in range(5)):
        center = np.mean([joints[i] for i in range(5)], axis=0).tolist()
        put('head', _aabb(center, anatomy['head_half_extents_m']))
        half = (np.asarray(anatomy['head_half_extents_m']) + anatomy['protection_margin_m']).tolist()
        put('protection', {'kind': 'protected_primitives', 'items': [dict(_aabb(center, half), part_id='head_exclusion')]})
    # No unvalidated directed orientation is created even for assessment.
    output['body_forward'] = {'status': 'unobservable', 'reason': 'front_back_ambiguous'}
    output['head_forward'] = {'status': 'unobservable', 'reason': 'head_direction_unvalidated'}
    return output

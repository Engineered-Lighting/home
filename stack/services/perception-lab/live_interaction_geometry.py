"""Registered per-observation material/hand proposals, never activity truth.

Two measured wrists on opposing edges of a detected material, with foreground
depth between them, support an *experimental holding-compatible* proposal.
The same arrangement can be hands adjacent to an unused object. Detector boxes
can include hands or background; this method does not prove physical contact,
manipulation, open pages, attention or task engagement. No history, identity,
calibrated probabilities, camera access or model execution lives here.
"""
from copy import deepcopy
import math

import numpy as np

from depth_association import camera_model, project
from head3d import DepthLifter, foreground_layer
from room_facts import to_room


METHOD = 'registered_depth_two_hand_enclosure-1'
POLICY_KEYS = frozenset({
    'labels', 'min_detector_score', 'max_materials', 'min_object_support',
    'max_object_spread_m', 'max_wrist_spread_m', 'max_hand_object_distance_m',
    'max_near_distance_m', 'min_hand_span_m', 'edge_band_fraction',
    'rgb_margin_px', 'min_between_fraction',
})
APPROVED_LABELS = {'book': 'book', 'cell phone': 'phone', 'phone': 'phone'}
LIMITATIONS = ['detector_box_can_include_background_or_hands',
               'geometric_enclosure_does_not_prove_contact_or_use']


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_material_policy(policy):
    """Require explicit commissioned thresholds and a closed material vocabulary."""
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError('closed material geometry policy required')
    labels = policy['labels']
    if (not isinstance(labels, dict) or not labels
            or any(label not in APPROVED_LABELS or APPROVED_LABELS[label] != category
                   for label, category in labels.items())):
        raise ValueError('explicit approved book and phone detector labels required')
    if (type(policy['max_materials']) is not int or not 1 <= policy['max_materials'] <= 8
            or type(policy['min_object_support']) is not int or not 3 <= policy['min_object_support'] <= 4096
            or not _finite(policy['min_detector_score']) or not 0 <= policy['min_detector_score'] <= 1):
        raise ValueError('bounded material candidates and model score threshold required')
    for key, maximum in (('max_object_spread_m', 2), ('max_wrist_spread_m', .5),
                         ('max_hand_object_distance_m', .5), ('max_near_distance_m', 1),
                         ('min_hand_span_m', 1), ('rgb_margin_px', 32)):
        if not _finite(policy[key]) or not 0 < policy[key] <= maximum:
            raise ValueError('positive bounded material geometry thresholds required')
    if (policy['max_hand_object_distance_m'] >= policy['max_near_distance_m']
            or not _finite(policy['edge_band_fraction']) or not 0 < policy['edge_band_fraction'] < .5
            or not _finite(policy['min_between_fraction']) or not .5 <= policy['min_between_fraction'] <= 1):
        raise ValueError('holding enclosure must be stricter than proximity')


def _empty(reason):
    return {'status': 'unobservable', 'reason': reason, 'method': METHOD,
            'qualification': 'experimental', 'materials': [], 'relations': [],
            'alternatives': [], 'limitations': list(LIMITATIONS)}


def _inputs(detector, original, width, height, policy):
    """Keep source-frame identities and original pose association exact."""
    if (not isinstance(detector, dict) or detector.get('schema_version') != 1
            or detector.get('coordinate_space') != 'source_pixels'
            or not isinstance(detector.get('frame_id'), str)
            or not isinstance(original, dict)
            or original.get('detector', {}).get('result') != detector):
        return None, None, 'material_capture_mismatch'
    pose = original.get('pose', {}).get('result', {})
    if pose.get('frame_id') != detector['frame_id']:
        return None, None, 'material_capture_mismatch'
    instances = pose.get('pose_instances')
    if not isinstance(instances, list) or len(instances) != 1:
        return None, None, 'material_person_association_unavailable'
    instance = instances[0]
    if not isinstance(instance, dict) or instance.get('layout') != 'coco17':
        return None, None, 'material_pose_unavailable'
    boxes = detector.get('boxes')
    if not isinstance(boxes, list) or len(boxes) > 128:
        return None, None, 'material_result_invalid'
    people = [box for box in boxes if isinstance(box, dict) and box.get('label') == 'person']
    if (len(people) != 1 or instance.get('instance_id') != people[0].get('instance_id')
            or instance.get('crop_xyxy') != [people[0].get('x'), people[0].get('y'),
                    people[0].get('x', 0) + people[0].get('w', 0),
                    people[0].get('y', 0) + people[0].get('h', 0)]):
        return None, None, 'material_person_association_unavailable'
    materials, ids = [], set()
    for box in boxes:
        if not isinstance(box, dict):
            return None, None, 'material_result_invalid'
        if box.get('label') not in policy['labels']:
            continue
        if (not _finite(box.get('confidence')) or not 0 <= box['confidence'] <= 1
                or not all(_finite(box.get(key)) for key in ('x', 'y', 'w', 'h'))
                or not 0 <= box['x'] < box['x'] + box['w'] <= width
                or not 0 <= box['y'] < box['y'] + box['h'] <= height
                or not isinstance(box.get('instance_id'), str) or not 1 <= len(box['instance_id']) <= 128
                or box['instance_id'] in ids):
            return None, None, 'material_result_invalid'
        ids.add(box['instance_id'])
        if box['confidence'] >= policy['min_detector_score']:
            materials.append(box)
    if len(materials) > policy['max_materials']:
        return None, None, 'material_candidate_budget_exceeded'
    return materials, instance, None


def _wrists(original, instance, source, policy, width, height, numeric, *, candidate_only=False):
    output = {name: {'status': 'unobservable', 'reason': 'wrist_depth_unavailable'}
              for name in ('left', 'right')}
    summaries = original.get('joints')
    joints = instance.get('joints')
    if not isinstance(summaries, list) or not isinstance(joints, list):
        return output
    # Duplicates and malformed entries never become a convenient last value.
    summary_map, pixel_map = {}, {}
    for item in summaries:
        if not isinstance(item, dict) or type(item.get('joint_id')) is not int or item['joint_id'] in summary_map:
            return output
        summary_map[item['joint_id']] = item
    for item in joints:
        if not isinstance(item, dict) or type(item.get('joint_id')) is not int or item['joint_id'] in pixel_map:
            return output
        pixel_map[item['joint_id']] = item
    for side, joint_id in (('left', 9), ('right', 10)):
        measured, joint = summary_map.get(joint_id, {}), pixel_map.get(joint_id, {})
        point, pixel = measured.get('xyz_m'), joint.get('pixel')
        bound = source.get('part_policies', {}).get('hand_' + side)
        if not candidate_only and not isinstance(bound, dict):
            output[side]['reason'] = 'wrist_measurement_bound_unavailable'
            continue
        if (measured.get('status') != 'measured' or joint.get('status') != 'supported'
                or not isinstance(point, list) or len(point) != 3 or not all(_finite(v) for v in point)
                or not isinstance(pixel, (list, tuple)) or len(pixel) != 2 or not all(_finite(v) for v in pixel)
                or not 0 <= pixel[0] < width or not 0 <= pixel[1] < height
                or type(measured.get('support')) is not int or measured['support'] < 3
                or not _finite(measured.get('spread_m')) or not 0 <= measured['spread_m'] <= policy['max_wrist_spread_m']):
            continue
        components = bound.get('components_m', {}) if isinstance(bound, dict) else {}
        if not candidate_only and (not _finite(components.get('depth')) or not _finite(components.get('registration'))
                or components['depth'] < measured['spread_m'] + numeric['projection']['error_bound_m']
                or components['registration'] < numeric['registration']['error_bound_m']):
            output[side]['reason'] = 'wrist_measurement_bound_unavailable'
            continue
        output[side] = {'status': 'measured', 'reason': None, 'xyz_m': deepcopy(point),
                        'pixel': list(pixel), 'support': measured['support'], 'spread_m': measured['spread_m']}
    return output


def _object_geometry(box, lifter, model, numeric, policy):
    xyxy = [box['x'], box['y'], box['x'] + box['w'], box['y'] + box['h']]
    points = lifter.select(box=xyxy)
    unavailable = lambda reason, support: ({'status': 'unobservable', 'reason': reason,
                                           'support': support, 'spread_m': None, 'center_m': None}, None, None)
    if len(points) < policy['min_object_support']:
        return unavailable('material_depth_unavailable', int(len(points)))
    points = foreground_layer(points)
    if len(points) < policy['min_object_support']:
        return unavailable('material_depth_unavailable', int(len(points)))
    center = np.median(points, axis=0)
    spread = float(np.max(np.linalg.norm(points - center, axis=1)))
    if not math.isfinite(spread) or spread > policy['max_object_spread_m']:
        return unavailable('material_depth_spread_exceeded', int(len(points)))
    matrix = numeric['registration']['room_from_rectified_left']
    room_points = np.asarray(points) @ np.asarray(matrix)[:3, :3].T + np.asarray(matrix)[:3, 3]
    transform = model['rgb_from_left']
    uv, ok = project(points @ transform[:3, :3].T + transform[:3, 3], model['rgb_k'], model['rgb_distortion'])
    if not bool(np.all(ok)) or not bool(np.all(np.isfinite(room_points))):
        return unavailable('material_depth_unavailable', int(len(points)))
    return ({'status': 'measured', 'reason': None, 'support': int(len(points)), 'spread_m': spread,
             'center_m': [float(value) for value in to_room(matrix, center)]}, room_points, uv)


def _relation(box, depth, room_points, uv, wrists, policy):
    relation = {'material_ref': box['instance_id'], 'category': policy['labels'][box['label']],
                'status': 'unobservable', 'reason': 'material_depth_unavailable', 'holding_proposal': False,
                'hand_distances_m': {'left': None, 'right': None}, 'enclosure': None,
                'alternatives': ['material_visible_without_supported_interaction']}
    if depth['status'] != 'measured':
        relation['reason'] = depth['reason']
        return relation
    measured = {side: hand for side, hand in wrists.items() if hand['status'] == 'measured'}
    if not measured:
        relation['reason'] = 'wrist_depth_unavailable'
        return relation
    distances = {side: float(np.min(np.linalg.norm(room_points - np.asarray(hand['xyz_m']), axis=1)))
                 for side, hand in measured.items()}
    relation['hand_distances_m'].update(distances)
    near = any(distance <= policy['max_near_distance_m'] for distance in distances.values())
    relation.update(status='near' if near else 'visible_only', reason='proximity_only' if near else 'material_not_near_hands')
    if not near:
        return relation
    relation['alternatives'] = ['hand_near_unused_material', 'passing_material', 'unsupported_holding']
    if len(measured) != 2:
        relation['reason'] = 'two_hand_support_unavailable'
        return relation
    left, right = (measured[side] for side in ('left', 'right'))
    pixels = np.asarray([left['pixel'], right['pixel']])
    delta = np.abs(pixels[1] - pixels[0])
    axis = int(np.argmax(delta))
    lower = [box['x'], box['y']]
    extent = [box['w'], box['h']]
    upper = [lower[index] + extent[index] for index in range(2)]
    margin = policy['rgb_margin_px']
    inside = bool(np.all(pixels >= np.asarray(lower) - margin) and np.all(pixels <= np.asarray(upper) + margin))
    ordered = sorted(float(pixel[axis]) for pixel in pixels)
    edge = extent[axis] * policy['edge_band_fraction']
    opposite = ordered[0] <= lower[axis] + edge and ordered[1] >= upper[axis] - edge
    span = math.dist(left['xyz_m'], right['xyz_m'])
    between = float(np.mean((uv[:, axis] >= ordered[0]) & (uv[:, axis] <= ordered[1])))
    # This bounded arrangement is stronger than proximity. It still retains
    # adjacent/unused alternatives instead of claiming physical contact.
    close = all(distance + measured[side]['spread_m'] <= policy['max_hand_object_distance_m']
                for side, distance in distances.items())
    relation['enclosure'] = {'axis': 'x' if axis == 0 else 'y', 'both_hands_in_expanded_box': inside,
                             'opposing_edges': opposite, 'hand_span_m': span,
                             'material_between_fraction': between, 'both_hands_depth_near': close}
    if inside and opposite and close and span >= policy['min_hand_span_m'] and between >= policy['min_between_fraction']:
        relation.update(status='holding_compatible', reason='two_hand_depth_enclosure', holding_proposal=True,
                        alternatives=['holding_material', 'hands_adjacent_to_material', 'unused_material_between_hands'])
    else:
        relation['reason'] = 'two_hand_enclosure_not_supported'
    return relation


def estimate_interaction(numeric, detector_result, spatial_original, policy, source, *, candidate_only=False):
    """Estimate bounded relations using only exact-frame owner-supplied inputs.

    ``spatial_original`` is #204's local original receipt. Its measured joints
    and exact detector/pose results remain separate from the returned material
    observations. Caller owns authorization, clock/freshness and provenance.
    Invalid input yields an explicit abstention, never a negative assertion.
    """
    material_policy = policy.get('material_geometry') if isinstance(policy, dict) else None
    validate_material_policy(material_policy)
    try:
        model = camera_model(numeric['source_metadata'], numeric['projection'],
                             intrinsics_tolerance_px=numeric['intrinsics_tolerance_px'])
        width, height = model['rgb_size']
        if (width, height) != (640, 360) or model['depth_size'] != (640, 400):
            return _empty('material_capture_mismatch')
        boxes, instance, reason = _inputs(detector_result, spatial_original, width, height, material_policy)
        if reason:
            return _empty(reason)
        result = _empty('no_material_detected')
        if not boxes:
            return result
        wrists = _wrists(spatial_original, instance, source, material_policy, width, height, numeric,
                         candidate_only=candidate_only)
        roi = [min(box['x'] for box in boxes), min(box['y'] for box in boxes),
               max(box['x'] + box['w'] for box in boxes), max(box['y'] + box['h'] for box in boxes)]
        lifter = DepthLifter(numeric['depth_mm'], model, rgb_roi=roi)
        for box in boxes:
            depth, room_points, uv = _object_geometry(box, lifter, model, numeric, material_policy)
            result['materials'].append({'material_ref': box['instance_id'], 'category': material_policy['labels'][box['label']],
                'detector_label': box['label'], 'detector_score': box['confidence'],
                'score_role': 'uncalibrated_model_score', 'box_xyxy': [box['x'], box['y'], box['x'] + box['w'], box['y'] + box['h']],
                'depth': depth, 'limitations': list(LIMITATIONS)})
            result['relations'].append(_relation(box, depth, room_points, uv, wrists, material_policy))
        candidates = [relation for relation in result['relations'] if relation['status'] in {'holding_compatible', 'near'}]
        if len(candidates) > 1:
            result.update(status='conflicted', reason='material_association_ambiguous')
            result['alternatives'] = [{'material_ref': relation['material_ref'], 'category': relation['category'],
                                       'proposed_relation': relation['status']} for relation in candidates]
            for relation in candidates:
                relation.update(status='conflicted', reason='material_association_ambiguous', holding_proposal=False)
        elif any(relation['holding_proposal'] for relation in result['relations']):
            result.update(status='supported', reason='experimental_holding_enclosure')
        else:
            result.update(status='unsupported', reason='material_visible_without_supported_interaction')
        if candidate_only:
            result['qualification'] = 'candidate_only_not_assessed'
        return result
    except (KeyError, ValueError, TypeError, IndexError, AttributeError, OverflowError):
        return _empty('material_input_unavailable')

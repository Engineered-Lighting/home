"""Fictional spatial commissioning and external SDK observations, never home data."""
from copy import deepcopy
import io

import numpy as np

import live_capture_fixtures as capture
from journal import digest


JOINTS = [[320, 70], [310, 65], [330, 65], [300, 70], [340, 70],
          [280, 110], [360, 110], [260, 160], [380, 160],
          [300, 200], [340, 200], [290, 220], [350, 220],
          [290, 270], [350, 270], [290, 320], [350, 320]]
BODY_IDS = ['torso', 'upper_arm_left', 'forearm_left', 'upper_arm_right', 'forearm_right',
            'thigh_left', 'shin_left', 'thigh_right', 'shin_right']
WORK_CENTER = [1.0, 3.234, 1.45064]
WORK_EXTENTS = [.14936, .1, .1]


def bundle_with_missing_wrist_depth(metadata, *, value=1234):
    """Blank local wrist neighborhoods while preserving the coarse mount plane."""
    encoded = capture.bundle(metadata, value=value)
    with np.load(io.BytesIO(encoded), allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    for u, v in ((302, 218), (338, 218)):
        arrays['depth_mm'][v - 10:v + 11, u - 10:u + 11] = 0
    output = io.BytesIO()
    np.savez_compressed(output, **arrays)
    return output.getvalue()


def commission(live_config, capture_policy, capture_method, grant, model_refs):
    """Pin independent literal test bounds, not bounds fitted from predictions."""
    config, policy, method, grant = map(deepcopy, (live_config, capture_policy, capture_method, grant))
    policy['projection']['rgb_from_rectified_left']['translation_m'] = [0, 0, 0]
    method['digest'] = digest(policy)
    source = config['manifest']['sources'][capture.SOURCE]
    source['method_refs'] = [deepcopy(method)]
    source['association_method_refs'] = [deepcopy(method)]
    spatial = {'schema': 'home-live-spatial-policy-1', 'version': 1,
        'capture_method_ref': deepcopy(method), 'model_refs': deepcopy(model_refs),
        'receipt_store_id': 'synthetic-spatial-receipts', 'receipt_component_id': 'spatial',
        'qualification_ref': capture.pin('synthetic-independent-spatial-applicability'),
        'sampling': {'radius_px': 1.5, 'min_support': 3, 'max_spread_m': .03},
        'continuity': {'max_gap_s': 2, 'max_speed_m_s': 2, 'max_step_m': .5},
        'work_margin_m': [.1, .1, .1],
        'anatomy': {'torso_radius_m': .15, 'limb_radius_m': .08, 'hand_radius_m': .07,
            'head_half_extents_m': [.12, .12, .15], 'protection_margin_m': [.08, .08, .08],
            'min_shoulder_baseline_m': .1}}
    spatial_method = {'id': 'synthetic-measured-spatial-method', 'digest': digest(spatial)}
    source['method_refs'].append(deepcopy(spatial_method))
    source['association_method_refs'].append(deepcopy(spatial_method))
    source['model_refs'] = list(deepcopy(model_refs).values())
    source['receipt_store_ids'].append(spatial['receipt_store_id'])
    source['receipt_component_ids'].append(spatial['receipt_component_id'])
    for name in source['part_policies']:
        if name.endswith('_forward'):
            bound = {'model': 'bounded_angle-1', 'angular_error_rad': .2,
                'age': {'max_age_s': 1, 'max_angular_speed_rad_s': .5},
                'applicability_ref': deepcopy(spatial['qualification_ref'])}
        else:
            bound = {'model': 'conservative_additive_radius-1', 'bound_at_observation_m': .19,
                'components_m': {'capture_association': .03, 'depth': .06, 'registration': .02, 'estimator': .08},
                'age': {'max_age_s': 1, 'max_speed_m_s': 1, 'max_accel_m_s2': 1},
                'applicability_ref': deepcopy(spatial['qualification_ref'])}
        source['part_policies'][name] = bound
    source['primitive_ids'] = {'body': list(BODY_IDS), 'protection': ['head_exclusion']}
    source['protection_policy_refs'] = [capture.pin('synthetic-declared-head-exclusion')]
    config['manifest_ref']['digest'] = digest(config['manifest'])
    grant['live_bindings'][0]['manifest_ref'] = deepcopy(config['manifest_ref'])
    return config, policy, method, grant, spatial, spatial_method

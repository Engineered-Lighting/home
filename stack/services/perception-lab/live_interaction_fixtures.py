"""Fictional interaction commissioning and external SDK data, never home data."""
from copy import deepcopy
from types import SimpleNamespace

import live_capture_fixtures as capture
from journal import digest
from test_live_acquisition_models import Tensor


def commission(config, capture_method, spatial_method, grant, model_refs):
    config, grant = deepcopy(config), deepcopy(grant)
    policy = {'schema': 'home-live-interaction-policy-1', 'version': 1,
        'capture_method_ref': deepcopy(capture_method), 'spatial_method_ref': deepcopy(spatial_method),
        'model_refs': deepcopy(model_refs), 'receipt_store_id': 'synthetic-interaction-receipts',
        'receipt_component_id': 'interaction', 'qualification_ref': capture.pin('synthetic-experimental-holding'),
        'max_age_s': 1, 'semantic_model_refs': [],
        'material_geometry': {'labels': {'book': 'book', 'cell phone': 'phone', 'phone': 'phone'},
            'min_detector_score': .6, 'max_materials': 4, 'min_object_support': 20,
            'max_object_spread_m': .5, 'max_wrist_spread_m': .03,
            'max_hand_object_distance_m': .08, 'max_near_distance_m': .35,
            'min_hand_span_m': .08, 'edge_band_fraction': .25, 'rgb_margin_px': 8.,
            'min_between_fraction': .7}}
    method = {'id': 'synthetic-experimental-holding-method', 'digest': digest(policy)}
    source = config['manifest']['sources'][capture.SOURCE]
    source['method_refs'].append(deepcopy(method))
    source['receipt_store_ids'].append(policy['receipt_store_id'])
    source['receipt_component_ids'].append(policy['receipt_component_id'])
    config['manifest_ref']['digest'] = digest(config['manifest'])
    grant['live_bindings'][0]['manifest_ref'] = deepcopy(config['manifest_ref'])
    return config, grant, policy, method


def detector_result(boxes, materials):
    """External YOLO SDK result; resident normalization/model stage stays real."""
    all_boxes = deepcopy(boxes) + [deepcopy(item[1]) for item in materials]
    labels = {0: 'person', **{index + 1: item[0] for index, item in enumerate(materials)}}
    classes = [0.] * len(boxes) + [float(index + 1) for index in range(len(materials))]
    return [SimpleNamespace(orig_shape=(360, 640), names=labels,
        boxes=SimpleNamespace(xyxy=Tensor(all_boxes), conf=Tensor([.9] * len(all_boxes)), cls=Tensor(classes)), speed={})]

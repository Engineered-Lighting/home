"""Generated numeric cases for experimental hand/material relations only."""
from copy import deepcopy
import unittest

import numpy as np

import live_capture_fixtures as capture
import live_spatial_fixtures as spatial
from live_spatial_geometry import estimate_geometry
from live_interaction_geometry import estimate_interaction, validate_material_policy


MATERIAL_POLICY = {
    'labels': {'book': 'book', 'cell phone': 'phone', 'phone': 'phone'},
    'min_detector_score': .6, 'max_materials': 4, 'min_object_support': 20,
    'max_object_spread_m': .5, 'max_wrist_spread_m': .03,
    'max_hand_object_distance_m': .08, 'max_near_distance_m': .35,
    'min_hand_span_m': .08, 'edge_band_fraction': .25,
    'rgb_margin_px': 8., 'min_between_fraction': .7,
}


def fixture(*, translation=0., boxes=None):
    config, policy, method, _, grant = capture.setup()
    refs = {'detector': capture.pin('detector'), 'pose': capture.pin('pose')}
    config, policy, _, _, spatial_policy, _ = spatial.commission(config, policy, method, grant, refs)
    policy['projection']['rgb_from_rectified_left']['translation_m'][0] = translation
    numeric = {'depth_mm': np.full((400, 640), 1234, np.uint16),
               'source_metadata': capture.frame_metadata(), 'projection': policy['projection'],
               'registration': policy['registration'], 'intrinsics_tolerance_px': 1}
    source = config['manifest']['sources'][capture.SOURCE]
    detector = {'schema_version': 1, 'frame_id': 'synthetic-frame', 'coordinate_space': 'source_pixels',
                'boxes': [{'instance_id': 'synthetic-frame:0', 'label': 'person', 'confidence': .9,
                           'x': 220, 'y': 40, 'w': 200, 'h': 310}] + (deepcopy(boxes) if boxes is not None else [
                    {'instance_id': 'synthetic-frame:1', 'label': 'book', 'confidence': .9,
                     'x': 300, 'y': 190, 'w': 40, 'h': 20}])}
    pose = {'layout': 'coco17', 'instance_id': 'synthetic-frame:0', 'crop_xyxy': [220, 40, 420, 350],
            'joints': [{'joint_id': index, 'status': 'supported', 'pixel': pixel}
                       for index, pixel in enumerate(deepcopy(spatial.JOINTS))]}
    original = {'detector': {'result': deepcopy(detector)},
                'pose': {'result': {'frame_id': 'synthetic-frame', 'pose_instances': [pose]}},
                'joints': estimate_geometry(numeric, pose, spatial_policy, source)['joints']}
    return numeric, detector, original, {'material_geometry': deepcopy(MATERIAL_POLICY)}, source


class MaterialGeometryTests(unittest.TestCase):
    def test_stationary_opposing_hands_support_only_experimental_holding(self):
        values = fixture()
        result = estimate_interaction(*values)
        self.assertEqual(result['status'], 'supported', result)
        relation = result['relations'][0]
        self.assertEqual(relation['status'], 'holding_compatible', result)
        self.assertIs(relation['holding_proposal'], True)
        self.assertIn('hands_adjacent_to_material', relation['alternatives'])
        self.assertEqual(result['qualification'], 'experimental')
        self.assertNotIn('probability', result)
        self.assertNotIn('reading', str(result))
        # The estimator has no history; an identical still observation is valid.
        self.assertEqual(estimate_interaction(*values), result)

    def test_visible_book_without_near_wrists_is_not_interaction(self):
        values = fixture(boxes=[{'instance_id': 'synthetic-frame:1', 'label': 'book', 'confidence': .9,
                                'x': 50, 'y': 50, 'w': 40, 'h': 20}])
        result = estimate_interaction(*values)
        self.assertEqual(result['materials'][0]['category'], 'book')
        self.assertEqual(result['relations'][0]['status'], 'visible_only', result)
        self.assertFalse(result['relations'][0]['holding_proposal'])

    def test_same_side_and_one_hand_are_proximity_only(self):
        values = list(fixture())
        values[1]['boxes'][1].update(x=335, y=190, w=40)
        values[2]['detector']['result'] = deepcopy(values[1])
        result = estimate_interaction(*values)
        self.assertEqual(result['relations'][0]['status'], 'near', result)
        self.assertFalse(result['relations'][0]['holding_proposal'])
        values[2]['joints'][9].update(status='unobservable', xyz_m=None, reason='pose_unavailable')
        result = estimate_interaction(*values)
        self.assertEqual(result['relations'][0]['status'], 'near', result)
        self.assertFalse(result['relations'][0]['holding_proposal'])

    def test_depth_hole_preserves_detection_and_withholds_relation(self):
        values = list(fixture())
        values[0]['depth_mm'][:] = 0
        result = estimate_interaction(*values)
        self.assertEqual(result['materials'][0]['depth']['status'], 'unobservable')
        self.assertEqual(result['relations'][0]['status'], 'unobservable')
        self.assertFalse(result['relations'][0]['holding_proposal'])

    def test_two_supported_material_candidates_remain_conflicted(self):
        boxes = [{'instance_id': 'synthetic-frame:' + str(i), 'label': label, 'confidence': .9,
                  'x': 300, 'y': 190, 'w': 40, 'h': 20} for i, label in ((1, 'book'), (2, 'cell phone'))]
        result = estimate_interaction(*fixture(boxes=boxes))
        self.assertEqual(result['status'], 'conflicted', result)
        self.assertEqual({item['category'] for item in result['materials']}, {'book', 'phone'})
        self.assertTrue(all(item['status'] == 'conflicted' and not item['holding_proposal']
                            for item in result['relations']))
        self.assertEqual(len(result['alternatives']), 2)

    def test_material_budget_abstains_without_selecting_first(self):
        values = list(fixture(boxes=[{'instance_id': 'synthetic-frame:' + str(i), 'label': 'book', 'confidence': .9,
                                    'x': 300, 'y': 190, 'w': 40, 'h': 20} for i in range(1, 6)]))
        result = estimate_interaction(*values)
        self.assertEqual(result['reason'], 'material_candidate_budget_exceeded')
        self.assertEqual(result['materials'], [])
        self.assertEqual(result['relations'], [])

    def test_rgb_shift_uses_registered_projection_not_rgb_depth_index(self):
        values = list(fixture(translation=.2))
        # At z=1.234, x_RGB=320 corresponds to x_left about247, never320.
        # Retain only the object-support plane in that registered left region.
        values[0]['depth_mm'][:] = 0
        values[0]['depth_mm'][205:232, 225:269] = 1234
        result = estimate_interaction(*values)
        self.assertEqual(result['relations'][0]['status'], 'holding_compatible', result)
        point = result['materials'][0]['depth']['center_m']
        self.assertLess(point[0], .9)
        self.assertGreater(point[0], .7)

    def test_exact_score_threshold_is_included_but_lower_is_withheld(self):
        values = list(fixture())
        values[1]['boxes'][1]['confidence'] = MATERIAL_POLICY['min_detector_score']
        values[2]['detector']['result'] = deepcopy(values[1])
        self.assertEqual(len(estimate_interaction(*values)['materials']), 1)
        values[1]['boxes'][1]['confidence'] -= .0001
        values[2]['detector']['result'] = deepcopy(values[1])
        self.assertEqual(estimate_interaction(*values)['materials'], [])

    def test_enclosure_thresholds_are_inclusive_and_failure_stays_proximity(self):
        values = list(fixture())
        relation = estimate_interaction(*values)['relations'][0]
        thresholds = values[3]['material_geometry']
        span = relation['enclosure']['hand_span_m']
        thresholds['min_hand_span_m'] = span
        thresholds['min_between_fraction'] = relation['enclosure']['material_between_fraction']
        measured = {item['joint_id']: item for item in values[2]['joints']}
        thresholds['max_hand_object_distance_m'] = max(
            relation['hand_distances_m'][side] + measured[joint_id]['spread_m']
            for side, joint_id in (('left', 9), ('right', 10)))
        self.assertTrue(estimate_interaction(*values)['relations'][0]['holding_proposal'])
        thresholds['min_hand_span_m'] = np.nextafter(span, np.inf).item()
        result = estimate_interaction(*values)
        self.assertEqual(result['relations'][0]['status'], 'near', result)
        self.assertFalse(result['relations'][0]['holding_proposal'])

    def test_missing_wrist_measurement_bound_and_large_spread_withhold_holding(self):
        values = list(fixture())
        values[4]['part_policies']['hand_left'] = None
        result = estimate_interaction(*values)
        self.assertEqual(result['relations'][0]['status'], 'near', result)
        self.assertFalse(result['relations'][0]['holding_proposal'])
        values = list(fixture())
        values[2]['joints'][9]['spread_m'] = MATERIAL_POLICY['max_wrist_spread_m'] + .001
        result = estimate_interaction(*values)
        self.assertFalse(result['relations'][0]['holding_proposal'])

    def test_duplicate_wrist_slot_and_multiple_people_never_get_convenient_association(self):
        values = list(fixture())
        values[2]['joints'].append(deepcopy(values[2]['joints'][9]))
        result = estimate_interaction(*values)
        self.assertEqual(result['relations'][0]['status'], 'unobservable', result)
        self.assertFalse(result['relations'][0]['holding_proposal'])
        values = list(fixture())
        second = deepcopy(values[1]['boxes'][0])
        second['instance_id'] = 'synthetic-frame:2'
        values[1]['boxes'].append(second)
        values[2]['detector']['result'] = deepcopy(values[1])
        result = estimate_interaction(*values)
        self.assertEqual(result['reason'], 'material_person_association_unavailable')
        self.assertEqual(result['relations'], [])

    def test_material_result_has_no_cross_frame_or_temporal_identity(self):
        values = fixture()
        result = estimate_interaction(*values)
        self.assertEqual(result['materials'][0]['material_ref'], 'synthetic-frame:1')
        self.assertFalse(any(key in result for key in ('person_ref', 'subject_ref', 'engagement', 'dwell')))
        self.assertEqual(result['materials'][0]['score_role'], 'uncalibrated_model_score')

    def test_invalid_and_unrecognized_labels_do_not_become_missing_material_negatives(self):
        values = list(fixture())
        values[1]['boxes'][1]['confidence'] = float('nan')
        values[2]['detector']['result'] = deepcopy(values[1])
        self.assertEqual(estimate_interaction(*values)['reason'], 'material_result_invalid')
        values = list(fixture())
        values[1]['boxes'][1]['label'] = 'magazine'
        values[2]['detector']['result'] = deepcopy(values[1])
        result = estimate_interaction(*values)
        self.assertEqual(result['reason'], 'no_material_detected')
        self.assertEqual(result['status'], 'unobservable')
        self.assertEqual(result['relations'], [])

    def test_detector_frame_and_duplicate_object_identity_fail_closed(self):
        values = list(fixture())
        values[1]['frame_id'] = 'another-frame'
        self.assertEqual(estimate_interaction(*values)['reason'], 'material_capture_mismatch')
        values = list(fixture())
        values[1]['boxes'].append(deepcopy(values[1]['boxes'][1]))
        values[2]['detector']['result'] = deepcopy(values[1])
        self.assertEqual(estimate_interaction(*values)['reason'], 'material_result_invalid')

    def test_policy_is_closed_and_bounds_do_not_invert_near_and_holding(self):
        validate_material_policy(deepcopy(MATERIAL_POLICY))
        for key, value in (('max_materials', 9), ('min_between_fraction', .4),
                           ('max_hand_object_distance_m', .36), ('rgb_margin_px', True)):
            policy = deepcopy(MATERIAL_POLICY)
            policy[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_material_policy(policy)
        policy = deepcopy(MATERIAL_POLICY)
        policy['guessed_probability'] = 1
        with self.assertRaises(ValueError):
            validate_material_policy(policy)


if __name__ == '__main__':
    unittest.main()

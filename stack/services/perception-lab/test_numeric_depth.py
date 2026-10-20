"""Depth sampler compatibility with the actual lossless numerical dtype."""
import math
import unittest
import numpy as np
from geometry import robust_depth, robust_support, horizontal_extent, floor_projection

FLOOR = [[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]


class NumericDepthTests(unittest.TestCase):
    def test_uint16_bundle_plane_matches_python_values(self):
        depth=np.full((20,30),1500,dtype=np.uint16)
        depth[:,:4]=0
        roi=[0,0,30,20]
        result=robust_depth(depth,roi)
        self.assertEqual(result,robust_depth(depth.tolist(),roi))
        self.assertEqual(result['status'],'measured')
        self.assertEqual(result['depth_m'],1.5)
        self.assertTrue(result['surface_only'])

    def test_mask_and_discontinuity_do_not_turn_background_into_person_depth(self):
        depth=np.full((20,30),4500,dtype=np.uint16)
        depth[:,5:15]=1200
        mask=np.zeros(depth.shape,dtype=bool);mask[:,5:15]=True
        result=robust_depth(depth,[0,0,30,20],mask=mask)
        self.assertEqual(result['depth_m'],1.2)
        self.assertEqual(robust_depth(depth,[0,0,30,20])['reason'],'depth_discontinuity')
        depth[:,5:15]=0
        self.assertEqual(robust_depth(depth,[0,0,30,20],mask=mask)['reason'],'insufficient_valid_support')

    def test_boolean_pixels_and_invalid_thresholds_are_not_measurements(self):
        self.assertEqual(robust_depth([[True]*20 for _ in range(20)],[0,0,20,20])['status'],'unknown')
        for settings in ({'minimum_support':0},{'minimum_support':True},{'max_age_s':float('nan')},{'max_skew_s':-1}):
            self.assertEqual(robust_depth([[1000]*20 for _ in range(20)],[0,0,20,20],**settings)['reason'],'invalid_sampling_configuration')

    def test_vectorized_support_makes_the_same_decisions(self):
        rng = np.random.default_rng(7)
        cases = []
        for _ in range(40):
            depth = rng.normal(2000, rng.choice([5, 40, 400]), (24, 32)).clip(0, 65535).astype(np.uint16)
            depth[rng.random(depth.shape) < rng.choice([0, .2, .6])] = 0
            mask = rng.random(depth.shape) < .7 if rng.random() < .5 else None
            cases.append((depth, [int(rng.integers(0, 8)), int(rng.integers(0, 6)), int(rng.integers(20, 33)), int(rng.integers(16, 25))], mask))
        split = np.full((20, 30), 1000, np.uint16); split[:, 15:] = 3000
        cases += [(split, [0, 0, 30, 20], None), (np.zeros((8, 8), np.uint16), [0, 0, 8, 8], None),
                  (np.full((20, 20), 1500, np.uint16), [-1, 0, 20, 20], None),
                  (np.full((20, 20), 1500, np.uint16), [0, 0, 20, 20], np.zeros((19, 20), bool))]
        outcomes = set()
        for depth, roi, mask in cases:
            expected = robust_depth(depth, roi, mask=mask)
            sample, support = robust_support(depth, roi, mask=mask)
            self.assertEqual(sample, expected)
            self.assertEqual(support is None, expected['status'] != 'measured')
            outcomes.add(expected.get('reason', 'measured'))
            if support is not None:
                u, v, z = support
                self.assertEqual(len(u), expected['support'])
                self.assertTrue(np.all(depth[v, u] == z))
        self.assertTrue({'measured', 'depth_discontinuity', 'insufficient_valid_support', 'roi_outside_depth_frame',
                         'mask_frame_mismatch'} <= outcomes)
        for settings in ({'visible': False}, {'age_s': 1}, {'skew_s': .05}, {'minimum_support': 0}):
            self.assertEqual(robust_support(depth, roi, **settings)[0], robust_depth(depth, roi, **settings))

    def test_horizontal_extent_uses_only_floor_axes_of_the_registration(self):
        points = [[-.1, -.8, 2], [.1, .8, 2], [0, 0, 2.3], [0, 0, 1.9]]
        centre, radius, vertical = horizontal_extent(points, FLOOR, floor_axes='xy')
        self.assertEqual(centre, [0, 0, 2])
        self.assertAlmostEqual(radius, .3)
        self.assertAlmostEqual(vertical, 1.6)
        with self.assertRaises(ValueError):
            horizontal_extent(points, [[2,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]], floor_axes='xy')
        with self.assertRaises(ValueError):
            horizontal_extent(points, FLOOR, floor_axes='up')

    def test_framed_floor_contact_is_map_xy_with_pixel_scale_uncertainty(self):
        h = [[.01, 0, 0], [0, .01, 0], [0, 0, 1]]
        legacy = floor_projection([10, 20], h, calibration_version='1', reference_error_m=.2)
        self.assertEqual(legacy['xyz_m'], [.1, 0, .2])
        self.assertNotIn('frame', legacy)
        framed = floor_projection([100, 200], h, calibration_version='fh', reference_error_m=.1,
                                  target_frame='z_up_metric_floor0', pixel_uncertainty_px=4)
        self.assertEqual(framed['xyz_m'], [1, 2, 0])
        self.assertEqual((framed['frame'], framed['method'], framed['surface_only']), ('z_up_metric_floor0', 'rgb_floor_contact', False))
        self.assertAlmostEqual(framed['metres_per_pixel'], .01)
        self.assertAlmostEqual(framed['uncertainty_m'], .14)
        # Perspective: far floor pixels cover more metres, so uncertainty grows.
        perspective = [[.01, 0, 0], [0, .01, 0], [0, -.002, 1]]
        near = floor_projection([0, 100], perspective, calibration_version='fh', reference_error_m=0,
                                target_frame='map', pixel_uncertainty_px=1)
        far = floor_projection([0, 400], perspective, calibration_version='fh', reference_error_m=0,
                               target_frame='map', pixel_uncertainty_px=1)
        self.assertGreater(far['uncertainty_m'], near['uncertainty_m'])
        self.assertEqual(floor_projection([0, 600], perspective, calibration_version='fh', reference_error_m=0,
                                          target_frame='map', pixel_uncertainty_px=1)['reason'], 'beyond_floor_horizon')
        for bad in ({'pixel_uncertainty_px': None}, {'pixel_uncertainty_px': math.inf}, {'target_frame': 7}):
            with self.assertRaises(ValueError):
                floor_projection([1, 1], h, calibration_version='fh', reference_error_m=0,
                                 **{'target_frame': 'map', 'pixel_uncertainty_px': 1, **bad})


if __name__=='__main__':unittest.main()

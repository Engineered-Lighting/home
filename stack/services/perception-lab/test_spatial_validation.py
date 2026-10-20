import math
import unittest
from copy import deepcopy
import numpy as np
from reasoning import digest
from room_context import validate_registration
import spatial_validation as sv

# Camera 1.2 m above the floor, yawed 20 degrees; x right, y down, z forward.
YAW = math.radians(20)
TRUE = np.array([[math.cos(YAW), 0, math.sin(YAW), .8], [-math.sin(YAW), 0, math.cos(YAW), 0],
                 [0, -1, 0, 1.2], [0, 0, 0, 1]])


def den_model(**zone_changes):
    zones = [{'id':'desk', 'name':'Desk', 'zone_type':'desk', 'floor_polygon':[[.5, 1.5], [1.5, 1.5], [1.5, 2.5], [.5, 2.5]],
              'confirmation':{'by':'owner', 'at':'2026-10-01'}},
             {'id':'sofa', 'name':'Sofa', 'zone_type':'sofa', 'floor_polygon':[[2, 1], [4, 1], [4, 2], [2, 2]],
              'confirmation':{'by':'owner', 'at':'2026-10-01'}},
             {'id':'reading-chair', 'name':'Reading chair', 'zone_type':'reading_chair',
              'floor_polygon':[[2.5, 3], [3.5, 3], [3.5, 4], [2.5, 4]], 'confirmation':{'by':'owner', 'at':'2026-10-01'}}]
    for index, changes in zone_changes.items():
        zones[int(index[1:])] = {**zones[int(index[1:])], **changes}
    return {'revision':3, 'meta':{'frame':'z_up_metric_floor0', 'units':'metres'}, 'zones':zones, 'targets':[]}


def recomputed(value):
    return {**value, 'provenance':{'kind':'recomputed', 'input_sha256':digest(value)}}


def zone_truth(xy, room_map=None):
    for zone in den_model()['zones']:
        if sv._inside(xy, zone['floor_polygon']):
            return zone['id']
    return 'none'


def markers(noise=.004, count=6, seed=1):
    rng = np.random.default_rng(seed)
    floor = np.array([[0.3, 1.0], [3.5, 1.2], [1.0, 3.6], [3.2, 3.8], [2.0, 2.2], [0.8, 2.6], [2.9, 1.6], [1.6, 3.1]])[:count]
    target = np.c_[floor, np.zeros(len(floor))]
    inverse = np.linalg.inv(TRUE)
    source = (np.c_[target, np.ones(len(target))] @ inverse.T)[:, :3]+rng.normal(0, noise, (len(target), 3))
    return source, target


class FitTests(unittest.TestCase):
    def test_nearest_rank(self):
        values = list(range(1, 21))
        self.assertEqual(sv.nearest_rank(values, .5), 10)
        self.assertEqual(sv.nearest_rank(values, .95), 19)
        with self.assertRaises(ValueError):
            sv.nearest_rank([], .5)

    def test_rigid_fit_recovers_pose_with_leave_one_out_bound(self):
        source, target = markers()
        fit = sv.fit_rigid(source, target)
        np.testing.assert_allclose(np.array(fit['matrix']), TRUE, atol=.02)
        self.assertAlmostEqual(np.linalg.det(np.array(fit['matrix'])[:3, :3]), 1, places=9)
        self.assertGreaterEqual(fit['error_bound_m'], fit['max_m'])
        self.assertGreaterEqual(fit['error_bound_m'], fit['loo_max_m'])
        self.assertLess(fit['error_bound_m'], .05)
        # Coplanar mirrored input would fit by flipping the floor: refused.
        with self.assertRaisesRegex(ValueError, 'above the floor'):
            sv.fit_rigid(source*[-1, 1, 1], target)
        # Non-coplanar mirrored input cannot become a reflection: det stays +1.
        lifted = target+np.c_[np.zeros((6, 2)), [0, .9, 0, .4, 0, 1.1]]
        cam = (np.c_[lifted, np.ones(6)] @ np.linalg.inv(TRUE).T)[:, :3]
        mirrored = sv.fit_rigid(cam*[-1, 1, 1], lifted)
        self.assertAlmostEqual(np.linalg.det(np.array(mirrored['matrix'])[:3, :3]), 1, places=9)
        self.assertGreater(mirrored['error_bound_m'], .1)
        with self.assertRaisesRegex(ValueError, 'two floor directions'):
            sv.fit_rigid(source, [[x, 0, 0] for x in range(6)])
        with self.assertRaises(ValueError):
            sv.fit_rigid(source[:3], target[:3])

    def test_floor_homography_needs_six_markers_and_reports_floor_error(self):
        h = np.array([[.005, 0, -1.6], [0, -.01, 4.0], [0, .001, 1]])
        pixels = np.array([[100, 300], [500, 310], [320, 200], [150, 220], [480, 230], [320, 340], [250, 260]], float)
        q = np.c_[pixels, np.ones(len(pixels))] @ h.T
        floor = q[:, :2]/q[:, 2:3]
        fit = sv.fit_floor_homography(pixels, floor+np.random.default_rng(2).normal(0, .005, floor.shape))
        fitted = np.array(fit['matrix'])
        w = np.c_[pixels, np.ones(len(pixels))] @ fitted[2]
        self.assertTrue(np.all(w > 0))
        self.assertLess(fit['error_bound_m'], .1)
        self.assertGreaterEqual(fit['error_bound_m'], fit['max_m'])
        self.assertEqual((fit['width'], fit['height']), (640, 360))
        with self.assertRaises(ValueError):
            sv.fit_floor_homography(pixels[:5], floor[:5])


class MapTests(unittest.TestCase):
    def test_three_owner_confirmed_zones_bind_to_room_context(self):
        model = den_model()
        result = sv.validate_den_map(model, site_id='victoria', map_id='den')
        self.assertEqual(result['zone_bindings'], {'desk':'desk', 'sofa':'sofa', 'reading-chair':'reading_chair'})
        self.assertEqual(result['binding']['model_sha256'], digest(model))
        self.assertEqual(result['room_map']['floor_axes'], 'xy')
        self.assertEqual(len(result['room_map']['zones']), 3)

    def test_invalid_zone_models_are_rejected(self):
        bowtie = [[0, 0], [1, 1], [1, 0], [0, 1]]
        cases = {'two zones':{**den_model(), 'zones':den_model()['zones'][:2]},
                 'duplicate type':den_model(z1={'zone_type':'desk'}),
                 'doorway is not furniture':den_model(z1={'zone_type':'doorway'}),
                 'unconfirmed':den_model(z2={'confirmation':{'by':'installer', 'at':'2026-10-01'}}),
                 'self intersecting':den_model(z2={'floor_polygon':bowtie}),
                 'overlapping':den_model(z1={'floor_polygon':[[1, 1], [3, 1], [3, 2], [1, 2]]}),
                 'touching':den_model(z1={'floor_polygon':[[1.5, 1], [3, 1], [3, 2], [1.5, 2]]}),
                 'contained':den_model(z1={'floor_polygon':[[.6, 1.6], [1.4, 1.6], [1.4, 2.4], [.6, 2.4]]}),
                 'tiny':den_model(z2={'floor_polygon':[[2.5, 3], [2.6, 3], [2.6, 3.1]]}),
                 'wrong frame':{**den_model(), 'meta':{'frame':'y_up'}}}
        for name, model in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                sv.validate_den_map(model, site_id='victoria', map_id='den')


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.map = sv.validate_den_map(den_model(), site_id='victoria', map_id='den')['room_map']

    def positions(self, stereo=.1, rgb=.3, count=24):
        result = []
        zones = ['desk', 'sofa', 'reading-chair', None]
        for i in range(count):
            angle = i*2.4
            truth = [.4+(i % 6)*.6, .8+(i // 6)*.8]
            result.append({'id':f'p{i}', 'truth_xy':truth, 'range_class':('near', 'mid', 'far')[i % 3],
                           'boundary_of':zones[i % 4] if i < 8 else None, 'zone_truth':zone_truth(truth),
                           'estimates':{'stereo':recomputed({'status':'measured', 'uncertainty_m':.2,
                                                  'xy':[truth[0]+stereo*math.cos(angle), truth[1]+stereo*math.sin(angle)]}),
                                        'rgb':recomputed({'status':'estimated', 'uncertainty_m':.4,
                                               'xy':[truth[0]+rgb*math.cos(angle), truth[1]+rgb*math.sin(angle)]})},
                           'depth_range':recomputed({'true_m':2.0+i*.1, 'measured_m':2.0+i*.1+.05})})
        return result

    def test_criteria_per_method_with_coverage_and_depth_range(self):
        report = sv.evaluate_reference_positions(self.positions(), self.map)
        self.assertEqual(report['coverage']['status'], 'passed')
        self.assertEqual(report['methods']['stereo']['status'], 'passed')
        self.assertAlmostEqual(report['methods']['stereo']['median_horizontal_m'], .1)
        self.assertEqual(report['methods']['rgb']['status'], 'failed')
        self.assertEqual(report['depth_range']['status'], 'passed')
        self.assertAlmostEqual(report['depth_range']['max_abs_m'], .05)
        zones = report['methods']['stereo']['zone_decisions']
        self.assertEqual(sum(zones.values()), 24*3)

    def test_abstentions_and_boundaries_are_reported_never_dropped(self):
        positions = self.positions()
        for position in positions[:5]:
            position['estimates']['stereo'] = {'status':'unknown', 'reason':'insufficient_valid_support'}
        del positions[5]['estimates']['stereo']
        report = sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']
        self.assertEqual(report['abstentions'], {'insufficient_valid_support':5, 'not_recorded':1})
        self.assertEqual((report['measured'], report['abstained'], report['positions']), (18, 6, 24))
        # Fewer than twenty measured positions cannot validate, however accurate.
        self.assertEqual(report['status'], 'failed')
        # A position straddling a zone edge with wide uncertainty abstains as boundary.
        positions = self.positions()
        positions[0].update(truth_xy=[1.5, 2.0], zone_truth='desk')
        positions[0]['estimates']['stereo'] = recomputed({'status':'measured', 'xy':[1.45, 2.0], 'uncertainty_m':.2})
        zones = sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']['zone_decisions']
        self.assertGreaterEqual(zones['boundary'], 1)

    def test_p95_is_nearest_rank_and_coverage_is_required(self):
        positions = self.positions(count=20)
        positions[0]['estimates']['stereo']['xy'] = [positions[0]['truth_xy'][0]+3, positions[0]['truth_xy'][1]]
        report = sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']
        self.assertEqual((report['status'], report['max_horizontal_m']), ('passed', 3.0))
        positions[1]['estimates']['stereo']['xy'] = [positions[1]['truth_xy'][0]+.6, positions[1]['truth_xy'][1]]
        self.assertEqual(sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']['status'], 'failed')
        far_missing = [p for p in self.positions(count=30) if p['range_class'] != 'far']
        self.assertEqual(sv.evaluate_reference_positions(far_missing, self.map)['coverage']['status'], 'failed')
        with self.assertRaises(ValueError):
            sv.evaluate_reference_positions(self.positions(count=19), self.map)
        bad = self.positions(); bad[0]['zone_truth'] = 'kitchen'
        with self.assertRaises(ValueError):
            sv.evaluate_reference_positions(bad, self.map)

    def test_typed_estimates_and_ranges_never_count(self):
        positions = self.positions()
        for position in positions:
            position['estimates']['stereo'].pop('provenance')
            position['depth_range'] = {k: v for k, v in position['depth_range'].items() if k != 'provenance'}
        positions[0]['estimates']['rgb']['provenance'] = {'kind':'typed', 'input_sha256':'0'*64}
        positions[1]['estimates']['rgb']['provenance'] = {'kind':'recomputed', 'input_sha256':'not-a-digest'}
        report = sv.evaluate_reference_positions(positions, self.map)
        stereo = report['methods']['stereo']
        self.assertEqual((stereo['status'], stereo['measured'], stereo['abstentions']), ('not_evaluated', 0, {'not_recomputed':24}))
        self.assertEqual(report['methods']['rgb']['abstentions'], {'not_recomputed':2})
        self.assertEqual(report['depth_range'], {'pairs':0, 'not_recomputed':24, 'status':'not_evaluated'})

    def test_coverage_is_per_method_and_counts_only_measured_positions(self):
        # Reviewer probe: 20 accurate near/mid positions, and every far or boundary
        # position abstained. Global coverage looks complete; the method is not covered.
        positions = [p for p in self.positions(count=36) if p['range_class'] != 'far' and p['boundary_of'] is None][:20]
        for i in range(8):
            positions.append({'id':f'f{i}', 'truth_xy':[3.0, .4+i*.1], 'range_class':'far', 'zone_truth':'none',
                              'boundary_of':['desk', 'sofa', 'reading-chair', None][i % 4],
                              'estimates':{'stereo':{'status':'unknown', 'reason':'insufficient_valid_support'}}})
        report = sv.evaluate_reference_positions(positions, self.map)
        self.assertEqual(report['coverage']['status'], 'passed')
        stereo = report['methods']['stereo']
        self.assertEqual(stereo['coverage'], {'range_classes':['mid', 'near'], 'boundaries':[], 'status':'failed'})
        self.assertLessEqual(stereo['p95_horizontal_m'], .25)
        self.assertEqual(stereo['status'], 'failed')

    def test_confident_wrong_zone_or_frequent_abstention_fails_the_method(self):
        positions = self.positions()
        desk = next(p for p in positions if p['zone_truth'] == 'desk')
        desk['estimates']['stereo'] = recomputed({'status':'measured', 'xy':[desk['truth_xy'][0]+.2, 3.5], 'uncertainty_m':.05})
        report = sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']
        self.assertGreaterEqual(report['zone_decisions']['confident_wrong'], 1)
        self.assertLessEqual(report['p95_horizontal_m'], .5)
        self.assertEqual(report['status'], 'failed')
        positions = self.positions(count=30)
        for position in positions[20:27]:
            position['estimates']['stereo'] = {'status':'unknown', 'reason':'depth_discontinuity'}
        report = sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']
        self.assertEqual((report['measured'], report['coverage']['status']), (23, 'passed'))
        self.assertAlmostEqual(report['abstention_fraction'], 7/30)
        self.assertEqual(report['status'], 'passed')
        positions[27]['estimates']['stereo'] = {'status':'unknown', 'reason':'depth_discontinuity'}
        report = sv.evaluate_reference_positions(positions, self.map)['methods']['stereo']
        self.assertAlmostEqual(report['abstention_fraction'], 8/30)
        self.assertEqual(report['status'], 'failed')

    def test_depth_range_uses_max_of_ten_centimetres_and_five_percent(self):
        positions = self.positions()
        for position in positions:
            position['depth_range'] = recomputed({'true_m':4.0, 'measured_m':4.19})
        self.assertEqual(sv.evaluate_reference_positions(positions, self.map)['depth_range']['status'], 'passed')
        for position in positions:
            position['depth_range'] = recomputed({'true_m':1.0, 'measured_m':1.12})
        self.assertEqual(sv.evaluate_reference_positions(positions, self.map)['depth_range']['status'], 'failed')


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.map = sv.validate_den_map(den_model(), site_id='victoria', map_id='den')['room_map']
        source, target = markers()
        self.fit = sv.fit_rigid(source, target)
        self.floor = {'matrix':[[.01, 0, 0], [0, .01, 0], [0, 0, 1]], 'error_bound_m':.12}
        plane = np.full((400, 640), 2500, np.uint16)
        self.registration = sv.registration_record(self.fit, site_id='victoria', camera_id='den',
            calibration_content_id='content-1', mount_revision='den-shelf-1', room_map=self.map, floor=self.floor,
            mount_signature=sv.mount_signature(plane))
        self.calibration = {'site_id':'victoria', 'camera_id':'den', 'id':'content-1', 'mount_revision':'den-shelf-1',
                            'frame':'rectified_left', 'width':640, 'height':400, 'error_bound_m':.5,
                            'intrinsics':{'fx':450, 'fy':450, 'cx':319.5, 'cy':199.5}}
        evaluation = EvaluationTests.positions(self, rgb=.15)
        self.positions = evaluation
        self.evaluation = sv.evaluate_reference_positions(evaluation, self.map, sensor_error_m=.03)
        self.receipts = [sv.fit_receipt(self.registration, self.fit, markers={'m':1}, floor=self.floor),
                         sv.reference_receipt(self.registration, self.evaluation, positions=self.positions),
                         sv.reprojection_receipt(self.registration, sv.reprojection_report([1, 1.5, 2, 2.5, 1, 3]), pairs=[1])]

    def test_receipts_are_content_addressed(self):
        receipt = self.receipts[1]
        self.assertEqual(sv.verify_receipt(receipt), receipt)
        tampered = deepcopy(receipt); tampered['evaluation']['methods']['stereo']['median_horizontal_m'] = .01
        with self.assertRaisesRegex(ValueError, 'does not match'):
            sv.verify_receipt(tampered)

    def test_passed_receipts_validate_registration_homography_and_calibration(self):
        original = deepcopy((self.registration, self.calibration))
        registration, calibration, summary = sv.apply_receipts(self.registration, self.calibration, self.receipts)
        self.assertEqual((self.registration, self.calibration), original)
        self.assertEqual(summary, {'registration':'validated', 'floor_homography':'validated',
                                   'depth':'validated', 'reprojection':'validated'})
        self.assertEqual(registration['validation_receipt'], self.receipts[1]['id'])
        self.assertEqual(registration['floor_homography']['validation_receipt'], self.receipts[1]['id'])
        self.assertNotIn('floor_homography_candidate', registration)
        self.assertEqual((calibration['status'], calibration['validation_receipt']), ('validated', self.receipts[1]['id']))
        self.assertAlmostEqual(calibration['error_bound_m'], .05)
        self.assertEqual(calibration['reprojection']['receipt'], self.receipts[2]['id'])
        self.assertEqual(calibration['mount_signature'], self.registration['mount_signature'])
        validate_registration(registration, self.map)
        from spatial_consumer import SpatialConsumer
        SpatialConsumer(object(), calibration, registration, self.map, association_revision='assoc-1')
        # Re-applying the same receipts to the validated records is stable.
        self.assertEqual(sv.apply_receipts(registration, calibration, self.receipts)[:2], (registration, calibration))

    def test_failed_or_mismatched_receipts_never_validate(self):
        failing = sv.evaluate_reference_positions(EvaluationTests.positions(self, stereo=.4, rgb=.4), self.map)
        registration, calibration, summary = sv.apply_receipts(self.registration, self.calibration,
            [self.receipts[0], sv.reference_receipt(self.registration, failing, positions=[])])
        self.assertEqual((registration['status'], summary['registration'], summary['floor_homography']),
                         ('fitted', 'failed_receipt', 'unvalidated'))
        self.assertNotIn('floor_homography', registration)
        with self.assertRaises(ValueError):
            validate_registration(registration, self.map)
        # Evaluation without the fit receipt for this exact matrix does not validate.
        self.assertEqual(sv.apply_receipts(self.registration, self.calibration, self.receipts[1:])[2]['registration'], 'unvalidated')
        moved = {**self.registration, 'transform_version':'tf-other'}
        with self.assertRaisesRegex(ValueError, 'transform'):
            sv.apply_receipts(moved, self.calibration, self.receipts)
        remounted = sv.registration_record(self.fit, site_id='victoria', camera_id='den', calibration_content_id='content-1',
                                           mount_revision='den-shelf-2', room_map=self.map, floor=self.floor)
        with self.assertRaisesRegex(ValueError, 'another camera'):
            sv.apply_receipts(remounted, {**self.calibration, 'mount_revision':'den-shelf-2'}, self.receipts)
        check = sv.check_mount(self.registration['mount_signature'], np.full((400, 640), 1500, np.uint16))
        self.assertEqual(check['status'], 'changed')
        invalidated = self.receipts+[sv.mount_invalidation_receipt(self.registration, check)]
        with self.assertRaisesRegex(ValueError, 'new mount_revision'):
            sv.apply_receipts(self.registration, self.calibration, invalidated)

    def test_a_failed_attempt_vetoes_a_passing_retry(self):
        failing = sv.evaluate_reference_positions(EvaluationTests.positions(self, stereo=.4, rgb=.4), self.map, sensor_error_m=.03)
        self.assertEqual({m['status'] for m in failing['methods'].values()}, {'failed'})
        failed = sv.reference_receipt(self.registration, failing, positions=[])
        registration, calibration, summary = sv.apply_receipts(self.registration, self.calibration, self.receipts+[failed])
        self.assertEqual((registration['status'], summary['registration'], summary['floor_homography']),
                         ('fitted', 'failed_receipt', 'unvalidated'))
        self.assertNotIn('floor_homography', registration)
        # Stereo passes in both attempts but RGB failed once: only the homography is refused.
        rgb_failed = deepcopy(self.evaluation); rgb_failed['methods']['rgb']['status'] = 'failed'
        registration, _, summary = sv.apply_receipts(self.registration, self.calibration,
            self.receipts+[sv.reference_receipt(self.registration, rgb_failed, positions=[])])
        self.assertEqual((summary['registration'], summary['floor_homography']), ('validated', 'failed_receipt'))
        self.assertNotIn('floor_homography', registration)
        depth_failed = deepcopy(self.evaluation); depth_failed['depth_range']['status'] = 'failed'
        _, calibration, summary = sv.apply_receipts(self.registration, self.calibration,
            self.receipts+[sv.reference_receipt(self.registration, depth_failed, positions=[])])
        self.assertEqual((calibration['status'], summary['depth']), ('unvalidated', 'failed_receipt'))
        bad = sv.reprojection_receipt(self.registration, sv.reprojection_report([1, 2, 3, 4, 5, 9]), pairs=[2])
        _, calibration, summary = sv.apply_receipts(self.registration, self.calibration, self.receipts+[bad])
        self.assertEqual(summary['reprojection'], 'failed_receipt')
        self.assertNotIn('reprojection', calibration)

    def test_error_bounds_are_bound_and_live_sensor_error_never_shrinks(self):
        candidate = self.registration['floor_homography_candidate']
        for edit in ({'error_bound_m':.01}, {'floor_homography_candidate':{**candidate, 'error_bound_m':.01}}):
            with self.subTest(edit=sorted(edit)), self.assertRaisesRegex(ValueError, 'does not match'):
                sv.apply_receipts({**self.registration, **edit}, self.calibration, self.receipts)
        # The evaluation assumed a 0.10 m sensor error: live never uses less, even when
        # the measured depth-range error was smaller.
        assumed = sv.evaluate_reference_positions(self.positions, self.map, sensor_error_m=.10)
        receipts = [self.receipts[0], sv.reference_receipt(self.registration, assumed, positions=self.positions)]
        calibration = sv.apply_receipts(self.registration, self.calibration, receipts)[1]
        self.assertEqual((calibration['status'], calibration['error_bound_m']), ('validated', .10))
        # Without a recorded assumption the depth calibration is not validated.
        unstated = sv.evaluate_reference_positions(self.positions, self.map)
        self.assertNotIn('assumptions', unstated)
        receipts = [self.receipts[0], sv.reference_receipt(self.registration, unstated, positions=self.positions)]
        registration, calibration, summary = sv.apply_receipts(self.registration, self.calibration, receipts)
        self.assertEqual((summary['registration'], summary['depth'], calibration['status']),
                         ('validated', 'unvalidated', 'unvalidated'))
        with self.assertRaises(ValueError):
            sv.evaluate_reference_positions(self.positions, self.map, sensor_error_m=-.1)

    def test_mount_check_tolerates_a_person_but_not_a_moved_camera(self):
        plane = np.full((400, 640), 2500, np.uint16)
        signature = sv.mount_signature(plane)
        person = plane.copy(); person[100:300, 200:400] = 1200
        self.assertEqual(sv.check_mount(signature, person)['status'], 'unchanged')
        self.assertEqual(sv.check_mount(signature, np.full((400, 640), 2700, np.uint16))['status'], 'changed')
        self.assertEqual(sv.check_mount(signature, np.zeros((400, 640), np.uint16))['status'], 'unknown')
        with self.assertRaises(ValueError):
            sv.mount_invalidation_receipt(self.registration, {'status':'unknown'})


if __name__ == '__main__': unittest.main()

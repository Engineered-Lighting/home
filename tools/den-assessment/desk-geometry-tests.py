"""Generated-coordinate tests only. No private data, models or camera calls."""
from copy import deepcopy
import importlib.util
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('desk_geometry_guide', Path(__file__).with_name('desk-geometry-guide.py'))
guide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guide)


def measurement(x, y, z, uncertainty=.01):
    return {'wall_distances_m': [x, y], 'height_above_floor_m': z, 'uncertainty_m': uncertainty}


def survey():
    return {'schema': 'home-desk-geometry-survey-1', 'version': 1, 'survey_method': 'tape',
        'references': {'frame': 'z_up_metric_floor0', 'unit': 'm',
            'equipment_ref': {'revision': 2, 'digest': 'a'*64, 'file_sha256': 'b'*64},
            'binding': {'content_revision': 'authored', 'registration_version': 1,
                'manifest_digest': 'c'*64, 'frame_digest': 'd'*64, 'canonical_collision_digest': 'e'*64},
            'floor': {'id': 'floor', 'normal': [0, 0, 1], 'offset_m': 0},
            'walls': [{'id': 'a', 'label': 'First wall', 'normal_xy': [1, 0], 'offset_m': 0},
                      {'id': 'b', 'label': 'Second wall', 'normal_xy': [0, 1], 'offset_m': 0}],
            'reference_uncertainty_m': .01},
        'work_patch': {'outer_corners': [measurement(1, 2, .75), measurement(2, 3, .75)], 'thickness_m': .02},
        'seated_envelope': {'outer_corners': [measurement(.5, .5, 0), measurement(2.5, 2, 1.8)],
            'coverage_declaration': 'body_head_both_hands_supported_seated_reach'}, 'camera': None}


class DeskGeometryTests(unittest.TestCase):
    def test_measured_work_patch_and_occluded_envelope_remain_candidate(self):
        value = survey(); original = deepcopy(value)
        result = guide.prepare_candidate(value, now=lambda: 1000)
        self.assertEqual(value, original)
        self.assertEqual(result['work_patch']['center_m'][:2], [1.5, 2.5])
        self.assertAlmostEqual(result['work_patch']['half_extents_m'][0], .52)
        self.assertAlmostEqual(result['work_patch']['half_extents_m'][2], .03)
        self.assertEqual(result['camera_registration']['status'], 'unavailable')
        self.assertFalse(result['qualified'])
        self.assertIsNone(result['operational_geometry'])
        self.assertIsNone(result['person_identity'])
        self.assertFalse(any(result['authority'].values()))
        self.assertEqual(result['survey_sha256'], guide.digest(value))
        unsigned = {k: v for k, v in result.items() if k != 'digest'}
        self.assertEqual(result['digest'], guide.digest(unsigned))

    def test_wall_offsets_sloped_floor_and_uncertainty_are_computed(self):
        refs = survey()['references']
        refs['walls'][0]['offset_m'] = -3
        refs['walls'][1]['offset_m'] = -2
        refs['floor']['normal'] = [.6, 0, .8]
        point, error = guide.room_point(measurement(4, 4, 1), guide.reference_frame(refs))
        for actual, expected in zip(point, [1., 2., .25]):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(error[2], .035)

    def test_nonfloor_camera_fit_and_independent_check_use_existing_rigid_fit(self):
        value = survey()
        points = [[0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 2], [.5, .4, 1.5]]
        value['camera'] = {'source_frame': 'rectified_left', 'points': [
            {'role': 'fit' if i < 4 else 'check', 'camera_xyz_m': p, 'camera_uncertainty_m': .005,
             'room_measurement': measurement(p[0]+2, p[1]+3, p[2]+.5)} for i, p in enumerate(points)]}
        result = guide.prepare_candidate(value, now=lambda: 1000)['camera_registration']
        self.assertEqual(result['status'], 'candidate')
        self.assertLess(max(result['independent_check_errors_m']), 1e-10)
        for i, expected in enumerate((2, 3, .5)):
            self.assertAlmostEqual(result['room_from_rectified_left'][i][3], expected)
        self.assertFalse(result['qualified'])

    def test_independent_check_error_is_retained_not_used_to_tune_fit(self):
        value = survey()
        points = [[0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 2], [.5, .4, 1.5]]
        value['camera'] = {'source_frame': 'rectified_left', 'points': [
            {'role': 'fit' if i < 4 else 'check', 'camera_xyz_m': p, 'camera_uncertainty_m': .005,
             'room_measurement': measurement(p[0]+2+(1 if i == 4 else 0), p[1]+3, p[2]+.5)}
            for i, p in enumerate(points)]}
        result = guide.prepare_candidate(value)['camera_registration']
        self.assertAlmostEqual(result['independent_check_errors_m'][0], 1)
        self.assertAlmostEqual(result['room_from_rectified_left'][0][3], 2)

    def test_parallel_walls_degenerate_bounds_unknown_values_and_authority_injection_refuse(self):
        mutations = [lambda v: v['references']['walls'][1].update(normal_xy=[1, 0]),
            lambda v: v['work_patch']['outer_corners'][1].update(wall_distances_m=[1, 2]),
            lambda v: v['seated_envelope']['outer_corners'][1].update(height_above_floor_m=0),
            lambda v: v['work_patch']['outer_corners'][0].update(uncertainty_m=None),
            lambda v: v['references'].update(reference_uncertainty_m=float('nan')),
            lambda v: v.update(operational_geometry=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = survey(); mutation(value)
                with self.assertRaises(guide.SurveyRefusal):
                    guide.prepare_candidate(value)

    def test_degenerate_camera_points_and_missing_independent_check_refuse(self):
        value = survey()
        rows = [{'role': 'fit', 'camera_xyz_m': [i, 0, 1], 'camera_uncertainty_m': .01,
                 'room_measurement': measurement(i+1, 1, 1)} for i in range(5)]
        value['camera'] = {'source_frame': 'rectified_left', 'points': rows}
        with self.assertRaises(guide.SurveyRefusal):
            guide.prepare_candidate(value)
        rows[-1]['role'] = 'check'
        with self.assertRaises(guide.SurveyRefusal):
            guide.prepare_candidate(value)

    def test_bounded_duplicate_key_parse_and_exclusive_candidate_export(self):
        with self.assertRaises(guide.SurveyRefusal):
            guide.parse(b'{"a":1,"a":2}')
        with self.assertRaises(guide.SurveyRefusal):
            guide.parse(b' '*(guide.MAX_BYTES+1))
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            path = Path(temporary)/'candidate.json'
            result = guide.prepare_candidate(survey(), now=lambda: 1000)
            guide.save_candidate(path, result)
            self.assertEqual(json.loads(path.read_bytes()), result)
            with self.assertRaises(FileExistsError):
                guide.save_candidate(path, result)

    def test_actual_loopback_form_contract_saves_candidate_then_stops(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            path = Path(temporary)/'candidate.json'
            server = guide.Guide(path, seconds=10)
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('GET', '/')
                response = connection.getresponse(); page = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIn(server.nonce, page)
                self.assertIn('The camera stays fixed', page)
                connection.close()
                headers = {'Origin': server.origin, 'X-Desk-Nonce': server.nonce, 'Content-Type': 'application/json'}
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('POST', '/prepare', guide.encoded(survey()), headers)
                response = connection.getresponse(); result = json.loads(response.read())
                self.assertEqual(response.status, 200, result)
                self.assertEqual(result['status'], 'candidate_saved')
                self.assertFalse(result['qualified'])
                self.assertEqual(json.loads(path.read_bytes())['digest'], result['digest'])
                connection.close()
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('POST', '/stop', b'{}', headers)
                response = connection.getresponse(); self.assertEqual(json.loads(response.read()), {'status': 'stopped'})
                connection.close()
            finally:
                server.stopped.set(); thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_cross_origin_wrong_nonce_and_oversized_body_cannot_write(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            path = Path(temporary)/'candidate.json'
            server = guide.Guide(path, seconds=10)
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            try:
                for origin, nonce, body, declared_size in [('http://wrong.invalid', server.nonce, guide.encoded(survey()), None),
                    (server.origin, 'wrong', guide.encoded(survey()), None),
                    (server.origin, server.nonce, b'{}', str(guide.MAX_BYTES+1))]:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    headers = {'Origin': origin, 'X-Desk-Nonce': nonce, 'Content-Type': 'application/json'}
                    if declared_size:
                        headers['Content-Length'] = declared_size
                    connection.request('POST', '/prepare', body,
                        headers)
                    response = connection.getresponse(); result = json.loads(response.read())
                    self.assertEqual(response.status, 409, result)
                    self.assertFalse(path.exists())
                    connection.close()
            finally:
                server.stopped.set(); thread.join(2)


if __name__ == '__main__':
    unittest.main()

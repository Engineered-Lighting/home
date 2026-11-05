"""Observation in, Track out: against schemas.json and fixtures written from it. Nothing here is real."""
import json
import os
import tempfile
import unittest

import method
import records
import synth
import truth

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMAS = os.path.join(HERE, '..', '..', 'docs', 'architecture', 'live-v1', 'schemas.json')
COV3 = [[0.01, 0.0, 0.0], [0.0, 0.02, 0.0], [0.0, 0.0, 0.01]]


def schema_fields(record_id):
    with open(SCHEMAS, encoding='utf-8') as f:
        data = json.load(f)
    record = next(r for r in data['records'] if r['id'] == record_id)
    return {field['name'] for field in record['fields']}


def params():
    return method.Method().tracker


def obs(oid, ts, x=1.0, y=2.0, kind='keypoints', box=(100, 100, 200, 400), **extra):
    rec = {'obs_id': oid, 'source': {'sensor_id': 'den-oak', 'model': 'm', 'model_version': 'v'}, 'capture_ts': ts,
           'kind': kind, 'image_box': list(box), 'room_position': {'x': x, 'y': y, 'z': 1.0, 'cov': COV3},
           'transform_id': 'den-oak@2026-10-18', 'score': 0.9, 'visibility': 'visible', 'validity': 'valid'}
    rec.update(extra)
    return rec


class SchemaTest(unittest.TestCase):
    def setUp(self):
        data = synth.scenario('clean_crossing', seed=0, noise='typical')
        self.observations, _, _ = synth.to_records(data, with_boxes=True)
        self.lines, _ = records.run_tracker(self.observations, params())

    def test_track_lines_use_exactly_the_schema_field_names_plus_version(self):
        fields = schema_fields('track')
        self.assertEqual(fields - set(self.lines[0]), {'appearance'}, 'appearance waits for the owner')
        for line in self.lines:
            self.assertEqual(set(line) - fields, {'version'})

    def test_track_values_have_the_schema_shape(self):
        for line in self.lines:
            self.assertEqual(line['version'], 'track/v1')
            self.assertIsInstance(line['track_id'], str)
            self.assertIn(line['state'], ('tentative', 'confirmed', 'coasting', 'ended'))
            self.assertEqual(set(line['position']), {'x', 'y', 'cov'})
            self.assertEqual(len(line['position']['cov']), 2)
            self.assertEqual(set(line['velocity']), {'vx', 'vy'})
            self.assertEqual(set(line['association']), {'status', 'hypotheses', 'since_ts'})
            self.assertIn(line['association']['status'], ('confident', 'ambiguous'))
            self.assertIsInstance(line['last_obs_ids'], list)
            if line['heading'] is not None:
                self.assertEqual(set(line['heading']), {'deg', 'sd_deg'})
                self.assertIsInstance(line['heading_as_of'], float)

    def test_identity_stays_unbound_with_nothing_filled_in(self):
        for line in self.lines:
            self.assertEqual(line['identity'], {'binding': 'unbound', 'person_id': None, 'method': None,
                                                'confidence': None, 'as_of': None})

    def test_generated_observations_use_the_schema_field_names_plus_version(self):
        fields = schema_fields('observation') | {'version'}
        for rec in self.observations:
            self.assertEqual(set(rec) - fields, set())

    def test_every_joint_is_null_or_located_never_zero_filled(self):
        for rec in self.observations:
            for joint in rec.get('joints_room', []):
                if joint['status'] == 'unsupported':
                    self.assertIsNone(joint['xyz'])
                    self.assertIsNone(joint['cov'])
                else:
                    self.assertNotEqual(joint['xyz'][:2], [0.0, 0.0])


class ObservationSeamTest(unittest.TestCase):
    """Rung 2's lines carry no version, add freshness and write rejected:<reason>; rung 5's carry a version."""

    def test_a_line_with_freshness_and_no_version_is_read(self):
        rec = obs('a', 1.0, freshness={'available_ts': 1.2, 'status': 'fresh'})
        lines, counts = records.run_tracker([rec], params())
        self.assertEqual(lines[0]['last_obs_ids'], ['a'])
        self.assertEqual(counts['rejected'], 0)

    def test_a_line_with_a_version_is_read(self):
        lines, _ = records.run_tracker([obs('a', 1.0, version='observation/v1')], params())
        self.assertEqual(len(lines), 1)

    def test_rejected_in_every_form_is_left_out(self):
        for validity in ('rejected:too_small', {'status': 'rejected', 'reason': 'too_small'}):
            lines, counts = records.run_tracker([obs('a', 1.0, validity=validity)], params())
            self.assertEqual(lines, [])
            self.assertEqual(counts['rejected'], 1)

    def test_valid_as_an_object_is_valid(self):
        lines, _ = records.run_tracker([obs('a', 1.0, validity={'status': 'valid'})], params())
        self.assertEqual(len(lines), 1)

    def test_no_validity_is_refused(self):
        rec = obs('a', 1.0)
        del rec['validity']
        with self.assertRaises(records.RecordError):
            records.run_tracker([rec], params())

    def test_no_position_is_left_out_absent_or_null(self):
        absent = obs('a', 1.0)
        del absent['room_position']
        for rec in (absent, obs('b', 1.0, room_position=None)):
            lines, counts = records.run_tracker([rec], params())
            self.assertEqual(lines, [])
            self.assertEqual(counts['no_position'], 1)

    def test_an_unusable_covariance_is_left_out(self):
        bad = [[[0.0, 0, 0], [0, 0.0, 0], [0, 0, 0.0]], [[-1, 0, 0], [0, 1, 0], [0, 0, 1]], None, [[1]]]
        for cov in bad:
            rec = obs('a', 1.0, room_position={'x': 1.0, 'y': 1.0, 'z': 1.0, 'cov': cov})
            lines, counts = records.run_tracker([rec], params())
            self.assertEqual(lines, [], cov)
            self.assertEqual(counts['no_position'], 1)

    def test_other_kinds_are_not_people_but_still_tick_the_clock(self):
        walk = [obs('k%d' % k, k * 0.25) for k in range(3)]
        chair = obs('chair', 10.0, kind='object_box', label='armchair')
        lines, counts = records.run_tracker(walk + [chair], params())
        self.assertEqual(counts['not_person'], 1)
        self.assertEqual(lines[-1]['state'], 'ended', 'the object frame is how the track learns time has passed')

    def test_a_repeated_obs_id_is_refused(self):
        with self.assertRaises(records.RecordError):
            records.run_tracker([obs('a', 1.0), obs('a', 2.0)], params())

    def test_a_missing_capture_time_is_refused(self):
        with self.assertRaises(records.RecordError):
            records.run_tracker([obs('a', None)], params())


class TwoObservationsOfOnePersonTest(unittest.TestCase):
    def test_box_and_keypoints_are_one_update_and_both_are_listed(self):
        frames = []
        for k in range(3):
            frames.append(obs('k%d' % k, k * 0.25, kind='keypoints'))
            frames.append(obs('b%d' % k, k * 0.25, x=1.05, kind='person_box'))
        lines, counts = records.run_tracker(frames, params())
        self.assertEqual(counts['tracks'], 1)
        self.assertEqual(lines[-1]['last_obs_ids'], ['b2', 'k2'])

    def test_the_keypoints_torso_point_is_the_measurement(self):
        rows = [obs('k', 0.0, x=1.0, kind='keypoints'), obs('b', 0.0, x=3.0, kind='person_box')]
        frames, _ = records.frames_from_observations(rows, params())
        self.assertEqual(frames[0]['detections'][0]['xy'], (1.0, 2.0))

    def test_a_box_with_no_position_still_joins_its_keypoints(self):
        box = obs('b', 0.0, kind='person_box')
        del box['room_position']
        lines, counts = records.run_tracker([obs('k', 0.0), box], params())
        self.assertEqual(lines[0]['last_obs_ids'], ['b', 'k'])
        self.assertEqual(counts['no_position'], 0)

    def test_two_people_side_by_side_pair_with_their_own_box(self):
        rows = [obs('ka', 0.0, x=1.0, box=(100, 100, 200, 400)), obs('kb', 0.0, x=2.0, box=(150, 100, 250, 400)),
                obs('ba', 0.0, x=1.0, kind='person_box', box=(102, 100, 200, 400)),
                obs('bb', 0.0, x=2.0, kind='person_box', box=(150, 100, 252, 400))]
        frames, _ = records.frames_from_observations(rows, params())
        pairs = sorted(d['obs_ids'] for d in frames[0]['detections'])
        self.assertEqual(pairs, [['ba', 'ka'], ['bb', 'kb']])

    def test_boxes_that_do_not_overlap_are_two_people(self):
        rows = [obs('k', 0.0, x=1.0, box=(100, 100, 200, 400)), obs('b', 0.0, x=4.0, kind='person_box', box=(800, 100, 900, 400))]
        frames, _ = records.frames_from_observations(rows, params())
        self.assertEqual(len(frames[0]['detections']), 2)

    def test_the_tracker_does_as_well_with_both_kinds(self):
        data = synth.scenario('close_crossing', seed=2, noise='typical')
        one, _, _ = synth.to_records(data, with_boxes=False)
        both, _, _ = synth.to_records(data, with_boxes=True)
        lines_one, counts_one = records.run_tracker(one, params())
        lines_both, counts_both = records.run_tracker(both, params())
        self.assertEqual(counts_one['tracks'], counts_both['tracks'])
        self.assertEqual([l['position'] for l in lines_one], [l['position'] for l in lines_both])


class FileFormatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_crlf_bom_and_blank_lines_read_the_same(self):
        rows = [{'a': 1}, {'b': 'x'}]
        lf = self.path('lf.jsonl')
        records.write_jsonl(lf, rows)
        with open(lf, 'rb') as f:
            raw = f.read()
        self.assertNotIn(b'\r', raw)
        crlf = self.path('crlf.jsonl')
        with open(crlf, 'wb') as f:
            f.write(b'\xef\xbb\xbf' + raw.replace(b'\n', b'\r\n') + b'\r\n')
        self.assertEqual(records.read_jsonl(crlf), rows)
        self.assertEqual(records.read_jsonl(lf), rows)

    def test_utf16_says_what_to_do(self):
        p = self.path('u16.jsonl')
        with open(p, 'w', encoding='utf-16') as f:
            f.write('{"a": 1}\n')
        with self.assertRaisesRegex(records.RecordError, 'UTF-16'):
            records.read_jsonl(p)

    def test_a_bad_line_names_its_number(self):
        p = self.path('bad.jsonl')
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write('{"a": 1}\nnot json\n')
        with self.assertRaisesRegex(records.RecordError, 'line 2'):
            records.read_jsonl(p)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write('[1, 2]\n')
        with self.assertRaisesRegex(records.RecordError, 'line 1'):
            records.read_jsonl(p)

    def test_truth_written_and_read_back(self):
        p = self.path('truth.jsonl')
        truth.write(p, 't', ['A', 'B'], [(1.0, {'o': 'A'})],
                    [{'crossing_id': 'c', 'type': 'crossing', 'interval': [0.0, 2.0]}])
        t = truth.read(p)
        self.assertEqual((t.take, t.labels), ('t', {'o': 'A'}))


if __name__ == '__main__':
    unittest.main()

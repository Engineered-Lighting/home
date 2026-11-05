"""Observation in, Track out: against schemas.json and fixtures written from it. Nothing here is real."""
import json
import os
import tempfile
import unittest

import method
import records
import score
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
        self.observations, _, _ = synth.to_records(data)
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


def rung2_obs(ms, index, kind, box, xy, t):
    """A line as rung 2's observe.py writes it: one per detector box, keypoints when the box got a pose, else person_box."""
    rec = {'obs_id': 'o-den-oak-%d-%d' % (ms, index),
           'source': {'sensor_id': 'den-oak', 'model': 'rtmpose-m' if kind == 'keypoints' else 'yolo26s', 'model_version': None},
           'kind': kind, 'capture_ts': t, 'transform_id': 'den-oak@synthetic', 'depth_ts': t, 'image_box': list(box),
           'score': 0.8 if kind == 'keypoints' else 0.45,
           'room_position': {'x': xy[0], 'y': xy[1], 'z': 1.1, 'cov': [[0.01, 0, 0], [0, 0.02, 0], [0, 0, 0.003]]},
           'visibility': 'visible' if kind == 'keypoints' else 'partial', 'validity': 'valid',
           'freshness': {'status': 'fresh', 'depth': 'used'}}
    if kind == 'keypoints':
        rec.update(keypoints=[[None, None, None]] * 17, joint_set='coco17',
                   joints_room=[{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(17)])
    return rec


class OneObservationOneDetectionTest(unittest.TestCase):
    """Rung 2 writes one Observation per detector box, so two in a frame are two detections, however much they overlap."""
    FPS, T0 = 4.0, 1793066600.0

    def crossing(self):
        """A walks behind B. While A is behind, A's box gets no pose and sits inside B's box, at IoU >= 0.5."""
        rows, labels, stamps, behind = [], {}, {}, []
        for k in range(int(8 * self.FPS)):
            t = round(self.T0 + k / self.FPS, 4)
            ms = int(round(t * 1000))
            xa = -2.0 + 0.5 * k / self.FPS
            front = rung2_obs(ms, 0, 'keypoints', (860, 200, 1060, 900), (0.0, 2.0), t)
            if abs(xa) < 0.35:
                rear = rung2_obs(ms, 1, 'person_box', (880, 240, 1040, 860), (xa, 2.6), t)
                behind.append(t)
            else:
                u = 960 + 600 * xa / 2.6
                rear = rung2_obs(ms, 1, 'keypoints', (u - 58, 260, u + 58, 830), (xa, 2.6), t)
            for rec, person in ((front, 'B'), (rear, 'A')):
                rows.append(rec)
                labels[rec['obs_id']] = person
                stamps[rec['obs_id']] = t
        self.assertEqual(len(behind), 5)
        crossings = [{'crossing_id': 'c1', 'type': 'crossing', 'interval': [self.T0 + 3.0, self.T0 + 5.0]}]
        return rows, labels, truth.Truth('t', ['A', 'B'], labels, stamps, crossings), behind

    def test_a_box_only_person_behind_another_is_never_on_the_other_track(self):
        rows, labels, _, _ = self.crossing()
        lines, _ = records.run_tracker(rows, params())
        both = [l for l in lines if len({labels[o] for o in l['last_obs_ids']}) > 1]
        self.assertEqual(both, [], 'a track line lists both people')

    def test_a_box_only_person_keeps_their_own_track_and_is_not_left_coasting(self):
        rows, labels, _, behind = self.crossing()
        lines, counts = records.run_tracker(rows, params())
        self.assertEqual(counts['tracks'], 2)
        on_track = {}
        for line in lines:
            for oid in line['last_obs_ids']:
                on_track.setdefault(line['track_id'], set()).add(labels[oid])
        self.assertEqual(sorted(sorted(v) for v in on_track.values()), [['A'], ['B']])
        listed = {o for l in lines for o in l['last_obs_ids']}
        self.assertEqual({r['obs_id'] for r in rows if r['capture_ts'] in behind} - listed, set(),
                         'every observation behind the other person is on a track')

    def test_the_crossing_scores_no_phantom_switch(self):
        rows, _, labelled, _ = self.crossing()
        lines, _ = records.run_tracker(rows, params())
        report = score.score_session([{'name': 't', 'observations': rows, 'tracks': lines, 'truth': labelled}],
                                     method.Method().scoring)
        window = report['windows'][0]
        self.assertEqual((window['switches'], window['silent_swaps']), (0, 0))
        self.assertEqual(report['pooled']['idf1'], 1.0)

    def test_two_people_seated_with_overlapping_boxes_keep_two_live_tracks(self):
        rows = []
        for k in range(40):
            t = round(self.T0 + k / self.FPS, 4)
            ms = int(round(t * 1000))
            rows.append(rung2_obs(ms, 0, 'keypoints', (860, 200, 1060, 900), (0.0, 2.0), t))
            rows.append(rung2_obs(ms, 1, 'person_box', (880, 240, 1040, 860), (0.2, 2.6), t))
        lines, counts = records.run_tracker(rows, params())
        self.assertEqual(counts['tracks'], 2)
        self.assertEqual({l['state'] for l in lines[-6:]}, {'confirmed'})
        self.assertEqual(max(len(l['last_obs_ids']) for l in lines), 1)

    def test_every_person_observation_with_a_position_is_its_own_detection(self):
        rows = [obs('k', 0.0, x=1.0, kind='keypoints'), obs('b', 0.0, x=1.05, kind='person_box')]
        frames, counts = records.frames_from_observations(rows, params())
        self.assertEqual(sorted(d['obs_ids'] for d in frames[0]['detections']), [['b'], ['k']])
        self.assertEqual(counts['no_position'], 0)

    def test_a_box_with_no_position_is_left_out_beside_keypoints(self):
        box = obs('b', 0.0, kind='person_box')
        del box['room_position']
        lines, counts = records.run_tracker([obs('k', 0.0), box], params())
        self.assertEqual(lines[0]['last_obs_ids'], ['k'])
        self.assertEqual(counts['no_position'], 1)

    def test_a_duplicate_box_of_one_person_never_hides_the_person_and_reads_as_doubt(self):
        rows = []
        for k in range(16):
            rows.append(obs('k%02d' % k, k * 0.25, kind='keypoints'))
            rows.append(obs('b%02d' % k, k * 0.25, x=1.05, kind='person_box'))
        lines, counts = records.run_tracker(rows, params())
        self.assertEqual(max(len(l['last_obs_ids']) for l in lines), 1)
        self.assertEqual(counts['no_position'] + counts['rejected'], 0)
        self.assertEqual({l['association']['status'] for l in lines[-2:]}, {'ambiguous'},
                         'two tracks in reach of each other are never confident')


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

    def test_the_tool_has_no_merge_setting_left(self):
        self.assertNotIn('merge_iou', method.read_block()['tracker'])

    def test_truth_written_and_read_back(self):
        p = self.path('truth.jsonl')
        truth.write(p, 't', ['A', 'B'], [(1.0, {'o': 'A'})],
                    [{'crossing_id': 'c', 'type': 'crossing', 'interval': [0.0, 2.0]}])
        t = truth.read(p)
        self.assertEqual((t.take, t.labels), ('t', {'o': 'A'}))


if __name__ == '__main__':
    unittest.main()

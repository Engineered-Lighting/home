"""The binding to live-v1's records, checked against schemas.json itself, and
the den-track-truth/v1 format."""
import copy
import json
import os
import shutil
import tempfile
import unittest

import method
import pipeline
import records
import synth
import truth

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMAS = os.path.join(HERE, '..', '..', 'docs', 'architecture', 'live-v1', 'schemas.json')
VALUES = dict(method.Method().values, room=synth.ROOM_MODEL)
CFG = VALUES['input']


def contract(name):
    with open(SCHEMAS, encoding='utf-8') as f:
        return next(r for r in json.load(f)['records'] if r['name'] == name)


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='den-track-test-')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, lines, newline='\n', bom=False):
        path = os.path.join(self.dir, name)
        with open(path, 'w', encoding='utf-8-sig' if bom else 'utf-8', newline='') as f:
            for rec in lines:
                f.write((json.dumps(rec) if not isinstance(rec, str) else rec) + newline)
        return path


def observation(obs_id, x, y, ts=10.0, kind='keypoints', model='pose', box=(100, 100, 200, 400), **extra):
    rec = {'schema_version': 'observation/v1', 'obs_id': obs_id,
           'source': {'sensor_id': 'cam', 'model': model, 'model_version': '1'},
           'capture_ts': ts, 'kind': kind, 'image_box': list(box) if box else None,
           'room_position': {'x': x, 'y': y, 'z': 1.0,
                             'cov': [[0.01, 0, 0], [0, 0.02, 0], [0, 0, 0.01]]},
           'transform_id': 'cam@1', 'score': 0.9, 'visibility': 'visible', 'validity': 'valid'}
    rec.update(extra)
    return rec


def joints(left_xy, right_xy, hips=True, status='supported'):
    out = [{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(17)]
    cov = [[0.0004, 0, 0], [0, 0.0004, 0], [0, 0, 0.0004]]
    pairs = [(5, 6, 1.4)] + ([(11, 12, 0.95)] if hips else [])
    for left, right, z in pairs:
        out[left] = {'j': left, 'xyz': [left_xy[0], left_xy[1], z], 'cov': cov, 'status': status, 'lift': 'stereo'}
        out[right] = {'j': right, 'xyz': [right_xy[0], right_xy[1], z], 'cov': cov, 'status': status, 'lift': 'stereo'}
    return out


class TrackRecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        obs, _ = synth.records(synth.scenario('near_pass', 1, 'typical'))
        cls.recs = pipeline.track_frames(pipeline.observation_frames(obs, CFG), VALUES)

    def test_field_names_are_the_contracts(self):
        want = [f['name'] for f in contract('Track')['fields']]
        self.assertEqual(set(records.TRACK_FIELDS) | set(records.TRACK_FIELDS_NOT_WRITTEN), set(want))
        time_field = VALUES['record']['time_field']
        for rec in self.recs:
            self.assertEqual(set(rec), set(records.TRACK_FIELDS) | {'schema_version', time_field})
            self.assertNotIn('appearance', rec)

    def test_shapes(self):
        states = set()
        for rec in self.recs:
            states.add(rec['state'])
            self.assertEqual(rec['schema_version'], 'track/v1')
            self.assertIn(rec['state'], ('tentative', 'confirmed', 'coasting', 'ended'))
            self.assertEqual(set(rec['position']), {'x', 'y', 'cov'})
            self.assertEqual(set(rec['velocity']), {'vx', 'vy'})
            self.assertEqual(set(rec['association']), {'status', 'hypotheses', 'since_ts'})
            self.assertIn(rec['association']['status'], ('confident', 'ambiguous'))
            if rec['association']['status'] == 'confident':
                self.assertEqual(rec['association']['hypotheses'], [])
            else:
                self.assertAlmostEqual(sum(h['p'] for h in rec['association']['hypotheses']), 1.0, places=3)
            self.assertEqual(rec['identity'], {'binding': 'unbound', 'person_id': None, 'method': None,
                                               'confidence': None, 'as_of': None})
            if rec['heading'] is not None:
                self.assertEqual(set(rec['heading']), {'deg', 'sd_deg'})
                self.assertIsNotNone(rec['heading_as_of'])
            self.assertIsInstance(rec['last_obs_ids'], list)
        self.assertTrue({'tentative', 'confirmed', 'ended'} <= states)

    def test_records_are_one_json_line_each(self):
        d = tempfile.mkdtemp(prefix='den-track-test-')
        try:
            p = os.path.join(d, 'tracks.jsonl')
            records.write_jsonl(p, self.recs[:5])
            with open(p, 'rb') as f:
                raw = f.read()
            self.assertNotIn(b'\r', raw)
            self.assertEqual(raw.count(b'\n'), 5)
            self.assertEqual(records.read_tracks(p), self.recs[:5])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ObservationTest(TempDir):
    def test_the_contracts_example_is_read(self):
        ex = copy.deepcopy(contract('Observation')['example'])
        ex = {k: v for k, v in ex.items() if not k.endswith('_note')}
        frames, counts = records.read_observations(self.write('obs.jsonl', [ex]), CFG)
        self.assertEqual(counts['used'], 1)
        self.assertEqual(counts.get('unversioned'), 1)
        (ts, sensor, ms), = frames
        self.assertEqual((ts, sensor), (1793066610.42, 'den-oak'))
        self.assertEqual(ms[0]['ids'], ['o-19033042-p7'])
        self.assertEqual(ms[0]['xy'], (1.95, 0.95))
        self.assertAlmostEqual(ms[0]['R'][1][1], 0.009 + CFG['floor_sd_m'] ** 2)
        self.assertIsNone(ms[0]['heading'])          # only a wrist in the example

    def test_what_is_never_used(self):
        lines = [
            observation('face', 0, 2, kind='face_match'),
            observation('vec', 0, 2, kind='appearance_vec'),
            observation('book', 0, 2, kind='object_box', label='book'),
            observation('bad', 0, 2, validity={'status': 'rejected', 'reason': 'implausible depth'}),
            observation('nopos', 0, 2, room_position=None),
            observation('badcov', 0, 2, room_position={'x': 0, 'y': 2, 'z': 1, 'cov': [[0, 0, 0], [0, 0, 0], [0, 0, 0]]}),
            observation('ok', 0, 2),
        ]
        frames, counts = records.read_observations(self.write('obs.jsonl', lines), CFG)
        self.assertEqual([m['ids'] for _, _, ms in frames for m in ms], [['ok']])
        self.assertEqual(counts['skipped_identity_kind'], 2)
        self.assertEqual(counts['skipped_not_a_person_kind'], 1)
        self.assertEqual(counts['skipped_not_valid'], 1)
        self.assertEqual(counts['skipped_no_room_position'], 1)
        self.assertEqual(counts['skipped_bad_covariance'], 1)

    def test_box_and_keypoints_of_one_person_merge(self):
        lines = [observation('k1', 0.0, 2.0, box=(100, 100, 200, 400)),
                 observation('b1', 0.05, 2.1, kind='person_box', model='det', box=(105, 98, 205, 405)),
                 observation('k2', 1.5, 3.0, box=(400, 120, 480, 360)),
                 observation('b3', -1.5, 3.0, kind='person_box', model='det', box=(10, 120, 60, 300))]
        frames, counts = records.read_observations(self.write('obs.jsonl', lines), CFG)
        ms = frames[0][2]
        self.assertEqual(sorted(m['ids'] for m in ms), [['b1', 'k1'], ['b3'], ['k2']])
        merged = next(m for m in ms if m['ids'] == ['b1', 'k1'])
        self.assertEqual(merged['xy'], (0.0, 2.0))                  # the keypoints torso point
        self.assertEqual([x['id'] for x in merged['members']], ['b1'])
        self.assertEqual(counts['merged'], 1)

    def test_last_obs_ids_lists_every_associated_observation(self):
        lines = []
        for k in range(6):
            t = 10.0 + 0.25 * k
            lines.append(observation('k%d' % k, 0.2 * k, 2.0, ts=t, box=(100 + 10 * k, 100, 200 + 10 * k, 400)))
            lines.append(observation('b%d' % k, 0.2 * k, 2.05, ts=t, kind='person_box', model='det',
                                     box=(102 + 10 * k, 98, 203 + 10 * k, 402)))
        frames, _ = records.read_observations(self.write('obs.jsonl', lines), CFG)
        recs = pipeline.track_frames(frames, VALUES)
        self.assertEqual([r['last_obs_ids'] for r in recs], [['b%d' % k, 'k%d' % k] for k in range(6)])

    def test_newer_major_version_is_refused(self):
        with self.assertRaises(records.RecordError):
            records.read_observations(self.write('obs.jsonl', [observation('o', 0, 2, schema_version='observation/v2')]), CFG)

    def test_duplicate_obs_id_is_refused(self):
        with self.assertRaises(records.RecordError):
            records.read_observations(self.write('obs.jsonl', [observation('o', 0, 2), observation('o', 1, 2)]), CFG)

    def test_crlf_and_bom_read_the_same(self):
        lines = [observation('k%d' % k, 0.1 * k, 2.0, ts=10 + 0.25 * k) for k in range(4)]
        lf = self.write('lf.jsonl', lines)
        crlf = self.write('crlf.jsonl', lines, newline='\r\n', bom=True)
        self.assertEqual(records.read_observations(lf, CFG), records.read_observations(crlf, CFG))
        self.assertEqual(records.file_sha256(lf), records.file_sha256(crlf))


class HeadingTest(TempDir):
    H = CFG['heading']

    def test_bearings(self):
        # Facing +y: the right shoulder is toward +x. Facing +x: right is -y.
        for left, right, want in (((-0.2, 2.0), (0.2, 2.0), 0.0), ((1.0, 2.2), (1.0, 1.8), 90.0),
                                  ((0.2, 2.0), (-0.2, 2.0), 180.0), ((1.0, 1.8), (1.0, 2.2), 270.0)):
            deg, sd = records.heading_from_joints(joints(left, right), self.H)
            self.assertAlmostEqual(deg % 360.0, want, places=6)
            self.assertLess(sd, 10.0)

    def test_refused_when_unsupported_too_narrow_or_too_spread(self):
        self.assertIsNone(records.heading_from_joints(joints((-0.2, 2), (0.2, 2), status='uncertain'), self.H))
        self.assertIsNone(records.heading_from_joints(joints((-0.05, 2), (0.05, 2)), self.H))
        wide = joints((-0.2, 2), (0.2, 2), hips=False)
        for j in (5, 6):
            wide[j]['cov'] = [[0.04, 0, 0], [0, 0.04, 0], [0, 0, 0.01]]
        self.assertIsNone(records.heading_from_joints(wide, self.H))
        self.assertIsNone(records.heading_from_joints(None, self.H))

    def test_heading_as_of_moves_only_on_accepted_evidence(self):
        lines = []
        for k in range(8):
            t = 10.0 + 0.25 * k
            j = joints((-0.2 + 0.1 * k, 2.0), (0.2 + 0.1 * k, 2.0)) if k in (1, 2) else None
            lines.append(observation('k%d' % k, 0.1 * k, 2.0, ts=t, joint_set='coco17', joints_room=j))
        frames, _ = records.read_observations(self.write('obs.jsonl', lines), CFG)
        frames = frames[:5] + [(f[0], f[1], []) for f in frames[5:]]     # then nobody is seen
        recs = pipeline.track_frames(frames, VALUES)
        self.assertEqual([r['heading_as_of'] for r in recs if r['state'] != 'ended'],
                         [None, 10.25, 10.5, 10.5, 10.5, 10.5, 10.5, 10.5])
        self.assertEqual(recs[-1]['heading']['deg'], 0.0)


class TruthTest(TempDir):
    def good(self):
        return [truth.take_line('t1', ['A', 'B']),
                truth.frame_line('cam', 10.0, [('o1', 'A'), ('o2', 'B'), ('o3', None), ('o4', '?')]),
                truth.window_line('c1', 'crossing', 9.0, 12.0)]

    def test_reads(self):
        t = truth.read(self.write('truth.jsonl', self.good()))
        self.assertEqual(t['labels'], {'o1': (10.0, 'A'), 'o2': (10.0, 'B'), 'o3': (10.0, None)})
        self.assertEqual(t['unsure'], 1)
        self.assertEqual(t['windows'], [{'id': 'c1', 'kind': 'crossing', 'start': 9.0, 'end': 12.0}])

    def test_crlf_reads_the_same(self):
        a = truth.read(self.write('a.jsonl', self.good()))
        b = truth.read(self.write('b.jsonl', self.good(), newline='\r\n', bom=True))
        self.assertEqual(a, b)

    def test_people_are_letters_never_names(self):
        lines = self.good()
        lines[0]['people'] = ['Alex', 'B']
        with self.assertRaises(truth.TruthError):
            truth.read(self.write('truth.jsonl', lines))

    def test_refusals(self):
        def bad(change):
            lines = self.good()
            change(lines)
            with self.assertRaises(truth.TruthError):
                truth.read(self.write('truth.jsonl', lines))
        bad(lambda l: l[1]['labels'].append({'obs_id': 'o5', 'person': 'C'}))          # not in people
        bad(lambda l: l[1]['labels'].append({'obs_id': 'o1', 'person': 'A'}))          # labelled twice
        bad(lambda l: l.append(truth.window_line('c2', 'crossing', 11.0, 13.0)))       # overlaps c1
        bad(lambda l: l.append(truth.window_line('c3', 'dance', 20.0, 21.0)))          # unknown kind
        bad(lambda l: l.insert(0, truth.take_line('t0', ['A'])))                       # two take lines
        bad(lambda l: l[0].update(truth_version='den-track-truth/v2'))


if __name__ == '__main__':
    unittest.main()

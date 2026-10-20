import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

import head3d_eval
import room_mapper
from test_head3d import scene
from test_room_mapper import CALIBRATION, bundle_bytes, metadata


def site_map(path, anchors, depth=None):
    # Floor-only registration: level camera 1.2 m above the floor.
    from spatial_validation import mount_signature
    floor = {'normal_camera': [0.0, -1.0, 0.0], 'camera_height_m': 1.2}
    if depth is not None:
        floor['mount_signature'] = mount_signature(depth)
    state = {'anchors': anchors, 'floor': floor, 'saved': []}
    document, _ = room_mapper.build_site_map(state, revision=1)
    Path(path).write_text(json.dumps(document), encoding='utf-8')
    return np.asarray(document['model']['meta']['camera_registration']['matrix'])


class LedgerEvaluationTests(unittest.TestCase):
    def test_sessions_without_bundles_are_reported_and_recorded_ones_evaluated(self):
        model = room_mapper.rgbd_model(metadata(), CALIBRATION)
        centre = [0.2, 0.0, 1.6]
        pose, depth, forward = scene(model, centre, 45.0)
        matrix = room_mapper.floor_transform({'normal_camera': [0.0, -1.0, 0.0], 'camera_height_m': 1.2})
        monitor = matrix[:3, :3] @ (np.asarray(centre)+1.5*forward)+matrix[:3, 3]
        anchors = {'monitor': room_mapper.anchor_record('monitor', 'mesh', 'den', monitor, radius_m=0.3),
                   'painting': room_mapper.anchor_record('painting', 'mesh', 'den', [-1.5, 3.0, 1.6])}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_map(root/'map.json', anchors)
            ledger = root/'lab.sqlite3'
            db = sqlite3.connect(ledger)
            db.executescript("""CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL);
                CREATE TABLE events(id INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, payload TEXT);
                CREATE TABLE media(id TEXT PRIMARY KEY, session_id TEXT, metadata TEXT);""")
            db.execute("INSERT INTO sessions VALUES ('aaaa0001', 1), ('bbbb0002', 2)")
            meta = metadata(100.0)
            db.execute('INSERT INTO media VALUES (?,?,?)', ('bundle1', 'bbbb0002', json.dumps(
                {'stream': 'rgbd_bundle', 'capture_epoch': 'epoch-1', 'source_metadata': meta, 'calibration': CALIBRATION})))
            db.execute('INSERT INTO media VALUES (?,?,?)', ('rgb1', 'bbbb0002', json.dumps(
                {'stream': 'rgb', 'capture_epoch': 'epoch-1', 'sequence': meta['frames']['rgb']['sequence'],
                 'source_timing_receipt': {'frame_device_ns': int(100.0*1e9)}})))
            for arm, t in (('a', 5.0), ('a', 5.5), ('a', 6.0)):
                db.execute('INSERT INTO events (session_id,kind,payload) VALUES (?,?,?)', ('bbbb0002', 'perception.output', json.dumps(
                    {'arm': arm, 'observed_at': t, 'media_ids': ['rgb1'],
                     'result': {'pose_instances': [pose], 'objects': [{'label': 'person'}]}})))
            db.commit()
            db.close()
            (root/'media'/'bbbb0002').mkdir(parents=True)
            (root/'media'/'bbbb0002'/'bundle1.bin').write_bytes(bundle_bytes(depth, 100.0))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                args = type('Args', (), {'map': str(root/'map.json'), 'ledger': str(ledger), 'expect': 'monitor',
                                         'sessions': ['aaaa', 'bbbb']})
                report = head3d_eval.cmd_ledger(args)
        self.assertEqual(report['aaaa0001'], {'status': 'no_depth_bundles'})
        self.assertIn('no depth bundles recorded', out.getvalue())
        arm = report['bbbb0002']['arms']['a']
        self.assertEqual(arm['looking_at'], {'monitor': 3})
        self.assertEqual(arm['stability'].get('steady'), 1)
        self.assertEqual(arm['expect']['hit_fraction_all'], 1.0)

    def test_bundle_matching_prefers_the_same_rgb_frame(self):
        bundles = [{'id': 'x', 'epoch': 'e', 'sequence': 10, 'device_s': 1.0},
                   {'id': 'y', 'epoch': 'e', 'sequence': 11, 'device_s': 1.03}]
        self.assertEqual(head3d_eval.match_bundle({'capture_epoch': 'e', 'sequence': 11, 'device_s': 9.0}, bundles)['id'], 'y')
        self.assertEqual(head3d_eval.match_bundle({'capture_epoch': 'e', 'sequence': 99, 'device_s': 1.01}, bundles)['id'], 'x')
        self.assertIsNone(head3d_eval.match_bundle({'capture_epoch': 'e', 'sequence': 99, 'device_s': 2.0}, bundles))
        self.assertIsNone(head3d_eval.match_bundle({'capture_epoch': 'f', 'sequence': 10, 'device_s': 1.0}, bundles))


class LiveEvaluationTests(unittest.TestCase):
    def test_live_pairs_new_outputs_with_ringed_bundles_and_writes_a_summary(self):
        from unittest import mock
        model = room_mapper.rgbd_model(metadata(), CALIBRATION)
        centre = [0.2, 0.0, 1.6]
        pose, depth, forward = scene(model, centre, 45.0)
        matrix = room_mapper.floor_transform({'normal_camera': [0.0, -1.0, 0.0], 'camera_height_m': 1.2})
        monitor = matrix[:3, :3] @ (np.asarray(centre)+1.5*forward)+matrix[:3, 3]
        anchors = {'monitor': room_mapper.anchor_record('monitor', 'mesh', 'den', monitor, radius_m=0.3)}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_map(root/'map.json', anchors, depth)
            ledger = root/'lab.sqlite3'
            db = sqlite3.connect(ledger, check_same_thread=False)
            db.executescript("""CREATE TABLE events(id INTEGER PRIMARY KEY, kind TEXT, payload TEXT);
                CREATE TABLE media(id TEXT PRIMARY KEY, metadata TEXT);""")
            db.commit()
            counter = iter(range(10**6))

            class Client:
                def _read(self, path):
                    if path == '/calibration':
                        return json.dumps(CALIBRATION).encode(), ''
                    i = next(counter)
                    stamp = 100.0+0.25*i
                    if i % 4 == 3 and i < 40:
                        db.execute('INSERT INTO media VALUES (?,?)', ('rgb%d' % i, json.dumps(
                            {'stream': 'rgb', 'capture_epoch': 'epoch-1', 'source_timing_receipt': {'frame_device_ns': int(stamp*1e9)}})))
                        db.execute('INSERT INTO events (kind,payload) VALUES (?,?)', ('perception.output', json.dumps(
                            {'arm': 'b', 'observed_at': 1000.0+0.25*i, 'media_ids': ['rgb%d' % i], 'result': {'pose_instances': [pose]}})))
                        db.commit()
                    return bundle_bytes(depth, stamp), ''

            reader = room_mapper.CameraReader
            try:
                with mock.patch.object(room_mapper, 'CameraReader', lambda env: reader(env, client=Client())), \
                        mock.patch.object(room_mapper.time, 'sleep', lambda s: None), contextlib.redirect_stdout(io.StringIO()) as out:
                    code = head3d_eval.main(['--dir', str(root/'room-map'), '--ledger', str(ledger), 'live',
                                             '--map', str(root/'map.json'), '--seconds', '1.5', '--expect', 'monitor'])
            finally:
                db.close()
            self.assertEqual(code, 0, out.getvalue())
            summaries = list((root/'room-map'/'eval').glob('head3d-live-*.json'))
            self.assertEqual(len(summaries), 1)
            summary = json.loads(summaries[0].read_text(encoding='utf-8'))
        self.assertIn('mount check', out.getvalue())
        arm = summary['arms']['b']
        self.assertGreaterEqual(arm['frames'], 3)
        self.assertEqual(arm['looking_at'], {'monitor': arm['frames']})
        self.assertEqual(arm['expect']['hit_fraction_all'], 1.0)
        self.assertGreaterEqual(arm['stability'].get('steady', 0), 1)
        self.assertIn("'status': 'unchanged'", out.getvalue())
        self.assertEqual(summary['provenance']['kind'], 'head_pose_not_eye_gaze')


if __name__ == '__main__':
    unittest.main()

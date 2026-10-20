import contextlib
import io
import json
import math
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import head3d
import room_mapper
import room_mesh
from test_room_mesh import camera_pose, raycast_room, room_den_mesh, scan_from_den

K_LEFT = [[396.8, 0, 315.79], [0, 396.81, 212.4], [0, 0, 1]]
K_RGB = [[485.78, 0, 312.6], [0, 485.47, 182.76], [0, 0, 1]]
IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
# Minimal EEPROM: left(1) -> right(2) -> RGB(0), translations in cm (RGB 3.75 cm left of left).
EEPROM = {'cameraData': [[0, {'extrinsics': {'toCameraSocket': -1}}],
                         [1, {'extrinsics': {'toCameraSocket': 2, 'rotationMatrix': IDENTITY, 'translation': {'x': -7.5, 'y': 0, 'z': 0}}}],
                         [2, {'extrinsics': {'toCameraSocket': 0, 'rotationMatrix': IDENTITY, 'translation': {'x': 3.75, 'y': 0, 'z': 0}}}]],
          'stereoRectificationData': {'leftCameraSocket': 1, 'rightCameraSocket': 2, 'rectifiedRotationLeft': IDENTITY}}
CALIBRATION = {'session': 'epoch-1', 'depth_alignment': 'rectified_left', 'depth_units': 'millimetres', 'eeprom': EEPROM}
TRUTH = camera_pose([2.5, 0.4, 1.3], 90.0, 15.0, 2.0)   # camera -> synthetic den (test_room_mesh room)


def metadata(stamp=100.0):
    frame = lambda k, w, h: {'intrinsics': k, 'width': w, 'height': h, 'transformation_valid': True,
                             'device_timestamp_seconds': stamp, 'sequence': int(stamp*30),
                             'source_distortion_coefficients': [0.0]*14}
    return {'schema': 1, 'session': 'epoch-1', 'depth_alignment': 'rectified_left', 'depth_units': 'millimetres',
            'invalid_depth': 0, 'frames': {'depth': frame(K_LEFT, 640, 400), 'rgb': frame(K_RGB, 640, 360),
                                           'left': frame(K_LEFT, 640, 400), 'right': frame(K_LEFT, 640, 400)}}


def render_room(pose=TRUTH, extra_spheres=()):
    v, u = np.mgrid[0:400, 0:640]
    rays = np.stack([(u-315.79)/396.8, (v-212.4)/396.81, np.ones(u.shape)], axis=-1).reshape(-1, 3)
    z = raycast_room(np.repeat(pose[None, :3, 3], len(rays), axis=0), rays @ pose[:3, :3].T)
    for centre, radius in extra_spheres:
        c = np.asarray(centre, dtype=float)
        b, n2 = rays @ c, (rays**2).sum(axis=1)
        disc = b*b-n2*(c @ c-radius*radius)
        t = np.where(disc >= 0, (b-np.sqrt(np.maximum(disc, 0)))/n2, np.inf)
        z = np.minimum(z, t)
    return np.round(z.reshape(400, 640)*1000).astype(np.uint16)


def bundle_bytes(depth, stamp=100.0):
    buffer = io.BytesIO()
    np.savez(buffer, depth_mm=depth, left=np.zeros((400, 640), np.uint8), right=np.zeros((400, 640), np.uint8),
             rgb_nv12=np.full((540, 640), 128, np.uint8),
             metadata_json=np.frombuffer(json.dumps(metadata(stamp)).encode(), dtype=np.uint8))
    return buffer.getvalue()


def den_to_camera(point):
    inverse = np.linalg.inv(TRUTH)
    return inverse[:3, :3] @ np.asarray(point, dtype=float)+inverse[:3, 3]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = room_mapper.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class HelperTests(unittest.TestCase):
    def test_median_depth_fills_flicker_and_keeps_true_holes(self):
        frames = np.array([[[1000, 0, 0]], [[1010, 2000, 0]], [[990, 2000, 0]]], dtype=np.uint16)
        depth, count, mad = room_mapper.median_depth(frames)
        np.testing.assert_array_equal(depth, [[1000, 2000, 0]])
        np.testing.assert_array_equal(count, [[3, 2, 0]])
        self.assertEqual(mad[0, 0], 10)

    def test_nv12_gray_and_color(self):
        nv12 = np.full((540, 640), 128, np.uint8)
        nv12[:360] = 126
        rgb = room_mapper.nv12_to_rgb(nv12)
        self.assertEqual(rgb.shape, (360, 640, 3))
        self.assertTrue(np.all(np.abs(rgb.astype(int)-128) <= 2))

    def test_floor_is_the_lowest_dense_plane_not_the_bigger_table(self):
        rng = np.random.default_rng(0)
        level = room_mesh.level_rotation([0.0, -math.cos(math.radians(20)), -math.sin(math.radians(20))])
        def plane(height, count, extent):
            xy = rng.uniform(-extent, extent, (count, 2))+[0, 2.0]
            room = np.c_[xy, np.full(count, -height)+rng.normal(0, 0.004, count)]
            return room @ level  # level -> camera (rows are camera axes)
        points = np.vstack([plane(1.25, 3000, 1.5), plane(0.55, 6000, 0.6)])
        floor = room_mapper.fit_floor(points)
        self.assertAlmostEqual(floor['camera_height_m'], 1.25, delta=0.01)
        self.assertAlmostEqual(floor['tilt_down_deg'], 20.0, delta=0.5)
        self.assertAlmostEqual(floor['roll_deg'], 0.0, delta=0.5)
        # The floor-only frame puts floor points at z=0.
        placed = room_mesh.apply(room_mapper.floor_transform(floor), points[:3000])
        self.assertLess(np.abs(placed[:, 2]).max(), 0.03)

    def test_names_rays_wrists_and_pairing(self):
        self.assertEqual(room_mapper.category_of('window_2'), 'window')
        with self.assertRaises(ValueError):
            room_mapper.category_of('tv')
        point, misses, angle = room_mapper.triangulate([([0, 0, 0], [1, 1, 0]), ([2, 0, 0], [-1, 1, 0])])
        np.testing.assert_allclose(point, [1, 1, 0], atol=1e-9)
        self.assertAlmostEqual(angle, 90.0)
        joints = {head3d.LEFT_SHOULDER: (300, 100), head3d.LEFT_WRIST: (310, 180), head3d.LEFT_ELBOW: (305, 140),
                  head3d.RIGHT_SHOULDER: (340, 100), head3d.RIGHT_WRIST: (460, 60), head3d.RIGHT_ELBOW: (400, 80)}
        self.assertEqual(room_mapper.select_wrist(joints), (head3d.RIGHT_WRIST, head3d.RIGHT_ELBOW))
        self.assertEqual(room_mapper.select_wrist(joints, 'left'), (head3d.LEFT_WRIST, head3d.LEFT_ELBOW))
        bundles = [{'meta': metadata(t)} for t in (10.0, 10.25, 10.5)]
        chosen, skew = room_mapper.pair_bundle({'capture_epoch': 'epoch-1', 'device_s': 10.3}, bundles)
        self.assertEqual(chosen['meta']['frames']['rgb']['device_timestamp_seconds'], 10.25)
        self.assertEqual(room_mapper.pair_bundle({'capture_epoch': 'other', 'device_s': 10.3}, bundles)[0], None)
        self.assertEqual(room_mapper.pair_bundle({'capture_epoch': 'epoch-1', 'device_s': 12.0}, bundles)[0], None)


class WorkflowTests(unittest.TestCase):
    """capture (synthetic) -> floor -> import-mesh -> register -> anchors -> review -> grid -> save."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)/'room-map'
        vertices, faces = room_den_mesh()
        self.scan = scan_from_den()
        scan_vertices = room_mesh.apply(self.scan, vertices)
        self.obj = Path(self.tmp.name)/'den-scan.obj'
        with open(self.obj, 'w', encoding='ascii') as target:
            for v in scan_vertices:
                target.write('v %.5f %.5f %.5f\n' % tuple(v))
            for f in faces+1:
                target.write('f %d %d %d\n' % tuple(f))
        workspace = room_mapper.Workspace(self.dir).ensure()
        state = workspace.load_state()
        bundle = room_mapper.decode_bundle(bundle_bytes(render_room()))
        snapshot_id, summary = room_mapper.save_snapshot(workspace, [bundle]*3, CALIBRATION, snapshot_id='t1')
        state['snapshots'][snapshot_id] = summary
        state['latest_snapshot'] = snapshot_id
        workspace.save_state(state)

    def tearDown(self):
        self.tmp.cleanup()

    def scan_point(self, den_point):
        return ','.join('%.5f' % v for v in room_mesh.apply(self.scan, [den_point])[0])

    def state(self):
        return json.loads((self.dir/'state.json').read_text(encoding='utf-8'))

    def test_full_mapping_session(self):
        code, out, err = run('--dir', str(self.dir), 'floor')
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(self.state()['floor']['camera_height_m'], 1.3, delta=0.01)
        code, out, err = run('--dir', str(self.dir), 'import-mesh', str(self.obj))
        self.assertEqual(code, 0, err)
        self.assertIn('floor footprint 5.0', out)
        # Tape check between two picked scan points (desk top width 1.2 m).
        code, out, _ = run('--dir', str(self.dir), 'measure', '--from', self.scan_point([3.2, 2.4, 0.75]),
                           '--to', self.scan_point([4.4, 2.4, 0.75]), '--tape', '1.2')
        self.assertIn('PASS', out)
        # Rough OAK position and a look-at point, both clicked in scan coordinates (labeller hints).
        scan = lambda p: [float(v) for v in room_mesh.apply(self.scan, [p])[0]]
        hints = Path(self.tmp.name)/'hints.json'
        hints.write_text(json.dumps({'frame': 'scan', 'anchors': [{'name': 'oak_camera', 'point': scan([2.8, 0.2, 1.3])},
                                                                   {'name': 'oak_looks_at', 'point': scan([2.0, 3.5, 1.0])}]}))
        code, out, err = run('--dir', str(self.dir), 'register', '--labels', str(hints))
        self.assertEqual(code, 0, err)
        self.assertIn('registration ACCEPTED', out)
        state = self.state()
        t_mesh = np.asarray(state['mesh']['T_mesh'])
        truth = t_mesh @ self.scan @ TRUTH        # camera -> computed den frame
        matrix = np.asarray(state['registration']['matrix'])
        moved, turned = room_mesh._pose_distance(matrix, truth)
        self.assertLess(moved, 0.02)
        self.assertLess(turned, 1.0)
        # Anchors labelled on the mesh (scan frame): a point and a clicked box.
        labels = {'frame': 'scan', 'anchors': [{'name': 'oak_camera', 'point': scan([2.8, 0.2, 1.3])},
            {'name': 'monitor', 'point': [float(v) for v in room_mesh.apply(self.scan, [[4.9, 1.0, 1.1]])[0]]},
            {'name': 'bookshelf', 'points': [[float(v) for v in room_mesh.apply(self.scan, [p])[0]]
                                             for p in ([0.1, 3.0, 0.0], [1.0, 3.3, 1.8])]}]}
        path = Path(self.tmp.name)/'labels.json'
        path.write_text(json.dumps(labels), encoding='utf-8')
        code, out, err = run('--dir', str(self.dir), 'anchors-import', str(path))
        self.assertEqual(code, 0, err)
        # Pixel anchor on the far wall, seen through the RGB camera.
        model = room_mapper.rgbd_model(metadata(), CALIBRATION)
        wall = [1.5, 4.0, 1.4]
        pixel = room_mapper.project_room_points([wall], TRUTH, model)[0]
        code, out, err = run('--dir', str(self.dir), 'anchor', 'painting', '--pixel', '%.1f,%.1f' % tuple(pixel))
        self.assertEqual(code, 0, err)
        code, out, err = run('--dir', str(self.dir), 'anchor', 'painting', '--pixel', '%.1f,%.1f' % (pixel[0]+2, pixel[1]), '--check')
        self.assertIn('PASS', out)
        # Box anchor around the desk front and a tape-measured window.
        corners = room_mapper.project_room_points([[3.3, 2.4, 0.1], [4.3, 2.4, 0.7]], TRUTH, model)
        box = [min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners)]
        code, out, err = run('--dir', str(self.dir), 'anchor', 'desk', '--box', ','.join('%.1f' % v for v in box))
        self.assertEqual(code, 0, err)
        code, out, err = run('--dir', str(self.dir), 'anchor', 'window', '--measured', '1.0,2.0,0.3')
        self.assertEqual(code, 0, err)
        code, out, err = run('--dir', str(self.dir), 'review', '--json')
        review = json.loads(out)
        self.assertEqual(review['registration'], 'mesh_icp')
        rows = {r['name']: r for r in review['anchors']}
        to_den = lambda p: room_mesh.apply(t_mesh @ self.scan, [p])[0]
        self.assertLess(np.linalg.norm(np.asarray(rows['painting']['pos'])-to_den(wall)), 0.04)
        self.assertLess(np.linalg.norm(np.asarray(rows['monitor']['pos'])-to_den([4.9, 1.0, 1.1])), 1e-3)
        np.testing.assert_allclose(sorted(rows['bookshelf']['size_m']), sorted([0.9, 0.3, 1.8]), atol=1e-3)
        self.assertLess(abs(rows['desk']['pos'][2]-to_den([3.8, 2.4, 0.4])[2]), 0.08)
        self.assertIsNotNone(rows['painting']['rgb_pixel'])
        # Pixel grid JPEG stays in the private directory.
        code, out, err = run('--dir', str(self.dir), 'grid')
        self.assertEqual(code, 0, err)
        self.assertTrue((self.dir/'grid-t1.jpg').is_file())
        # Versioned site map, bound like room_context expects.
        code, out, err = run('--dir', str(self.dir), 'save', '--note', 'test')
        self.assertEqual(code, 0, err)
        room_map, camera_to_room, document = room_mapper.load_site_map(self.dir/'site-map-r001.json')
        self.assertEqual(room_map['revision'], '1')
        self.assertEqual(document['binding']['map_id'], 'victoria-den')
        words = {t['label']: t for t in room_map['targets']}
        self.assertEqual(set(words), {'monitor', 'bookshelf', 'painting', 'desk', 'window'})
        self.assertEqual(words['monitor']['category'], 'monitor')
        self.assertEqual(words['bookshelf']['shape'], 'box')
        np.testing.assert_allclose(camera_to_room, matrix, atol=1e-5)
        # A head in the room facing the stored monitor anchor reads 'monitor'.
        head = {'head_m': [2.5, 2.0, 1.2], 'forward': list(np.asarray(words['monitor']['pos'])-[2.5, 2.0, 1.2]), 'pitch_deg': 0.0}
        head['forward'] = list(np.asarray(head['forward'])/np.linalg.norm(head['forward']))
        self.assertEqual(head3d.look_target(head, room_map['targets'])['looking_at'], 'monitor')
        code, out, err = run('--dir', str(self.dir), 'save')
        self.assertTrue((self.dir/'site-map-r002.json').is_file())

    def test_refused_registration_is_not_stored_and_errors_are_clean(self):
        run('--dir', str(self.dir), 'floor')
        run('--dir', str(self.dir), 'import-mesh', str(self.obj))
        code, out, err = run('--dir', str(self.dir), 'register', '--camera-at', self.scan_point([2.8, 0.2, 1.3]),
                             '--heading', '0', '--min-inliers', '0.999')
        self.assertIn('REFUSED', out)
        self.assertNotIn('registration', self.state())
        code, out, err = run('--dir', str(self.dir), 'anchor', 'tv', '--pixel', '1,1')
        self.assertEqual(code, 2)
        self.assertIn('unsupported anchor name', err)
        code, out, err = run('--dir', str(self.dir), 'save')
        self.assertEqual(code, 2)


class PointingTests(unittest.TestCase):
    def test_hold_anchor_uses_the_ledger_wrist_and_the_paired_bundle(self):
        hand = den_to_camera([1.5, 3.0, 1.0])  # owner's hand at the object, in the camera frame
        model = room_mapper.rgbd_model(metadata(), CALIBRATION)
        transform = model['rgb_from_left']
        from depth_association import project
        rgb = lambda p: [float(v) for v in project((transform[:3, :3] @ np.asarray(p)+transform[:3, 3])[None, :],
                                                   model['rgb_k'], model['rgb_distortion'])[0][0]]
        elbow = hand+np.array([0.0, 0.05, -0.3])
        depth = render_room(extra_spheres=[(hand+0.03*hand/np.linalg.norm(hand), 0.03), (elbow, 0.04)])
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)/'lab.sqlite3'
            db = sqlite3.connect(ledger)
            db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT, payload TEXT)')
            db.execute('CREATE TABLE media (id TEXT PRIMARY KEY, metadata TEXT)')
            db.commit()
            stamps = iter(100.0+0.25*i for i in range(1000))
            pose = {'joints': [{'joint_id': head3d.RIGHT_WRIST, 'pixel': rgb(hand), 'status': 'supported'},
                               {'joint_id': head3d.RIGHT_ELBOW, 'pixel': rgb(elbow), 'status': 'supported'},
                               {'joint_id': head3d.RIGHT_SHOULDER, 'pixel': [320, 60], 'status': 'supported'}]}

            class Client:
                def _read(self, path):
                    if path == '/calibration':
                        return json.dumps(CALIBRATION).encode(), ''
                    stamp = next(stamps)
                    media = 'm%d' % int(stamp*100)
                    db.execute('INSERT INTO media VALUES (?,?)', (media, json.dumps(
                        {'stream': 'rgb', 'capture_epoch': 'epoch-1', 'source_timing_receipt': {'frame_device_ns': int(stamp*1e9)}})))
                    db.execute('INSERT INTO events (kind,payload) VALUES (?,?)', ('perception.output', json.dumps(
                        {'arm': 'a', 'observed_at': __import__('time').time(), 'media_ids': [media],
                         'result': {'pose_instances': [pose]}})))
                    db.commit()
                    return bundle_bytes(depth, stamp), ''

            state = {'anchors': {}, 'pending_rays': {}}
            args = types.SimpleNamespace(camera_env='unused', ledger=str(ledger), hold=0.3, settle=0.0, wrist='auto', mode='hold',
                             name='painting', radius_m=None)
            reader = room_mapper.CameraReader
            try:
                with mock.patch.object(room_mapper, 'CameraReader', lambda env: reader(env, client=Client())), \
                        mock.patch.object(room_mapper.time, 'sleep', lambda s: None), contextlib.redirect_stdout(io.StringIO()):
                    record = room_mapper.pointing_anchor(args, state)
            finally:
                db.close()
        self.assertEqual(record['method'], 'pointing_hold')
        self.assertGreaterEqual(record['samples'], 2)
        # Wrist surface plus 8 cm along the forearm: within 10 cm of the hand.
        self.assertLess(np.linalg.norm(np.asarray(record['xyz'])-hand), 0.10)


if __name__ == '__main__':
    unittest.main()

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

import room_mesh

ROOM = (5.0, 4.0, 2.5)
# Asymmetric furniture: a desk in one corner, a tall bookshelf on another wall.
FURNITURE = [((3.2, 2.4, 0.0), (4.4, 3.0, 0.75)), ((0.1, 3.0, 0.0), (1.0, 3.3, 1.8))]


def box_mesh(lo, hi, inward=False):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    faces = []
    for a, b, c, d in quads:
        tri = [(a, b, c), (a, c, d)]
        faces += [t[::-1] for t in tri] if inward else tri
    return v, np.array(faces)


def room_den_mesh():
    parts = [box_mesh((0, 0, 0), ROOM, inward=True)]+[box_mesh(lo, hi) for lo, hi in FURNITURE]
    vertices, faces, total = [], [], 0
    for v, f in parts:
        vertices.append(v)
        faces.append(f+total)
        total += len(v)
    return np.concatenate(vertices), np.concatenate(faces)


def scan_from_den():
    """A Y-up scan transform: den (Z up) -> glTF Y-up, yawed 30 deg and offset."""
    to_y_up = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])  # (x, y, z) -> (x, z, -y)
    yaw = room_mesh.rot_z(math.radians(30))
    m = np.eye(4)
    m[:3, :3] = to_y_up @ yaw
    m[:3, 3] = [1.5, -0.8, 2.0]
    return m


def raycast_room(origins, directions):
    """Distance to the first surface of the box room and furniture for rays inside the room."""
    d = np.where(np.abs(directions) < 1e-12, 1e-12, directions)
    lo, hi = np.zeros(3), np.array(ROOM)
    t_exit = np.min(np.maximum((lo-origins)/d, (hi-origins)/d), axis=1)
    best = t_exit
    for flo, fhi in FURNITURE:
        t1, t2 = (np.array(flo)-origins)/d, (np.array(fhi)-origins)/d
        near, far = np.max(np.minimum(t1, t2), axis=1), np.min(np.maximum(t1, t2), axis=1)
        hit = (near <= far) & (near > 0)
        best = np.where(hit & (near < best), near, best)
    return best


def camera_pose(position, heading_deg, tilt_down_deg, roll_deg):
    """camera (x right, y down, z forward) -> den."""
    level = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])  # camera -> (right, forward, up), level
    tilt = np.array([[1, 0, 0], [0, math.cos(math.radians(-tilt_down_deg)), -math.sin(math.radians(-tilt_down_deg))],
                     [0, math.sin(math.radians(-tilt_down_deg)), math.cos(math.radians(-tilt_down_deg))]])
    roll = np.array([[math.cos(math.radians(roll_deg)), 0, -math.sin(math.radians(roll_deg))], [0, 1, 0],
                     [math.sin(math.radians(roll_deg)), 0, math.cos(math.radians(roll_deg))]])
    m = np.eye(4)
    m[:3, :3] = room_mesh.rot_z(math.radians(heading_deg-90)) @ tilt @ roll @ level
    m[:3, 3] = position
    return m


def oak_points(pose, noise_m=0.004, seed=1):
    v, u = np.mgrid[0:400:4, 0:640:4]
    rays = np.stack([(u-315.79)/396.8, (v-212.4)/396.81, np.ones(u.shape)], axis=-1).reshape(-1, 3)
    world = rays @ pose[:3, :3].T
    t = raycast_room(np.repeat(pose[None, :3, 3], len(rays), axis=0), world)
    z = t+np.random.default_rng(seed).normal(0, noise_m, len(t))
    return rays*z[:, None]


def write_glb(path, vertices, faces, node):
    positions = np.asarray(vertices, dtype='<f4').tobytes()
    indices = np.asarray(faces, dtype='<u4').tobytes()
    binary = positions+indices
    gltf = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [node],
            'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
            'buffers': [{'byteLength': len(binary)}],
            'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(positions)},
                            {'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(indices)}],
            'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(vertices), 'type': 'VEC3'},
                          {'bufferView': 1, 'componentType': 5125, 'count': faces.size, 'type': 'SCALAR'}]}
    text = json.dumps(gltf).encode()
    text += b' '*(-len(text) % 4)
    binary += b'\0'*(-len(binary) % 4)
    body = struct.pack('<II', len(text), 0x4E4F534A)+text+struct.pack('<II', len(binary), 0x004E4942)+binary
    Path(path).write_bytes(struct.pack('<III', 0x46546C67, 2, 12+len(body))+body)


class LoaderTests(unittest.TestCase):
    def test_glb_applies_node_transforms_and_obj_triangulates(self):
        vertices, faces = box_mesh((0, 0, 0), (1, 2, 3))
        with tempfile.TemporaryDirectory() as tmp:
            glb = Path(tmp)/'scan.glb'
            # Rotate 90 deg about Y (quaternion x,y,z,w) and translate.
            s = math.sqrt(0.5)
            write_glb(glb, vertices, faces, {'mesh': 0, 'rotation': [0, s, 0, s], 'translation': [1, 2, 3]})
            loaded, loaded_faces = room_mesh.load_mesh(glb)
            expected = vertices @ np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]]).T+[1, 2, 3]
            np.testing.assert_allclose(loaded, expected, atol=1e-6)
            np.testing.assert_array_equal(loaded_faces, faces)
            obj = Path(tmp)/'scan.obj'
            obj.write_text('# quad and a negative-index triangle\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n'
                           'vt 0 0\nf 1/1 2/1 3/1 4/1\nf -1 -2 -3\n', encoding='ascii')
            v, f = room_mesh.load_mesh(obj)
            self.assertEqual(v.shape, (4, 3))
            np.testing.assert_array_equal(f, [[0, 1, 2], [0, 2, 3], [3, 2, 1]])

    def test_compressed_or_unknown_files_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            glb = Path(tmp)/'draco.glb'
            text = json.dumps({'asset': {'version': '2.0'}, 'extensionsRequired': ['KHR_draco_mesh_compression']}).encode()
            text += b' '*(-len(text) % 4)
            body = struct.pack('<II', len(text), 0x4E4F534A)+text
            glb.write_bytes(struct.pack('<III', 0x46546C67, 2, 12+len(body))+body)
            with self.assertRaisesRegex(ValueError, 'compressed GLB'):
                room_mesh.load_mesh(glb)
            ply = Path(tmp)/'scan.ply'
            ply.write_bytes(b'ply\n')
            with self.assertRaises(ValueError):
                room_mesh.load_mesh(ply)


class FrameTests(unittest.TestCase):
    def test_scan_frame_puts_the_floor_at_zero_with_the_long_axis_on_x(self):
        vertices, faces = room_den_mesh()
        scan = room_mesh.apply(scan_from_den(), vertices)
        frame = room_mesh.scan_frame(scan, faces, up='y')
        placed = room_mesh.apply(frame['T_mesh'], scan)
        self.assertAlmostEqual(placed[:, 2].min(), 0.0, places=3)
        self.assertAlmostEqual(placed[:, 2].max(), 2.5, places=3)
        np.testing.assert_allclose(placed.min(axis=0)[:2], [0, 0], atol=0.03)
        np.testing.assert_allclose(placed.max(axis=0), ROOM, atol=0.03)
        np.testing.assert_allclose(frame['floor_footprint_m'], [5.0, 4.0], atol=0.03)
        self.assertLess(frame['floor_rms_mm'], 1.0)
        # A viewer click in scan coordinates lands at the same den point.
        corner = room_mesh.viewer_to_den(scan[0], 'scan', frame['T_mesh'])
        np.testing.assert_allclose(corner, placed[0], atol=1e-9)
        blender = [scan[0][0], -scan[0][2], scan[0][1]]
        np.testing.assert_allclose(room_mesh.viewer_to_den(blender, 'blender', frame['T_mesh']), placed[0], atol=1e-9)

    def test_wrong_up_axis_is_refused(self):
        vertices, faces = room_den_mesh()
        scan = room_mesh.apply(scan_from_den(), vertices)
        with self.assertRaisesRegex(ValueError, 'floor'):
            room_mesh.scan_frame(scan, faces, up='z')

    def test_voxel_index_finds_the_nearest_representative(self):
        rng = np.random.default_rng(0)
        points = rng.random((5000, 3))
        normals = np.tile([0, 0, 1.0], (5000, 1))
        index = room_mesh.VoxelIndex(points, normals, 0.1)
        queries = rng.random((200, 3))
        found, distance = index.nearest(queries)
        brute = np.sqrt(((queries[:, None, :]-index.points[None, :, :])**2).sum(axis=2))
        within = brute.min(axis=1) <= 0.1
        np.testing.assert_array_equal(found >= 0, within)
        np.testing.assert_allclose(distance[within], brute.min(axis=1)[within])


class RegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vertices, faces = room_den_mesh()
        cls.points, cls.normals = room_mesh.sample_surface(vertices, faces, spacing=0.03)
        cls.truth = camera_pose([2.5, 0.4, 1.3], 90.0, 15.0, 2.0)
        cls.source = oak_points(cls.truth)
        up = cls.truth[:3, :3].T @ np.array([0, 0, 1.0])
        cls.floor = {'normal_camera': list(up), 'camera_height_m': 1.3}

    def check(self, report):
        self.assertEqual(report['status'], 'accepted', report)
        moved, turned = room_mesh._pose_distance(np.asarray(report['matrix']), self.truth)
        self.assertLess(moved, 0.02)
        self.assertLess(turned, 1.0)
        self.assertLess(report['rms_m'], 0.01)
        self.assertGreater(report['inlier_fraction'], 0.9)
        self.assertEqual(report['scale_check']['status'], 'estimated')
        self.assertAlmostEqual(report['scale_check']['scale'], 1.0, delta=0.01)

    def test_rough_position_and_heading_converge(self):
        report = room_mesh.register(self.source, self.points, self.normals, self.floor, [2.8, 0.2], heading_deg=105.0)
        self.check(report)
        self.assertAlmostEqual(report['heading_deg'], 90.0, delta=1.0)
        self.assertAlmostEqual(report['tilt_down_deg'], 15.0, delta=1.0)

    def test_heading_sweep_without_a_heading(self):
        self.check(room_mesh.register(self.source, self.points, self.normals, self.floor, [2.2, 0.7]))

    def test_scale_error_and_garbage_are_reported_or_refused(self):
        # The OAK floor fit sees the same 4% scale error as the rest of the depth.
        floor = {**self.floor, 'camera_height_m': 1.3*1.04}
        scaled = room_mesh.register(self.source*1.04, self.points, self.normals, floor, [2.8, 0.2], heading_deg=90.0)
        self.assertAlmostEqual(scaled['scale_check']['scale'], 1/1.04, delta=0.01)
        self.assertTrue(any('scale' in w for w in scaled['warnings']), scaled['warnings'])
        noise = np.random.default_rng(3).uniform(-3, 3, (5000, 3))+[0, 0, 3.5]
        refused = room_mesh.register(noise, self.points, self.normals, self.floor, [2.8, 0.2], heading_deg=90.0)
        self.assertEqual(refused['status'], 'refused')
        self.assertTrue(refused['reasons'])


if __name__ == '__main__':
    unittest.main()

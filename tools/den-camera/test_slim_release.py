"""The den camera's slim-bundle release (pilot-20261114-slim): what it changes, what it keeps, and its wire.

    python -B tools/den-camera/test_slim_release.py

Stdlib unittest and numpy. No camera: both releases' HTTP servers run on 127.0.0.1 with a fictional token and
fictional frames. One test reads the slim bundle with den-live's own readers (den-take's check.read_bundle and
den-observe's takes.read_frame, by git archive at the commit den-live pins); it skips where that commit is absent.
"""
import ast
import hashlib
import http.client
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / 'tools/fixtures/installed-oak-capture-20261114.py'
EARLIER = ROOT / 'tools/fixtures/installed-oak-capture-20261111.py'
EARLIER_SHA = '15430138f9dffdead4a7d2833cb57514a0162a037d0328931f1b134cee1374cc'   # pilot-20261111-depth, Lab's pin
DEN_TAKE_PIN = '257eca52'   # den-live's pinned den-take and den-observe (tools/den-sim/refs.json on claude/den-live)
TOKEN = 'fictional-token-' + 'x' * 16
# What the slim release may change. Everything else, the clock checks perception-lab reuses included, stays
# byte-identical to pilot-20261111-depth, so the Lab's pin and the receiver-clock proofs it signed stay valid.
CHANGED = {'__doc__', 'SLIM_PATH', 'SLIM_SCHEMA', 'SLIM_COMPRESSLEVEL', 'encode_slim_bundle', 'State', 'serve',
           'capture_session', 'import socket'}
STATE_CHANGED = {'reset', 'publish_slim', 'slim_response'}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def definitions(nodes):
    out = {}
    for index, node in enumerate(nodes):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            key = '__doc__'
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            key = node.name
        elif isinstance(node, ast.Assign):
            key = ','.join(sorted(n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)))
        elif isinstance(node, ast.Import):
            key = 'import ' + ','.join(alias.name for alias in node.names)
        else:
            key = ast.dump(node)
        out[key] = node
    return out


def module_definitions(path):
    return definitions(ast.parse(path.read_text(encoding='utf-8')).body)


def frames(seed=1):
    rng = np.random.default_rng(seed)
    return {'depth_mm': rng.integers(0, 6000, (400, 640), dtype=np.uint16),
            'left': rng.integers(0, 256, (400, 640), dtype=np.uint8),
            'right': rng.integers(0, 256, (400, 640), dtype=np.uint8),
            'rgb_nv12': rng.integers(0, 256, (540, 640), dtype=np.uint8)}


def plane(sequence, t_host, width, height):
    return {'sequence': sequence, 'host_monotonic_timestamp_seconds': t_host, 'device_timestamp_seconds': t_host - 1,
            'exposure_duration_seconds': 0.01, 'width': width, 'height': height, 'type': 'fictional',
            'transformation_valid': True,
            'intrinsics': [[400.0, 0.0, 320.0], [0.0, 400.0, 200.0], [0.0, 0.0, 1.0]]}


def metadata(session, sequence=7, t_host=12.5):
    return {'schema': 1, 'session': session, 'captured_unix_ns': 1_800_000_000_000_000_000,
            'captured_unix_ns_semantics': 'bundle_packaging_time_not_sensor_exposure',
            'depth_alignment': 'rectified_left', 'depth_units': 'millimetres', 'invalid_depth': 0,
            'depth_correction': {'applied': True, 'factor': 0.889, 'measured': '2026-11-11', 'calibration_sha256': 'b' * 64},
            'frames': {'rgb': plane(sequence, t_host, 640, 360), 'depth': plane(sequence // 2, t_host, 640, 400),
                       'left': plane(sequence // 2, t_host, 640, 400), 'right': plane(sequence // 2, t_host, 640, 400)},
            'synchronization': {'fictional': True}}


def members(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return [info.filename for info in archive.infolist()]


def npz(blob):
    loaded = np.load(io.BytesIO(blob), allow_pickle=False)
    return {name: loaded[name] for name in loaded.files}


class ReleaseDiff(unittest.TestCase):
    def test_the_earlier_release_is_the_labs_pin(self):
        self.assertEqual(hashlib.sha256(EARLIER.read_bytes()).hexdigest(), EARLIER_SHA)

    def test_only_the_slim_path_changed_since_the_earlier_release(self):
        old, new = module_definitions(EARLIER), module_definitions(RELEASE)
        self.assertLessEqual(set(new) - set(old), CHANGED)
        self.assertEqual(set(old) - set(new), set())
        for key in set(old) & set(new) - CHANGED:
            with self.subTest(definition=key):
                self.assertEqual(ast.dump(old[key]), ast.dump(new[key]))

    def test_inside_state_only_the_slim_store_changed(self):
        old = definitions(module_definitions(EARLIER)['State'].body)
        new = definitions(module_definitions(RELEASE)['State'].body)
        self.assertEqual(set(new) - set(old), STATE_CHANGED - {'reset'})
        self.assertEqual(set(old) - set(new), set())
        for key in set(old) & set(new) - STATE_CHANGED:
            with self.subTest(method=key):
                self.assertEqual(ast.dump(old[key]), ast.dump(new[key]))


class SlimBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = load(RELEASE, '_slim_release_camera')

    def setUp(self):
        self.arrays, self.meta = frames(), metadata('fictional-session')
        self.slim = self.camera.encode_slim_bundle(self.arrays, self.meta)

    def test_members_are_depth_rgb_and_metadata_only(self):
        self.assertEqual(members(self.slim), ['depth_mm.npy', 'rgb_nv12.npy', 'metadata_json.npy'])

    def test_depth_and_rgb_are_lossless(self):
        got = npz(self.slim)
        for name in ('depth_mm', 'rgb_nv12'):
            with self.subTest(member=name):
                self.assertEqual(got[name].dtype, self.arrays[name].dtype)
                self.assertEqual(got[name].shape, self.arrays[name].shape)
                self.assertTrue(np.array_equal(got[name], self.arrays[name]))

    def test_metadata_is_the_full_bundles_plus_the_schema(self):
        slim_meta = json.loads(bytes(npz(self.slim)['metadata_json']).decode())
        full_meta = json.loads(bytes(npz(self.camera.encode_bundle(self.arrays, self.meta))['metadata_json']).decode())
        self.assertEqual(slim_meta.pop('bundle_schema'), 'den-camera-slim-1')
        self.assertEqual(slim_meta.pop('bundle_omits'), ['left', 'right'])
        self.assertEqual(slim_meta, full_meta)
        self.assertEqual(slim_meta['schema'], 1, "den-take's reader needs metadata schema 1")
        self.assertEqual(set(slim_meta['frames']), {'rgb', 'depth', 'left', 'right'}, "every plane's frame metadata")

    def test_encoding_leaves_the_full_bundle_and_its_metadata_alone(self):
        before = json.dumps(self.meta, sort_keys=True)
        full = self.camera.encode_bundle(self.arrays, self.meta)
        self.assertEqual(json.dumps(self.meta, sort_keys=True), before)
        self.assertEqual(members(full), ['depth_mm.npy', 'left.npy', 'right.npy', 'rgb_nv12.npy', 'metadata_json.npy'])
        earlier = load(EARLIER, '_slim_release_earlier')
        same = npz(earlier.encode_bundle(self.arrays, self.meta))
        for name, value in npz(full).items():
            with self.subTest(member=name):
                self.assertTrue(np.array_equal(value, same[name]))

    def test_member_headers_are_plain_npy_version_1(self):
        with zipfile.ZipFile(io.BytesIO(self.slim)) as archive:
            for name in archive.namelist():
                with self.subTest(member=name):
                    raw = archive.read(name)
                    self.assertEqual(raw[:8], b'\x93NUMPY\x01\x00')

    def test_depth_must_stay_uint16(self):
        bad = dict(self.arrays, depth_mm=self.arrays['depth_mm'].astype(np.float32))
        with self.assertRaisesRegex(ValueError, 'uint16'):
            self.camera.encode_slim_bundle(bad, self.meta)

    def test_it_is_about_half_the_full_bundle(self):
        full = self.camera.encode_bundle(self.arrays, self.meta)
        self.assertLess(len(self.slim), 0.7 * len(full))


class DenLiveReaders(unittest.TestCase):
    """The slim bundle through den-live's own readers, as pinned: den-take's check and den-observe's takes."""

    @classmethod
    def setUpClass(cls):
        cls.tree = Path(tempfile.mkdtemp(prefix='den-camera-slim-readers-'))
        cls.addClassCleanup(shutil.rmtree, cls.tree, ignore_errors=True)
        done = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', DEN_TAKE_PIN,
                               'tools/den-take', 'tools/den-observe'], capture_output=True)
        if done.returncode:
            raise unittest.SkipTest("den-live's pinned den-take (%s) isn't in this checkout" % DEN_TAKE_PIN)
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
            tar.extractall(cls.tree, filter='data')
        for folder in ('tools/den-take', 'tools/den-observe'):
            sys.path.insert(0, str(cls.tree / folder))
        cls.check = load(cls.tree / 'tools/den-take/check.py', 'check')
        cls.takes = load(cls.tree / 'tools/den-observe/takes.py', '_slim_release_takes')
        cls.camera = load(RELEASE, '_slim_release_camera_readers')

    def test_den_take_and_den_observe_read_it_as_they_read_the_full_bundle(self):
        arrays, meta = frames(3), metadata('fictional-session', sequence=41, t_host=99.25)
        slim = self.camera.encode_slim_bundle(arrays, meta)
        full = self.camera.encode_bundle(arrays, meta)
        for name, blob in (('full', full), ('slim', slim)):
            with self.subTest(bundle=name):
                parsed, nv12 = self.check.read_bundle(blob, with_rgb=True)
                self.assertEqual((parsed['session'], parsed['rgb_seq'], parsed['t_host']), ('fictional-session', 41, 99.25))
                self.assertEqual(bytes(nv12), arrays['rgb_nv12'].tobytes())
                frame_meta, depth = self.takes.read_frame(blob)
                self.assertEqual(list(depth.mm), arrays['depth_mm'].ravel().tolist())
                self.assertEqual(frame_meta['raw']['depth_correction'], meta['depth_correction'])
                self.assertEqual(frame_meta['intrinsics']['rectified_left'], meta['frames']['depth']['intrinsics'])


class Wire(unittest.TestCase):
    """Both releases' servers side by side: the slim path is new, and every other path answers as before."""

    @classmethod
    def setUpClass(cls):
        cls.new = load(RELEASE, '_slim_release_wire_new')
        cls.old = load(EARLIER, '_slim_release_wire_old')

    def serve(self, module):
        state = module.State(15)
        arrays, meta = frames(), metadata(state.session)
        if hasattr(state, 'publish_slim'):
            state.publish_slim(module.encode_slim_bundle(arrays, meta), meta['frames']['rgb'])
        state.publish_bundle(module.encode_bundle(arrays, meta), meta['frames']['rgb'], arrays['rgb_nv12'].tobytes(),
                             time.monotonic())
        with state.lock:
            state.encoded_updated = time.monotonic()
            state.calibration = {'session': 'fictional-session', 'fictional': True}
        server = module.serve(state, '127.0.0.1', 0, TOKEN)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return state, server.server_address[1], arrays, meta

    @staticmethod
    def get(port, path, headers=None, connection=None, token=TOKEN):
        connection = connection or http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        sent = {'Authorization': 'Bearer ' + token} if token else {}
        sent.update(headers or {})
        connection.request('GET', path, headers=sent)
        response = connection.getresponse()
        return connection, response, response.read()

    def test_one_connection_carries_many_slim_bundles(self):
        state, port, _arrays, _meta = self.serve(self.new)
        connection, first, body = self.get(port, '/frame-slim.npz')
        self.addCleanup(connection.close)
        socket_used = connection.sock
        self.assertEqual((first.status, first.version, first.will_close), (200, 11, False))
        self.assertEqual(first.getheader('X-Den-Bundle-Schema'), 'den-camera-slim-1')
        self.assertEqual(members(body), ['depth_mm.npy', 'rgb_nv12.npy', 'metadata_json.npy'])
        for _ in range(3):
            _connection, again, empty = self.get(port, '/frame-slim.npz', {'If-None-Match': first.getheader('ETag')},
                                                 connection)
            self.assertEqual((again.status, empty, again.getheader('ETag')), (304, b'', first.getheader('ETag')))
        arrays, meta = frames(2), metadata(state.session, sequence=8, t_host=12.7)
        state.publish_slim(self.new.encode_slim_bundle(arrays, meta), meta['frames']['rgb'])
        _connection, newer, body = self.get(port, '/frame-slim.npz', {'If-None-Match': first.getheader('ETag')},
                                            connection)
        self.assertEqual(newer.status, 200)
        self.assertNotEqual(newer.getheader('ETag'), first.getheader('ETag'))
        self.assertTrue(np.array_equal(npz(body)['depth_mm'], arrays['depth_mm']))
        self.assertIs(connection.sock, socket_used, 'the same TCP connection throughout')

    def test_a_new_session_gets_a_new_tag(self):
        state, port, arrays, _meta = self.serve(self.new)
        _connection, first, _body = self.get(port, '/frame-slim.npz')
        state.reset()
        meta = metadata(state.session)
        state.publish_slim(self.new.encode_slim_bundle(arrays, meta), meta['frames']['rgb'])
        state.publish_bundle(b'full', meta['frames']['rgb'], arrays['rgb_nv12'].tobytes(), time.monotonic())
        with state.lock:
            state.encoded_updated = time.monotonic()
        _connection, second, _body = self.get(port, '/frame-slim.npz', {'If-None-Match': first.getheader('ETag')})
        self.assertEqual(second.status, 200)

    def test_stale_or_missing_slim_bundles_are_503_and_close(self):
        state, port, _arrays, _meta = self.serve(self.new)
        with state.lock:
            state.updated = time.monotonic() - 60
        _connection, response, body = self.get(port, '/frame-slim.npz')
        self.assertEqual((response.status, body, response.will_close), (503, b'Unavailable\n', True))
        state.reset()
        _connection, response, _body = self.get(port, '/frame-slim.npz')
        self.assertEqual((response.status, response.will_close), (503, True))

    def test_the_slim_path_needs_the_token(self):
        _state, port, _arrays, _meta = self.serve(self.new)
        for token in (None, 'wrong-token-' + 'y' * 20):
            with self.subTest(token=bool(token)):
                _connection, response, _body = self.get(port, '/frame-slim.npz', token=token)
                self.assertEqual((response.status, response.will_close), (401, True))

    def test_every_other_path_answers_exactly_as_the_earlier_release(self):
        _s, old_port, _a, _m = self.serve(self.old)
        _s, new_port, _a, _m = self.serve(self.new)
        cases = [('/frame.npz', TOKEN), ('/calibration', TOKEN), ('/health', TOKEN), ('/frame.jpg', TOKEN),
                 ('/timing', TOKEN), ('/nothing', TOKEN), ('/frame.npz', None), ('/frame-slim.npz', TOKEN)]
        for path, token in cases:
            with self.subTest(path=path, token=bool(token)):
                _c, old, old_body = self.get(old_port, path, token=token)
                _c, new, new_body = self.get(new_port, path, token=token)
                if path == '/frame-slim.npz':
                    self.assertEqual((old.status, new.status), (404, 200), 'the only path the earlier release lacks')
                    continue
                self.assertEqual((new.status, new.version, new.will_close), (old.status, old.version, old.will_close))
                self.assertEqual(sorted(k for k, _ in new.getheaders()), sorted(k for k, _ in old.getheaders()))
                if path == '/frame.npz' and token:
                    self.assertEqual((old.version, old.will_close), (10, True))
                    self.assertEqual(npz(new_body).keys(), npz(old_body).keys())
                elif path not in ('/health', '/timing'):
                    self.assertEqual(new_body, old_body)

    def test_an_http_1_0_client_gets_one_answer_then_close(self):
        _state, port, _arrays, _meta = self.serve(self.new)
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        connection._http_vsn, connection._http_vsn_str = 10, 'HTTP/1.0'
        _c, response, body = self.get(port, '/frame-slim.npz', connection=connection)
        self.assertEqual((response.status, response.will_close), (200, True))
        self.assertEqual(members(body), ['depth_mm.npy', 'rgb_nv12.npy', 'metadata_json.npy'])


if __name__ == '__main__':
    unittest.main()

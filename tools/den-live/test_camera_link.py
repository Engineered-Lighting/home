"""Tests for the camera link: source.py's KeptLink on the camera's /frame-slim.npz (den-camera-slim-1), its fallback to
/frame.npz, and CameraSource over it. Stdlib unittest.

    python -B -X utf8 tools\\den-live\\test_camera_link.py

No camera: a fake on 127.0.0.1 answers den-camera-slim-1 as the slim camera release does (HTTP/1.1 kept open, ETag,
If-None-Match and 304), or as an earlier release (404 on the slim path), with fictional frames and a random token.
den-take and den-observe are the pinned trees the other tests use (git archive at refs.json's commits, under TEMP).
"""
import http.client
import io
import json
import secrets
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import body_clock  # noqa: E402
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402
import source  # noqa: E402

TOOLS = Path(tempfile.gettempdir()) / 'den-live-test-tools'
TOKEN = secrets.token_hex(16)


def observe_tree():
    return sc.archive(TOOLS, 'observe')


def den_take():
    return rl.den_take(observe_tree())


def npy(descr, shape, body):
    header = "{'descr': %r, 'fortran_order': False, 'shape': %s, }" % (descr, tuple(shape))
    header += ' ' * ((64 - (10 + len(header) + 1) % 64) % 64) + '\n'
    return b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin-1') + body


def metadata(session, seq, t_host):
    def plane(width, height):
        return {'sequence': seq, 'device_timestamp_seconds': t_host - 3000.0, 'host_monotonic_timestamp_seconds': t_host,
                'width': width, 'height': height, 'type': 'fictional', 'exposure_duration_seconds': 0.008,
                'transformation_valid': True, 'intrinsics': [[400.0, 0.0, 320.0], [0.0, 400.0, 200.0], [0.0, 0.0, 1.0]]}
    return {'schema': 1, 'session': session, 'captured_unix_ns': 1_800_000_000_000_000_000,
            'captured_unix_ns_semantics': 'bundle_packaging_time_not_sensor_exposure',
            'depth_alignment': 'rectified_left', 'depth_units': 'millimetres', 'invalid_depth': 0,
            'depth_correction': {'applied': True, 'factor': 0.889, 'calibration_sha256': 'fictional-eeprom'},
            'frames': {'rgb': plane(640, 360), 'depth': plane(640, 400), 'left': plane(640, 400), 'right': plane(640, 400)},
            'synchronization': {'fictional': True}}


def depth_plane(seq):
    return struct.pack('<%dH' % (640 * 400), *([1000 + seq] * (640 * 400)))


def bundle(meta, seq, *, slim):
    """The full /frame.npz, or the slim one as the camera's encode_slim_bundle makes it: the same members without
    left and right, and the full metadata plus bundle_schema and bundle_omits."""
    if slim:
        meta = dict(meta, bundle_schema='den-camera-slim-1', bundle_omits=['left', 'right'])
    raw = json.dumps(meta, separators=(',', ':')).encode()
    members = {'depth_mm.npy': npy('<u2', (400, 640), depth_plane(seq))}
    if not slim:
        members.update({'left.npy': npy('|u1', (400, 640), bytes(256_000)),
                        'right.npy': npy('|u1', (400, 640), bytes(256_000))})
    members.update({'rgb_nv12.npy': npy('|u1', (540, 640), bytes([seq % 256]) * (640 * 540)),
                    'metadata_json.npy': npy('|u1', (len(raw),), raw)})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


class FakeCamera:
    """den-camera-slim-1 as the slim release serves it, or an earlier release (slim=False: the slim path is 404)."""

    def __init__(self, *, slim=True, schema='den-camera-slim-1', age_receipt=None):
        self.slim, self.schema, self.age_receipt = slim, schema, age_receipt
        self.lock = threading.Lock()
        self.connections, self.requests, self.sockets = 0, [], []
        self.session = 'fictional-' + secrets.token_hex(4)
        self.publish(1)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def do_GET(self):
                with owner.lock:
                    owner.requests.append((self.path, self.headers.get('If-None-Match')))
                    full, slim, etag = owner.full, owner.slim_body, owner.etag
                kept = self.path == source.SLIM_PATH and owner.slim
                if not kept:
                    self.protocol_version = 'HTTP/1.0'
                    self.close_connection = True
                if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
                    self.send_error(401)
                    return
                headers, status, body = {}, 404, b'Not found\n'
                if self.path == '/frame.npz':
                    status, body = 200, full
                elif kept:
                    headers = {'ETag': etag, 'X-Den-Bundle-Schema': owner.schema}
                    if owner.age_receipt is not None:
                        headers[body_clock.AGE_HEADER] = json.dumps(owner.age_receipt)
                    status, body = (304, b'') if self.headers.get('If-None-Match') == etag else (200, slim)
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass   # a write on a connection drop_connections shut: the client's side is what's tested

            def process_request(self, request, client_address):
                with owner.lock:
                    owner.connections += 1
                    owner.sockets.append(request)
                super().process_request(request, client_address)

        self.server = Server(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, seq):
        meta = metadata(self.session, seq, 100.0 + seq / 5)
        with self.lock:
            self.seq, self.meta = seq, meta
            self.full, self.slim_body = bundle(meta, seq, slim=False), bundle(meta, seq, slim=True)
            self.etag = '"%s-%d"' % (self.session, seq)

    def drop_connections(self):
        """As the camera does to a connection idle for 5 s: close it from this side."""
        with self.lock:
            sockets, self.sockets = self.sockets, []
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def paths(self):
        with self.lock:
            return [path for path, _ in self.requests]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.take = den_take()

    def camera(self, **kw):
        camera = FakeCamera(**kw)
        self.addCleanup(camera.close)
        client = source.camera_client(self.take, camera.url, TOKEN, body_timing=kw.get('age_receipt') is not None)
        self.addCleanup(client.close)
        return camera, client

    def test_one_connection_carries_every_pull(self):
        camera, client = self.camera()
        status, body, _ = client.get_bundle()
        self.assertEqual(status, 200)
        self.assertEqual(self.take.check.read_bundle(body)[0]['rgb_seq'], 1)
        for _ in range(4):
            self.assertEqual(client.get_bundle()[:2], (304, b''))
        camera.publish(2)
        status, body, _ = client.get_bundle()
        self.assertEqual((status, self.take.check.read_bundle(body)[0]['rgb_seq']), (200, 2))
        self.assertEqual(camera.connections, 1, 'one TCP connection for all six pulls')
        self.assertEqual(camera.paths(), [source.SLIM_PATH] * 6)
        self.assertEqual([tag for _, tag in camera.requests], [None] + ['"%s-1"' % camera.session] * 5)
        self.assertEqual(client.link.counts, {'connections': 1, 'slim': 2, 'not_modified': 4, 'full': 0})

    def test_den_lives_readers_read_the_slim_bundle_as_the_full_one(self):
        camera, client = self.camera()
        camera.publish(7)
        slim = client.get_bundle()[1]
        full = client.get('/frame.npz')[1]
        self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(slim)).namelist()),
                         ['depth_mm.npy', 'metadata_json.npy', 'rgb_nv12.npy'])
        sys.path.insert(0, str(observe_tree() / 'tools' / 'den-observe'))
        import takes
        for name, data in (('full', full), ('slim', slim)):
            with self.subTest(bundle=name):
                meta, nv12 = self.take.check.read_bundle(data, with_rgb=True)   # the source, the detector, the stills
                self.assertEqual((meta['session'], meta['rgb_seq']), (camera.session, 7))
                self.assertEqual(bytes(nv12), bytes([7]) * (640 * 540))
                frame, depth = takes.read_frame(data)                           # perceive's depth and checks
                self.assertEqual(set(depth.mm), {1007})
                raw = frame['raw']
                self.assertEqual(raw['depth_correction'], camera.meta['depth_correction'])
                self.assertEqual(raw['frames']['right'], camera.meta['frames']['right'], "perceive's FrameRef planes")
                self.assertEqual(frame['intrinsics']['rectified_left'], camera.meta['frames']['depth']['intrinsics'])
                self.assertEqual(raw['captured_unix_ns'], camera.meta['captured_unix_ns'])
        try:
            import numpy as np
        except ImportError:
            return
        import body
        for data in (full, slim):
            self.assertEqual(int(body.depth_of(np, data).max()), 1007)                 # the figure's depth

    def test_a_camera_without_the_slim_path_gets_the_full_bundle_as_before(self):
        camera, client = self.camera(slim=False)
        clock = Clock()
        client.link.clock = clock
        for _ in range(3):
            status, body, _ = client.get_bundle()
            self.assertEqual(status, 200)
            self.assertIn('left.npy', zipfile.ZipFile(io.BytesIO(body)).namelist())
        self.assertEqual(camera.paths(), [source.SLIM_PATH, '/frame.npz', '/frame.npz', '/frame.npz'])
        self.assertEqual(client.link.mode, 'full')
        camera.slim = True   # the slim release, switched in
        clock.t += source.SLIM_RETRY_S - 1
        client.get_bundle()
        self.assertEqual(camera.paths()[-1], '/frame.npz', 'not asked again before SLIM_RETRY_S')
        clock.t += 2
        status, body, _ = client.get_bundle()
        self.assertEqual((status, client.link.mode), (200, 'slim'))
        self.assertNotIn('left.npy', zipfile.ZipFile(io.BytesIO(body)).namelist())

    def test_a_connection_the_camera_closed_is_asked_again_once_on_a_new_one(self):
        camera, client = self.camera()
        self.assertEqual(client.get_bundle()[0], 200)
        camera.drop_connections()
        time.sleep(0.05)
        camera.publish(2)
        self.assertEqual(client.get_bundle()[0], 200)
        self.assertEqual(client.link.counts['connections'], 2)

    def test_a_slim_answer_of_another_schema_is_refused(self):
        _camera, client = self.camera(schema='den-camera-slim-0')
        with self.assertRaises(source.SchemaRefused):
            client.get_bundle()

    def test_the_figure_timing_receipt_comes_with_a_slim_bundle(self):
        receipt = {'schema': body_clock.AGE_SCHEMA, 'fictional': True}
        _camera, client = self.camera(age_receipt=receipt)
        self.assertEqual(client.get_bundle()[2], receipt)
        self.assertEqual(client.get_bundle()[:3], (304, b'', None))

    def test_the_slim_path_is_on_the_allowlist_and_nothing_else_is_new(self):
        self.assertEqual(source.ALLOWED_PATHS, ('/frame.npz', '/calibration', '/frame-slim.npz'))
        _camera, client = self.camera()
        for path in ('/frame.jpg', '/frame-slim.npz/', '/FRAME-SLIM.NPZ', '/frame-slim.npz?x=1'):
            with self.assertRaises(source.PathRefused, msg=path):
                client.get(path)


class SourceOverTheLink(unittest.TestCase):
    def run_source(self, camera, seconds, *, every_s=0.2, token=TOKEN):
        take = den_take()
        client = source.camera_client(take, camera.url, token)
        box = source.Mailbox()
        src = source.CameraSource(take, client, box, poll_s=0.2, recheck_s=0.05)
        got, stop = [], threading.Event()

        def drain():
            while not stop.is_set():
                item = box.take(0.05)
                if item is not None:
                    got.append(item)
        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()
        src.start()
        seq, ends = camera.seq, time.monotonic() + seconds
        while time.monotonic() < ends:
            time.sleep(every_s)
            seq += 1
            camera.publish(seq)
        time.sleep(0.3)
        src.halt.set()
        src.join(5)
        stop.set()
        drainer.join(5)
        return src, client, got

    def test_every_frame_arrives_on_one_connection_with_no_errors(self):
        camera = FakeCamera()
        self.addCleanup(camera.close)
        src, client, got = self.run_source(camera, 2.0)
        seqs = [b.rgb_seq for b in got]
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertGreaterEqual(len(seqs), 9, 'about every frame of 2 s at 5 a second')
        self.assertEqual((src.errors, src.status, src.state()['link']), (0, 'ok', 'slim'))
        self.assertEqual(camera.connections, 1)
        self.assertGreater(client.link.counts['not_modified'], 0, 'between frames it asks again and gets 304s')
        self.assertTrue(all(b.fetch_s is not None and b.fetch_s < 0.2 for b in got))

    def test_an_earlier_camera_release_still_feeds_the_source(self):
        camera = FakeCamera(slim=False)
        self.addCleanup(camera.close)
        src, client, got = self.run_source(camera, 1.2)
        self.assertGreaterEqual(len(got), 4)
        self.assertEqual((src.errors, src.state()['link']), (0, 'full'))
        self.assertIn('left.npy', zipfile.ZipFile(io.BytesIO(got[-1].data)).namelist())

    def test_a_refused_token_on_the_slim_path_says_refused(self):
        camera = FakeCamera()
        self.addCleanup(camera.close)
        src, _client, got = self.run_source(camera, 0.5, token='wrong-' + secrets.token_hex(12))
        self.assertEqual((got, src.status), ([], 'refused'))


if __name__ == '__main__':
    unittest.main()

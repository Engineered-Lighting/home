"""The den camera's body-clock release (pilot-20261114-body): the slim release plus the measured figure's
capture-age receipt, on the slim answers den-live reads.

    python -B tools/den-camera/test_body_receipt_release.py

Stdlib unittest and numpy. No camera: fictional frames, a fictional token and a fictional clock. Without a body clock
profile the release must answer exactly as the slim release; with one, an authenticated 200 for the current frame's
slim or full bundle carries the same den-body-pair-age-1 receipt, and nothing else does.
"""
import ast
import json
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_slim_release as slim_test  # noqa: E402  fictional frames, metadata, loaders and wire helpers

ROOT = slim_test.ROOT
RELEASE = ROOT / 'tools/fixtures/installed-oak-capture-20261114-body.py'
SLIM = slim_test.RELEASE
TOKEN = slim_test.TOKEN
AGE = 'X-Den-Body-Capture-Age'
# What this release may change since the slim release. Everything else stays byte-identical.
BODY_CLOCK = {'BodyClockProfile', 'BodyClockState', '_body_interval', '_body_offset', '_ceil_ratio', '_exposure_fits'}
CHANGED = BODY_CLOCK | {'__doc__', 'capture_session', 'main', 'serve'}
frames, metadata, members = slim_test.frames, slim_test.metadata, slim_test.members


class ReleaseDiff(unittest.TestCase):
    def test_only_the_body_clock_changed_since_the_slim_release(self):
        old, new = slim_test.module_definitions(SLIM), slim_test.module_definitions(RELEASE)
        self.assertEqual(set(new) - set(old), BODY_CLOCK)
        self.assertEqual(set(old) - set(new), set())
        for key in set(old) & set(new) - CHANGED:
            with self.subTest(definition=key):
                self.assertEqual(ast.dump(old[key]), ast.dump(new[key]))

    def test_the_capture_loop_changed_only_by_binding_and_sampling_the_clock(self):
        def lines(path):
            source = path.read_text(encoding='utf-8')
            node = slim_test.module_definitions(path)['capture_session']
            return source.splitlines()[node.lineno - 1:node.end_lineno]
        new = [line for line in lines(RELEASE) if 'state.bind_body_clock(' not in line
               and 'fps=args.fps, correction=depth_correction)' not in line
               and 'The body clock is sampled with the slim bundle' not in line
               and 'metadata=metadata, source_now=dai.Clock.now)' not in line]
        new = [line.replace("metadata['frames']['rgb'],", "metadata['frames']['rgb'])")
               if 'state.publish_slim(' in line else line for line in new]
        self.assertEqual(new, lines(SLIM))

    def test_without_a_profile_main_builds_a_state_with_no_clock(self):
        source = RELEASE.read_text(encoding='utf-8')
        self.assertIn('body_clock = None\n    if args.body_clock_profile is not None:', source)
        self.assertIn('body_clock=body_clock)', source)


class SlimWire(slim_test.Wire):
    """The slim release's own wire tests, run on this release with no body clock profile beside the earlier release."""

    @classmethod
    def setUpClass(cls):
        cls.new = slim_test.load(RELEASE, '_body_release_wire_new')
        cls.old = slim_test.load(slim_test.EARLIER, '_body_release_wire_old')


class Clock(unittest.TestCase):
    """BodyClockState on a fictional camera host: monotonic nanoseconds and an SDK clock 2 s ahead of it."""

    @classmethod
    def setUpClass(cls):
        cls.m = slim_test.load(RELEASE, '_body_release_clock')

    def setUp(self):
        m = self.m
        self.mono_ns = 10_000_000_000
        self.boot_delta_ns = 0
        self.sdk_offset_ns = 2_000_000_000
        self.correction = dict(m.DEPTH_CORRECTION, applied=True)
        self.profile = m.BodyClockProfile(
            schema='den-body-clock-profile-1', evidence_id='synthetic', device_id='unit',
            depthai_version='3.10.0', calibration_sha256=self.correction['calibration_sha256'],
            fps=30, clock_drift_ppm=100, receiver_drift_ppm=100,
            rgb={'before_stamp_s': .012, 'after_stamp_s': .003},
            depth={'before_stamp_s': .015, 'after_stamp_s': .004})
        self.state = m.BodyClockState(15, body_clock=self.profile, _point=self.point)
        self.bind()
        self.real_sample = m.sample_clock_pair

    def point(self):
        before = self.mono_ns
        self.mono_ns += 1000
        return self.m.HostPoint(before, self.mono_ns + self.boot_delta_ns, 1_000_000_000, self.mono_ns)

    def sdk_now(self):
        return timedelta(microseconds=(self.mono_ns + self.sdk_offset_ns) // 1000)

    def sample(self, source_now):
        def mono():
            self.mono_ns += 1000
            return self.mono_ns
        return self.real_sample(source_now, monotonic_ns=mono, wall_ns=lambda: 1_000_000_000)

    def bind(self, **overrides):
        self.state.bind_body_clock(**({'sdk': '3.10.0', 'device_id': 'unit', 'fps': 30,
                                        'correction': self.correction} | overrides))

    def meta(self, sequence, *, depth_seq=None, frame_delay_ns=30_000_000, rgb_exposure=.008, stereo_exposure=.009):
        rgb_stamp = self.mono_ns + self.sdk_offset_ns - frame_delay_ns
        depth_stamp = rgb_stamp - 9_000_000
        rgb = {'sequence': sequence, 'host_monotonic_timestamp_seconds': rgb_stamp / 1e9,
               'exposure_duration_seconds': rgb_exposure}
        depth = {'sequence': sequence + 20 if depth_seq is None else depth_seq,
                 'host_monotonic_timestamp_seconds': depth_stamp / 1e9}
        mono = dict(depth, exposure_duration_seconds=stereo_exposure)
        return {'session': self.state.session, 'frames': {'rgb': rgb, 'depth': depth, 'left': mono, 'right': mono}}

    def publish(self, sequence, *, full=True, **frame):
        """One frame as the capture loop publishes it: slim first, then the full bundle."""
        self.mono_ns += 200_000_000
        meta = self.meta(sequence, **frame)
        slim = b'slim' + bytes([sequence]) * 24
        with mock.patch.object(self.m, 'sample_clock_pair', self.sample):
            self.state.publish_slim(slim, meta['frames']['rgb'], metadata=meta, source_now=self.sdk_now)
        bundle = None
        if full:
            bundle = b'full' + bytes([sequence]) * 24
            self.state.publish_bundle(bundle, meta['frames']['rgb'], b'rgb', self.mono_ns / 1e9)
        return slim, bundle

    def receipt(self, body):
        value = self.state.body_capture_age(body)
        return None if value is None else json.loads(value)

    def test_the_slim_bundle_has_its_receipt_before_the_full_one_is_published(self):
        self.publish(1)
        slim, _full = self.publish(2, full=False)
        self.mono_ns += 20_000_000
        receipt = self.receipt(slim)
        self.assertEqual(receipt['schema'], 'den-body-pair-age-1')
        self.assertEqual(receipt['frame_key'], [self.state.session, 2, 22])
        self.assertEqual(receipt['evidence_id'], 'synthetic')
        self.assertGreaterEqual(receipt['rgb_age_upper_s'], .062)
        self.assertGreaterEqual(receipt['depth_age_upper_s'], .074)
        self.assertLess(receipt['depth_age_upper_s'], .075)
        self.assertGreaterEqual(receipt['separation_upper_s'], .027)

    def test_slim_and_full_bundles_of_one_frame_share_one_receipt(self):
        self.publish(1)
        slim, full = self.publish(2)
        receipts = [self.receipt(body) for body in (slim, full)]
        self.assertTrue(all(receipts))
        self.assertEqual(receipts[0], receipts[1] | {key: receipts[0][key] for key in receipts[0] if key.endswith('_s')})
        self.assertEqual({r['frame_key'][1] for r in receipts}, {2})

    def test_only_the_exact_current_bytes_get_a_receipt(self):
        self.publish(1)
        slim, full = self.publish(2)
        self.assertIsNone(self.receipt(bytes(bytearray(slim))), 'equal bytes are not the sampled object')
        self.assertIsNone(self.receipt(b'something else'))
        newer, _full = self.publish(3, full=False)
        self.assertIsNone(self.receipt(slim), 'the previous frame is no longer current')
        self.assertIsNone(self.receipt(full), 'nor is its full bundle once the next frame is sampled')
        self.assertIsNotNone(self.receipt(newer))

    def test_needs_two_consistent_samples(self):
        first, _full = self.publish(1)
        self.assertIsNone(self.receipt(first))
        self.boot_delta_ns += 5_000_000   # a suspend between samples
        self.assertIsNone(self.receipt(self.publish(2)[0]))
        self.assertIsNone(self.receipt(self.publish(3)[0]))
        self.assertIsNotNone(self.receipt(self.publish(4)[0]))
        self.sdk_offset_ns += 30_000_000   # an SDK clock step
        self.assertIsNone(self.receipt(self.publish(5)[0]))
        self.assertIsNotNone(self.receipt(self.publish(6)[0]))

    def test_a_mismatched_profile_or_no_profile_gives_none(self):
        self.bind(device_id='other')
        self.publish(1)
        self.assertIsNone(self.receipt(self.publish(2)[0]))
        plain = self.m.BodyClockState(15, _point=self.point)
        plain.bind_body_clock(sdk='3.10.0', device_id='unit', fps=30, correction=self.correction)
        self.state = plain
        self.publish(3)
        self.assertIsNone(self.receipt(self.publish(4)[0]))

    def test_exposure_longer_than_the_commissioned_bounds_gives_none(self):
        self.publish(1)
        self.assertIsNone(self.receipt(self.publish(2, rgb_exposure=.016)[0]))
        self.assertIsNone(self.receipt(self.publish(3, rgb_exposure=None)[0]))
        self.assertIsNone(self.receipt(self.publish(4, stereo_exposure=.020)[0]))
        self.assertIsNotNone(self.receipt(self.publish(5, stereo_exposure=None)[0]))

    def test_a_pair_a_second_old_gives_none(self):
        self.publish(1)
        slim, _full = self.publish(2)
        self.assertIsNotNone(self.receipt(slim))
        self.mono_ns += 1_000_000_000
        self.assertIsNone(self.receipt(slim))

    def test_a_new_session_drops_the_receipt(self):
        self.publish(1)
        slim, _full = self.publish(2)
        self.state.reset()
        self.assertIsNone(self.receipt(slim))


class ClockWire(unittest.TestCase):
    """The receipt on the real HTTP path: on 200 slim and full answers with the token, never on a 304."""

    get = staticmethod(slim_test.Wire.get)

    @classmethod
    def setUpClass(cls):
        cls.new = slim_test.load(RELEASE, '_body_release_clock_wire')

    def serve_clock(self):
        m = self.new
        state = m.BodyClockState(15)
        arrays, meta = frames(), metadata(state.session)
        slim, full = m.encode_slim_bundle(arrays, meta), m.encode_bundle(arrays, meta)
        state.publish_slim(slim, meta['frames']['rgb'])
        state.publish_bundle(full, meta['frames']['rgb'], arrays['rgb_nv12'].tobytes(), time.monotonic())
        receipt = json.dumps({'schema': 'den-body-pair-age-1', 'frame_key': [state.session, 7, 3]})
        state.body_capture_age = lambda body: receipt if body in (slim, full) else None
        with state.lock:
            state.encoded_updated = time.monotonic()
        server = m.serve(state, '127.0.0.1', 0, TOKEN)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1], receipt

    def test_the_slim_and_full_answers_carry_it_and_a_304_does_not(self):
        port, receipt = self.serve_clock()
        connection, slim, body = self.get(port, '/frame-slim.npz')
        self.addCleanup(connection.close)
        self.assertEqual((slim.status, slim.getheader(AGE)), (200, receipt))
        self.assertEqual(members(body), ['depth_mm.npy', 'rgb_nv12.npy', 'metadata_json.npy'])
        _c, again, empty = self.get(port, '/frame-slim.npz', {'If-None-Match': slim.getheader('ETag')}, connection)
        self.assertEqual((again.status, again.getheader(AGE), empty), (304, None, b''))
        _c, full, _body = self.get(port, '/frame.npz')
        self.assertEqual((full.status, full.getheader(AGE)), (200, receipt))
        for path in ('/calibration', '/health', '/timing'):
            with self.subTest(path=path):
                _c, other, _body = self.get(port, path)
                self.assertIsNone(other.getheader(AGE))

    def test_no_token_no_answer(self):
        port, _receipt = self.serve_clock()
        _c, refused, _body = self.get(port, '/frame-slim.npz', token=None)
        self.assertEqual((refused.status, refused.getheader(AGE)), (401, None))


if __name__ == '__main__':
    unittest.main()

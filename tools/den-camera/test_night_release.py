"""The den camera's night release (pilot-20261114-night): what it changes, what it keeps, and its wire.

    python -B tools/den-camera/test_night_release.py

Stdlib unittest and numpy. No camera: the night and slim releases' HTTP servers run side by side on 127.0.0.1 with a
fictional token and fictional frames. The night bundle is read with den-live's pinned readers (den-take's check and
den-observe's takes, by git archive at den-live's pin); that test skips where the pin is absent.
"""
import ast
import hashlib
import http.client
import io
import json
import logging
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_slim_release as slim_test  # noqa: E402  its fictional frames, metadata, loaders and wire helpers

ROOT = slim_test.ROOT
RELEASE = ROOT / 'tools/fixtures/installed-oak-capture-20261114-night.py'
SLIM = slim_test.RELEASE
SLIM_SHA = '3c86660dc01f4551f7e8c9e5d559a07ae4d61723ef3f5e347fa2aa81667be086'   # pilot-20261114-slim, running now
TOKEN = slim_test.TOKEN
ACCEPT = {'X-Den-Bundle-Accept': 'den-camera-slim-2'}
# What the night release may change since pilot-20261114-slim. Everything else stays byte-identical.
CHANGED = {'__doc__', 'ACCEPT_HEADER', 'NIGHT', 'SLIM_NIGHT_SCHEMA', 'encode_night_bundle', 'NightSwitch', 'mean_luma',
           'night_step', 'State', 'serve', 'capture_session', 'main'}
STATE_CHANGED = {'reset', 'publish_slim', 'slim_response'}
frames, metadata, members, npz, load = slim_test.frames, slim_test.metadata, slim_test.members, slim_test.npz, slim_test.load


class ReleaseDiff(unittest.TestCase):
    def test_the_slim_release_is_the_one_running(self):
        self.assertEqual(hashlib.sha256(SLIM.read_bytes()).hexdigest(), SLIM_SHA)
        self.assertEqual(hashlib.sha256(slim_test.EARLIER.read_bytes()).hexdigest(), slim_test.EARLIER_SHA,
                         "the Lab's pin is untouched")

    def test_only_night_changed_since_the_slim_release(self):
        old, new = slim_test.module_definitions(SLIM), slim_test.module_definitions(RELEASE)
        self.assertLessEqual(set(new) - set(old), CHANGED)
        self.assertEqual(set(old) - set(new), set())
        for key in set(old) & set(new) - CHANGED:
            with self.subTest(definition=key):
                self.assertEqual(ast.dump(old[key]), ast.dump(new[key]))

    def test_inside_state_only_the_slim_store_changed(self):
        old = slim_test.definitions(slim_test.module_definitions(SLIM)['State'].body)
        new = slim_test.definitions(slim_test.module_definitions(RELEASE)['State'].body)
        self.assertEqual(set(new), set(old))
        for key in set(old) - STATE_CHANGED:
            with self.subTest(method=key):
                self.assertEqual(ast.dump(old[key]), ast.dump(new[key]))

    def test_the_capture_loop_changed_only_by_the_night_switch(self):
        """capture_session's text, less the three night lines, is the slim release's."""
        def lines(path):
            source = path.read_text(encoding='utf-8')
            node = slim_test.module_definitions(path)['capture_session']
            return source.splitlines()[node.lineno - 1:node.end_lineno]
        night = [line for line in lines(RELEASE) if 'switch = NightSwitch' not in line
                 and 'night = night_step(' not in line]
        night = [line.replace(", metadata['frames']['rgb'], night)", ", metadata['frames']['rgb'])") for line in night]
        self.assertEqual(night, lines(SLIM))


class Switch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = load(RELEASE, '_night_release_switch')

    def run_lumas(self, lumas, step=0.25):
        switch = self.camera.NightSwitch(**self.camera.NIGHT)
        return [switch.update(luma, i * step) for i, luma in enumerate(lumas)], switch

    def test_den_lives_numbers(self):
        self.assertEqual({k: self.camera.NIGHT[k] for k in ('dark_luma', 'light_luma', 'hold_s')},
                         {'dark_luma': 40.0, 'light_luma': 60.0, 'hold_s': 3.0})

    def test_dark_for_the_hold_is_night_and_light_for_the_hold_is_day(self):
        modes, switch = self.run_lumas([10.0] * 13 + [120.0] * 13)
        self.assertEqual(modes[11], 'day')
        self.assertEqual(modes[12], 'night')   # 3.0 s after the first dark frame
        self.assertEqual(modes[24], 'night')
        self.assertEqual(modes[25], 'day')
        self.assertEqual(switch.switches, 2)

    def test_between_the_lines_or_one_frame_the_other_way_restarts_the_count(self):
        modes, switch = self.run_lumas(([10.0] * 10 + [50.0]) * 4)
        self.assertEqual(set(modes), {'day'})
        modes, _ = self.run_lumas(([10.0] * 10 + [90.0]) * 4)
        self.assertEqual(set(modes), {'day'}, 'dusk flicker never switches')
        self.assertEqual(switch.switches, 0)

    def test_the_rule_refuses_nonsense(self):
        with self.assertRaises(ValueError):
            self.camera.NightSwitch(60.0, 40.0, 3.0)
        with self.assertRaises(ValueError):
            self.camera.NightSwitch(40.0, 60.0, 0.0)

    def test_night_step_sets_the_drivers_on_a_change_and_bundles_only_at_night(self):
        class Device:
            def __init__(self):
                self.calls = []

            def setIrFloodLightIntensity(self, value):
                self.calls.append(('flood', value))

            def setIrLaserDotProjectorIntensity(self, value):
                self.calls.append(('dots', value))

        device, switch = Device(), self.camera.NightSwitch(**self.camera.NIGHT)
        arrays, meta = frames(), metadata('fictional-session')
        dark = dict(arrays, rgb_nv12=np.full((540, 640), 8, np.uint8))
        light = dict(arrays, rgb_nv12=np.full((540, 640), 150, np.uint8))
        logging.disable(logging.INFO)
        self.addCleanup(logging.disable, logging.NOTSET)
        out = [self.camera.night_step(switch, device, dark, meta, t) for t in (0.0, 1.0, 2.0, 3.0, 3.25)]
        self.assertEqual(out[:4], [None] * 4, 'by day, and on the switching frame itself, no night bundle')
        self.assertEqual(device.calls, [('flood', 1.0), ('dots', 0.0)])
        night = json.loads(bytes(npz(out[4])['metadata_json']).decode())
        self.assertEqual(night['ir'], {'mode': 'night', 'flood': 1.0, 'dots': 0.0, 'switches': 1,
                                       'rule': {'dark_luma': 40.0, 'light_luma': 60.0, 'hold_s': 3.0}})
        back = [self.camera.night_step(switch, device, light, meta, t) for t in (4.0, 7.0, 7.25)]
        self.assertIsNotNone(back[0], 'still night while the light holds')
        self.assertEqual(back[1:], [None, None])
        self.assertEqual(device.calls[-2:], [('flood', 0.0), ('dots', 0.0)])
        self.assertIsNone(self.camera.night_step(None, device, dark, meta, 9.0), '--night off')


class NightBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = load(RELEASE, '_night_release_bundle')

    def setUp(self):
        self.arrays, self.meta = frames(), metadata('fictional-session')
        self.ir = {'mode': 'night', 'flood': 1.0, 'dots': 0.0, 'switches': 1}
        self.night = self.camera.encode_night_bundle(self.arrays, self.meta, self.ir)

    def test_members_are_the_slim_ones_plus_left(self):
        self.assertEqual(members(self.night), ['depth_mm.npy', 'rgb_nv12.npy', 'left.npy', 'metadata_json.npy'])

    def test_planes_are_lossless(self):
        got = npz(self.night)
        for name in ('depth_mm', 'rgb_nv12', 'left'):
            with self.subTest(member=name):
                self.assertEqual((got[name].dtype, got[name].shape), (self.arrays[name].dtype, self.arrays[name].shape))
                self.assertTrue(np.array_equal(got[name], self.arrays[name]))

    def test_metadata_is_the_full_bundles_plus_schema_omits_and_ir(self):
        night = json.loads(bytes(npz(self.night)['metadata_json']).decode())
        full = json.loads(bytes(npz(self.camera.encode_bundle(self.arrays, self.meta))['metadata_json']).decode())
        self.assertEqual(night.pop('bundle_schema'), 'den-camera-slim-2')
        self.assertEqual(night.pop('bundle_omits'), ['right'])
        self.assertEqual(night.pop('ir'), self.ir)
        self.assertEqual(night, full)

    def test_the_slim_bundle_is_the_slim_releases(self):
        earlier = load(SLIM, '_night_release_bundle_slim')
        self.assertEqual(self.camera.encode_slim_bundle(self.arrays, self.meta),
                         earlier.encode_slim_bundle(self.arrays, self.meta))

    def test_planes_must_keep_their_types(self):
        for name, bad in (('depth_mm', self.arrays['depth_mm'].astype(np.float32)),
                          ('left', self.arrays['left'].astype(np.uint16)), ('left', self.arrays['left'][:360])):
            with self.subTest(member=name), self.assertRaises(ValueError):
                self.camera.encode_night_bundle(dict(self.arrays, **{name: bad}), self.meta, self.ir)


class DenLiveReaders(unittest.TestCase):
    """The night bundle through den-live's pinned readers, as the slim release's test reads the slim bundle."""

    @classmethod
    def setUpClass(cls):
        cls.tree = Path(tempfile.mkdtemp(prefix='den-camera-night-readers-'))
        cls.addClassCleanup(shutil.rmtree, cls.tree, ignore_errors=True)
        done = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', slim_test.DEN_TAKE_PIN,
                               'tools/den-take', 'tools/den-observe'], capture_output=True)
        if done.returncode:
            raise unittest.SkipTest("den-live's pinned den-take (%s) isn't in this checkout" % slim_test.DEN_TAKE_PIN)
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
            tar.extractall(cls.tree, filter='data')
        for folder in ('tools/den-take', 'tools/den-observe'):
            sys.path.insert(0, str(cls.tree / folder))
        cls.check = load(cls.tree / 'tools/den-take/check.py', 'check')
        cls.takes = load(cls.tree / 'tools/den-observe/takes.py', '_night_release_takes')
        cls.camera = load(RELEASE, '_night_release_camera_readers')

    def test_den_take_and_den_observe_read_it_as_they_read_the_slim_bundle(self):
        arrays, meta = frames(3), metadata('fictional-session', sequence=41, t_host=99.25)
        blob = self.camera.encode_night_bundle(arrays, meta, {'mode': 'night'})
        parsed, nv12 = self.check.read_bundle(blob, with_rgb=True)
        self.assertEqual((parsed['session'], parsed['rgb_seq'], parsed['t_host']), ('fictional-session', 41, 99.25))
        self.assertEqual(bytes(nv12), arrays['rgb_nv12'].tobytes())
        frame_meta, depth = self.takes.read_frame(blob)
        self.assertEqual(list(depth.mm), arrays['depth_mm'].ravel().tolist())
        self.assertEqual(frame_meta['raw']['ir'], {'mode': 'night'})
        self.assertEqual(frame_meta['raw']['bundle_omits'], ['right'])


class Wire(slim_test.Wire):
    """The night release beside the slim release: by day, and to anyone who doesn't ask for den-camera-slim-2, the
    same answers; at night, to den-live's ask, the night bundle."""

    @classmethod
    def setUpClass(cls):
        cls.new = load(RELEASE, '_night_release_wire_new')
        cls.old = load(SLIM, '_night_release_wire_slim')

    def serve(self, module, night=False, session=None):
        """The slim release's fixture; with session, both releases serve the very same frame under that session."""
        state, port, arrays, meta = super().serve(module)
        if session is not None:
            with state.lock:
                state.session = session
            meta = metadata(session)
            state.publish_slim(module.encode_slim_bundle(arrays, meta), meta['frames']['rgb'])
            state.publish_bundle(module.encode_bundle(arrays, meta), meta['frames']['rgb'], arrays['rgb_nv12'].tobytes(),
                                 time.monotonic())
        if night:
            state.publish_slim(module.encode_slim_bundle(arrays, meta), meta['frames']['rgb'],
                               module.encode_night_bundle(arrays, meta, {'mode': 'night', 'flood': 1.0, 'dots': 0.0}))
        return state, port, arrays, meta

    def test_every_other_path_answers_exactly_as_the_earlier_release(self):
        """By day and by night, every path, and the slim path for anyone who doesn't ask for den-camera-slim-2 (and
        for den-live too by day): the slim release's status line, headers and body."""
        for night in (False, True):
            _s, old_port, _a, _m = self.serve(self.old, session='fictional-session')
            _s, new_port, _a, _m = self.serve(self.new, night=night, session='fictional-session')
            for path in ('/frame.npz', '/calibration', '/health', '/frame.jpg', '/timing', '/nothing', '/frame-slim.npz'):
                for headers in ({}, ACCEPT) if path == '/frame-slim.npz' and not night else ({},):
                    with self.subTest(path=path, night=night, headers=headers):
                        _c, old, old_body = self.get(old_port, path, headers)
                        _c, new, new_body = self.get(new_port, path, headers)
                        self.assertEqual((new.status, new.version, new.will_close), (old.status, old.version, old.will_close))
                        exact = path not in ('/health', '/timing')
                        self.assertEqual(sorted((k, v if exact else '') for k, v in new.getheaders() if k != 'Date'),
                                         sorted((k, v if exact else '') for k, v in old.getheaders() if k != 'Date'))
                        if path == '/frame.npz':   # savez stamps each member's time; the members must be equal
                            old_npz, new_npz = npz(old_body), npz(new_body)
                            self.assertEqual(sorted(new_npz), sorted(old_npz))
                            for name in old_npz:
                                self.assertTrue(np.array_equal(new_npz[name], old_npz[name]), name)
                        elif exact:
                            self.assertEqual(new_body, old_body)

    def test_at_night_den_lives_ask_gets_the_night_bundle_on_the_kept_connection(self):
        state, port, arrays, meta = self.serve(self.new, night=True)
        connection, first, body = self.get(port, '/frame-slim.npz', ACCEPT)
        self.addCleanup(connection.close)
        self.assertEqual((first.status, first.version, first.will_close), (200, 11, False))
        self.assertEqual(first.getheader('X-Den-Bundle-Schema'), 'den-camera-slim-2')
        self.assertEqual(members(body), ['depth_mm.npy', 'rgb_nv12.npy', 'left.npy', 'metadata_json.npy'])
        self.assertTrue(np.array_equal(npz(body)['left'], arrays['left']))
        _c, again, empty = self.get(port, '/frame-slim.npz', dict(ACCEPT, **{'If-None-Match': first.getheader('ETag')}),
                                    connection)
        self.assertEqual((again.status, empty), (304, b''))
        _c, plain, _body = self.get(port, '/frame-slim.npz')
        self.assertEqual(plain.getheader('X-Den-Bundle-Schema'), 'den-camera-slim-1')
        self.assertNotEqual(plain.getheader('ETag'), first.getheader('ETag'), 'each schema has its own tag')
        _c, mixed, _body = self.get(port, '/frame-slim.npz', {'X-Den-Bundle-Accept': 'den-camera-slim-1, den-camera-slim-2'})
        self.assertEqual(mixed.getheader('X-Den-Bundle-Schema'), 'den-camera-slim-2')
        state.publish_slim(self.new.encode_slim_bundle(arrays, meta), meta['frames']['rgb'])   # day again
        _c, day, day_body = self.get(port, '/frame-slim.npz', dict(ACCEPT, **{'If-None-Match': first.getheader('ETag')}),
                                     connection)
        self.assertEqual((day.status, day.getheader('X-Den-Bundle-Schema')), (200, 'den-camera-slim-1'))
        self.assertEqual(members(day_body), ['depth_mm.npy', 'rgb_nv12.npy', 'metadata_json.npy'])

    def test_a_new_session_drops_the_night_bundle(self):
        state, port, _arrays, _meta = self.serve(self.new, night=True)
        state.reset()
        self.assertIsNone(state.slim_night)



if __name__ == '__main__':
    unittest.main()
